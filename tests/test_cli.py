from typer.testing import CliRunner

from pickem.cli import app

runner = CliRunner()


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in [
        "ingest-cbs",
        "poll-odds",
        "report",
        "sync-results",
        "backfill",
        "backtest",
    ]:
        assert command in result.stdout


def test_ingest_cbs_reports_a_parse_summary(tmp_path):
    paste = tmp_path / "week3.txt"
    paste.write_text("Buffalo Bills at Miami Dolphins -3.0\n")
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest-cbs",
            "--file",
            str(paste),
            "--sport",
            "nfl",
            "--season",
            "2025",
            "--week",
            "3",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0
    assert "1" in result.stdout


def test_ingest_cbs_exits_nonzero_on_an_unknown_team(tmp_path):
    paste = tmp_path / "bad.txt"
    paste.write_text("Fictional State Aardvarks at Miami Dolphins -3.0\n")
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest-cbs",
            "--file",
            str(paste),
            "--sport",
            "nfl",
            "--season",
            "2025",
            "--week",
            "3",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code != 0


def test_ingest_cbs_rejects_a_partially_parsed_block_without_creating_a_database(tmp_path):
    paste = tmp_path / "mixed.txt"
    paste.write_text(
        "Buffalo Bills at Miami Dolphins -3.0\nKansas City Chiefs -6.5 at New York Jets 45.5\n"
    )
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest-cbs",
            "--file",
            str(paste),
            "--sport",
            "nfl",
            "--season",
            "2025",
            "--week",
            "3",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code != 0
    assert "skipped" in result.stdout
    assert "Kansas City Chiefs -6.5 at New York Jets 45.5" in result.stdout
    assert not db.exists()


def _seed(db):
    """A single completed 2025 week-3 game with a frozen line and one closer."""
    from datetime import UTC, datetime

    from pickem.models import Game, LeagueLine, MarketLine, Sport
    from pickem.store.db import Store

    gid = "nfl-2025-03-BUF-at-MIA"
    kick = datetime(2025, 9, 21, 17, 0, tzinfo=UTC)
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(
            [
                Game(
                    game_id=gid,
                    sport=Sport.NFL,
                    season=2025,
                    week=3,
                    kickoff_utc=kick,
                    home_team_id="MIA",
                    away_team_id="BUF",
                    home_score=24,
                    away_score=17,
                )
            ]
        )
        store.upsert_league_lines(
            [LeagueLine(game_id=gid, season=2025, week=3, spread_home=-3.0, posted_at=kick)]
        )
        store.append_market_lines(
            [
                MarketLine(
                    game_id=gid,
                    source="oddsapi",
                    book="pinnacle",
                    spread_home=-6.0,
                    captured_at=kick,
                )
            ]
        )
    return gid


def test_report_shows_provenance_and_records_its_picks(tmp_path):
    from pickem.store.db import Store

    db = tmp_path / "test.duckdb"
    _seed(db)
    result = runner.invoke(
        app,
        ["report", "--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)],
    )
    assert result.exit_code == 0, result.output
    assert "CBS frozen league lines" in result.stdout

    with Store(db) as store:
        picks = store.picks_for_week(2025, 3)
    assert len(picks) == 1
    assert picks[0][2] == "nfl-2025-03-BUF-at-MIA"
    assert picks[0][5] == "strong"


def test_backtest_explains_why_nothing_was_graded(tmp_path):
    # Before `backfill-history` there are no frozen-line snapshots at all, so
    # every game is excluded. A bare 0-0-0 with no reason is the silent
    # degradation the project forbids.
    db = tmp_path / "test.duckdb"
    _seed(db)
    result = runner.invoke(app, ["backtest", "--from", "2025", "--to", "2025", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "0-0-0" in result.stdout
    assert "games not graded" in result.stdout
    assert "missing frozen-line snapshot" in result.stdout
    # The stored in-season oddsapi snapshot is neither proxy and must be named,
    # not silently graded as a submission-time line.
    assert "neither the frozen-line nor the submission-time proxy" in result.stdout


def test_poll_odds_refuses_to_run_before_the_week_is_ingested(tmp_path):
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        ["poll-odds", "--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)],
    )
    assert result.exit_code == 1
    assert "run ingest-cbs first" in result.output
