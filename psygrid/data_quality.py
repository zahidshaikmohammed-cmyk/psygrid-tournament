"""Data-quality assessment: freshness, continuity, history sufficiency.

NO DATA = UNKNOWN. This module never fabricates missing candles; it only
measures and reports. When RealMarketAPI's own per-symbol metadata
(``status``, ``market_state``, ``gap_recoveries``, ``rejected_count`` —
see :class:`psygrid.api_client.SymbolMeta`) is available, it is folded
into the same usability/quality determination as the raw candles
themselves, not merely recorded for decoration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from .candle_store import IngestReport
from .models import Candle, DataQualityReport

if TYPE_CHECKING:
    from .api_client import SymbolMeta


def assess(
    instrument: str,
    candles: List[Candle],
    ingest_report: IngestReport,
    now_ts: int,
    freshness_max_seconds: float,
    history_min_candles: int,
    symbol_meta: Optional[SymbolMeta] = None,
) -> DataQualityReport:
    issues: list = []

    provider_status = symbol_meta.status if symbol_meta else None
    provider_market_state = symbol_meta.market_state if symbol_meta else None
    provider_gap_recoveries = (symbol_meta.gap_recoveries or 0) if symbol_meta else 0
    provider_rejected_count = (symbol_meta.rejected_count or 0) if symbol_meta else 0

    if provider_status is not None and provider_status != "ok":
        issues.append(f"Provider reports symbol status={provider_status!r} (expected 'ok').")
    if provider_gap_recoveries:
        issues.append(f"Provider recovered {provider_gap_recoveries} gap(s) in this symbol's feed.")
    if provider_rejected_count:
        issues.append(f"Provider itself rejected {provider_rejected_count} candle(s) for this symbol.")

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
            provider_status=provider_status,
            provider_market_state=provider_market_state,
            provider_gap_recoveries=provider_gap_recoveries,
            provider_rejected_count=provider_rejected_count,
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
        provider_status=provider_status,
        provider_market_state=provider_market_state,
        provider_gap_recoveries=provider_gap_recoveries,
        provider_rejected_count=provider_rejected_count,
    )
