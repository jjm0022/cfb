from datetime import UTC, datetime, timedelta

from results_helpers import imported_store, seed
from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import Sport
from pickem.operations.pool_weeks import (
    PoolWeekStatus,
    pending_results_week,
    pool_week_status,
)
from pickem.store.db import Store

NOW = datetime(2026, 9, 22, 13, tzinfo=UTC)  # Tuesday 09:00 America/New_York
runner = CliRunner()


def _store(rows=()):
    store = Store(":memory:")
    store.init_schema()
    if rows:
        seed(store, rows)
    return store


def test_a_week_with_no_lines_is_new():
    assert pool_week_status(_store(), 2026, 3) is PoolWeekStatus.NEW


def test_one_league_stored_is_partial():
    store = _store([(Sport.CFB, 3, "OU", "MICH", 5.5, None, None)])
    assert pool_week_status(store, 2026, 3) is PoolWeekStatus.PARTIAL


def test_both_leagues_stored_is_started():
    store = _store([
        (Sport.CFB, 3, "OU", "MICH", 5.5, None, None),
        (Sport.NFL, 2, "NE", "SEA", -3.5, None, None),
    ])
    assert pool_week_status(store, 2026, 3) is PoolWeekStatus.STARTED


def test_pool_week_one_needs_only_cfb():
    store = _store([(Sport.CFB, 1, "OU", "MICH", 5.5, None, None)])
    assert pool_week_status(store, 2026, 1) is PoolWeekStatus.STARTED


def test_an_imported_week_is_finished():
    assert pool_week_status(imported_store(), 2026, 2) is PoolWeekStatus.FINISHED


def test_the_week_that_ended_last_night_is_pending():
    kickoffs = {4: NOW - timedelta(hours=13)}  # Monday 20:00 ET kickoff
    assert pending_results_week(kickoffs, [1, 2, 3], NOW) == 4


def test_unstarted_week_is_not_pending():
    # Wednesday retry: week 4 already imported, week 5's board loaded Tuesday evening.
    wednesday = NOW + timedelta(days=1)
    kickoffs = {4: NOW - timedelta(hours=13), 5: wednesday + timedelta(days=1)}
    assert pending_results_week(kickoffs, [1, 2, 3, 4], wednesday) is None


def test_a_game_still_in_progress_keeps_the_week_pending_later():
    kickoffs = {4: NOW - timedelta(hours=1)}
    assert pending_results_week(kickoffs, [1, 2, 3], NOW) is None


def test_the_earliest_unimported_finished_week_comes_first():
    kickoffs = {3: NOW - timedelta(days=7), 4: NOW - timedelta(hours=13)}
    assert pending_results_week(kickoffs, [1, 2], NOW) == 3


def test_nothing_pending_when_everything_is_imported():
    assert pending_results_week({2: NOW - timedelta(days=7)}, [2], NOW) is None


def test_last_kickoffs_group_nfl_lines_into_the_following_pool_week():
    early, late = datetime(2026, 9, 19, 16, tzinfo=UTC), datetime(2026, 9, 22, 0, tzinfo=UTC)
    store = _store()
    seed(store, [(Sport.CFB, 4, "OU", "MICH", 5.5, None, None)])
    seed(store, [(Sport.NFL, 3, "NE", "SEA", -3.5, None, None)])
    store._con.execute(
        "UPDATE games SET kickoff_utc = CASE WHEN sport = 'nfl' THEN ? ELSE ? END", [late, early]
    )

    assert store.pool_week_last_kickoffs(2026) == {4: late}


def test_status_command_prints_the_status(tmp_path):
    db = tmp_path / "pickem.duckdb"
    with Store(db) as store:
        store.init_schema()
        seed(store, [(Sport.CFB, 1, "OU", "MICH", 5.5, None, None)])

    result = runner.invoke(
        app, ["pool-week-status", "--season", "2026", "--pool-week", "1", "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "started"


def test_pending_command_prints_nothing_when_nothing_is_waiting(tmp_path):
    db = tmp_path / "pickem.duckdb"
    with Store(db) as store:
        store.init_schema()

    result = runner.invoke(app, ["pending-results-week", "--season", "2026", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""
