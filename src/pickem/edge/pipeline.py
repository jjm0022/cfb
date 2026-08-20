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

from pickem.edge.divergence import Thresholds, _compute_edge
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

    measured = [
        _compute_edge(league, market_by_game.get(league.game_id, []), thresholds)
        for league in league_lines
    ]
    return _apply_tiebreaks(measured, games, history, elo_config)


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
