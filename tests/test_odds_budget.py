from datetime import UTC, datetime, timedelta

import pytest

from pickem.models import OddsPull, PollTrigger, Sport
from pickem.operations.odds_budget import OddsBudgetSkipped, check_budget
from pickem.store.db import Store

NOW = datetime(2026, 10, 22, 23, 15, tzinfo=UTC)
MONTH_AGO = NOW - timedelta(days=30)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "budget.duckdb") as opened:
        opened.init_schema()
        yield opened


def _pulled(store, games: int, *, days_ago: float = 1.0, skipped: bool = False) -> None:
    store.record_odds_pull(
        OddsPull(
            pulled_at=NOW - timedelta(days=days_ago),
            sport=Sport.NFL,
            season=2026,
            week=7,
            trigger=PollTrigger.MIDWEEK,
            games_requested=games,
            games_returned=0 if skipped else games,
            skipped=skipped,
        )
    )


def _check(store, games: int, trigger=PollTrigger.MIDWEEK, offset=None) -> int:
    return check_budget(
        store,
        now=NOW,
        sport=Sport.NFL,
        season=2026,
        week=7,
        trigger=trigger,
        offset_hours=offset,
        games_requested=games,
    )


def test_a_pull_that_fits_returns_the_games_already_used(store):
    _pulled(store, 700)

    assert _check(store, 100) == 700  # exactly 800 is allowed


def test_a_pull_that_would_pass_the_share_is_skipped_and_recorded(store):
    _pulled(store, 700)

    with pytest.raises(OddsBudgetSkipped) as skip:
        _check(store, 101)

    assert skip.value.notify is True
    assert "700 of its 800" in str(skip.value)
    assert store.odds_skips_since(MONTH_AGO) == 1
    assert store.odds_games_used(MONTH_AGO) == 700


def test_the_two_hour_and_one_hour_kickoff_pulls_always_run(store):
    _pulled(store, 800)

    assert _check(store, 14, PollTrigger.KICKOFF, 2.0) == 800
    assert _check(store, 14, PollTrigger.KICKOFF, 1.0) == 800


def test_earlier_kickoff_pulls_are_not_protected(store):
    _pulled(store, 800)

    with pytest.raises(OddsBudgetSkipped):
        _check(store, 14, PollTrigger.KICKOFF, 6.0)


def test_the_owner_is_told_once_per_thirty_days(store):
    _pulled(store, 800)
    _pulled(store, 30, days_ago=3, skipped=True)

    with pytest.raises(OddsBudgetSkipped) as skip:
        _check(store, 5)

    assert skip.value.notify is False


def test_pulls_older_than_thirty_days_no_longer_count(store):
    _pulled(store, 790, days_ago=31)

    assert _check(store, 30) == 0


def test_a_skip_is_logged(store, records):
    _pulled(store, 800)

    with pytest.raises(OddsBudgetSkipped):
        _check(store, 5)

    assert any(r["extra"].get("event") == "odds_budget_skipped" for r in records)
