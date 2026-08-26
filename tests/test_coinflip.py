import math
from datetime import UTC, datetime, timedelta

import pytest

from pickem.backtest.coinflip import (
    CoinflipEvaluation,
    CoinflipPrediction,
    CoinflipRow,
    build_coinflip_rows,
    evaluate_coinflip,
    passes_acceptance_gate,
    replay_elo_sides,
    summarize_coinflip_predictions,
)
from pickem.edge.pipeline import decide_edges
from pickem.models import Game, LeagueLine, MarketLine, Side, Sport, Tier

KICKOFF = datetime(2025, 9, 6, 17, tzinfo=UTC)
CAPTURED = KICKOFF - timedelta(minutes=15)
GAME = Game(
    game_id="cfb-2025-02-AWAY-at-HOME",
    sport=Sport.CFB,
    season=2025,
    week=2,
    kickoff_utc=KICKOFF,
    home_team_id="HOME",
    away_team_id="AWAY",
    home_score=24,
    away_score=20,
)
FROZEN = [
    MarketLine(
        game_id=GAME.game_id,
        source="oddsapi:frozen",
        book="a",
        spread_home=-3.0,
        captured_at=CAPTURED - timedelta(days=4),
    )
]
SUBMISSION = [
    MarketLine(
        game_id=GAME.game_id,
        source="oddsapi:submit",
        book=book,
        spread_home=spread,
        captured_at=CAPTURED,
    )
    for book, spread in (("a", -3.5), ("b", -3.0), ("c", -2.5))
]


def coinflip_row(
    season: int, week: int, target: int, index: int, median_delta: float
) -> CoinflipRow:
    """Create independent, balanced CFB rows for walk-forward evaluator tests."""
    return CoinflipRow(
        game_id=f"cfb-{season}-{week:02d}-{index}",
        season=season,
        week=week,
        kickoff_utc=datetime(season, 9, min(week, 28), 17, tzinfo=UTC),
        frozen_spread=-3.0,
        median_delta=median_delta,
        mean_delta=median_delta / 2,
        book_balance=1.0 if target else -1.0,
        target_home_cover=target,
        is_push=False,
    )


ROWS = [
    coinflip_row(season, week, target, index, median_delta)
    for season in range(2021, 2026)
    for week, target, index, median_delta in (
        (1, 0, 0, -0.8),
        (2, 1, 1, 0.8),
        (9, 0, 2, -0.6),
        (10, 1, 3, 0.6),
    )
]
ELO_SIDES = {
    row.game_id: Side.HOME if row.target_home_cover == 0 else Side.AWAY
    for row in ROWS
    if row.season >= 2022
}
PUSH_ROW = CoinflipRow(
    game_id="cfb-2025-11-push",
    season=2025,
    week=11,
    kickoff_utc=datetime(2025, 11, 15, 17, tzinfo=UTC),
    frozen_spread=-3.0,
    median_delta=0.0,
    mean_delta=0.0,
    book_balance=0.0,
    target_home_cover=None,
    is_push=True,
)
ROWS_WITH_EXTREME_2025 = [
    row.model_copy(update={"median_delta": 99.0}) if row.season == 2025 else row for row in ROWS
]
TIED_INNER_ROWS = ROWS
TIED_INNER_ELO = ELO_SIDES


def test_market_distribution_becomes_the_three_declared_features():
    """Catches a feature builder that does not preserve the market distribution."""
    dataset = build_coinflip_rows([GAME], FROZEN, SUBMISSION)

    [row] = dataset.rows
    assert row.median_delta == 0.0
    assert row.mean_delta == 0.0
    assert row.book_balance == 0.0


def test_latest_snapshot_per_book_gets_one_vote():
    """Catches historical snapshots giving one book multiple votes."""
    older = SUBMISSION[0].model_copy(
        update={
            "spread_home": -20.0,
            "captured_at": SUBMISSION[0].captured_at - timedelta(hours=1),
        }
    )

    baseline = build_coinflip_rows([GAME], FROZEN, SUBMISSION).rows[0]
    with_history = build_coinflip_rows([GAME], FROZEN, [older, *SUBMISSION]).rows[0]

    assert with_history == baseline


