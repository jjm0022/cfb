from datetime import UTC, datetime, timedelta

import pytest

from pickem.backtest.snapshots import (
    ARCHIVE_START,
    SnapshotKind,
    estimate_credits,
    plan_snapshots,
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
    early = next(r for r in submissions if r.at == SUN_EARLY - timedelta(minutes=5))
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
