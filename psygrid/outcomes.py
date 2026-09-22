"""Forward outcome tracking for recorded tournament candidates.

Every winning (and, for research purposes, near-miss) candidate is watched
forward in time so that MFE/MAE and fixed-horizon outcomes (1/5/10/15/30
minutes) get filled in from genuine subsequent M1 candles — never
fabricated. This is what eventually lets the historical-conditional
component of the score be validated instead of assumed.
"""

from __future__ import annotations

from typing import List

from .candle_store import MultiInstrumentCandleStore
from .models import CandidateOutcome, Direction, Setup, TournamentResult, TournamentRow
from .persistence import Persistence

HORIZONS_MINUTES = (1, 5, 10, 15, 30)
RESOLUTION_GRACE_MINUTES = 35


def record_candidate(
    persistence: Persistence,
    tournament_id: int,
    tournament_ts: int,
    row: TournamentRow,
    regime: str,
    session: str,
    volatility_atr: float,
    structure_state: str,
) -> None:
    setup = row.setup
    if setup is None:
        return
    outcome = CandidateOutcome(
        tournament_ts=tournament_ts,
        instrument=row.instrument,
        setup_type=setup.setup_type,
        direction=setup.direction.value,
        entry=setup.entry_candidate,
        stop=setup.invalidation,
        target=setup.target_candidate,
        regime=regime,
        session=session,
        volatility=volatility_atr,
        structure_state=structure_state,
    )
    persistence.save_candidate_outcome(tournament_id, outcome)


def update_open_outcomes(
    persistence: Persistence, candle_store: MultiInstrumentCandleStore, now_ts: int
) -> int:
    updated = 0
    for row in persistence.open_candidate_outcomes():
        instrument = row["instrument"]
        entry_ts = row["tournament_ts"]
        entry = row["entry"]
        stop = row["stop"]
        direction = row["direction"]
        risk = abs(entry - stop)
        if risk <= 0:
            persistence.update_candidate_outcome(row["id"], resolved=1)
            continue

        candles = [c for c in candle_store.candles(instrument) if c.ts >= entry_ts]
        sign = 1 if direction == Direction.LONG.value else -1

        fields = {}
        if candles:
            best_favorable = None
            best_favorable_ts = None
            worst_adverse = None
            worst_adverse_ts = None
            for c in candles:
                if sign == 1:
                    favorable = c.high - entry
                    adverse = entry - c.low
                else:
                    favorable = entry - c.low
                    adverse = c.high - entry
                if best_favorable is None or favorable > best_favorable:
                    best_favorable = favorable
                    best_favorable_ts = c.ts
                if worst_adverse is None or adverse > worst_adverse:
                    worst_adverse = adverse
                    worst_adverse_ts = c.ts

            fields["mfe"] = round(best_favorable / risk, 4)
            fields["mae"] = round(worst_adverse / risk, 4)
            fields["time_to_mfe"] = int((best_favorable_ts - entry_ts) / 60)
            fields["time_to_mae"] = int((worst_adverse_ts - entry_ts) / 60)

            for horizon in HORIZONS_MINUTES:
                target_ts = entry_ts + horizon * 60
                candidates_at_or_after = [c for c in candles if c.ts >= target_ts]
                if not candidates_at_or_after:
                    continue
                marker = candidates_at_or_after[0]
                outcome_r = sign * (marker.close - entry) / risk
                fields[f"outcome_{horizon}m"] = round(outcome_r, 4)

        age_minutes = (now_ts - entry_ts) / 60.0
        if fields.get("outcome_30m") is not None or age_minutes >= RESOLUTION_GRACE_MINUTES:
            fields["resolved"] = 1

        if fields:
            persistence.update_candidate_outcome(row["id"], **fields)
            updated += 1

    return updated
