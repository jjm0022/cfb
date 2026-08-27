"""Build audit-ready, pre-kickoff feature rows for CFB COINFLIP games."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from statistics import fmean

import numpy as np
from pydantic import BaseModel
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from pickem.edge.divergence import consensus_spread
from pickem.edge.pipeline import decide_edges
from pickem.models import Game, LeagueLine, MarketLine, Side, Sport, Tier

_COINFLIP_BOUNDARY = 1.0


class CoinflipRow(BaseModel):
    game_id: str
    season: int
    week: int
    kickoff_utc: datetime
    frozen_spread: float
    median_delta: float
    mean_delta: float
    book_balance: float
    target_home_cover: int | None
    is_push: bool


class CoinflipDataset(BaseModel):
    rows: list[CoinflipRow]
    skipped: list[str]


class CoinflipPrediction(BaseModel):
    game_id: str
    season: int
    week: int
    frozen_spread: float
    median_delta: float
    mean_delta: float
    book_balance: float
    probability_home: float | None
    candidate_side: Side
    elo_side: Side
    target_home_cover: int | None
    is_push: bool
    candidate_correct: bool | None
    elo_correct: bool | None
    agrees_with_elo: bool


class CoinflipFold(BaseModel):
    train_from: int
    train_through: int
    test_season: int
    selected_c: float
    means: list[float]
    scales: list[float]
    coefficients: list[float]
    intercept: float


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    count: int
    mean_probability: float | None
    observed_rate: float | None


class SeasonDelta(BaseModel):
    season: int
    candidate_accuracy: float
    elo_accuracy: float
    candidate_minus_elo: float


class CoinflipEvaluation(BaseModel):
    folds: list[CoinflipFold]
    predictions: list[CoinflipPrediction]
    pushes: int
    candidate_wins: int = 0
    candidate_losses: int = 0
    elo_wins: int = 0
    elo_losses: int = 0
    candidate_accuracy: float | None = None
    elo_accuracy: float | None = None
    accuracy_delta: float | None = None
    brier_score: float | None = None
    log_loss: float | None = None
    calibration: list[CalibrationBin] = []
    season_deltas: list[SeasonDelta] = []
    bootstrap_lower: float | None = None
    bootstrap_upper: float | None = None
    positive_seasons: int = 0
    leakage_safe: bool = False
    deterministic: bool = False


def evaluate_coinflip(
    rows: Sequence[CoinflipRow],
    elo_sides: dict[str, Side],
    c_grid: Sequence[float] = (0.1, 1.0, 10.0),
) -> CoinflipEvaluation:
    """Evaluate the fixed CFB candidate on chronological, paired outer folds."""
    if not c_grid:
        raise ValueError("c_grid must contain at least one C value")
    if any(c <= 0 for c in c_grid):
        raise ValueError("every C value must be positive")

    ordered_rows = sorted(rows, key=lambda row: (row.season, row.week, row.game_id))
    seasons = sorted({row.season for row in ordered_rows})
    if len(seasons) < 2:
        raise ValueError("walk-forward evaluation requires at least two seasons")
    prediction_ids = {row.game_id for row in ordered_rows if row.season != seasons[0]}
    if prediction_ids != set(elo_sides):
        raise ValueError("outer-test rows and elo_sides must contain exactly the same game IDs")

    folds: list[CoinflipFold] = []
    predictions: list[CoinflipPrediction] = []
    for test_season in seasons[1:]:
        train_rows = [row for row in ordered_rows if row.season < test_season and not row.is_push]
        test_rows = [row for row in ordered_rows if row.season == test_season]
        if not test_rows:
            continue
        selected_c = _select_c(train_rows, test_season, c_grid)
        pipeline = _fit_pipeline(
            train_rows, selected_c, f"outer training through {test_season - 1}"
        )
        scaler: StandardScaler = pipeline.named_steps["scale"]
        model: LogisticRegression = pipeline.named_steps["model"]
        folds.append(
            CoinflipFold(
                train_from=min(row.season for row in train_rows),
                train_through=max(row.season for row in train_rows),
                test_season=test_season,
                selected_c=selected_c,
                means=[float(value) for value in scaler.mean_],
                scales=[float(value) for value in scaler.scale_],
                coefficients=[float(value) for value in model.coef_[0]],
                intercept=float(model.intercept_[0]),
            )
        )
        probabilities = pipeline.predict_proba(_features(test_rows))[:, 1]
        # Historical candidate scoring deliberately retains the estimator's
        # class prediction at exactly 0.5.  This keeps Candidate 1 independent
        # of the Elo comparator it is measured against.  Prospective live
        # artifact inference, if authorized, instead delegates an exact half
        # probability to Elo (design §2); that path is not this evaluator.
        candidate_labels = pipeline.predict(_features(test_rows))
        for row, probability, candidate_label in zip(
            test_rows, probabilities, candidate_labels, strict=True
        ):
            elo_side = elo_sides[row.game_id]
            candidate_side = Side.HOME if candidate_label == 1 else Side.AWAY
            candidate_correct = _is_correct(candidate_side, row.target_home_cover)
            elo_correct = _is_correct(elo_side, row.target_home_cover)
            predictions.append(
                CoinflipPrediction(
                    game_id=row.game_id,
                    season=row.season,
                    week=row.week,
                    frozen_spread=row.frozen_spread,
                    median_delta=row.median_delta,
                    mean_delta=row.mean_delta,
                    book_balance=row.book_balance,
                    probability_home=float(probability),
                    candidate_side=candidate_side,
                    elo_side=elo_side,
                    target_home_cover=row.target_home_cover,
                    is_push=row.is_push,
                    candidate_correct=candidate_correct,
                    elo_correct=elo_correct,
                    agrees_with_elo=candidate_side is elo_side,
                )
            )

    return summarize_coinflip_predictions(predictions, folds)


def summarize_coinflip_predictions(
    predictions: Sequence[CoinflipPrediction], folds: Sequence[CoinflipFold]
) -> CoinflipEvaluation:
    """Compute paired accuracy and probability diagnostics from outer-fold rows."""
    ordered = sorted(predictions, key=lambda row: (row.season, row.week, row.game_id))
    decided = [
        prediction
        for prediction in ordered
        if not prediction.is_push
        and prediction.target_home_cover is not None
        and prediction.candidate_correct is not None
        and prediction.elo_correct is not None
    ]
    candidate_wins = sum(prediction.candidate_correct for prediction in decided)
    elo_wins = sum(prediction.elo_correct for prediction in decided)
    candidate_accuracy = candidate_wins / len(decided) if decided else None
    elo_accuracy = elo_wins / len(decided) if decided else None
    accuracy_delta = (
        candidate_accuracy - elo_accuracy
        if candidate_accuracy is not None and elo_accuracy is not None
        else None
    )
    probabilities = [prediction.probability_home for prediction in decided]
    if any(probability is None for probability in probabilities):
        raise ValueError("decided predictions must have a home-cover probability")
    scores = [float(probability) for probability in probabilities]
    targets = [int(prediction.target_home_cover) for prediction in decided]
    brier_score = (
        sum(
            (probability - target) ** 2 for probability, target in zip(scores, targets, strict=True)
        )
        / len(scores)
        if scores
        else None
    )
    log_loss = (
        -sum(
            target * math.log(_clipped_probability(probability))
            + (1 - target) * math.log(1 - _clipped_probability(probability))
            for probability, target in zip(scores, targets, strict=True)
        )
        / len(scores)
        if scores
        else None
    )
    season_deltas = _season_deltas(decided)
    bootstrap_lower, bootstrap_upper = _bootstrap_interval(decided)
    return CoinflipEvaluation(
        folds=list(folds),
        predictions=ordered,
        pushes=sum(prediction.is_push for prediction in ordered),
        candidate_wins=candidate_wins,
        candidate_losses=len(decided) - candidate_wins,
        elo_wins=elo_wins,
        elo_losses=len(decided) - elo_wins,
        candidate_accuracy=candidate_accuracy,
        elo_accuracy=elo_accuracy,
        accuracy_delta=accuracy_delta,
        brier_score=brier_score,
        log_loss=log_loss,
        calibration=_calibration(scores, targets),
        season_deltas=season_deltas,
        bootstrap_lower=bootstrap_lower,
        bootstrap_upper=bootstrap_upper,
        positive_seasons=sum(delta.candidate_minus_elo > 0 for delta in season_deltas),
        leakage_safe=_has_safe_fold_boundaries(ordered, folds),
    )


def passes_acceptance_gate(evaluation: CoinflipEvaluation) -> bool:
    """Apply the frozen Candidate 1 acceptance requirements without tuning them."""
    return (
        evaluation.accuracy_delta is not None
        and evaluation.accuracy_delta >= 0.01
        and evaluation.positive_seasons >= 3
        and evaluation.brier_score is not None
        and evaluation.brier_score < 0.25
        and evaluation.leakage_safe
        and evaluation.deterministic
    )


def evaluations_are_byte_identical(first: CoinflipEvaluation, second: CoinflipEvaluation) -> bool:
    """Compare independently evaluated candidate results before certification."""
    return first.model_dump_json() == second.model_dump_json() and render_predictions_jsonl(
        first
    ) == render_predictions_jsonl(second)


def replay_elo_sides(
    games: Sequence[Game], frozen: Sequence[MarketLine], submission: Sequence[MarketLine]
) -> dict[str, Side]:
    """Replay the live CFB Elo tiebreak, retaining only eligible COINFLIPs.

    The feature builder establishes the candidate population, while this
    replay deliberately calls the same final-decision pipeline used by the
    weekly report. Week results are appended only after all that week's
    decisions, preserving the pre-kickoff information boundary.
    """
    feature_by_id = {
        row.game_id: row for row in build_coinflip_rows(games, frozen, submission).rows
    }
    feature_ids = set(feature_by_id)
    frozen_by_game = _group_by_game(frozen)
    submission_by_game = _group_by_game(submission)
    by_week: dict[tuple[int, int], list[Game]] = defaultdict(list)
    for game in sorted(games, key=lambda game: (game.season, game.week, game.game_id)):
        by_week[(game.season, game.week)].append(game)

    sides: dict[str, Side] = {}
    history: list[Game] = []
    for key in sorted(by_week):
        week_games = by_week[key]
        league_lines: list[LeagueLine] = []
        for game in week_games:
            if game.game_id not in feature_ids:
                continue
            game_frozen = _pre_kickoff_lines(frozen_by_game.get(game.game_id, []), game.kickoff_utc)
            frozen_spread = consensus_spread(game_frozen)
            assert frozen_spread is not None  # guaranteed by build_coinflip_rows
            league_lines.append(
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=frozen_spread,
                    posted_at=max(line.captured_at for line in game_frozen),
                )
            )
        week_submission = [
            line
            for game in week_games
            if game.game_id in feature_ids
            for line in _pre_kickoff_lines(
                submission_by_game.get(game.game_id, []), game.kickoff_utc
            )
        ]
        for edge in decide_edges(league_lines, week_submission, week_games, history):
            if edge.tier is Tier.COINFLIP and edge.game_id in feature_ids:
                sides[edge.game_id] = edge.side
        history.extend(week_games)

    if set(sides) != feature_ids:
        missing = sorted(feature_ids - set(sides))
        unexpected = sorted(set(sides) - feature_ids)
        raise ValueError(
            "Elo replay and feature rows must contain exactly the same game IDs; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return {game_id: sides[game_id] for game_id in sorted(sides)}


def render_predictions_jsonl(evaluation: CoinflipEvaluation) -> str:
    """Serialize sorted outer-fold predictions as stable newline-delimited JSON."""
    rows = sorted(evaluation.predictions, key=lambda row: (row.season, row.week, row.game_id))
    return "".join(
        json.dumps(
            row.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
        for row in rows
    )


def render_coinflip_report(
    evaluation: CoinflipEvaluation,
    *,
    prediction_sha256: str | None = None,
    feature_rows: int | None = None,
    feature_skipped: Sequence[str] = (),
    proxy_skipped: Sequence[str] = (),
) -> str:
    """Render the frozen Candidate 1 audit report without wall-clock metadata."""

    def number(value: float | None, digits: int = 4) -> str:
        return "n/a" if value is None else f"{value:.{digits}f}"

    def percentage(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.2f}%"

    gate = passes_acceptance_gate(evaluation)
    gate_rows = [
        (
            "accuracy delta >= 1.0 percentage point",
            evaluation.accuracy_delta is not None and evaluation.accuracy_delta >= 0.01,
            percentage(evaluation.accuracy_delta),
        ),
        (
            "at least 3 positive seasons",
            evaluation.positive_seasons >= 3,
            str(evaluation.positive_seasons),
        ),
        (
            "Brier score < 0.25",
            evaluation.brier_score is not None and evaluation.brier_score < 0.25,
            number(evaluation.brier_score),
        ),
        ("leakage-safe replay", evaluation.leakage_safe, str(evaluation.leakage_safe).lower()),
        (
            "deterministic execution",
            evaluation.deterministic,
            str(evaluation.deterministic).lower(),
        ),
    ]
    lines = [
        "# Candidate 1: CFB COINFLIP walk-forward evaluation",
        "",
        "## Folds",
        "",
        "| Training seasons | Test season | Chosen C |",
        "| --- | ---: | ---: |",
    ]
    lines.extend(
        f"| {fold.train_from}–{fold.train_through} | {fold.test_season} | {fold.selected_c:g} |"
        for fold in evaluation.folds
    )
    lines.extend(["", "## Fitted fold/model state", "", "```json"])
    lines.extend(
        json.dumps(
            fold.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        for fold in evaluation.folds
    )
    lines.extend(["```", ""])
    lines.extend(
        [
            "",
            "## Paired outcomes",
            "",
            f"- Candidate accuracy: {percentage(evaluation.candidate_accuracy)} "
            f"({evaluation.candidate_wins}-{evaluation.candidate_losses})",
            f"- Elo accuracy: {percentage(evaluation.elo_accuracy)} "
            f"({evaluation.elo_wins}-{evaluation.elo_losses})",
            f"- Paired accuracy delta: {percentage(evaluation.accuracy_delta)}",
            f"- Pushes: {evaluation.pushes}",
            f"- Brier score: {number(evaluation.brier_score)}",
            f"- Log loss: {number(evaluation.log_loss)}",
            "",
            "## Season deltas",
            "",
            "| Season | Candidate accuracy | Elo accuracy | Candidate minus Elo |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    lines.extend(
        f"| {delta.season} | {percentage(delta.candidate_accuracy)} | "
        f"{percentage(delta.elo_accuracy)} | {percentage(delta.candidate_minus_elo)} |"
        for delta in evaluation.season_deltas
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
            "- Bootstrap 95% interval: "
            f"[{percentage(evaluation.bootstrap_lower)}, {percentage(evaluation.bootstrap_upper)}]",
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
            f"- Prediction SHA-256: `{prediction_sha256 or 'n/a'}`",
            f"- Eligible feature rows: {feature_rows if feature_rows is not None else 'n/a'}",
            f"- Outer-fold predictions: {len(evaluation.predictions)}",
            "",
            f"### Feature-row exclusions ({len(feature_skipped)})",
            "",
        ]
    )
    lines.extend(f"- {reason}" for reason in sorted(feature_skipped))
    if not feature_skipped:
        lines.append("- none")
    lines.extend(["", f"### Stored proxy exclusions ({len(proxy_skipped)})", ""])
    lines.extend(f"- {reason}" for reason in sorted(proxy_skipped))
    if not proxy_skipped:
        lines.append("- none")
    lines.extend(["", f"Candidate 1: {'PASS' if gate else 'NULL — retain Elo'}", ""])
    return "\n".join(lines)


def _has_safe_fold_boundaries(
    predictions: Sequence[CoinflipPrediction], folds: Sequence[CoinflipFold]
) -> bool:
    """Verify that every prediction belongs to a strictly prior-trained fold."""
    if not folds:
        return False
    by_test_season = {fold.test_season: fold for fold in folds}
    if len(by_test_season) != len(folds):
        return False
    if any(
        fold.train_from > fold.train_through or fold.train_through >= fold.test_season
        for fold in folds
    ):
        return False
    return bool(predictions) and all(
        prediction.season in by_test_season for prediction in predictions
    )


def _calibration(scores: Sequence[float], targets: Sequence[int]) -> list[CalibrationBin]:
    bins: list[CalibrationBin] = []
    for lower, upper in ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)):
        values = [
            (score, target)
            for score, target in zip(scores, targets, strict=True)
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


def _clipped_probability(probability: float) -> float:
    epsilon = np.finfo(float).eps
    return min(max(probability, epsilon), 1 - epsilon)


def _season_deltas(predictions: Sequence[CoinflipPrediction]) -> list[SeasonDelta]:
    deltas: list[SeasonDelta] = []
    for season in sorted({prediction.season for prediction in predictions}):
        rows = [prediction for prediction in predictions if prediction.season == season]
        candidate_accuracy = sum(row.candidate_correct for row in rows) / len(rows)
        elo_accuracy = sum(row.elo_correct for row in rows) / len(rows)
        deltas.append(
            SeasonDelta(
                season=season,
                candidate_accuracy=candidate_accuracy,
                elo_accuracy=elo_accuracy,
                candidate_minus_elo=candidate_accuracy - elo_accuracy,
            )
        )
    return deltas


def _bootstrap_interval(
    predictions: Sequence[CoinflipPrediction],
) -> tuple[float | None, float | None]:
    if not predictions:
        return None, None
    clusters: dict[tuple[int, int], list[CoinflipPrediction]] = defaultdict(list)
    for prediction in predictions:
        clusters[(prediction.season, prediction.week)].append(prediction)
    cluster_rows = [clusters[key] for key in sorted(clusters)]
    generator = np.random.default_rng(20260825)
    differences = np.empty(10_000, dtype=float)
    for index in range(len(differences)):
        sampled = [
            prediction
            for cluster_index in generator.integers(len(cluster_rows), size=len(cluster_rows))
            for prediction in cluster_rows[cluster_index]
        ]
        candidate_accuracy = sum(prediction.candidate_correct for prediction in sampled) / len(
            sampled
        )
        elo_accuracy = sum(prediction.elo_correct for prediction in sampled) / len(sampled)
        differences[index] = candidate_accuracy - elo_accuracy
    lower, upper = np.percentile(differences, (2.5, 97.5))
    return float(lower), float(upper)


def _select_c(
    train_rows: Sequence[CoinflipRow], test_season: int, c_grid: Sequence[float]
) -> float:
    partitions = _inner_partitions(train_rows, test_season)
    if not partitions:
        raise ValueError("at least one inner validation partition is required to select C")
    scores: list[tuple[int, float]] = []
    for c_value in sorted(set(c_grid)):
        correct = 0
        for inner_train, validation in partitions:
            pipeline = _fit_pipeline(inner_train, c_value, "inner training")
            predicted = pipeline.predict(_features(validation))
            targets = _targets(validation, "inner validation")
            correct += int(np.sum(predicted == targets))
        scores.append((correct, float(c_value)))
    highest_correct = max(score for score, _ in scores)
    return min(c_value for score, c_value in scores if score == highest_correct)


def _inner_partitions(
    train_rows: Sequence[CoinflipRow], test_season: int
) -> list[tuple[list[CoinflipRow], list[CoinflipRow]]]:
    if test_season == min(row.season for row in train_rows) + 1:
        earliest = min(row.season for row in train_rows)
        inner_train = [row for row in train_rows if row.season == earliest and row.week <= 8]
        validation = [row for row in train_rows if row.season == earliest and 9 <= row.week <= 15]
        _targets(validation, "2021 weeks 9-15 validation")
        return [(inner_train, validation)]

    partitions: list[tuple[list[CoinflipRow], list[CoinflipRow]]] = []
    for validation_season in sorted({row.season for row in train_rows})[1:]:
        inner_train = [row for row in train_rows if row.season < validation_season]
        validation = [row for row in train_rows if row.season == validation_season]
        _targets(validation, f"{validation_season} inner validation")
        partitions.append((inner_train, validation))
    return partitions


def _fit_pipeline(rows: Sequence[CoinflipRow], selected_c: float, partition: str) -> Pipeline:
    targets = _targets(rows, partition)
    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    penalty="l2",
                    C=selected_c,
                    solver="lbfgs",
                    class_weight=None,
                    max_iter=1000,
                    random_state=0,
                ),
            ),
        ]
    ).fit(_features(rows), targets)


def _features(rows: Sequence[CoinflipRow]) -> np.ndarray:
    return np.asarray(
        [[row.median_delta, row.mean_delta, row.book_balance] for row in rows], dtype=float
    )


def _targets(rows: Sequence[CoinflipRow], partition: str) -> np.ndarray:
    targets = [row.target_home_cover for row in rows if not row.is_push]
    if len(targets) != len(rows) or set(targets) != {0, 1}:
        raise ValueError(f"{partition} must contain both target classes and no pushes")
    return np.asarray(targets, dtype=int)


def _is_correct(side: Side, target_home_cover: int | None) -> bool | None:
    if target_home_cover is None:
        return None
    return (side is Side.HOME) == bool(target_home_cover)


def latest_by_book(lines: Sequence[MarketLine]) -> list[MarketLine]:
    """Choose a deterministic latest quote from every book.

    The market API records append-only snapshots. A frequently sampled book
    therefore gets one vote, never one vote per archived snapshot. Spread is
    a deterministic tie-breaker for otherwise indistinguishable timestamps.
    """
    latest: dict[str, MarketLine] = {}
    for line in sorted(lines, key=lambda line: (line.book, line.captured_at, line.spread_home)):
        latest[line.book] = line
    return [latest[book] for book in sorted(latest)]


def build_coinflip_rows(
    games: Sequence[Game],
    frozen: Sequence[MarketLine],
    submission: Sequence[MarketLine],
) -> CoinflipDataset:
    """Turn completed, eligible CFB games into leakage-safe model rows.

    The median feature deliberately uses the live strategy's
    :func:`consensus_spread`, keeping experiment membership aligned with the
    shipped COINFLIP definition. This function performs no I/O, so a caller can
    replay the identical archive independently of the database.
    """
    frozen_by_game = _group_by_game(frozen)
    submission_by_game = _group_by_game(submission)
    rows: list[CoinflipRow] = []
    skipped: list[str] = []

    for game in sorted(games, key=lambda game: (game.season, game.week, game.game_id)):
        frozen_lines = frozen_by_game.get(game.game_id, [])
        submission_lines = submission_by_game.get(game.game_id, [])

        if game.sport is not Sport.CFB:
            skipped.append(f"{game.game_id}: non-CFB game is outside the experiment population")
            continue
        if game.home_score is None or game.away_score is None:
            skipped.append(f"{game.game_id}: unplayed game (missing final score)")
            continue
        frozen_pre_kickoff = _pre_kickoff_lines(frozen_lines, game.kickoff_utc)
        submission_pre_kickoff = _pre_kickoff_lines(submission_lines, game.kickoff_utc)
        if not frozen_pre_kickoff:
            skipped.append(f"{game.game_id}: missing frozen-line snapshot before kickoff")
            continue
        if not submission_pre_kickoff:
            skipped.append(f"{game.game_id}: missing submission-time snapshot before kickoff")
            continue
        if not _all_spreads_finite([*frozen_pre_kickoff, *submission_pre_kickoff]):
            skipped.append(f"{game.game_id}: non-finite market spread")
            continue

        frozen_latest = latest_by_book(frozen_pre_kickoff)
        submission_latest = latest_by_book(submission_pre_kickoff)
        frozen_spread = consensus_spread(frozen_latest)
        market_median = consensus_spread(submission_latest)
        if frozen_spread is None:
            skipped.append(f"{game.game_id}: missing frozen-line books")
            continue
        if market_median is None:
            skipped.append(f"{game.game_id}: missing submission-time books")
            continue

        market_spreads = [line.spread_home for line in submission_latest]
        median_delta = frozen_spread - market_median
        mean_delta = frozen_spread - fmean(market_spreads)
        book_balance = _book_balance(frozen_spread, market_spreads)
        features = (frozen_spread, median_delta, mean_delta, book_balance)
        if not all(math.isfinite(value) for value in features):
            skipped.append(f"{game.game_id}: non-finite feature")
            continue
        if abs(median_delta) >= _COINFLIP_BOUNDARY:
            skipped.append(
                f"{game.game_id}: not coinflip (median delta {median_delta:+.3f} is outside "
                f"the {_COINFLIP_BOUNDARY:.1f}-point boundary)"
            )
            continue

        adjusted_margin = game.home_score - game.away_score + frozen_spread
        is_push = adjusted_margin == 0
        rows.append(
            CoinflipRow(
                game_id=game.game_id,
                season=game.season,
                week=game.week,
                kickoff_utc=game.kickoff_utc,
                frozen_spread=frozen_spread,
                median_delta=median_delta,
                mean_delta=mean_delta,
                book_balance=book_balance,
                target_home_cover=None if is_push else int(adjusted_margin > 0),
                is_push=is_push,
            )
        )

    return CoinflipDataset(rows=rows, skipped=skipped)


def _group_by_game(lines: Sequence[MarketLine]) -> dict[str, list[MarketLine]]:
    grouped: dict[str, list[MarketLine]] = defaultdict(list)
    for line in lines:
        grouped[line.game_id].append(line)
    return grouped


def _pre_kickoff_lines(lines: Sequence[MarketLine], kickoff: datetime) -> list[MarketLine]:
    """Exclude in-play history before selecting each book's latest quote."""
    return [line for line in lines if line.captured_at < kickoff]


def _all_spreads_finite(lines: Sequence[MarketLine]) -> bool:
    return all(math.isfinite(line.spread_home) for line in lines)


def _book_balance(frozen_spread: float, market_spreads: Sequence[float]) -> float:
    deltas = [frozen_spread - spread for spread in market_spreads]
    positive = sum(delta > 0 for delta in deltas)
    negative = sum(delta < 0 for delta in deltas)
    return (positive - negative) / len(deltas)
