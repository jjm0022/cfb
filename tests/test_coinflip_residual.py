"""Contract tests for Candidate 2's leakage-safe residual rows."""

from datetime import UTC, datetime, timedelta

import pytest

from pickem.backtest.coinflip import CoinflipRow, build_coinflip_rows
from pickem.backtest.coinflip_residual import (
    ResidualTrainingRow,
    WeightedResidual,
    _fit_residual_model,
    _inner_partitions,
    _predict_market_error,
    _probability_home,
    _recency_weights,
    _select_alpha,
    build_residual_dataset,
)
from pickem.models import Game, MarketLine, Sport

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
    selection = _select_alpha(TIED_DATASET, test_season=2025)

    assert selection.alpha == 100.0


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
