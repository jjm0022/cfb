from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cbs-login.sh"


def test_a_non_executable_chrome_binary_fails_before_launching(tmp_path):
    profile = tmp_path / "profile"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "PICKEM_CHROME_BINARY": "/nonexistent",
            "PICKEM_CBS_CHROME_PROFILE": str(profile),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "/nonexistent" in result.stderr
    assert not profile.exists()
