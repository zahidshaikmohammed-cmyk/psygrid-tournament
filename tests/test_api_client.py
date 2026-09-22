"""Tests for the RealMarketAPI adapter against the CONFIRMED live schema.

The old tests in this file targeted several *guessed* payload shapes,
written before a real response was available. A real response has since
been captured and confirms the actual schema: a top-level `symbols` object
(not `instruments`), with provider-contract metadata
(schema_version/service/provider/timeframe/candle_source/
synthetic_candles/generated_at/status/universe_size) and per-symbol
metadata (symbol/market_state/status/last_candle_timestamp/candle_count/
gap_recoveries/rejected_count) alongside each symbol's `candles_1m` array
— the authoritative M1 candle array key, confirmed directly from the live
endpoint (two earlier guesses, `candles` and then `candles_l1`, were both
wrong). Every test below targets that confirmed shape; the disproven
guessed shapes have been removed rather than kept "for compatibility"
with an API that was never real.
"""

import json

import pytest

from psygrid.api_client import ApiError, RealMarketApiClient, parse_payload
from tests.fixtures import BASE_TS, live_payload_json, live_symbol_block, make_m1_series


def _ten_symbol_candles(n: int = 210):
    names = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
        "USDCHF", "NZDUSD", "XAUUSD", "XAGUSD", "USOIL",
    ]
    return {sym: make_m1_series(sym, [100.0 + i * 0.01 for i in range(n)]) for sym in names}


# ---------------------------------------------------------------------------
# Core parsing against the confirmed schema
# ---------------------------------------------------------------------------


def test_parses_well_formed_live_payload():
    candles = make_m1_series("XAUUSD", [100.0, 100.5, 101.0])
    raw = live_payload_json({"XAUUSD": candles})
    result = parse_payload(raw)
    assert result.ok
    assert "XAUUSD" in result.instruments
    assert len(result.instruments["XAUUSD"]) == 3
    assert result.instruments["XAUUSD"][0].close == 100.0
    assert result.skipped_candles["XAUUSD"] == 0


def test_all_ten_symbols_parse_correctly():
    instrument_candles = _ten_symbol_candles()
    raw = live_payload_json(instrument_candles)
    result = parse_payload(raw)

    assert result.ok
    assert result.universe_size_actual == 10
    assert set(result.instruments.keys()) == set(instrument_candles.keys())
    assert result.rejected_symbols == {}
    assert result.coverage_issues == []
    for symbol, candles in instrument_candles.items():
        parsed = result.instruments[symbol]
        assert len(parsed) == len(candles)
        assert parsed[0].open == candles[0].open
        assert parsed[-1].close == candles[-1].close
        assert result.symbol_meta[symbol].status == "ok"
        assert result.symbol_meta[symbol].market_state == "open"
        assert result.symbol_meta[symbol].candle_count == len(candles)


def test_all_ten_symbols_parse_via_hand_written_candles_1m_payload():
    # Regression test for the exact CONFIRMED live field name: the live
    # provider's per-symbol candle array key is "candles_1m". Two earlier
    # guesses were both wrong ("candles", then "candles_l1") and have both
    # been corrected. This builds the payload by hand (bypassing
    # tests/fixtures.py entirely) so it can never pass merely because the
    # fixture happens to agree with the adapter — it independently proves
    # entry["candles_1m"] is read.
    names = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
        "USDCHF", "NZDUSD", "XAUUSD", "XAGUSD", "USOIL",
    ]
    symbols = {}
    for i, name in enumerate(names):
        base = 100.0 + i
        symbols[name] = {
            "symbol": name,
            "market_state": "open",
            "status": "ok",
            "last_candle_timestamp": BASE_TS + 2 * 60,
            "updated_at": BASE_TS + 2 * 60,
            "websocket_connected": True,
            "reconnect_count": 0,
            "gap_recoveries": 0,
            "rejected_count": 0,
            "candles_1m": [
                {
                    "timestamp": BASE_TS + j * 60,
                    "open": base + j * 0.01,
                    "high": base + j * 0.01 + 0.02,
                    "low": base + j * 0.01 - 0.02,
                    "close": base + j * 0.01 + 0.01,
                    "volume": 10.0,
                    "bid": None,
                    "ask": None,
                }
                for j in range(3)
            ],
            "m1_valid": True,
        }
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "service": "psygrid-forex",
            "provider": "realmarketapi",
            "timeframe": "M1",
            "candle_source": "provider_native",
            "synthetic_candles": False,
            "generated_at": BASE_TS + 2 * 60,
            "status": "ok",
            "universe_size": 10,
            "symbols": symbols,
        }
    )

    result = parse_payload(raw, expected_universe_size=10)

    assert result.ok
    assert result.universe_size_actual == 10
    assert set(result.instruments.keys()) == set(names)
    assert result.rejected_symbols == {}
    assert result.coverage_issues == []
    for name in names:
        assert len(result.instruments[name]) == 3


