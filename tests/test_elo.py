from datetime import UTC, datetime

from pickem.edge.elo import EloConfig, build_ratings, projected_margin, tiebreak_side
from pickem.models import Game, Side, Sport


def played(away: str, home: str, away_score: int, home_score: int, week: int) -> Game:
    return Game(
        game_id=f"nfl-2025-{week:02d}-{away}-at-{home}",
        sport=Sport.NFL,
        season=2025,
        week=week,
        kickoff_utc=datetime(2025, 9, 7 + week, tzinfo=UTC),
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
    )


def test_all_teams_start_equal():
    ratings = build_ratings([])
    assert ratings == {}


def test_winning_raises_a_rating_and_losing_lowers_it():
    ratings = build_ratings([played("BUF", "MIA", 10, 30, 1)])
    assert ratings["MIA"] > ratings["BUF"]


def test_unplayed_games_are_ignored():
    unplayed = Game(
        game_id="nfl-2025-01-BUF-at-MIA",
        sport=Sport.NFL,
        season=2025,
        week=1,
        kickoff_utc=datetime(2025, 9, 8, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    assert build_ratings([unplayed]) == {}


def test_repeated_wins_compound():
    one = build_ratings([played("BUF", "MIA", 10, 30, 1)])
    two = build_ratings([played("BUF", "MIA", 10, 30, 1), played("BUF", "MIA", 10, 30, 2)])
    assert two["MIA"] > one["MIA"]


def test_projected_margin_favors_the_stronger_team_at_home():
    ratings = {"MIA": 1600.0, "BUF": 1500.0}
    assert projected_margin(ratings, "MIA", "BUF", EloConfig()) > 0


def test_home_field_advantage_is_applied():
    ratings = {"MIA": 1500.0, "BUF": 1500.0}
    config = EloConfig(home_field=2.5)
    assert projected_margin(ratings, "MIA", "BUF", config) == 2.5


def test_unrated_team_is_treated_as_average():
    ratings = {"MIA": 1500.0}
    # A team with no history must not crash or be treated as infinitely weak.
    assert projected_margin(ratings, "MIA", "NEWTEAM", EloConfig()) == EloConfig().home_field


def test_tiebreak_picks_home_when_projection_beats_the_line():
    # Home favored by 3 on the board; we project home by 7 -> home covers.
    assert tiebreak_side(projected_margin=7.0, league_spread=-3.0) is Side.HOME


def test_tiebreak_picks_away_when_projection_falls_short_of_the_line():
    # Home laying 10 but we only project home by 3 -> away covers.
    assert tiebreak_side(projected_margin=3.0, league_spread=-10.0) is Side.AWAY
