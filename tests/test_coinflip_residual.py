"""Contract tests for Candidate 2's leakage-safe residual rows."""

from datetime import UTC, datetime, timedelta

import pytest

import pickem.backtest.coinflip_residual as residual_module
from pickem.backtest.coinflip import CoinflipRow, build_coinflip_rows
from pickem.backtest.coinflip_residual import (
    InnerSelection,
    ResidualEvaluation,
    ResidualFold,
    ResidualPrediction,
    ResidualTrainingRow,
    WeightedResidual,
    _fit_residual_model,
    _inner_partitions,
    _predict_market_error,
    _probability_home,
    _recency_weights,
    _select_alpha,
    build_residual_dataset,
    evaluate_residual_candidate,
    passes_residual_gate,
    summarize_residual_predictions,
)
from pickem.models import Game, MarketLine, Side, Sport

KICKOFF = datetime(2025, 9, 6, 17, tzinfo=UTC)
GAME = Game(
    game_id="cfb-2025-02-AWAY-at-HOME",
    sport=Sport.CFB,
    season=2025,
    week=2,
    kickoff_utc=KICKOFF,
    home_team_id="HOME",
    away_team_id="AWAY",
    home_score=27,
    away_score=24,
)
GAMES = [GAME]
FROZEN = [
    MarketLine(
        game_id=GAME.game_id,
        source="oddsapi:frozen",
        book="a",
        spread_home=-2.5,
        captured_at=KICKOFF - timedelta(days=4),
    )
]
SUBMISSION = [
    MarketLine(
        game_id=GAME.game_id,
        source="oddsapi:submit",
        book="a",
        spread_home=-4.0,
        captured_at=KICKOFF - timedelta(hours=2),
    ),
    MarketLine(
        game_id=GAME.game_id,
        source="oddsapi:submit",
        book="a",
        spread_home=-2.0,
        captured_at=KICKOFF - timedelta(minutes=15),
    ),
    MarketLine(
        game_id=GAME.game_id,
        source="oddsapi:submit",
        book="b",
        spread_home=-2.0,
        captured_at=KICKOFF - timedelta(minutes=15),
    ),
]

CUTOFF = datetime(2025, 8, 25, tzinfo=UTC)


def training_row(
    *,
    kickoff: datetime,
    home_team_id: str = "KNOWN_HOME",
    away_team_id: str = "KNOWN_AWAY",
    market_error: float = 1.0,
) -> ResidualTrainingRow:
    """Create a residual row with only the varied fitting inputs exposed."""
    return ResidualTrainingRow(
        game_id=f"{home_team_id}-at-{away_team_id}-{kickoff.isoformat()}",
        season=kickoff.year,
        week=1,
        kickoff_utc=kickoff,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        submission_spread=-3.0,
        market_error=market_error,
    )


SYMMETRIC_ROWS = [
    training_row(
        kickoff=CUTOFF - timedelta(days=1),
        home_team_id="KNOWN_HOME",
        away_team_id="KNOWN_AWAY",
        market_error=3.0,
    ),
    training_row(
        kickoff=CUTOFF - timedelta(days=1),
        home_team_id="KNOWN_AWAY",
        away_team_id="KNOWN_HOME",
        market_error=-3.0,
    ),
]


def residual_dataset_row(season: int, week: int, index: int) -> ResidualTrainingRow:
    """Create an eligible historical residual row for chronological tests."""
    kickoff = datetime(season, 9, min(week, 28), 17, tzinfo=UTC)
    return ResidualTrainingRow(
        game_id=f"cfb-{season}-{week:02d}-{index}",
        season=season,
        week=week,
        kickoff_utc=kickoff,
        home_team_id="HOME",
        away_team_id="AWAY",
        submission_spread=-3.0,
        market_error=0.0,
    )


def residual_evaluation_row(row: ResidualTrainingRow) -> CoinflipRow:
    """Create a COINFLIP row aligned with a residual training-population row."""
    return CoinflipRow(
        game_id=row.game_id,
        season=row.season,
        week=row.week,
        kickoff_utc=row.kickoff_utc,
        frozen_spread=-2.5,
        median_delta=0.5,
        mean_delta=0.5,
        book_balance=1.0,
        target_home_cover=1,
        is_push=False,
    )


