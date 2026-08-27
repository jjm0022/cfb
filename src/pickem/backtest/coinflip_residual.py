"""Build leakage-safe CFB market-residual training and evaluation rows."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

import numpy as np
from pydantic import BaseModel, model_validator
from sklearn.linear_model import Ridge

from pickem.backtest.coinflip import (
    CalibrationBin,
    CoinflipRow,
    build_coinflip_rows,
    latest_by_book,
    replay_elo_sides,
)
from pickem.edge.divergence import consensus_spread
from pickem.models import Game, MarketLine, Side, Sport

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


class WeightedResidual(BaseModel):
    """A chronological out-of-fold residual reweighted to an outer cutoff."""

    error: float
    weight: float


class InnerSelection(BaseModel):
    """The deterministic alpha choice and calibration state for one outer fold."""

    alpha: float
    correct_by_alpha: dict[float, int]
    residuals: list[WeightedResidual]


class _InnerPartition(BaseModel):
    """One strictly chronological training and validation split."""

    training_rows: list[ResidualTrainingRow]
    validation_rows: list[ResidualTrainingRow]
    evaluation_rows: list[CoinflipRow]
    validation_season: int
    cutoff_utc: datetime


class ResidualPrediction(BaseModel):
    """One paired, season-locked Candidate 2 outer-fold prediction."""

    game_id: str
    season: int
    week: int
    kickoff_utc: datetime
    frozen_spread: float
    submission_spread: float
    predicted_market_error: float
    predicted_ats_margin: float
    probability_home: float | None
    candidate_side: Side
    favorite_side: Side
    elo_side: Side
    always_home_side: Side = Side.HOME
    target_home_cover: int | None
    is_push: bool
    candidate_correct: bool | None
    favorite_correct: bool | None
    elo_correct: bool | None
    always_home_correct: bool | None


class ResidualFold(BaseModel):
    """The immutable state used for one outer test season."""

    train_from: int
    train_through: int
    test_season: int
    cutoff_utc: datetime
    alpha: float
    fit: ResidualFit
    residual_count: int
    correct_by_alpha: dict[float, int]


class ResidualSeasonDelta(BaseModel):
    season: int
    candidate_accuracy: float
    favorite_accuracy: float
    candidate_minus_favorite: float


class ResidualEvaluation(BaseModel):
    """Complete auditable Candidate 2 evaluation and certification state."""

    folds: list[ResidualFold] = []
    predictions: list[ResidualPrediction] = []
    pushes: int = 0
    candidate_wins: int = 0
    candidate_losses: int = 0
    favorite_wins: int = 0
    favorite_losses: int = 0
    elo_wins: int = 0
    elo_losses: int = 0
    always_home_wins: int = 0
    always_home_losses: int = 0
    candidate_accuracy: float | None = None
    favorite_accuracy: float | None = None
    elo_accuracy: float | None = None
    always_home_accuracy: float | None = None
    accuracy_delta: float | None = None
    brier_score: float | None = None
    log_loss: float | None = None
    calibration: list[CalibrationBin] = []
    season_deltas: list[ResidualSeasonDelta] = []
    bootstrap_lower: float | None = None
    bootstrap_upper: float | None = None
    positive_seasons: int = 0
    calibration_safe: bool = False
    leakage_safe: bool = False
    deterministic: bool = False
    uses_2026_outcomes: bool = False
    training_skipped: list[str] = []
    evaluation_skipped: list[str] = []


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


def _inner_partitions(dataset: ResidualDataset, test_season: int) -> list[_InnerPartition]:
    """Return deterministic, expanding validation folds before ``test_season``."""
    training_rows = sorted(
        (row for row in dataset.training_rows if row.season < test_season),
        key=lambda row: (row.season, row.week, row.game_id),
    )
    if not training_rows:
        raise ValueError("inner selection requires training rows before the test season")

    evaluation_by_game = {
        row.game_id: row
        for row in sorted(
            (row for row in dataset.evaluation_rows if row.season < test_season),
            key=lambda row: (row.season, row.week, row.game_id),
        )
    }
    earliest_season = min(row.season for row in training_rows)
    if test_season == earliest_season + 1:
        validation_rows = [
            row for row in training_rows if row.season == earliest_season and row.week >= 9
        ]
        return [
            _build_inner_partition(
                [row for row in training_rows if row.season == earliest_season and row.week <= 8],
                validation_rows,
                evaluation_by_game,
                earliest_season,
            )
        ]

    partitions: list[_InnerPartition] = []
    for validation_season in range(earliest_season + 1, test_season):
        validation_rows = [row for row in training_rows if row.season == validation_season]
        if not validation_rows:
            continue
        partitions.append(
            _build_inner_partition(
                [row for row in training_rows if row.season < validation_season],
                validation_rows,
                evaluation_by_game,
                validation_season,
            )
        )
    return partitions


def _build_inner_partition(
    training_rows: Sequence[ResidualTrainingRow],
    validation_rows: Sequence[ResidualTrainingRow],
    evaluation_by_game: dict[str, CoinflipRow],
    validation_season: int,
) -> _InnerPartition:
    """Create a chronological split, rejecting empty or unordered fit populations."""
    if not training_rows or not validation_rows:
        raise ValueError("inner validation partition requires training and validation rows")
    cutoff = min(row.kickoff_utc for row in validation_rows)
    if any(row.kickoff_utc >= cutoff for row in training_rows):
        raise ValueError("inner training rows must be strictly before validation cutoff")
    evaluation_rows = [
        evaluation_by_game[row.game_id]
        for row in validation_rows
        if row.game_id in evaluation_by_game
    ]
    return _InnerPartition(
        training_rows=list(training_rows),
        validation_rows=list(validation_rows),
        evaluation_rows=evaluation_rows,
        validation_season=validation_season,
        cutoff_utc=cutoff,
    )


def _select_alpha(dataset: ResidualDataset, test_season: int) -> InnerSelection:
    """Choose ridge shrinkage on chronological COINFLIP accuracy and retain OOF errors."""
    partitions = _inner_partitions(dataset, test_season)
    if not partitions:
        raise ValueError("at least one inner validation partition is required to select alpha")

    correct_by_alpha: dict[float, int] = {}
    for alpha in ALPHA_GRID:
        correct = 0
        for partition in partitions:
            fit = _fit_residual_model(partition.training_rows, partition.cutoff_utc, alpha)
            training_by_game = {row.game_id: row for row in partition.validation_rows}
            for evaluation in partition.evaluation_rows:
                training = training_by_game[evaluation.game_id]
                margin = (
                    evaluation.frozen_spread
                    - training.submission_spread
                    + _predict_market_error(fit, training.home_team_id, training.away_team_id)
                )
                if evaluation.target_home_cover is not None:
                    correct += int((margin > 0) == bool(evaluation.target_home_cover))
        correct_by_alpha[alpha] = correct

    highest_correct = max(correct_by_alpha.values())
    alpha = max(value for value, correct in correct_by_alpha.items() if correct == highest_correct)
    outer_cutoff = _outer_cutoff(dataset, test_season)
    residuals = _out_of_fold_residuals(partitions, alpha, outer_cutoff)
    return InnerSelection(
        alpha=alpha,
        correct_by_alpha=correct_by_alpha,
        residuals=sorted(residuals, key=lambda row: (row.error, row.weight)),
    )


def _outer_cutoff(dataset: ResidualDataset, test_season: int) -> datetime:
    """Find the fixed opening cutoff for a season-locked outer fold."""
    cutoffs = [
        row.kickoff_utc
        for row in [*dataset.training_rows, *dataset.evaluation_rows]
        if row.season == test_season
    ]
    if not cutoffs:
        raise ValueError("outer selection requires a cutoff in the test season")
    return min(cutoffs)


def _out_of_fold_residuals(
    partitions: Sequence[_InnerPartition], alpha: float, outer_cutoff: datetime
) -> list[WeightedResidual]:
    """Replay selected-alpha inner folds and reweight their prediction errors."""
    residuals: list[WeightedResidual] = []
    for partition in partitions:
        fit = _fit_residual_model(partition.training_rows, partition.cutoff_utc, alpha)
        weights = _recency_weights(partition.validation_rows, outer_cutoff)
        for row, weight in zip(partition.validation_rows, weights, strict=True):
            residuals.append(
                WeightedResidual(
                    error=row.market_error
                    - _predict_market_error(fit, row.home_team_id, row.away_team_id),
                    weight=float(weight),
                )
            )
    return residuals


def _probability_home(ats_margin: float, residuals: Sequence[WeightedResidual]) -> float:
    """Estimate home-cover probability from the smoothed weighted OOF survival curve."""
    if len(residuals) < 100:
        raise ValueError("probability calibration requires at least 100 OOF residuals")
    threshold = -ats_margin
    total = sum(row.weight for row in residuals)
    above = sum(row.weight for row in residuals if row.error > threshold)
    tied = sum(row.weight for row in residuals if row.error == threshold)
    return (0.5 + above + 0.5 * tied) / (1.0 + total)


def _group_by_game(lines: Sequence[MarketLine]) -> dict[str, list[MarketLine]]:
    grouped: dict[str, list[MarketLine]] = defaultdict(list)
    for line in lines:
        grouped[line.game_id].append(line)
    return grouped


def evaluate_residual_candidate(
    games: Sequence[Game],
    frozen_lines: Sequence[MarketLine],
    submission_lines: Sequence[MarketLine],
) -> ResidualEvaluation:
    """Evaluate Candidate 2 with one immutable residual fit per outer season."""
    if any(game.season < 2021 or game.season > 2025 for game in games):
        raise ValueError("residual evaluation only permits seasons 2021 through 2025")
    permitted_games = list(games)
    dataset = build_residual_dataset(permitted_games, frozen_lines, submission_lines)
    elo_sides = replay_elo_sides(permitted_games, frozen_lines, submission_lines)
    training_by_game = {row.game_id: row for row in dataset.training_rows}
    predictions: list[ResidualPrediction] = []
    folds: list[ResidualFold] = []

    for test_season in range(2022, 2026):
        test_rows = sorted(
            (row for row in dataset.evaluation_rows if row.season == test_season),
            key=lambda row: (row.week, row.game_id),
        )
        if not test_rows:
            continue
        cutoff = min(row.kickoff_utc for row in test_rows)
        selection = _select_alpha(dataset, test_season)
        train_rows = [
            row
            for row in dataset.training_rows
            if row.season < test_season and row.kickoff_utc < cutoff
        ]
        fit = _fit_residual_model(train_rows, cutoff, selection.alpha)
        folds.append(
            ResidualFold(
                train_from=min(row.season for row in train_rows),
                train_through=max(row.season for row in train_rows),
                test_season=test_season,
                cutoff_utc=cutoff,
                alpha=selection.alpha,
                fit=fit,
                residual_count=len(selection.residuals),
                correct_by_alpha=selection.correct_by_alpha,
            )
        )
        for row in test_rows:
            training = training_by_game.get(row.game_id)
            if training is None:
                raise ValueError(f"evaluation row {row.game_id} is missing its training market row")
            elo_side = elo_sides.get(row.game_id)
            if elo_side is None:
                raise ValueError(f"evaluation row {row.game_id} is missing its Elo replay side")
            predicted_error = _predict_market_error(
                fit, training.home_team_id, training.away_team_id
            )
            margin = row.frozen_spread - training.submission_spread + predicted_error
            candidate_side = Side.HOME if margin > 0 else Side.AWAY if margin < 0 else elo_side
            favorite_side = Side.HOME if row.frozen_spread <= 0 else Side.AWAY
            predictions.append(
                ResidualPrediction(
                    game_id=row.game_id,
                    season=row.season,
                    week=row.week,
                    kickoff_utc=row.kickoff_utc,
                    frozen_spread=row.frozen_spread,
                    submission_spread=training.submission_spread,
                    predicted_market_error=predicted_error,
                    predicted_ats_margin=margin,
                    probability_home=_probability_home(margin, selection.residuals),
                    candidate_side=candidate_side,
                    favorite_side=favorite_side,
                    elo_side=elo_side,
                    target_home_cover=row.target_home_cover,
                    is_push=row.is_push,
                    candidate_correct=_side_correct(candidate_side, row.target_home_cover),
                    favorite_correct=_side_correct(favorite_side, row.target_home_cover),
                    elo_correct=_side_correct(elo_side, row.target_home_cover),
                    always_home_correct=_side_correct(Side.HOME, row.target_home_cover),
                )
            )

    expected_ids = {row.game_id for row in dataset.evaluation_rows if 2022 <= row.season <= 2025}
    actual_ids = {prediction.game_id for prediction in predictions}
    if expected_ids != actual_ids:
        raise ValueError(
            "outer predictions must exactly match Candidate 1's paired population; "
            f"missing={sorted(expected_ids - actual_ids)}, "
            f"unexpected={sorted(actual_ids - expected_ids)}"
        )
    result = summarize_residual_predictions(
        predictions,
        folds,
        training_skipped=dataset.training_skipped,
        evaluation_skipped=dataset.evaluation_skipped,
    )
    return result.model_copy(
        update={
            "leakage_safe": _residual_folds_are_safe(result.predictions, result.folds),
            "uses_2026_outcomes": False,
        }
    )


def summarize_residual_predictions(
    predictions: Sequence[ResidualPrediction],
    folds: Sequence[ResidualFold],
    *,
    training_skipped: Sequence[str] = (),
    evaluation_skipped: Sequence[str] = (),
) -> ResidualEvaluation:
    """Summarize paired Candidate 2 predictions without changing their fold state."""
    ordered = sorted(predictions, key=lambda row: (row.season, row.week, row.game_id))
    decided = [
        row
        for row in ordered
        if not row.is_push
        and row.target_home_cover is not None
        and row.candidate_correct is not None
        and row.favorite_correct is not None
        and row.elo_correct is not None
        and row.always_home_correct is not None
    ]
    candidate_wins = sum(row.candidate_correct for row in decided)
    favorite_wins = sum(row.favorite_correct for row in decided)
    elo_wins = sum(row.elo_correct for row in decided)
    always_home_wins = sum(row.always_home_correct for row in decided)
    total = len(decided)
    candidate_accuracy = candidate_wins / total if total else None
    favorite_accuracy = favorite_wins / total if total else None
    elo_accuracy = elo_wins / total if total else None
    always_home_accuracy = always_home_wins / total if total else None
    scores = [row.probability_home for row in decided]
    if any(score is None for score in scores):
        raise ValueError("decided residual predictions must have a home-cover probability")
    probabilities = [float(score) for score in scores]
    targets = [int(row.target_home_cover) for row in decided]
    brier_score = (
        sum((score - target) ** 2 for score, target in zip(probabilities, targets, strict=True))
        / total
        if total
        else None
    )
    log_loss = (
        -sum(
            target * math.log(_clipped_probability(score))
            + (1 - target) * math.log(1 - _clipped_probability(score))
            for score, target in zip(probabilities, targets, strict=True)
        )
        / total
        if total
        else None
    )
    season_deltas = _residual_season_deltas(decided)
    calibration = _residual_calibration(probabilities, targets)
    bootstrap_lower, bootstrap_upper = _residual_bootstrap_interval(decided)
    calibration_safe = all(
        bin_.count < 100 or abs(float(bin_.mean_probability) - float(bin_.observed_rate)) <= 0.05
        for bin_ in calibration
    )
    return ResidualEvaluation(
        folds=list(folds),
        predictions=ordered,
        pushes=sum(row.is_push for row in ordered),
        candidate_wins=candidate_wins,
        candidate_losses=total - candidate_wins,
        favorite_wins=favorite_wins,
        favorite_losses=total - favorite_wins,
        elo_wins=elo_wins,
        elo_losses=total - elo_wins,
        always_home_wins=always_home_wins,
        always_home_losses=total - always_home_wins,
        candidate_accuracy=candidate_accuracy,
        favorite_accuracy=favorite_accuracy,
        elo_accuracy=elo_accuracy,
        always_home_accuracy=always_home_accuracy,
        accuracy_delta=(candidate_accuracy - favorite_accuracy if total else None),
        brier_score=brier_score,
        log_loss=log_loss,
        calibration=calibration,
        season_deltas=season_deltas,
        bootstrap_lower=bootstrap_lower,
        bootstrap_upper=bootstrap_upper,
        positive_seasons=sum(delta.candidate_minus_favorite > 0 for delta in season_deltas),
        calibration_safe=calibration_safe,
        training_skipped=sorted(training_skipped),
        evaluation_skipped=sorted(evaluation_skipped),
    )


def passes_residual_gate(result: ResidualEvaluation) -> bool:
    """Apply every frozen Candidate 2 acceptance condition, fail-closed."""
    return (
        result.accuracy_delta is not None
        and result.accuracy_delta >= 0.01
        and result.positive_seasons >= 3
        and result.bootstrap_lower is not None
        and result.bootstrap_lower > 0.0
        and result.brier_score is not None
        and result.brier_score < 0.25
        and result.calibration_safe
        and result.leakage_safe
        and result.deterministic
        and not result.uses_2026_outcomes
    )


def residual_evaluations_are_byte_identical(
    first: ResidualEvaluation, second: ResidualEvaluation
) -> bool:
    """Compare independent evaluations before Candidate 2 can be certified."""
    return (
        first.model_dump_json() == second.model_dump_json()
        and render_residual_predictions_jsonl(first) == render_residual_predictions_jsonl(second)
        and render_residual_report(first) == render_residual_report(second)
    )


def render_residual_predictions_jsonl(evaluation: ResidualEvaluation) -> str:
    """Serialize predictions as stable compact, sorted newline-delimited JSON."""
    return "".join(
        json.dumps(
            row.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
        for row in sorted(
            evaluation.predictions, key=lambda row: (row.season, row.week, row.game_id)
        )
    )


def render_residual_report(
    evaluation: ResidualEvaluation, *, prediction_sha256: str | None = None
) -> str:
    """Render a deterministic report containing all Candidate 2 audit evidence."""

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.2f}%"

    def number(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    prediction_hash = (
        prediction_sha256
        or hashlib.sha256(render_residual_predictions_jsonl(evaluation).encode()).hexdigest()
    )
    gate_rows = [
        (
            "accuracy delta >= 1.0 percentage point",
            evaluation.accuracy_delta is not None and evaluation.accuracy_delta >= 0.01,
            pct(evaluation.accuracy_delta),
        ),
        (
            "at least 3 positive seasons",
            evaluation.positive_seasons >= 3,
            str(evaluation.positive_seasons),
        ),
        (
            "bootstrap lower bound > 0",
            evaluation.bootstrap_lower is not None and evaluation.bootstrap_lower > 0,
            pct(evaluation.bootstrap_lower),
        ),
        (
            "Brier score < 0.25",
            evaluation.brier_score is not None and evaluation.brier_score < 0.25,
            number(evaluation.brier_score),
        ),
        ("calibration-safe", evaluation.calibration_safe, str(evaluation.calibration_safe).lower()),
        ("leakage-safe replay", evaluation.leakage_safe, str(evaluation.leakage_safe).lower()),
        (
            "deterministic execution",
            evaluation.deterministic,
            str(evaluation.deterministic).lower(),
        ),
        (
            "no 2026 outcomes",
            not evaluation.uses_2026_outcomes,
            str(not evaluation.uses_2026_outcomes).lower(),
        ),
    ]
    lines = [
        "# Candidate 2: CFB COINFLIP residual walk-forward evaluation",
        "",
        "## Folds",
        "",
        "| Training seasons | Test season | Cutoff | Alpha | Training rows | "
        "Effective weight | OOF residuals |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {fold.train_from}–{fold.train_through} | {fold.test_season} | "
        f"{fold.cutoff_utc.isoformat()} | {fold.alpha:g} | {fold.fit.training_rows} | "
        f"{fold.fit.effective_weight:.4f} | {fold.residual_count} |"
        for fold in evaluation.folds
    )
    lines.extend(["", "## Fitted fold/model state", "", "```json"])
    lines.extend(
        json.dumps(
            fold.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        for fold in evaluation.folds
    )
    lines.extend(["```", "", "## Paired outcomes", ""])
    for name, accuracy, wins, losses in (
        (
            "Candidate",
            evaluation.candidate_accuracy,
            evaluation.candidate_wins,
            evaluation.candidate_losses,
        ),
        (
            "Frozen-line favorite (primary)",
            evaluation.favorite_accuracy,
            evaluation.favorite_wins,
            evaluation.favorite_losses,
        ),
        ("Elo (diagnostic)", evaluation.elo_accuracy, evaluation.elo_wins, evaluation.elo_losses),
        (
            "Always home (diagnostic)",
            evaluation.always_home_accuracy,
            evaluation.always_home_wins,
            evaluation.always_home_losses,
        ),
    ):
        lines.append(f"- {name}: {pct(accuracy)} ({wins}-{losses})")
    lines.extend(
        [
            f"- Paired accuracy delta: {pct(evaluation.accuracy_delta)}",
            f"- Pushes: {evaluation.pushes}",
            f"- Brier score: {number(evaluation.brier_score)}",
            f"- Log loss: {number(evaluation.log_loss)}",
            "",
            "## Season deltas",
            "",
            "| Season | Candidate accuracy | Favorite accuracy | Candidate minus favorite |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    lines.extend(
        f"| {row.season} | {pct(row.candidate_accuracy)} | "
        f"{pct(row.favorite_accuracy)} | {pct(row.candidate_minus_favorite)} |"
        for row in evaluation.season_deltas
    )
    lines.extend(
        [
            "",
            "## Calibration",
            "",
            "| Probability bin | Count | Mean probability | Observed rate |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    lines.extend(
        f"| [{bin_.lower:.1f}, {bin_.upper:.1f}] | {bin_.count} | "
        f"{number(bin_.mean_probability)} | {number(bin_.observed_rate)} |"
        for bin_ in evaluation.calibration
    )
    lines.extend(
        [
            "",
            "## Bootstrap",
            "",
            "- Paired season/week-clustered 95% interval: "
            f"[{pct(evaluation.bootstrap_lower)}, {pct(evaluation.bootstrap_upper)}]",
            "",
            "## Acceptance gate",
            "",
            "| Condition | Result | Observed |",
            "| --- | --- | --- |",
        ]
    )
    lines.extend(
        f"| {condition} | {'PASS' if passed else 'FAIL'} | {observed} |"
        for condition, passed, observed in gate_rows
    )
    lines.extend(
        [
            "",
            "## Coverage and exclusions",
            "",
            "- Historical frozen and submission lines are Odds API proxies, "
            "not historical CBS lines.",
            f"- Prediction SHA-256: `{prediction_hash}`",
            f"- Outer-fold predictions: {len(evaluation.predictions)}",
            "",
            f"### Training exclusions ({len(evaluation.training_skipped)})",
            "",
        ]
    )
    lines.extend(f"- {reason}" for reason in evaluation.training_skipped) or lines.append("- none")
    lines.extend(["", f"### Evaluation exclusions ({len(evaluation.evaluation_skipped)})", ""])
    lines.extend(f"- {reason}" for reason in evaluation.evaluation_skipped) or lines.append(
        "- none"
    )
    lines.extend(
        [
            "",
            f"Candidate 2: {'PASS' if passes_residual_gate(evaluation) else 'NULL — retain Elo'}",
            "",
        ]
    )
    return "\n".join(lines)


def _side_correct(side: Side, target_home_cover: int | None) -> bool | None:
    if target_home_cover is None:
        return None
    return (side is Side.HOME) == bool(target_home_cover)


def _clipped_probability(probability: float) -> float:
    epsilon = np.finfo(float).eps
    return min(max(probability, epsilon), 1 - epsilon)


def _residual_calibration(
    probabilities: Sequence[float], targets: Sequence[int]
) -> list[CalibrationBin]:
    bins: list[CalibrationBin] = []
    for lower, upper in ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)):
        values = [
            (score, target)
            for score, target in zip(probabilities, targets, strict=True)
            if lower <= score < upper or (upper == 1.0 and score == 1.0)
        ]
        bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=len(values),
                mean_probability=(
                    sum(score for score, _ in values) / len(values) if values else None
                ),
                observed_rate=(
                    sum(target for _, target in values) / len(values) if values else None
                ),
            )
        )
    return bins


def _residual_season_deltas(
    predictions: Sequence[ResidualPrediction],
) -> list[ResidualSeasonDelta]:
    deltas: list[ResidualSeasonDelta] = []
    for season in sorted({row.season for row in predictions}):
        rows = [row for row in predictions if row.season == season]
        candidate_accuracy = sum(bool(row.candidate_correct) for row in rows) / len(rows)
        favorite_accuracy = sum(bool(row.favorite_correct) for row in rows) / len(rows)
        deltas.append(
            ResidualSeasonDelta(
                season=season,
                candidate_accuracy=candidate_accuracy,
                favorite_accuracy=favorite_accuracy,
                candidate_minus_favorite=candidate_accuracy - favorite_accuracy,
            )
        )
    return deltas


def _residual_bootstrap_interval(
    predictions: Sequence[ResidualPrediction],
) -> tuple[float | None, float | None]:
    if not predictions:
        return None, None
    clusters: dict[tuple[int, int], list[ResidualPrediction]] = defaultdict(list)
    for prediction in predictions:
        clusters[(prediction.season, prediction.week)].append(prediction)
    cluster_rows = [clusters[key] for key in sorted(clusters)]
    generator = np.random.default_rng(20260827)
    differences = np.empty(10_000, dtype=float)
    for index in range(len(differences)):
        sampled = [
            prediction
            for cluster_index in generator.integers(len(cluster_rows), size=len(cluster_rows))
            for prediction in cluster_rows[cluster_index]
        ]
        differences[index] = (
            sum(bool(row.candidate_correct) for row in sampled)
            - sum(bool(row.favorite_correct) for row in sampled)
        ) / len(sampled)
    lower, upper = np.percentile(differences, (2.5, 97.5))
    return float(lower), float(upper)


def _residual_folds_are_safe(
    predictions: Sequence[ResidualPrediction], folds: Sequence[ResidualFold]
) -> bool:
    if not folds or not predictions:
        return False
    expected_train_through = {2022: 2021, 2023: 2022, 2024: 2023, 2025: 2024}
    by_test_season = {fold.test_season: fold for fold in folds}
    if len(by_test_season) != len(folds) or set(by_test_season) != set(expected_train_through):
        return False
    if {prediction.season for prediction in predictions} != set(expected_train_through):
        return False
    for fold in folds:
        fold_predictions = [
            prediction for prediction in predictions if prediction.season == fold.test_season
        ]
        if (
            fold.train_from > fold.train_through
            or fold.train_through >= fold.test_season
            or fold.train_through != expected_train_through[fold.test_season]
            or fold.fit.cutoff_utc != fold.cutoff_utc
            or fold.residual_count < 100
            or not fold_predictions
            or fold.cutoff_utc != min(prediction.kickoff_utc for prediction in fold_predictions)
        ):
            return False
    return all(
        prediction.season in by_test_season
        and prediction.kickoff_utc >= by_test_season[prediction.season].cutoff_utc
        for prediction in predictions
    )