def _block_with_candle_key(candle_key: str) -> dict:
    return {
        "symbol": "XAUUSD",
        "market_state": "open",
        "status": "ok",
        "last_candle_timestamp": BASE_TS,
        "updated_at": BASE_TS,
        "websocket_connected": True,
        "reconnect_count": 0,
        "gap_recoveries": 0,
        "rejected_count": 0,
        candle_key: [
            {
                "timestamp": BASE_TS,
                "open": 2400.0,
                "high": 2401.0,
                "low": 2399.0,
                "close": 2400.5,
                "volume": 5.0,
                "bid": None,
                "ask": None,
            }
        ],
        "m1_valid": True,
    }


def _payload_with_candle_key(candle_key: str) -> str:
    return json.dumps(
        {
            "schema_version": "1.0", "service": "psygrid-forex", "provider": "realmarketapi",
            "timeframe": "M1", "candle_source": "provider_native", "synthetic_candles": False,
            "generated_at": BASE_TS, "status": "ok", "universe_size": 1,
            "symbols": {"XAUUSD": _block_with_candle_key(candle_key)},
        }
    )


def test_candles_1m_array_is_accepted():
    result = parse_payload(_payload_with_candle_key("candles_1m"))
    assert result.ok
    assert "XAUUSD" in result.instruments
    assert len(result.instruments["XAUUSD"]) == 1
    assert result.rejected_symbols == {}


def test_candles_l1_key_alone_is_not_required_and_is_rejected():
    # "candles_l1" was an earlier, incorrect guess at the field name — a
    # symbol carrying ONLY that key (no "candles_1m" at all) must be
    # rejected by name, not silently accepted as if it were the real key.
    result = parse_payload(_payload_with_candle_key("candles_l1"))
    assert not result.ok
    assert "XAUUSD" not in result.instruments
    assert "XAUUSD" in result.rejected_symbols
    assert "candles_1m" in result.rejected_symbols["XAUUSD"]


def test_candles_key_alone_is_not_required_and_is_rejected():
    # "candles" was the original, also-incorrect guess — same treatment:
    # a symbol carrying ONLY "candles" (no "candles_1m") is rejected by
    # name rather than silently treated as having data.
    result = parse_payload(_payload_with_candle_key("candles"))
    assert not result.ok
    assert "XAUUSD" not in result.instruments
    assert "XAUUSD" in result.rejected_symbols
    assert "candles_1m" in result.rejected_symbols["XAUUSD"]


def test_timestamp_open_high_low_close_volume_mapped_correctly():
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "service": "psygrid-forex",
            "provider": "realmarketapi",
            "timeframe": "M1",
            "candle_source": "provider_native",
            "synthetic_candles": False,
            "generated_at": BASE_TS,
            "status": "ok",
            "universe_size": 1,
            "symbols": {
                "XAUUSD": {
                    "symbol": "XAUUSD",
                    "market_state": "open",
                    "status": "ok",
                    "last_candle_timestamp": BASE_TS,
                    "candle_count": 1,
                    "gap_recoveries": 0,
                    "rejected_count": 0,
                    "candles_1m": [
                        {
                            "timestamp": BASE_TS,
                            "open": 2400.1,
                            "high": 2401.2,
                            "low": 2399.3,
                            "close": 2400.9,
                            "volume": 123.0,
                            "bid": None,
                            "ask": None,
                        }
                    ],
                }
            },
        }
    )
    result = parse_payload(raw)
    candle = result.instruments["XAUUSD"][0]
    assert candle.ts == BASE_TS
    assert candle.open == 2400.1
    assert candle.high == 2401.2
    assert candle.low == 2399.3
    assert candle.close == 2400.9
    assert candle.volume == 123.0


