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
