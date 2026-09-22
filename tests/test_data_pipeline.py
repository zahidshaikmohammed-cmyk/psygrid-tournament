from psygrid.candle_store import InstrumentCandleStore, MultiInstrumentCandleStore
from psygrid.data_quality import assess
from psygrid.models import Candle
from psygrid.timeframes import aggregate, aggregate_all
from tests.fixtures import BASE_TS, make_m1_series


def test_ingest_reports_no_gaps_for_contiguous_series():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.1 for i in range(10)])
    store = InstrumentCandleStore()
    report = store.ingest("XAUUSD", candles)
    assert report.is_continuous
    assert report.missing_candle_count == 0
    assert report.history_count == 10


def test_ingest_detects_missing_candles_gap():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.1 for i in range(10)])
    # Remove three candles from the middle to create a gap.
    gapped = candles[:4] + candles[7:]
    store = InstrumentCandleStore()
    report = store.ingest("XAUUSD", gapped)
    assert not report.is_continuous
    assert report.missing_candle_count == 3


def test_ingest_is_idempotent_on_duplicate_candles():
    candles = make_m1_series("XAUUSD", [100.0, 100.1, 100.2])
    store = InstrumentCandleStore()
    store.ingest("XAUUSD", candles)
    report = store.ingest("XAUUSD", candles)
    assert report.duplicate_candles == 3
    assert report.new_candles == 0
    assert len(store) == 3


def test_ingest_rejects_impossible_ohlc_without_fabricating():
    bad = Candle(instrument="XAUUSD", timeframe="M1", ts=BASE_TS, open=100.0, high=99.0, low=101.0, close=100.0)
    good = make_m1_series("XAUUSD", [100.0])[0]
    store = InstrumentCandleStore()
    report = store.ingest("XAUUSD", [good, bad])
    assert report.invalid_candles == 1
    assert len(store) == 1


def test_ingest_flags_misaligned_timestamp():
    candles = make_m1_series("XAUUSD", [100.0, 100.1])
    misaligned = Candle(
        instrument="XAUUSD", timeframe="M1", ts=BASE_TS + 90, open=100.1, high=100.2, low=100.0, close=100.15
    )
    store = InstrumentCandleStore()
    report = store.ingest("XAUUSD", candles + [misaligned])
    assert report.timestamps_valid is False


def test_rolling_window_trims_to_max_candles():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.01 for i in range(50)])
    store = InstrumentCandleStore(max_candles=20)
    store.ingest("XAUUSD", candles)
    assert len(store) == 20
    ordered = store.ordered_candles()
    assert ordered[0].ts == candles[-20].ts


def test_multi_instrument_store_keeps_instruments_independent():
    a = make_m1_series("XAUUSD", [100.0, 100.1])
    b = make_m1_series("EURUSD", [1.1, 1.11, 1.12])
    store = MultiInstrumentCandleStore()
    store.ingest("XAUUSD", a)
    store.ingest("EURUSD", b)
    assert len(store.candles("XAUUSD")) == 2
    assert len(store.candles("EURUSD")) == 3
    assert set(store.instruments()) == {"XAUUSD", "EURUSD"}


# -- data quality -----------------------------------------------------------


def test_data_quality_fresh_and_sufficient_is_usable():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.01 for i in range(210)])
    store = InstrumentCandleStore()
    report = store.ingest("XAUUSD", candles)
    now_ts = candles[-1].ts + 10  # 10 seconds after latest candle
    dq = assess("XAUUSD", store.ordered_candles(), report, now_ts, freshness_max_seconds=180, history_min_candles=200)
    assert dq.is_usable
    assert dq.is_fresh


def test_data_quality_stale_data_is_not_usable():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.01 for i in range(210)])
    store = InstrumentCandleStore()
    report = store.ingest("XAUUSD", candles)
    now_ts = candles[-1].ts + 600  # 10 minutes stale
    dq = assess("XAUUSD", store.ordered_candles(), report, now_ts, freshness_max_seconds=180, history_min_candles=200)
    assert not dq.is_fresh
    assert not dq.is_usable
    assert any("old" in issue for issue in dq.issues)


def test_data_quality_insufficient_history_is_not_usable():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.01 for i in range(20)])
    store = InstrumentCandleStore()
    report = store.ingest("XAUUSD", candles)
    now_ts = candles[-1].ts + 5
    dq = assess("XAUUSD", store.ordered_candles(), report, now_ts, freshness_max_seconds=180, history_min_candles=200)
    assert not dq.has_sufficient_history
    assert not dq.is_usable


def test_data_quality_no_candles_is_unknown_not_usable():
    dq = assess("XAUUSD", [], _empty_report(), 0, 180, 200)
    assert dq.history_count == 0
    assert not dq.is_usable


def _empty_report():
    from psygrid.candle_store import IngestReport

    return IngestReport(
        instrument="XAUUSD",
        new_candles=0,
        duplicate_candles=0,
        invalid_candles=0,
        missing_candle_count=0,
        is_continuous=True,
        timestamps_valid=True,
        latest_ts=None,
        history_count=0,
    )


# -- timeframe aggregation ----------------------------------------------------


def test_aggregate_m5_buckets_align_to_clock_and_are_marked_synthetic():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.01 for i in range(15)], start_ts=BASE_TS)
    m5 = aggregate(candles, "M5")
    assert len(m5) == 3
    for c in m5:
        assert c.synthetic_from_m1 is True
        assert c.timeframe == "M5"
        assert c.ts % (5 * 60) == 0


def test_aggregate_ohlc_matches_manual_expectation():
    closes = [100.0, 100.5, 99.5, 101.0, 100.0]
    candles = make_m1_series("XAUUSD", closes, start_ts=BASE_TS)
    m5 = aggregate(candles, "M5")
    assert len(m5) == 1
    bucket = m5[0]
    assert bucket.open == candles[0].open
    assert bucket.close == candles[-1].close
    assert bucket.high == max(c.high for c in candles)
    assert bucket.low == min(c.low for c in candles)


def test_aggregate_does_not_fabricate_bucket_with_no_data():
    # Only minute 0 and minute 12 present -> M5 buckets for minutes 5-9 must
    # not appear at all.
    candles = make_m1_series("XAUUSD", [100.0, 100.5], start_ts=BASE_TS)
    candles[1] = candles[1].__class__(
        instrument="XAUUSD",
        timeframe="M1",
        ts=BASE_TS + 12 * 60,
        open=candles[1].open,
        high=candles[1].high,
        low=candles[1].low,
        close=candles[1].close,
    )
    m5 = aggregate(candles, "M5")
    bucket_starts = {c.ts for c in m5}
    assert (BASE_TS // 300) * 300 in bucket_starts
    assert (BASE_TS + 300) not in bucket_starts or len(m5) == 2


def test_aggregate_all_returns_every_supported_timeframe():
    candles = make_m1_series("XAUUSD", [100.0 + i * 0.01 for i in range(70)], start_ts=BASE_TS)
    all_tf = aggregate_all(candles)
    assert set(all_tf.keys()) == {"M5", "M15", "M30", "H1"}
    for tf, series in all_tf.items():
        assert all(c.synthetic_from_m1 for c in series)
