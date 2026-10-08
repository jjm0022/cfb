"""Shared fixtures for the dashboard: a synthetic season, and runners for Node and Chrome."""

import json
import random
import re
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


def page_fixture(tmp_path: Path, report=None, **render_kwargs) -> Path:
    path = tmp_path / "index.html"
    if report is None and "season" not in render_kwargs:
        report = fixture_report()
    path.write_text(render_dashboard(report, generated_at=GENERATED, **render_kwargs))
    return path


def data_block(page: str) -> dict:
    """The JSON the page carries in its data block."""
    pattern = r'<script type="application/json" id="pickem-data">(.*?)</script>'
    match = re.search(pattern, page, re.S)
    return json.loads(match.group(1))


TW_KICK = datetime(2026, 10, 10, 16, tzinfo=UTC)
TW_STRONG = "cfb-2026-06-OU-at-MICH"
TW_SLIGHT = "nfl-2026-05-BUF-at-MIA"
TW_LOCKED = "cfb-2026-06-PSU-at-TEM"
TW_NO_MODEL = "nfl-2026-05-NE-at-SEA"
LOGOS_FIXTURE = {
    "light": {"cfb": ["MICH", "OU"], "nfl": ["BUF"]},
    "dark": {"cfb": ["OU"], "nfl": []},
}


def this_week_fixture() -> dict:
    """This week's board as the publisher writes it: one game in each state the page draws."""
    def at(hours: float) -> str:
        return (TW_KICK - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")

    def rec(hours, side, tier, edge):
        return {"at": at(hours), "side": side, "tier": tier, "edge": edge}

    def us(hours, spread):
        return {"at": at(hours), "source": "us", "spread": spread}

    def base(game_id, sport, week, away, home, kickoff_hours, line, **rest):
        return {"id": game_id, "sport": sport, "week": week, "kickoff": at(kickoff_hours),
                "home": home, "away": away, "home_score": None, "away_score": None,
                "line": line, "locked": False, "final": False, "model": None, "result": None,
                "market": {"us": None, "pinnacle": None}, "gap": None, "key_number": None,
                "history": [], "lines": [], **rest}

    return {"pool_week": 6, "games": [
        base("cfb-2026-06-AF-at-ARMY", "cfb", 6, "AF", "ARMY", 48, -3.5,
             home_score=24, away_score=17, locked=True, final=True, result="win",
             model={**rec(49, "home", "strong", 3.5),
                    "rationale": "league -3.5 vs market -7.0: 3.5 pts toward home"},
             market={"us": -7.0, "pinnacle": None}, gap=3.5,
             history=[rec(49, "home", "strong", 3.5)], lines=[us(50, -7.0)]),
        base(TW_LOCKED, "cfb", 6, "PSU", "TEM", 4, 7.0, locked=True,
             model={**rec(6, "home", "lean", 1.5), "rationale": None},
             market={"us": 5.5, "pinnacle": None}, gap=1.5,
             history=[rec(6, "home", "lean", 1.5)], lines=[us(6, 5.5)]),
        base(TW_STRONG, "cfb", 6, "OU", "MICH", 0, -3.0,
             model={**rec(3, "home", "strong", 3.0),
                    "rationale": "league -3.0 vs market -6.0: 3.0 pts toward home"},
             market={"us": -6.0, "pinnacle": -5.5}, gap=3.0,
             history=[rec(80, "away", "lean", -1.5), rec(30, "home", "lean", 1.5),
                      rec(6, "home", "strong", 3.0)],
             lines=[us(81, -4.0), us(31, -5.0),
                    {"at": at(31), "source": "pinnacle", "spread": -5.5}, us(7, -6.0)]),
        base(TW_SLIGHT, "nfl", 5, "BUF", "MIA", -24, -3.5,
             model={**rec(2, "away", "slight", -0.5),
                    "rationale": ("league -3.5 vs market -3.0: 0.5 pts toward away; "
                                  "crosses key number 3")},
             market={"us": -3.0, "pinnacle": None}, gap=-0.5, key_number=3,
             history=[rec(2, "away", "slight", -0.5)], lines=[us(3, -3.0)]),
        base(TW_NO_MODEL, "nfl", 5, "NE", "SEA", -27, 2.5),
    ]}


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
    # Drop the inlined scripts and data so assertions only see what the page drew.
    return re.sub(r"<script\b[^>]*>.*?</script>", "", dom, flags=re.S)
