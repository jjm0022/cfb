"""The bulk CFB results load that trains the Elo tiebreak.

Offline: the CFBD fetcher is injected, so no test touches the network.
"""

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from pickem import cli
from pickem.models import Sport
from pickem.store.db import Store

runner = CliRunner()


def _row(away, home, season, week, home_points=None, away_points=None):
    return {
        "home_team": home,
        "away_team": away,
        "start_date": f"{season}-09-{week + 5:02d}T19:30:00.000Z",
        "home_points": home_points,
        "away_points": away_points,
    }


@pytest.fixture
def fake_cfbd(monkeypatch):
    """One completed game per season/week, plus one unplayed game in week 2."""
    calls: list[tuple[int, int]] = []

    def fetcher(season: int, week: int) -> list[dict]:
        calls.append((season, week))
        rows = [_row("Clemson", "LSU", season, week, home_points=31, away_points=17)]
        if week == 2:
            rows.append(_row("Duke", "Tulane", season, week))
        return rows

    monkeypatch.setattr(cli, "default_games_fetcher", lambda _config: fetcher)
    monkeypatch.setattr(cli.CfbdConfig, "from_env", classmethod(lambda cls: None))
    return calls


def test_it_loads_every_week_of_every_season_in_the_range(fake_cfbd, tmp_path):
    """Catches an off-by-one that silently drops a season or the last week."""
    db = tmp_path / "cfb.duckdb"
    result = runner.invoke(
        cli.app,
        ["backfill-cfb", "--from", "2021", "--to", "2023", "--weeks", "4", "--db", str(db)],
    )

    assert result.exit_code == 0, result.output
    assert fake_cfbd == [(s, w) for s in (2021, 2022, 2023) for w in (1, 2, 3, 4)]


def test_final_scores_land_so_the_rating_has_history(fake_cfbd, tmp_path):
    """Catches a load that stores games without their results.

    A game row with no score contributes nothing to `build_ratings`, so a
    backfill that dropped scores would look successful and leave the tiebreak
    exactly as untrained as before.
    """
    db = tmp_path / "cfb.duckdb"
    runner.invoke(
        cli.app, ["backfill-cfb", "--from", "2021", "--to", "2021", "--weeks", "1", "--db", str(db)]
    )

    with Store(db) as store:
        games = store.load_week(Sport.CFB, 2021, 1).games

    assert len(games) == 1
    assert (games[0].home_score, games[0].away_score) == (31, 17)
    assert games[0].kickoff_utc == datetime(2021, 9, 6, 19, 30, tzinfo=UTC)


def test_an_unplayed_game_is_reported_rather_than_counted_as_loaded(fake_cfbd, tmp_path):
    """Catches a summary that counts rows instead of results.

    Cancelled and postponed games come back with null scores. They are still
    stored — they are real fixtures — but reporting them as loaded results
    would overstate how much history the rating actually has.
    """
    db = tmp_path / "cfb.duckdb"
    result = runner.invoke(
        cli.app, ["backfill-cfb", "--from", "2021", "--to", "2021", "--weeks", "2", "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert "3 games" in result.output
    assert "2 with final scores" in result.output
