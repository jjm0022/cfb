from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "import-cbs-week.sh"


def _run(tmp_path: Path, *args: str, fail_sport: str = "") -> tuple[subprocess.CompletedProcess, list[str]]:
    command_log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$IMPORT_COMMAND_LOG"\n'
        'if [[ -n "$IMPORT_FAIL_SPORT" && "$*" == *"--sport $IMPORT_FAIL_SPORT "* ]]; then exit 1; fi\n'
    )
    fake_uv.chmod(0o755)
    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "IMPORT_COMMAND_LOG": str(command_log),
        "IMPORT_FAIL_SPORT": fail_sport,
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


def test_a_pool_week_ingests_cfb_that_week_and_nfl_the_week_before(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(tmp_path, "3", "--season", "2026", "--file", str(page), "--db", str(db))

    assert result.returncode == 0, result.stderr
    assert commands == [
        f"run pickem ingest-cbs --html --file {page} --sport cfb --season 2026 --week 3 --db {db}",
        f"run pickem ingest-cbs --html --file {page} --sport nfl --season 2026 --week 2 --db {db}",
    ]


def test_pool_week_one_has_no_nfl_board(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(tmp_path, "1", "--season", "2026", "--file", str(page), "--db", str(db))

    assert result.returncode == 0, result.stderr
    assert commands == [
        f"run pickem ingest-cbs --html --file {page} --sport cfb --season 2026 --week 1 --db {db}",
    ]


def test_sport_flag_imports_only_that_league(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "3", "--sport", "nfl", "--season", "2026", "--file", str(page), "--db", str(db)
    )

    assert result.returncode == 0, result.stderr
    assert commands == [
        f"run pickem ingest-cbs --html --file {page} --sport nfl --season 2026 --week 2 --db {db}",
    ]


def test_a_league_that_fails_still_lets_the_other_import_but_exits_nonzero(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"

    result, commands = _run(
        tmp_path, "3", "--season", "2026", "--file", str(page), "--db", str(db), fail_sport="cfb"
    )

    assert result.returncode == 1
    assert len(commands) == 2
    assert "cfb" in result.stderr


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