def test_non_coinflip_median_delta_is_excluded_with_reason():
    """Catches a builder that trains on games outside the live COINFLIP tier."""
    moved = [line.model_copy(update={"spread_home": -5.0}) for line in SUBMISSION]

    dataset = build_coinflip_rows([GAME], FROZEN, moved)

    assert dataset.rows == []
    assert GAME.game_id in dataset.skipped[0]
    assert "not coinflip" in dataset.skipped[0].lower()


def test_push_is_retained_but_has_no_binary_target():
    """Catches a builder that drops pushes or assigns them an arbitrary target."""
    pushed = GAME.model_copy(update={"home_score": 20, "away_score": 17})

    [row] = build_coinflip_rows([pushed], FROZEN, SUBMISSION).rows

    assert row.is_push
    assert row.target_home_cover is None


def test_snapshot_at_or_after_kickoff_is_rejected():
    """Catches in-play market data leaking into a pre-kickoff feature row."""
    in_play = [line.model_copy(update={"captured_at": GAME.kickoff_utc}) for line in SUBMISSION]

    dataset = build_coinflip_rows([GAME], FROZEN, in_play)

    assert dataset.rows == []
    assert "kickoff" in dataset.skipped[0].lower()


def test_in_play_history_is_ignored_when_pre_kickoff_quote_exists():
    """Catches an in-play archive row excluding an otherwise valid game."""
    in_play = SUBMISSION[0].model_copy(
        update={"spread_home": -20.0, "captured_at": GAME.kickoff_utc}
    )

    baseline = build_coinflip_rows([GAME], FROZEN, SUBMISSION).rows[0]
    dataset = build_coinflip_rows([GAME], FROZEN, [*SUBMISSION, in_play])

    assert dataset.rows == [baseline]
    assert dataset.skipped == []


def test_missing_frozen_or_submission_books_are_reported():
    """Catches proxy gaps being silently omitted from the audit trail."""
    no_frozen = build_coinflip_rows([GAME], [], SUBMISSION)
    no_submission = build_coinflip_rows([GAME], FROZEN, [])

    assert GAME.game_id in no_frozen.skipped[0]
    assert "frozen" in no_frozen.skipped[0].lower()
    assert GAME.game_id in no_submission.skipped[0]
    assert "submission" in no_submission.skipped[0].lower()


def test_non_finite_features_are_excluded_with_a_named_reason():
    """Catches NaN or infinity silently entering the later model evaluator."""
    non_finite = [line.model_copy(update={"spread_home": float("inf")}) for line in SUBMISSION]

    dataset = build_coinflip_rows([GAME], FROZEN, non_finite)

    assert dataset.rows == []
    assert "non-finite" in dataset.skipped[0].lower()


def test_rows_are_sorted_by_season_week_and_game_id():
    """Catches input ordering leaking into serialized experiment artifacts."""
    later = GAME.model_copy(update={"game_id": "cfb-2025-03-AWAY-at-HOME", "week": 3})
    later_frozen = [line.model_copy(update={"game_id": later.game_id}) for line in FROZEN]
    later_submission = [line.model_copy(update={"game_id": later.game_id}) for line in SUBMISSION]

    dataset = build_coinflip_rows(
        [later, GAME],
        [*later_frozen, *FROZEN],
        [*later_submission, *SUBMISSION],
    )

    assert [row.game_id for row in dataset.rows] == [GAME.game_id, later.game_id]


def test_non_cfb_game_is_excluded_from_the_cfb_experiment_population():
    """Catches an NFL game silently contaminating the CFB-only model dataset."""
    nfl_game = GAME.model_copy(update={"game_id": "nfl-2025-02-AWAY-at-HOME", "sport": Sport.NFL})
    nfl_frozen = [line.model_copy(update={"game_id": nfl_game.game_id}) for line in FROZEN]
    nfl_submission = [line.model_copy(update={"game_id": nfl_game.game_id}) for line in SUBMISSION]

    dataset = build_coinflip_rows([nfl_game], nfl_frozen, nfl_submission)

    assert dataset.rows == []
    assert "cfb" in dataset.skipped[0].lower()


