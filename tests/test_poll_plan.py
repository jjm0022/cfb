from datetime import UTC, datetime, timedelta

from pickem.automation.poll_plan import PollInstant, plan_polls

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
OFFSETS = (12.0, 6.0, 2.0, 1.0)


def at(*args: int) -> datetime:
    return datetime(2026, 9, *args, tzinfo=UTC)


def test_plan_emits_one_instant_per_offset_for_each_distinct_kickoff():
    kickoff = at(13, 17)

    plan = plan_polls([kickoff], offsets_hours=OFFSETS, now=NOW)

    assert plan == (
        PollInstant(at=at(13, 5), offset_hours=12.0, kickoff_utc=kickoff),
        PollInstant(at=at(13, 11), offset_hours=6.0, kickoff_utc=kickoff),
        PollInstant(at=at(13, 15), offset_hours=2.0, kickoff_utc=kickoff),
        PollInstant(at=at(13, 16), offset_hours=1.0, kickoff_utc=kickoff),
    )


def test_plan_collapses_games_that_share_a_kickoff_into_one_set_of_polls():
    kickoff = at(13, 17)

    plan = plan_polls([kickoff, kickoff, kickoff], offsets_hours=OFFSETS, now=NOW)

    assert len(plan) == len(OFFSETS)


def test_plan_drops_instants_that_have_already_passed():
    # 12h and 6h before this kickoff are behind NOW; 2h and 1h are still ahead.
    kickoff = at(10, 15)

    plan = plan_polls([kickoff], offsets_hours=OFFSETS, now=NOW)

    assert [instant.offset_hours for instant in plan] == [2.0, 1.0]


def test_plan_drops_every_instant_for_a_kickoff_that_has_already_started():
    plan = plan_polls([at(10, 9)], offsets_hours=OFFSETS, now=NOW)

    assert plan == ()


def test_plan_deduplicates_an_instant_two_kickoff_slots_both_ask_for():
    # 12h before the 13:00 kickoff and 1h before the 02:00 kickoff are both 01:00.
    afternoon, overnight = at(14, 13), at(14, 2)

    plan = plan_polls([afternoon, overnight], offsets_hours=(12.0, 1.0), now=NOW)

    assert [instant.at for instant in plan] == [at(13, 14), at(14, 1), at(14, 12)]


def test_plan_keeps_the_most_urgent_reason_for_a_shared_instant():
    afternoon, overnight = at(14, 13), at(14, 2)

    plan = plan_polls([afternoon, overnight], offsets_hours=(12.0, 1.0), now=NOW)
    shared = next(instant for instant in plan if instant.at == at(14, 1))

    assert shared.offset_hours == 1.0
    assert shared.kickoff_utc == overnight


def test_plan_ignores_kickoffs_beyond_the_horizon():
    near, far = at(13, 17), at(30, 17)

    plan = plan_polls([near, far], offsets_hours=OFFSETS, now=NOW, horizon=timedelta(days=10))

    assert {instant.kickoff_utc for instant in plan} == {near}


def test_plan_returns_nothing_for_an_empty_slate():
    assert plan_polls([], offsets_hours=OFFSETS, now=NOW) == ()


def test_plan_returns_nothing_when_no_offsets_are_configured():
    assert plan_polls([at(13, 17)], offsets_hours=(), now=NOW) == ()


def test_plan_is_ordered_by_poll_time():
    plan = plan_polls([at(13, 17), at(12, 20), at(14, 1)], offsets_hours=OFFSETS, now=NOW)

    assert [instant.at for instant in plan] == sorted(instant.at for instant in plan)


def test_plan_accepts_fractional_offsets():
    kickoff = at(13, 17)

    plan = plan_polls([kickoff], offsets_hours=(0.5,), now=NOW)

    assert plan == (
        PollInstant(
            at=datetime(2026, 9, 13, 16, 30, tzinfo=UTC),
            offset_hours=0.5,
            kickoff_utc=kickoff,
        ),
    )
