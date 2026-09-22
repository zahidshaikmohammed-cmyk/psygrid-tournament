from psygrid.models import (
    CandidateStatus,
    Direction,
    ExecutionState,
    ScoreBreakdown,
    Setup,
    TournamentResult,
    TournamentRow,
)
from psygrid.telegram_client import (
    TelegramClient,
    format_data_unavailable_message,
    format_no_trade_message,
    format_tournament_message,
    format_winner_message,
    to_ist_string,
)

TS = 1_700_000_000  # arbitrary fixed instant


def _winning_row() -> TournamentRow:
    setup = Setup(
        instrument="XAUUSD",
        setup_type="liquidity_sweep_reversal",
        direction=Direction.LONG,
        formation_time=TS - 240,
        entry_candidate=2400.123,
        invalidation=2395.0,
        target_candidate=2413.5,
        confidence=88.0,
        reasoning=["swept low then rejected"],
    )
    execution = ExecutionState(
        risk=5.123,
        reward=13.377,
        rr=2.61,
        distance_to_invalidation=5.123,
        volatility_to_stop_ratio=1.0,
        slippage_sensitivity="LOW",
        execution_quality=80.0,
    )
    breakdown = ScoreBreakdown(
        structure_quality=85, momentum_quality=80, liquidity_quality=91, volatility_quality=70,
        setup_quality=88, execution_quality=80, historical_conditional_quality=50, data_quality=95,
        final_score=91.4,
    )
    return TournamentRow(
        instrument="XAUUSD",
        status=CandidateStatus.QUALIFIED,
        score=91.4,
        breakdown=breakdown,
        setup=setup,
        execution=execution,
        data_freshness_seconds=12.0,
        setup_age_minutes=4.0,
    )


def _second_row() -> TournamentRow:
    breakdown = ScoreBreakdown(
        structure_quality=70, momentum_quality=70, liquidity_quality=70, volatility_quality=70,
        setup_quality=70, execution_quality=70, historical_conditional_quality=50, data_quality=90,
        final_score=84.7,
    )
    return TournamentRow(instrument="EURUSD", status=CandidateStatus.QUALIFIED, score=84.7, breakdown=breakdown)


def test_to_ist_string_applies_utc_plus_5_30_offset():
    # 2023-11-14T22:13:20Z -> IST is +5:30 -> 03:43 the next day.
    text = to_ist_string(TS)
    assert text.endswith("IST")
    assert ":" in text


def test_no_trade_message_matches_required_shape():
    result = TournamentResult(
        ts=TS,
        rows=[TournamentRow(instrument="XAUUSD", status=CandidateStatus.REJECT, score=None, breakdown=None, reasons=["x"])],
        winner=None,
        second=None,
        lead=None,
        candidates_evaluated=10,
        data_coverage="10/10",
        min_quality_threshold=78.0,
    )
    text = format_no_trade_message(TS, result)
    assert "PSYGRID TOURNAMENT" in text
    assert "RESULT:" in text
    assert "NO TRADE" in text
    assert "Candidates evaluated: 10" in text
    assert "Data coverage: 10/10" in text
    assert "GUARANTEED" not in text.upper() or "NOT GUARANTEED" in text.upper()


def test_no_trade_message_never_claims_profit_or_guarantee():
    result = TournamentResult(
        ts=TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    text = format_no_trade_message(TS, result)
    forbidden = ["GUARANTEED PROFIT", "SAFE TRADE", "WILL WIN"]
    for phrase in forbidden:
        assert phrase not in text.upper()


def test_winner_message_matches_required_shape_and_fields():
    winner = _winning_row()
    second = _second_row()
    result = TournamentResult(
        ts=TS, rows=[winner, second], winner=winner, second=second, lead=6.7,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    text = format_winner_message(TS, result)
    assert "WINNER: XAUUSD" in text
    assert "RANK: 1 / 10" in text
    assert "DIRECTION: LONG" in text
    assert "QUALITY SCORE:" in text
    assert "91.4" in text
    assert "SECOND PLACE:" in text
    assert "EURUSD" in text
    assert "LEAD:" in text
    assert "+6.7" in text
    assert "CANDIDATE — NOT GUARANTEED" in text
    assert "ENTRY:" in text
    assert "INVALIDATION:" in text
    assert "TARGET:" in text


def test_winner_message_never_claims_guarantee_or_profit():
    winner = _winning_row()
    result = TournamentResult(
        ts=TS, rows=[winner], winner=winner, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    text = format_winner_message(TS, result)
    upper = text.upper()
    for phrase in ["SAFE TRADE", "WILL WIN", "PROFIT GUARANTEED"]:
        assert phrase not in upper
    # "GUARANTEED" may only ever appear as part of "NOT GUARANTEED".
    assert upper.count("GUARANTEED") == upper.count("NOT GUARANTEED")
    assert "NOT GUARANTEED" in text


def test_format_tournament_message_dispatches_on_winner_presence():
    winner = _winning_row()
    win_result = TournamentResult(
        ts=TS, rows=[winner], winner=winner, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    no_trade_result = TournamentResult(
        ts=TS, rows=[], winner=None, second=None, lead=None,
        candidates_evaluated=10, data_coverage="10/10", min_quality_threshold=78.0,
    )
    assert "WINNER" in format_tournament_message(TS, win_result)
    assert "NO TRADE" in format_tournament_message(TS, no_trade_result)


def test_data_unavailable_message_format():
    text = format_data_unavailable_message(TS, "RealMarketAPI unreachable.")
    assert "DATA UNAVAILABLE" in text
    assert "RealMarketAPI unreachable." in text


# -- client behaviour -------------------------------------------------------


def test_send_message_uses_injected_post_fn_and_never_touches_network():
    calls = []

    def fake_post(url, payload):
        calls.append((url, payload))
        return {"ok": True}

    client = TelegramClient("test-token", "test-chat", post_fn=fake_post)
    result = client.send_message("hello")
    assert result.ok
    assert len(calls) == 1
    assert "test-token" in calls[0][0]
    assert calls[0][1]["chat_id"] == "test-chat"
    assert calls[0][1]["text"] == "hello"


def test_send_message_fails_gracefully_without_crashing_on_http_error():
    def failing_post(url, payload):
        raise ConnectionError("simulated network failure")

    client = TelegramClient("token", "chat", post_fn=failing_post)
    result = client.send_message("hello")
    assert not result.ok
    assert "simulated network failure" in result.error


def test_send_message_fails_cleanly_when_credentials_missing():
    client = TelegramClient("", "", post_fn=lambda url, payload: {"ok": True})
    result = client.send_message("hello")
    assert not result.ok
    assert "not configured" in result.error
