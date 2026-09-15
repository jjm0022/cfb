"""Parser for a saved CBS Weekly Standings page.

The standings table is server-rendered DOM, not part of the Apollo blob: on a
saved results page that blob carries the *current* period's events, not the
week on screen. So this reads the table itself.

Keyed on `data-testid` attributes, never on the hashed `mui-*` classes that
change on every CBS deploy. The one class read is MUI's semantic
`MuiSvgIcon-colorSuccess` / `MuiSvgIcon-colorError` on the grade icon: MUI
derives it from the icon's `color` prop, so it survives rebuilds, and CBS gives
the grade no test id of its own.

Fails closed. A misread standings page would silently corrupt every conclusion
drawn from it, so anything unexpected raises `CbsParseError`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

from loguru import logger

from pickem.ingest.cbs import CbsParseError
from pickem.models import Sport

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
     "source", "track", "wbr"}
)
_LINK_RE = re.compile(r"/(NFL|NCAAF)_(\d{8})_([A-Z0-9&]+)@([A-Z0-9&]+)/")
_ORDINAL_RE = re.compile(r"^(\d+)(?:st|nd|rd|th)$")
_FINAL_RE = re.compile(r"^FINAL(?:/\d*OT)?$")
_SPREAD_RE = re.compile(r"^\(\s*([+-]?\d+(?:\.\d+)?|PK|EVEN)\s*\)$")
_SPORTS = {"NFL": Sport.NFL, "NCAAF": Sport.CFB}
_HEADER = "event-hdr-cell-"
_ROW = "pickem-weekly-table-row-"
_CELL = "pickem-weekly-table-cell-"
_BLANK = "-"
_CORRECT = "MuiSvgIcon-colorSuccess"
_INCORRECT = "MuiSvgIcon-colorError"


@dataclass(frozen=True)
class StandingsGame:
    cbs_event_id: int
    sport: Sport
    game_date: date
    away_abbrev: str
    home_abbrev: str
    status: str
    away_score: int
    home_score: int
    spread_home: float


@dataclass(frozen=True)
class StandingsEntrant:
    entry_id: str
    name: str
    rank: int
    points: int
    ytd: int
    tiebreak: int | None


@dataclass(frozen=True)
class StandingsPick:
    entry_id: str
    cbs_event_id: int
    picked_abbrev: str | None
    cbs_correct: bool | None


@dataclass(frozen=True)
class ParsedStandings:
    games: tuple[StandingsGame, ...]
    entrants: tuple[StandingsEntrant, ...]
    picks: tuple[StandingsPick, ...]


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)

    @property
    def testid(self) -> str:
        return self.attrs.get("data-testid", "")

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self) -> Iterator[_Node]:
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.walk()

    def child_elements(self, tag: str) -> list[_Node]:
        return [c for c in self.children if isinstance(c, _Node) and c.tag == tag]

    def first(self, testid: str) -> _Node | None:
        return next((node for node in self.walk() if node.testid == testid), None)


class _TreeBuilder(HTMLParser):
    """Just enough DOM to find elements by attribute and read their text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {name: value or "" for name, value in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(_Node(tag, {name: value or "" for name, value in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def parse_cbs_results_html(text: str) -> ParsedStandings:
    """Read a saved CBS Weekly Standings page into games, entrants and picks."""
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()

    table = next(
        (
            node
            for node in builder.root.walk()
            if node.tag == "table" and node.attrs.get("aria-label") == "Weekly Standings"
        ),
        None,
    )
    if table is None:
        raise CbsParseError(
            "no Weekly Standings table found — save the CBS Standings > Weekly page "
            "after it has finished loading"
        )

    games = tuple(_game(node) for node in table.walk() if node.testid.startswith(_HEADER))
    if not games:
        raise CbsParseError("the Weekly Standings table has no game header cells")
    by_event = {game.cbs_event_id: game for game in games}
    if len(by_event) != len(games):
        raise CbsParseError("the Weekly Standings table lists a game header twice")

    entrants: list[StandingsEntrant] = []
    picks: list[StandingsPick] = []
    for row in (node for node in table.walk() if node.testid.startswith(_ROW)):
        entrant, row_picks = _row(row, by_event)
        entrants.append(entrant)
        picks.extend(row_picks)
    if not entrants:
        raise CbsParseError("the Weekly Standings table has no entrant rows")

    blank = sum(1 for pick in picks if pick.picked_abbrev is None)
    logger.bind(
        event="results_page_parsed",
        games=len(games),
        entrants=len(entrants),
        picks=len(picks),
        blank_picks=blank,
    ).info(
        f"parsed standings page: {len(games)} games, {len(entrants)} entrants, "
        f"{len(picks)} picks ({blank} blank)"
    )
    return ParsedStandings(games, tuple(entrants), tuple(picks))


def _game(node: _Node) -> StandingsGame:
    raw_id = node.testid.removeprefix(_HEADER)
    if not raw_id.isdigit():
        raise CbsParseError(f"game header has a non-numeric event id {raw_id!r}")
    event_id = int(raw_id)

    link = _LINK_RE.search(node.attrs.get("href", ""))
    if link is None:
        raise CbsParseError(
            f"game {event_id}: header has no gametracker link to read sport and date from"
        )
    sport_tag, day, _, _ = link.groups()

    status = _text_of(node, "event-status", event_id)
    if not _FINAL_RE.match(status):
        raise CbsParseError(
            f"game {event_id}: status is {status!r}, not final — save the page after "
            "every game has finished"
        )

    abbrevs = _span_texts(node, "team-abbrev", event_id)
    scores = _span_texts(node, "team-score", event_id)
    if len(abbrevs) != 2 or len(scores) != 2:
        raise CbsParseError(
            f"game {event_id}: expected away and home in team-abbrev and team-score, "
            f"got {abbrevs} and {scores}"
        )
    try:
        away_score, home_score = int(scores[0]), int(scores[1])
    except ValueError as exc:
        raise CbsParseError(f"game {event_id}: scores {scores} are not integers") from exc

    return StandingsGame(
        cbs_event_id=event_id,
        sport=_SPORTS[sport_tag],
        game_date=date(int(day[:4]), int(day[4:6]), int(day[6:])),
        away_abbrev=abbrevs[0],
        home_abbrev=abbrevs[1],
        status=status,
        away_score=away_score,
        home_score=home_score,
        spread_home=_spread(_text_of(node, "team-spread", event_id), event_id),
    )


def _text_of(node: _Node, testid: str, event_id: int) -> str:
    child = node.first(testid)
    if child is None:
        raise CbsParseError(f"game {event_id}: header has no {testid}")
    return child.text().strip()


def _span_texts(node: _Node, testid: str, event_id: int) -> list[str]:
    child = node.first(testid)
    if child is None:
        raise CbsParseError(f"game {event_id}: header has no {testid}")
    return [span.text().strip() for span in child.child_elements("span")]


def _spread(text: str, event_id: int) -> float:
    match = _SPREAD_RE.match(text)
    if match is None:
        raise CbsParseError(f"game {event_id}: line {text!r} is not a parenthesized spread")
    value = match.group(1)
    return 0.0 if value in {"PK", "EVEN"} else float(value)


def _row(
    row: _Node, games: dict[int, StandingsGame]
) -> tuple[StandingsEntrant, list[StandingsPick]]:
    entry_id = row.testid.removeprefix(_ROW)

    name_cell = row.first("standings-player-name-cell")
    if name_cell is None:
        raise CbsParseError(f"entry {entry_id}: row has no player name cell")
    spans = [node.text().strip() for node in name_cell.walk() if node.tag == "span"]
    rank_index = next((i for i, text in enumerate(spans) if _ORDINAL_RE.match(text)), None)
    names = [text for text in spans[rank_index + 1 :] if text] if rank_index is not None else []
    if rank_index is None or not names:
        raise CbsParseError(f"entry {entry_id}: could not read rank and name from {spans}")
    rank = int(_ORDINAL_RE.match(spans[rank_index]).group(1))

    tiebreak_cell = row.first("weekly-tb-cell")
    tiebreak_text = tiebreak_cell.text().strip() if tiebreak_cell is not None else ""
    if tiebreak_text and not tiebreak_text.isdigit():
        raise CbsParseError(f"entry {entry_id}: tiebreaker {tiebreak_text!r} is not an integer")

    entrant = StandingsEntrant(
        entry_id=entry_id,
        name=names[0],
        rank=rank,
        points=_int_cell(row, "weekly-pts-cell", entry_id),
        ytd=_int_cell(row, "weekly-ytd-cell", entry_id),
        tiebreak=int(tiebreak_text) if tiebreak_text else None,
    )

    picks: list[StandingsPick] = []
    for cell in (node for node in row.walk() if node.testid.startswith(_CELL)):
        raw_id = cell.testid.removeprefix(_CELL)
        game = games.get(int(raw_id)) if raw_id.isdigit() else None
        if game is None:
            raise CbsParseError(f"entry {entry_id}: pick for game {raw_id}, which has no header")
        picks.append(_pick(cell, entry_id, game))

    covered = sorted(pick.cbs_event_id for pick in picks)
    if covered != sorted(games):
        raise CbsParseError(
            f"entry {entry_id}: picks cover games {covered}, but the header lists {sorted(games)}"
        )
    return entrant, picks


def _int_cell(row: _Node, testid: str, entry_id: str) -> int:
    cell = row.first(testid)
    text = cell.text().strip() if cell is not None else ""
    if not text.isdigit():
        raise CbsParseError(f"entry {entry_id}: {testid} {text!r} is not an integer")
    return int(text)


def _pick(cell: _Node, entry_id: str, game: StandingsGame) -> StandingsPick:
    where = f"entry {entry_id}, game {game.cbs_event_id}"
    team = cell.text().strip()
    icon_classes = " ".join(
        node.attrs.get("class", "") for node in cell.walk() if node.tag == "svg"
    )

    if team == _BLANK:
        if icon_classes:
            raise CbsParseError(f"{where}: a blank pick carries a grade icon")
        return StandingsPick(entry_id, game.cbs_event_id, None, None)

    if team not in (game.away_abbrev, game.home_abbrev):
        raise CbsParseError(
            f"{where}: picked {team!r}, which is neither {game.away_abbrev} nor {game.home_abbrev}"
        )
    correct = _CORRECT in icon_classes
    incorrect = _INCORRECT in icon_classes
    if correct == incorrect:
        raise CbsParseError(
            f"{where}: unrecognized grade icon (classes {icon_classes!r}) — CBS may be "
            "marking a push or a state this parser has never seen"
        )
    return StandingsPick(entry_id, game.cbs_event_id, team, correct)
