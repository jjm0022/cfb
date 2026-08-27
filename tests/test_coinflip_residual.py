"""Contract tests for Candidate 2's leakage-safe residual rows."""

from datetime import UTC, datetime, timedelta

from pickem.backtest.coinflip import build_coinflip_rows
from pickem.backtest.coinflip_residual import build_residual_dataset
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
