import re
from datetime import date
from pathlib import Path

import pytest

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_results import (
    StandingsEntrant,
    StandingsGame,
    StandingsPick,
    parse_cbs_results_html,
)
from pickem.models import Sport

FIXTURE = Path("tests/fixtures/cbs_results_page.html")
JOTA = "ivxhi4tzhizdiojwgq2tgmjv"


@pytest.fixture(scope="module")
def page() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_reads_every_game_header(page):
    parsed = parse_cbs_results_html(page)
    assert parsed.games == (
        StandingsGame(50029202, Sport.NFL, date(2026, 9, 9), "NE", "SEA", "FINAL", 10, 13, -3.5),
        StandingsGame(
            50027615, Sport.CFB, date(2026, 9, 12), "OKLA", "MICH", "FINAL", 10, 17, 5.5
        ),
        StandingsGame(
            50027628, Sport.CFB, date(2026, 9, 12), "PSU", "TEMPLE", "FINAL", 27, 9, 24.5
        ),
        StandingsGame(
            50029215, Sport.NFL, date(2026, 9, 13), "NO", "DET", "FINAL/OT", 30, 31, -7.5
        ),
    )


def test_reads_every_entrant_row(page):
    parsed = parse_cbs_results_html(page)
    assert parsed.entrants == (
        StandingsEntrant("fixtureentrya", "Entrant A", 1, 20, 28, 44),
        StandingsEntrant(JOTA, "Jota", 17, 16, 24, 41),
        StandingsEntrant("fixtureentryb", "Entrant B", 25, 16, 24, 56),
        StandingsEntrant("fixtureentryc", "Entrant C", 53, 6, 13, 52),
    )


def test_reads_picks_grades_and_blanks(page):
    picks = {(p.entry_id, p.cbs_event_id): p for p in parse_cbs_results_html(page).picks}
    assert len(picks) == 16
    assert picks[(JOTA, 50029202)] == StandingsPick(JOTA, 50029202, "SEA", False)
    assert picks[(JOTA, 50027615)] == StandingsPick(JOTA, 50027615, "MICH", True)
    assert picks[("fixtureentryb", 50029202)] == StandingsPick(
        "fixtureentryb", 50029202, None, None
    )
    assert picks[("fixtureentryc", 50027628)].picked_abbrev is None


def test_page_without_standings_table_is_rejected():
    with pytest.raises(CbsParseError, match="no Weekly Standings table"):
        parse_cbs_results_html("<html><body><p>Lobby</p></body></html>")


def test_game_that_is_not_final_is_rejected(page):
    unfinished = page.replace('event-status">FINAL/OT<', 'event-status">4th 2:11<')
    with pytest.raises(CbsParseError, match="not final"):
        parse_cbs_results_html(unfinished)


def test_header_without_gametracker_link_is_rejected(page):
    broken = page.replace("NFL_20260909_NE@SEA", "unknown")
    with pytest.raises(CbsParseError, match="no gametracker link"):
        parse_cbs_results_html(broken)


def test_unrecognized_grade_icon_is_rejected(page):
    broken = page.replace("MuiSvgIcon-colorSuccess", "MuiSvgIcon-colorWarning", 1)
    with pytest.raises(CbsParseError, match="unrecognized grade icon"):
        parse_cbs_results_html(broken)


def test_row_missing_a_pick_cell_is_rejected(page):
    broken = re.sub(
        r'<td[^>]*data-testid="pickem-weekly-table-cell-50027615".*?</td>',
        "",
        page,
        count=1,
        flags=re.S,
    )
    with pytest.raises(CbsParseError, match="picks cover games"):
        parse_cbs_results_html(broken)


def test_pick_for_a_game_without_header_is_rejected(page):
    broken = page.replace(
        'data-testid="pickem-weekly-table-cell-50027615"',
        'data-testid="pickem-weekly-table-cell-99999999"',
        1,
    )
    with pytest.raises(CbsParseError, match="which has no header"):
        parse_cbs_results_html(broken)


def test_picked_team_outside_the_game_is_rejected(page):
    broken = re.sub(
        r'(pickem-weekly-table-cell-50027615".*?>)MICH(<)',
        r"\1BAMA\2",
        page,
        count=1,
        flags=re.S,
    )
    with pytest.raises(CbsParseError, match="neither OKLA nor MICH"):
        parse_cbs_results_html(broken)


def test_tiebreak_placeholder_reads_as_no_tiebreak(page):
    broken = page.replace('mui-1hkd266">41</span>', 'mui-1hkd266">-</span>')
    parsed = parse_cbs_results_html(broken)
    jota = next(e for e in parsed.entrants if e.entry_id == JOTA)
    assert jota.tiebreak is None


def test_non_integer_tiebreak_is_rejected(page):
    broken = page.replace('mui-1hkd266">41</span>', 'mui-1hkd266">x</span>')
    with pytest.raises(CbsParseError, match="is not an integer"):
        parse_cbs_results_html(broken)
