import json

from psygrid.api_client import ApiError, RealMarketApiClient
from psygrid.config import Config
from psygrid.engine import Engine
from psygrid.persistence import Persistence
from psygrid.telegram_client import TelegramClient
from tests.fixtures import BASE_TS, api_payload_json, make_m1_series, uptrend_closes


def _make_engine(tmp_path, fetch_fn, now_holder, telegram_sent=None, config=None):
    config = config or Config(min_quality_score=1.0, history_min_candles=200, expected_instrument_count=2)
    api_client = RealMarketApiClient("http://example.invalid", fetch_fn=fetch_fn)
    persistence = Persistence(str(tmp_path / "engine_test.sqlite3"))

    telegram_sent = telegram_sent if telegram_sent is not None else []

    def post_fn(url, payload):
        telegram_sent.append(payload["text"])
        return {"ok": True}

    telegram_client = TelegramClient("token", "chat", post_fn=post_fn)
    engine = Engine(config, api_client, persistence, telegram_client, now_fn=lambda: now_holder[0])
    return engine, persistence, telegram_sent


def _two_instrument_fixture():
    good = make_m1_series("XAUUSD", uptrend_closes(220, start=100.0, step=0.1, pullback_every=18), start_ts=BASE_TS)
    weak = make_m1_series("EURUSD", [1.1000] * 220, start_ts=BASE_TS)
    return good, weak


def test_full_tick_analyzes_all_instruments(tmp_path):
    good, weak = _two_instrument_fixture()
    now_holder = [good[-1].ts + 5]
    payload = api_payload_json({"XAUUSD": good, "EURUSD": weak}, server_time=good[-1].ts)
    engine, persistence, sent = _make_engine(tmp_path, lambda: payload, now_holder)

    engine.scan_once()
    assert set(engine.analyses.keys()) == {"XAUUSD", "EURUSD"}
    assert engine.analyses["XAUUSD"].data_quality.is_usable
    persistence.close()


