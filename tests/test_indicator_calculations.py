"""Verify every locally-derived indicator/feature against hand-computed
values on known OHLCV fixtures.

RealMarketAPI supplies raw OHLCV + timestamp only — no RSI/EMA/MACD/ATR/
VWAP/Bollinger Bands. Every number asserted here is computed purely from
that raw data by the engine itself (`psygrid/volatility.py`,
`psygrid/momentum.py`, `psygrid/structure.py`, `psygrid/liquidity.py`,
`psygrid/timeframes.py`) — this file exists to prove those computations are
arithmetically correct, not just "runs without crashing".
"""

from __future__ import annotations

from psygrid.liquidity import analyze_liquidity
from psygrid.models import Candle, TrendLabel
from psygrid.momentum import analyze_momentum
from psygrid.structure import find_swings
from psygrid.timeframes import aggregate
from psygrid.volatility import _atr, analyze_volatility, true_range
from tests.fixtures import BASE_TS, make_m1_series


def _candle(ts, o, h, l, c, volume=None):
    return Candle(instrument="X", timeframe="M1", ts=ts, open=o, high=h, low=l, close=c, volume=volume)


# ---------------------------------------------------------------------------
# True range / ATR — hand-computed
# ---------------------------------------------------------------------------


def _build_constant_range_series(n: int):
    """n candles where high-low, |high-prev_close|, |low-prev_close| are all
    exactly 3 for every bar after the first — true range is trivially 3.0
    for every bar, by construction, so ATR of any period is exactly 3.0."""
    candles = []
    for i in range(n):
        o = 100.0 + i
        h = 102.0 + i
        l = 99.0 + i
        c = 101.0 + i
        candles.append(_candle(BASE_TS + i * 60, o, h, l, c))
    return candles


def test_true_range_matches_hand_calculation():
    candles = _build_constant_range_series(2)
    prev, cur = candles[0], candles[1]
    # cur: H=103, L=100, prev close=101
    # H-L = 3; |H-prevC| = |103-101| = 2; |L-prevC| = |100-101| = 1
    # -> max(3, 2, 1) = 3
    assert true_range(prev, cur) == 3.0


def test_true_range_when_gap_dominates():
    prev = _candle(BASE_TS, 100.0, 101.0, 99.0, 100.0)
    cur = _candle(BASE_TS + 60, 110.0, 111.0, 109.5, 110.5)
    # H-L = 1.5; |H-prevC| = |111-100| = 11; |L-prevC| = |109.5-100| = 9.5
    # -> max(1.5, 11, 9.5) = 11 (a gap-up dominates the bar's own range)
    assert true_range(prev, cur) == 11.0


def test_atr_equals_hand_computed_average_of_true_ranges():
    candles = _build_constant_range_series(7)  # atr_period=5 needs >= period+1
    atr = _atr(candles, period=5)
    assert atr == 3.0


def test_analyze_volatility_matches_hand_computed_atr_and_flags():
    candles = _build_constant_range_series(7)
    state = analyze_volatility(candles, atr_period=5, median_window=60, abnormal_multiple=3.5)
    assert state.atr == 3.0
    assert state.relative_volatility == 1.0
    assert state.expansion is False
    assert state.compression is False
    assert state.abnormal is False
    assert state.volatility_quality == 60.0  # baseline quality, no flags triggered


def test_analyze_volatility_detects_hand_verifiable_abnormal_spike():
    candles = _build_constant_range_series(7)
    # Replace the final bar with an obvious, oversized spike: true range vs
    # the previous close jumps from 3 to 50.
    spiked = candles[:-1] + [_candle(candles[-1].ts, 106.0, 156.0, 105.0, 155.0)]
    state = analyze_volatility(spiked, atr_period=5, median_window=60, abnormal_multiple=3.5)
    # current true range = max(156-105, |156-106|, |105-106|) = 51
    # median ATR from history is still 3.0 -> 51 > 3.5 * 3.0 = 10.5
    assert state.abnormal is True
    assert state.volatility_quality == 15.0


def test_insufficient_data_never_fabricates_an_atr_value():
    candles = _build_constant_range_series(3)  # well short of atr_period(14)+2
    state = analyze_volatility(candles)
    assert state.atr == 0.0
    assert "Insufficient history" in state.notes[0]


# ---------------------------------------------------------------------------
# Momentum rate-of-change — hand-computed
# ---------------------------------------------------------------------------


def test_momentum_matches_hand_computed_roc_and_persistence():
    # 25 candles, closes strictly 100, 101, 102, ..., 124 (a clean, fully
    # hand-checkable arithmetic progression).
    closes = [100.0 + i for i in range(25)]
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_momentum(candles, short_bars=5, long_bars=20)

    # roc_short = (close[-1] - close[-6]) / close[-6] = (124 - 119) / 119
    expected_roc_short = (124.0 - 119.0) / 119.0
    # roc_long = (close[-1] - close[-21]) / close[-21] = (124 - 104) / 104
    expected_roc_long = (124.0 - 104.0) / 104.0

    assert round(state.roc_short, 10) == round(expected_roc_short, 10)
    assert round(state.roc_long, 10) == round(expected_roc_long, 10)
    assert state.direction == TrendLabel.UP
    # Strictly increasing for all 24 consecutive bar-pairs.
    assert state.persistence_bars == 24
    # 40 (directional) + 0 (not accelerating) + min(20, 24*4)=20 = 60
    assert state.momentum_quality == 60.0
    assert state.accelerating is False
    assert state.decelerating is False
    assert state.exhaustion is False


