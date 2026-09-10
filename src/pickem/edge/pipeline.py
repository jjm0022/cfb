"""Resolve the games divergence cannot answer.

Two deliberately weak rules split the work, and neither is ever allowed to
decide a STRONG or LEAN game. A real divergence signal is never overridden —
letting a rating outvote a moved line would throw away the edge this system
exists to capture.

COINFLIP takes the frozen board's favorite. The frozen line and the market
agree there, so any pick is a bet against the market's own number; the cheapest
rule that scored at the noise band wins. See `edge/favorite.py`.

NO_MARKET still uses Elo, because there is no market line to compare against
and the favorite rule has never been evaluated on those games.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from statistics import median

from pickem.edge.divergence import (
    Thresholds,
    _buffer_decision_logging,
    _compute_edge,
    _emit_decision_event,
)
from pickem.edge.elo import EloConfig, build_ratings, projected_margin, tiebreak_side
from pickem.edge.favorite import favorite_side
from pickem.models import Edge, Game, LeagueLine, MarketLine, Side, Tier

_TIEBREAK_TIERS = {Tier.COINFLIP, Tier.NO_MARKET}


class MissingGameError(LookupError):
    """A game needing a tiebreak has no matching Game record."""


def decide_edges(
    league_lines: Sequence[LeagueLine],
    market_lines: Sequence[MarketLine],
    games: Sequence[Game],
    history: Sequence[Game],
    thresholds: Thresholds | None = None,
    elo_config: EloConfig | None = None,
) -> list[Edge]:
    """Return final auditable picks; no placeholder side crosses this interface."""
    market_by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in market_lines:
        market_by_game[line.game_id].append(line)

    with _buffer_decision_logging():
        measured = [
            _compute_edge(league, market_by_game.get(league.game_id, []), thresholds)
            for league in league_lines
        ]
        decided = _apply_tiebreaks(measured, games, history, elo_config)
    by_id = {game.game_id: game for game in games}
    for edge in decided:
        game = by_id.get(edge.game_id)
        _emit_decision_event(
            "INFO",
            _decision_message(edge, game),
            event="edge_decided",
            game_id=edge.game_id,
            side=edge.side.value,
            tier=edge.tier.value,
            delta=round(edge.delta, 3),
            league_spread=edge.league_spread,
            market_spread=edge.market_spread,
            **_team_fields(game),
        )
    return decided


def _decision_message(edge: Edge, game: Game | None) -> str:
    """Name the matchup and the team picked, ahead of the rationale.

    The rationale alone reads "1.5 pts toward away" — true, but the text sink
    renders only the message, so without this prefix a human tailing the log
    sees a decision with no idea whose. `divergence.py` is pure and never sees a
    Game, so the names are attached here, where `decide_edges` already has them.
    """
    if game is None:
        return edge.rationale
    picked = game.home_team_id if edge.side is Side.HOME else game.away_team_id
    return (
        f"{game.away_team_id} at {game.home_team_id}: "
        f"pick {picked} ({edge.tier.value}) - {edge.rationale}"
    )


def _team_fields(game: Game | None) -> dict[str, str]:
    if game is None:
        return {}
    return {"home_team_id": game.home_team_id, "away_team_id": game.away_team_id}


def _apply_tiebreaks(
    edges: Sequence[Edge],
    games: Sequence[Game],
    history: Sequence[Game],
    config: EloConfig | None = None,
) -> list[Edge]:
    config = config or EloConfig()
    ratings = build_ratings(history, config)
    by_id = {game.game_id: game for game in games}

    resolved: list[Edge] = []
    for edge in edges:
        if edge.tier not in _TIEBREAK_TIERS:
            resolved.append(edge)
            continue

        game = by_id.get(edge.game_id)
        if game is None:
            # Falling through would ship the measurement stage's placeholder HOME side as
            # though it were a decision. These edges are exactly the ones with
            # no real signal, so a silent pass-through is the worst outcome.
            raise MissingGameError(
                f"{edge.game_id} needs a {edge.tier.value} tiebreak but has no game record"
            )

        if edge.tier is Tier.COINFLIP:
            resolved.append(_decide_by_favorite(edge, game))
        else:
            resolved.append(_decide_by_elo(edge, game, ratings, config))
    return resolved


def _decide_by_favorite(edge: Edge, game: Game) -> Edge:
    """Take the frozen board's favorite; the market gave us nothing to trade."""
    side = favorite_side(edge.league_spread)
    _emit_decision_event(
        "INFO",
        f"Frozen-board favorite is {side.value} on a board of {edge.league_spread:+.1f}",
        event="tiebreak_applied",
        method="frozen_line_favorite",
        game_id=edge.game_id,
        tier=edge.tier.value,
        home_team_id=game.home_team_id,
        away_team_id=game.away_team_id,
        league_spread=edge.league_spread,
        side=side.value,
    )
    return edge.model_copy(
        update={
            "side": side,
            "rationale": (
                f"{edge.rationale}; no divergence, so we take the frozen-board "
                f"favorite ({side.value}) on a board of {edge.league_spread:+.1f}"
            ),
        }
    )


def _decide_by_elo(edge: Edge, game: Game, ratings: dict[str, float], config: EloConfig) -> Edge:
    """Rate the matchup ourselves; there is no market line to lean on."""
    margin = projected_margin(ratings, game.home_team_id, game.away_team_id, config)
    side = tiebreak_side(margin, edge.league_spread)
    _emit_decision_event(
        "INFO",
        f"Elo tiebreak projects home by {margin:+.1f}",
        event="tiebreak_applied",
        method="elo",
        game_id=edge.game_id,
        tier=edge.tier.value,
        home_team_id=game.home_team_id,
        away_team_id=game.away_team_id,
        home_rating=ratings.get(game.home_team_id, config.initial),
        away_rating=ratings.get(game.away_team_id, config.initial),
        projected_margin=margin,
        league_spread=edge.league_spread,
        side=side.value,
    )
    return edge.model_copy(
        update={
            "side": side,
            "rationale": (
                f"{edge.rationale}; Elo rating projects home by {margin:+.1f} "
                f"vs a board of {edge.league_spread:+.1f}"
            ),
        }
    )


def predict_tiebreaker_total(market_lines: Sequence[MarketLine]) -> float | None:
    """Median market total. The market total is the estimate; we do not model it."""
    totals = [line.total for line in market_lines if line.total is not None]
    return median(totals) if totals else None
