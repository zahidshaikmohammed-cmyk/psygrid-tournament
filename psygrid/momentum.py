"""Momentum analysis: direction, acceleration, persistence, exhaustion.

Uses only price data already present in the candle series (no RSI/EMA —
added per-instrument indicator libraries are explicitly avoided; instead we
measure rate-of-change directly, which is simpler and equally auditable).
"""

from __future__ import annotations

from typing import List

from .models import Candle, MomentumState, TrendLabel


def _roc(candles: List[Candle], bars: int) -> float:
    if len(candles) <= bars:
        return 0.0
    start = candles[-bars - 1].close
    end = candles[-1].close
    if start == 0:
        return 0.0
    return (end - start) / abs(start)


def analyze_momentum(candles: List[Candle], short_bars: int = 5, long_bars: int = 20) -> MomentumState:
    notes: List[str] = []
    if len(candles) < long_bars + 1:
        return MomentumState(
            direction=TrendLabel.UNKNOWN,
            roc_short=0.0,
            roc_long=0.0,
            accelerating=False,
            decelerating=False,
            persistence_bars=0,
            exhaustion=False,
            momentum_quality=0.0,
            notes=["Insufficient history for momentum analysis."],
        )

    roc_short = _roc(candles, short_bars)
    roc_long = _roc(candles, long_bars)

    if roc_short > 0 and roc_long > 0:
        direction = TrendLabel.UP
    elif roc_short < 0 and roc_long < 0:
        direction = TrendLabel.DOWN
    else:
        direction = TrendLabel.RANGE
        notes.append("Short- and long-term momentum disagree in direction.")

    # Acceleration: is the most recent short window moving faster than the
    # window immediately before it, in the same direction?
    prior_short = _roc(candles[:-short_bars], short_bars) if len(candles) > 2 * short_bars else 0.0
    accelerating = False
    decelerating = False
    if direction in (TrendLabel.UP, TrendLabel.DOWN):
        if abs(roc_short) > abs(prior_short) * 1.1:
            accelerating = True
            notes.append("Short-term momentum is accelerating.")
        elif abs(roc_short) < abs(prior_short) * 0.75:
            decelerating = True
            notes.append("Short-term momentum is decelerating.")

    # Persistence: consecutive closes in the same direction.
    persistence = 0
    for prev, cur in zip(reversed(candles[:-1]), reversed(candles[1:])):
        if direction == TrendLabel.UP and cur.close > prev.close:
            persistence += 1
        elif direction == TrendLabel.DOWN and cur.close < prev.close:
            persistence += 1
        else:
            break

    # Exhaustion: price extended further over the long window while short
    # window momentum has clearly decelerated (simple divergence proxy).
    exhaustion = decelerating and abs(roc_long) > abs(roc_short) * 3 and persistence <= 1
    if exhaustion:
        notes.append("Momentum shows signs of exhaustion (deceleration after extension).")

    quality = 0.0
    if direction in (TrendLabel.UP, TrendLabel.DOWN):
        quality += 40.0
    if accelerating:
        quality += 25.0
    quality += min(20.0, persistence * 4.0)
    if exhaustion:
        quality -= 30.0
    if decelerating and not exhaustion:
        quality -= 10.0
    quality = max(0.0, min(100.0, quality))

    return MomentumState(
        direction=direction,
        roc_short=roc_short,
        roc_long=roc_long,
        accelerating=accelerating,
        decelerating=decelerating,
        persistence_bars=persistence,
        exhaustion=exhaustion,
        momentum_quality=quality,
        notes=notes,
    )