# ---------------------------------------------------------------------------
# provider_native / synthetic_candles=false acceptance
# ---------------------------------------------------------------------------


def test_provider_native_and_non_synthetic_payload_is_accepted():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])},
        candle_source="provider_native",
        synthetic_candles=False,
    )
    result = parse_payload(raw)
    assert result.ok
    assert result.provider_meta.candle_source == "provider_native"
    assert result.provider_meta.synthetic_candles is False


def test_non_provider_native_candle_source_is_rejected():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])},
        candle_source="synthetic_generated",
    )
    with pytest.raises(ApiError, match="candle_source"):
        parse_payload(raw)


def test_synthetic_candles_true_is_rejected():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])},
        synthetic_candles=True,
    )
    with pytest.raises(ApiError, match="synthetic_candles"):
        parse_payload(raw)


def test_wrong_timeframe_is_rejected():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])},
        timeframe="M5",
    )
    with pytest.raises(ApiError, match="timeframe"):
        parse_payload(raw)


def test_top_level_status_not_ok_is_rejected():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])},
        status="degraded",
    )
    with pytest.raises(ApiError, match="status"):
        parse_payload(raw)


def test_provider_meta_is_preserved_verbatim():
    raw = live_payload_json({"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])})
    result = parse_payload(raw)
    meta = result.provider_meta
    assert meta.schema_version == "1.0"
    assert meta.service == "psygrid-forex"
    assert meta.provider == "realmarketapi"
    assert meta.timeframe == "M1"
    assert meta.candle_source == "provider_native"
    assert meta.synthetic_candles is False
    assert meta.status == "ok"
    assert meta.universe_size == 1


# ---------------------------------------------------------------------------
# null bid/ask acceptance
# ---------------------------------------------------------------------------


def test_null_bid_ask_never_reject_an_otherwise_valid_candle():
    candles = make_m1_series("XAUUSD", [100.0, 100.5, 101.0])
    raw = live_payload_json({"XAUUSD": candles})  # live_symbol_block sets bid=ask=None
    result = parse_payload(raw)
    assert result.ok
    assert len(result.instruments["XAUUSD"]) == 3
    assert "XAUUSD" not in result.rejected_symbols


def test_bid_ask_are_never_read_onto_candle():
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "service": "psygrid-forex",
            "provider": "realmarketapi",
            "timeframe": "M1",
            "candle_source": "provider_native",
            "synthetic_candles": False,
            "generated_at": BASE_TS,
            "status": "ok",
            "universe_size": 1,
            "symbols": {
                "XAUUSD": {
                    "symbol": "XAUUSD",
                    "market_state": "open",
                    "status": "ok",
                    "last_candle_timestamp": BASE_TS,
                    "candle_count": 1,
                    "gap_recoveries": 0,
                    "rejected_count": 0,
                    "candles_1m": [
                        {
                            "timestamp": BASE_TS,
                            "open": 100.0,
                            "high": 100.5,
                            "low": 99.5,
                            "close": 100.2,
                            "volume": 10.0,
                            "bid": None,
                            "ask": None,
                        }
                    ],
                }
            },
        }
    )
    result = parse_payload(raw)
    candle = result.instruments["XAUUSD"][0]
    assert not hasattr(candle, "bid")
    assert not hasattr(candle, "ask")
    assert set(vars(candle).keys()) == {
        "instrument", "timeframe", "ts", "open", "high", "low", "close", "volume", "synthetic_from_m1",
    }


# ---------------------------------------------------------------------------
# malformed / missing candles rejected safely (never fabricated)
# ---------------------------------------------------------------------------


def test_missing_ohlc_field_in_one_candle_is_dropped_not_fabricated():
    raw = json.dumps(
        {
            "schema_version": "1.0", "service": "psygrid-forex", "provider": "realmarketapi",
            "timeframe": "M1", "candle_source": "provider_native", "synthetic_candles": False,
            "generated_at": BASE_TS, "status": "ok", "universe_size": 1,
            "symbols": {
                "USDJPY": {
                    "symbol": "USDJPY", "market_state": "open", "status": "ok",
                    "last_candle_timestamp": BASE_TS + 60, "candle_count": 2,
                    "gap_recoveries": 0, "rejected_count": 0,
                    "candles_1m": [
                        {"timestamp": BASE_TS, "open": 150.0, "high": 150.2, "low": 149.8, "volume": 1.0},  # no close
                        {"timestamp": BASE_TS + 60, "open": 150.1, "high": 150.3, "low": 149.9, "close": 150.2, "volume": 1.0},
                    ],
                }
            },
        }
    )
    result = parse_payload(raw)
    assert len(result.instruments["USDJPY"]) == 1
    assert result.skipped_candles["USDJPY"] == 1
    assert "USDJPY" not in result.rejected_symbols  # still usable overall