def stored_games_for(rows: list[CoinflipRow]) -> list[Game]:
    """Create stored-game inputs whose kickoff timestamps match evaluation rows."""
    return [
        Game(
            game_id=f"stored-{row.game_id}",
            sport=Sport.CFB,
            season=row.season,
            week=row.week,
            kickoff_utc=row.kickoff_utc,
            home_team_id="STORED_HOME",
            away_team_id="STORED_AWAY",
        )
        for row in rows
    ]


DATASET_ROWS = [
    residual_dataset_row(season, week, index)
    for season in range(2021, 2026)
    for week, index in ((1, 0), (2, 1), (9, 2), (10, 3))
]
DATASET = build_residual_dataset([], [], []).model_copy(
    update={
        "training_rows": DATASET_ROWS,
        "evaluation_rows": [residual_evaluation_row(row) for row in DATASET_ROWS],
    }
)
TIED_DATASET = DATASET


def test_residual_dataset_uses_market_error_and_reuses_coinflip_population():
    """Catches market-error targets or Candidate 1 membership being recomputed differently."""
    dataset = build_residual_dataset(GAMES, FROZEN, SUBMISSION)

    row = next(row for row in dataset.training_rows if row.game_id == GAME.game_id)
    assert row.submission_spread == -2.0
    assert row.market_error == (27 - 24) + (-2.0)
    assert {row.game_id for row in dataset.evaluation_rows} == {
        row.game_id for row in build_coinflip_rows(GAMES, FROZEN, SUBMISSION).rows
    }


def test_training_keeps_an_ats_push_as_a_continuous_market_error():
    """Catches ATS pushes being dropped despite their valid continuous training target."""
    pushed = GAME.model_copy(update={"home_score": 20, "away_score": 17})

    [row] = build_residual_dataset([pushed], FROZEN, SUBMISSION).training_rows

    assert row.market_error == 1.0


def test_training_ignores_in_play_quotes_and_reports_missing_consensus():
    """Catches in-play submission quotes leaking into a residual training row."""
    in_play = SUBMISSION[0].model_copy(update={"captured_at": GAME.kickoff_utc})

    result = build_residual_dataset([GAME], FROZEN, [in_play])

    assert result.training_rows == []
    assert result.training_skipped == [
        f"{GAME.game_id}: missing submission consensus before kickoff"
    ]


def test_recency_weight_has_the_frozen_365_day_half_life():
    """Catches a changed or incorrectly applied recency half-life."""
    cutoff = datetime(2025, 8, 25, tzinfo=UTC)
    rows = [
        training_row(kickoff=cutoff - timedelta(seconds=1)),
        training_row(kickoff=cutoff - timedelta(days=365)),
    ]

    assert _recency_weights(rows, cutoff).tolist() == pytest.approx([1.0, 0.5])


def test_team_encoding_is_home_plus_one_away_minus_one_and_unknown_zero():
    """Catches reversed, unsorted, or nonzero unknown-team residual predictions."""
    fit = _fit_residual_model(SYMMETRIC_ROWS, CUTOFF, alpha=30.0)

    assert fit.teams == sorted(fit.teams)
    assert _predict_market_error(fit, "KNOWN_HOME", "KNOWN_AWAY") == pytest.approx(
        fit.intercept + fit.team_effects["KNOWN_HOME"] - fit.team_effects["KNOWN_AWAY"]
    )
    assert _predict_market_error(fit, "NEW_HOME", "NEW_AWAY") == pytest.approx(fit.intercept)


def test_fit_rejects_rows_at_or_after_its_cutoff():
    """Catches same-time or future rows being included in a leakage-safe fit."""
    with pytest.raises(ValueError, match="strictly before fit cutoff"):
        _fit_residual_model([training_row(kickoff=CUTOFF)], CUTOFF, alpha=30.0)