def test_momentum_roc_sign_flips_for_downtrend():
    closes = [124.0 - i for i in range(25)]  # mirror image: strictly falling
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_momentum(candles, short_bars=5, long_bars=20)
    assert state.roc_short < 0
    assert state.roc_long < 0
    assert state.direction == TrendLabel.DOWN
    assert state.persistence_bars == 24


def test_momentum_insufficient_history_reports_unknown_not_a_guessed_value():
    closes = [100.0 + i for i in range(10)]  # short of long_bars(20)+1
    candles = make_m1_series("XAUUSD", closes)
    state = analyze_momentum(candles, short_bars=5, long_bars=20)
    assert state.direction == TrendLabel.UNKNOWN
    assert state.roc_short == 0.0
    assert state.roc_long == 0.0
    assert state.momentum_quality == 0.0


# ---------------------------------------------------------------------------
# Swing (structure) detection — hand-computed
# ---------------------------------------------------------------------------


def test_find_swings_detects_exact_single_swing_high():
    # A clean symmetric "triangle": highs rise 10..14 then fall 14..10,
    # lows trail 1 below. The apex at index 4 is the only fractal swing
    # high (wing=2); there is no swing low anywhere in this shape.
    highs = [10, 11, 12, 13, 14, 13, 12, 11, 10]
    candles = [
        _candle(BASE_TS + i * 60, o=h - 0.5, h=h, l=h - 1, c=h - 0.5) for i, h in enumerate(highs)
    ]
    swings = find_swings(candles, wing=2)
    assert len(swings) == 1
    assert swings[0].kind == "HIGH"
    assert swings[0].price == 14.0
    assert swings[0].ts == candles[4].ts


def test_find_swings_detects_exact_single_swing_low():
    lows = [14, 13, 12, 11, 10, 11, 12, 13, 14]  # mirror-image valley
    candles = [
        _candle(BASE_TS + i * 60, o=l + 0.5, h=l + 1, l=l, c=l + 0.5) for i, l in enumerate(lows)
    ]
    swings = find_swings(candles, wing=2)
    assert len(swings) == 1
    assert swings[0].kind == "LOW"
    assert swings[0].price == 10.0
    assert swings[0].ts == candles[4].ts


def test_find_swings_finds_nothing_in_a_monotonic_series():
    # Strictly increasing highs/lows: no interior bar can ever be a local
    # extreme, so there must be exactly zero swings.
    candles = [_candle(BASE_TS + i * 60, o=100 + i, h=101 + i, l=99 + i, c=100 + i) for i in range(20)]
    swings = find_swings(candles, wing=2)
    assert swings == []


# ---------------------------------------------------------------------------
# Timeframe aggregation — hand-computed OHLCV
# ---------------------------------------------------------------------------


def test_aggregation_ohlcv_matches_hand_computation_including_volume():
    # 5 M1 candles -> exactly one M5 bucket. Every field hand-checked.
    candles = [
        _candle(BASE_TS + 0 * 60, 100.0, 101.0, 99.5, 100.5, volume=10.0),
        _candle(BASE_TS + 1 * 60, 100.5, 102.0, 100.0, 101.5, volume=20.0),
        _candle(BASE_TS + 2 * 60, 101.5, 101.8, 98.0, 99.0, volume=15.0),
        _candle(BASE_TS + 3 * 60, 99.0, 103.0, 98.5, 102.5, volume=25.0),
        _candle(BASE_TS + 4 * 60, 102.5, 102.9, 101.0, 102.0, volume=30.0),
    ]
    m5 = aggregate(candles, "M5")
    assert len(m5) == 1
    bucket = m5[0]
    assert bucket.open == 100.0  # first candle's open
    assert bucket.close == 102.0  # last candle's close
    assert bucket.high == 103.0  # max high across all 5
    assert bucket.low == 98.0  # min low across all 5
    assert bucket.volume == 100.0  # 10+20+15+25+30, exact sum
    assert bucket.synthetic_from_m1 is True
    assert bucket.timeframe == "M5"


def test_aggregation_volume_is_none_when_no_source_candle_has_volume():
    candles = [
        _candle(BASE_TS + 0 * 60, 100.0, 101.0, 99.0, 100.5, volume=None),
        _candle(BASE_TS + 1 * 60, 100.5, 101.5, 99.5, 101.0, volume=None),
    ]
    m5 = aggregate(candles, "M5")
    assert m5[0].volume is None  # never fabricated as 0.0


# ---------------------------------------------------------------------------
# Liquidity extremes — hand-computed
# ---------------------------------------------------------------------------


def test_liquidity_recent_high_low_match_hand_computed_extremes():
    # Highs/lows chosen so the maximum/minimum (excluding the final,
    # still-forming bar) are unambiguous and easy to hand-verify.
    highs = [100, 105, 103, 108, 104, 106, 107]  # max excluding last = 108 (idx 3)
    lows = [95, 96, 94, 97, 93, 98, 99]  # min excluding last = 93 (idx 4)
    candles = [
        _candle(BASE_TS + i * 60, o=(h + l) / 2, h=h, l=l, c=(h + l) / 2)
        for i, (h, l) in enumerate(zip(highs, lows))
    ]
    state = analyze_liquidity(candles, h1_candles=[], atr=1.0, lookback_bars=60)
    assert state.recent_high == 108.0
    assert state.recent_low == 93.0
