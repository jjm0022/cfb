from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-week.sh"


def _run(tmp_path: Path, *args: str, fail_on: str = "") -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the script against a fake ``uv`` that logs each call.

    ``fail_on`` makes the fake exit 1 for any call whose arguments contain it,
    e.g. ``"ingest-cbs --html --file"`` or ``"poll-odds --sport nfl"``.
    """
    command_log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$START_WEEK_COMMAND_LOG"\n'
        'if [[ -n "$START_WEEK_FAIL_ON" && "$*" == *"$START_WEEK_FAIL_ON"* ]]; then exit 1; fi\n'
    )
    fake_uv.chmod(0o755)
    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "START_WEEK_COMMAND_LOG": str(command_log),
        "START_WEEK_FAIL_ON": fail_on,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    commands = command_log.read_text().splitlines() if command_log.exists() else []
    return result, commands


def _page(tmp_path: Path) -> Path:
    page = tmp_path / "week3.html"
    page.write_text("<html></html>")
    return page


def _ingest(page: Path, sport: str, week: int, db: Path) -> str:
    return f"run pickem ingest-cbs --html --file {page} --sport {sport} --season 2026 --week {week} --db {db}"


def _poll(sport: str, week: int, db: Path, days: int = 11) -> str:
    return f"run pickem poll-odds --sport {sport} --season 2026 --week {week} --days {days} --db {db}"


def test_a_pool_week_ingests_then_polls_cfb_that_week_and_nfl_the_week_before(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(tmp_path, "3", "--season", "2026", "--file", str(page), "--db", str(db))

    assert result.returncode == 0, result.stderr
    assert commands == [
        _ingest(page, "cfb", 3, db),
        _poll("cfb", 3, db),
        _ingest(page, "nfl", 2, db),
        _poll("nfl", 2, db),
    ]


def test_days_flag_sets_the_poll_window(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "3", "--sport", "cfb", "--days", "7", "--season", "2026", "--file", str(page), "--db", str(db)
    )

    assert result.returncode == 0, result.stderr
    assert commands == [_ingest(page, "cfb", 3, db), _poll("cfb", 3, db, days=7)]


def test_pool_week_one_has_no_nfl_board(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(tmp_path, "1", "--season", "2026", "--file", str(page), "--db", str(db))

    assert result.returncode == 0, result.stderr
    assert commands == [_ingest(page, "cfb", 1, db), _poll("cfb", 1, db)]


def test_sport_flag_starts_only_that_league(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "3", "--sport", "nfl", "--season", "2026", "--file", str(page), "--db", str(db)
    )

    assert result.returncode == 0, result.stderr
    assert commands == [_ingest(page, "nfl", 2, db), _poll("nfl", 2, db)]


def test_a_failed_ingest_skips_that_leagues_poll_but_not_the_other_league(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "3", "--season", "2026", "--file", str(page), "--db", str(db),
        fail_on=f"ingest-cbs --html --file {page} --sport cfb",
    )

    assert result.returncode == 1
    assert commands == [_ingest(page, "cfb", 3, db), _ingest(page, "nfl", 2, db), _poll("nfl", 2, db)]
    assert "cfb week 3 (ingest)" in result.stderr


def test_a_failed_poll_is_reported_by_stage_and_the_other_league_still_runs(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "3", "--season", "2026", "--file", str(page), "--db", str(db),
        fail_on="poll-odds --sport cfb",
    )

    assert result.returncode == 1
    assert commands == [
        _ingest(page, "cfb", 3, db),
        _poll("cfb", 3, db),
        _ingest(page, "nfl", 2, db),
        _poll("nfl", 2, db),
    ]
    assert "cfb week 3 (poll)" in result.stderr
    assert "nfl" not in result.stderr


def test_a_missing_page_runs_nothing(tmp_path):
    result, commands = _run(
        tmp_path, "3", "--season", "2026", "--file", str(tmp_path / "absent.html")
    )

    assert result.returncode == 1
    assert commands == []
    assert "absent.html" in result.stderr


def test_a_non_numeric_week_is_a_usage_error(tmp_path):
    result, commands = _run(tmp_path, "three")

    assert result.returncode == 2
    assert commands == []


def test_a_bad_days_value_is_a_usage_error(tmp_path):
    page = _page(tmp_path)

    for days in ("0", "seven", "-3"):
        result, commands = _run(tmp_path, "3", "--days", days, "--file", str(page))

        assert result.returncode == 2, days
        assert commands == [], days


def test_an_unknown_flag_is_a_usage_error(tmp_path):
    result, commands = _run(tmp_path, "3", "--dayz", "7")

    assert result.returncode == 2
    assert commands == []
