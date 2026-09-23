"""Seed a store that matches tests/fixtures/cbs_results_page.html (pool week 2)."""

from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from pickem.ingest.cbs_results import ParsedStandings, parse_cbs_results_html
from pickem.models import Game, LeagueLine, Sport, make_game_id
from pickem.operations.results_import import import_results
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store

FIXTURE = Path("tests/fixtures/cbs_results_page.html")
JOTA_ID = "ivxhi4tzhizdiojwgq2tgmjv"
KICK = datetime(2026, 9, 12, 16, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 9, 15, 12, tzinfo=UTC)

# (sport, league week, away, home, CBS line, away score, home score)
SEEDED = [
    (Sport.NFL, 1, "NE", "SEA", -3.5, 10, 13),
    (Sport.NFL, 1, "NO", "DET", -7.5, 30, 31),
    (Sport.CFB, 2, "OU", "MICH", 5.5, 10, 17),
    (Sport.CFB, 2, "PSU", "TEM", 24.5, 27, 9),
]


def seed(store: Store, rows=SEEDED, *, scores: bool = True) -> None:
    for sport, week, away, home, spread, away_score, home_score in rows:
        game_id = make_game_id(sport, 2026, week, away, home)
        store.upsert_games([
            Game(
                game_id=game_id, sport=sport, season=2026, week=week, kickoff_utc=KICK,
                home_team_id=home, away_team_id=away,
                home_score=home_score if scores else None,
                away_score=away_score if scores else None,
            )
        ])
        store.upsert_league_lines([
            LeagueLine(game_id=game_id, season=2026, week=week, spread_home=spread,
                       posted_at=KICK - timedelta(days=4))
        ])


def parsed_fixture() -> ParsedStandings:
    return parse_cbs_results_html(FIXTURE.read_text(encoding="utf-8"))


def imported_store() -> Store:
    store = Store(":memory:")
    store.init_schema()
    seed(store)
    import_results(
        store, parsed_fixture(), season=2026, pool_week=2,
        resolver=TeamResolver.default(), imported_at=IMPORTED_AT,
    )
    return store


_VOID = {"meta", "br", "hr", "img", "input", "link", "wbr"}


class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1:] or 'nothing'}")
        else:
            self.stack.pop()


def assert_well_formed(text: str) -> None:
    """Every element the page opens is closed, in order (SVG uses self-closing tags)."""
    checker = _Balance()
    checker.feed(text)
    checker.close()
    assert not checker.errors, checker.errors[:5]
    assert not checker.stack, f"unclosed: {checker.stack}"
