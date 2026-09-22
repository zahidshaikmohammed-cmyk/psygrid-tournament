"""Price-behaviour classification: impulse, pullback, continuation, reversal,
range behaviour, breakout / failed breakout.

DERIVED FEATURE — a pure re-reading of the locally-derived structure and
volatility states plus raw OHLC body/wick geometry; no provider indicator
or additional data source is consulted.
"""

from __future__ import annotations

from typing import List

from .models import Candle, PriceBehaviorState, StructureState, TrendLabel, VolatilityState


def analyze_price_behavior(
    candles: List[Candle],
    structure: StructureState,
    volatility: VolatilityState,
    bars: int = 10,
) -> PriceBehaviorState:
    notes: List[str] = []
    if len(candles) < bars + 1:
        return PriceBehaviorState(
            impulse=False,
            pullback=False,
            continuation=False,
            reversal=False,
            range_behavior=False,
            breakout=False,
            failed_breakout=False,
            notes=["Insufficient history for price-behaviour analysis."],
        )

    recent = candles[-bars:]
    body_sizes = [abs(c.close - c.open) for c in recent]
    avg_body = sum(body_sizes) / len(body_sizes)
    last = recent[-1]
    last_body = abs(last.close - last.open)

    impulse = last_body > avg_body * 1.5
    if impulse:
        notes.append("Last bar's body is a strong impulse relative to recent average.")

    # Pullback: last 3 bars move counter to the immediately preceding
    # directional run.
    pullback = False
    if len(recent) >= 6:
        run = recent[-6:-3]
        tail = recent[-3:]
        run_dir = 1 if run[-1].close > run[0].close else -1
        tail_dir = 1 if tail[-1].close > tail[0].close else -1
        pullback = run_dir != tail_dir and abs(tail[-1].close - tail[0].close) < abs(
            run[-1].close - run[0].close
        )
        if pullback:
            notes.append("Recent bars show a shallow pullback against the prior run.")

    continuation = structure.trend in (TrendLabel.UP, TrendLabel.DOWN) and structure.last_break in (
        "BOS_UP",
        "BOS_DOWN",
    )
    if continuation:
        notes.append("Structure break aligns with the prevailing trend (continuation).")

    reversal = structure.last_break in ("CHOCH_UP", "CHOCH_DOWN")
    if reversal:
        notes.append("Structure break is counter-trend (possible reversal).")

    range_behavior = structure.trend == TrendLabel.RANGE and not impulse
    if range_behavior:
        notes.append("Price is oscillating without a confirmed directional structure.")

    breakout = impulse and volatility.expansion and structure.last_break in ("BOS_UP", "BOS_DOWN")
    if breakout:
        notes.append("Impulsive move with volatility expansion confirms a breakout.")

    failed_breakout = structure.failed_break
    if failed_breakout:
        notes.append("A recent breakout attempt failed to hold.")

    return PriceBehaviorState(
        impulse=impulse,
        pullback=pullback,
        continuation=continuation,
        reversal=reversal,
        range_behavior=range_behavior,
        breakout=breakout,
        failed_breakout=failed_breakout,
        notes=notes,
    )
