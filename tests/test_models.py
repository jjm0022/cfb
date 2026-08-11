from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pickem.models import Edge, Game, LeagueLine, MarketLine, Side, Sport, Tier, make_game_id


def test_game_id_is_deterministic_and_source_independent():
    a = make_game_id(Sport.NFL, 2025, 3, "BUF", "MIA")
    b = make_game_id(Sport.NFL, 2025, 3, "BUF", "MIA")
    assert a == b
    assert a == "nfl-2025-03-BUF-at-MIA"


def test_game_id_distinguishes_home_and_away():
    assert make_game_id(Sport.NFL, 2025, 3, "BUF", "MIA") != make_game_id(
        Sport.NFL, 2025, 3, "MIA", "BUF"
    )


def test_game_rejects_a_team_playing_itself():
    with pytest.raises(ValidationError):
        Game(
            game_id="nfl-2025-03-BUF-at-BUF",
            sport=Sport.NFL,
            season=2025,
            week=3,
            kickoff_utc=datetime(2025, 9, 21, 17, 0, tzinfo=UTC),
            home_team_id="BUF",
            away_team_id="BUF",
        )


def test_game_scores_default_to_none_until_played():
    game = Game(
        game_id="nfl-2025-03-BUF-at-MIA",
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=datetime(2025, 9, 21, 17, 0, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    assert game.home_score is None
    assert game.away_score is None


def test_league_line_holds_home_perspective_spread():
    line = LeagueLine(
        game_id="nfl-2025-03-BUF-at-MIA",
        season=2025,
        week=3,
        spread_home=-3.0,
        posted_at=datetime(2025, 9, 16, 12, 0, tzinfo=UTC),
    )
    assert line.spread_home == -3.0


def test_market_line_total_is_optional():
    line = MarketLine(
        game_id="nfl-2025-03-BUF-at-MIA",
        source="oddsapi",
        book="pinnacle",
        spread_home=-6.0,
        total=None,
        captured_at=datetime(2025, 9, 21, 12, 0, tzinfo=UTC),
    )
    assert line.total is None


def test_edge_records_both_numbers_that_produced_it():
    edge = Edge(
        game_id="nfl-2025-03-BUF-at-MIA",
        side=Side.HOME,
        delta=3.0,
        tier=Tier.STRONG,
        league_spread=-3.0,
        market_spread=-6.0,
        rationale="market moved 3.0 toward home",
    )
    assert edge.league_spread == -3.0
    assert edge.market_spread == -6.0
