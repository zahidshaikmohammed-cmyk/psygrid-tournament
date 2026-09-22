"""Execution-quality analysis: R:R, distance to invalidation, slippage sensitivity.

DERIVED FEATURE — built entirely from a candidate Setup's own price levels
(themselves derived from raw OHLCV) plus the locally-derived ATR. No
provider-side risk, spread, or liquidity-depth data exists to consult;
`slippage_sensitivity` is explicitly reported "UNKNOWN" rather than guessed
whenever a real spread estimate is not supplied.
"""

from __future__ import annotations

from typing import List, Optional

from .models import Direction, ExecutionState, Setup


def analyze_execution(
    setup: Setup,
    atr: float,
    spread_estimate: Optional[float] = None,
) -> ExecutionState:
    notes: List[str] = []
    risk = abs(setup.entry_candidate - setup.invalidation)
    reward = abs(setup.target_candidate - setup.entry_candidate)
    rr = reward / risk if risk > 0 else 0.0
    volatility_to_stop_ratio = (atr / risk) if risk > 0 else 0.0

    if spread_estimate is not None and risk > 0:
        spread_ratio = spread_estimate / risk
        if spread_ratio > 0.25:
            slippage_sensitivity = "HIGH"
        elif spread_ratio > 0.1:
            slippage_sensitivity = "MEDIUM"
        else:
            slippage_sensitivity = "LOW"
    else:
        slippage_sensitivity = "UNKNOWN"
        notes.append("No spread/liquidity-depth data available — slippage sensitivity is UNKNOWN.")

    quality = 0.0
    if rr >= 3.0:
        quality += 45.0
    elif rr >= 2.0:
        quality += 35.0
    elif rr >= 1.5:
        quality += 20.0
    else:
        quality += 5.0
        notes.append(f"R:R of {rr:.2f} is below the generally-accepted 1.5 floor.")

    if 0.5 <= volatility_to_stop_ratio <= 1.5:
        quality += 30.0
    elif volatility_to_stop_ratio < 0.5:
        quality += 10.0
        notes.append("Stop is wide relative to current volatility.")
    else:
        quality += 5.0
        notes.append("Stop is tight relative to current volatility — elevated risk of a noise stop-out.")

    if slippage_sensitivity == "LOW":
        quality += 15.0
    elif slippage_sensitivity == "MEDIUM":
        quality += 8.0
    elif slippage_sensitivity == "UNKNOWN":
        quality += 10.0

    quality = max(0.0, min(100.0, quality))

    return ExecutionState(
        risk=risk,
        reward=reward,
        rr=rr,
        distance_to_invalidation=risk,
        volatility_to_stop_ratio=volatility_to_stop_ratio,
        slippage_sensitivity=slippage_sensitivity,
        execution_quality=quality,
        notes=notes,
    )
