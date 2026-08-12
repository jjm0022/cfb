"""Minimal Elo margin rating, used only to order games the market has not moved.

This is intentionally shallow. It is not trying to beat the closing line; it is
trying to be better than a coin flip on the handful of games where divergence
gives no answer. Sophistication here belongs in Phase B.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from pickem.models import Game, Side


class EloConfig(BaseModel):
    k: float = 20.0
    home_field: float = 2.0
    points_per_elo: float = 0.04
    initial: float = 1500.0


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def build_ratings(games: Sequence[Game], config: EloConfig | None = None) -> dict[str, float]:
    """Walk completed games in chronological order, updating ratings.

    Only games with both scores present are counted; scheduled-but-unplayed
    games must not move a rating.
    """
    config = config or EloConfig()
    ratings: dict[str, float] = {}
    played = [g for g in games if g.home_score is not None and g.away_score is not None]

    for game in sorted(played, key=lambda g: (g.season, g.week, g.kickoff_utc)):
        home = ratings.setdefault(game.home_team_id, config.initial)
        away = ratings.setdefault(game.away_team_id, config.initial)

        home_won = 1.0 if game.home_score > game.away_score else 0.0
        if game.home_score == game.away_score:
            home_won = 0.5

        elo_home = home + config.home_field / config.points_per_elo
        expected_home = _expected(elo_home, away)

        # Margin of victory multiplier, dampened so blowouts do not dominate.
        margin = abs(game.home_score - game.away_score)
        multiplier = ((margin + 1) ** 0.5) if margin else 1.0

        change = config.k * multiplier * (home_won - expected_home)
        ratings[game.home_team_id] = home + change
        ratings[game.away_team_id] = away - change

    return ratings


def projected_margin(
    ratings: dict[str, float],
    home_team_id: str,
    away_team_id: str,
    config: EloConfig | None = None,
) -> float:
    """Projected home margin in points. Positive means home is favored."""
    config = config or EloConfig()
    home = ratings.get(home_team_id, config.initial)
    away = ratings.get(away_team_id, config.initial)
    return (home - away) * config.points_per_elo + config.home_field


def tiebreak_side(projected_margin: float, league_spread: float) -> Side:
    """Pick the side our rating favors relative to the frozen line.

    `league_spread` is home-perspective, so the home team's implied margin is
    its negation. Home is the pick when we project a bigger margin than the
    board requires.
    """
    implied_home_margin = -league_spread
    return Side.HOME if projected_margin > implied_home_margin else Side.AWAY
