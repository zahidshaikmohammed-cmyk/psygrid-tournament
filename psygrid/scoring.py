"""Hard gates and explainable scoring.

Two clearly separated stages, per spec:

1. Hard gates — pass/fail. Anything that fails is REJECTed before scoring
   even runs; scores are never used to "rescue" a disqualified candidate.
2. Quality scoring — every surviving candidate gets an auditable breakdown
   of named components, combined via a declared (not secretly tuned)
   weighted sum. Components are explicitly labelled "scores", never
   "probabilities", unless/until calibrated against real outcome history.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

from .config import Config
from .models import Direction, GateResult, InstrumentAnalysis, ScoreBreakdown

HistoricalStatsFn = Callable[[str, str, str], Tuple[float, int, bool]]

CONTEXT_TIMEFRAME = "M15"


def evaluate_hard_gates(analysis: InstrumentAnalysis, config: Config) -> GateResult:
    reasons = []

    if not analysis.data_quality.is_usable:
        reasons.extend([f"DATA: {issue}" for issue in analysis.data_quality.issues])
        return GateResult(passed=False, reasons=reasons or ["Data quality gate failed."])

    if analysis.setup is None:
        return GateResult(passed=False, reasons=["No qualifying setup detected."])

    if analysis.volatility and analysis.volatility.abnormal:
        reasons.append("Abnormal volatility spike — unacceptable execution risk.")

    if analysis.execution is None:
        reasons.append("Execution metrics could not be computed.")
    elif analysis.execution.rr < config.min_rr:
        reasons.append(f"R:R {analysis.execution.rr:.2f} below minimum {config.min_rr:.2f}.")

    if analysis.time_behavior:
        if analysis.time_behavior.is_stale:
            reasons.append(
                f"Setup age {analysis.time_behavior.setup_age_minutes:.1f}m exceeds "
                f"max {config.max_setup_age_minutes:.1f}m."
            )
        if analysis.time_behavior.travelled_too_far:
            reasons.append("Price has already travelled too far from the entry candidate.")

    if analysis.current_price is not None and analysis.setup is not None:
        price = analysis.current_price
        setup = analysis.setup
        if setup.direction == Direction.LONG and price <= setup.invalidation:
            reasons.append("Setup already invalidated (price at/through stop level).")
        elif setup.direction == Direction.SHORT and price >= setup.invalidation:
            reasons.append("Setup already invalidated (price at/through stop level).")

    return GateResult(passed=len(reasons) == 0, reasons=reasons)


def score_candidate(
    analysis: InstrumentAnalysis,
    config: Config,
    historical_stats_fn: Optional[HistoricalStatsFn] = None,
) -> ScoreBreakdown:
    context_structure = analysis.structure.get(CONTEXT_TIMEFRAME) or analysis.structure.get("M1")
    structure_quality = context_structure.structure_quality if context_structure else 0.0
    momentum_quality = analysis.momentum.momentum_quality if analysis.momentum else 0.0
    liquidity_quality = analysis.liquidity.liquidity_quality if analysis.liquidity else 0.0
    volatility_quality = analysis.volatility.volatility_quality if analysis.volatility else 0.0
    setup_quality = analysis.setup.confidence if analysis.setup else 0.0
    execution_quality = analysis.execution.execution_quality if analysis.execution else 0.0
    data_quality = analysis.data_quality.quality_score

    historical_quality = 50.0
    sample_size = 0
    calibrated = False
    if historical_stats_fn is not None and analysis.setup is not None:
        historical_quality, sample_size, calibrated = historical_stats_fn(
            analysis.instrument, analysis.setup.setup_type, analysis.regime
        )

    final_score = (
        structure_quality * config.weight_structure
        + momentum_quality * config.weight_momentum
        + liquidity_quality * config.weight_liquidity
        + volatility_quality * config.weight_volatility
        + setup_quality * config.weight_setup
        + execution_quality * config.weight_execution
        + historical_quality * config.weight_historical
        + data_quality * config.weight_data_quality
    )

    return ScoreBreakdown(
        structure_quality=structure_quality,
        momentum_quality=momentum_quality,
        liquidity_quality=liquidity_quality,
        volatility_quality=volatility_quality,
        setup_quality=setup_quality,
        execution_quality=execution_quality,
        historical_conditional_quality=historical_quality,
        data_quality=data_quality,
        final_score=round(final_score, 2),
        historical_sample_size=sample_size,
        historical_calibrated=calibrated,
    )
