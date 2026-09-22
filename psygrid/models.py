"""Shared data model for the PSYGRID Tournament Engine.

Every analytical module reads/writes these dataclasses so the same
methodology is applied identically to every instrument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candle:
    """A single OHLC candle for one instrument/timeframe.

    ``ts`` is the candle OPEN time, epoch seconds UTC. ``timeframe`` is one
    of "M1", "M5", "M15", "M30", "H1". Anything other than "M1" must be
    produced by :mod:`psygrid.timeframes` from genuine M1 candles and carries
    ``synthetic_from_m1=True``.
    """

    instrument: str
    timeframe: str
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    synthetic_from_m1: bool = False

    def is_valid_ohlc(self) -> bool:
        if any(v is None for v in (self.open, self.high, self.low, self.close)):
            return False
        if self.high < self.low:
            return False
        if not (self.low <= self.open <= self.high):
            return False
        if not (self.low <= self.close <= self.high):
            return False
        return True


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TrendLabel(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class CandidateStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    REJECT = "REJECT"


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


@dataclass
class DataQualityReport:
    instrument: str
    freshness_seconds: Optional[float]
    is_fresh: bool
    history_count: int
    has_sufficient_history: bool
    timestamps_valid: bool
    is_continuous: bool
    missing_candle_count: int
    duplicate_count: int
    invalid_ohlc_count: int
    issues: list = field(default_factory=list)
    # Provider-reported metadata (RealMarketAPI's per-symbol status/
    # market_state/gap_recoveries/rejected_count), preserved here so it
    # actively participates in the usability/quality determination below —
    # not just carried along decoratively. Absent (None) when the caller
    # has no provider metadata to give (e.g. synthetic test fixtures),
    # which is treated as neutral/OK rather than a failure.
    provider_status: Optional[str] = None
    provider_market_state: Optional[str] = None
    provider_gap_recoveries: int = 0
    provider_rejected_count: int = 0

    @property
    def provider_status_ok(self) -> bool:
        return self.provider_status is None or self.provider_status == "ok"

    @property
    def is_usable(self) -> bool:
        return (
            self.is_fresh
            and self.has_sufficient_history
            and self.timestamps_valid
            and self.invalid_ohlc_count == 0
            and self.provider_status_ok
        )

    @property
    def quality_score(self) -> float:
        """0-100 explainable data-quality score."""
        score = 100.0
        if not self.is_fresh:
            score -= 40
        if not self.has_sufficient_history:
            score -= 30
        if not self.timestamps_valid:
            score -= 15
        if not self.is_continuous:
            gap_penalty = min(20.0, self.missing_candle_count * 2.0)
            score -= gap_penalty
        if self.duplicate_count:
            score -= min(10.0, self.duplicate_count)
        if self.invalid_ohlc_count:
            score -= min(25.0, self.invalid_ohlc_count * 5.0)
        if not self.provider_status_ok:
            score -= 30
        if self.provider_gap_recoveries:
            score -= min(10.0, self.provider_gap_recoveries * 2.0)
        return max(0.0, min(100.0, score))


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


@dataclass
class SwingPoint:
    ts: int
    price: float
    kind: str  # "HIGH" | "LOW"


@dataclass
class StructureState:
    timeframe: str
    trend: TrendLabel
    swings: list = field(default_factory=list)  # list[SwingPoint]
    sequence: list = field(default_factory=list)  # e.g. ["HH","HL","HH","HL"]
    last_break: Optional[str] = None  # "BOS_UP" | "BOS_DOWN" | "CHOCH_UP" | "CHOCH_DOWN" | None
    failed_break: bool = False
    consolidation: bool = False
    expansion: bool = False
    structure_quality: float = 0.0
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------


@dataclass
class MomentumState:
    direction: TrendLabel
    roc_short: float
    roc_long: float
    accelerating: bool
    decelerating: bool
    persistence_bars: int
    exhaustion: bool
    momentum_quality: float = 0.0
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


@dataclass
class VolatilityState:
    atr: float
    relative_volatility: float  # current TR / median ATR, ~1.0 = normal
    expansion: bool
    compression: bool
    abnormal: bool
    volatility_quality: float = 0.0
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------


@dataclass
class LiquidityState:
    recent_high: Optional[float]
    recent_low: Optional[float]
    prior_session_high: Optional[float]
    prior_session_low: Optional[float]
    equal_highs: bool
    equal_lows: bool
    swept_high: bool
    swept_low: bool
    rejection_after_sweep: bool
    liquidity_quality: float = 0.0
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Price behaviour
# ---------------------------------------------------------------------------


@dataclass
class PriceBehaviorState:
    impulse: bool
    pullback: bool
    continuation: bool
    reversal: bool
    range_behavior: bool
    breakout: bool
    failed_breakout: bool
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Time behaviour
# ---------------------------------------------------------------------------


@dataclass
class TimeBehaviorState:
    session: str  # "ASIAN" | "LONDON" | "NEWYORK" | "OVERLAP"
    setup_age_minutes: Optional[float]
    is_stale: bool
    travel_ratio: Optional[float]
    travelled_too_far: bool


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@dataclass
class Setup:
    instrument: str
    setup_type: str
    direction: Direction
    formation_time: int
    entry_candidate: float
    invalidation: float
    target_candidate: float
    confidence: float  # 0-100 internal setup-quality, explainable
    reasoning: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class ExecutionState:
    risk: float
    reward: float
    rr: float
    distance_to_invalidation: float
    volatility_to_stop_ratio: float
    slippage_sensitivity: str  # "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN"
    execution_quality: float = 0.0
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Combined per-instrument analysis
# ---------------------------------------------------------------------------


@dataclass
class InstrumentAnalysis:
    instrument: str
    as_of: int
    current_price: Optional[float]
    data_quality: DataQualityReport
    structure: dict = field(default_factory=dict)  # timeframe -> StructureState
    momentum: Optional[MomentumState] = None
    volatility: Optional[VolatilityState] = None
    liquidity: Optional[LiquidityState] = None
    price_behavior: Optional[PriceBehaviorState] = None
    setup: Optional[Setup] = None
    execution: Optional[ExecutionState] = None
    time_behavior: Optional[TimeBehaviorState] = None
    regime: str = "UNKNOWN"


# ---------------------------------------------------------------------------
# Scoring / gates
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    passed: bool
    reasons: list = field(default_factory=list)


@dataclass
class ScoreBreakdown:
    structure_quality: float
    momentum_quality: float
    liquidity_quality: float
    volatility_quality: float
    setup_quality: float
    execution_quality: float
    historical_conditional_quality: float
    data_quality: float
    final_score: float
    historical_sample_size: int = 0
    historical_calibrated: bool = False


@dataclass
class TournamentRow:
    instrument: str
    status: CandidateStatus
    score: Optional[float]
    breakdown: Optional[ScoreBreakdown]
    reasons: list = field(default_factory=list)
    setup: Optional[Setup] = None
    execution: Optional[ExecutionState] = None
    data_freshness_seconds: Optional[float] = None
    setup_age_minutes: Optional[float] = None


@dataclass
class TournamentResult:
    ts: int
    rows: list  # list[TournamentRow], all instruments, internal only
    winner: Optional[TournamentRow]
    second: Optional[TournamentRow]
    lead: Optional[float]
    candidates_evaluated: int
    data_coverage: str  # e.g. "8/10"
    min_quality_threshold: float


@dataclass
class CandidateOutcome:
    tournament_ts: int
    instrument: str
    setup_type: str
    direction: str
    entry: float
    stop: float
    target: float
    regime: str
    session: str
    volatility: float
    structure_state: str
    mfe: Optional[float] = None
    mae: Optional[float] = None
    outcome_1m: Optional[float] = None
    outcome_5m: Optional[float] = None
    outcome_10m: Optional[float] = None
    outcome_15m: Optional[float] = None
    outcome_30m: Optional[float] = None
    time_to_mfe: Optional[int] = None
    time_to_mae: Optional[int] = None
    resolved: bool = False
