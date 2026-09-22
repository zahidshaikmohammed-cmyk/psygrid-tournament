"""30-minute tournament scheduler.

Continuous scanning happens on every loop tick; the tournament itself fires
exactly once per boundary crossing (UTC :00 and :30 marks — which are also
IST :00/:30 marks, since IST is UTC+5:30). Restart-safe: the clock can be
seeded from the last persisted tournament timestamp so a restart never
re-fires the boundary that already ran, and never fires more than one
"catch-up" tournament for time missed while the engine was down.
"""

from __future__ import annotations

from typing import Optional, Tuple


class TournamentClock:
    def __init__(self, interval_minutes: int = 30, last_fired_boundary: Optional[int] = None):
        self.interval_seconds = interval_minutes * 60
        self.last_fired_boundary = last_fired_boundary

    def boundary_for(self, ts: int) -> int:
        return (ts // self.interval_seconds) * self.interval_seconds

    def next_boundary_after(self, ts: int) -> int:
        boundary = self.boundary_for(ts)
        return boundary if boundary > ts else boundary + self.interval_seconds

    def should_fire(self, now_ts: int) -> Tuple[bool, int]:
        boundary = self.boundary_for(now_ts)
        if self.last_fired_boundary is not None and boundary <= self.last_fired_boundary:
            return False, boundary
        return True, boundary

    def mark_fired(self, boundary: int) -> None:
        self.last_fired_boundary = boundary

    @classmethod
    def restore(cls, interval_minutes: int, last_tournament_ts: Optional[int]) -> "TournamentClock":
        clock = cls(interval_minutes)
        if last_tournament_ts is not None:
            clock.last_fired_boundary = clock.boundary_for(last_tournament_ts)
        return clock
