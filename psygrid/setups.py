"""Setup detection.

One shared rule set is evaluated identically for every instrument. Each
rule, if triggered, produces a fully-specified, measurable candidate
(entry/invalidation/target/confidence) — never a bare label. When several
rules trigger at once, only the single highest-confidence candidate is kept
per instrument; cross-instrument selection happens later in the tournament.

None of these rules assert that the setup is profitable — that is left to
:mod:`psygrid.persistence` historical-outcome tracking.
"""

from __future__ import annotations

from typing import List, Optional

from .models import (
    Candle,
    Direction,
    LiquidityState,
    MomentumState,
    PriceBehaviorState,
    Setup,
    StructureState,
    TrendLabel,
    VolatilityState,
)

MIN_ATR_STOP_MULT = 1.0


def _candidate(
    instrument: str,
    setup_type: str,
    direction: Direction,
    formation_time: int,
    entry: float,
    invalidation: float,
    target: float,
    confidence: float,
    reasoning: List[str],
) -> Optional[Setup]:
    risk = abs(entry - invalidation)
    reward = abs(target - entry)
    if risk <= 0 or reward <= 0:
        return None
    return Setup(
        instrument=instrument,
        setup_type=setup_type,
        direction=direction,
        formation_time=formation_time,
        entry_candidate=entry,
        invalidation=invalidation,
        target_candidate=target,
        confidence=max(0.0, min(100.0, confidence)),
        reasoning=reasoning,
    )


def _rule_liquidity_sweep_reversal(
    instrument: str, m1: List[Candle], liquidity: LiquidityState, atr: float
) -> Optional[Setup]:
    if not liquidity.rejection_after_sweep:
        return None
    last = m1[-1]
    if liquidity.swept_high:
        entry = last.close
        invalidation = last.high + atr * 0.25
        target = liquidity.recent_low if liquidity.recent_low is not None else entry - atr * 2
        return _candidate(
            instrument,
            "liquidity_sweep_reversal",
            Direction.SHORT,
            last.ts,
            entry,
            invalidation,
            target,
            confidence=70.0 + liquidity.liquidity_quality * 0.2,
            reasoning=liquidity.notes,
        )
    if liquidity.swept_low:
        entry = last.close
        invalidation = last.low - atr * 0.25
        target = liquidity.recent_high if liquidity.recent_high is not None else entry + atr * 2
        return _candidate(
            instrument,
            "liquidity_sweep_reversal",
            Direction.LONG,
            last.ts,
            entry,
            invalidation,
            target,
            confidence=70.0 + liquidity.liquidity_quality * 0.2,
            reasoning=liquidity.notes,
        )
    return None


def _rule_trend_pullback_continuation(
    instrument: str,
    m1: List[Candle],
    context_structure: StructureState,
    momentum: MomentumState,
    price_behavior: PriceBehaviorState,
    atr: float,
) -> Optional[Setup]:
    if context_structure.trend not in (TrendLabel.UP, TrendLabel.DOWN):
        return None
    if momentum.exhaustion:
        return None
    if momentum.direction != context_structure.trend:
        return None

    last = m1[-1]
    swing_lows = [s for s in context_structure.swings if s.kind == "LOW"]
    swing_highs = [s for s in context_structure.swings if s.kind == "HIGH"]
    setup_type = "pullback_continuation" if price_behavior.pullback else "trend_continuation"
    confidence = 55.0 + context_structure.structure_quality * 0.25 + momentum.momentum_quality * 0.15
    if price_behavior.pullback:
        confidence += 10.0

    if context_structure.trend == TrendLabel.UP:
        if not swing_lows:
            return None
        invalidation = swing_lows[-1].price - atr * 0.2
        entry = last.close
        risk = entry - invalidation
        target = entry + risk * 2.0
        return _candidate(
            instrument,
            setup_type,
            Direction.LONG,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            context_structure.notes + momentum.notes,
        )
    else:
        if not swing_highs:
            return None
        invalidation = swing_highs[-1].price + atr * 0.2
        entry = last.close
        risk = invalidation - entry
        target = entry - risk * 2.0
        return _candidate(
            instrument,
            setup_type,
            Direction.SHORT,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            context_structure.notes + momentum.notes,
        )


def _rule_breakout_confirmation(
    instrument: str,
    m1: List[Candle],
    structure: StructureState,
    price_behavior: PriceBehaviorState,
    atr: float,
) -> Optional[Setup]:
    if not price_behavior.breakout:
        return None
    last = m1[-1]
    swing_lows = [s for s in structure.swings if s.kind == "LOW"]
    swing_highs = [s for s in structure.swings if s.kind == "HIGH"]
    confidence = 60.0 + structure.structure_quality * 0.3

    if structure.last_break == "BOS_UP" and swing_lows:
        invalidation = swing_lows[-1].price
        entry = last.close
        risk = entry - invalidation
        if risk <= 0:
            return None
        target = entry + risk * 2.2
        return _candidate(
            instrument,
            "breakout_with_confirmation",
            Direction.LONG,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            structure.notes + price_behavior.notes,
        )
    if structure.last_break == "BOS_DOWN" and swing_highs:
        invalidation = swing_highs[-1].price
        entry = last.close
        risk = invalidation - entry
        if risk <= 0:
            return None
        target = entry - risk * 2.2
        return _candidate(
            instrument,
            "breakout_with_confirmation",
            Direction.SHORT,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            structure.notes + price_behavior.notes,
        )
    return None


