"""SQLite persistence layer.

Schema is created with ``CREATE TABLE IF NOT EXISTS`` so restarting the
engine against an existing database file is always safe (restart
recovery) — no data is dropped or recreated on a fresh connection.

Full raw JSON payloads are not stored on every fetch (at ~1500 candles x 10
instruments every scan interval that would grow unboundedly); instead every
fetch is logged compactly (coverage, counts, parse errors) in
``fetch_log``, and every genuine M1 candle actually ingested is persisted
in full in ``m1_candles``. That is the "practical" reading of "persist raw
observations" the spec calls for.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from .models import Candle, CandidateOutcome, Setup, TournamentResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    instrument_count INTEGER NOT NULL,
    coverage TEXT NOT NULL,
    parse_errors_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS m1_candles (
    instrument TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    ingested_at INTEGER NOT NULL,
    PRIMARY KEY (instrument, ts)
);

CREATE TABLE IF NOT EXISTS derived_candles (
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    synthetic_from_m1 INTEGER NOT NULL,
    PRIMARY KEY (instrument, timeframe, ts)
);

CREATE TABLE IF NOT EXISTS instrument_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    instrument TEXT NOT NULL,
    state_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS setups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    formation_time INTEGER NOT NULL,
    entry_candidate REAL NOT NULL,
    invalidation REAL NOT NULL,
    target_candidate REAL NOT NULL,
    confidence REAL NOT NULL,
    detected_at INTEGER NOT NULL,
    reasoning_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tournament_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL UNIQUE,
    result_type TEXT NOT NULL,
    winner_instrument TEXT,
    winner_score REAL,
    second_instrument TEXT,
    second_score REAL,
    lead REAL,
    candidates_evaluated INTEGER NOT NULL,
    coverage TEXT NOT NULL,
    min_quality_threshold REAL NOT NULL,
    table_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    tournament_ts INTEGER NOT NULL,
    instrument TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    regime TEXT NOT NULL,
    session TEXT NOT NULL,
    volatility REAL NOT NULL,
    structure_state TEXT NOT NULL,
    mfe REAL,
    mae REAL,
    outcome_1m REAL,
    outcome_5m REAL,
    outcome_10m REAL,
    outcome_15m REAL,
    outcome_30m REAL,
    time_to_mfe INTEGER,
    time_to_mae INTEGER,
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    instrument TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL
);
"""