def test_symbol_with_no_candles_1m_key_is_rejected_by_name_not_silently_dropped():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1]), "EURUSD": make_m1_series("EURUSD", [1.1, 1.11])},
        extra_symbol_overrides={"EURUSD": {"candles_1m": None}},
    )
    result = parse_payload(raw)
    assert result.ok  # XAUUSD alone is enough for ok=True
    assert "XAUUSD" in result.instruments
    assert "EURUSD" not in result.instruments
    assert "EURUSD" in result.rejected_symbols
    assert "candles_1m" in result.rejected_symbols["EURUSD"]


def test_symbol_with_empty_candles_array_is_rejected_by_name():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1]), "EURUSD": make_m1_series("EURUSD", [1.1, 1.11])},
        extra_symbol_overrides={"EURUSD": {"candles_1m": []}},
    )
    result = parse_payload(raw)
    assert "EURUSD" not in result.instruments
    assert "EURUSD" in result.rejected_symbols
    assert "empty" in result.rejected_symbols["EURUSD"]


def test_symbol_whose_candles_are_all_malformed_is_rejected_by_name():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])},
        extra_symbol_overrides={
            "XAUUSD": {"candles_1m": [{"timestamp": BASE_TS, "open": 100.0}]}  # missing high/low/close
        },
    )
    result = parse_payload(raw)
    assert result.ok is False
    assert "XAUUSD" in result.rejected_symbols
    assert result.skipped_candles["XAUUSD"] == 1


def test_symbol_entry_that_is_not_an_object_is_rejected_by_name():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1]), "EURUSD": make_m1_series("EURUSD", [1.1, 1.11])},
    )
    payload = json.loads(raw)
    payload["symbols"]["EURUSD"] = "not-an-object"
    result = parse_payload(json.dumps(payload))
    assert "EURUSD" in result.rejected_symbols
    assert "XAUUSD" in result.instruments


def test_impossible_ohlc_is_rejected_by_candle_validation():
    from psygrid.models import Candle

    candles = make_m1_series("AUDUSD", [1.0, 1.01])
    bad = candles[0]
    corrupted = Candle(
        instrument=bad.instrument, timeframe="M1", ts=bad.ts, open=1.0, high=0.5, low=0.9, close=1.0
    )
    assert not corrupted.is_valid_ohlc()


def test_missing_symbols_key_raises_api_error():
    raw = json.dumps({"schema_version": "1.0", "status": "ok"})  # no 'symbols' at all
    with pytest.raises(ApiError, match="symbols"):
        parse_payload(raw)


def test_symbols_not_a_dict_raises_api_error():
    raw = live_payload_json({"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])})
    payload = json.loads(raw)
    payload["symbols"] = ["XAUUSD"]
    with pytest.raises(ApiError, match="symbols"):
        parse_payload(json.dumps(payload))


def test_empty_symbols_produces_no_instruments_and_error_note():
    raw = live_payload_json({})
    result = parse_payload(raw)
    assert not result.ok
    assert result.parse_errors


def test_malformed_json_raises_api_error():
    with pytest.raises(ApiError):
        parse_payload("{not valid json")


def test_unexpected_top_level_type_raises():
    with pytest.raises(ApiError):
        parse_payload(json.dumps(42))


# ---------------------------------------------------------------------------
# universe_size mismatch
# ---------------------------------------------------------------------------


def test_universe_size_shortfall_is_reported_not_hidden():
    # Provider (correctly) declares universe_size matching what it actually
    # sent (8), but the ENGINE expected 10 — this must be surfaced, and the
    # 8 valid symbols must still be usable (never treated as losers).
    instrument_candles = {k: v for k, v in list(_ten_symbol_candles().items())[:8]}
    raw = live_payload_json(instrument_candles)  # universe_size auto = 8
    result = parse_payload(raw, expected_universe_size=10)

    assert result.ok
    assert len(result.instruments) == 8
    assert result.universe_size_actual == 8
    assert result.universe_size_expected == 10
    assert any("8/10" in issue for issue in result.coverage_issues)


def test_universe_size_field_inconsistent_with_actual_symbol_count_is_reported():
    # Provider's own universe_size claim (10) disagrees with how many
    # symbol entries it actually included (2) — an internal inconsistency
    # worth flagging regardless of what the engine expected.
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1]), "EURUSD": make_m1_series("EURUSD", [1.1, 1.11])},
        universe_size=10,
    )
    result = parse_payload(raw)
    assert result.ok
    assert any("universe_size=10" in issue and "2 symbol entries" in issue for issue in result.coverage_issues)