def test_tournament_fires_only_on_boundary_crossing(tmp_path):
    good, weak = _two_instrument_fixture()
    boundary = (good[-1].ts // 1800) * 1800 + 1800  # next 30-min boundary
    now_holder = [boundary - 100]  # not yet at boundary
    payload = api_payload_json({"XAUUSD": good, "EURUSD": weak}, server_time=good[-1].ts)
    engine, persistence, sent = _make_engine(tmp_path, lambda: payload, now_holder)

    # A brand-new engine with no persisted history "catches up" to the
    # current window on its very first tick (there is nothing to be
    # idempotent against yet) — this is the one legitimate immediate fire.
    engine.scan_once()
    first = engine.maybe_run_tournament()
    assert first is not None
    assert engine.tournament_count == 1
    assert len(sent) == 1

    # Ticking again inside the SAME window must not fire a second time.
    now_holder[0] = boundary - 20
    engine.scan_once()
    second = engine.maybe_run_tournament()
    assert second is None
    assert engine.tournament_count == 1
    assert len(sent) == 1

    # Only once the real next boundary is crossed does it fire again.
    now_holder[0] = boundary + 5
    engine.scan_once()
    third = engine.maybe_run_tournament()
    assert third is not None
    assert engine.tournament_count == 2
    assert len(sent) == 2
    persistence.close()


def test_exactly_one_tournament_across_many_scan_ticks_in_one_window(tmp_path):
    good, weak = _two_instrument_fixture()
    boundary = (good[-1].ts // 1800) * 1800 + 1800
    now_holder = [boundary]
    payload = api_payload_json({"XAUUSD": good, "EURUSD": weak}, server_time=good[-1].ts)
    engine, persistence, sent = _make_engine(tmp_path, lambda: payload, now_holder)

    fired = 0
    for offset in range(0, 1800, 100):
        now_holder[0] = boundary + offset
        engine.scan_once()
        result = engine.maybe_run_tournament()
        if result is not None:
            fired += 1
    assert fired == 1
    assert engine.tournament_count == 1
    assert len(sent) == 1
    persistence.close()


def test_winner_produces_exactly_one_telegram_message(tmp_path):
    good, weak = _two_instrument_fixture()
    now_holder = [good[-1].ts + 5]
    payload = api_payload_json({"XAUUSD": good, "EURUSD": weak}, server_time=good[-1].ts)
    config = Config(min_quality_score=1.0, history_min_candles=200, expected_instrument_count=2)
    engine, persistence, sent = _make_engine(tmp_path, lambda: payload, now_holder, config=config)

    engine.scan_once()
    result = engine.run_tournament_cycle(now_holder[0])
    assert result.winner is not None
    assert len(sent) == 1
    assert "WINNER" in sent[0]
    persistence.close()


def test_no_trade_when_all_instruments_reject(tmp_path):
    flat_a = make_m1_series("XAUUSD", [100.0] * 220, start_ts=BASE_TS)
    flat_b = make_m1_series("EURUSD", [1.1] * 220, start_ts=BASE_TS)
    now_holder = [flat_a[-1].ts + 5]
    payload = api_payload_json({"XAUUSD": flat_a, "EURUSD": flat_b}, server_time=flat_a[-1].ts)
    config = Config(history_min_candles=200, expected_instrument_count=2)
    engine, persistence, sent = _make_engine(tmp_path, lambda: payload, now_holder, config=config)

    engine.scan_once()
    result = engine.run_tournament_cycle(now_holder[0])
    assert result.winner is None
    assert len(sent) == 1
    assert "NO TRADE" in sent[0]
    persistence.close()


def test_data_unavailable_sent_when_api_totally_fails(tmp_path):
    now_holder = [BASE_TS + 100000]

    def failing_fetch():
        raise ConnectionError("simulated total outage")

    config = Config(expected_instrument_count=2)
    engine, persistence, sent = _make_engine(tmp_path, failing_fetch, now_holder, config=config)

    engine.scan_once()  # logs the failure, analyses stays empty
    assert engine.analyses == {}
    result = engine.run_tournament_cycle(now_holder[0])
    assert result.winner is None
    assert result.candidates_evaluated == 0
    assert len(sent) == 1
    assert "DATA UNAVAILABLE" in sent[0]
    persistence.close()


def test_partial_coverage_reported_without_treating_missing_as_losers(tmp_path):
    good, _ = _two_instrument_fixture()
    now_holder = [good[-1].ts + 5]
    # Only XAUUSD present this cycle — EURUSD is simply absent, not scored.
    payload = api_payload_json({"XAUUSD": good}, server_time=good[-1].ts)
    config = Config(min_quality_score=1.0, history_min_candles=200, expected_instrument_count=2)
    engine, persistence, sent = _make_engine(tmp_path, lambda: payload, now_holder, config=config)

    engine.scan_once()
    result = engine.run_tournament_cycle(now_holder[0])
    assert result.data_coverage == "1/2"
    assert all(row.instrument != "EURUSD" for row in result.rows)
    persistence.close()


def test_restart_recovery_seeds_clock_from_persisted_tournament(tmp_path):
    good, weak = _two_instrument_fixture()
    now_holder = [good[-1].ts + 5]
    payload = api_payload_json({"XAUUSD": good, "EURUSD": weak}, server_time=good[-1].ts)
    db_path = str(tmp_path / "restart_test.sqlite3")
    config = Config(min_quality_score=1.0, history_min_candles=200, expected_instrument_count=2)

    api_client = RealMarketApiClient("http://example.invalid", fetch_fn=lambda: payload)
    persistence = Persistence(db_path)
    telegram_client = TelegramClient("t", "c", post_fn=lambda url, payload: {"ok": True})
    engine1 = Engine(config, api_client, persistence, telegram_client, now_fn=lambda: now_holder[0])
    engine1.scan_once()
    result = engine1.run_tournament_cycle(now_holder[0])
    persistence.close()

    # Full restart: new Engine instance, same DB file.
    persistence2 = Persistence(db_path)
    engine2 = Engine(config, api_client, persistence2, telegram_client, now_fn=lambda: now_holder[0] + 60)
    fire, _ = engine2.clock.should_fire(now_holder[0] + 60)
    assert not fire  # same 30-min window as the tournament already recorded
    persistence2.close()
