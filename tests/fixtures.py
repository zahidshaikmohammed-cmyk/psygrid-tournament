"""Deterministic synthetic fixtures for the PSYGRID test suite.

No randomness anywhere — every series is built from an explicit, reproducible
formula so tests are exactly repeatable and failures are diagnosable.
"""

from __future__ import annotations

import json
from typing import List, Optional

from psygrid.models import Candle

# Fixed epoch anchor, aligned to an hour boundary so every derived timeframe
# (M5/M15/M30/H1) buckets cleanly from the very first synthetic candle —
# avoids partial-bucket edge effects in aggregation tests.
BASE_TS = (1_700_000_000 // 3600) * 3600


def make_m1_series(
    instrument: str,
    closes: List[float],
    start_ts: int = BASE_TS,
    wick: float = 0.05,
) -> List[Candle]:
    """Build a deterministic, always-valid M1 candle series from close prices."""
    candles = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close
        # A tiny, deterministic, non-periodic per-bar jitter on the wick
        # avoids exact tied highs/lows between adjacent bars (which would
        # otherwise silently suppress fractal swing detection whenever two
        # bars happen to share an OHLC extreme, e.g. right around a
        # single-bar pullback). Magnitude is negligible versus `wick`.
        jitter = ((i * 37) % 13) * 1e-5
        high = max(open_, close) + wick + jitter
        low = min(open_, close) - wick - jitter
        candles.append(
            Candle(
                instrument=instrument,
                timeframe="M1",
                ts=start_ts + i * 60,
                open=round(open_, 5),
                high=round(high, 5),
                low=round(low, 5),
                close=round(close, 5),
                volume=100.0,
                synthetic_from_m1=False,
            )
        )
        prev_close = close
    return candles


def uptrend_closes(n: int, start: float = 100.0, step: float = 0.05, pullback_every: int = 15) -> List[float]:
    closes = []
    price = start
    for i in range(n):
        if pullback_every and i % pullback_every == pullback_every - 1:
            price -= step * 3
        else:
            price += step
        closes.append(round(price, 5))
    return closes


def downtrend_closes(n: int, start: float = 100.0, step: float = 0.05, pullback_every: int = 15) -> List[float]:
    closes = []
    price = start
    for i in range(n):
        if pullback_every and i % pullback_every == pullback_every - 1:
            price += step * 3
        else:
            price -= step
        closes.append(round(price, 5))
    return closes


def range_closes(n: int, center: float = 100.0, amplitude: float = 0.5, period: int = 20) -> List[float]:
    import math

    return [round(center + amplitude * math.sin(2 * math.pi * i / period), 5) for i in range(n)]


def flat_closes(n: int, price: float = 100.0) -> List[float]:
    return [price for _ in range(n)]


def sweep_and_reject_closes(n: int, base: float = 100.0) -> List[float]:
    """A quiet range that, on the final bar, sweeps above the range high and
    closes back below it (a textbook liquidity-sweep-reversal setup)."""
    closes = range_closes(n - 1, center=base, amplitude=0.3, period=20)
    closes.append(base + 0.05)  # closes back near the range after the sweep wick
    return closes


def make_sweep_series(instrument: str, n: int = 220, base: float = 100.0, start_ts: int = BASE_TS) -> List[Candle]:
    candles = make_m1_series(instrument, sweep_and_reject_closes(n, base), start_ts=start_ts, wick=0.05)
    last = candles[-1]
    swept_high = max(c.high for c in candles[:-1])
    candles[-1] = Candle(
        instrument=last.instrument,
        timeframe=last.timeframe,
        ts=last.ts,
        open=last.open,
        high=swept_high + 0.6,
        low=last.low,
        close=last.close,
        volume=last.volume,
        synthetic_from_m1=False,
    )
    return candles


def api_payload_json(instrument_candles: dict, server_time: Optional[int] = None) -> str:
    payload = {
        "server_time": server_time if server_time is not None else BASE_TS,
        "instruments": {
            symbol: {
                "candles": [
                    {
                        "time": c.ts,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
                    for c in candles
                ]
            }
            for symbol, candles in instrument_candles.items()
        },
    }
    return json.dumps(payload)
