from datetime import UTC, datetime

import pytest

from pickem.edge.divergence import Thresholds
from pickem.edge.pipeline import MissingGameError, decide_edges, predict_tiebreaker_total
from pickem.models import Game, LeagueLine, MarketLine, Side, Sport, Tier

NOW = datetime(2025, 9, 21, tzinfo=UTC)
POSTED = datetime(2025, 9, 16, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


def game(gid: str = GID, week: int = 3, home_score=None, away_score=None) -> Game:
    return Game(
        game_id=gid,
        sport=Sport.NFL,
        season=2025,
        week=week,
        kickoff_utc=NOW,
        home_team_id="MIA",
        away_team_id="BUF",
        home_score=home_score,
        away_score=away_score,
    )


def league(spread: float = -3.0) -> LeagueLine:
    return LeagueLine(game_id=GID, season=2025, week=3, spread_home=spread, posted_at=POSTED)


def market(spread: float) -> MarketLine:
    return MarketLine(
        game_id=GID, source="oddsapi", book="pinnacle", spread_home=spread, captured_at=NOW
    )


HISTORY = [
    game(gid=f"nfl-2025-{week:02d}-BUF-at-MIA", week=week, home_score=30, away_score=10)
    for week in range(1, 4)
]


def test_strong_divergence_returns_its_final_side_without_rating_override():
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.STRONG


def test_coinflip_returns_the_rating_side_not_a_placeholder():
    [edge] = decide_edges([league(-3.0)], [market(-3.5)], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.COINFLIP
    assert "rating" in edge.rationale.lower()


def test_no_market_returns_the_rating_side_not_a_placeholder():
    [edge] = decide_edges([league(-3.0)], [], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.NO_MARKET
    assert edge.market_spread is None


def test_a_required_tiebreak_without_a_game_fails_loudly():
    with pytest.raises(MissingGameError):
        decide_edges([league(-3.0)], [], [], HISTORY)


@pytest.mark.parametrize(
    ("league_spread", "market_spread", "expected_side", "expected_delta"),
    [(-3.0, -6.0, Side.HOME, 3.0), (-6.0, -3.0, Side.AWAY, -3.0), (2.0, -1.0, Side.HOME, 3.0)],
)
def test_final_pick_obeys_the_home_perspective_sign_convention(
    league_spread, market_spread, expected_side, expected_delta
):
    [edge] = decide_edges([league(league_spread)], [market(market_spread)], [game()], HISTORY)
    assert edge.side is expected_side
    assert edge.delta == expected_delta


@pytest.mark.parametrize(
    ("market_spread", "expected_tier"),
    [(-5.0, Tier.STRONG), (-4.0, Tier.LEAN), (-3.5, Tier.COINFLIP)],
)
def test_final_pick_uses_inclusive_tier_thresholds(market_spread, expected_tier):
    [edge] = decide_edges([league(-3.0)], [market(market_spread)], [game()], HISTORY)
    assert edge.tier is expected_tier


def test_threshold_configuration_changes_tiers_without_changing_the_interface():
    strict = Thresholds(strong=5.0, lean=3.0)
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY, thresholds=strict)
    assert edge.tier is Tier.LEAN


def test_final_pick_carries_both_source_numbers_for_audit():
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY)
    assert edge.league_spread == -3.0
    assert edge.market_spread == -6.0
    assert edge.rationale


def test_tiebreaker_total_uses_the_market():
    lines = [
        market(-3.0).model_copy(update={"book": "a", "total": 44.5}),
        market(-3.0).model_copy(update={"book": "b", "total": 45.5}),
    ]
    assert predict_tiebreaker_total(lines) == 45.0


def test_tiebreaker_total_is_none_without_a_market():
    assert predict_tiebreaker_total([]) is None


def test_tiebreaker_total_ignores_books_without_a_total():
    lines = [
        market(-3.0).model_copy(update={"book": "a"}),
        market(-3.0).model_copy(update={"book": "b", "total": 45.0}),
    ]
    assert predict_tiebreaker_total(lines) == 45.0