class Persistence:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        with closing(self._conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- fetch log -----------------------------------------------------

    def log_fetch(self, fetched_at: int, ok: bool, instrument_count: int, coverage: str, parse_errors: list) -> None:
        self._conn.execute(
            "INSERT INTO fetch_log (fetched_at, ok, instrument_count, coverage, parse_errors_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (fetched_at, int(ok), instrument_count, coverage, json.dumps(parse_errors)),
        )
        self._conn.commit()

    # -- candles ---------------------------------------------------------

    def save_m1_candles(self, candles: List[Candle]) -> None:
        if not candles:
            return
        now = int(time.time())
        self._conn.executemany(
            "INSERT OR REPLACE INTO m1_candles "
            "(instrument, ts, open, high, low, close, volume, ingested_at) VALUES (?,?,?,?,?,?,?,?)",
            [(c.instrument, c.ts, c.open, c.high, c.low, c.close, c.volume, now) for c in candles],
        )
        self._conn.commit()

    def save_derived_candles(self, candles: List[Candle]) -> None:
        if not candles:
            return
        self._conn.executemany(
            "INSERT OR REPLACE INTO derived_candles "
            "(instrument, timeframe, ts, open, high, low, close, volume, synthetic_from_m1) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (c.instrument, c.timeframe, c.ts, c.open, c.high, c.low, c.close, c.volume, int(c.synthetic_from_m1))
                for c in candles
            ],
        )
        self._conn.commit()

    # -- states / setups --------------------------------------------------

    def save_instrument_state(self, ts: int, instrument: str, state: dict) -> None:
        self._conn.execute(
            "INSERT INTO instrument_states (ts, instrument, state_json) VALUES (?, ?, ?)",
            (ts, instrument, json.dumps(state, default=str)),
        )
        self._conn.commit()

    def save_setup(self, setup: Setup, detected_at: int) -> int:
        cur = self._conn.execute(
            "INSERT INTO setups (instrument, setup_type, direction, formation_time, entry_candidate, "
            "invalidation, target_candidate, confidence, detected_at, reasoning_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                setup.instrument,
                setup.setup_type,
                setup.direction.value,
                setup.formation_time,
                setup.entry_candidate,
                setup.invalidation,
                setup.target_candidate,
                setup.confidence,
                detected_at,
                json.dumps(setup.reasoning),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    # -- tournaments -------------------------------------------------------

    def save_tournament_result(self, result: TournamentResult, table_payload: list) -> int:
        winner = result.winner
        second = result.second
        cur = self._conn.execute(
            "INSERT OR REPLACE INTO tournament_results (ts, result_type, winner_instrument, winner_score, "
            "second_instrument, second_score, lead, candidates_evaluated, coverage, min_quality_threshold, "
            "table_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                result.ts,
                "WINNER" if winner else "NO_TRADE",
                winner.instrument if winner else None,
                winner.score if winner else None,
                second.instrument if second else None,
                second.score if second else None,
                result.lead,
                result.candidates_evaluated,
                result.data_coverage,
                result.min_quality_threshold,
                json.dumps(table_payload, default=str),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def last_tournament_ts(self) -> Optional[int]:
        row = self._conn.execute("SELECT MAX(ts) AS max_ts FROM tournament_results").fetchone()
        return row["max_ts"] if row and row["max_ts"] is not None else None

    # -- candidate outcomes -------------------------------------------------

    def save_candidate_outcome(self, tournament_id: int, outcome: CandidateOutcome) -> int:
        cur = self._conn.execute(
            "INSERT INTO candidate_outcomes (tournament_id, tournament_ts, instrument, setup_type, direction, "
            "entry, stop, target, regime, session, volatility, structure_state, mfe, mae, outcome_1m, outcome_5m, "
            "outcome_10m, outcome_15m, outcome_30m, time_to_mfe, time_to_mae, resolved) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tournament_id,
                outcome.tournament_ts,
                outcome.instrument,
                outcome.setup_type,
                outcome.direction,
                outcome.entry,
                outcome.stop,
                outcome.target,
                outcome.regime,
                outcome.session,
                outcome.volatility,
                outcome.structure_state,
                outcome.mfe,
                outcome.mae,
                outcome.outcome_1m,
                outcome.outcome_5m,
                outcome.outcome_10m,
                outcome.outcome_15m,
                outcome.outcome_30m,
                outcome.time_to_mfe,
                outcome.time_to_mae,
                int(outcome.resolved),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def open_candidate_outcomes(self) -> List[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM candidate_outcomes WHERE resolved = 0"
        ).fetchall()

    def update_candidate_outcome(self, outcome_id: int, **fields) -> None:
        if not fields:
            return
        columns = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [outcome_id]
        self._conn.execute(f"UPDATE candidate_outcomes SET {columns} WHERE id = ?", values)
        self._conn.commit()

    def historical_conditional_stats(
        self, instrument: str, setup_type: str, regime: str, min_sample: int
    ) -> Tuple[float, int, bool]:
        """Return (score 0-100, sample_size, calibrated).

        Score is derived from the mean 30-minute forward outcome (in R
        multiples of the original risk, already normalized by the caller
        when writing outcome_30m) of resolved candidates matching
        instrument+setup_type+regime. Falls back to setup_type-only if the
        stricter match has too few samples. If neither has enough samples,
        returns a neutral 50 and ``calibrated=False`` — this is explicitly
        NOT a probability until validated with real history.
        """
        strict = self._conn.execute(
            "SELECT outcome_30m FROM candidate_outcomes WHERE resolved = 1 AND outcome_30m IS NOT NULL "
            "AND instrument = ? AND setup_type = ? AND regime = ?",
            (instrument, setup_type, regime),
        ).fetchall()
        rows = strict
        if len(rows) < min_sample:
            rows = self._conn.execute(
                "SELECT outcome_30m FROM candidate_outcomes WHERE resolved = 1 AND outcome_30m IS NOT NULL "
                "AND setup_type = ?",
                (setup_type,),
            ).fetchall()
        if len(rows) < min_sample:
            return 50.0, len(rows), False

        values = [r["outcome_30m"] for r in rows]
        mean_r = sum(values) / len(values)
        # Map mean R-multiple outcome to a bounded 0-100 score. This mapping
        # is a declared heuristic (tanh-style squashing), not a calibrated
        # probability.
        score = 50.0 + max(-50.0, min(50.0, mean_r * 25.0))
        return score, len(rows), True

    # -- diagnostics ---------------------------------------------------------

    def log_error(self, component: str, message: str, ts: Optional[int] = None) -> None:
        self._conn.execute(
            "INSERT INTO errors (ts, component, message) VALUES (?, ?, ?)",
            (ts if ts is not None else int(time.time()), component, message),
        )
        self._conn.commit()

    def log_data_quality_event(self, instrument: str, event_type: str, details: str, ts: Optional[int] = None) -> None:
        self._conn.execute(
            "INSERT INTO data_quality_events (ts, instrument, event_type, details) VALUES (?, ?, ?, ?)",
            (ts if ts is not None else int(time.time()), instrument, event_type, details),
        )
        self._conn.commit()
