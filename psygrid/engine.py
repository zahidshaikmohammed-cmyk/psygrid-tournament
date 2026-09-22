"""The engine orchestrator: continuous scanning + 30-minute tournaments."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from . import outcomes
from .api_client import ApiError, RealMarketApiClient
from .candle_store import MultiInstrumentCandleStore
from .config import Config
from .instrument_analysis import analyze_instrument
from .models import CandidateStatus, InstrumentAnalysis, TournamentResult, TournamentRow
from .persistence import Persistence
from .scheduler import TournamentClock
from .telegram_client import TelegramClient, format_data_unavailable_message, format_tournament_message
from .tournament import run_tournament


def _row_payload(row: TournamentRow) -> dict:
    return {
        "instrument": row.instrument,
        "status": row.status.value,
        "score": row.score,
        "reasons": row.reasons,
        "setup_type": row.setup.setup_type if row.setup else None,
        "direction": row.setup.direction.value if row.setup else None,
    }


class Engine:
    def __init__(
        self,
        config: Config,
        api_client: RealMarketApiClient,
        persistence: Persistence,
        telegram_client: TelegramClient,
        now_fn: Callable[[], float] = time.time,
    ):
        self.config = config
        self.api_client = api_client
        self.persistence = persistence
        self.telegram_client = telegram_client
        self.now_fn = now_fn

        self.store = MultiInstrumentCandleStore(config.max_rolling_candles)
        self.analyses: Dict[str, InstrumentAnalysis] = {}
        self.last_fetch_ok: Optional[bool] = None
        self.last_fetch_ts: Optional[int] = None
        self.last_error: Optional[str] = None
        self.scan_count = 0
        self.tournament_count = 0
        self.last_tournament_result: Optional[TournamentResult] = None
        self.clock = TournamentClock.restore(
            config.tournament_interval_minutes, persistence.last_tournament_ts()
        )

    # -- scanning ------------------------------------------------------------

    def scan_once(self) -> None:
        now_ts = int(self.now_fn())
        try:
            result = self.api_client.fetch()
        except ApiError as exc:
            self.last_fetch_ok = False
            self.last_error = str(exc)
            self.persistence.log_error("api_client", str(exc), now_ts)
            self.persistence.log_fetch(
                now_ts, False, 0, f"0/{self.config.expected_instrument_count}", [str(exc)]
            )
            return

        self.last_fetch_ok = result.ok
        self.last_fetch_ts = now_ts
        if not result.ok:
            self.last_error = "; ".join(result.parse_errors) or "Unknown parse failure."

        self.persistence.log_fetch(
            result.fetched_at,
            result.ok,
            len(result.instruments),
            f"{len(result.instruments)}/{self.config.expected_instrument_count}",
            result.parse_errors,
        )

        for instrument, candles in result.instruments.items():
            ingest_report = self.store.ingest(instrument, candles)
            self.persistence.save_m1_candles(candles)
            if not ingest_report.is_continuous:
                self.persistence.log_data_quality_event(
                    instrument,
                    "gap",
                    f"{ingest_report.missing_candle_count} missing minute(s) detected",
                    now_ts,
                )
            analysis = analyze_instrument(
                instrument, self.store.candles(instrument), ingest_report, now_ts, self.config
            )
            self.analyses[instrument] = analysis

        self.scan_count += 1

    # -- tournaments -----------------------------------------------------------

    def maybe_run_tournament(self) -> Optional[TournamentResult]:
        now_ts = int(self.now_fn())
        fire, boundary = self.clock.should_fire(now_ts)
        if not fire:
            return None
        self.clock.mark_fired(boundary)
        return self.run_tournament_cycle(boundary)

    def run_tournament_cycle(self, ts: int) -> Optional[TournamentResult]:
        usable = {k: v for k, v in self.analyses.items() if v.data_quality.history_count > 0}

        if not usable:
            reason = self.last_error or "RealMarketAPI returned no usable data for any instrument."
            message = format_data_unavailable_message(ts, reason)
            send = self.telegram_client.send_message(message)
            if not send.ok:
                self.persistence.log_error("telegram", send.error or "unknown send failure", ts)
            empty_result = TournamentResult(
                ts=ts,
                rows=[],
                winner=None,
                second=None,
                lead=None,
                candidates_evaluated=0,
                data_coverage=f"0/{self.config.expected_instrument_count}",
                min_quality_threshold=self.config.min_quality_score,
            )
            self.persistence.save_tournament_result(empty_result, [])
            self.tournament_count += 1
            self.last_tournament_result = empty_result
            return empty_result

        def historical_fn(instrument: str, setup_type: str, regime: str):
            return self.persistence.historical_conditional_stats(
                instrument, setup_type, regime, self.config.historical_min_sample
            )

        result = run_tournament(self.analyses, self.config, ts, historical_fn)
        table_payload = [_row_payload(r) for r in result.rows]
        tournament_id = self.persistence.save_tournament_result(result, table_payload)

        for row in result.rows:
            if row.setup is not None:
                self.persistence.save_setup(row.setup, ts)

        if result.winner is not None:
            analysis = self.analyses.get(result.winner.instrument)
            if analysis is not None:
                structure_m1 = analysis.structure.get("M1")
                outcomes.record_candidate(
                    self.persistence,
                    tournament_id,
                    ts,
                    result.winner,
                    analysis.regime,
                    analysis.time_behavior.session if analysis.time_behavior else "UNKNOWN",
                    analysis.volatility.atr if analysis.volatility else 0.0,
                    structure_m1.trend.value if structure_m1 else "UNKNOWN",
                )

        message = format_tournament_message(ts, result)
        send = self.telegram_client.send_message(message)
        if not send.ok:
            self.persistence.log_error("telegram", send.error or "unknown send failure", ts)

        self.tournament_count += 1
        self.last_tournament_result = result
        return result

    # -- outcome tracking -------------------------------------------------------

    def update_outcomes(self) -> int:
        now_ts = int(self.now_fn())
        return outcomes.update_open_outcomes(self.persistence, self.store, now_ts)

    # -- one full tick --------------------------------------------------------

    def tick(self) -> Optional[TournamentResult]:
        self.scan_once()
        result = self.maybe_run_tournament()
        self.update_outcomes()
        return result

    # -- terminal status --------------------------------------------------------

    def render_status(self) -> str:
        now_ts = int(self.now_fn())
        last_update = (
            datetime.fromtimestamp(self.last_fetch_ts, tz=timezone.utc).strftime("%H:%M:%S")
            if self.last_fetch_ts
            else "N/A"
        )
        next_boundary = self.clock.next_boundary_after(now_ts)
        next_tournament = datetime.fromtimestamp(next_boundary, tz=timezone.utc).strftime("%H:%M:%S")
        data_status = "LIVE" if self.last_fetch_ok else "DEGRADED" if self.last_fetch_ok is not None else "STARTING"

        lines = [
            "PSYGRID TOURNAMENT ENGINE",
            "Status: RUNNING",
            f"Instruments: {len(self.analyses)}",
            f"Data: {data_status}",
            f"Last update (UTC): {last_update}",
            f"Next tournament (UTC): {next_tournament}",
            "",
        ]
        for instrument in sorted(self.analyses.keys()):
            analysis = self.analyses[instrument]
            if not analysis.data_quality.is_usable:
                state = "data issue"
            elif analysis.setup is not None:
                state = f"setup: {analysis.setup.setup_type}"
            else:
                state = "scanning"
            lines.append(f"{instrument:<10} {state}")
        return "\n".join(lines)
