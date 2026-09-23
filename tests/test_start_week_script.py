from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-week.sh"


def _run(
    tmp_path: Path,
    *args: str,
    fail_on: str | tuple[str, ...] = (),
    current_week: int = 4,
    status: str = "new",
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the script against a fake ``uv`` that logs each call.

    ``fail_on`` makes the fake exit 1 for any call whose arguments contain one
    of the patterns. ``cbs-current-week`` prints ``current_week`` and
    ``pool-week-status`` prints ``status``.
    """
    patterns = (fail_on,) if isinstance(fail_on, str) else fail_on
    command_log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$START_WEEK_COMMAND_LOG"\n'
        'while IFS= read -r pattern; do\n'
        '    if [[ -n "$pattern" && "$*" == *"$pattern"* ]]; then exit 1; fi\n'
        'done <<< "$START_WEEK_FAIL_ON"\n'
        'if [[ "$*" == *cbs-current-week* ]]; then echo "$START_WEEK_CURRENT"; fi\n'
        'if [[ "$*" == *pool-week-status* ]]; then echo "$START_WEEK_STATUS"; fi\n'
        "exit 0\n"
    )
    fake_uv.chmod(0o755)
    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "START_WEEK_COMMAND_LOG": str(command_log),
        "START_WEEK_FAIL_ON": "\n".join(patterns),
        "START_WEEK_CURRENT": str(current_week),
        "START_WEEK_STATUS": status,
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
    return (
        f"run pickem ingest-cbs --html --file {page} --sport {sport} "
        f"--season 2026 --week {week} --db {db}"
    )


def _poll(sport: str, week: int, db: Path, days: int = 11) -> str:
    return (
        f"run pickem poll-odds --sport {sport} --season 2026 --week {week} "
        f"--days {days} --db {db}"
    )


def _fetch(page: Path, week: int, *extra: str) -> str:
    return " ".join(
        ["run pickem fetch-cbs --page board --pool-week", str(week), "--out", str(page), *extra]
    )


def _status(week: int, db: Path) -> str:
    return f"run pickem pool-week-status --season 2026 --pool-week {week} --db {db}"


def _dms(commands: list[str]) -> list[str]:
    return [c for c in commands if "notify-owner" in c]


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
        tmp_path, "3", "--sport", "cfb", "--days", "7", "--season", "2026",
        "--file", str(page), "--db", str(db),
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
    assert commands == [
        _ingest(page, "cfb", 3, db), _ingest(page, "nfl", 2, db), _poll("nfl", 2, db),
    ]
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


def test_a_missing_default_page_is_fetched_before_ingest(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"
    page = weeks / "week4.html"

    result, commands = _run(
        tmp_path, "4", "--sport", "cfb", "--season", "2026",
        "--weeks-dir", str(weeks), "--db", str(db),
    )

    assert result.returncode == 0, result.stderr
    assert commands == [_fetch(page, 4), _ingest(page, "cfb", 4, db), _poll("cfb", 4, db)]


def test_an_existing_default_page_is_used_without_fetching(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"
    weeks.mkdir()
    page = weeks / "week4.html"
    page.write_text("<html></html>")

    result, commands = _run(
        tmp_path, "4", "--sport", "cfb", "--season", "2026",
        "--weeks-dir", str(weeks), "--db", str(db),
    )

    assert result.returncode == 0, result.stderr
    assert commands == [_ingest(page, "cfb", 4, db), _poll("cfb", 4, db)]


def test_refetch_replaces_an_existing_page(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"
    weeks.mkdir()
    page = weeks / "week4.html"
    page.write_text("<html></html>")

    result, commands = _run(
        tmp_path, "4", "--sport", "cfb", "--refetch", "--season", "2026",
        "--weeks-dir", str(weeks), "--db", str(db),
    )

    assert result.returncode == 0, result.stderr
    assert commands[0] == _fetch(page, 4, "--force")


def test_a_failed_fetch_ingests_nothing(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "4", "--season", "2026", "--weeks-dir", str(weeks), "--db", str(db),
        fail_on="fetch-cbs",
    )

    assert result.returncode == 1
    assert commands == [_fetch(weeks / "week4.html", 4)]
    assert _dms(commands) == []  # manual runs report on the terminal only


def test_auto_starts_the_current_week_and_sends_the_reminder(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"
    page = weeks / "week4.html"

    result, commands = _run(
        tmp_path, "--auto", "--season", "2026", "--weeks-dir", str(weeks), "--db", str(db)
    )

    assert result.returncode == 0, result.stderr
    assert commands[:3] == ["run pickem cbs-current-week", _status(4, db), _fetch(page, 4)]
    assert commands[3:7] == [
        _ingest(page, "cfb", 4, db), _poll("cfb", 4, db),
        _ingest(page, "nfl", 3, db), _poll("nfl", 3, db),
    ]
    [dm] = _dms(commands)
    assert "Pool week 4 board loaded" in dm and "submit this week's picks" in dm


def test_auto_does_nothing_for_a_week_already_started(tmp_path):
    db = tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "--auto", "--season", "2026", "--weeks-dir", str(tmp_path), "--db", str(db),
        status="started",
    )

    assert result.returncode == 0, result.stderr
    assert commands == ["run pickem cbs-current-week", _status(4, db)]


def test_auto_reports_when_cbs_still_shows_a_finished_week(tmp_path):
    db = tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "--auto", "--season", "2026", "--weeks-dir", str(tmp_path), "--db", str(db),
        status="finished",
    )

    assert result.returncode == 1
    [dm] = _dms(commands)
    assert "still shows pool week 4" in dm
    assert not any("fetch-cbs" in c or "ingest-cbs" in c for c in commands)


def test_auto_dm_names_the_league_that_failed_and_still_reminds(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "--auto", "--season", "2026", "--weeks-dir", str(weeks), "--db", str(db),
        fail_on="ingest-cbs --html --file " + str(weeks / "week4.html") + " --sport nfl",
    )

    assert result.returncode == 1
    [dm] = _dms(commands)
    assert "failed for nfl week 3 (ingest)" in dm
    assert "Loaded: cfb week 4" in dm and "submit this week's picks" in dm


def test_auto_dm_omits_the_parenthetical_when_nothing_loaded(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "--auto", "--sport", "nfl", "--season", "2026",
        "--weeks-dir", str(weeks), "--db", str(db), current_week=1,
    )

    assert result.returncode == 0, result.stderr
    [dm] = _dms(commands)
    assert "()" not in dm
    assert "Pool week 1 board loaded." in dm and "submit this week's picks" in dm


def test_auto_reports_a_failed_week_lookup(tmp_path):
    result, commands = _run(
        tmp_path, "--auto", "--season", "2026", "--weeks-dir", str(tmp_path),
        "--db", str(tmp_path / "db"), fail_on="cbs-current-week",
    )

    assert result.returncode == 1
    assert len(_dms(commands)) == 1


def test_a_failed_dm_does_not_change_the_exit_status(tmp_path):
    result, _ = _run(
        tmp_path, "--auto", "--season", "2026", "--weeks-dir", str(tmp_path / "w"),
        "--db", str(tmp_path / "db"), fail_on="notify-owner",
    )

    assert result.returncode == 0
    assert "DM" in result.stderr


def test_auto_and_a_pool_week_together_are_a_usage_error(tmp_path):
    result, commands = _run(tmp_path, "4", "--auto")

    assert result.returncode == 2
    assert commands == []
