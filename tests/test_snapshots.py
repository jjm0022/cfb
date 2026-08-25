from datetime import UTC, datetime, timedelta

import pytest

from pickem.backtest.snapshots import (
    _SUBMISSION_LEAD,
    ARCHIVE_START,
    SnapshotKind,
    estimate_credits,
    plan_snapshots,
    snapshot_request_id,
)
from pickem.models import Game, Sport, make_game_id


def game(week, kickoff, away, home, season=2024, played=True):
    return Game(
        game_id=make_game_id(Sport.NFL, season, week, away, home),
        sport=Sport.NFL,
        season=season,
        week=week,
        kickoff_utc=kickoff,
        home_team_id=home,
        away_team_id=away,
        home_score=20 if played else None,
        away_score=17 if played else None,
    )


SUN_EARLY = datetime(2024, 9, 22, 17, 0, tzinfo=UTC)  # Sun 13:00 ET
SUN_LATE = datetime(2024, 9, 22, 20, 25, tzinfo=UTC)  # Sun 16:25 ET
MON_NIGHT = datetime(2024, 9, 24, 0, 15, tzinfo=UTC)  # Mon 20:15 ET


def cfb_game(kickoff: datetime, away: str, home: str) -> Game:
    return Game(
        game_id=make_game_id(Sport.CFB, 2024, 3, away, home),
        sport=Sport.CFB,
        season=2024,
        week=3,
        kickoff_utc=kickoff,
        home_team_id=home,
        away_team_id=away,
        home_score=24,
        away_score=17,
    )


def test_frozen_anchor_is_the_tuesday_before_the_first_kickoff():
    plan = plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA")])
    frozen = [r for r in plan if r.kind is SnapshotKind.FROZEN]
    assert len(frozen) == 1
    assert frozen[0].at == datetime(2024, 9, 17, 14, 0, tzinfo=UTC)
    assert frozen[0].slate == {"nfl-2024-03-BUF-at-MIA"}


def test_a_tuesday_kickoff_anchors_to_that_tuesday_morning():
    # The boundary case of "the last anchor weekday strictly before the first
    # kickoff". It does not arise in 2020-2025 — even the COVID-rescheduled
    # Tuesday games of week 15 2021 sat in a week whose first kickoff was the
    # preceding Thursday — but the rule must still be total.
    tuesday_game = datetime(2024, 9, 24, 23, 0, tzinfo=UTC)
    plan = plan_snapshots([game(3, tuesday_game, "BUF", "MIA")])
    frozen = next(r for r in plan if r.kind is SnapshotKind.FROZEN)
    assert frozen.at == datetime(2024, 9, 24, 14, 0, tzinfo=UTC)
    assert frozen.at < tuesday_game


def test_one_submission_snapshot_per_distinct_kickoff_slot():
    games = [
        game(3, SUN_EARLY, "BUF", "MIA"),
        game(3, SUN_EARLY, "NYJ", "NE"),
        game(3, SUN_LATE, "SF", "SEA"),
        game(3, MON_NIGHT, "DAL", "NYG"),
    ]
    plan = plan_snapshots(games)
    submissions = [r for r in plan if r.kind is SnapshotKind.SUBMISSION]
    # Three slots, not four games — one snapshot carries every game with odds.
    assert len(submissions) == 3
    early = next(r for r in submissions if r.at == SUN_EARLY - _SUBMISSION_LEAD)
    # A slot's slate holds only the games kicking at that slot, so a game can
    # never be graded against a snapshot taken after it started.
    assert early.slate == {"nfl-2024-03-BUF-at-MIA", "nfl-2024-03-NYJ-at-NE"}


def test_every_snapshot_precedes_the_kickoffs_it_covers():
    games = [game(3, SUN_EARLY, "BUF", "MIA"), game(3, MON_NIGHT, "DAL", "NYG")]
    kickoffs = {g.game_id: g.kickoff_utc for g in games}
    for request in plan_snapshots(games):
        for game_id in request.slate:
            assert request.at < kickoffs[game_id]


def test_window_spans_the_whole_week_for_every_request():
    games = [game(3, SUN_EARLY, "BUF", "MIA"), game(3, MON_NIGHT, "DAL", "NYG")]
    plan = plan_snapshots(games)
    # One window per week, shared: the guard is about which week an event
    # belongs to, not which slot.
    assert len({r.window for r in plan}) == 1
    start, end = plan[0].window
    assert start < SUN_EARLY
    assert end > MON_NIGHT


def test_weeks_are_planned_independently():
    plan = plan_snapshots(
        [
            game(3, SUN_EARLY, "BUF", "MIA"),
            game(4, SUN_EARLY + timedelta(days=7), "NYJ", "NE"),
        ]
    )
    assert len({(r.season, r.week) for r in plan}) == 2
    assert len({r.window for r in plan}) == 2


