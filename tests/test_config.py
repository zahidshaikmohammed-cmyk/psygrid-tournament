from psygrid.config import MIN_RELIABLE_HISTORY_CANDLES, Config


def test_default_config_validates_clean():
    assert Config().validate() == []


def test_default_history_min_candles_covers_the_widest_feature_lookback():
    # Structure's swing lookback (120 bars) is the widest window any
    # locally-derived feature needs; the shipped default must reliably
    # cover it so features are never silently starved of history.
    assert Config().history_min_candles >= MIN_RELIABLE_HISTORY_CANDLES


def test_validate_flags_history_min_candles_below_widest_feature_window():
    config = Config(history_min_candles=MIN_RELIABLE_HISTORY_CANDLES - 1, max_rolling_candles=1600)
    problems = config.validate()
    assert any("PSYGRID_HISTORY_MIN_CANDLES" in p for p in problems)


def test_validate_flags_rolling_window_smaller_than_history_minimum():
    config = Config(history_min_candles=200, max_rolling_candles=100)
    problems = config.validate()
    assert any("PSYGRID_MAX_ROLLING_CANDLES" in p for p in problems)


def test_validate_flags_bad_scoring_weights():
    config = Config(weight_structure=0.9)  # weights now sum well over 1.0
    problems = config.validate()
    assert any("weights must sum to 1.0" in p for p in problems)


def test_validate_flags_non_positive_min_rr():
    config = Config(min_rr=0.0)
    problems = config.validate()
    assert any("PSYGRID_MIN_RR" in p for p in problems)


def test_validate_flags_empty_api_url():
    config = Config(api_url="")
    problems = config.validate()
    assert any("REALMARKET_API_URL" in p for p in problems)
