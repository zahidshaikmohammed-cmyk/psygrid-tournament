"""Genuine M1 -> higher-timeframe aggregation.

The engine never trusts provider-generated higher-timeframe candles. Every
M5/M15/M30/H1 candle used anywhere in this engine is built here, directly
from the rolling M1 series, and is always tagged ``synthetic_from_m1=True``.
"""

from __future__ import annotations

from typing import Dict, List

from .models import Candle

TIMEFRAME_MINUTES = {
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
}


def aggregate(m1_candles: List[Candle], timeframe: str) -> List[Candle]:
    """Aggregate ascending-ordered M1 candles into ``timeframe`` buckets.

    Buckets are aligned to UTC clock boundaries (e.g. M15 buckets start at
    :00/:15/:30/:45). Only buckets with at least one real M1 candle are
    emitted — no bucket is ever fabricated for a period with no data.
    """
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    bucket_seconds = TIMEFRAME_MINUTES[timeframe] * 60

    buckets: Dict[int, List[Candle]] = {}
    for candle in m1_candles:
        bucket_ts = (candle.ts // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_ts, []).append(candle)

    out: List[Candle] = []
    for bucket_ts in sorted(buckets.keys()):
        members = sorted(buckets[bucket_ts], key=lambda c: c.ts)
        instrument = members[0].instrument
        open_ = members[0].open
        close = members[-1].close
        high = max(c.high for c in members)
        low = min(c.low for c in members)
        has_volume = any(c.volume is not None for c in members)
        volume = sum(c.volume or 0.0 for c in members) if has_volume else None
        out.append(
            Candle(
                instrument=instrument,
                timeframe=timeframe,
                ts=bucket_ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                synthetic_from_m1=True,
            )
        )
    return out


def aggregate_all(m1_candles: List[Candle]) -> Dict[str, List[Candle]]:
    return {tf: aggregate(m1_candles, tf) for tf in TIMEFRAME_MINUTES}
