import time
from contextlib import asynccontextmanager, contextmanager

import pytest
from cbs_fetch_helpers import JOIN_PAGE, POOL, StubSession, pool_page
from loguru import logger
from typer.testing import CliRunner

from pickem import config
from pickem.cli import app
from pickem.ingest.cbs_fetch import LOGIN_HINT, FetchedPage
from pickem.obs.log import configure_logging as _configure_logging

runner = CliRunner()


@pytest.fixture
def cbs(monkeypatch, tmp_path):
    """Point the CLI at a stub session and a scratch NAS."""
    monkeypatch.setattr(config, "CBS_POOL_URL", POOL)
    monkeypatch.setattr(config, "NAS_DIR", tmp_path)
    state = {"pages": {}, "opened": 0}

    @asynccontextmanager
    async def fake_open(*, profile, chrome):
        state["opened"] += 1
        yield StubSession(state["pages"])

    monkeypatch.setattr("pickem.cli.open_cbs_session", fake_open)
    return state


def test_fetch_board_saves_the_page(cbs, tmp_path):
    cbs["pages"][POOL] = pool_page(weeks=4, shown=4, current=4)
    out = tmp_path / "week4.html"

    result = runner.invoke(
        app, ["fetch-cbs", "--page", "board", "--pool-week", "4", "--out", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert f"saved CBS board for pool week 4 to {out}" in result.output
    assert out.exists()


def test_fetch_reports_an_expired_session_with_the_login_hint(cbs, tmp_path):
    cbs["pages"][POOL] = FetchedPage(f"{POOL}/join", JOIN_PAGE)

    result = runner.invoke(
        app, ["fetch-cbs", "--page", "board", "--pool-week", "4", "--out", str(tmp_path / "w.html")]
    )

    assert result.exit_code == 1
    assert "cbs-login.sh" in result.output


def test_the_reason_is_the_last_line_of_output_on_failure(cbs, tmp_path, monkeypatch):
    """Ruling F: logger.complete() drains the queued cbs_fetch_failed record
    before the reason is printed, so it cannot land after the reason and
    break the scheduled scripts' "forward the last stderr line" contract.

    A slow custom sink stands in for a queued write still in flight: without
    `logger.complete()`, `_run_cbs` returns (and `invoke` with it) long before
    this sink's 50ms write finishes, so `written` would still be empty right
    after `invoke` returns. `run_context` is stubbed out so its own separate
    `run_failed` record — logged, unsynced, after `_run_cbs` has already
    raised `typer.Exit` — cannot also land in `written` or `result.output`
    and mask what this test checks.
    """
    written = []

    def slow_sink(message):
        time.sleep(0.05)
        written.append(message.record["message"])

    def configure_then_add_slow_sink(*args, **kwargs):
        directory = _configure_logging(*args, **kwargs)
        logger.add(slow_sink, level="ERROR", enqueue=True)
        return directory

    @contextmanager
    def passthrough_run_context(entry, **facts):
        yield "test-run"

    monkeypatch.setattr("pickem.cli.configure_logging", configure_then_add_slow_sink)
    monkeypatch.setattr("pickem.cli.run_context", passthrough_run_context)
    cbs["pages"][POOL] = FetchedPage(f"{POOL}/join", JOIN_PAGE)

    result = runner.invoke(
        app, ["fetch-cbs", "--page", "board", "--pool-week", "4", "--out", str(tmp_path / "w.html")]
    )

    assert result.exit_code == 1
    # The slow sink already has the record: logger.complete() blocked for it.
    assert written and LOGIN_HINT in written[-1]
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines[-1].endswith(LOGIN_HINT)


def test_fetch_refuses_to_replace_a_saved_page(cbs, tmp_path):
    out = tmp_path / "week4.html"
    out.write_text("saved by hand")

    result = runner.invoke(
        app, ["fetch-cbs", "--page", "board", "--pool-week", "4", "--out", str(out)]
    )

    assert result.exit_code == 1
    assert "--force" in result.output
    assert out.read_text() == "saved by hand"


def test_fetch_reports_a_chrome_failure_after_one_retry(cbs, tmp_path):
    cbs["pages"][POOL] = [RuntimeError("chrome died"), RuntimeError("chrome died again")]

    result = runner.invoke(
        app, ["fetch-cbs", "--page", "board", "--pool-week", "4", "--out", str(tmp_path / "w.html")]
    )

    assert result.exit_code == 1
    assert "chrome died again" in result.output


def test_fetch_checks_the_nas_before_opening_chrome(cbs, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "NAS_DIR", tmp_path / "offline")

    result = runner.invoke(
        app,
        ["fetch-cbs", "--page", "board", "--pool-week", "4",
         "--out", str(tmp_path / "offline" / "weeks" / "week4.html")],
    )

    assert result.exit_code == 1
    assert "not available" in result.output
    assert cbs["opened"] == 0


def test_an_unknown_page_is_a_usage_error(cbs):
    result = runner.invoke(app, ["fetch-cbs", "--page", "scores", "--pool-week", "4"])

    assert result.exit_code == 2


def test_current_week_prints_only_the_number(cbs):
    cbs["pages"][POOL] = pool_page(weeks=5, shown=5, current=5)

    result = runner.invoke(app, ["cbs-current-week"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "5"


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send(embed, *, token, owner_id):
        calls.append((embed.title, embed.description))

    monkeypatch.setattr("pickem.cli.send_owner_dm", fake_send)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    return calls


def test_notify_owner_sends_one_dm(sent):
    result = runner.invoke(app, ["notify-owner", "--title", "Week start", "board loaded"])

    assert result.exit_code == 0, result.output
    assert sent == [("Week start", "board loaded")]


def test_notify_owner_exits_2_when_the_dm_fails(monkeypatch):
    async def broken(embed, *, token, owner_id):
        raise RuntimeError("discord down")

    monkeypatch.setattr("pickem.cli.send_owner_dm", broken)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")

    result = runner.invoke(app, ["notify-owner", "hello"])

    assert result.exit_code == 2
    assert "discord down" in result.output
