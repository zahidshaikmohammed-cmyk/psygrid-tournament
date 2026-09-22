from psygrid.scheduler import TournamentClock

THIRTY_MIN = 30 * 60


def test_boundary_alignment_to_30_minute_marks():
    clock = TournamentClock(interval_minutes=30)
    ts = 1_700_000_000
    boundary = clock.boundary_for(ts)
    assert boundary % THIRTY_MIN == 0
    assert boundary <= ts


def test_fires_exactly_once_per_boundary_even_with_many_ticks():
    clock = TournamentClock(interval_minutes=30)
    base_boundary = clock.boundary_for(1_700_000_000)
    fired_count = 0
    # Simulate many scan ticks (e.g. every 20s) inside the same 30-min window.
    for offset in range(0, THIRTY_MIN, 20):
        fire, boundary = clock.should_fire(base_boundary + offset)
        if fire:
            clock.mark_fired(boundary)
            fired_count += 1
    assert fired_count == 1


def test_fires_again_on_next_boundary():
    clock = TournamentClock(interval_minutes=30)
    base_boundary = clock.boundary_for(1_700_000_000)
    fire, boundary = clock.should_fire(base_boundary)
    assert fire
    clock.mark_fired(boundary)

    fire2, _ = clock.should_fire(base_boundary + 10)  # still within same window
    assert not fire2

    fire3, boundary3 = clock.should_fire(base_boundary + THIRTY_MIN)
    assert fire3
    assert boundary3 == base_boundary + THIRTY_MIN


def test_restart_recovery_does_not_refire_same_boundary():
    base_boundary = 1_700_000_000 - (1_700_000_000 % THIRTY_MIN)
    last_tournament_ts = base_boundary + 5  # tournament already ran in this window
    clock = TournamentClock.restore(30, last_tournament_ts)
    fire, boundary = clock.should_fire(base_boundary + 200)  # still same window, engine restarted
    assert not fire


def test_restart_recovery_fires_new_boundary_once_it_arrives():
    base_boundary = 1_700_000_000 - (1_700_000_000 % THIRTY_MIN)
    last_tournament_ts = base_boundary + 5
    clock = TournamentClock.restore(30, last_tournament_ts)
    fire, boundary = clock.should_fire(base_boundary + THIRTY_MIN + 1)
    assert fire
    assert boundary == base_boundary + THIRTY_MIN


def test_restart_with_no_prior_history_fires_on_first_tick():
    clock = TournamentClock.restore(30, None)
    fire, boundary = clock.should_fire(1_700_000_000)
    assert fire


def test_does_not_catch_up_multiple_missed_boundaries_at_once():
    # Engine was down across several 30-minute windows; on restart it must
    # fire exactly one tournament for "now", not one per missed window.
    base_boundary = 1_700_000_000 - (1_700_000_000 % THIRTY_MIN)
    last_tournament_ts = base_boundary
    clock = TournamentClock.restore(30, last_tournament_ts)
    much_later = base_boundary + 5 * THIRTY_MIN
    fire, boundary = clock.should_fire(much_later)
    assert fire
    clock.mark_fired(boundary)
    fire_again, _ = clock.should_fire(much_later + 30)
    assert not fire_again
