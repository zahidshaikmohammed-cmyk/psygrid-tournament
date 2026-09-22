"""Data-quality assessment: freshness, continuity, history sufficiency.

NO DATA = UNKNOWN. This module never fabricates missing candles; it only
measures and reports.
"""

from __future__ import annotations

from typing import List, Optional

from .candle_store import IngestReport
from .models import Candle, DataQualityReport


def assess(
    instrument: str,
    candles: List[Candle],
    ingest_report: IngestReport,
    now_ts: int,
    freshness_max_seconds: float,
    history_min_candles: int,
) -> DataQualityReport:
    issues: list = []

    if not candles:
        issues.append("No candles available.")
        return DataQualityReport(
            instrument=instrument,
            freshness_seconds=None,
            is_fresh=False,
            history_count=0,
            has_sufficient_history=False,
            timestamps_valid=False,
            is_continuous=False,
            missing_candle_count=0,
            duplicate_count=0,
            invalid_ohlc_count=ingest_report.invalid_candles,
            issues=issues,
        )

    latest_ts = candles[-1].ts
    freshness_seconds = float(now_ts - latest_ts)
    is_fresh = 0 <= freshness_seconds <= freshness_max_seconds
    if freshness_seconds < 0:
        issues.append("Latest candle timestamp is in the future.")
    elif not is_fresh:
        issues.append(f"Latest candle is {freshness_seconds:.0f}s old (max {freshness_max_seconds:.0f}s).")

    has_sufficient_history = len(candles) >= history_min_candles
    if not has_sufficient_history:
        issues.append(f"Only {len(candles)} candles available (need >= {history_min_candles}).")

    if not ingest_report.timestamps_valid:
        issues.append("One or more timestamps failed validation.")

    if not ingest_report.is_continuous:
        issues.append(f"Candle series has gaps ({ingest_report.missing_candle_count} missing minute(s)).")

    if ingest_report.invalid_candles:
        issues.append(f"{ingest_report.invalid_candles} candle(s) had impossible OHLC values and were dropped.")

    return DataQualityReport(
        instrument=instrument,
        freshness_seconds=freshness_seconds,
        is_fresh=is_fresh,
        history_count=len(candles),
        has_sufficient_history=has_sufficient_history,
        timestamps_valid=ingest_report.timestamps_valid,
        is_continuous=ingest_report.is_continuous,
        missing_candle_count=ingest_report.missing_candle_count,
        duplicate_count=ingest_report.duplicate_candles,
        invalid_ohlc_count=ingest_report.invalid_candles,
        issues=issues,
    )
