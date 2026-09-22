"""Liquidity analysis: recent extremes, equal highs/lows, sweeps, rejection."""

from __future__ import annotations

from typing import List, Optional

from .models import Candle, LiquidityState

EQUAL_LEVEL_TOLERANCE_ATR_MULT = 0.15


def _cluster_equal(levels: List[float], tolerance: float) -> bool:
    levels = sorted(levels)
    for a, b in zip(levels, levels[1:]):
        if abs(a - b) <= tolerance:
            return True
    return False


def analyze_liquidity(
    m1_candles: List[Candle],
    h1_candles: List[Candle],
    atr: float,
    lookback_bars: int = 60,
) -> LiquidityState:
    notes: List[str] = []
    if len(m1_candles) < 5:
        return LiquidityState(
            recent_high=None,
            recent_low=None,
            prior_session_high=None,
            prior_session_low=None,
            equal_highs=False,
            equal_lows=False,
            swept_high=False,
            swept_low=False,
            rejection_after_sweep=False,
            liquidity_quality=0.0,
            notes=["Insufficient history for liquidity analysis."],
        )

    recent = m1_candles[-lookback_bars:] if len(m1_candles) > lookback_bars else m1_candles
    recent_high = max(c.high for c in recent[:-1]) if len(recent) > 1 else recent[-1].high
    recent_low = min(c.low for c in recent[:-1]) if len(recent) > 1 else recent[-1].low

    prior_session_high = None
    prior_session_low = None
    if len(h1_candles) >= 2:
        prior_block = h1_candles[-25:-1] if len(h1_candles) > 25 else h1_candles[:-1]
        if prior_block:
            prior_session_high = max(c.high for c in prior_block)
            prior_session_low = min(c.low for c in prior_block)

    tolerance = max(atr * EQUAL_LEVEL_TOLERANCE_ATR_MULT, 1e-9)
    recent_highs = [c.high for c in recent[-20:]]
    recent_lows = [c.low for c in recent[-20:]]
    equal_highs = _cluster_equal(recent_highs, tolerance)
    equal_lows = _cluster_equal(recent_lows, tolerance)
    if equal_highs:
        notes.append("Equal highs detected — resting liquidity above.")
    if equal_lows:
        notes.append("Equal lows detected — resting liquidity below.")

    last = recent[-1]
    prev = recent[-2] if len(recent) > 1 else recent[-1]

    swept_high = last.high > recent_high and last.close < recent_high
    swept_low = last.low < recent_low and last.close > recent_low
    rejection_after_sweep = False
    if swept_high:
        notes.append("Recent high swept with a close back below it.")
        body = abs(last.close - last.open)
        upper_wick = last.high - max(last.open, last.close)
        if upper_wick > body:
            rejection_after_sweep = True
            notes.append("Rejection candle confirms the sweep (long upper wick).")
    if swept_low:
        notes.append("Recent low swept with a close back above it.")
        body = abs(last.close - last.open)
        lower_wick = min(last.open, last.close) - last.low
        if lower_wick > body:
            rejection_after_sweep = True
            notes.append("Rejection candle confirms the sweep (long lower wick).")

    quality = 30.0
    if swept_high or swept_low:
        quality += 30.0
    if rejection_after_sweep:
        quality += 25.0
    if equal_highs or equal_lows:
        quality += 10.0
    quality = max(0.0, min(100.0, quality))

    return LiquidityState(
        recent_high=recent_high,
        recent_low=recent_low,
        prior_session_high=prior_session_high,
        prior_session_low=prior_session_low,
        equal_highs=equal_highs,
        equal_lows=equal_lows,
        swept_high=swept_high,
        swept_low=swept_low,
        rejection_after_sweep=rejection_after_sweep,
        liquidity_quality=quality,
        notes=notes,
    )
