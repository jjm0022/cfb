"""Build leakage-safe CFB market-residual training and evaluation rows."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from pickem.backtest.coinflip import CoinflipRow, build_coinflip_rows, latest_by_book
from pickem.edge.divergence import consensus_spread
from pickem.models import Game, MarketLine, Sport


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


def _group_by_game(lines: Sequence[MarketLine]) -> dict[str, list[MarketLine]]:
    grouped: dict[str, list[MarketLine]] = defaultdict(list)
    for line in lines:
        grouped[line.game_id].append(line)
    return grouped