def _rule_failed_breakout(
    instrument: str, m1: List[Candle], structure: StructureState, atr: float
) -> Optional[Setup]:
    if not structure.failed_break:
        return None
    last = m1[-1]
    swing_highs = [s for s in structure.swings if s.kind == "HIGH"]
    swing_lows = [s for s in structure.swings if s.kind == "LOW"]
    confidence = 50.0 + structure.structure_quality * 0.2

    # A failed break above resolves short back into range; a failed break
    # below resolves long.
    if swing_highs and any(c.high > swing_highs[-1].price for c in m1[-10:]):
        entry = last.close
        invalidation = max(c.high for c in m1[-10:]) + atr * 0.15
        target = swing_lows[-1].price if swing_lows else entry - atr * 2
        return _candidate(
            instrument,
            "failed_breakout",
            Direction.SHORT,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            structure.notes,
        )
    if swing_lows and any(c.low < swing_lows[-1].price for c in m1[-10:]):
        entry = last.close
        invalidation = min(c.low for c in m1[-10:]) - atr * 0.15
        target = swing_highs[-1].price if swing_highs else entry + atr * 2
        return _candidate(
            instrument,
            "failed_breakout",
            Direction.LONG,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            structure.notes,
        )
    return None


def _rule_momentum_expansion(
    instrument: str, m1: List[Candle], momentum: MomentumState, volatility: VolatilityState
) -> Optional[Setup]:
    if not (momentum.accelerating and volatility.expansion and not momentum.exhaustion):
        return None
    if momentum.direction not in (TrendLabel.UP, TrendLabel.DOWN):
        return None
    last = m1[-1]
    atr = volatility.atr
    if atr <= 0:
        return None
    confidence = 45.0 + momentum.momentum_quality * 0.2 + volatility.volatility_quality * 0.15

    if momentum.direction == TrendLabel.UP:
        entry = last.close
        invalidation = entry - atr * (MIN_ATR_STOP_MULT + 0.2)
        target = entry + atr * 2.5
        return _candidate(
            instrument,
            "momentum_expansion",
            Direction.LONG,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            momentum.notes + volatility.notes,
        )
    else:
        entry = last.close
        invalidation = entry + atr * (MIN_ATR_STOP_MULT + 0.2)
        target = entry - atr * 2.5
        return _candidate(
            instrument,
            "momentum_expansion",
            Direction.SHORT,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            momentum.notes + volatility.notes,
        )


def _rule_range_rejection(
    instrument: str, m1: List[Candle], structure: StructureState, liquidity: LiquidityState, atr: float
) -> Optional[Setup]:
    if structure.trend != TrendLabel.RANGE:
        return None
    if liquidity.recent_high is None or liquidity.recent_low is None:
        return None
    last = m1[-1]
    range_mid = (liquidity.recent_high + liquidity.recent_low) / 2.0
    near_top = last.high >= liquidity.recent_high - atr * 0.3
    near_bottom = last.low <= liquidity.recent_low + atr * 0.3
    confidence = 40.0 + structure.structure_quality * 0.15

    if near_top and last.close < liquidity.recent_high:
        entry = last.close
        invalidation = liquidity.recent_high + atr * 0.25
        target = range_mid
        return _candidate(
            instrument,
            "range_rejection",
            Direction.SHORT,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            structure.notes,
        )
    if near_bottom and last.close > liquidity.recent_low:
        entry = last.close
        invalidation = liquidity.recent_low - atr * 0.25
        target = range_mid
        return _candidate(
            instrument,
            "range_rejection",
            Direction.LONG,
            last.ts,
            entry,
            invalidation,
            target,
            confidence,
            structure.notes,
        )
    return None


def detect_best_setup(
    instrument: str,
    m1: List[Candle],
    structure_m1: StructureState,
    structure_context: StructureState,
    momentum: MomentumState,
    volatility: VolatilityState,
    liquidity: LiquidityState,
    price_behavior: PriceBehaviorState,
) -> Optional[Setup]:
    if len(m1) < 5:
        return None
    atr = volatility.atr

    candidates: List[Optional[Setup]] = [
        _rule_liquidity_sweep_reversal(instrument, m1, liquidity, atr),
        _rule_trend_pullback_continuation(instrument, m1, structure_context, momentum, price_behavior, atr),
        _rule_breakout_confirmation(instrument, m1, structure_m1, price_behavior, atr),
        _rule_failed_breakout(instrument, m1, structure_m1, atr),
        _rule_momentum_expansion(instrument, m1, momentum, volatility),
        _rule_range_rejection(instrument, m1, structure_m1, liquidity, atr),
    ]
    valid = [c for c in candidates if c is not None]
    if not valid:
        return None
    valid.sort(key=lambda s: s.confidence, reverse=True)
    return valid[0]
