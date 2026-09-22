"""Volatility analysis: ATR, relative volatility, expansion/compression, abnormal spikes."""

from __future__ import annotations

from typing import List

from .models import Candle, VolatilityState


def true_range(prev: Candle, cur: Candle) -> float:
    return max(
        cur.high - cur.low,
        abs(cur.high - prev.close),
        abs(cur.low - prev.close),
    )


def _atr(candles: List[Candle], period: int) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = [true_range(p, c) for p, c in zip(candles[-period - 1 : -1], candles[-period:])]
    return sum(trs) / len(trs) if trs else 0.0


def analyze_volatility(
    candles: List[Candle],
    atr_period: int = 14,
    median_window: int = 60,
    abnormal_multiple: float = 3.5,
) -> VolatilityState:
    notes: List[str] = []
    if len(candles) < atr_period + 2:
        return VolatilityState(
            atr=0.0,
            relative_volatility=1.0,
            expansion=False,
            compression=False,
            abnormal=False,
            volatility_quality=0.0,
            notes=["Insufficient history for volatility analysis."],
        )

    atr = _atr(candles, atr_period)
    current_tr = true_range(candles[-2], candles[-1])

    window = candles[-median_window:] if len(candles) >= median_window else candles
    historical_atrs = []
    for i in range(atr_period + 1, len(window)):
        historical_atrs.append(_atr(window[: i + 1], atr_period))
    historical_atrs = [a for a in historical_atrs if a > 0]

    if historical_atrs:
        sorted_atrs = sorted(historical_atrs)
        median_atr = sorted_atrs[len(sorted_atrs) // 2]
    else:
        median_atr = atr

    relative_volatility = (atr / median_atr) if median_atr > 0 else 1.0
    expansion = relative_volatility > 1.3
    compression = relative_volatility < 0.7
    abnormal = median_atr > 0 and current_tr > abnormal_multiple * median_atr

    if expansion:
        notes.append("ATR is expanding relative to its recent median.")
    if compression:
        notes.append("ATR is compressed relative to its recent median.")
    if abnormal:
        notes.append("Current bar's true range is an abnormal spike versus recent volatility.")

    quality = 60.0
    if abnormal:
        quality = 15.0
        notes.append("Abnormal volatility penalizes quality heavily — unreliable stop placement.")
    elif expansion:
        quality = 75.0
    elif compression:
        quality = 45.0
        notes.append("Compression can precede a breakout but currently offers thin room to target.")
    quality = max(0.0, min(100.0, quality))

    return VolatilityState(
        atr=atr,
        relative_volatility=relative_volatility,
        expansion=expansion,
        compression=compression,
        abnormal=abnormal,
        volatility_quality=quality,
        notes=notes,
    )
