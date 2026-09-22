"""Time-behaviour analysis: trading session, setup age/freshness, travel distance.

Session boundaries are a documented, approximate UTC banding — not sourced
from any calendar feed — used only to label context, never as a hard gate
by itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import TimeBehaviorState


def classify_session(ts: int) -> str:
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    if 0 <= hour < 7:
        return "ASIAN"
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 16:
        return "OVERLAP"
    if 16 <= hour < 21:
        return "NEWYORK"
    return "ASIAN"


def analyze_time_behavior(
    now_ts: int,
    formation_time: Optional[int],
    current_price: Optional[float],
    entry_candidate: Optional[float],
    risk: Optional[float],
    max_setup_age_minutes: float,
    max_travel_ratio: float,
) -> TimeBehaviorState:
    session = classify_session(now_ts)

    setup_age_minutes = None
    is_stale = False
    if formation_time is not None:
        setup_age_minutes = (now_ts - formation_time) / 60.0
        is_stale = setup_age_minutes > max_setup_age_minutes

    travel_ratio = None
    travelled_too_far = False
    if current_price is not None and entry_candidate is not None and risk:
        travel_ratio = abs(current_price - entry_candidate) / risk
        travelled_too_far = travel_ratio > max_travel_ratio

    return TimeBehaviorState(
        session=session,
        setup_age_minutes=setup_age_minutes,
        is_stale=is_stale,
        travel_ratio=travel_ratio,
        travelled_too_far=travelled_too_far,
    )
