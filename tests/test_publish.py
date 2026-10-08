"""The one routine every job uses to write the dashboard page."""

import json
from datetime import UTC, datetime, timedelta

from dashboard_helpers import data_block
from loguru import logger
from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import Game, LeagueLine, Sport
from pickem.report.publish import TRIGGER_BOT, publish_dashboard
from pickem.store.db import Store

NOW = datetime(2026, 10, 8, 14, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n"


def seed_board(store: Store) -> None:
    game = Game(game_id="cfb-2026-06-OU-at-MICH", sport=Sport.CFB, season=2026, week=6,
                kickoff_utc=NOW + timedelta(days=2), home_team_id="MICH", away_team_id="OU")
    store.upsert_games([game])
    store.upsert_league_lines([LeagueLine(game_id=game.game_id, season=2026, week=6,
                                          spread_home=-3.0, posted_at=NOW)])


def open_store(tmp_path) -> Store:
    store = Store(tmp_path / "pickem.duckdb")
    store.init_schema()
    return store


def test_a_board_with_no_imported_results_still_publishes(tmp_path):
    dash = tmp_path / "dash"
    (dash / "logos" / "cfb").mkdir(parents=True)
    (dash / "logos" / "cfb" / "OU.png").write_bytes(PNG)
    with open_store(tmp_path) as store:
        seed_board(store)
        published = publish_dashboard(store, dash, trigger=TRIGGER_BOT, now=NOW)
    assert published.path == dash / "index.html" and published.error is None
    data = data_block(published.path.read_text())
    assert data["season"] == 2026
    assert data["standings"] == []
    assert data["this_week"]["pool_week"] == 6
    assert data["logos"]["light"]["cfb"] == ["OU"]
    assert not list(dash.glob("week-*.html"))


def test_nothing_stored_writes_nothing(tmp_path, records):
    dash = tmp_path / "dash"
    with open_store(tmp_path) as store:
        published = publish_dashboard(store, dash, trigger=TRIGGER_BOT, now=NOW)
    assert published.path is None and published.error is None
    assert not dash.exists()
    skipped = [r for r in records if r["extra"].get("event") == "dashboard_skipped"]
    assert [r["extra"]["trigger"] for r in skipped] == ["bot"]


def test_a_failure_is_logged_with_its_trigger_and_never_raised(tmp_path, records, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr("pickem.report.publish.render_dashboard", boom)
    dash = tmp_path / "dash"
    with open_store(tmp_path) as store:
        seed_board(store)
        published = publish_dashboard(store, dash, trigger=TRIGGER_BOT, now=NOW)
    assert published.path is None and published.error == "render broke"
    failed = [r for r in records if r["extra"].get("event") == "dashboard_write_failed"]
    assert len(failed) == 1
    assert failed[0]["extra"]["trigger"] == "bot"
    assert failed[0]["extra"]["error_detail"] == "render broke"


def test_a_successful_write_is_logged_with_its_trigger(tmp_path, records):
    with open_store(tmp_path) as store:
        seed_board(store)
        publish_dashboard(store, tmp_path / "dash", trigger=TRIGGER_BOT, now=NOW)
    [written] = [r for r in records if r["extra"].get("event") == "dashboard_written"]
    assert written["extra"]["trigger"] == "bot"
    assert written["extra"]["this_week"] == 6


def test_publish_dashboard_command(tmp_path):
    db, dash = tmp_path / "pickem.duckdb", tmp_path / "dash"
    with open_store(tmp_path) as store:
        seed_board(store)
    result = CliRunner().invoke(app, ["publish-dashboard", "--trigger", "week-start",
                                      "--db", str(db), "--dashboard-dir", str(dash)])
    assert result.exit_code == 0, result.output
    assert (dash / "index.html").exists()
    logger.complete()
    rows = [json.loads(line) for line in
            (tmp_path / "pickem-logs" / "pickem.jsonl").read_text().splitlines() if line.strip()]
    assert any(r["event"] == "dashboard_written" and r["trigger"] == "week-start" for r in rows)


def test_publish_dashboard_command_exits_3_when_not_written(tmp_path, monkeypatch):
    db = tmp_path / "pickem.duckdb"
    with open_store(tmp_path) as store:
        seed_board(store)

    def boom(*args, **kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr("pickem.report.publish.render_dashboard", boom)
    result = CliRunner().invoke(app, ["publish-dashboard", "--db", str(db),
                                      "--dashboard-dir", str(tmp_path / "dash")])
    assert result.exit_code == 3
    assert "dashboard not written: render broke" in result.output
