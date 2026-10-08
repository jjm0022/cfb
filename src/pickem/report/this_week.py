"""This week's board: every game of the current pool week, its pick, and how its line moved.

Read-only. A game not yet started carries the pick the bot's refresh would make
now, computed the same way. A game already kicked off carries the last pick
recorded before kickoff, the one that counted. Market quotes after a game's
kickoff are ignored everywhere, so the chart and the pick stop where the pick
locked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from pickem.backtest.stats import grade_pick
from pickem.edge.divergence import suppress_decision_logging
from pickem.edge.key_numbers import key_number_crossed
from pickem.edge.pipeline import decide_edges
from pickem.models import (
    PINNACLE_SOURCE,
    Edge,
    Game,
    MarketLine,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.operations.recommendations import LIVE_THRESHOLDS
from pickem.report.dashboard import iso_utc, recommendation_json
from pickem.report.results import PINNACLE_LINE, US_LINE, LinePoint, last_before, line_history
from pickem.store.db import Store


def build_this_week(store: Store, season: int, now: datetime) -> dict | None:
    """The latest pool week's games on both boards in kickoff order; None before any board."""
    weeks = store.pool_week_last_kickoffs(season)
    if not weeks:
        return None
    pool_week = max(weeks)
    games: list[dict] = []
    for sport, week in ((Sport.CFB, pool_week), (Sport.NFL, pool_week - 1)):
        if week >= 1:
            games.extend(_board(store, sport, season, week, now))
    games.sort(key=lambda g: (g["kickoff"], g["id"]))
    return {"pool_week": pool_week, "games": games}


def _board(store: Store, sport: Sport, season: int, week: int, now: datetime) -> list[dict]:
    dataset = store.load_week(sport, season, week)
    games = {game.game_id: game for game in dataset.games}
    # load_week joins CBS lines to their games, so every line here has its game.
    league = dataset.league_lines
    if not league:
        return []
    quotes: dict[str, list[MarketLine]] = defaultdict(list)
    for line in dataset.market_lines:
        if line.game_id in games and line.captured_at < games[line.game_id].kickoff_utc:
            quotes[line.game_id].append(line)
    with suppress_decision_logging():
        edges = decide_edges(
            league,
            # The same inputs as the bot's refresh: Pinnacle is recorded, not used.
            [q for lines in quotes.values() for q in lines if q.source != PINNACLE_SOURCE],
            dataset.games,
            store.games_before(sport, season, week),
            LIVE_THRESHOLDS[sport],
        )
    by_game = {edge.game_id: edge for edge in edges}
    history: dict[str, list[RecommendationRecord]] = defaultdict(list)
    for record in store.recommendation_history([line.game_id for line in league]):
        history[record.game_id].append(record)
    return [
        _game(games[line.game_id], line.spread_home, by_game.get(line.game_id),
              history[line.game_id], quotes[line.game_id], now)
        for line in league
    ]


def _game(
    game: Game,
    league_spread: float,
    edge: Edge | None,
    history: Sequence[RecommendationRecord],
    quotes: Sequence[MarketLine],
    now: datetime,
) -> dict:
    locked = game.kickoff_utc <= now
    final = game.home_score is not None and game.away_score is not None
    model = _locked_pick(edge, history, game.kickoff_utc) if locked else _live_pick(edge, now)
    points = line_history(quotes, game.kickoff_utc)
    us, pinnacle = _last(points, US_LINE), _last(points, PINNACLE_LINE)
    shown = sorted(
        (r for r in history if r.generated_at < game.kickoff_utc),
        key=lambda r: (r.generated_at, r.source),
    )
    return {
        "id": game.game_id,
        "sport": game.sport.value,
        "week": game.week,
        "kickoff": iso_utc(game.kickoff_utc),
        "home": game.home_team_id,
        "away": game.away_team_id,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "line": league_spread,
        "locked": locked,
        "final": final,
        "model": model,
        "result": _result(model, game, league_spread) if final else None,
        "market": {"us": us, "pinnacle": pinnacle},
        "gap": None if us is None else round(league_spread - us, 2),
        "key_number": _key_number(model, league_spread),
        "history": [recommendation_json(r) for r in shown],
        "lines": [
            {"at": iso_utc(p.captured_at), "source": p.source, "spread": p.spread_home}
            for p in points
        ],
    }


def _live_pick(edge: Edge | None, now: datetime) -> dict | None:
    if edge is None:
        return None
    return {"at": iso_utc(now), "side": edge.side.value, "tier": edge.tier.value,
            "edge": edge.delta, "rationale": edge.rationale}


def _locked_pick(
    edge: Edge | None, history: Sequence[RecommendationRecord], kickoff: datetime
) -> dict | None:
    record = last_before(history, kickoff)
    if record is None:
        return None
    # The recompute's reason only explains the recorded pick when they agree.
    agrees = edge is not None and edge.side is record.side and edge.tier is record.tier
    pick = recommendation_json(record)
    pick["rationale"] = edge.rationale if agrees else None
    return pick


def _last(points: Sequence[LinePoint], source: str) -> float | None:
    matching = [p.spread_home for p in points if p.source == source]
    return matching[-1] if matching else None


def _result(model: dict | None, game: Game, league_spread: float) -> str | None:
    if model is None:
        return None
    margin = game.home_score - game.away_score
    return grade_pick(Side(model["side"]), margin, league_spread).value


def _key_number(model: dict | None, league_spread: float) -> int | None:
    if model is None or model["tier"] != Tier.SLIGHT.value:
        return None
    return key_number_crossed(league_spread, league_spread - model["edge"])
