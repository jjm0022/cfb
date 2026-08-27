"""Contract tests for Candidate 2's leakage-safe residual rows."""

from datetime import UTC, datetime, timedelta

import pytest

from pickem.backtest.coinflip import build_coinflip_rows
from pickem.backtest.coinflip_residual import (
    ResidualTrainingRow,
    _fit_residual_model,
    _predict_market_error,
    _recency_weights,
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
