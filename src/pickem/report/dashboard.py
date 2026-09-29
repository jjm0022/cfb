"""The interactive results dashboard: the season's data, and the one page that draws it.

The page is self-contained: its data, styles and scripts are inlined, and it
makes no outside requests. Grading happens here in Python; the page only
counts what this module wrote.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

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


def build_dashboard_data(report: ResultsReport, *, generated_at: datetime) -> dict:
    """Everything the page shows, as plain JSON-ready values."""
    return {
        "season": report.season,
        "entry": report.entry_name,
        "latest_week": report.pool_week,
        "generated_at": _iso(generated_at),
        "strategies": [s.value for s in Strategy],
        "baselines": [s.value for s in BASELINES],
        "week_strategies": [s.value for s in WEEK_STRATEGIES],
        "definitions": {s.value: text for s, text in STRATEGY_DEFINITIONS.items()},
        "glossary_intro": GLOSSARY_INTRO,
        "backtest": {tier.value: rate for tier, rate in BACKTEST_TIER_RATES.items()},
        "standings": [_standing(s) for s in report.standings],
        "findings": {
            str(s.pool_week): _findings(report, s.pool_week) for s in report.standings
        },
        "games": [_game(g) for g in report.season_games],
        "analysis": None,
    }


def _iso(moment: datetime) -> str:
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


def _recommendation(record: RecommendationRecord) -> dict:
    return {
        "at": _iso(record.generated_at),
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
        "kickoff": _iso(game.kickoff_utc),
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
        "picks": {s.value: _value(graded.picks.get(s)) for s in Strategy},
        "results": {s.value: _value(graded.result(s)) for s in Strategy},
        "model": None if graded.model is None else _recommendation(graded.model),
        "history": [_recommendation(r) for r in graded.history],
        "lines": [
            {"at": _iso(p.captured_at), "source": p.source, "spread": p.spread_home}
            for p in graded.line_history
        ],
    }
