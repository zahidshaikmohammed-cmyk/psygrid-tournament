import os

from psygrid.models import (
    CandidateOutcome,
    CandidateStatus,
    Direction,
    ExecutionState,
    ScoreBreakdown,
    Setup,
    TournamentResult,
    TournamentRow,
)
from psygrid.persistence import Persistence
from tests.fixtures import BASE_TS, make_m1_series


def _tmp_db_path(tmp_path):
    return str(tmp_path / "psygrid_test.sqlite3")


def test_schema_created_on_first_connect(tmp_path):
    db_path = _tmp_db_path(tmp_path)
    p = Persistence(db_path)
    p.close()
    assert os.path.exists(db_path)


def test_save_and_read_m1_candles_round_trip(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    candles = make_m1_series("XAUUSD", [100.0, 100.1, 100.2])
    p.save_m1_candles(candles)
    rows = p._conn.execute("SELECT * FROM m1_candles ORDER BY ts").fetchall()
    assert len(rows) == 3
    assert rows[0]["instrument"] == "XAUUSD"
    p.close()


def test_save_m1_candles_upserts_on_conflict(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    candles = make_m1_series("XAUUSD", [100.0, 100.1])
    p.save_m1_candles(candles)
    updated = make_m1_series("XAUUSD", [100.0, 100.15])  # revised forming candle
    p.save_m1_candles([updated[-1]])
    rows = p._conn.execute("SELECT close FROM m1_candles WHERE ts = ?", (updated[-1].ts,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["close"] == updated[-1].close
    p.close()


def test_setup_persistence(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    setup = Setup(
        instrument="XAUUSD", setup_type="liquidity_sweep_reversal", direction=Direction.LONG,
        formation_time=BASE_TS, entry_candidate=100.0, invalidation=99.0, target_candidate=103.0,
        confidence=80.0, reasoning=["a", "b"],
    )
    setup_id = p.save_setup(setup, BASE_TS + 60)
    row = p._conn.execute("SELECT * FROM setups WHERE id = ?", (setup_id,)).fetchone()
    assert row["instrument"] == "XAUUSD"
    assert row["direction"] == "LONG"
    p.close()


def test_tournament_result_persistence_and_last_ts(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    p.save_tournament_result(result, [])
    assert p.last_tournament_ts() == BASE_TS

    result2 = TournamentResult(
        ts=BASE_TS + 1800, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    p.save_tournament_result(result2, [])
    assert p.last_tournament_ts() == BASE_TS + 1800
    p.close()


def test_candidate_outcome_lifecycle(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    tid = p.save_tournament_result(result, [])

    outcome = CandidateOutcome(
        tournament_ts=BASE_TS, instrument="XAUUSD", setup_type="trend_continuation", direction="LONG",
        entry=100.0, stop=99.0, target=103.0, regime="UP_NORMAL_VOL", session="LONDON", volatility=0.5,
        structure_state="UP",
    )
    outcome_id = p.save_candidate_outcome(tid, outcome)
    open_rows = p.open_candidate_outcomes()
    assert len(open_rows) == 1
    assert open_rows[0]["id"] == outcome_id

    p.update_candidate_outcome(outcome_id, mfe=1.2, mae=0.3, outcome_30m=0.9, resolved=1)
    open_rows_after = p.open_candidate_outcomes()
    assert len(open_rows_after) == 0
    p.close()


def test_historical_conditional_stats_neutral_when_insufficient_sample(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    score, n, calibrated = p.historical_conditional_stats("XAUUSD", "trend_continuation", "UP_NORMAL_VOL", min_sample=20)
    assert score == 50.0
    assert n == 0
    assert calibrated is False
    p.close()


def test_historical_conditional_stats_calibrated_once_enough_resolved_samples(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    tid = p.save_tournament_result(result, [])
    for i in range(25):
        outcome = CandidateOutcome(
            tournament_ts=BASE_TS + i, instrument="XAUUSD", setup_type="trend_continuation", direction="LONG",
            entry=100.0, stop=99.0, target=103.0, regime="UP_NORMAL_VOL", session="LONDON", volatility=0.5,
            structure_state="UP",
        )
        oid = p.save_candidate_outcome(tid, outcome)
        p.update_candidate_outcome(oid, outcome_30m=1.0, resolved=1)

    score, n, calibrated = p.historical_conditional_stats("XAUUSD", "trend_continuation", "UP_NORMAL_VOL", min_sample=20)
    assert n == 25
    assert calibrated is True
    assert score > 50.0  # positive mean outcome should push score above neutral
    p.close()


def test_errors_and_data_quality_events_logged(tmp_path):
    p = Persistence(_tmp_db_path(tmp_path))
    p.log_error("api_client", "boom", BASE_TS)
    p.log_data_quality_event("XAUUSD", "gap", "3 missing minutes", BASE_TS)
    errors = p._conn.execute("SELECT * FROM errors").fetchall()
    events = p._conn.execute("SELECT * FROM data_quality_events").fetchall()
    assert len(errors) == 1
    assert len(events) == 1
    p.close()


# -- restart recovery ----------------------------------------------------------


def test_restart_recovery_preserves_data_and_schema(tmp_path):
    db_path = _tmp_db_path(tmp_path)
    p1 = Persistence(db_path)
    candles = make_m1_series("XAUUSD", [100.0, 100.1, 100.2])
    p1.save_m1_candles(candles)
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    p1.save_tournament_result(result, [])
    p1.close()

    # Simulate a full process restart: reconnect to the same file.
    p2 = Persistence(db_path)
    rows = p2._conn.execute("SELECT * FROM m1_candles").fetchall()
    assert len(rows) == 3
    assert p2.last_tournament_ts() == BASE_TS

    # Schema re-creation on reconnect must be a no-op, not destructive.
    more_candles = make_m1_series("XAUUSD", [100.3], start_ts=BASE_TS + 3 * 60)
    p2.save_m1_candles(more_candles)
    rows_after = p2._conn.execute("SELECT * FROM m1_candles").fetchall()
    assert len(rows_after) == 4
    p2.close()


def test_scheduler_restart_recovery_uses_persisted_last_tournament(tmp_path):
    from psygrid.scheduler import TournamentClock

    db_path = _tmp_db_path(tmp_path)
    p1 = Persistence(db_path)
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    p1.save_tournament_result(result, [])
    p1.close()

    p2 = Persistence(db_path)
    clock = TournamentClock.restore(30, p2.last_tournament_ts())
    fire, _ = clock.should_fire(BASE_TS + 10)  # still within the same 30-min window
    assert not fire
    p2.close()