def test_first_outer_fold_uses_early_2021_to_validate_late_2021():
    """Catches a first outer fold that uses late-2021 outcomes while fitting."""
    partitions = _inner_partitions(DATASET, test_season=2022)

    assert {(row.season, row.week) for row in partitions[0].training_rows} <= {
        (2021, week) for week in range(1, 9)
    }
    assert all(row.season == 2021 and row.week >= 9 for row in partitions[0].validation_rows)


def test_first_outer_fold_excludes_week_eight_kickoff_after_validation_cutoff():
    """Catches the production week-label chronology defect in the first inner fold."""
    early_row = residual_dataset_row(2021, 1, 0)
    late_week_eight_row = residual_dataset_row(2021, 8, 1).model_copy(
        update={"kickoff_utc": datetime(2021, 10, 1, 17, tzinfo=UTC)}
    )
    first_validation_row = residual_dataset_row(2021, 9, 2).model_copy(
        update={"kickoff_utc": datetime(2021, 9, 30, 17, tzinfo=UTC)}
    )
    later_validation_row = residual_dataset_row(2021, 10, 3)
    rows = [early_row, late_week_eight_row, first_validation_row, later_validation_row]
    dataset = DATASET.model_copy(
        update={
            "training_rows": rows,
            "evaluation_rows": [residual_evaluation_row(row) for row in rows],
        }
    )

    [partition] = _inner_partitions(dataset, test_season=2022)

    assert late_week_eight_row not in partition.training_rows
    assert all(row.kickoff_utc < partition.cutoff_utc for row in partition.training_rows)


def test_later_outer_fold_uses_only_expanding_prior_seasons():
    """Catches same-season or future rows leaking into later inner fits."""
    partitions = _inner_partitions(DATASET, test_season=2025)

    assert [partition.validation_season for partition in partitions] == [2022, 2023, 2024]
    assert all(
        row.season < partition.validation_season
        for partition in partitions
        for row in partition.training_rows
    )


def test_tied_inner_accuracy_selects_largest_alpha():
    """Catches selecting less shrinkage when chronological scores tie."""
    selection = _select_alpha(
        TIED_DATASET,
        test_season=2025,
        outer_cutoff=datetime(2025, 9, 1, 17, tzinfo=UTC),
    )

    assert selection.alpha == 100.0