def test_games_before_the_archive_start_are_rejected():
    with pytest.raises(ValueError, match="archive"):
        plan_snapshots([game(1, ARCHIVE_START - timedelta(days=1), "BUF", "MIA", season=2019)])


def test_unplayed_games_are_excluded():
    # The backtest cannot grade them, so paying to snapshot them is waste.
    assert plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA", played=False)]) == []


def test_unplayed_games_do_not_drag_a_played_week_out_of_the_plan():
    games = [
        game(3, SUN_EARLY, "BUF", "MIA"),
        game(3, MON_NIGHT, "DAL", "NYG", played=False),
    ]
    plan = plan_snapshots(games)
    slates = {gid for r in plan for gid in r.slate}
    assert slates == {"nfl-2024-03-BUF-at-MIA"}


def test_credit_estimate_is_ten_per_request():
    plan = plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA"), game(3, MON_NIGHT, "DAL", "NYG")])
    # One frozen anchor plus two slots.
    assert len(plan) == 3
    assert estimate_credits(plan) == 30


def test_empty_input_costs_nothing():
    assert plan_snapshots([]) == []
    assert estimate_credits([]) == 0


def test_submission_lead_outruns_the_sources_disagreeing_about_kickoff():
    """The Odds API and nflverse disagree about when a game starts.

    Measured against a real archived snapshot, commence_time ran from 5 minutes
    before to 2 minutes after the nflverse kickoff. The lead must clear that,
    or the returned snapshot can carry in-play odds into a proxy that is meant
    to predate the game entirely.
    """
    worst_disagreement = timedelta(minutes=5)
    plan = plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA")])
    submission = next(r for r in plan if r.kind is SnapshotKind.SUBMISSION)
    assert submission.at < SUN_EARLY - worst_disagreement


def test_default_planner_keeps_distinct_nfl_slots_separate():
    submissions = [
        r for r in plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA"), game(3, SUN_EARLY + timedelta(minutes=30), "NYJ", "NE")])
        if r.kind is SnapshotKind.SUBMISSION
    ]
    assert len(submissions) == 2


def test_ninety_minute_mode_batches_a_seventy_five_minute_kickoff_span():
    games = [cfb_game(SUN_EARLY, "CLEM", "UGA"), cfb_game(SUN_EARLY + timedelta(minutes=75), "BAMA", "LSU")]
    submissions = [r for r in plan_snapshots(games, max_submission_age=timedelta(minutes=90)) if r.kind is SnapshotKind.SUBMISSION]
    assert len(submissions) == 1
    assert submissions[0].slate == {g.game_id for g in games}


def test_ninety_minute_mode_splits_a_seventy_six_minute_kickoff_span():
    games = [cfb_game(SUN_EARLY, "CLEM", "UGA"), cfb_game(SUN_EARLY + timedelta(minutes=76), "BAMA", "LSU")]
    assert len([r for r in plan_snapshots(games, max_submission_age=timedelta(minutes=90)) if r.kind is SnapshotKind.SUBMISSION]) == 2


def test_every_batched_game_has_exactly_one_submission_request_with_valid_age():
    games = [cfb_game(SUN_EARLY + timedelta(minutes=n), f"A{n}", f"H{n}") for n in (0, 30, 75, 76, 150)]
    submissions = [r for r in plan_snapshots(games, max_submission_age=timedelta(minutes=90)) if r.kind is SnapshotKind.SUBMISSION]
    kickoff = {g.game_id: g.kickoff_utc for g in games}
    assigned = [gid for request in submissions for gid in request.slate]
    assert sorted(assigned) == sorted(kickoff)
    for request in submissions:
        for gid in request.slate:
            assert timedelta(minutes=15) <= kickoff[gid] - request.at <= timedelta(minutes=90)


def test_max_submission_age_cannot_be_shorter_than_the_safety_lead():
    with pytest.raises(ValueError, match="15 minutes"):
        plan_snapshots([cfb_game(SUN_EARLY, "CLEM", "UGA")], max_submission_age=timedelta(minutes=14))


def test_snapshot_request_id_is_deterministic_and_sensitive_to_fields():
    request = next(r for r in plan_snapshots([cfb_game(SUN_EARLY, "CLEM", "UGA")]) if r.kind is SnapshotKind.SUBMISSION)
    assert snapshot_request_id(Sport.CFB, request) == snapshot_request_id(Sport.CFB, request.model_copy(update={"slate": frozenset(reversed(tuple(request.slate)))}))
    for field, value in (("kind", SnapshotKind.FROZEN), ("at", request.at + timedelta(minutes=1)), ("slate", frozenset({"different"}))):
        assert snapshot_request_id(Sport.CFB, request) != snapshot_request_id(Sport.CFB, request.model_copy(update={field: value}))
    assert snapshot_request_id(Sport.CFB, request) != snapshot_request_id(Sport.NFL, request)
