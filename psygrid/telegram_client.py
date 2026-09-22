"""Telegram Bot API integration.

Credentials come ONLY from ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``
environment variables (see :mod:`psygrid.config`) — never hard-coded here.
The HTTP call is injectable (``post_fn``) so tests can verify formatting and
failure handling without any network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

from .models import CandidateStatus, TournamentResult, TournamentRow

IST = timezone(timedelta(hours=5, minutes=30))
SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


class TelegramError(Exception):
    pass


@dataclass
class SendResult:
    ok: bool
    error: Optional[str] = None


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, post_fn: Optional[Callable[[str, dict], dict]] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._post_fn = post_fn

    def _default_post(self, url: str, payload: dict) -> dict:
        if requests is None:
            raise TelegramError("The 'requests' package is not installed.")
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if not resp.ok or not data.get("ok", False):
            raise TelegramError(f"Telegram API error: HTTP {resp.status_code} {data}")
        return data

    def send_message(self, text: str) -> SendResult:
        if not self.bot_token or not self.chat_id:
            return SendResult(ok=False, error="Telegram credentials are not configured.")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}
        try:
            if self._post_fn is not None:
                self._post_fn(url, payload)
            else:
                self._default_post(url, payload)
            return SendResult(ok=True)
        except Exception as exc:  # never let a Telegram failure crash the engine
            return SendResult(ok=False, error=str(exc))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def to_ist_string(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=IST).strftime("%H:%M IST")


def _fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "UNKNOWN"
    text = f"{value:.5f}".rstrip("0")
    if text.endswith("."):
        text += "00"
    parts = text.split(".")
    if len(parts[1]) < 2:
        text = f"{parts[0]}.{parts[1]:0<2}"
    return text


def _qualitative(score: Optional[float], high: float = 75.0, mid: float = 50.0) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= high:
        return "STRONG"
    if score >= mid:
        return "MODERATE"
    return "WEAK"


def _structure_label(score: Optional[float]) -> str:
    if score is None:
        return "UNKNOWN"
    return "CONFIRMED" if score >= 60.0 else "DEVELOPING"


def _liquidity_label(row: TournamentRow) -> str:
    if row.breakdown is None:
        return "UNKNOWN"
    return "CONFIRMED" if row.breakdown.liquidity_quality >= 55.0 else "NOT CONFIRMED"


def _volatility_label(row: TournamentRow) -> str:
    if row.breakdown is None:
        return "UNKNOWN"
    score = row.breakdown.volatility_quality
    if score >= 60:
        return "ACCEPTABLE"
    if score >= 30:
        return "ELEVATED"
    return "ABNORMAL"


def format_no_trade_message(ts: int, result: TournamentResult) -> str:
    lines = [
        SEPARATOR,
        "PSYGRID TOURNAMENT",
        SEPARATOR,
        "",
        f"TIME: {to_ist_string(ts)}",
        "",
        "RESULT:",
        "NO TRADE",
        "",
        "REASON:",
        "No instrument passed minimum quality gates."
        if any(r.status == CandidateStatus.REJECT for r in result.rows)
        else "No qualifying candidate emerged from this cycle.",
        "",
        f"Candidates evaluated: {result.candidates_evaluated}",
        f"Data coverage: {result.data_coverage}",
        "",
        SEPARATOR,
    ]
    return "\n".join(lines)


def format_winner_message(ts: int, result: TournamentResult) -> str:
    winner = result.winner
    assert winner is not None
    setup = winner.setup
    execution = winner.execution
    second = result.second

    rank_text = "1 / " + str(result.candidates_evaluated)
    second_line = f"{second.instrument}" if second else "N/A"
    lead_line = f"+{result.lead:.1f}" if result.lead is not None else "N/A"
    setup_age = (
        f"{winner.setup_age_minutes:.0f} minutes" if winner.setup_age_minutes is not None else "UNKNOWN"
    )
    freshness = (
        f"{winner.data_freshness_seconds:.0f}" if winner.data_freshness_seconds is not None else "UNKNOWN"
    )

    lines = [
        SEPARATOR,
        "🔥 PSYGRID TOURNAMENT",
        SEPARATOR,
        "",
        f"TIME: {to_ist_string(ts)}",
        "",
        f"WINNER: {winner.instrument}",
        f"RANK: {rank_text}",
        "",
        f"DIRECTION: {setup.direction.value if setup else 'UNKNOWN'}",
        "",
        "SETUP:",
        setup.setup_type.replace("_", " ").title() if setup else "UNKNOWN",
        "",
        "QUALITY SCORE:",
        f"{winner.score:.1f}",
        "",
        f"STRUCTURE:\n{_structure_label(winner.breakdown.structure_quality if winner.breakdown else None)}",
        "",
        f"MOMENTUM:\n{_qualitative(winner.breakdown.momentum_quality if winner.breakdown else None)}",
        "",
        f"LIQUIDITY:\n{_liquidity_label(winner)}",
        "",
        f"VOLATILITY:\n{_volatility_label(winner)}",
        "",
        f"R:R:\n{execution.rr:.1f}" if execution else "R:R:\nUNKNOWN",
        "",
        f"SETUP AGE:\n{setup_age}",
        "",
        "ENTRY:",
        _fmt_price(setup.entry_candidate if setup else None),
        "",
        "INVALIDATION:",
        _fmt_price(setup.invalidation if setup else None),
        "",
        "TARGET:",
        _fmt_price(setup.target_candidate if setup else None),
        "",
        "EXPECTED HOLD:",
        "Research-derived / UNKNOWN",
        "",
        "DATA FRESHNESS:",
        f"{freshness} seconds",
        "",
        "SECOND PLACE:",
        second_line,
        "",
        "LEAD:",
        lead_line,
        "",
        "STATUS:",
        "CANDIDATE — NOT GUARANTEED",
        "",
        SEPARATOR,
    ]
    return "\n".join(lines)


def format_data_unavailable_message(ts: int, reason: str) -> str:
    lines = [
        SEPARATOR,
        "PSYGRID TOURNAMENT",
        SEPARATOR,
        "",
        f"TIME: {to_ist_string(ts)}",
        "",
        "RESULT:",
        "DATA UNAVAILABLE",
        "",
        "REASON:",
        reason,
        "",
        SEPARATOR,
    ]
    return "\n".join(lines)


def format_tournament_message(ts: int, result: TournamentResult) -> str:
    if result.winner is not None:
        return format_winner_message(ts, result)
    return format_no_trade_message(ts, result)