def test_outer_folds_never_train_on_the_test_or_future_season():
    """Catches outer folds that learn from their held-out or a future season."""
    result = evaluate_coinflip(ROWS, ELO_SIDES)

    assert [(fold.train_through, fold.test_season) for fold in result.folds] == [
        (2021, 2022),
        (2022, 2023),
        (2023, 2024),
        (2024, 2025),
    ]


def test_selected_c_always_comes_from_the_fixed_grid():
    """Catches an evaluator that searches or retains a C outside its declared grid."""
    result = evaluate_coinflip(ROWS, ELO_SIDES)

    assert {fold.selected_c for fold in result.folds} <= {0.1, 1.0, 10.0}


def test_tied_inner_accuracy_selects_stronger_regularization():
    """Catches nondeterministic or weaker-regularization selection on an accuracy tie."""
    result = evaluate_coinflip(TIED_INNER_ROWS, TIED_INNER_ELO)

    assert all(fold.selected_c == 0.1 for fold in result.folds)


def test_scaler_means_are_learned_only_from_outer_training_rows():
    """Catches the extreme 2025 test feature leaking into the outer scaler mean."""
    result = evaluate_coinflip(ROWS_WITH_EXTREME_2025, ELO_SIDES)
    fold = next(item for item in result.folds if item.test_season == 2025)
    training = [row for row in ROWS_WITH_EXTREME_2025 if row.season <= 2024 and not row.is_push]

    assert fold.means[0] == pytest.approx(sum(row.median_delta for row in training) / len(training))


def test_pushes_are_reported_but_not_fitted_or_scored_as_decisions():
    """Catches pushes being forced into a binary fit or an accuracy denominator."""
    result = evaluate_coinflip([*ROWS, PUSH_ROW], {**ELO_SIDES, PUSH_ROW.game_id: Side.HOME})
    pushed = next(
        prediction for prediction in result.predictions if prediction.game_id == PUSH_ROW.game_id
    )

    assert result.pushes >= 1
    assert pushed.is_push
    assert pushed.candidate_correct is None
    assert pushed.elo_correct is None


def test_predictions_are_paired_to_the_same_elo_game_ids():
    """Catches candidate predictions being scored on a different game population than Elo."""
    result = evaluate_coinflip(ROWS, ELO_SIDES)

    assert {prediction.game_id for prediction in result.predictions} == set(ELO_SIDES)


def test_repeated_evaluation_is_byte_identical():
    """Catches random fitting, ordering, or bootstrap output entering experiment records."""
    first = evaluate_coinflip(ROWS, ELO_SIDES).model_dump_json()
    second = evaluate_coinflip(ROWS, ELO_SIDES).model_dump_json()

    assert first == second


def test_frozen_estimator_keeps_the_explicit_l2_penalty_contract():
    """Catches Task 8 changing Task 7's frozen LogisticRegression constructor."""
    with pytest.warns(FutureWarning, match="'penalty' was deprecated"):
        evaluate_coinflip(ROWS, ELO_SIDES)


def test_exact_half_model_probability_uses_the_candidate_class_prediction_not_elo():
    """Catches an exact-half candidate prediction silently borrowing Elo's side."""
    rows = [
        coinflip_row(season, week, target, index, 0.0).model_copy(
            update={"mean_delta": 0.0, "book_balance": 0.0}
        )
        for season, week, target, index in (
            (2021, 1, 0, 0),
            (2021, 2, 1, 1),
            (2021, 9, 0, 2),
            (2021, 10, 1, 3),
            (2022, 1, 0, 4),
        )
    ]
    test_row = rows[-1]

    [result] = evaluate_coinflip(rows, {test_row.game_id: Side.HOME}).predictions

    assert result.probability_home == 0.5
    assert result.candidate_side is Side.AWAY
    assert result.candidate_side is not result.elo_side


