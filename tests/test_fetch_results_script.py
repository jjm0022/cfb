from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch-results.sh"


def _run(tmp_path: Path, *, pending: str = "4", fail_on: tuple[str, ...] = ()):
    log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$RESULTS_LOG"\n'
        'while IFS= read -r pattern; do\n'
        '    if [[ -n "$pattern" && "$*" == *"$pattern"* ]]; then\n'
        '        echo "noise line" >&2; echo "game 501: status is Thu, not final" >&2; exit 1\n'
        '    fi\n'
        'done <<< "$RESULTS_FAIL_ON"\n'
        'if [[ "$*" == *pending-results-week* ]]; then printf "%s" "$RESULTS_PENDING"; fi\n'
        "exit 0\n"
    )
    fake_uv.chmod(0o755)
    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RESULTS_LOG": str(log),
        "RESULTS_FAIL_ON": "\n".join(fail_on),
        "RESULTS_PENDING": pending,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--season", "2026", "--results-dir", str(results),
         "--db", str(tmp_path / "db")],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    commands = log.read_text().splitlines() if log.exists() else []
    return result, commands, results


def test_fetches_then_imports_the_pending_week(tmp_path):
    result, commands, results = _run(tmp_path)
    page, db = results / "week4.html", tmp_path / "db"

    assert result.returncode == 0, result.stderr
    assert commands == [
        f"run pickem pending-results-week --season 2026 --db {db}",
        f"run pickem fetch-cbs --page standings --pool-week 4 --out {page}",
        f"run pickem import-results --season 2026 --pool-week 4 --file {page} "
        f"--out-dir {results} --db {db}",
    ]


def test_nothing_pending_does_nothing(tmp_path):
    result, commands, _ = _run(tmp_path, pending="")

    assert result.returncode == 0
    assert len(commands) == 1


def test_an_already_saved_page_is_imported_without_fetching(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "week4.html").write_text("<html></html>")

    result, commands, _ = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert not any("fetch-cbs" in c for c in commands)


def test_unfinished_games_dm_the_last_error_line_and_skip_the_import(tmp_path):
    result, commands, _ = _run(tmp_path, fail_on=("fetch-cbs",))

    assert result.returncode == 1
    [dm] = [c for c in commands if "notify-owner" in c]
    assert "not final" in dm and "noise line" not in dm and "Wednesday 09:00" in dm
    assert not any("import-results" in c for c in commands)


def test_a_failed_import_is_reported(tmp_path):
    result, commands, _ = _run(tmp_path, fail_on=("import-results",))

    assert result.returncode == 1
    [dm] = [c for c in commands if "notify-owner" in c]
    assert "Wednesday 09:00" in dm


def test_a_non_numeric_pending_week_is_reported(tmp_path):
    result, commands, _ = _run(tmp_path, pending="garbage")

    assert result.returncode == 1
    dms = [c for c in commands if "notify-owner" in c]
    assert len(dms) == 1
    assert not any("fetch-cbs" in c or "import-results" in c for c in commands)


def test_a_failed_pending_lookup_is_reported(tmp_path):
    result, commands, _ = _run(tmp_path, fail_on=("pending-results-week",))

    assert result.returncode == 1
    assert any("notify-owner" in c for c in commands)
