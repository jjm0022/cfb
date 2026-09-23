import json
import shutil
from pathlib import Path

import pytest
from loguru import logger
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


def test_an_offline_nas_is_named_as_the_cause_and_writes_nothing(workspace, monkeypatch, tmp_path):
    db, _ = workspace
    # A results dir on a share that is not mounted: the parent does not exist.
    mount = tmp_path / "mnt" / "nas"
    nas_dir = mount / "Betting" / "pickem"
    monkeypatch.setattr("pickem.config.NAS_MOUNT", mount)
    monkeypatch.setattr("pickem.config.NAS_DIR", nas_dir)

    result = runner.invoke(app, [
        "import-results", "--season", "2026", "--pool-week", "2", "--db", str(db),
        "--out-dir", str(nas_dir / "results"), "--no-notify",
    ])

    assert result.exit_code == 1
    assert "not available" in result.output
    assert str(mount) in result.output
    # The guard must run before mkdir -p builds a tree shadowing the real share.
    assert not nas_dir.exists()


def test_non_utf8_page_exits_1_without_writing(workspace):
    db, results = workspace
    (results / "week2.html").write_bytes(b"\xff\xfe\x00bad")
    result = invoke_import(db, results, "--no-notify")
    assert result.exit_code == 1
    assert "week2.html" in result.output
    assert "not UTF-8" in result.output
    assert not (results / "week2-report.md").exists()


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


@pytest.fixture
def embeds(monkeypatch):
    captured = []

    async def fake_send(embed, *, token, owner_id):
        captured.append(embed)

    monkeypatch.setattr("pickem.cli.send_owner_dm", fake_send)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    return captured


def test_import_writes_the_week_page_and_the_index(workspace, tmp_path):
    db, results = workspace
    dash = tmp_path / "dash"
    result = invoke_import(db, results, "--no-notify", "--dashboard-dir", str(dash))
    assert result.exit_code == 0, result.output
    page = (dash / "week-2.html").read_text()
    assert page.startswith("<!doctype html>")
    assert (dash / "index.html").read_text() == page
    assert not list(dash.glob(".*.tmp"))
    assert "dashboard written to" in result.output


def test_a_failed_atomic_write_leaves_no_temp_file(workspace, tmp_path, monkeypatch):
    db, results = workspace
    dash = tmp_path / "dash"

    def boom(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    result = invoke_import(db, results, "--no-notify", "--dashboard-dir", str(dash))
    assert result.exit_code == 3, result.output
    assert "dashboard not written" in result.output
    assert not list(dash.glob(".*.tmp"))


def test_dashboard_defaults_to_the_configured_directory(workspace, tmp_path):
    db, results = workspace
    assert invoke_import(db, results, "--no-notify").exit_code == 0
    assert (tmp_path / "dashboard" / "week-2.html").exists()  # conftest's PICKEM_DASHBOARD_DIR


def test_rebuilding_an_older_week_leaves_the_index_alone(workspace, tmp_path, monkeypatch):
    db, results = workspace
    dash = tmp_path / "dash"
    assert invoke_import(db, results, "--no-notify", "--dashboard-dir", str(dash)).exit_code == 0
    (dash / "index.html").write_text("latest week")
    monkeypatch.setattr(Store, "pool_weeks", lambda self, season: [2, 3])
    result = runner.invoke(app, [
        "results-report", "--season", "2026", "--pool-week", "2", "--db", str(db),
        "--out-dir", str(results), "--dashboard-dir", str(dash),
    ])
    assert result.exit_code == 0, result.output
    assert (dash / "index.html").read_text() == "latest week"
    assert 'href="week-3.html"' in (dash / "week-2.html").read_text()


def test_dm_links_the_dashboard_when_the_url_is_set(workspace, embeds, monkeypatch, tmp_path):
    db, results = workspace
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem")
    assert invoke_import(db, results).exit_code == 0
    fields = {field.name: field.value for field in embeds[0].fields}
    assert fields["Dashboard"] == "https://sandbox.tail750bff.ts.net/pickem/week-2.html"


def test_dm_has_no_link_when_the_url_is_unset(workspace, embeds):
    db, results = workspace
    assert invoke_import(db, results).exit_code == 0
    assert "Dashboard" not in [field.name for field in embeds[0].fields]


def test_a_render_failure_keeps_the_report_sends_the_dm_and_exits_3(
    workspace, embeds, monkeypatch
):
    db, results = workspace
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem/")

    def boom(*args, **kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr("pickem.cli.render_results_dashboard", boom)
    result = invoke_import(db, results)
    assert result.exit_code == 3, result.output
    assert "dashboard not written: render broke" in result.output
    assert (results / "week2-report.md").exists()
    assert len(embeds) == 1
    assert "Dashboard" not in [field.name for field in embeds[0].fields]
    with Store(db) as store:
        assert store.pool_weeks(2026) == [2]


def test_a_dm_failure_takes_precedence_over_a_dashboard_failure(
    workspace, sent, monkeypatch
):
    db, results = workspace
    monkeypatch.delenv("DISCORD_BOT_TOKEN")

    def boom(*args, **kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr("pickem.cli.render_results_dashboard", boom)
    result = invoke_import(db, results)
    assert result.exit_code == 2, result.output
    assert "the Discord DM failed" in result.output
    assert "dashboard not written" in result.output
    assert (results / "week2-report.md").exists()


def test_an_unwritable_dashboard_dir_exits_3_and_keeps_the_report(workspace, tmp_path):
    db, results = workspace
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file where the directory should be")
    result = invoke_import(db, results, "--no-notify", "--dashboard-dir", str(blocker))
    assert result.exit_code == 3, result.output
    assert "dashboard not written" in result.output
    assert (results / "week2-report.md").exists()


def test_dashboard_write_is_logged(workspace, tmp_path):
    db, results = workspace
    assert invoke_import(db, results, "--no-notify").exit_code == 0
    logger.complete()
    rows = [
        json.loads(line)
        for line in (tmp_path / "pickem-logs" / "pickem.jsonl").read_text().splitlines()
        if line.strip()
    ]
    written = [row for row in rows if row["event"] == "dashboard_written"]
    assert len(written) == 1
    assert written[0]["pool_week"] == 2
    assert written[0]["index_updated"] is True
    assert not any(row["event"] == "dashboard_write_failed" for row in rows)
