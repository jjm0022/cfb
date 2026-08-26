from datetime import UTC, datetime, timedelta

from pickem.backtest.coinflip import build_coinflip_rows
from pickem.models import Game, MarketLine, Sport

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
