import pytest
from cbs_entry_helpers import ATL, ATL_GB, GB, LOU, WAKE_LOU, entry, entry_page, event

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_entry import parse_entry_picks
from pickem.models import Side
from pickem.resolve.resolver import TeamResolver

NFL_GAME = "nfl-2026-03-ATL-at-GB"
CFB_GAME = "cfb-2026-04-WAKE-at-LOU"


def parse(html: str):
    return parse_entry_picks(html, resolver=TeamResolver.default(), season=2026, pool_week=4)


def test_each_pick_becomes_the_side_of_its_game():
    board = parse(entry_page(entry({ATL_GB["cbsEventId"]: ATL, WAKE_LOU["cbsEventId"]: LOU})))

    assert board.picks == {NFL_GAME: Side.AWAY, CFB_GAME: Side.HOME}
    assert board.unresolved == ()


def test_a_game_without_a_pick_is_listed_with_no_side():
    board = parse(entry_page(entry({ATL_GB["cbsEventId"]: GB})))

    assert board.picks == {NFL_GAME: Side.HOME, CFB_GAME: None}


def test_an_entry_before_any_picks_lists_every_game_unpicked():
    board = parse(entry_page(entry({})))

    assert board.picks == {NFL_GAME: None, CFB_GAME: None}


def test_a_repeated_copy_of_the_same_entry_is_not_a_second_entry():
    same = entry({ATL_GB["cbsEventId"]: ATL})

    assert parse(entry_page(same, same)).picks[NFL_GAME] is Side.AWAY


def test_other_peoples_entries_are_ignored():
    board = parse(
        entry_page(
            entry({ATL_GB["cbsEventId"]: GB}, entry_id="THEM", mine=False),
            entry({ATL_GB["cbsEventId"]: ATL}),
        )
    )

    assert board.picks[NFL_GAME] is Side.AWAY


def test_a_page_with_no_entry_of_mine_is_refused():
    with pytest.raises(CbsParseError, match="no entry marked as yours"):
        parse(entry_page(entry({}, mine=False)))


def test_two_different_entries_of_mine_are_refused():
    with pytest.raises(CbsParseError, match="2 entries"):
        parse(entry_page(entry({}, entry_id="ONE"), entry({}, entry_id="TWO")))


def test_a_pick_naming_a_team_not_in_its_game_is_refused():
    with pytest.raises(CbsParseError, match="neither side"):
        parse(entry_page(entry({ATL_GB["cbsEventId"]: LOU})))


def test_a_pick_on_a_game_not_on_the_page_is_refused():
    with pytest.raises(CbsParseError, match="not on the page"):
        parse(entry_page(entry({99999999: ATL})))


def test_an_event_whose_teams_do_not_resolve_is_reported_not_raised():
    mystery = event(1, sport="NFL", away="Nowhere", home="Green Bay", away_id=1, home_id=GB)

    board = parse(entry_page(entry({}), events=(ATL_GB, mystery)))

    assert board.picks == {NFL_GAME: None}
    assert board.unresolved == ("Nowhere at Green Bay",)


def test_a_page_with_no_events_is_refused():
    with pytest.raises(CbsParseError, match="no CBS event"):
        parse(entry_page(entry({}), events=()))
