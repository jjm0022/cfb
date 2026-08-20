"""Measure the assumption the backtest rests on.

The phase-exit result proxies the frozen CBS spread with an early-week market
snapshot, because historical CBS numbers were never recorded. That substitution
is only sound if CBS's number tracks the market. This module measures the gap
once real pastes exist.

Pure functions only — no network, no database — so the measurement is testable
offline and replayable, exactly like ``edge/``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from pydantic import BaseModel

from pickem.edge.divergence import consensus_spread
from pickem.models import LeagueLine, MarketLine

ASSUMPTIONS = [
    "A league line's `posted_at` is the moment the CBS block was pasted at "
    "ingest, not the moment CBS froze the number; a late paste widens the "
    "residual through our own delay rather than through any disagreement.",
    "The market end is the consensus across books at or before that moment, "
    "collapsed by the same `consensus_spread` the strategy itself uses.",
    "Bias and dispersion are reported separately: a systematic offset re-tiers "
    "the whole sheet, while symmetric noise around zero largely washes out.",
]


class GameResidual(BaseModel):
    game_id: str
    league_spread: float
    market_spread: float
    residual: float


class CalibrationReport(BaseModel):
    compared: int
    # Bias: is CBS systematically off the market? A systematic offset shifts
    # every game's delta the same direction and re-tiers the whole sheet.
    mean_residual: float | None
    # Dispersion: how noisy is the agreement? Symmetric noise around zero
    # mostly washes out of a divergence strategy. Kept separate on purpose.
    mean_abs_residual: float | None
    # Cumulative shares, in the vocabulary the archive-vs-nflverse cross-check
    # already uses, so the two numbers can be read side by side.
    share_exact: float | None
    share_within_half: float | None
    share_within_one: float | None
    residuals: list[GameResidual]
    assumptions: list[str]
    skipped: list[str]


def calibrate(
    league_lines: Sequence[LeagueLine],
    market_lines: Sequence[MarketLine],
    source: str | None = None,
) -> CalibrationReport:
    """Compare each league line against the market consensus behind it.

    ``source`` restricts the market end to one capture regime — the archive
    proxy or the live polls — so the two are never averaged together.
    """
    by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in market_lines:
        if source is not None and line.source != source:
            continue
        by_game[line.game_id].append(line)

    residuals: list[GameResidual] = []
    skipped: list[str] = []
    for league in league_lines:
        # At or before only: a later snapshot is what the strategy trades
        # against, not what CBS could have been looking at.
        available = [
            line for line in by_game.get(league.game_id, []) if line.captured_at <= league.posted_at
        ]
        consensus = consensus_spread(available)
        if consensus is None:
            skipped.append(
                f"{league.game_id}: no market line captured at or before "
                f"{league.posted_at.isoformat()} — nothing to calibrate against"
            )
            continue
        residuals.append(
            GameResidual(
                game_id=league.game_id,
                league_spread=league.spread_home,
                market_spread=consensus,
                residual=league.spread_home - consensus,
            )
        )

    return CalibrationReport(
        compared=len(residuals),
        mean_residual=_mean([r.residual for r in residuals]) if residuals else None,
        mean_abs_residual=_mean([abs(r.residual) for r in residuals]) if residuals else None,
        share_exact=_share_within(residuals, 0.0),
        share_within_half=_share_within(residuals, 0.5),
        share_within_one=_share_within(residuals, 1.0),
        residuals=residuals,
        assumptions=list(ASSUMPTIONS),
        skipped=skipped,
    )


def _share_within(residuals: Sequence[GameResidual], tolerance: float) -> float | None:
    """Cumulative share agreeing to within ``tolerance`` points, inclusive."""
    if not residuals:
        return None
    return sum(1 for r in residuals if abs(r.residual) <= tolerance) / len(residuals)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)
