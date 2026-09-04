"""Resolve the games divergence cannot answer.

The rating is only ever allowed to decide COINFLIP and NO_MARKET games. A real
divergence signal is never overridden by it — the rating is deliberately weaker
than the market, and letting it outvote a moved line would throw away the edge
this system exists to capture.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from statistics import median

from loguru import logger

from pickem.edge.divergence import (
    Thresholds,
    _compute_edge,
    _decision_logging_enabled,
    suppress_decision_logging,
)
from pickem.edge.elo import EloConfig, build_ratings, projected_margin, tiebreak_side
from pickem.models import Edge, Game, LeagueLine, MarketLine, Tier

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

    # A missing game is an error, not a decision. Probe the measurement stage
    # first so it cannot emit records before the later tiebreak validation
    # raises. The real measurement below still owns all normal event details.
    game_ids = {game.game_id for game in games}
    effective_thresholds = thresholds or Thresholds()
    for league in league_lines:
        if league.game_id in game_ids:
            continue
        with suppress_decision_logging():
            measured = _compute_edge(
                league, market_by_game.get(league.game_id, []), effective_thresholds
            )
        if measured.tier in _TIEBREAK_TIERS:
            raise MissingGameError(
                f"{league.game_id} needs a {measured.tier.value} tiebreak but has no game record"
            )

    measured = [
        _compute_edge(league, market_by_game.get(league.game_id, []), thresholds)
        for league in league_lines
    ]
    decided = _apply_tiebreaks(measured, games, history, elo_config)
    if _decision_logging_enabled():
        for edge in decided:
            logger.bind(
                event="edge_decided",
                game_id=edge.game_id,
                side=edge.side.value,
                tier=edge.tier.value,
                delta=round(edge.delta, 3),
                league_spread=edge.league_spread,
                market_spread=edge.market_spread,
            ).info(edge.rationale)
    return decided


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

        margin = projected_margin(ratings, game.home_team_id, game.away_team_id, config)
        side = tiebreak_side(margin, edge.league_spread)
        if _decision_logging_enabled():
            logger.bind(
                event="tiebreak_applied",
                game_id=edge.game_id,
                tier=edge.tier.value,
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
                home_rating=ratings.get(game.home_team_id, config.initial),
                away_rating=ratings.get(game.away_team_id, config.initial),
                projected_margin=margin,
                league_spread=edge.league_spread,
                side=side.value,
            ).info(f"Elo tiebreak projects home by {margin:+.1f}")
        resolved.append(
            edge.model_copy(
                update={
                    "side": side,
                    "rationale": (
                        f"{edge.rationale}; Elo rating projects home by {margin:+.1f} "
                        f"vs a board of {edge.league_spread:+.1f}"
                    ),
                }
            )
        )
    return resolved


def predict_tiebreaker_total(market_lines: Sequence[MarketLine]) -> float | None:
    """Median market total. The market total is the estimate; we do not model it."""
    totals = [line.total for line in market_lines if line.total is not None]
    return median(totals) if totals else None