def test_outer_cutoff_uses_earliest_stored_game_before_proxy_filtering(
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches deriving an outer cutoff only from proxy-eligible Candidate 2 rows."""
    earliest_stored = Game(
        game_id="cfb-2022-ineligible-earliest",
        sport=Sport.CFB,
        season=2022,
        week=1,
        kickoff_utc=datetime(2022, 8, 1, 17, tzinfo=UTC),
        home_team_id="INELIGIBLE_HOME",
        away_team_id="INELIGIBLE_AWAY",
    )
    expected_cutoffs = {
        2022: earliest_stored.kickoff_utc,
        **{
            season: min(row.kickoff_utc for row in DATASET.evaluation_rows if row.season == season)
            for season in range(2023, 2026)
        },
    }
    selected_cutoffs: dict[int, datetime] = {}
    fitted_cutoffs: list[datetime] = []
    original_fit = residual_module._fit_residual_model

    monkeypatch.setattr(residual_module, "build_residual_dataset", lambda *_: DATASET)
    monkeypatch.setattr(
        residual_module,
        "replay_elo_sides",
        lambda *_: {row.game_id: Side.HOME for row in DATASET.evaluation_rows},
    )

    def select_alpha(
        _dataset: object,
        test_season: int,
        outer_cutoff: datetime,
    ) -> InnerSelection:
        selected_cutoffs[test_season] = outer_cutoff
        return InnerSelection(
            alpha=100.0,
            correct_by_alpha={10.0: 0, 30.0: 0, 100.0: 0},
            residuals=[WeightedResidual(error=0.0, weight=1.0)] * 100,
        )

    def fit(rows: object, cutoff: datetime, alpha: float):
        fitted_cutoffs.append(cutoff)
        return original_fit(rows, cutoff, alpha)

    monkeypatch.setattr(residual_module, "_select_alpha", select_alpha)
    monkeypatch.setattr(residual_module, "_fit_residual_model", fit)

    stored_games = [
        earliest_stored,
        *[
            Game(
                game_id=f"stored-{row.game_id}",
                sport=Sport.CFB,
                season=row.season,
                week=row.week,
                kickoff_utc=row.kickoff_utc,
                home_team_id="STORED_HOME",
                away_team_id="STORED_AWAY",
            )
            for row in DATASET.evaluation_rows
            if row.season >= 2023
        ],
    ]
    result = evaluate_residual_candidate(stored_games, [], [])

    assert selected_cutoffs == expected_cutoffs
    assert [fold.cutoff_utc for fold in result.folds] == list(expected_cutoffs.values())
    assert fitted_cutoffs == list(expected_cutoffs.values())


def test_alpha_oof_weights_use_the_explicit_outer_cutoff():
    """Catches OOF residual recency weighting from a filtered-season cutoff."""
    outer_cutoff = datetime(2025, 8, 1, 17, tzinfo=UTC)
    selection = _select_alpha(
        TIED_DATASET,
        test_season=2025,
        outer_cutoff=outer_cutoff,
    )
    validation_rows = [
        row for row in TIED_DATASET.training_rows if row.season in (2022, 2023, 2024)
    ]
    expected_weights = sorted(
        2 ** (-(outer_cutoff - row.kickoff_utc).total_seconds() / 86400.0 / 365.0)
        for row in validation_rows
    )

    assert [residual.weight for residual in selection.residuals] == pytest.approx(expected_weights)


def test_weighted_empirical_probability_uses_half_weight_for_ties_and_smoothing():
    """Catches changed threshold tie handling or endpoint smoothing."""
    residuals = [
        *[WeightedResidual(error=-1.0, weight=1.0) for _ in range(98)],
        WeightedResidual(error=0.0, weight=1.0),
        WeightedResidual(error=2.0, weight=1.0),
    ]

    assert _probability_home(0.0, residuals) == pytest.approx((0.5 + 1.0 + 0.5) / 101.0)


def test_fewer_than_one_hundred_oof_residuals_fails_closed():
    """Catches calibration silently proceeding without the frozen OOF minimum."""
    with pytest.raises(ValueError, match="at least 100"):
        _probability_home(1.0, [WeightedResidual(error=0.0, weight=1.0)] * 99)


def residual_prediction(
    game_id: str,
    season: int,
    week: int,
    *,
    target_home_cover: int = 1,
    candidate_correct: bool = True,
    favorite_correct: bool = False,
) -> ResidualPrediction:
    """Build one paired decided prediction for evaluator metric tests."""
    return ResidualPrediction(
        game_id=game_id,
        season=season,
        week=week,
        kickoff_utc=datetime(season, 9, min(week, 28), 17, tzinfo=UTC),
        frozen_spread=-2.5,
        submission_spread=-2.0,
        predicted_market_error=0.0,
        predicted_ats_margin=0.5,
        probability_home=0.6,
        candidate_side=Side.HOME,
        favorite_side=Side.HOME,
        elo_side=Side.HOME,
        always_home_side=Side.HOME,
        target_home_cover=target_home_cover,
        is_push=False,
        candidate_correct=candidate_correct,
        favorite_correct=favorite_correct,
        elo_correct=candidate_correct,
        always_home_correct=candidate_correct,
    )


MANUAL_RESIDUAL_PREDICTIONS = [
    residual_prediction("a", 2022, 1),
    residual_prediction("b", 2022, 2, candidate_correct=False, favorite_correct=True),
    residual_prediction("c", 2023, 1),
    residual_prediction("d", 2023, 2, candidate_correct=False, favorite_correct=True),
]
MANUAL_RESIDUAL_FOLDS = [
    ResidualFold(
        train_from=2021,
        train_through=2021,
        test_season=2022,
        cutoff_utc=datetime(2022, 9, 1, tzinfo=UTC),
        alpha=100.0,
        fit=_fit_residual_model(SYMMETRIC_ROWS, CUTOFF, 100.0),
        residual_count=100,
        correct_by_alpha={10.0: 0, 30.0: 0, 100.0: 0},
    ),
]


def test_primary_comparator_is_frozen_line_favorite_and_zero_picks_home():
    """Catches a primary comparator that is not the locked frozen favorite."""
    prediction = residual_prediction("zero", 2022, 1)

    assert prediction.favorite_side is Side.HOME


def test_outer_models_are_season_locked_and_predict_the_exact_paired_population(
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches refitting inside a test season or losing an eligible paired game."""
    monkeypatch.setattr(residual_module, "build_residual_dataset", lambda *_: DATASET)
    monkeypatch.setattr(
        residual_module,
        "replay_elo_sides",
        lambda *_: {row.game_id: Side.AWAY for row in DATASET.evaluation_rows},
    )
    monkeypatch.setattr(
        residual_module,
        "_select_alpha",
        lambda *_: InnerSelection(
            alpha=100.0,
            correct_by_alpha={10.0: 0, 30.0: 0, 100.0: 0},
            residuals=[WeightedResidual(error=0.0, weight=1.0)] * 100,
        ),
    )

    result = evaluate_residual_candidate(stored_games_for(DATASET.evaluation_rows), [], [])

    assert [(fold.train_through, fold.test_season) for fold in result.folds] == [
        (2021, 2022),
        (2022, 2023),
        (2023, 2024),
        (2024, 2025),
    ]
    assert all(
        fold.cutoff_utc
        == min(
            prediction.kickoff_utc
            for prediction in result.predictions
            if prediction.season == fold.test_season
        )
        for fold in result.folds
    )
    assert {prediction.game_id for prediction in result.predictions} == {
        row.game_id for row in DATASET.evaluation_rows if row.season >= 2022
    }


def test_gate_requires_every_predeclared_condition():
    """Catches any acceptance condition being silently omitted from Candidate 2's gate."""
    passing = ResidualEvaluation(
        accuracy_delta=0.01,
        positive_seasons=3,
        bootstrap_lower=0.001,
        brier_score=0.249,
        calibration_safe=True,
        leakage_safe=True,
        deterministic=True,
        uses_2026_outcomes=False,
    )
    assert passes_residual_gate(passing)
    mutations = [
        {"accuracy_delta": 0.009},
        {"positive_seasons": 2},
        {"bootstrap_lower": 0.0},
        {"brier_score": 0.25},
        {"calibration_safe": False},
        {"leakage_safe": False},
        {"deterministic": False},
        {"uses_2026_outcomes": True},
    ]
    assert all(not passes_residual_gate(passing.model_copy(update=change)) for change in mutations)


def test_bootstrap_is_paired_by_season_week_and_deterministic():
    """Catches unseeded or row-level bootstrap sampling of paired outcomes."""
    first = summarize_residual_predictions(MANUAL_RESIDUAL_PREDICTIONS, MANUAL_RESIDUAL_FOLDS)
    second = summarize_residual_predictions(MANUAL_RESIDUAL_PREDICTIONS, MANUAL_RESIDUAL_FOLDS)

    assert (first.bootstrap_lower, first.bootstrap_upper) == (
        second.bootstrap_lower,
        second.bootstrap_upper,
    )


def test_fold_certification_rejects_an_incomplete_outer_replay():
    """Catches a partial 2022-only replay being certified as leakage-safe."""
    [fold] = MANUAL_RESIDUAL_FOLDS
    cutoff = MANUAL_RESIDUAL_PREDICTIONS[0].kickoff_utc
    safe_2022_fold = fold.model_copy(
        update={
            "cutoff_utc": cutoff,
            "fit": fold.fit.model_copy(update={"cutoff_utc": cutoff}),
        }
    )

    assert not residual_module._residual_folds_are_safe(
        MANUAL_RESIDUAL_PREDICTIONS[:2],
        [safe_2022_fold],
    )


def test_evaluation_rejects_pre_2021_inputs_before_building_rows():
    """Catches 2020 outcomes entering the frozen 2021–2025 experiment."""
    pre_contract = GAME.model_copy(update={"season": 2020})

    with pytest.raises(ValueError, match="2021 through 2025"):
        evaluate_residual_candidate([pre_contract], FROZEN, SUBMISSION)
