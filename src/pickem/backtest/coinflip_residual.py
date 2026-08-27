"""Build leakage-safe CFB market-residual training and evaluation rows."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

import numpy as np
from pydantic import BaseModel, model_validator
from sklearn.linear_model import Ridge

from pickem.backtest.coinflip import CoinflipRow, build_coinflip_rows, latest_by_book
from pickem.edge.divergence import consensus_spread
from pickem.models import Game, MarketLine, Sport

ALPHA_GRID = (10.0, 30.0, 100.0)
HALF_LIFE_DAYS = 365.0


class ResidualTrainingRow(BaseModel):
    game_id: str
    season: int
    week: int
    kickoff_utc: datetime
    home_team_id: str
    away_team_id: str
    submission_spread: float
    market_error: float


class ResidualDataset(BaseModel):
    training_rows: list[ResidualTrainingRow]
    evaluation_rows: list[CoinflipRow]
    training_skipped: list[str]
    evaluation_skipped: list[str]


class ResidualFit(BaseModel):
    """A fitted, recency-weighted ridge model of market residuals."""

    cutoff_utc: datetime
    alpha: float
    intercept: float
    teams: list[str]
    team_effects: dict[str, float]
    training_rows: int
    effective_weight: float

    @model_validator(mode="after")
    def validate_finite_values(self) -> ResidualFit:
        """Reject non-finite values before they can be serialized."""
        values = [self.alpha, self.intercept, self.effective_weight, *self.team_effects.values()]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("residual fit values must be finite")
        return self


def build_residual_dataset(
    games: Sequence[Game],
    frozen_lines: Sequence[MarketLine],
    submission_lines: Sequence[MarketLine],
) -> ResidualDataset:
    """Build continuous market-error rows alongside Candidate 1 evaluation rows."""
    evaluation = build_coinflip_rows(games, frozen_lines, submission_lines)
    submission_by_game = _group_by_game(submission_lines)
    training_rows: list[ResidualTrainingRow] = []
    skipped: list[str] = []

    for game in sorted(games, key=lambda row: (row.season, row.week, row.game_id)):
        if game.sport is not Sport.CFB:
            skipped.append(f"{game.game_id}: non-CFB game is outside Candidate 2")
            continue
        if game.home_score is None or game.away_score is None:
            skipped.append(f"{game.game_id}: unplayed game (missing final score)")
            continue
        available = [
            line
            for line in submission_by_game.get(game.game_id, [])
            if line.captured_at < game.kickoff_utc and math.isfinite(line.spread_home)
        ]
        spread = consensus_spread(latest_by_book(available))
        if spread is None or not math.isfinite(spread):
            skipped.append(f"{game.game_id}: missing submission consensus before kickoff")
            continue
        training_rows.append(
            ResidualTrainingRow(
                game_id=game.game_id,
                season=game.season,
                week=game.week,
                kickoff_utc=game.kickoff_utc,
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
                submission_spread=spread,
                market_error=game.home_score - game.away_score + spread,
            )
        )

    return ResidualDataset(
        training_rows=training_rows,
        evaluation_rows=evaluation.rows,
        training_skipped=skipped,
        evaluation_skipped=evaluation.skipped,
    )


def _recency_weights(rows: Sequence[ResidualTrainingRow], cutoff: datetime) -> np.ndarray:
    """Return exponentially decayed weights for rows strictly preceding ``cutoff``."""
    ages = np.asarray(
        [(cutoff - row.kickoff_utc).total_seconds() / 86400.0 for row in rows], dtype=float
    )
    if np.any(ages <= 0):
        raise ValueError("every training row must be strictly before fit cutoff")
    return np.power(2.0, -ages / HALF_LIFE_DAYS)


def _fit_residual_model(
    rows: Sequence[ResidualTrainingRow], cutoff: datetime, alpha: float
) -> ResidualFit:
    """Fit the fixed-alpha ridge model using sorted home-plus/away-minus encoding."""
    if alpha not in ALPHA_GRID:
        raise ValueError(f"alpha must be one of {ALPHA_GRID}")

    teams = sorted({team for row in rows for team in (row.home_team_id, row.away_team_id)})
    if not rows or not teams:
        raise ValueError("residual fit requires at least one training row")

    index = {team: column for column, team in enumerate(teams)}
    matrix = np.zeros((len(rows), len(teams)), dtype=float)
    for row_index, row in enumerate(rows):
        matrix[row_index, index[row.home_team_id]] = 1.0
        matrix[row_index, index[row.away_team_id]] = -1.0

    weights = _recency_weights(rows, cutoff)
    target = np.asarray([row.market_error for row in rows], dtype=float)
    model = Ridge(alpha=alpha, fit_intercept=True).fit(matrix, target, sample_weight=weights)
    effects = {team: float(model.coef_[index[team]]) for team in teams}

    return ResidualFit(
        cutoff_utc=cutoff,
        alpha=alpha,
        intercept=float(model.intercept_),
        teams=teams,
        team_effects=effects,
        training_rows=len(rows),
        effective_weight=float(np.sum(weights)),
    )


def _predict_market_error(fit: ResidualFit, home: str, away: str) -> float:
    """Predict the market error, treating teams absent from the fit as neutral."""
    prediction = fit.intercept + fit.team_effects.get(home, 0.0) - fit.team_effects.get(away, 0.0)
    if not math.isfinite(prediction):
        raise ValueError("residual prediction must be finite")
    return prediction


def _group_by_game(lines: Sequence[MarketLine]) -> dict[str, list[MarketLine]]:
    grouped: dict[str, list[MarketLine]] = defaultdict(list)
    for line in lines:
        grouped[line.game_id].append(line)
    return grouped
