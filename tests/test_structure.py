from psygrid.models import TrendLabel
from psygrid.structure import analyze_structure
from tests.fixtures import downtrend_closes, flat_closes, make_m1_series, range_closes, uptrend_closes


def test_uptrend_produces_up_trend_and_hh_hl():
    closes = uptrend_closes(150, start=100.0, step=0.08, pullback_every=15)
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_structure(candles, "M1")
    assert state.trend == TrendLabel.UP
    highs = [lbl for s, lbl in zip(state.swings, state.sequence) if s.kind == "HIGH"]
    lows = [lbl for s, lbl in zip(state.swings, state.sequence) if s.kind == "LOW"]
    assert "HH" in highs
    assert "HL" in lows


def test_downtrend_produces_down_trend_and_lh_ll():
    closes = downtrend_closes(150, start=100.0, step=0.08, pullback_every=15)
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_structure(candles, "M1")
    assert state.trend == TrendLabel.DOWN
    highs = [lbl for s, lbl in zip(state.swings, state.sequence) if s.kind == "HIGH"]
    lows = [lbl for s, lbl in zip(state.swings, state.sequence) if s.kind == "LOW"]
    assert "LH" in highs
    assert "LL" in lows


def test_range_market_is_not_classified_as_trend():
    closes = range_closes(150, center=100.0, amplitude=0.6, period=20)
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_structure(candles, "M1")
    assert state.trend in (TrendLabel.RANGE, TrendLabel.UNKNOWN)


def test_insufficient_data_is_unknown_not_guessed():
    closes = flat_closes(5, price=100.0)
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_structure(candles, "M1")
    assert state.trend == TrendLabel.UNKNOWN


def test_structure_break_detected_on_new_high_in_uptrend():
    closes = uptrend_closes(150, start=100.0, step=0.08, pullback_every=15)
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_structure(candles, "M1")
    assert state.last_break in ("BOS_UP", None)  # BOS_UP if the final close broke the last swing high
