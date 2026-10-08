"""The interactive dashboard: the season's results and this week's board, and the one page
that draws them.

The page is self-contained: its data, styles and scripts are inlined, and it
makes no outside requests. Grading happens here in Python; the page only
counts what this module wrote.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pickem.models import RecommendationRecord
from pickem.report.results import (
    BACKTEST_TIER_RATES,
    BASELINES,
    GLOSSARY_INTRO,
    STRATEGY_DEFINITIONS,
    WEEK_STRATEGIES,
    GradedGame,
    ResultsReport,
    Strategy,
    WeekStanding,
    findings,
)

ASSETS = Path(__file__).with_name("dashboard_assets")
# Inline order: pure logic first, then helpers, charts, panel and tabs, and app.js last (it boots).
SCRIPTS = (
    "stats.js",
    "filters.js",
    "scales.js",
    "ui_core.js",
    "charts.js",
    "panel.js",
    "tab_thisweek.js",
    "tab_week.js",
    "tab_season.js",
    "tab_model.js",
    "tab_games.js",
    "app.js",
)


# The saved-logo listing, by theme then board: team ids with a file under logos/<sport>/.
NO_LOGOS = {"light": {"cfb": [], "nfl": []}, "dark": {"cfb": [], "nfl": []}}


def build_dashboard_data(
    report: ResultsReport | None,
    *,
    generated_at: datetime,
    season: int | None = None,
    this_week: dict | None = None,
    logos: dict | None = None,
) -> dict:
    """Everything the page shows, as plain JSON-ready values.

    ``report`` is None before the season's first results import; the page then
    carries only this week's board, and ``season`` must say which season it is.
    """
    if report is None and season is None:
        raise ValueError("a page with no results report needs its season")
    return {
        "season": report.season if report is not None else season,
        "entry": report.entry_name if report is not None else None,
        "latest_week": report.pool_week if report is not None else None,
        "generated_at": iso_utc(generated_at),
        "strategies": [s.value for s in Strategy],
        "baselines": [s.value for s in BASELINES],
        "week_strategies": [s.value for s in WEEK_STRATEGIES],
        "definitions": {s.value: text for s, text in STRATEGY_DEFINITIONS.items()},
        "glossary_intro": GLOSSARY_INTRO,
        "backtest": {tier.value: rate for tier, rate in BACKTEST_TIER_RATES.items()},
        "standings": [] if report is None else [_standing(s) for s in report.standings],
        "findings": {} if report is None else {
            str(s.pool_week): _findings(report, s.pool_week) for s in report.standings
        },
        "games": [] if report is None else [_game(g) for g in report.season_games],
        "analysis": None,
        "this_week": this_week,
        "logos": logos if logos is not None else NO_LOGOS,
    }


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _value(member: Enum | None) -> str | None:
    return None if member is None else member.value


def _standing(standing: WeekStanding) -> dict:
    return {
        "week": standing.pool_week,
        "entrants": standing.entrants,
        "rank": standing.our_rank,
        "points": standing.our_points,
        "median": standing.median_points,
        "winner": standing.winner_points,
        "beat_share": standing.beat_share,
        "boards": [
            {"sport": b.sport.value, "points": b.our_points, "median": b.median_points,
             "best": b.best_points}
            for b in standing.boards
        ],
    }


def _findings(report: ResultsReport, week: int) -> dict:
    found = findings([g for g in report.season_games if g.pool_week <= week])
    return {"claims": list(found.claims), "not_yet": list(found.not_yet)}


def recommendation_json(record: RecommendationRecord) -> dict:
    return {
        "at": iso_utc(record.generated_at),
        "side": record.side.value,
        "tier": record.tier.value,
        "edge": record.edge_points,
    }


def _game(graded: GradedGame) -> dict:
    game = graded.game
    return {
        "id": game.game_id,
        "sport": game.sport.value,
        "week": graded.pool_week,
        "kickoff": iso_utc(game.kickoff_utc),
        "home": game.home_team_id,
        "away": game.away_team_id,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "line": graded.league_spread,
        "close": graded.close_spread,
        "field_home": graded.field_home,
        "field_away": graded.field_away,
        "against_field": graded.against_field,
        "clv": graded.clv,
        "key_number": graded.key_number,
        "picks": {s.value: _value(graded.picks.get(s)) for s in Strategy},
        "results": {s.value: _value(graded.result(s)) for s in Strategy},
        "model": None if graded.model is None else recommendation_json(graded.model),
        "history": [recommendation_json(r) for r in graded.history],
        "lines": [
            {"at": iso_utc(p.captured_at), "source": p.source, "spread": p.spread_home}
            for p in graded.line_history
        ],
    }


def render_dashboard(
    report: ResultsReport | None,
    *,
    generated_at: datetime,
    season: int | None = None,
    this_week: dict | None = None,
    logos: dict | None = None,
) -> str:
    """One self-contained page: the season's results, and this week's board."""
    data = build_dashboard_data(
        report, generated_at=generated_at, season=season, this_week=this_week, logos=logos
    )
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    js = "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in SCRIPTS)
    title = html.escape(f"Pick'em {data['season']}")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{title}</title><style>{css}</style></head>"
        '<body><div id="app"><noscript>This page needs JavaScript.</noscript></div>'
        f'<script type="application/json" id="pickem-data">{_script_json(data)}</script>'
        f"<script>{js}</script></body></html>\n"
    )


def render_week_forwarder(pool_week: int) -> str:
    """A tiny page that opens the dashboard at ``pool_week``, so old links keep working."""
    target = f"index.html#week={int(pool_week)}"
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Week {int(pool_week)}</title>"
        f'<script>location.replace("{target}")</script></head>'
        f'<body><p><a href="{target}">Open week {int(pool_week)}</a></p></body></html>\n'
    )


def _script_json(data: dict) -> str:
    """JSON that no string inside it can use to close the surrounding script tag."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
