from datetime import UTC, datetime
from pathlib import Path

import pytest

from pickem.ingest.cbs import CbsParseError, parse_cbs_block
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

FIXTURE = Path(__file__).parent / "fixtures" / "cbs_week3.txt"
POSTED = datetime(2025, 9, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def resolver():
    return TeamResolver.default()


def parse(text, resolver, sport=Sport.NFL):
    return parse_cbs_block(
        text, resolver=resolver, sport=sport, season=2025, week=3, posted_at=POSTED
    )


def test_home_favorite_becomes_negative_home_spread(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0", resolver)
    assert result.games[0].league_line.spread_home == -3.0


def test_away_favorite_becomes_positive_home_spread(resolver):
    # KC favored by 6.5 on the road: the home team is the +6.5 underdog.
    result = parse("Kansas City Chiefs -6.5 at New York Jets", resolver)
    assert result.games[0].league_line.spread_home == 6.5


def test_explicit_home_underdog_sign_is_preserved(resolver):
    result = parse("Green Bay Packers at Chicago Bears +2.5", resolver)
    assert result.games[0].league_line.spread_home == 2.5


def test_game_id_matches_canonical_construction(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0", resolver)
    assert result.games[0].league_line.game_id == "nfl-2025-03-BUF-at-MIA"


def test_text_result_keeps_game_and_league_line_together(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0", resolver)
    [parsed] = result.games
    assert parsed.game.game_id == "nfl-2025-03-BUF-at-MIA"
    assert parsed.game.away_team_id == "BUF"
    assert parsed.game.home_team_id == "MIA"
    assert parsed.game.kickoff_utc == POSTED
    assert parsed.league_line.game_id == parsed.game.game_id
    assert parsed.league_line.spread_home == -3.0


def test_noise_lines_are_reported_not_silently_dropped(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0\nBye: Cleveland Browns", resolver)
    assert len(result.games) == 1
    assert any("Bye" in s for s in result.skipped)


def test_unknown_team_raises(resolver):
    with pytest.raises(UnknownTeamError):
        parse("Fictional State at Miami Dolphins -3.0", resolver, sport=Sport.CFB)


def test_parses_the_nfl_portion_of_the_fixture(resolver):
    # A real sheet mixes sports; each is ingested with its own --sport run.
    nfl_lines = [
        line
        for line in FIXTURE.read_text().splitlines()
        if "at" in line
        and "Ole Miss" not in line
        and "Ohio State" not in line
        and "Michigan" not in line
    ]
    result = parse("\n".join(nfl_lines), resolver)
    assert len(result.games) == 3
    assert [parsed.league_line.spread_home for parsed in result.games] == [-3.0, 6.5, 2.5]


def test_parses_the_cfb_portion_of_the_fixture(resolver):
    text = "Ole Miss at Alabama -7.5\nOhio State -14.0 at Michigan"
    result = parse(text, resolver, sport=Sport.CFB)
    assert [parsed.league_line.spread_home for parsed in result.games] == [-7.5, 14.0]


def test_pickem_pushes_are_allowed(resolver):
    # A pick'em game (no favorite) is a legitimate 0.0 line, not a parse failure.
    result = parse("Buffalo Bills at Miami Dolphins PK", resolver)
    assert result.games[0].league_line.spread_home == 0.0


def test_dual_number_lines_are_reported_as_ambiguous(resolver):
    # A line with numbers on both sides (e.g., game line + total) is ambiguous
    # and must be reported in skipped, not silently resolved to the home number.
    text = "Buffalo Bills at Miami Dolphins -3.0\nKansas City Chiefs -6.5 at New York Jets 45.5"
    result = parse(text, resolver)
    assert len(result.games) == 1  # only the first line parses
    assert result.games[0].league_line.spread_home == -3.0
    assert any("45.5" in s for s in result.skipped)  # second line is reported


def test_a_block_with_no_games_at_all_raises():
    # The most manual step in the system: the user pasted the wrong thing.
    with pytest.raises(CbsParseError):
        parse_cbs_block(
            "Week 3 picks\nsubmit by Sunday\n",
            resolver=TeamResolver.default(),
            sport=Sport.NFL,
            season=2025,
            week=3,
            posted_at=POSTED,
        )
