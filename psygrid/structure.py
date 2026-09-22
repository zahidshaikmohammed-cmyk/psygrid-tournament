"""Market structure analysis: swings, HH/HL/LH/LL, breaks, consolidation.

One deterministic fractal-swing methodology is applied identically to every
instrument and timeframe — no per-instrument special casing.
"""

from __future__ import annotations

from typing import List, Optional

from .models import Candle, StructureState, SwingPoint, TrendLabel

FRACTAL_WING = 2  # bars required on each side to confirm a swing point


def find_swings(candles: List[Candle], wing: int = FRACTAL_WING) -> List[SwingPoint]:
    swings: List[SwingPoint] = []
    n = len(candles)
    for i in range(wing, n - wing):
        window = candles[i - wing : i + wing + 1]
        c = candles[i]
        if c.high == max(k.high for k in window) and c.high > max(
            k.high for k in window if k is not c
        ):
            swings.append(SwingPoint(ts=c.ts, price=c.high, kind="HIGH"))
        if c.low == min(k.low for k in window) and c.low < min(
            k.low for k in window if k is not c
        ):
            swings.append(SwingPoint(ts=c.ts, price=c.low, kind="LOW"))
    swings.sort(key=lambda s: s.ts)
    return swings


def _label_sequence(swings: List[SwingPoint]) -> List[str]:
    labels: List[str] = []
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    for s in swings:
        if s.kind == "HIGH":
            if last_high is None:
                labels.append("H")
            else:
                labels.append("HH" if s.price > last_high else "LH")
            last_high = s.price
        else:
            if last_low is None:
                labels.append("L")
            else:
                labels.append("HL" if s.price > last_low else "LL")
            last_low = s.price
    return labels


def analyze_structure(candles: List[Candle], timeframe: str, lookback_bars: int = 120) -> StructureState:
    recent = candles[-lookback_bars:] if len(candles) > lookback_bars else candles
    swings = find_swings(recent)
    sequence = _label_sequence(swings)
    notes: List[str] = []

    highs_seq = [lbl for s, lbl in zip(swings, sequence) if s.kind == "HIGH"]
    lows_seq = [lbl for s, lbl in zip(swings, sequence) if s.kind == "LOW"]

    trend = TrendLabel.RANGE
    if len(highs_seq) >= 2 and len(lows_seq) >= 2:
        up = highs_seq[-1] == "HH" and lows_seq[-1] == "HL"
        down = highs_seq[-1] == "LH" and lows_seq[-1] == "LL"
        if up and not down:
            trend = TrendLabel.UP
            notes.append("Higher-high + higher-low sequence confirmed.")
        elif down and not up:
            trend = TrendLabel.DOWN
            notes.append("Lower-high + lower-low sequence confirmed.")
        else:
            trend = TrendLabel.RANGE
    else:
        trend = TrendLabel.UNKNOWN
        notes.append("Insufficient confirmed swings to classify trend.")

    # Structure break / failed break: compare last close to most recent
    # opposite-type swing prior to it.
    last_break = None
    failed_break = False
    if swings and recent:
        last_close = recent[-1].close
        highs = [s for s in swings if s.kind == "HIGH"]
        lows = [s for s in swings if s.kind == "LOW"]
        last_swing_high = highs[-1] if highs else None
        last_swing_low = lows[-1] if lows else None

        if last_swing_high and last_close > last_swing_high.price:
            last_break = "BOS_UP" if trend == TrendLabel.UP else "CHOCH_UP"
        elif last_swing_low and last_close < last_swing_low.price:
            last_break = "BOS_DOWN" if trend == TrendLabel.DOWN else "CHOCH_DOWN"

        # Failed break: price broke a swing point within the recent window
        # but the *current* close has already returned back inside it.
        if last_swing_high:
            broke_and_returned = any(
                c.high > last_swing_high.price for c in recent[-10:]
            ) and last_close < last_swing_high.price
            if broke_and_returned:
                failed_break = True
                notes.append("Break above recent swing high failed to hold.")
        if last_swing_low:
            broke_and_returned = any(
                c.low < last_swing_low.price for c in recent[-10:]
            ) and last_close > last_swing_low.price
            if broke_and_returned:
                failed_break = True
                notes.append("Break below recent swing low failed to hold.")

    # Consolidation vs expansion: compare recent range to prior range.
    consolidation = False
    expansion = False
    if len(recent) >= 40:
        recent_range = max(c.high for c in recent[-20:]) - min(c.low for c in recent[-20:])
        prior_range = max(c.high for c in recent[-40:-20]) - min(c.low for c in recent[-40:-20])
        if prior_range > 0:
            ratio = recent_range / prior_range
            if ratio < 0.65:
                consolidation = True
                notes.append("Recent range has contracted versus the prior window.")
            elif ratio > 1.4:
                expansion = True
                notes.append("Recent range has expanded versus the prior window.")

    quality = 0.0
    if trend in (TrendLabel.UP, TrendLabel.DOWN):
        quality += 55.0
    elif trend == TrendLabel.RANGE:
        quality += 25.0
    if last_break in ("BOS_UP", "BOS_DOWN"):
        quality += 25.0
    elif last_break in ("CHOCH_UP", "CHOCH_DOWN"):
        quality += 10.0
    if failed_break:
        quality -= 20.0
    if expansion:
        quality += 10.0
    if consolidation:
        quality -= 5.0
    quality = max(0.0, min(100.0, quality))

    return StructureState(
        timeframe=timeframe,
        trend=trend,
        swings=swings,
        sequence=sequence,
        last_break=last_break,
        failed_break=failed_break,
        consolidation=consolidation,
        expansion=expansion,
        structure_quality=quality,
        notes=notes,
    )
