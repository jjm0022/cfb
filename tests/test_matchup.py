import pytest

from pickem.models import Sport
from pickem.resolve.matchup import CanonicalMatchup, resolve_matchup
from pickem.resolve.resolver import TeamResolver, UnknownTeamError


def test_resolve_matchup_preserves_away_home_order_and_exact_game_id():
    matchup = resolve_matchup(
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        away_name="Buffalo Bills",
        home_name="Miami Dolphins",
    )
    assert matchup.away_team_id == "BUF"
    assert matchup.home_team_id == "MIA"
    assert matchup.game_id == "nfl-2025-03-BUF-at-MIA"


def test_canonical_matchup_rejects_the_same_team_on_both_sides():
    with pytest.raises(ValueError):
        CanonicalMatchup(
            sport=Sport.NFL,
            season=2025,
            week=3,
            away_team_id="BUF",
            home_team_id="BUF",
        )


def test_resolve_matchup_preserves_fail_loud_resolution():
    with pytest.raises(UnknownTeamError):
        resolve_matchup(
            resolver=TeamResolver.default(),
            sport=Sport.NFL,
            season=2025,
            week=3,
            away_name="Fictional Aardvarks",
            home_name="Miami Dolphins",
        )


def test_flipped_swaps_the_sides_and_the_id():
    matchup = CanonicalMatchup(
        sport=Sport.NFL,
        season=2025,
        week=3,
        away_team_id="BUF",
        home_team_id="MIA",
    )
    flipped = matchup.flipped()

    assert (flipped.away_team_id, flipped.home_team_id) == ("MIA", "BUF")
    assert flipped.game_id == "nfl-2025-03-MIA-at-BUF"
    assert flipped.flipped().game_id == matchup.game_id
