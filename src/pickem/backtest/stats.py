"""Grading and honest interval estimation for backtest results."""

from __future__ import annotations

import math
from enum import StrEnum

from pickem.models import Side


class Result(StrEnum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"


def grade_pick(side: Side, home_margin: int, spread_home: float) -> Result:
    """Grade a pick against a home-perspective spread.

    ``home_margin`` is home_score - away_score. The home side covers when
    ``home_margin + spread_home > 0``; exactly zero is a push.
    """
    cover = home_margin + spread_home
    if cover == 0:
        return Result.PUSH
    home_covered = cover > 0
    picked_home = side is Side.HOME
    return Result.WIN if home_covered == picked_home else Result.LOSS


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because it stays sane at small
    samples and near the boundaries — exactly the regime a season of picks
    lives in.
    """
    if successes > trials or successes < 0 or trials < 0:
        raise ValueError(f"impossible input: {successes} successes in {trials} trials")
    if trials == 0:
        return (0.0, 1.0)

    p = successes / trials
    denominator = 1 + z**2 / trials
    center = (p + z**2 / (2 * trials)) / denominator
    spread = z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / denominator
    return (max(0.0, center - spread), min(1.0, center + spread))
