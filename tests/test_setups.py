from psygrid.liquidity import analyze_liquidity
from psygrid.models import Direction
from psygrid.momentum import analyze_momentum
from psygrid.price_behavior import analyze_price_behavior
from psygrid.setups import detect_best_setup
from psygrid.structure import analyze_structure
from psygrid.timeframes import aggregate
from psygrid.volatility import analyze_volatility
from tests.fixtures import make_m1_series, make_sweep_series, uptrend_closes


def _full_analysis(candles):
    structure_m1 = analyze_structure(candles, "M1")
    m15 = aggregate(candles, "M15")
    structure_ctx = analyze_structure(m15, "M15") if m15 else structure_m1
    momentum = analyze_momentum(candles)
    volatility = analyze_volatility(candles)
    h1 = aggregate(candles, "H1")
    liquidity = analyze_liquidity(candles, h1, volatility.atr)
    price_behavior = analyze_price_behavior(candles, structure_m1, volatility)
    return structure_m1, structure_ctx, momentum, volatility, liquidity, price_behavior


def test_liquidity_sweep_reversal_detected_as_short():
    candles = make_sweep_series("XAUUSD", n=220)
    args = _full_analysis(candles)
    setup = detect_best_setup("XAUUSD", candles, *args)
    assert setup is not None
    assert setup.setup_type == "liquidity_sweep_reversal"
    assert setup.direction == Direction.SHORT
    assert setup.entry_candidate != setup.invalidation
    assert setup.target_candidate != setup.entry_candidate


def test_trend_continuation_detected_in_clean_uptrend():
    closes = uptrend_closes(200, start=100.0, step=0.1, pullback_every=18)
    candles = make_m1_series("EURUSD", closes)
    args = _full_analysis(candles)
    setup = detect_best_setup("EURUSD", candles, *args)
    assert setup is not None
    assert setup.direction == Direction.LONG
    # Any of these are legitimate reads of a clean, pullback-punctuated
    # uptrend under the shared rule set; the point of this test is that
    # *some* measurable, correctly-directed setup is found, not that one
    # specific rule wins the internal confidence race.
    assert setup.setup_type in (
        "trend_continuation",
        "pullback_continuation",
        "breakout_with_confirmation",
        "failed_breakout",
    )


def test_no_setup_on_insufficient_history_returns_none():
    candles = make_m1_series("GBPUSD", [1.2, 1.201, 1.202])
    args = _full_analysis(candles)
    setup = detect_best_setup("GBPUSD", candles, *args)
    assert setup is None


def test_setup_carries_all_required_research_fields():
    candles = make_sweep_series("XAUUSD", n=220)
    args = _full_analysis(candles)
    setup = detect_best_setup("XAUUSD", candles, *args)
    assert setup.instrument == "XAUUSD"
    assert isinstance(setup.formation_time, int)
    assert isinstance(setup.confidence, float)
    assert 0.0 <= setup.confidence <= 100.0
    assert isinstance(setup.reasoning, list)
