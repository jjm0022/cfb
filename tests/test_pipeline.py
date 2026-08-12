from datetime import UTC, datetime

from pickem.edge.pipeline import apply_tiebreaks, predict_tiebreaker_total
from pickem.models import Edge, Game, MarketLine, Side, Sport, Tier

NOW = datetime(2025, 9, 21, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


def game(gid=GID, week=3, home_score=None, away_score=None) -> Game:
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


def edge(tier: Tier, side: Side = Side.HOME, league_spread: float = -3.0) -> Edge:
    return Edge(
        game_id=GID,
        side=side,
        delta=0.0,
        tier=tier,
        league_spread=league_spread,
        market_spread=None,
        rationale="original",
    )


# MIA has beaten BUF repeatedly, so the rating strongly favors MIA.
HISTORY = [
    game(gid=f"nfl-2025-{w:02d}-BUF-at-MIA", week=w, home_score=30, away_score=10)
    for w in range(1, 4)
]


def test_strong_edges_are_left_untouched():
    original = edge(Tier.STRONG, side=Side.AWAY)
    result = apply_tiebreaks([original], [game()], HISTORY)
    # A real divergence signal must never be overridden by the weak rating.
    assert result[0].side is Side.AWAY
    assert result[0].rationale == "original"


def test_lean_edges_are_left_untouched():
    result = apply_tiebreaks([edge(Tier.LEAN, side=Side.AWAY)], [game()], HISTORY)
    assert result[0].side is Side.AWAY


def test_coinflip_is_resolved_by_the_rating():
    # Rating loves MIA; the board only asks MIA to win by 3.
    result = apply_tiebreaks([edge(Tier.COINFLIP, side=Side.AWAY)], [game()], HISTORY)
    assert result[0].side is Side.HOME


def test_no_market_is_resolved_by_the_rating():
    result = apply_tiebreaks([edge(Tier.NO_MARKET, side=Side.AWAY)], [game()], HISTORY)
    assert result[0].side is Side.HOME


def test_resolved_edges_say_the_rating_decided_them():
    result = apply_tiebreaks([edge(Tier.COINFLIP)], [game()], HISTORY)
    assert "rating" in result[0].rationale.lower()


def test_a_heavy_number_flips_the_rating_to_the_dog():
    # Same strong MIA rating, but the board asks MIA to win by 40.
    result = apply_tiebreaks(
        [edge(Tier.COINFLIP, side=Side.HOME, league_spread=-40.0)], [game()], HISTORY
    )
    assert result[0].side is Side.AWAY


def test_edge_for_an_unknown_game_is_passed_through_unchanged():
    result = apply_tiebreaks([edge(Tier.COINFLIP)], [], HISTORY)
    assert result[0].rationale == "original"


def test_tiebreaker_total_uses_the_market():
    lines = [
        MarketLine(
            game_id=GID,
            source="oddsapi",
            book="a",
            spread_home=-3.0,
            total=44.5,
            captured_at=NOW,
        ),
        MarketLine(
            game_id=GID,
            source="oddsapi",
            book="b",
            spread_home=-3.0,
            total=45.5,
            captured_at=NOW,
        ),
    ]
    assert predict_tiebreaker_total(lines) == 45.0


def test_tiebreaker_total_is_none_without_a_market():
    assert predict_tiebreaker_total([]) is None


def test_tiebreaker_total_ignores_books_without_a_total():
    lines = [
        MarketLine(
            game_id=GID,
            source="oddsapi",
            book="a",
            spread_home=-3.0,
            total=None,
            captured_at=NOW,
        ),
        MarketLine(
            game_id=GID,
            source="oddsapi",
            book="b",
            spread_home=-3.0,
            total=45.0,
            captured_at=NOW,
        ),
    ]
    assert predict_tiebreaker_total(lines) == 45.0
