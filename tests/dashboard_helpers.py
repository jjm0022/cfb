"""Shared fixtures for the dashboard: a synthetic season, and runners for Node and Chrome."""

import random
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from results_helpers import KICK as STORE_KICK
from results_helpers import imported_store

from pickem.models import (
    HISTORY_MONITOR,
    LIVE_SOURCE,
    PINNACLE_SOURCE,
    Game,
    MarketLine,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
    make_game_id,
)
from pickem.report.dashboard import render_dashboard
from pickem.report.results import (
    BoardStanding,
    ResultsReport,
    WeekStanding,
    build_results_report,
    grade_game,
)

GENERATED = datetime(2026, 9, 29, 13, tzinfo=UTC)
KICK = datetime(2026, 9, 5, 16, tzinfo=UTC)


def synthetic_season(seed: int = 7, per_week: int = 30) -> ResultsReport:
    """Two pool weeks on both boards.

    Includes pushes, blank picks, every tier, and games the model skipped.
    """
    rng = random.Random(seed)
    games = []
    for week in (1, 2):
        for i in range(per_week):
            sport = Sport.CFB if i % 2 else Sport.NFL
            line = rng.choice([-7.0, -3.5, -3.0, 1.5, 3.0, 6.5])
            margin = -int(line) if i % 7 == 0 and line == int(line) else rng.randint(-14, 14)
            game = Game(
                game_id=f"{sport.value}-2026-{week:02d}-a{i}-at-h{i}", sport=sport, season=2026,
                week=week, kickoff_utc=KICK + timedelta(days=7 * (week - 1), hours=i),
                home_team_id=f"H{week}{i}", away_team_id=f"A{week}{i}",
                home_score=20 + margin, away_score=20,
            )
            tier = rng.choice([None, Tier.STRONG, Tier.LEAN, Tier.COINFLIP, Tier.NO_MARKET])
            history = [] if tier is None else [
                RecommendationRecord(
                    game_id=game.game_id, sport=sport, season=2026, week=week,
                    side=rng.choice([Side.HOME, Side.AWAY]), tier=tier,
                    edge_points=round(rng.uniform(-3, 3), 1),
                    generated_at=game.kickoff_utc - timedelta(hours=2), source=HISTORY_MONITOR,
                )
            ]
            games.append(grade_game(
                game=game, pool_week=week, league_spread=line,
                close_spread=rng.choice([None, line - 1, line, line + 0.5]),
                our_side=rng.choice([None, Side.HOME, Side.AWAY, Side.HOME]),
                field_home=rng.randint(0, 20), field_away=rng.randint(0, 20), history=history,
            ))
    standings = tuple(
        WeekStanding(
            pool_week=week, entrants=20, our_rank=5, our_points=15, median_points=14.0,
            winner_points=20, beat_share=0.75,
            boards=(BoardStanding(Sport.CFB, 8, 7.0, 11), BoardStanding(Sport.NFL, 7, 7.0, 10)),
        )
        for week in (1, 2)
    )
    return ResultsReport(
        season=2026, pool_week=2, entry_name="Jota", standings=standings,
        week_games=tuple(g for g in games if g.pool_week == 2), season_games=tuple(games),
    )


NODE = shutil.which("node")


def run_node(*args: str) -> str:
    """Run Node from the repo root and return stdout; fail loudly when Node is missing."""
    assert NODE, "Node is required for the dashboard's JavaScript tests: install nodejs"
    result = subprocess.run(
        [NODE, *args], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


CHROME = shutil.which("google-chrome") or shutil.which("chromium")
MODEL_GAME = "cfb-2026-02-PSU-at-TEM"
BARE_GAME = make_game_id(Sport.NFL, 2026, 1, "NE", "SEA")


def fixture_report():
    """The CBS fixture week, with one modelled game that has US and Pinnacle line history."""
    store = imported_store()
    store.append_recommendation_history([
        RecommendationRecord(
            game_id=MODEL_GAME, sport=Sport.CFB, season=2026, week=2, side=side, tier=tier,
            edge_points=edge, generated_at=STORE_KICK - timedelta(hours=hours),
            source=HISTORY_MONITOR,
        )
        for side, tier, edge, hours in [
            (Side.HOME, Tier.LEAN, 1.0, 30), (Side.HOME, Tier.STRONG, 2.5, 6),
            (Side.AWAY, Tier.STRONG, 2.1, 1),
        ]
    ])
    store.append_market_lines([
        MarketLine(game_id=MODEL_GAME, source=source, book=book, spread_home=spread,
                   captured_at=STORE_KICK - timedelta(hours=hours))
        for source, book, spread, hours in [
            (LIVE_SOURCE, "dk", 24.0, 30), (LIVE_SOURCE, "fd", 24.5, 30),
            (LIVE_SOURCE, "dk", 25.5, 2), (PINNACLE_SOURCE, "pinnacle", 25.0, 3),
        ]
    ])
    try:
        return build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    finally:
        store.close()


def page_fixture(tmp_path: Path, report=None) -> Path:
    path = tmp_path / "index.html"
    path.write_text(render_dashboard(report or fixture_report(), generated_at=GENERATED))
    return path


def rendered_dom(page: Path, fragment: str, tmp_path: Path) -> str:
    """The page's DOM after its scripts ran in headless Chrome, opened at ``#fragment``."""
    assert CHROME, "Google Chrome is required for the dashboard page tests"
    result = subprocess.run(
        [
            CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", f"--user-data-dir={tmp_path / 'chrome-profile'}",
            "--virtual-time-budget=3000", "--dump-dom", f"{page.as_uri()}#{fragment}",
        ],
        capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stderr
    dom = result.stdout
    assert 'data-boot="ok"' in dom, dom[-2000:]
    return dom
