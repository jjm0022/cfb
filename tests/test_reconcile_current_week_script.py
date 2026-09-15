from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pickem.models import Game, LeagueLine, Sport
from pickem.store.db import Store

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconcile-current-week.sh"


def test_reconcile_script_syncs_the_active_nfl_and_cfb_scopes(tmp_path):
    """Removing either current-scope sync command must fail this contract."""
    db = tmp_path / "pickem.duckdb"
    kickoff = datetime(2026, 9, 13, 17, tzinfo=UTC)
    games = [
        Game(
            game_id="cfb-2026-02-A-at-B",
            sport=Sport.CFB,
            season=2026,
            week=2,
            kickoff_utc=kickoff,
            home_team_id="B",
            away_team_id="A",
        ),
        Game(
            game_id="nfl-2026-01-C-at-D",
            sport=Sport.NFL,
            season=2026,
            week=1,
            kickoff_utc=kickoff,
            home_team_id="D",
            away_team_id="C",
        ),
    ]
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(games)
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=-3.0,
                    posted_at=kickoff,
                )
                for game in games
            ]
        )

    command_log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$RECONCILE_COMMAND_LOG"\n')
    fake_uv.chmod(0o755)
    environment = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PYTHONPATH": f"{ROOT / 'src'}:{os.environ.get('PYTHONPATH', '')}",
        "RECONCILE_COMMAND_LOG": str(command_log),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), "--db", str(db)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert command_log.read_text().splitlines() == [
        f"run pickem sync-results --sport cfb --season 2026 --week 2 --db {db}",
        f"run pickem sync-results --sport nfl --season 2026 --db {db}",
    ]