def test_rejected_symbols_are_named_exactly_in_coverage_issues():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1]), "EURUSD": make_m1_series("EURUSD", [1.1, 1.11])},
        extra_symbol_overrides={"EURUSD": {"candles_1m": []}},
    )
    result = parse_payload(raw)
    assert any("EURUSD" in issue for issue in result.coverage_issues)


def test_full_ten_of_ten_coverage_has_no_coverage_issues():
    raw = live_payload_json(_ten_symbol_candles())
    result = parse_payload(raw, expected_universe_size=10)
    assert result.ok
    assert result.coverage_issues == []
    assert result.rejected_symbols == {}
    assert len(result.instruments) == 10


# ---------------------------------------------------------------------------
# per-symbol status is metadata, not a parse-level rejection reason
# ---------------------------------------------------------------------------


def test_per_symbol_non_ok_status_still_parses_candles_but_carries_the_status():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1, 100.2])},
        extra_symbol_overrides={"XAUUSD": {"status": "degraded"}},
    )
    result = parse_payload(raw)
    assert "XAUUSD" in result.instruments  # not rejected at parse time
    assert result.symbol_meta["XAUUSD"].status == "degraded"  # but the status is preserved


# ---------------------------------------------------------------------------
# indicator fields are still never stored (unchanged behaviour, new schema)
# ---------------------------------------------------------------------------


def test_provider_indicator_fields_are_parsed_out_and_never_stored():
    raw = live_payload_json(
        {"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])},
        extra_symbol_overrides={
            "XAUUSD": {
                "candles_1m": [
                    {
                        "timestamp": BASE_TS,
                        "open": 2400.0,
                        "high": 2401.0,
                        "low": 2399.0,
                        "close": 2400.5,
                        "volume": 10.0,
                        "bid": None,
                        "ask": None,
                        "rsi": 71.4,
                        "ema_20": 2398.2,
                        "macd": 1.23,
                        "atr": 3.5,
                        "vwap": 2400.1,
                        "bollinger_upper": 2405.0,
                    }
                ]
            }
        },
    )
    result = parse_payload(raw)
    assert result.ok
    candle = result.instruments["XAUUSD"][0]
    assert set(vars(candle).keys()) == {
        "instrument", "timeframe", "ts", "open", "high", "low", "close", "volume", "synthetic_from_m1",
    }
    assert candle.open == 2400.0
    assert candle.close == 2400.5
    assert candle.volume == 10.0


# ---------------------------------------------------------------------------
# RealMarketApiClient wrapper
# ---------------------------------------------------------------------------


def test_client_uses_injected_fetch_fn_and_never_hits_network():
    raw = live_payload_json({"XAUUSD": make_m1_series("XAUUSD", [100.0, 100.1])})
    client = RealMarketApiClient("http://example.invalid/should-not-be-called", fetch_fn=lambda: raw)
    result = client.fetch()
    assert result.ok
    assert "XAUUSD" in result.instruments


def test_client_forwards_expected_universe_size():
    instrument_candles = {k: v for k, v in list(_ten_symbol_candles().items())[:8]}
    raw = live_payload_json(instrument_candles)
    client = RealMarketApiClient("http://example.invalid", fetch_fn=lambda: raw)
    result = client.fetch(expected_universe_size=10)
    assert any("8/10" in issue for issue in result.coverage_issues)


def test_client_wraps_fetch_exceptions_as_api_error():
    def boom():
        raise ConnectionError("no route to host")

    client = RealMarketApiClient("http://example.invalid", fetch_fn=boom)
    with pytest.raises(ApiError):
        client.fetch()