def test_missing_inner_validation_season_fails_loudly():
    """Catches a gapped population selecting a C without any inner validation."""
    rows = [row for row in ROWS if row.season in {2021, 2023}]
    elo_sides = {row.game_id: Side.HOME for row in rows if row.season == 2023}

    with pytest.raises(ValueError, match="at least one inner validation partition"):
        evaluate_coinflip(rows, elo_sides)


def prediction(
    game_id: str,
    season: int,
    week: int,
    probability_home: float | None,
    target_home_cover: int | None,
    candidate_correct: bool | None,
    elo_correct: bool | None,
) -> CoinflipPrediction:
    """Create fixed, hand-scored rows for metric tests without fitting a model."""
    return CoinflipPrediction(
        game_id=game_id,
        season=season,
        week=week,
        frozen_spread=-3.0,
        median_delta=0.0,
        mean_delta=0.0,
        book_balance=0.0,
        probability_home=probability_home,
        candidate_side=Side.HOME
        if probability_home is None or probability_home >= 0.5
        else Side.AWAY,
        elo_side=Side.HOME,
        target_home_cover=target_home_cover,
        is_push=target_home_cover is None,
        candidate_correct=candidate_correct,
        elo_correct=elo_correct,
        agrees_with_elo=True,
    )


MANUAL_PREDICTIONS = [
    prediction("a", 2022, 1, 0.1, 0, True, False),
    prediction("b", 2022, 2, 0.3, 0, True, True),
    prediction("c", 2023, 1, 0.5, 1, True, True),
    prediction("d", 2023, 2, 0.7, 1, True, False),
    prediction("e", 2024, 1, 0.9, 1, True, True),
    prediction("push", 2025, 1, None, None, None, None),
]


def test_summary_reports_hand_calculated_paired_metrics_and_calibration():
    """Catches metrics that include pushes, unpair Elo, or mis-bin endpoint probabilities."""
    result = summarize_coinflip_predictions(MANUAL_PREDICTIONS, folds=[])

    assert (result.candidate_wins, result.candidate_losses, result.pushes) == (5, 0, 1)
    assert (result.elo_wins, result.elo_losses) == (3, 2)
    assert result.candidate_accuracy == pytest.approx(1.0)
    assert result.elo_accuracy == pytest.approx(0.6)
    assert result.accuracy_delta == pytest.approx(0.4)
    assert result.brier_score == pytest.approx(0.09)
    assert result.log_loss == pytest.approx(-math.log(0.9 * 0.7 * 0.5 * 0.7 * 0.9) / 5)
    assert [
        (item.lower, item.upper, item.count, item.mean_probability, item.observed_rate)
        for item in result.calibration
    ] == pytest.approx(
        [
            (0.0, 0.2, 1, 0.1, 0.0),
            (0.2, 0.4, 1, 0.3, 0.0),
            (0.4, 0.6, 1, 0.5, 1.0),
            (0.6, 0.8, 1, 0.7, 1.0),
            (0.8, 1.0, 1, 0.9, 1.0),
        ]
    )
    assert [
        (item.season, item.candidate_minus_elo) for item in result.season_deltas
    ] == pytest.approx([(2022, 0.5), (2023, 0.5), (2024, 0.0)])
    assert (result.bootstrap_lower, result.bootstrap_upper) == pytest.approx((0.0, 0.8))


def test_summary_keeps_log_loss_finite_at_numeric_probability_limits():
    """Catches a valid extreme model prediction crashing the diagnostic report."""
    result = summarize_coinflip_predictions(
        [
            prediction("low", 2022, 1, 0.0, 1, False, False),
            prediction("high", 2022, 2, 1.0, 0, False, False),
        ],
        folds=[],
    )

    assert result.log_loss is not None
    assert math.isfinite(result.log_loss)


def passing_evaluation() -> CoinflipEvaluation:
    return CoinflipEvaluation(
        folds=[],
        predictions=[],
        pushes=0,
        candidate_wins=52,
        candidate_losses=48,
        elo_wins=51,
        elo_losses=49,
        candidate_accuracy=0.52,
        elo_accuracy=0.51,
        accuracy_delta=0.01,
        brier_score=0.24,
        positive_seasons=3,
        leakage_safe=True,
        deterministic=True,
    )


