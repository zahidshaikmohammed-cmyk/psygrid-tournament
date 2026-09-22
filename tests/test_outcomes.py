from psygrid.candle_store import MultiInstrumentCandleStore
from psygrid.models import CandidateOutcome, TournamentResult
from psygrid.outcomes import update_open_outcomes
from psygrid.persistence import Persistence
from tests.fixtures import BASE_TS, make_m1_series


def test_outcome_tracker_fills_horizons_and_mfe_mae(tmp_path):
    persistence = Persistence(str(tmp_path / "outcomes.sqlite3"))
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=1, data_coverage="1/1", min_quality_threshold=78.0,
    )
    tid = persistence.save_tournament_result(result, [])

    outcome = CandidateOutcome(
        tournament_ts=BASE_TS, instrument="XAUUSD", setup_type="trend_continuation", direction="LONG",
        entry=100.0, stop=99.0, target=103.0, regime="UP_NORMAL_VOL", session="LONDON", volatility=0.5,
        structure_state="UP",
    )
    persistence.save_candidate_outcome(tid, outcome)

    # Build 35 minutes of forward M1 candles: price rises steadily by 0.05/min.
    closes = [100.0 + i * 0.05 for i in range(36)]
    candles = make_m1_series("XAUUSD", closes, start_ts=BASE_TS)
    store = MultiInstrumentCandleStore()
    store.ingest("XAUUSD", candles)

    now_ts = BASE_TS + 36 * 60
    updated = update_open_outcomes(persistence, store, now_ts)
    assert updated == 1

    row = persistence._conn.execute("SELECT * FROM candidate_outcomes").fetchone()
    assert row["resolved"] == 1
    assert row["outcome_1m"] is not None
    assert row["outcome_30m"] is not None
    # Price rose monotonically in favor of a LONG: MFE should be positive
    # and larger than any adverse excursion.
    assert row["mfe"] > 0
    assert row["mae"] >= 0
    assert row["outcome_30m"] > 0
    persistence.close()


def test_outcome_tracker_leaves_unresolved_when_horizon_not_yet_reached(tmp_path):
    persistence = Persistence(str(tmp_path / "outcomes2.sqlite3"))
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=1, data_coverage="1/1", min_quality_threshold=78.0,
    )
    tid = persistence.save_tournament_result(result, [])
    outcome = CandidateOutcome(
        tournament_ts=BASE_TS, instrument="XAUUSD", setup_type="trend_continuation", direction="LONG",
        entry=100.0, stop=99.0, target=103.0, regime="UP_NORMAL_VOL", session="LONDON", volatility=0.5,
        structure_state="UP",
    )
    persistence.save_candidate_outcome(tid, outcome)

    # Only 3 minutes of forward data exist so far.
    closes = [100.0, 100.05, 100.1, 100.15]
    candles = make_m1_series("XAUUSD", closes, start_ts=BASE_TS)
    store = MultiInstrumentCandleStore()
    store.ingest("XAUUSD", candles)

    now_ts = BASE_TS + 3 * 60
    update_open_outcomes(persistence, store, now_ts)

    row = persistence._conn.execute("SELECT * FROM candidate_outcomes").fetchone()
    assert row["resolved"] == 0
    assert row["outcome_1m"] is not None
    assert row["outcome_30m"] is None
    persistence.close()


def test_outcome_tracker_never_fabricates_missing_horizon_data(tmp_path):
    persistence = Persistence(str(tmp_path / "outcomes3.sqlite3"))
    result = TournamentResult(
        ts=BASE_TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=1, data_coverage="1/1", min_quality_threshold=78.0,
    )
    tid = persistence.save_tournament_result(result, [])
    outcome = CandidateOutcome(
        tournament_ts=BASE_TS, instrument="XAUUSD", setup_type="trend_continuation", direction="LONG",
        entry=100.0, stop=99.0, target=103.0, regime="UP_NORMAL_VOL", session="LONDON", volatility=0.5,
        structure_state="UP",
    )
    persistence.save_candidate_outcome(tid, outcome)

    store = MultiInstrumentCandleStore()  # no candle data ingested at all

    now_ts = BASE_TS + 10 * 60
    update_open_outcomes(persistence, store, now_ts)
    row = persistence._conn.execute("SELECT * FROM candidate_outcomes").fetchone()
    assert row["outcome_1m"] is None
    assert row["mfe"] is None
    assert row["resolved"] == 0  # not yet at the resolution grace period
    persistence.close()
