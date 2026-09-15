import json
import shutil

import pytest
from results_helpers import FIXTURE, seed
from typer.testing import CliRunner

from pickem.cli import app
from pickem.store.db import Store

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path):
    db = tmp_path / "pickem.duckdb"
    with Store(db) as store:
        store.init_schema()
        seed(store)
    results = tmp_path / "results"
    results.mkdir()
    shutil.copy(FIXTURE, results / "week2.html")
    return db, results


def invoke_import(db, results, *extra):
    return runner.invoke(app, [
        "import-results", "--season", "2026", "--pool-week", "2", "--db", str(db),
        "--file", str(results / "week2.html"), "--out-dir", str(results), *extra,
    ])


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send(embed, *, token, owner_id):
        calls.append((embed.title, token, owner_id))

    monkeypatch.setattr("pickem.cli.send_owner_dm", fake_send)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    return calls


def test_import_results_stores_the_week_and_writes_the_report(workspace, sent):
    db, results = workspace
    result = invoke_import(db, results, "--no-notify")
    assert result.exit_code == 0, result.output
    assert "imported pool week 2: 4 entrants" in result.output
    report = results / "week2-report.md"
    assert "## Headline" in report.read_text()
    assert sent == []
    with Store(db) as store:
        assert store.pool_weeks(2026) == [2]


def test_import_results_sends_the_dm_by_default(workspace, sent):
    db, results = workspace
    result = invoke_import(db, results)
    assert result.exit_code == 0, result.output
    assert sent == [("🏈 Pool week 2 results", "test-token", 123)]
    assert "Discord DM sent" in result.output


def test_dm_failure_exits_2_and_keeps_the_report(workspace, sent, monkeypatch):
    db, results = workspace
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    result = invoke_import(db, results)
    assert result.exit_code == 2, result.output
    assert "the Discord DM failed" in result.output
    assert (results / "week2-report.md").exists()


def test_unparseable_page_exits_1(workspace):
    db, results = workspace
    (results / "week2.html").write_text("<html><body>Lobby</body></html>")
    result = invoke_import(db, results, "--no-notify")
    assert result.exit_code == 1
    assert "no Weekly Standings table" in result.output


def test_page_that_does_not_link_exits_1_without_writing(tmp_path):
    db = tmp_path / "empty.duckdb"
    results = tmp_path / "results"
    results.mkdir()
    shutil.copy(FIXTURE, results / "week2.html")
    result = invoke_import(db, results, "--no-notify")
    assert result.exit_code == 1
    assert "was not imported" in result.output
    assert not (results / "week2-report.md").exists()


def test_results_report_rebuilds_the_latest_week_without_a_dm(workspace, sent):
    db, results = workspace
    assert invoke_import(db, results, "--no-notify").exit_code == 0
    (results / "week2-report.md").unlink()
    result = runner.invoke(app, [
        "results-report", "--season", "2026", "--db", str(db), "--out-dir", str(results),
    ])
    assert result.exit_code == 0, result.output
    assert (results / "week2-report.md").exists()
    assert sent == []


def test_results_report_with_nothing_imported_exits_1(workspace):
    db, results = workspace
    result = runner.invoke(app, ["results-report", "--season", "2026", "--db", str(db)])
    assert result.exit_code == 1
    assert "run import-results first" in result.output


def test_backfill_recommendations_reports_what_it_added(workspace, tmp_path):
    db, _ = workspace
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "pickem.jsonl").write_text(json.dumps({
        "ts": "2026-09-12T10:00:00-04:00", "event": "edge_decided",
        "game_id": "cfb-2026-02-PSU-at-TEM", "side": "away", "tier": "coinflip", "delta": 0.5,
    }) + "\n")
    result = runner.invoke(app, [
        "backfill-recommendations", "--season", "2026", "--db", str(db), "--logs", str(logs),
    ])
    assert result.exit_code == 0, result.output
    assert "0 history rows from report batches and 1 from logs" in result.output
