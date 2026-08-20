"""The HTML path, pinned against a real saved CBS page.

The fixture is trimmed from an actual saved page rather than hand-written, on
the same reasoning as the odds archive fixture: the bugs that cost the most
here were in assumptions a hand-built fixture would have encoded rather than
caught.
"""

import pathlib
from datetime import UTC, datetime

import pytest

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_html import parse_cbs_html
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

PAGE = pathlib.Path("tests/fixtures/cbs_picks_page.html").read_text()
POSTED = datetime(2026, 8, 19, 23, 38, tzinfo=UTC)


@pytest.fixture(scope="module")
def resolver():
    return TeamResolver.default()


def parse(text=PAGE, resolver=None, week=1):
    return parse_cbs_html(
        text,
        resolver=resolver or TeamResolver.default(),
        sport=Sport.CFB,
        season=2026,
        week=week,
        posted_at=POSTED,
    )


def test_every_game_is_extracted_once_despite_the_duplicated_payload(resolver):
    """Catches a parser that counts each game twice.

    CBS emits the same events in two separate Apollo blobs. Duplicated league
    lines would be absorbed by the primary key, but the count reported to the
    operator would be wrong and a real drop could hide behind it.
    """
    result = parse(resolver=resolver)

    assert len(result.lines) == 3
    assert len(set(line.game_id for line in result.lines)) == 3


def test_a_home_favourite_keeps_its_negative_spread(resolver):
    """Catches a sign flip on the home side.

    CBS's `homeTeamSpread` is already home-perspective favourite-negative,
    which is this codebase's convention, so it passes through unchanged.
    Alabama -28.5 over East Carolina.
    """
    result = parse(resolver=resolver)
    by_id = {line.game_id: line for line in result.lines}

    assert by_id["cfb-2026-01-ECU-at-BAMA"].spread_home == -28.5


def test_an_away_favourite_keeps_its_positive_spread(resolver):
    """Catches a parser that negates, or takes the absolute value of, the spread.

    Oklahoma State is favoured by 14.5 at Tulsa, so the home line is +14.5. A
    parser that flipped signs would look correct on every home favourite in the
    slate and be wrong only here.
    """
    result = parse(resolver=resolver)
    by_id = {line.game_id: line for line in result.lines}

    assert by_id["cfb-2026-01-OKST-at-TLSA"].spread_home == 14.5


def test_kickoffs_are_real_instants_not_midnight(resolver):
    """Catches a date-only kickoff, the bug that already bit nflverse._kickoff.

    CBS ships epoch milliseconds, so there is no excuse for a placeholder here.
    East Carolina at Alabama kicks at 16:00 UTC.
    """
    result = parse(resolver=resolver)

    assert result.kickoffs["cfb-2026-01-ECU-at-BAMA"] == datetime(2026, 9, 5, 16, 0, tzinfo=UTC)


def test_an_unknown_school_aborts_the_whole_page(resolver):
    """Catches a silent drop on the one source where every row must be picked.

    The CBS sheet is the list of games we are required to pick. Unlike the odds
    firehose, an unresolvable name here is a week we cannot submit, not a row
    to pass over.
    """
    broken = PAGE.replace('"mediumName": "Alabama"', '"mediumName": "Notaschool Tech"')

    with pytest.raises(UnknownTeamError):
        parse(broken, resolver=resolver)


def test_a_page_with_no_payload_raises_rather_than_returning_nothing(resolver):
    """Catches the JavaScript-rendered case degrading into an empty week.

    If CBS ever stops server-rendering the payload, the saved file becomes a
    shell. Returning zero games would read as "no games this week".
    """
    with pytest.raises(CbsParseError):
        parse("<html><body><div id='root'></div></body></html>", resolver=resolver)


def test_the_cli_stores_the_page_with_its_real_kickoffs(tmp_path):
    """Catches an HTML ingest that keeps the text path's placeholder kickoff.

    `poll-odds` filters the odds firehose by a kickoff window before it resolves
    any name, so a placeholder kickoff would put every game outside the window
    and store nothing — while reporting success.
    """
    from typer.testing import CliRunner

    from pickem import cli
    from pickem.store.db import Store

    db = tmp_path / "cfb.duckdb"
    result = CliRunner().invoke(
        cli.app,
        [
            "ingest-cbs",
            "--html",
            "--file",
            "tests/fixtures/cbs_picks_page.html",
            "--sport",
            "cfb",
            "--season",
            "2026",
            "--week",
            "1",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code == 0, result.output
    with Store(db) as store:
        stored = {g.game_id: g for g in store.games_for_week(Sport.CFB, 2026, 1)}
        lines = store.league_lines_for_week(Sport.CFB, 2026, 1)

    assert len(lines) == 3
    assert stored["cfb-2026-01-ECU-at-BAMA"].kickoff_utc == datetime(2026, 9, 5, 16, 0, tzinfo=UTC)
