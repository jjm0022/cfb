"""Resolve the games divergence cannot answer.

The rating is only ever allowed to decide COINFLIP and NO_MARKET games. A real
divergence signal is never overridden by it — the rating is deliberately weaker
than the market, and letting it outvote a moved line would throw away the edge
this system exists to capture.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from pickem.edge.elo import EloConfig, build_ratings, projected_margin, tiebreak_side
from pickem.models import Edge, Game, MarketLine, Tier

_TIEBREAK_TIERS = {Tier.COINFLIP, Tier.NO_MARKET}


def apply_tiebreaks(
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
        game = by_id.get(edge.game_id)
        if edge.tier not in _TIEBREAK_TIERS or game is None:
            resolved.append(edge)
            continue

        margin = projected_margin(ratings, game.home_team_id, game.away_team_id, config)
        side = tiebreak_side(margin, edge.league_spread)
        resolved.append(
            edge.model_copy(
                update={
                    "side": side,
                    "rationale": (
                        f"{edge.rationale}; rating projects home by {margin:+.1f} "
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
