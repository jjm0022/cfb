from contextlib import asynccontextmanager

import pytest
from cbs_fetch_helpers import JOIN_PAGE, POOL, StubSession, pool_page
from typer.testing import CliRunner

from pickem import config
from pickem.cli import app
from pickem.ingest.cbs_fetch import FetchedPage

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
