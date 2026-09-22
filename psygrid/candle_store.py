"""Rolling per-instrument M1 candle state.

Maintains up to ``max_candles`` genuine M1 candles per instrument, merges
newly-fetched candles idempotently (by timestamp), never fabricates a
missing candle, and reports continuity diagnostics on every ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import Candle

M1_SECONDS = 60


@dataclass
class IngestReport:
    instrument: str
    new_candles: int
    duplicate_candles: int
    invalid_candles: int
    missing_candle_count: int
    is_continuous: bool
    timestamps_valid: bool
    latest_ts: Optional[int]
    history_count: int


class InstrumentCandleStore:
    def __init__(self, max_candles: int = 1600):
        self.max_candles = max_candles
        self._by_ts: Dict[int, Candle] = {}

    def ingest(self, instrument: str, candles: List[Candle]) -> IngestReport:
        new_count = 0
        dup_count = 0
        invalid_count = 0
        now_ordered_ts = None
        timestamps_valid = True

        for candle in candles:
            if candle.ts is None or candle.ts <= 0:
                invalid_count += 1
                timestamps_valid = False
                continue
            if candle.ts % M1_SECONDS != 0:
                # M1 candle opens should align to whole minutes; not fatal,
                # but flagged as a timestamp-validity issue.
                timestamps_valid = False
            if not candle.is_valid_ohlc():
                invalid_count += 1
                continue
            if candle.ts in self._by_ts:
                if self._by_ts[candle.ts] == candle:
                    dup_count += 1
                    continue
                # Same timestamp, different values: provider corrected the
                # candle (common for the most recent, still-forming one).
                # Accept the newer value; never average/fabricate.
            else:
                new_count += 1
            self._by_ts[candle.ts] = candle
            if now_ordered_ts is None or candle.ts > now_ordered_ts:
                now_ordered_ts = candle.ts

        # Trim to rolling window.
        if len(self._by_ts) > self.max_candles:
            for ts in sorted(self._by_ts.keys())[: len(self._by_ts) - self.max_candles]:
                del self._by_ts[ts]

        ordered = self.ordered_candles()
        missing = 0
        is_continuous = True
        for prev, cur in zip(ordered, ordered[1:]):
            gap = (cur.ts - prev.ts) // M1_SECONDS
            if gap > 1:
                missing += gap - 1
                is_continuous = False
            elif gap < 1:
                is_continuous = False

        return IngestReport(
            instrument=instrument,
            new_candles=new_count,
            duplicate_candles=dup_count,
            invalid_candles=invalid_count,
            missing_candle_count=missing,
            is_continuous=is_continuous,
            timestamps_valid=timestamps_valid,
            latest_ts=ordered[-1].ts if ordered else None,
            history_count=len(ordered),
        )

    def ordered_candles(self) -> List[Candle]:
        return [self._by_ts[ts] for ts in sorted(self._by_ts.keys())]

    def __len__(self) -> int:
        return len(self._by_ts)


class MultiInstrumentCandleStore:
    """Owns one :class:`InstrumentCandleStore` per instrument."""

    def __init__(self, max_candles: int = 1600):
        self.max_candles = max_candles
        self._stores: Dict[str, InstrumentCandleStore] = {}

    def ingest(self, instrument: str, candles: List[Candle]) -> IngestReport:
        store = self._stores.setdefault(instrument, InstrumentCandleStore(self.max_candles))
        return store.ingest(instrument, candles)

    def candles(self, instrument: str) -> List[Candle]:
        store = self._stores.get(instrument)
        return store.ordered_candles() if store else []

    def instruments(self) -> List[str]:
        return list(self._stores.keys())