def with_delta(delta: float) -> CoinflipEvaluation:
    return passing_evaluation().model_copy(update={"accuracy_delta": delta})


def with_positive_seasons(count: int) -> CoinflipEvaluation:
    return passing_evaluation().model_copy(update={"positive_seasons": count})


def with_brier(brier: float) -> CoinflipEvaluation:
    return passing_evaluation().model_copy(update={"brier_score": brier})


def test_gate_requires_one_percentage_point_three_seasons_and_brier_below_quarter():
    """Catches a gate that weakens any predeclared acceptance condition."""
    assert passes_acceptance_gate(passing_evaluation())
    assert not passes_acceptance_gate(with_delta(0.0099))
    assert not passes_acceptance_gate(with_positive_seasons(2))
    assert not passes_acceptance_gate(with_brier(0.25))


def test_replay_elo_sides_matches_live_coinflip_decisions_with_prior_week_history_only():
    """Catches replaying a different tier or leaking a week's final into Elo."""
    kickoff_2021 = datetime(2021, 9, 4, 17, tzinfo=UTC)
    kickoff_2022 = datetime(2022, 9, 3, 17, tzinfo=UTC)
    games = [
        Game(
            game_id="cfb-2021-01-B-at-A",
            sport=Sport.CFB,
            season=2021,
            week=1,
            kickoff_utc=kickoff_2021,
            home_team_id="A",
            away_team_id="B",
            home_score=10,
            away_score=40,
        ),
        Game(
            game_id="cfb-2022-01-B-at-A",
            sport=Sport.CFB,
            season=2022,
            week=1,
            kickoff_utc=kickoff_2022,
            home_team_id="A",
            away_team_id="B",
            home_score=60,
            away_score=0,
        ),
        Game(
            game_id="cfb-2022-01-D-at-C",
            sport=Sport.CFB,
            season=2022,
            week=1,
            kickoff_utc=kickoff_2022,
            home_team_id="C",
            away_team_id="D",
            home_score=24,
            away_score=17,
        ),
    ]
    frozen = [
        MarketLine(
            game_id=game.game_id,
            source="oddsapi:frozen",
            book="a",
            spread_home=2.0 if game.home_team_id == "A" else -3.0,
            captured_at=game.kickoff_utc - timedelta(days=3),
        )
        for game in games
    ]
    submission = [
        MarketLine(
            game_id=game.game_id,
            source="oddsapi:submit",
            book="a",
            spread_home=(2.0 if game.home_team_id == "A" else -5.0),
            captured_at=game.kickoff_utc - timedelta(minutes=10),
        )
        for game in games
    ]

    feature_ids = {row.game_id for row in build_coinflip_rows(games, frozen, submission).rows}
    sides = replay_elo_sides(games, frozen, submission)
    expected = decide_edges(
        [
            LeagueLine(
                game_id=games[1].game_id,
                season=2022,
                week=1,
                spread_home=2.0,
                posted_at=kickoff_2022 - timedelta(days=3),
            )
        ],
        [submission[1]],
        [games[1]],
        [games[0]],
    )[0]
    leaked = decide_edges(
        [
            LeagueLine(
                game_id=games[1].game_id,
                season=2022,
                week=1,
                spread_home=2.0,
                posted_at=kickoff_2022 - timedelta(days=3),
            )
        ],
        [submission[1]],
        [games[1]],
        games[:2],
    )[0]

    assert feature_ids == {games[0].game_id, games[1].game_id}
    assert set(sides) == feature_ids
    assert expected.tier is Tier.COINFLIP
    assert sides[games[1].game_id] is expected.side
    assert sides[games[1].game_id] is not leaked.side

    in_play = MarketLine(
        game_id=games[1].game_id,
        source="oddsapi:submit",
        book="a",
        spread_home=-20.0,
        captured_at=kickoff_2022,
    )
    assert replay_elo_sides(games, frozen, [*submission, in_play]) == sides
