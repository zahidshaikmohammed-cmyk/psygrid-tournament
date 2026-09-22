import json

import pytest

from psygrid.api_client import ApiError, RealMarketApiClient, parse_payload
from tests.fixtures import BASE_TS, api_payload_json, make_m1_series


def test_parses_well_formed_payload():
    candles = make_m1_series("XAUUSD", [100.0, 100.5, 101.0])
    raw = api_payload_json({"XAUUSD": candles})
    result = parse_payload(raw)
    assert result.ok
    assert "XAUUSD" in result.instruments
    assert len(result.instruments["XAUUSD"]) == 3
    assert result.instruments["XAUUSD"][0].close == 100.0
    assert result.skipped_candles["XAUUSD"] == 0


def test_parses_flat_top_level_shape():
    raw = json.dumps(
        {
            "EURUSD": [
                {"t": BASE_TS, "o": 1.1, "h": 1.11, "l": 1.09, "c": 1.105},
                {"t": BASE_TS + 60, "o": 1.105, "h": 1.115, "l": 1.10, "c": 1.11},
            ]
        }
    )
    result = parse_payload(raw)
    assert result.ok
    assert len(result.instruments["EURUSD"]) == 2


def test_parses_list_of_instrument_objects_shape():
    raw = json.dumps(
        [
            {
                "symbol": "GBPUSD",
                "candles": [{"time": BASE_TS, "open": 1.2, "high": 1.21, "low": 1.19, "close": 1.205}],
            }
        ]
    )
    result = parse_payload(raw)
    assert result.ok
    assert "GBPUSD" in result.instruments


def test_malformed_json_raises_api_error():
    with pytest.raises(ApiError):
        parse_payload("{not valid json")


def test_missing_ohlc_fields_are_dropped_not_fabricated():
    raw = json.dumps(
        {
            "instruments": {
                "USDJPY": {
                    "candles": [
                        {"time": BASE_TS, "open": 150.0, "high": 150.2, "low": 149.8},  # missing close
                        {"time": BASE_TS + 60, "open": 150.1, "high": 150.3, "low": 149.9, "close": 150.2},
                    ]
                }
            }
        }
    )
    result = parse_payload(raw)
    assert len(result.instruments["USDJPY"]) == 1
    assert result.skipped_candles["USDJPY"] == 1


def test_impossible_ohlc_is_rejected_by_candle_validation():
    candles = make_m1_series("AUDUSD", [1.0, 1.01])
    bad = candles[0]
    # Corrupt: high below low.
    from psygrid.models import Candle

    corrupted = Candle(
        instrument=bad.instrument, timeframe="M1", ts=bad.ts, open=1.0, high=0.5, low=0.9, close=1.0
    )
    assert not corrupted.is_valid_ohlc()


def test_empty_payload_produces_no_instruments_and_error_note():
    result = parse_payload(json.dumps({"instruments": {}}))
    assert not result.ok
    assert result.parse_errors


def test_unexpected_top_level_type_raises():
    with pytest.raises(ApiError):
        parse_payload(json.dumps(42))


def test_client_uses_injected_fetch_fn_and_never_hits_network():
    candles = make_m1_series("XAUUSD", [100.0, 100.1])
    raw = api_payload_json({"XAUUSD": candles})
    client = RealMarketApiClient("http://example.invalid/should-not-be-called", fetch_fn=lambda: raw)
    result = client.fetch()
    assert result.ok
    assert "XAUUSD" in result.instruments


def test_client_wraps_fetch_exceptions_as_api_error():
    def boom():
        raise ConnectionError("no route to host")

    client = RealMarketApiClient("http://example.invalid", fetch_fn=boom)
    with pytest.raises(ApiError):
        client.fetch()


def test_provider_indicator_fields_are_parsed_out_and_never_stored():
    # RealMarketAPI is contractually OHLCV + timestamp only, but even if a
    # payload included indicator-looking fields (rsi/ema/macd/atr/vwap), the
    # parser must only ever read the fixed OHLCV keys it knows about and
    # silently drop everything else — never store, forward, or let an
    # indicator value ride along as if it were raw market data.
    raw = json.dumps(
        {
            "instruments": {
                "XAUUSD": {
                    "candles": [
                        {
                            "time": BASE_TS,
                            "open": 2400.0,
                            "high": 2401.0,
                            "low": 2399.0,
                            "close": 2400.5,
                            "volume": 10.0,
                            "rsi": 71.4,
                            "ema_20": 2398.2,
                            "macd": 1.23,
                            "atr": 3.5,
                            "vwap": 2400.1,
                            "bollinger_upper": 2405.0,
                        }
                    ]
                }
            }
        }
    )
    result = parse_payload(raw)
    assert result.ok
    candle = result.instruments["XAUUSD"][0]
    # Candle is a fixed-field dataclass — there is no attribute an
    # indicator value could have been smuggled into.
    assert set(vars(candle).keys()) == {
        "instrument",
        "timeframe",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "synthetic_from_m1",
    }
    assert candle.open == 2400.0
    assert candle.close == 2400.5
    assert candle.volume == 10.0
