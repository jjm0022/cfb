# Weekly Results Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import each saved CBS Weekly Standings page, grade us / the model / the field / baselines, and write a Markdown report plus a Discord DM every pool week.

**Architecture:** A `data-testid`-keyed parser turns the saved page into plain records. An importer links them to stored games, cross-checks CBS's grading, and writes `pool_results`/`pool_picks`. A new append-only `recommendation_history` table (fed by the bot's monitor, `report`, and a one-off backfill) supplies "the last recommendation before kickoff". A pure grading module builds a `ResultsReport`, which a Markdown renderer and a one-shot Discord REST sender consume.

**Tech Stack:** Python 3.12, DuckDB, pydantic, typer, loguru, discord.py 2.x, stdlib `html.parser`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-weekly-results-tracking-design.md`

## Global Constraints

- Python `>=3.12`; ruff `line-length = 100`, lint rules `E, F, I, UP, B`. Run `uv run ruff check src tests` before every commit.
- No new dependencies. HTML is parsed with stdlib `html.parser`.
- Tests: `uv run pytest`. Async bot tests use `@pytest.mark.asyncio`; monitor tests use `asyncio.run(...)`, like their neighbours.
- Logging: `logger.bind(event="<snake_case>", ...).<level>(message)`, as in the existing code. Commands run inside `run_context("cli:<command>", ...)`.
- `data/` is gitignored. Never commit anything under `data/`. Real entrant names never go into committed files; only `Jota` (the user's entry) may appear.
- The working tree has **uncommitted user work** in `README.md`, `src/pickem/automation/__init__.py`, `src/pickem/automation/monitor.py`, `src/pickem/discord_bot.py`, `tests/test_discord_bot.py`, plus untracked `scripts/` and two untracked test files. Never revert, reformat, or stash it. Stage only the exact paths each task names (`git add <path>…`, never `git add -A` or `git add .`). Task 6 and Task 11 touch files with uncommitted user work: **stop before those tasks and ask the user to commit or approve including their pending changes.**
- Pool week N = CFB week N + NFL week N−1. Pool week 1 has no NFL board.
- "The model's pick" = the `recommendation_history` row with the greatest `generated_at` strictly before the game's `kickoff_utc`.
- Every commit message ends with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

## Spec clarifications made while planning

1. **Scores.** Grading needs final scores, but `games` rows may not have them yet if `sync-results` has not run. The import therefore writes CBS's final score into a stored game **only when that game has no score**. A stored score that disagrees still aborts the import.
2. **Log backfill source.** The JSONL logs carry structured `edge_decided` records (`ts`, `game_id`, `side`, `tier`, `delta`). The backfill reads those fields instead of regex-parsing the message text.
3. **The field excludes us.** Field shares and the field consensus are computed over entrants other than `--entry-name`.
4. **Discord field limit.** `_field_value_chunks` in `discord_bot.py` splits on pick boundaries, which results text does not have. The DM module caps each field at 1024 characters itself.
5. **Final status.** Accepted statuses match `^FINAL(?:/\d*OT)?$`, so `FINAL`, `FINAL/OT` and `FINAL/2OT` all count.

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/pickem/resolve/aliases.yaml` | modify | CBS standings abbreviations |
| `src/pickem/ingest/cbs_results.py` | create | Saved Weekly Standings HTML → `ParsedStandings` (pure) |
| `src/pickem/models.py` | modify | `PoolResult`, `PoolPick`, `RecommendationRecord`, history source constants |
| `src/pickem/store/schema.sql` | modify | `pool_results`, `pool_picks`, `recommendation_history` |
| `src/pickem/store/db.py` | modify | Read/write methods for the new tables and by-id loaders |
| `src/pickem/operations/results_import.py` | create | Pool→league week, link, validate, store |
| `src/pickem/operations/recommendation_history.py` | create | Snapshot → history rows; backfill from `picks` and logs |
| `src/pickem/automation/monitor.py` | modify | Optional `record_history` callback |
| `src/pickem/discord_bot.py` | modify | Wire `record_history` into each monitor |
| `src/pickem/report/results.py` | create | Grading, aggregates, standings, findings, `build_results_report` |
| `src/pickem/report/results_markdown.py` | create | `render_results_report` |
| `src/pickem/notify/__init__.py`, `src/pickem/notify/discord_dm.py` | create | Results embed and one-shot REST DM |
| `src/pickem/config.py` | modify | `DEFAULT_RESULTS_DIR`, `DEFAULT_ENTRY_NAME` |
| `src/pickem/cli.py` | modify | `import-results`, `results-report`, `backfill-recommendations`; `report` records history |
| `tests/fixtures/cbs_results_page.html` | create | Trimmed, anonymized week-2 standings page |
| `tests/results_helpers.py` | create | Shared seeding for results tests |
| `tests/test_cbs_abbreviations.py`, `tests/test_cbs_results.py`, `tests/test_results_store.py`, `tests/test_results_import.py`, `tests/test_recommendation_history.py`, `tests/test_results_report.py`, `tests/test_results_markdown.py`, `tests/test_discord_dm.py`, `tests/test_results_cli.py`, `tests/test_results_real_pages.py` | create | Tests |
| `tests/test_monitor.py`, `tests/test_discord_bot.py`, `tests/test_cli.py` | modify | Live history recording tests |
| `README.md` | modify | Weekly workflow step for results |

---

### Task 1: CBS standings abbreviations

**Files:**
- Modify: `src/pickem/resolve/aliases.yaml` (CFB lines for ASU, BOIS, CIN, CSU, GT, ISU, KSU, LOU, MRSH, MSST, OKST, ORE, ORST, OSU, OU, TAM, TTU, WIS, WSU)
- Test: `tests/test_cbs_abbreviations.py`

**Interfaces:**
- Consumes: `TeamResolver.default().resolve(name, sport)` (existing).
- Produces: every abbreviation on the 2026 pool week 1–2 standings pages resolves.

- [ ] **Step 1: Write the failing test**

```python
"""CBS prints its own team abbreviations on the Weekly Standings page.

Pinned from the saved 2026 pool week 1-2 pages. Several already resolve through
existing spellings; they are pinned too, so a later table edit cannot break them.
"""

import pytest

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

CBS_CFB = {
    "ARIZST": "ASU",
    "BOISE": "BOIS",
    "CINCY": "CIN",
    "COLOST": "CSU",
    "GATECH": "GT",
    "IOWAST": "ISU",
    "KSTATE": "KSU",
    "LVILLE": "LOU",
    "MISSST": "MSST",
    "MRSHL": "MRSH",
    "OHIOST": "OSU",
    "OKLA": "OU",
    "OKLAST": "OKST",
    "OREG": "ORE",
    "OREGST": "ORST",
    "TXAM": "TAM",
    "TXTECH": "TTU",
    "WASHST": "WSU",
    "WISC": "WIS",
    "AUBURN": "AUB",
    "BAYLOR": "BAY",
    "TEMPLE": "TEM",
    "TEXAS": "TEX",
    "TULANE": "TULN",
    "TULSA": "TLSA",
    "BAMA": "BAMA",
    "UK": "UK",
}


@pytest.fixture(scope="module")
def resolver():
    return TeamResolver.default()


@pytest.mark.parametrize(("abbrev", "team_id"), sorted(CBS_CFB.items()))
def test_cbs_cfb_abbreviation_resolves(resolver, abbrev, team_id):
    assert resolver.resolve(abbrev, Sport.CFB) == team_id


def test_cbs_nfl_jacksonville_abbreviation_resolves(resolver):
    assert resolver.resolve("JAC", Sport.NFL) == "JAX"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cbs_abbreviations.py -v`
Expected: 19 FAIL with `UnknownTeamError` (ARIZST … WISC); the pinned existing spellings PASS.

- [ ] **Step 3: Add the abbreviations**

In `src/pickem/resolve/aliases.yaml`, append one alias to the end of each of these CFB lists. Only the list contents change:

```yaml
  "ASU":   ["Arizona State", "Arizona State Sun Devils", "ASU", "Arizona St.", "ARIZST"]
  "BOIS":  ["Boise State", "Boise State Broncos", "BOIS", "Boise St.", "BOISE"]
  "CIN":   ["Cincinnati", "Cincinnati Bearcats", "CIN", "CINCY"]
  "CSU":   ["Colorado State", "Colorado State Rams", "CSU", "Colorado St.", "COLOST"]
  "GT":    ["Georgia Tech", "Georgia Tech Yellow Jackets", "GT", "GATECH"]
  "ISU":   ["Iowa State", "Iowa State Cyclones", "ISU", "Iowa St.", "IOWAST"]
  "KSU":   ["Kansas State", "Kansas State Wildcats", "KSU", "Kansas St.", "KSTATE"]
  "LOU":   ["Louisville", "Louisville Cardinals", "LOU", "LVILLE"]
  "MRSH":  ["Marshall", "Marshall Thundering Herd", "MRSH", "MRSHL"]
  "MSST":  ["Mississippi State", "Mississippi St", "Miss State", "Mississippi State Bulldogs", "MSST", "Mississippi St.", "Miss St.", "MISSST"]
  "OKST":  ["Oklahoma State", "Oklahoma State Cowboys", "OKST", "Oklahoma St.", "OKLAST"]
  "ORE":   ["Oregon", "Oregon Ducks", "ORE", "OREG"]
  "ORST":  ["Oregon State", "Oregon State Beavers", "ORST", "Oregon St.", "OREGST"]
  "OSU":   ["Ohio State", "Ohio St", "Ohio State Buckeyes", "OSU", "Ohio St.", "OHIOST"]
  "OU":    ["Oklahoma", "Oklahoma Sooners", "OU", "OKLA"]
  "TAM":   ["Texas A&M", "Texas A&M Aggies", "TA&M", "TXAM"]
  "TTU":   ["Texas Tech", "Texas Tech Red Raiders", "TTU", "TXTECH"]
  "WIS":   ["Wisconsin", "Wisconsin Badgers", "WIS", "WISC"]
  "WSU":   ["Washington State", "Washington State Cougars", "WSU", "Washington St.", "WASHST"]
```

The resolver raises `ValueError` at load if an alias is claimed twice, so a collision fails every test loudly.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cbs_abbreviations.py tests/test_cfb_aliases.py tests/test_resolver.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/resolve/aliases.yaml tests/test_cbs_abbreviations.py
git commit -m "feat: resolve the team abbreviations CBS prints on its standings page

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 2: Weekly Standings parser

**Files:**
- Create: `tests/fixtures/cbs_results_page.html` (generated, then committed)
- Create: `src/pickem/ingest/cbs_results.py`
- Test: `tests/test_cbs_results.py`

**Interfaces:**
- Consumes: `CbsParseError` from `pickem.ingest.cbs`; `Sport` from `pickem.models`.
- Produces:
  - `parse_cbs_results_html(text: str) -> ParsedStandings`
  - `@dataclass(frozen=True) StandingsGame(cbs_event_id: int, sport: Sport, game_date: date, away_abbrev: str, home_abbrev: str, status: str, away_score: int, home_score: int, spread_home: float)`
  - `@dataclass(frozen=True) StandingsEntrant(entry_id: str, name: str, rank: int, points: int, ytd: int, tiebreak: int | None)`
  - `@dataclass(frozen=True) StandingsPick(entry_id: str, cbs_event_id: int, picked_abbrev: str | None, cbs_correct: bool | None)`
  - `@dataclass(frozen=True) ParsedStandings(games: tuple[StandingsGame, ...], entrants: tuple[StandingsEntrant, ...], picks: tuple[StandingsPick, ...])`

- [ ] **Step 1: Generate the fixture from the real page**

Save this as a throwaway script **outside the repo**, e.g. `$SCRATCH/make_fixture.py` (use the session scratchpad; never commit it, because it contains real entry ids):

```python
"""Trim the saved week-2 standings page into a small anonymized fixture."""

import re
import sys

KEEP_EVENTS = ["50029202", "50027615", "50027628", "50029215"]  # NE@SEA, OKLA@MICH, PSU@TEMPLE, NO@DET
KEEP_ENTRIES = {
    "ivxhi4tzhizdiojwgq2tgmjq": ("Entrant A", "fixtureentrya"),
    "ivxhi4tzhizdiojwgq2tgmjv": ("Jota", "ivxhi4tzhizdiojwgq2tgmjv"),
    "ivxhi4tzhizdiojwgq2tgmzz": ("Entrant B", "fixtureentryb"),
    "ivxhi4tzhizdiojwgq2tgojx": ("Entrant C", "fixtureentryc"),
}


def main(src: str, dst: str) -> None:
    text = open(src, encoding="utf-8").read()
    table = re.search(r'<table[^>]*aria-label="Weekly Standings">.*?</table>', text, re.S).group(0)

    def keep_th(match):
        event = re.search(r'event-hdr-cell-(\d+)"', match.group(0))
        return match.group(0) if event is None or event.group(1) in KEEP_EVENTS else ""

    table = re.sub(r"<th\b.*?</th>", keep_th, table, flags=re.S)

    def keep_row(match):
        row = match.group(0)
        entry = re.search(r'pickem-weekly-table-row-([a-z0-9]+)"', row).group(1)
        if entry not in KEEP_ENTRIES:
            return ""
        name, new_id = KEEP_ENTRIES[entry]
        row = row.replace(entry, new_id)
        row = re.sub(r"(noWrap[^>]*>)[^<]+(<)", rf"\g<1>{name} \g<2>", row)

        def keep_td(cell):
            event = re.search(r'pickem-weekly-table-cell-(\d+)"', cell.group(0))
            return cell.group(0) if event is None or event.group(1) in KEEP_EVENTS else ""

        return re.sub(r"<td\b.*?</td>", keep_td, row, flags=re.S)

    table = re.sub(r'<tr[^>]*pickem-weekly-table-row-[a-z0-9]+".*?</tr>', keep_row, table, flags=re.S)
    page = (
        "<!DOCTYPE html><html><head><title>Big Dawg's Pick'em  | Weekly Standings</title>"
        "</head><body>\n" + table + "\n</body></html>\n"
    )
    open(dst, "w", encoding="utf-8").write(page)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

Run: `uv run python $SCRATCH/make_fixture.py data/cbs/results/week2.html tests/fixtures/cbs_results_page.html`
Expected: a file of about 22 KB. Confirm no real names survive:
`grep -o 'noWrap[^>]*>[^<]*<' tests/fixtures/cbs_results_page.html` prints only `Entrant A`, `Jota`, `Entrant B`, `Entrant C`.

The fixture holds exactly these values, taken from the real page and used by the tests below:

| Game (event id) | Sport | Away–Home | Score | Line | Status |
|---|---|---|---|---|---|
| 50029202 | NFL 2026-09-09 | NE @ SEA | 10–13 | −3.5 | FINAL |
| 50027615 | CFB 2026-09-12 | OKLA @ MICH | 10–17 | +5.5 | FINAL |
| 50027628 | CFB 2026-09-12 | PSU @ TEMPLE | 27–9 | +24.5 | FINAL |
| 50029215 | NFL 2026-09-13 | NO @ DET | 30–31 | −7.5 | FINAL/OT |

| Entry id | Name | Rank | Pts | YTD | TB | NE@SEA | OKLA@MICH | PSU@TEMPLE | NO@DET |
|---|---|---|---|---|---|---|---|---|---|
| fixtureentrya | Entrant A | 1 | 20 | 28 | 44 | NE ✓ | MICH ✓ | TEMPLE ✓ | NO ✓ |
| ivxhi4tzhizdiojwgq2tgmjv | Jota | 17 | 16 | 24 | 41 | SEA ✗ | MICH ✓ | PSU ✗ | DET ✗ |
| fixtureentryb | Entrant B | 25 | 16 | 24 | 56 | blank | OKLA ✗ | PSU ✗ | DET ✗ |
| fixtureentryc | Entrant C | 53 | 6 | 13 | 52 | SEA ✗ | blank | blank | DET ✗ |

Points, rank and YTD are CBS's real values for the full 31-game week, so they do **not** equal the fixture's green-mark counts. Tests must not assert that they do.

If the dates in the NFL links differ from the table, trust the file and fix the test values.

- [ ] **Step 2: Write the failing tests**

`tests/test_cbs_results.py`:

```python
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
        StandingsGame(50027615, Sport.CFB, date(2026, 9, 12), "OKLA", "MICH", "FINAL", 10, 17, 5.5),
        StandingsGame(50027628, Sport.CFB, date(2026, 9, 12), "PSU", "TEMPLE", "FINAL", 27, 9, 24.5),
        StandingsGame(50029215, Sport.NFL, date(2026, 9, 13), "NO", "DET", "FINAL/OT", 30, 31, -7.5),
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
    assert picks[("fixtureentryb", 50029202)] == StandingsPick("fixtureentryb", 50029202, None, None)
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
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_cbs_results.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'pickem.ingest.cbs_results'`.

- [ ] **Step 4: Implement the parser**

`src/pickem/ingest/cbs_results.py`:

```python
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
    icon_classes = " ".join(node.attrs.get("class", "") for node in cell.walk() if node.tag == "svg")

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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cbs_results.py -v`
Expected: 10 PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/pickem/ingest/cbs_results.py tests/test_cbs_results.py
git add src/pickem/ingest/cbs_results.py tests/test_cbs_results.py tests/fixtures/cbs_results_page.html
git commit -m "feat: parse a saved CBS Weekly Standings page

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 3: Result records and store tables

**Files:**
- Modify: `src/pickem/models.py` (append after `Edge`)
- Modify: `src/pickem/store/schema.sql` (append)
- Modify: `src/pickem/store/db.py` (import line; new methods after `picks_for_week`)
- Test: `tests/test_results_store.py`

**Interfaces:**
- Consumes: `Store`, `Game`, `LeagueLine`, `MarketLine`, `_game_from_row`, `_league_line_from_row`, `_market_line_from_row` (existing).
- Produces:
  - `models.HISTORY_MONITOR = "monitor"`, `HISTORY_REPORT = "report"`, `HISTORY_LOG_BACKFILL = "log_backfill"`
  - `PoolResult(season, pool_week, entry_id, name, rank, points, ytd, tiebreak: int | None, imported_at: datetime)`
  - `PoolPick(season, pool_week, entry_id, game_id, cbs_event_id: int, side: Side | None, cbs_correct: bool | None)`
  - `RecommendationRecord(game_id, sport: Sport, season, week, side: Side, tier: Tier, edge_points: float, generated_at: datetime, source: str)`
  - `Store.replace_pool_week(season: int, pool_week: int, results: Sequence[PoolResult], picks: Sequence[PoolPick]) -> None`
  - `Store.pool_weeks(season: int) -> list[int]`
  - `Store.pool_results(season: int, pool_week: int) -> list[PoolResult]` (ordered by rank, name)
  - `Store.pool_picks(season: int, pool_week: int) -> list[PoolPick]` (ordered by entry_id, game_id)
  - `Store.append_recommendation_history(records: Sequence[RecommendationRecord]) -> int` (rows actually added)
  - `Store.recommendation_history(game_ids: Sequence[str]) -> list[RecommendationRecord]`
  - `Store.games_by_ids(game_ids: Sequence[str]) -> list[Game]`
  - `Store.league_lines_by_ids(game_ids: Sequence[str]) -> list[LeagueLine]`
  - `Store.market_lines_by_ids(game_ids: Sequence[str]) -> list[MarketLine]`
  - `Store.league_line_count(sport: Sport, season: int, week: int) -> int`
  - `Store.games_for_season(season: int) -> list[Game]`
  - `Store.pick_batches(season: int) -> list[RecommendationRecord]` (every `picks` row as a `report`-source history record)

- [ ] **Step 1: Write the failing tests**

`tests/test_results_store.py`:

```python
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from pickem.models import (
    HISTORY_MONITOR,
    HISTORY_REPORT,
    Edge,
    Game,
    LeagueLine,
    MarketLine,
    PoolPick,
    PoolResult,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.store.db import Store

AT = datetime(2026, 9, 15, 12, tzinfo=UTC)
KICK = datetime(2026, 9, 12, 16, tzinfo=UTC)
GID = "cfb-2026-02-PSU-at-TEM"


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    yield s
    s.close()


def result(entry_id: str, rank: int, points: int, week: int = 2) -> PoolResult:
    return PoolResult(
        season=2026, pool_week=week, entry_id=entry_id, name=entry_id.title(), rank=rank,
        points=points, ytd=points, tiebreak=None, imported_at=AT,
    )


def pick(entry_id: str, side: Side | None, week: int = 2, game_id: str = GID) -> PoolPick:
    return PoolPick(
        season=2026, pool_week=week, entry_id=entry_id, game_id=game_id, cbs_event_id=1,
        side=side, cbs_correct=None if side is None else side is Side.HOME,
    )


def history(at: datetime, source: str = HISTORY_MONITOR, side: Side = Side.HOME):
    return RecommendationRecord(
        game_id=GID, sport=Sport.CFB, season=2026, week=2, side=side, tier=Tier.LEAN,
        edge_points=1.0, generated_at=at, source=source,
    )


def seed_game(store: Store) -> None:
    store.upsert_games([
        Game(game_id=GID, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
             home_team_id="TEM", away_team_id="PSU")
    ])
    store.upsert_league_lines([
        LeagueLine(game_id=GID, season=2026, week=2, spread_home=24.5, posted_at=KICK)
    ])


def test_pool_week_round_trips(store):
    store.replace_pool_week(
        2026, 2, [result("b", 2, 10), result("a", 1, 12)], [pick("a", Side.HOME), pick("b", None)]
    )
    assert store.pool_weeks(2026) == [2]
    assert [r.entry_id for r in store.pool_results(2026, 2)] == ["a", "b"]
    picks = store.pool_picks(2026, 2)
    assert [(p.entry_id, p.side, p.cbs_correct) for p in picks] == [
        ("a", Side.HOME, True),
        ("b", None, None),
    ]


def test_replacing_a_pool_week_leaves_other_weeks_alone(store):
    store.replace_pool_week(2026, 1, [result("a", 1, 9, week=1)], [pick("a", Side.AWAY, week=1)])
    store.replace_pool_week(2026, 2, [result("a", 1, 12)], [pick("a", Side.HOME)])
    store.replace_pool_week(2026, 2, [result("z", 1, 5)], [])
    assert store.pool_weeks(2026) == [1, 2]
    assert [r.entry_id for r in store.pool_results(2026, 2)] == ["z"]
    assert store.pool_picks(2026, 2) == []
    assert len(store.pool_picks(2026, 1)) == 1


def test_failed_replace_keeps_the_previous_import(store):
    store.replace_pool_week(2026, 2, [result("a", 1, 12)], [pick("a", Side.HOME)])
    with pytest.raises(duckdb.ConstraintException):
        store.replace_pool_week(2026, 2, [result("b", 1, 3)], [pick("b", Side.HOME)] * 2)
    assert [r.entry_id for r in store.pool_results(2026, 2)] == ["a"]


def test_history_is_append_only_and_ignores_duplicates(store):
    rows = [history(KICK - timedelta(hours=2)), history(KICK - timedelta(hours=1))]
    assert store.append_recommendation_history(rows) == 2
    assert store.append_recommendation_history(rows) == 0
    assert store.append_recommendation_history([history(KICK - timedelta(hours=1), HISTORY_REPORT)]) == 1
    got = store.recommendation_history([GID])
    assert [(r.generated_at, r.source) for r in got] == [
        (KICK - timedelta(hours=2), HISTORY_MONITOR),
        (KICK - timedelta(hours=1), HISTORY_MONITOR),
        (KICK - timedelta(hours=1), HISTORY_REPORT),
    ]
    assert store.recommendation_history([]) == []


def test_by_id_loaders_and_counts(store):
    seed_game(store)
    store.append_market_lines([
        MarketLine(game_id=GID, source="oddsapi", book="fd", spread_home=25.0, captured_at=KICK)
    ])
    assert [g.game_id for g in store.games_by_ids([GID, "missing"])] == [GID]
    assert [line.spread_home for line in store.league_lines_by_ids([GID])] == [24.5]
    assert [line.book for line in store.market_lines_by_ids([GID])] == ["fd"]
    assert store.league_line_count(Sport.CFB, 2026, 2) == 1
    assert store.league_line_count(Sport.NFL, 2026, 1) == 0
    assert [g.game_id for g in store.games_for_season(2026)] == [GID]
    assert store.games_by_ids([]) == []


def test_pick_batches_become_report_history(store):
    seed_game(store)
    edge = Edge(game_id=GID, side=Side.AWAY, delta=0.5, tier=Tier.COINFLIP,
                league_spread=24.5, market_spread=24.0, rationale="test")
    store.record_picks([edge], 2026, 2, KICK - timedelta(days=4))
    assert store.pick_batches(2026) == [
        RecommendationRecord(
            game_id=GID, sport=Sport.CFB, season=2026, week=2, side=Side.AWAY,
            tier=Tier.COINFLIP, edge_points=0.5, generated_at=KICK - timedelta(days=4),
            source=HISTORY_REPORT,
        )
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results_store.py -v`
Expected: collection error, `ImportError: cannot import name 'HISTORY_MONITOR'`.

- [ ] **Step 3: Add the records to `src/pickem/models.py`**

Append at the end of the file:

```python
# Where a recommendation-history row came from.
HISTORY_MONITOR = "monitor"
HISTORY_REPORT = "report"
HISTORY_LOG_BACKFILL = "log_backfill"


class PoolResult(BaseModel):
    """One entrant's line on a CBS Weekly Standings page."""

    season: int
    pool_week: int
    entry_id: str
    name: str
    rank: int
    points: int
    ytd: int
    tiebreak: int | None
    imported_at: datetime


class PoolPick(BaseModel):
    """One entrant's pick on one game. `side` is None when it was left blank."""

    season: int
    pool_week: int
    entry_id: str
    game_id: str
    cbs_event_id: int
    side: Side | None
    cbs_correct: bool | None


class RecommendationRecord(BaseModel):
    """What the model said about a game, and when. Not a submitted pick."""

    game_id: str
    sport: Sport
    season: int
    week: int
    side: Side
    tier: Tier
    edge_points: float
    generated_at: datetime
    source: str
```

- [ ] **Step 4: Add the tables to `src/pickem/store/schema.sql`**

Append:

```sql
-- One row per entrant per pool week, from a saved CBS Weekly Standings page.
-- A re-import replaces the whole pool week.
CREATE TABLE IF NOT EXISTS pool_results (
    season      INTEGER NOT NULL,
    pool_week   INTEGER NOT NULL,
    entry_id    VARCHAR NOT NULL,
    name        VARCHAR NOT NULL,
    rank        INTEGER NOT NULL,
    points      INTEGER NOT NULL,
    ytd         INTEGER NOT NULL,
    tiebreak    INTEGER,
    imported_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, pool_week, entry_id)
);

CREATE TABLE IF NOT EXISTS pool_picks (
    season       INTEGER NOT NULL,
    pool_week    INTEGER NOT NULL,
    entry_id     VARCHAR NOT NULL,
    game_id      VARCHAR NOT NULL,
    cbs_event_id BIGINT  NOT NULL,
    side         VARCHAR,
    cbs_correct  BOOLEAN,
    PRIMARY KEY (season, pool_week, entry_id, game_id)
);

-- APPEND-ONLY. What the model recommended, and when. Distinct from `picks`,
-- which only `report` writes; see operations/recommendation_history.py.
CREATE TABLE IF NOT EXISTS recommendation_history (
    game_id      VARCHAR NOT NULL,
    sport        VARCHAR NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    side         VARCHAR NOT NULL,
    tier         VARCHAR NOT NULL,
    edge_points  DOUBLE  NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    source       VARCHAR NOT NULL,
    PRIMARY KEY (game_id, generated_at, source)
);
```

- [ ] **Step 5: Add the store methods to `src/pickem/store/db.py`**

Change the models import to:

```python
from pickem.models import (
    HISTORY_REPORT,
    Edge,
    Game,
    LeagueLine,
    MarketLine,
    PoolPick,
    PoolResult,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
```

Add a module-level helper next to `_market_line_from_row`:

```python
def _history_from_row(row: tuple) -> RecommendationRecord:
    return RecommendationRecord(
        game_id=row[0],
        sport=Sport(row[1]),
        season=row[2],
        week=row[3],
        side=Side(row[4]),
        tier=Tier(row[5]),
        edge_points=row[6],
        generated_at=row[7],
        source=row[8],
    )
```

Add these methods to `Store`, directly after `picks_for_week`:

```python
    def replace_pool_week(
        self,
        season: int,
        pool_week: int,
        results: Sequence[PoolResult],
        picks: Sequence[PoolPick],
    ) -> None:
        """Replace one pool week's standings and picks in one transaction.

        A re-import must never leave a week half old and half new.
        """
        self._con.execute("BEGIN TRANSACTION")
        try:
            for table in ("pool_picks", "pool_results"):
                self._con.execute(
                    f"DELETE FROM {table} WHERE season = ? AND pool_week = ?", [season, pool_week]
                )
            self._executemany(
                "INSERT INTO pool_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (r.season, r.pool_week, r.entry_id, r.name, r.rank, r.points, r.ytd,
                     r.tiebreak, r.imported_at)
                    for r in results
                ],
            )
            self._executemany(
                "INSERT INTO pool_picks VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (p.season, p.pool_week, p.entry_id, p.game_id, p.cbs_event_id,
                     p.side.value if p.side is not None else None, p.cbs_correct)
                    for p in picks
                ],
            )
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")

    def pool_weeks(self, season: int) -> list[int]:
        rows = self._con.execute(
            "SELECT DISTINCT pool_week FROM pool_results WHERE season = ? ORDER BY pool_week",
            [season],
        ).fetchall()
        return [row[0] for row in rows]

    def pool_results(self, season: int, pool_week: int) -> list[PoolResult]:
        rows = self._con.execute(
            """
            SELECT season, pool_week, entry_id, name, rank, points, ytd, tiebreak, imported_at
            FROM pool_results WHERE season = ? AND pool_week = ? ORDER BY rank, name
            """,
            [season, pool_week],
        ).fetchall()
        return [
            PoolResult(
                season=r[0], pool_week=r[1], entry_id=r[2], name=r[3], rank=r[4], points=r[5],
                ytd=r[6], tiebreak=r[7], imported_at=r[8],
            )
            for r in rows
        ]

    def pool_picks(self, season: int, pool_week: int) -> list[PoolPick]:
        rows = self._con.execute(
            """
            SELECT season, pool_week, entry_id, game_id, cbs_event_id, side, cbs_correct
            FROM pool_picks WHERE season = ? AND pool_week = ? ORDER BY entry_id, game_id
            """,
            [season, pool_week],
        ).fetchall()
        return [
            PoolPick(
                season=r[0], pool_week=r[1], entry_id=r[2], game_id=r[3], cbs_event_id=r[4],
                side=Side(r[5]) if r[5] is not None else None, cbs_correct=r[6],
            )
            for r in rows
        ]

    def _row_count(self, table: str) -> int:
        return self._con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def append_recommendation_history(self, records: Sequence[RecommendationRecord]) -> int:
        """Append model decisions and return how many were new.

        An identical (game, time, source) row is ignored, so replaying a
        backfill or a refresh is safe.
        """
        before = self._row_count("recommendation_history")
        self._executemany(
            "INSERT OR IGNORE INTO recommendation_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (r.game_id, r.sport.value, r.season, r.week, r.side.value, r.tier.value,
                 r.edge_points, r.generated_at, r.source)
                for r in records
            ],
        )
        return self._row_count("recommendation_history") - before

    def recommendation_history(self, game_ids: Sequence[str]) -> list[RecommendationRecord]:
        if not game_ids:
            return []
        rows = self._con.execute(
            """
            SELECT game_id, sport, season, week, side, tier, edge_points, generated_at, source
            FROM recommendation_history WHERE list_contains(?, game_id)
            ORDER BY game_id, generated_at, source
            """,
            [list(game_ids)],
        ).fetchall()
        return [_history_from_row(row) for row in rows]

    def games_by_ids(self, game_ids: Sequence[str]) -> list[Game]:
        if not game_ids:
            return []
        rows = self._con.execute(
            """
            SELECT game_id, sport, season, week, kickoff_utc, home_team_id, away_team_id,
                   home_score, away_score
            FROM games WHERE list_contains(?, game_id) ORDER BY kickoff_utc, game_id
            """,
            [list(game_ids)],
        ).fetchall()
        return [_game_from_row(row) for row in rows]

    def league_lines_by_ids(self, game_ids: Sequence[str]) -> list[LeagueLine]:
        if not game_ids:
            return []
        rows = self._con.execute(
            """
            SELECT game_id, season, week, spread_home, posted_at
            FROM league_lines WHERE list_contains(?, game_id) ORDER BY game_id
            """,
            [list(game_ids)],
        ).fetchall()
        return [_league_line_from_row(row) for row in rows]

    def market_lines_by_ids(self, game_ids: Sequence[str]) -> list[MarketLine]:
        if not game_ids:
            return []
        rows = self._con.execute(
            """
            SELECT game_id, source, book, spread_home, total, captured_at
            FROM lines WHERE list_contains(?, game_id) ORDER BY game_id, captured_at, book
            """,
            [list(game_ids)],
        ).fetchall()
        return [_market_line_from_row(row) for row in rows]

    def league_line_count(self, sport: Sport, season: int, week: int) -> int:
        return self._con.execute(
            """
            SELECT count(*) FROM league_lines l JOIN games g USING (game_id)
            WHERE g.sport = ? AND l.season = ? AND l.week = ?
            """,
            [sport.value, season, week],
        ).fetchone()[0]

    def games_for_season(self, season: int) -> list[Game]:
        rows = self._con.execute(
            """
            SELECT game_id, sport, season, week, kickoff_utc, home_team_id, away_team_id,
                   home_score, away_score
            FROM games WHERE season = ? ORDER BY kickoff_utc, game_id
            """,
            [season],
        ).fetchall()
        return [_game_from_row(row) for row in rows]

    def pick_batches(self, season: int) -> list[RecommendationRecord]:
        """Every recorded `report` batch for a season, as history rows."""
        rows = self._con.execute(
            """
            SELECT p.game_id, g.sport, p.season, p.week, p.side, p.tier, p.edge_points,
                   p.generated_at, ?
            FROM picks p JOIN games g USING (game_id)
            WHERE p.season = ? ORDER BY p.generated_at, p.game_id
            """,
            [HISTORY_REPORT, season],
        ).fetchall()
        return [_history_from_row(row) for row in rows]
```

If ruff flags the SQL f-strings (it should not: `S608` is not in the selected rules), leave them; both table names are module constants.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results_store.py tests/test_store.py -v`
Expected: all PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/pickem/models.py src/pickem/store/db.py tests/test_results_store.py
git add src/pickem/models.py src/pickem/store/schema.sql src/pickem/store/db.py tests/test_results_store.py
git commit -m "feat: store pool standings, pool picks and recommendation history

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 4: Link, validate and store an imported page

**Files:**
- Create: `src/pickem/operations/results_import.py`
- Create: `tests/results_helpers.py`
- Test: `tests/test_results_import.py`

**Interfaces:**
- Consumes: `parse_cbs_results_html`, `ParsedStandings`, `StandingsGame` (Task 2); `PoolResult`, `PoolPick` and the Task 3 store methods; `grade_pick`, `Result` (`pickem.backtest.stats`); `make_game_id`; `TeamResolver`, `UnknownTeamError`.
- Produces:
  - `class ResultsImportError(RuntimeError)`
  - `league_week(sport: Sport, pool_week: int) -> int`
  - `@dataclass(frozen=True) ImportSummary(season: int, pool_week: int, entrants: int, picks: int, blank_picks: int, games_by_sport: dict[Sport, int], scores_filled: int)`
  - `import_results(store: Store, parsed: ParsedStandings, *, season: int, pool_week: int, resolver: TeamResolver, imported_at: datetime) -> ImportSummary`
  - `tests/results_helpers.py`: `FIXTURE`, `JOTA_ID`, `KICK`, `SEEDED`, `seed(store, rows=SEEDED, *, scores=True)`, `parsed_fixture()`, `imported_store()`

- [ ] **Step 1: Write the shared test helper**

`tests/results_helpers.py`:

```python
"""Seed a store that matches tests/fixtures/cbs_results_page.html (pool week 2)."""

from datetime import UTC, datetime, timedelta
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
```

- [ ] **Step 2: Write the failing tests**

`tests/test_results_import.py`:

```python
from dataclasses import replace

import pytest
from results_helpers import IMPORTED_AT, JOTA_ID, SEEDED, parsed_fixture, seed

from pickem.models import Side, Sport
from pickem.operations.results_import import (
    ResultsImportError,
    import_results,
    league_week,
)
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    yield s
    s.close()


def run_import(store, parsed=None):
    return import_results(
        store, parsed or parsed_fixture(), season=2026, pool_week=2,
        resolver=TeamResolver.default(), imported_at=IMPORTED_AT,
    )


def test_pool_week_translates_to_each_boards_league_week():
    assert league_week(Sport.CFB, 2) == 2
    assert league_week(Sport.NFL, 2) == 1
    with pytest.raises(ResultsImportError, match="no NFL board"):
        league_week(Sport.NFL, 1)


def test_imports_every_entrant_and_pick(store):
    seed(store)
    summary = run_import(store)
    assert (summary.entrants, summary.picks, summary.blank_picks) == (4, 16, 3)
    assert summary.games_by_sport == {Sport.CFB: 2, Sport.NFL: 2}
    assert summary.scores_filled == 0

    jota = next(r for r in store.pool_results(2026, 2) if r.entry_id == JOTA_ID)
    assert (jota.name, jota.rank, jota.points, jota.tiebreak) == ("Jota", 17, 16, 41)

    picks = {(p.entry_id, p.game_id): p for p in store.pool_picks(2026, 2)}
    assert picks[(JOTA_ID, "nfl-2026-01-NE-at-SEA")].side is Side.HOME
    assert picks[(JOTA_ID, "nfl-2026-01-NE-at-SEA")].cbs_correct is False
    assert picks[(JOTA_ID, "cfb-2026-02-PSU-at-TEM")].side is Side.AWAY
    assert picks[("fixtureentryb", "nfl-2026-01-NE-at-SEA")].side is None


def test_fills_missing_scores_from_cbs(store):
    seed(store, scores=False)
    summary = run_import(store)
    assert summary.scores_filled == 4
    game = store.games_by_ids(["cfb-2026-02-OU-at-MICH"])[0]
    assert (game.away_score, game.home_score) == (10, 17)


def test_unresolved_team_aborts_without_writing(store):
    seed(store)
    parsed = parsed_fixture()
    parsed = replace(parsed, games=(replace(parsed.games[0], away_abbrev="ZZZZ"),) + parsed.games[1:])
    with pytest.raises(ResultsImportError, match="ZZZZ"):
        run_import(store, parsed)
    assert store.pool_weeks(2026) == []


def test_missing_stored_game_aborts(store):
    seed(store, [row for row in SEEDED if row[3] != "TEM"])
    with pytest.raises(ResultsImportError, match="no stored game cfb-2026-02-PSU-at-TEM"):
        run_import(store)
    assert store.pool_weeks(2026) == []


def test_extra_stored_league_line_aborts(store):
    seed(store, [*SEEDED, (Sport.CFB, 2, "ARK", "UTAH", -11.5, 10, 43)])
    with pytest.raises(ResultsImportError, match="the page has 2 games but the store has 3"):
        run_import(store)


def test_missing_board_aborts(store):
    seed(store)
    parsed = parsed_fixture()
    cfb_only = replace(
        parsed,
        games=tuple(g for g in parsed.games if g.sport is Sport.CFB),
        picks=tuple(p for p in parsed.picks if p.cbs_event_id in {50027615, 50027628}),
    )
    with pytest.raises(ResultsImportError, match="nfl week 1: the page has 0 games"):
        run_import(store, cfb_only)


def test_score_disagreement_aborts(store):
    seed(store, [row if row[3] != "MICH" else (*row[:6], 20) for row in SEEDED])
    with pytest.raises(ResultsImportError, match="CBS final 10-17 but the store has 10-20"):
        run_import(store)


def test_line_disagreement_aborts(store):
    seed(store, [row if row[3] != "TEM" else (*row[:4], 23.5, *row[5:]) for row in SEEDED])
    with pytest.raises(ResultsImportError, match=r"CBS line \+24.5 but the stored league line is \+23.5"):
        run_import(store)


def test_grade_that_disagrees_with_the_score_aborts(store):
    seed(store)
    parsed = parsed_fixture()
    picks = tuple(
        replace(p, cbs_correct=False) if (p.entry_id, p.cbs_event_id) == (JOTA_ID, 50027615) else p
        for p in parsed.picks
    )
    with pytest.raises(ResultsImportError, match="Jota on OKLA at MICH"):
        run_import(store, replace(parsed, picks=picks))
    assert store.pool_weeks(2026) == []


def test_reimport_replaces_the_week(store):
    seed(store)
    run_import(store)
    parsed = parsed_fixture()
    fewer = replace(
        parsed,
        entrants=tuple(e for e in parsed.entrants if e.entry_id != "fixtureentryc"),
        picks=tuple(p for p in parsed.picks if p.entry_id != "fixtureentryc"),
    )
    run_import(store, fewer)
    assert len(store.pool_results(2026, 2)) == 3
    assert len(store.pool_picks(2026, 2)) == 12
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_results_import.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'pickem.operations.results_import'`.

- [ ] **Step 4: Implement the importer**

`src/pickem/operations/results_import.py`:

```python
"""Link a parsed CBS Weekly Standings page to stored games, then store it.

Fails closed: every problem is collected and reported together, and nothing is
written unless the whole page links and cross-checks. A misread page would
silently corrupt every conclusion drawn from it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from pickem.backtest.stats import Result, grade_pick
from pickem.ingest.cbs_results import ParsedStandings, StandingsGame
from pickem.models import Game, PoolPick, PoolResult, Side, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver, UnknownTeamError
from pickem.store.db import Store


class ResultsImportError(RuntimeError):
    """The page could not be linked to stored games, or failed a cross-check."""


@dataclass(frozen=True)
class ImportSummary:
    season: int
    pool_week: int
    entrants: int
    picks: int
    blank_picks: int
    games_by_sport: dict[Sport, int]
    scores_filled: int


def league_week(sport: Sport, pool_week: int) -> int:
    """Translate a pool week into one board's league week.

    The pool pairs CFB week N with NFL week N-1, because the college season
    starts a week earlier. Pool week 1 is CFB only.
    """
    if pool_week < 1:
        raise ResultsImportError(f"pool week must be positive, got {pool_week}")
    if sport is Sport.CFB:
        return pool_week
    if pool_week == 1:
        raise ResultsImportError("pool week 1 has no NFL board")
    return pool_week - 1


def import_results(
    store: Store,
    parsed: ParsedStandings,
    *,
    season: int,
    pool_week: int,
    resolver: TeamResolver,
    imported_at: datetime,
) -> ImportSummary:
    """Validate the whole page against the store, then replace its pool week."""
    errors: list[str] = []
    linked = _link_games(store, parsed.games, season=season, pool_week=pool_week,
                         resolver=resolver, errors=errors)
    if not errors:
        _check_boards(store, parsed.games, season=season, pool_week=pool_week, errors=errors)
        _check_grades(parsed, errors)
    if errors:
        logger.bind(
            event="results_import_rejected", season=season, pool_week=pool_week,
            problems=len(errors), reasons=errors,
        ).error(f"results import rejected for pool week {pool_week}: {len(errors)} problem(s)")
        raise ResultsImportError(
            f"pool week {pool_week} was not imported ({len(errors)} problem(s)):\n"
            + "\n".join(f"  - {error}" for error in errors)
        )

    page_games = {game.cbs_event_id: game for game in parsed.games}
    unscored = [
        game.model_copy(update={
            "away_score": page_games[event_id].away_score,
            "home_score": page_games[event_id].home_score,
        })
        for event_id, game in linked.items()
        if game.home_score is None or game.away_score is None
    ]
    store.upsert_games(unscored)

    results = [
        PoolResult(
            season=season, pool_week=pool_week, entry_id=e.entry_id, name=e.name, rank=e.rank,
            points=e.points, ytd=e.ytd, tiebreak=e.tiebreak, imported_at=imported_at,
        )
        for e in parsed.entrants
    ]
    picks = [
        PoolPick(
            season=season, pool_week=pool_week, entry_id=p.entry_id,
            game_id=linked[p.cbs_event_id].game_id, cbs_event_id=p.cbs_event_id,
            side=_side(page_games[p.cbs_event_id], p.picked_abbrev), cbs_correct=p.cbs_correct,
        )
        for p in parsed.picks
    ]
    store.replace_pool_week(season, pool_week, results, picks)

    summary = ImportSummary(
        season=season,
        pool_week=pool_week,
        entrants=len(results),
        picks=len(picks),
        blank_picks=sum(1 for p in picks if p.side is None),
        games_by_sport=dict(Counter(game.sport for game in parsed.games)),
        scores_filled=len(unscored),
    )
    logger.bind(
        event="results_imported", season=season, pool_week=pool_week,
        entrants=summary.entrants, picks=summary.picks, blank_picks=summary.blank_picks,
        games={sport.value: count for sport, count in summary.games_by_sport.items()},
        scores_filled=summary.scores_filled,
    ).info(
        f"imported pool week {pool_week}: {summary.entrants} entrants, {summary.picks} picks, "
        f"{summary.scores_filled} missing score(s) filled from CBS"
    )
    return summary


def _side(game: StandingsGame, abbrev: str | None) -> Side | None:
    if abbrev is None:
        return None
    return Side.HOME if abbrev == game.home_abbrev else Side.AWAY


def _label(game: StandingsGame) -> str:
    return f"{game.away_abbrev} at {game.home_abbrev}"


def _link_games(
    store: Store,
    games: tuple[StandingsGame, ...],
    *,
    season: int,
    pool_week: int,
    resolver: TeamResolver,
    errors: list[str],
) -> dict[int, Game]:
    wanted: dict[int, str] = {}
    for game in games:
        try:
            week = league_week(game.sport, pool_week)
            away = resolver.resolve(game.away_abbrev, game.sport)
            home = resolver.resolve(game.home_abbrev, game.sport)
        except UnknownTeamError as exc:
            errors.append(
                f"{_label(game)}: {exc} — add the CBS abbreviation to resolve/aliases.yaml"
            )
            continue
        except ResultsImportError as exc:
            errors.append(f"{_label(game)}: {exc}")
            continue
        wanted[game.cbs_event_id] = make_game_id(game.sport, season, week, away, home)

    ids = list(wanted.values())
    stored = {game.game_id: game for game in store.games_by_ids(ids)}
    lines = {line.game_id: line for line in store.league_lines_by_ids(ids)}
    page = {game.cbs_event_id: game for game in games}

    linked: dict[int, Game] = {}
    for event_id, game_id in wanted.items():
        cbs = page[event_id]
        game = stored.get(game_id)
        if game is None:
            errors.append(f"{_label(cbs)}: no stored game {game_id} — run ingest-cbs for that board")
            continue
        if (
            game.home_score is not None
            and game.away_score is not None
            and (game.away_score, game.home_score) != (cbs.away_score, cbs.home_score)
        ):
            errors.append(
                f"{_label(cbs)}: CBS final {cbs.away_score}-{cbs.home_score} but the store has "
                f"{game.away_score}-{game.home_score}"
            )
        line = lines.get(game_id)
        if line is None:
            errors.append(f"{_label(cbs)}: stored game {game_id} has no league line")
        elif line.spread_home != cbs.spread_home:
            errors.append(
                f"{_label(cbs)}: CBS line {cbs.spread_home:+.1f} but the stored league line is "
                f"{line.spread_home:+.1f}"
            )
        linked[event_id] = game
    return linked


def _check_boards(
    store: Store,
    games: tuple[StandingsGame, ...],
    *,
    season: int,
    pool_week: int,
    errors: list[str],
) -> None:
    on_page = Counter(game.sport for game in games)
    boards = {Sport.CFB} | ({Sport.NFL} if pool_week > 1 else set()) | set(on_page)
    for sport in sorted(boards):
        week = league_week(sport, pool_week)
        stored = store.league_line_count(sport, season, week)
        if stored != on_page[sport]:
            errors.append(
                f"{sport.value} week {week}: the page has {on_page[sport]} games but the store "
                f"has {stored} league lines"
            )


def _check_grades(parsed: ParsedStandings, errors: list[str]) -> None:
    games = {game.cbs_event_id: game for game in parsed.games}
    names = {entrant.entry_id: entrant.name for entrant in parsed.entrants}
    for pick in parsed.picks:
        if pick.picked_abbrev is None:
            continue
        game = games[pick.cbs_event_id]
        result = grade_pick(
            _side(game, pick.picked_abbrev), game.home_score - game.away_score, game.spread_home
        )
        # No push markup has been seen, so a push cannot be cross-checked; the
        # parser already rejects a pick whose icon is neither correct nor incorrect.
        if result is Result.PUSH:
            continue
        if (result is Result.WIN) != pick.cbs_correct:
            marked = "correct" if pick.cbs_correct else "incorrect"
            errors.append(
                f"{names[pick.entry_id]} on {_label(game)}: CBS marks {pick.picked_abbrev} "
                f"{marked} but the score and line say {result.value}"
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results_import.py -v`
Expected: 11 PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/pickem/operations/results_import.py tests/results_helpers.py tests/test_results_import.py
git add src/pickem/operations/results_import.py tests/results_helpers.py tests/test_results_import.py
git commit -m "feat: link an imported standings page to stored games and fail closed

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 5: Recommendation history and its backfill

**Files:**
- Create: `src/pickem/operations/recommendation_history.py`
- Test: `tests/test_recommendation_history.py`

**Interfaces:**
- Consumes: `RecommendationSnapshot` (`pickem.operations.recommendations`); `RecommendationRecord`, `HISTORY_LOG_BACKFILL`; `Store.append_recommendation_history`, `Store.pick_batches`, `Store.games_for_season` (Task 3).
- Produces:
  - `history_from_snapshot(snapshot: RecommendationSnapshot, source: str) -> list[RecommendationRecord]`
  - `record_snapshot_history(db: Path, snapshot: RecommendationSnapshot, source: str) -> int`
  - `iter_log_records(log_dir: Path) -> Iterator[dict]`
  - `@dataclass(frozen=True) BackfillSummary(from_picks: int, from_logs: int, log_decisions_seen: int, log_decisions_unmatched: int)`
  - `backfill_history(store: Store, *, season: int, log_dir: Path) -> BackfillSummary`

- [ ] **Step 1: Write the failing tests**

`tests/test_recommendation_history.py`:

```python
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from pickem.models import (
    HISTORY_LOG_BACKFILL,
    HISTORY_MONITOR,
    Edge,
    Game,
    Side,
    Sport,
    Tier,
)
from pickem.operations.recommendation_history import (
    backfill_history,
    history_from_snapshot,
    iter_log_records,
    record_snapshot_history,
)
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import Store

KICK = datetime(2026, 9, 12, 16, tzinfo=UTC)
GID = "cfb-2026-02-PSU-at-TEM"


def edge(side: Side = Side.AWAY, tier: Tier = Tier.COINFLIP) -> Edge:
    return Edge(game_id=GID, side=side, delta=0.5, tier=tier, league_spread=24.5,
                market_spread=24.0, rationale="test")


def snapshot(at: datetime) -> RecommendationSnapshot:
    return RecommendationSnapshot(sport=Sport.CFB, season=2026, week=2, generated_at=at,
                                  edges=(edge(),))


def decision(ts: str, game_id: str = GID, side: str = "away", tier: str = "coinflip") -> str:
    return json.dumps({
        "ts": ts, "level": "INFO", "event": "edge_decided", "message": "PSU at TEM: pick PSU",
        "game_id": game_id, "side": side, "tier": tier, "delta": 0.5,
    })


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    s.upsert_games([
        Game(game_id=GID, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
             home_team_id="TEM", away_team_id="PSU")
    ])
    yield s
    s.close()


def test_history_from_snapshot_keeps_side_tier_edge_and_time():
    rows = history_from_snapshot(snapshot(KICK - timedelta(hours=1)), HISTORY_MONITOR)
    assert [(r.game_id, r.sport, r.week, r.side, r.tier, r.edge_points, r.source) for r in rows] == [
        (GID, Sport.CFB, 2, Side.AWAY, Tier.COINFLIP, 0.5, HISTORY_MONITOR)
    ]
    assert rows[0].generated_at == KICK - timedelta(hours=1)


def test_record_snapshot_history_is_idempotent(tmp_path):
    db = tmp_path / "pickem.duckdb"
    assert record_snapshot_history(db, snapshot(KICK - timedelta(hours=1)), HISTORY_MONITOR) == 1
    assert record_snapshot_history(db, snapshot(KICK - timedelta(hours=1)), HISTORY_MONITOR) == 0


def test_iter_log_records_reads_current_and_rotated_files(tmp_path):
    (tmp_path / "pickem.jsonl").write_text(decision("2026-09-11T10:00:00-04:00") + "\nnot json\n")
    with zipfile.ZipFile(tmp_path / "pickem.2026-09-10_00-00-00_1.jsonl.zip", "w") as archive:
        archive.writestr("pickem.2026-09-10_00-00-00_1.jsonl", decision("2026-09-10T10:00:00-04:00"))
    (tmp_path / "pickem.log").write_text(decision("2026-09-09T10:00:00-04:00"))
    assert [r["ts"] for r in iter_log_records(tmp_path)] == [
        "2026-09-10T10:00:00-04:00",
        "2026-09-11T10:00:00-04:00",
    ]


def test_backfill_reads_pick_batches_and_logged_decisions(store, tmp_path):
    store.record_picks([edge(Side.HOME, Tier.LEAN)], 2026, 2, KICK - timedelta(days=4))
    (tmp_path / "pickem.jsonl").write_text("\n".join([
        decision("2026-09-11T10:00:00-04:00"),
        decision("2026-09-11T11:00:00-04:00", game_id="cfb-2026-02-XXX-at-YYY"),
        json.dumps({"ts": "2026-09-11T10:00:00-04:00", "event": "refresh_started"}),
        decision("2026-09-11T12:00:00-04:00", game_id="nfl-2025-01-BUF-at-MIA"),
    ]))
    with zipfile.ZipFile(tmp_path / "pickem.2026-09-10_00-00-00_1.jsonl.zip", "w") as archive:
        archive.writestr("x.jsonl", decision("2026-09-10T10:00:00-04:00", side="home"))

    summary = backfill_history(store, season=2026, log_dir=tmp_path)

    assert (summary.from_picks, summary.from_logs) == (1, 2)
    assert (summary.log_decisions_seen, summary.log_decisions_unmatched) == (3, 1)
    logged = [r for r in store.recommendation_history([GID]) if r.source == HISTORY_LOG_BACKFILL]
    assert [(r.generated_at, r.side) for r in logged] == [
        (datetime(2026, 9, 10, 14, tzinfo=UTC), Side.HOME),
        (datetime(2026, 9, 11, 14, tzinfo=UTC), Side.AWAY),
    ]

    again = backfill_history(store, season=2026, log_dir=tmp_path)
    assert (again.from_picks, again.from_logs) == (0, 0)


def test_backfill_tolerates_a_missing_log_directory(store, tmp_path):
    summary = backfill_history(store, season=2026, log_dir=tmp_path / "absent")
    assert (summary.from_logs, summary.log_decisions_seen) == (0, 0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_recommendation_history.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'pickem.operations.recommendation_history'`.

- [ ] **Step 3: Implement the module**

`src/pickem/operations/recommendation_history.py`:

```python
"""What the model recommended for each game, and when.

This is not the `picks` table. `picks` records a rendered `report` batch, and
`report` stays the only command that writes it; what was actually submitted now
comes from the CBS standings page. History exists so a result can be graded
against the last recommendation before kickoff, which is what the owner acted on.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from pickem.models import (
    HISTORY_LOG_BACKFILL,
    Game,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import Store

_EDGE_EVENT = "edge_decided"


@dataclass(frozen=True)
class BackfillSummary:
    from_picks: int
    from_logs: int
    log_decisions_seen: int
    log_decisions_unmatched: int


def history_from_snapshot(
    snapshot: RecommendationSnapshot, source: str
) -> list[RecommendationRecord]:
    sport = Sport(snapshot.sport)
    return [
        RecommendationRecord(
            game_id=edge.game_id,
            sport=sport,
            season=snapshot.season,
            week=snapshot.week,
            side=edge.side,
            tier=edge.tier,
            edge_points=edge.delta,
            generated_at=snapshot.generated_at,
            source=source,
        )
        for edge in snapshot.edges
    ]


def record_snapshot_history(db: Path, snapshot: RecommendationSnapshot, source: str) -> int:
    """Append one snapshot's recommendations to the store at ``db``."""
    db.parent.mkdir(parents=True, exist_ok=True)
    with Store(db) as store:
        store.init_schema()
        added = store.append_recommendation_history(history_from_snapshot(snapshot, source))
    logger.bind(
        event="recommendation_history_recorded", source=source,
        sport=Sport(snapshot.sport).value, season=snapshot.season, week=snapshot.week,
        rows=added,
    ).debug(f"recorded {added} recommendation history row(s) from {source}")
    return added


def iter_log_records(log_dir: Path) -> Iterator[dict]:
    """Yield JSON records from current and rotated JSONL logs, oldest file first.

    Rotated files are zipped by the logger. Lines that are not JSON objects are
    skipped: a record cut off mid-write must not stop a backfill.
    """
    for path in sorted(log_dir.glob("pickem*.jsonl*")):
        if path.name.endswith(".jsonl.zip"):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    with archive.open(member) as handle:
                        yield from _json_objects(
                            line.decode("utf-8", errors="replace") for line in handle
                        )
        elif path.suffix == ".jsonl":
            with path.open(encoding="utf-8", errors="replace") as handle:
                yield from _json_objects(handle)


def _json_objects(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield record


def _history_from_log(record: dict, games: dict[str, Game]) -> RecommendationRecord | None:
    game = games.get(str(record.get("game_id")))
    if game is None:
        return None
    try:
        return RecommendationRecord(
            game_id=game.game_id,
            sport=game.sport,
            season=game.season,
            week=game.week,
            side=Side(record["side"]),
            tier=Tier(record["tier"]),
            edge_points=float(record["delta"]),
            generated_at=datetime.fromisoformat(record["ts"]),
            source=HISTORY_LOG_BACKFILL,
        )
    except (KeyError, TypeError, ValueError):
        return None


def backfill_history(store: Store, *, season: int, log_dir: Path) -> BackfillSummary:
    """Rebuild history from recorded `report` batches and logged decisions."""
    from_picks = store.append_recommendation_history(store.pick_batches(season))

    if not log_dir.is_dir():
        logger.bind(event="log_directory_missing", log_dir=str(log_dir)).warning(
            f"no log directory at {log_dir}; only report batches were backfilled"
        )

    games = {game.game_id: game for game in store.games_for_season(season)}
    season_tag = f"-{season}-"
    seen = 0
    records: list[RecommendationRecord] = []
    if log_dir.is_dir():
        for record in iter_log_records(log_dir):
            if record.get("event") != _EDGE_EVENT or season_tag not in str(record.get("game_id")):
                continue
            seen += 1
            row = _history_from_log(record, games)
            if row is not None:
                records.append(row)
    from_logs = store.append_recommendation_history(records)

    summary = BackfillSummary(from_picks, from_logs, seen, seen - len(records))
    logger.bind(
        event="recommendation_history_backfilled", season=season, log_dir=str(log_dir),
        from_picks=from_picks, from_logs=from_logs, log_decisions_seen=seen,
        log_decisions_unmatched=summary.log_decisions_unmatched,
    ).info(
        f"backfilled {from_picks} report row(s) and {from_logs} logged decision(s); "
        f"{summary.log_decisions_unmatched} of {seen} logged decisions matched no stored game"
    )
    return summary
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_recommendation_history.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pickem/operations/recommendation_history.py tests/test_recommendation_history.py
git add src/pickem/operations/recommendation_history.py tests/test_recommendation_history.py
git commit -m "feat: keep and backfill the model's recommendation history

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 6: Record history live from the bot and from `report`

> **Gate:** `monitor.py`, `discord_bot.py` and `tests/test_discord_bot.py` carry uncommitted user work. Before starting, ask the user to commit it, or to approve this task's commit including it. Do not continue without an answer.

**Files:**
- Modify: `src/pickem/automation/monitor.py` (`RecommendationMonitor.__init__`, `_record_success`, new `_record_history_for`)
- Modify: `src/pickem/discord_bot.py` (imports; `_monitor_for`)
- Modify: `src/pickem/cli.py` (imports; `report` command)
- Test: `tests/test_monitor.py`, `tests/test_discord_bot.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `record_snapshot_history`, `history_from_snapshot` (Task 5); `HISTORY_MONITOR`, `HISTORY_REPORT` (Task 3).
- Produces: `RecommendationMonitor(..., scope, record_history: Callable[[Any, RecommendationSnapshot], Awaitable[None] | None] | None = None)`. Every successful refresh records its snapshot before any notification. A recording failure is logged as `history_record_failed` and never fails the refresh.

- [ ] **Step 1: Write the failing monitor tests**

Append to `tests/test_monitor.py`:

```python
def test_refresh_records_history_for_each_successful_snapshot():
    fake = FakeMonitor([snapshot_with({"game-a": Side.HOME})])
    recorded = []
    monitor = RecommendationMonitor(
        fake.refresh_week, fake.load_state, fake.save_state, fake.notify, SCOPE,
        record_history=lambda scope, snapshot: recorded.append((scope, snapshot)),
    )

    result = asyncio.run(monitor.refresh())

    assert result.error is None
    assert recorded == [(SCOPE, result.snapshot)]


def test_history_failure_is_logged_and_does_not_fail_the_refresh(records):
    fake = FakeMonitor([snapshot_with({"game-a": Side.HOME})])

    def fail(_scope, _snapshot):
        raise RuntimeError("disk full")

    monitor = RecommendationMonitor(
        fake.refresh_week, fake.load_state, fake.save_state, fake.notify, SCOPE,
        record_history=fail,
    )

    result = asyncio.run(monitor.refresh())

    assert result.error is None
    assert len(fake.saved_states) == 1
    failures = [r for r in records if r["extra"].get("event") == "history_record_failed"]
    assert len(failures) == 1
    assert "disk full" in failures[0]["message"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_monitor.py -k history -v`
Expected: FAIL with `TypeError: RecommendationMonitor.__init__() got an unexpected keyword argument 'record_history'`.

- [ ] **Step 3: Add the hook to the monitor**

In `src/pickem/automation/monitor.py`, next to the other callback aliases:

```python
_RecordHistory = Callable[[Any, RecommendationSnapshot], Awaitable[None] | None]
```

Change `__init__` to take and store it (keep every existing line):

```python
    def __init__(
        self,
        refresh_week: _RefreshWeek,
        load_state: _LoadState,
        save_state: _SaveState,
        notify: _Notify,
        scope: Any,
        *,
        record_history: _RecordHistory | None = None,
    ) -> None:
        self._refresh_week = refresh_week
        self._load_state = load_state
        self._save_state = save_state
        self._notify = notify
        self._scope = scope
        self._record_history = record_history
        self._lock = asyncio.Lock()
        self._load_error_fingerprint: str | None = None
        self._persistence_error_fingerprint: str | None = None
```

Extend the class docstring with one sentence: ``record_history``, when given, receives the scope and every successful snapshot.

Make the first statement of `_record_success`:

```python
        await self._record_history_for(snapshot)
```

Add the method after `_record_success`:

```python
    async def _record_history_for(self, snapshot: RecommendationSnapshot) -> None:
        """Keep what the model said before anything else can fail.

        A failure is logged, never raised: history is evidence for grading
        later, and losing one refresh's rows must not stop a pick-change DM.
        """
        if self._record_history is None:
            return
        try:
            await _invoke(self._record_history, self._scope, snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.bind(
                event="history_record_failed",
                error_type=type(error).__name__,
                error_detail=str(error),
                **_scope_fields(self._scope),
            ).opt(exception=(type(error), error, error.__traceback__)).error(
                f"recommendation history not recorded{_for_scope(self._scope)}: {error}"
            )
```

- [ ] **Step 4: Run the monitor tests to verify they pass**

Run: `uv run pytest tests/test_monitor.py -v`
Expected: all PASS, including every existing test.

- [ ] **Step 5: Write the failing bot test**

Append to `tests/test_discord_bot.py`:

```python
@pytest.mark.asyncio
async def test_monitor_refresh_records_recommendation_history(settings, monkeypatch):
    scope = MonitorScope(Sport.NFL, 2026, 1)
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 9, 2, tzinfo=UTC),
        edges=(
            Edge(
                game_id="nfl-2026-01-BUF-at-MIA",
                side=Side.HOME,
                delta=3.0,
                tier=Tier.STRONG,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale="league -3.0 vs market -6.0: 3.0 pts toward home",
            ),
        ),
    )
    monkeypatch.setattr(
        "pickem.discord_bot.refresh_recommendations", lambda *args, **kwargs: snapshot
    )
    bot = PickemBot(settings, scheduler=FakeScheduler())
    bot._load_state = lambda _scope: AutomationState()
    bot._save_state = lambda _scope, _state: None

    result = await bot._monitor_for(scope).refresh()

    assert result.error is None
    with Store(settings.db) as store:
        store.init_schema()
        history = store.recommendation_history(["nfl-2026-01-BUF-at-MIA"])
    assert [(r.side, r.tier, r.source) for r in history] == [(Side.HOME, Tier.STRONG, "monitor")]
```

Run: `uv run pytest tests/test_discord_bot.py -k records_recommendation_history -v`
Expected: FAIL, `assert [] == [(...)]`.

- [ ] **Step 6: Wire the hook in the bot**

In `src/pickem/discord_bot.py` add imports:

```python
from pickem.models import HISTORY_MONITOR, Game, Side, Sport, Tier
from pickem.operations.recommendation_history import record_snapshot_history
```

(the first replaces the existing `from pickem.models import Game, Side, Sport, Tier`). In `_monitor_for`, add one keyword argument to the `RecommendationMonitor(...)` call, after `scope=scope,`:

```python
                record_history=lambda _scope, snapshot: asyncio.to_thread(
                    record_snapshot_history, self.settings.db, snapshot, HISTORY_MONITOR
                ),
```

Run: `uv run pytest tests/test_discord_bot.py -v`
Expected: all PASS.

- [ ] **Step 7: Write the failing `report` test**

Append to `tests/test_cli.py`, next to `test_report_shows_provenance_and_records_its_picks`:

```python
def test_report_records_recommendation_history(tmp_path):
    from pickem.store.db import Store

    db = tmp_path / "test.duckdb"
    _seed(db)
    result = runner.invoke(
        app,
        ["report", "--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)],
    )
    assert result.exit_code == 0, result.output

    with Store(db) as store:
        history = store.recommendation_history(["nfl-2025-03-BUF-at-MIA"])
    assert [(r.tier.value, r.source) for r in history] == [("strong", "report")]
```

Run: `uv run pytest tests/test_cli.py -k records_recommendation_history -v`
Expected: FAIL, `assert [] == [('strong', 'report')]`.

- [ ] **Step 8: Record history in `report`**

In `src/pickem/cli.py` add imports:

```python
from pickem.models import HISTORY_REPORT, Game, Sport
from pickem.operations.recommendation_history import history_from_snapshot
```

(the first replaces `from pickem.models import Game, Sport`). In `report`, directly after `store.record_picks(snapshot.edges, season, week, now)`:

```python
            store.append_recommendation_history(history_from_snapshot(snapshot, HISTORY_REPORT))
```

- [ ] **Step 9: Run the affected suites**

Run: `uv run pytest tests/test_monitor.py tests/test_discord_bot.py tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 10: Lint and commit**

```bash
uv run ruff check src/pickem/automation/monitor.py src/pickem/discord_bot.py src/pickem/cli.py tests/test_monitor.py tests/test_discord_bot.py tests/test_cli.py
git add src/pickem/automation/monitor.py src/pickem/discord_bot.py src/pickem/cli.py tests/test_monitor.py tests/test_discord_bot.py tests/test_cli.py
git commit -m "feat: record every live recommendation for grading after kickoff

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 7: Grading, aggregates and the report model

**Files:**
- Create: `src/pickem/report/results.py`
- Test: `tests/test_results_report.py`

**Interfaces:**
- Consumes: `grade_pick`, `Result`, `wilson_interval` (`pickem.backtest.stats`); `consensus_spread`, `suppress_decision_logging` (`pickem.edge.divergence`); `favorite_side` (`pickem.edge.favorite`); `LIVE_SOURCE`; Task 3 store methods; `tests/results_helpers.imported_store`.
- Produces (all in `pickem.report.results`):
  - `AGAINST_FIELD_SHARE = 0.40`; `BACKTEST_TIER_RATES: dict[Tier, float]`
  - `class ResultsReportError(RuntimeError)`
  - `class Strategy(StrEnum)`: `US="us"`, `MODEL="model"`, `FIRST_SHEET="first sheet"`, `CLOSE_DIVERGENCE="close divergence"`, `FAVORITES="favorites"`, `HOME="home"`, `FIELD="field consensus"`; `BASELINES: tuple[Strategy, ...]`
  - `close_divergence_side(league_spread: float, close_spread: float | None) -> Side | None`
  - `field_consensus_side(home_picks: int, away_picks: int) -> Side | None`
  - `closing_line_value(side: Side | None, league_spread: float, close_spread: float | None) -> float | None`
  - `closing_spread(lines: Iterable[MarketLine], kickoff: datetime) -> float | None`
  - `last_before(history, kickoff) -> RecommendationRecord | None`; `earliest(history, kickoff) -> RecommendationRecord | None`
  - `@dataclass(frozen=True) GradedGame(game, pool_week, league_spread, close_spread, field_home, field_away, model, picks: dict[Strategy, Side | None])` with `result(strategy) -> Result | None`, `clv -> float | None`, `our_field_share -> float | None`, `against_field -> bool`
  - `grade_game(*, game, pool_week, league_spread, close_spread, our_side, field_home, field_away, history) -> GradedGame`
  - `@dataclass(frozen=True) Record(wins=0, losses=0, pushes=0)` with `decided`, `rate`, `interval`, `__str__`
  - `record_for(games, strategy, *, sport=None, tier=None) -> Record`
  - `@dataclass(frozen=True) ClvSummary(n, mean, positive_share, interval)`; `clv_summary(games, *, sport=None) -> ClvSummary`
  - `@dataclass(frozen=True) BoardStanding(sport, our_points, median_points, best_points)`
  - `@dataclass(frozen=True) WeekStanding(pool_week, entrants, our_rank, our_points, median_points, winner_points, beat_share, boards)` with `gap_to_winner`
  - `@dataclass(frozen=True) ResultsReport(season, pool_week, entry_name, standings, week_games, season_games)` with `current -> WeekStanding`, `unknown_model_games -> int`
  - `build_results_report(store: Store, *, season: int, pool_week: int, entry_name: str) -> ResultsReport`

Worked values the build test relies on (fixture store from Task 4, `KICK` = 2026-09-12 16:00 UTC for every game):

| Game | Covered | Us (Jota) | Field (others, blanks excluded) | Favorite |
|---|---|---|---|---|
| NE at SEA, −3.5, 10–13 | NE (away) | SEA ✗ | NE 1, SEA 1 → no consensus | SEA ✗ |
| OU at MICH, +5.5, 10–17 | MICH (home) | MICH ✓ | MICH 1, OU 1 → none | OU ✗ |
| PSU at TEM, +24.5, 27–9 | TEM (home) | PSU ✗ | TEM 1, PSU 1 → none | PSU ✗ |
| NO at DET, −7.5, 30–31 | NO (away) | DET ✗ | DET 2, NO 1 → DET ✗ | DET ✗ |

History added for PSU at TEM only: home/coinflip at KICK−2d, away/coinflip at KICK−1h, home/lean at KICK+1h (after kickoff, ignored). So model = PSU ✗ and first sheet = TEM ✓. Market lines for PSU at TEM: `oddsapi` book `a` 25.5 at KICK−2h, book `b` 26.0 at KICK−1h, book `a` 23.0 at KICK+1h (ignored), `oddsapi:frozen` 10.0 at KICK−3h (ignored). Close = median(25.5, 26.0) = 25.75 → close divergence = away (PSU) ✗; our CLV on PSU = +1.25.

Standings: points 20/16/16/6 → median 16, winner 20, gap 4, beat share 1/3. CFB board green marks A 2, Jota 1, B 0, C 0 → median 0.5, best 2. NFL board A 2, others 0 → median 0, best 2.

- [ ] **Step 1: Write the failing tests**

`tests/test_results_report.py`:

```python
from datetime import timedelta

import pytest
from results_helpers import KICK, imported_store

from pickem.backtest.stats import Result, wilson_interval
from pickem.models import (
    HISTORY_MONITOR,
    HISTORY_REPORT,
    Game,
    MarketLine,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.report.results import (
    Record,
    ResultsReportError,
    Strategy,
    build_results_report,
    close_divergence_side,
    closing_line_value,
    closing_spread,
    field_consensus_side,
    grade_game,
    last_before,
    record_for,
)

PSU_TEM = "cfb-2026-02-PSU-at-TEM"


def rec(at, side, tier=Tier.COINFLIP, source=HISTORY_MONITOR):
    return RecommendationRecord(game_id=PSU_TEM, sport=Sport.CFB, season=2026, week=2, side=side,
                                tier=tier, edge_points=0.5, generated_at=at, source=source)


def line(book, spread, at, source="oddsapi"):
    return MarketLine(game_id=PSU_TEM, source=source, book=book, spread_home=spread, captured_at=at)


def test_close_divergence_takes_the_side_the_market_moved_toward():
    assert close_divergence_side(-3.5, -5.0) is Side.HOME
    assert close_divergence_side(-3.5, -2.0) is Side.AWAY
    assert close_divergence_side(-3.5, -3.5) is None
    assert close_divergence_side(-3.5, None) is None


def test_closing_line_value_sign():
    assert closing_line_value(Side.HOME, -3.5, -5.0) == pytest.approx(1.5)
    assert closing_line_value(Side.AWAY, -3.5, -5.0) == pytest.approx(-1.5)
    assert closing_line_value(Side.HOME, -3.5, None) is None
    assert closing_line_value(None, -3.5, -5.0) is None


def test_field_consensus_skips_ties():
    assert field_consensus_side(3, 2) is Side.HOME
    assert field_consensus_side(1, 4) is Side.AWAY
    assert field_consensus_side(2, 2) is None


def test_model_pick_is_the_last_one_strictly_before_kickoff():
    history = [
        rec(KICK - timedelta(days=2), Side.HOME),
        rec(KICK - timedelta(hours=1), Side.AWAY),
        rec(KICK, Side.HOME, source=HISTORY_REPORT),
        rec(KICK + timedelta(hours=1), Side.HOME),
    ]
    assert last_before(history, KICK).side is Side.AWAY
    assert last_before([rec(KICK, Side.HOME)], KICK) is None


def test_closing_spread_uses_live_quotes_before_kickoff_only():
    lines = [
        line("a", 25.5, KICK - timedelta(hours=2)),
        line("b", 26.0, KICK - timedelta(hours=1)),
        line("a", 23.0, KICK + timedelta(hours=1)),
        line("c", 10.0, KICK - timedelta(hours=3), source="oddsapi:frozen"),
    ]
    assert closing_spread(lines, KICK) == pytest.approx(25.75)
    assert closing_spread([], KICK) is None


def test_grade_game_grades_every_strategy():
    game = Game(game_id=PSU_TEM, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
                home_team_id="TEM", away_team_id="PSU", home_score=9, away_score=27)
    graded = grade_game(
        game=game, pool_week=2, league_spread=24.5, close_spread=25.75, our_side=Side.AWAY,
        field_home=1, field_away=3,
        history=[rec(KICK - timedelta(days=2), Side.HOME), rec(KICK - timedelta(hours=1), Side.AWAY)],
    )
    assert graded.result(Strategy.US) is Result.LOSS
    assert graded.result(Strategy.MODEL) is Result.LOSS
    assert graded.result(Strategy.FIRST_SHEET) is Result.WIN
    assert graded.result(Strategy.CLOSE_DIVERGENCE) is Result.LOSS
    assert graded.result(Strategy.FAVORITES) is Result.LOSS
    assert graded.result(Strategy.HOME) is Result.WIN
    assert graded.result(Strategy.FIELD) is Result.LOSS
    assert graded.clv == pytest.approx(1.25)
    assert graded.our_field_share == pytest.approx(0.75)
    assert graded.against_field is False


def test_record_formats_rate_interval_and_n():
    record = Record(9, 6, 1)
    assert record.interval == wilson_interval(9, 15)
    text = str(record)
    assert text.startswith("9–6 (1) = 60.0% [")
    assert text.endswith("], n=15")
    assert str(Record(0, 0, 2)) == "0–0 (2) = n/a, n=0"


@pytest.fixture
def store():
    s = imported_store()
    s.append_recommendation_history([
        rec(KICK - timedelta(days=2), Side.HOME),
        rec(KICK - timedelta(hours=1), Side.AWAY),
        rec(KICK + timedelta(hours=1), Side.HOME, tier=Tier.LEAN),
    ])
    s.append_market_lines([
        line("a", 25.5, KICK - timedelta(hours=2)),
        line("b", 26.0, KICK - timedelta(hours=1)),
        line("a", 23.0, KICK + timedelta(hours=1)),
        line("c", 10.0, KICK - timedelta(hours=3), source="oddsapi:frozen"),
    ])
    yield s
    s.close()


def test_build_grades_us_the_model_the_field_and_baselines(store):
    report = build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    games = report.week_games
    assert len(games) == 4
    assert (record_for(games, Strategy.US).wins, record_for(games, Strategy.US).losses) == (1, 3)
    assert record_for(games, Strategy.FAVORITES).losses == 4
    assert (record_for(games, Strategy.HOME).wins, record_for(games, Strategy.HOME).losses) == (2, 2)
    assert record_for(games, Strategy.FIELD).decided == 1
    assert record_for(games, Strategy.MODEL).losses == 1
    assert record_for(games, Strategy.FIRST_SHEET).wins == 1
    assert report.unknown_model_games == 3

    psu = next(g for g in games if g.game.game_id == PSU_TEM)
    assert psu.model.tier is Tier.COINFLIP
    assert psu.close_spread == pytest.approx(25.75)
    assert psu.clv == pytest.approx(1.25)

    det = next(g for g in games if g.game.game_id == "nfl-2026-01-NO-at-DET")
    assert (det.field_home, det.field_away) == (2, 1)


def test_build_computes_standings(store):
    week = build_results_report(store, season=2026, pool_week=2, entry_name="Jota").current
    assert (week.entrants, week.our_rank, week.our_points) == (4, 17, 16)
    assert (week.median_points, week.winner_points, week.gap_to_winner) == (16, 20, 4)
    assert week.beat_share == pytest.approx(1 / 3)
    boards = {b.sport: (b.our_points, b.median_points, b.best_points) for b in week.boards}
    assert boards == {Sport.CFB: (1, 0.5, 2), Sport.NFL: (0, 0, 2)}


def test_build_rejects_unknown_entrant_and_week(store):
    with pytest.raises(ResultsReportError, match="no entrant named 'Nobody'.*Entrant A"):
        build_results_report(store, season=2026, pool_week=2, entry_name="Nobody")
    with pytest.raises(ResultsReportError, match="pool week 3 of 2026 is not imported"):
        build_results_report(store, season=2026, pool_week=3, entry_name="Jota")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results_report.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'pickem.report.results'`.

- [ ] **Step 3: Implement the grading module**

`src/pickem/report/results.py`:

```python
"""Grade a pool week: us, the model, the field, and simple baselines.

Reads stored rows only — never CBS, never the Odds API. Every rate carries its
interval, because ~15 games per board cannot support a conclusion on its own.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pickem.backtest.stats import Result, grade_pick, wilson_interval
from pickem.edge.divergence import consensus_spread, suppress_decision_logging
from pickem.edge.favorite import favorite_side
from pickem.models import (
    LIVE_SOURCE,
    Game,
    MarketLine,
    PoolPick,
    PoolResult,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.store.db import Store

AGAINST_FIELD_SHARE = 0.40

# NFL 2020-2025 backtest (docs/results.md). Shown as NFL numbers even beside CFB.
BACKTEST_TIER_RATES = {Tier.STRONG: 0.637, Tier.LEAN: 0.542, Tier.COINFLIP: 0.495}


class ResultsReportError(RuntimeError):
    """The requested report cannot be built from what is stored."""


class Strategy(StrEnum):
    US = "us"
    MODEL = "model"
    FIRST_SHEET = "first sheet"
    CLOSE_DIVERGENCE = "close divergence"
    FAVORITES = "favorites"
    HOME = "home"
    FIELD = "field consensus"


BASELINES = (
    Strategy.MODEL,
    Strategy.FIRST_SHEET,
    Strategy.CLOSE_DIVERGENCE,
    Strategy.FAVORITES,
    Strategy.HOME,
    Strategy.FIELD,
)


def close_divergence_side(league_spread: float, close_spread: float | None) -> Side | None:
    """The side the closing market favors against the frozen CBS line.

    A close more negative than the board makes home a bigger favorite than CBS
    did, so home is the side the market says is underpriced.
    """
    if close_spread is None or close_spread == league_spread:
        return None
    return Side.HOME if close_spread < league_spread else Side.AWAY


def field_consensus_side(home_picks: int, away_picks: int) -> Side | None:
    if home_picks == away_picks:
        return None
    return Side.HOME if home_picks > away_picks else Side.AWAY


def closing_line_value(
    side: Side | None, league_spread: float, close_spread: float | None
) -> float | None:
    """Points the close moved toward ``side``, measured from the CBS line.

    Taking home at -3.5 when the market closes at -5.0 is +1.5: the market came
    to us after CBS froze its number.
    """
    if side is None or close_spread is None:
        return None
    toward_home = league_spread - close_spread
    return toward_home if side is Side.HOME else -toward_home


def closing_spread(lines: Iterable[MarketLine], kickoff: datetime) -> float | None:
    """Live-market consensus from quotes captured before kickoff."""
    before = [ln for ln in lines if ln.source == LIVE_SOURCE and ln.captured_at < kickoff]
    with suppress_decision_logging():
        return consensus_spread(before)


def last_before(
    history: Iterable[RecommendationRecord], kickoff: datetime
) -> RecommendationRecord | None:
    """The recommendation the owner could still act on: latest strictly before kickoff."""
    eligible = [r for r in history if r.generated_at < kickoff]
    return max(eligible, key=lambda r: (r.generated_at, r.source), default=None)


def earliest(
    history: Iterable[RecommendationRecord], kickoff: datetime
) -> RecommendationRecord | None:
    eligible = [r for r in history if r.generated_at < kickoff]
    return min(eligible, key=lambda r: (r.generated_at, r.source), default=None)


@dataclass(frozen=True)
class GradedGame:
    game: Game
    pool_week: int
    league_spread: float
    close_spread: float | None
    field_home: int
    field_away: int
    model: RecommendationRecord | None
    picks: dict[Strategy, Side | None]

    def result(self, strategy: Strategy) -> Result | None:
        side = self.picks.get(strategy)
        if side is None:
            return None
        return grade_pick(side, self.game.home_score - self.game.away_score, self.league_spread)

    @property
    def clv(self) -> float | None:
        return closing_line_value(self.picks[Strategy.US], self.league_spread, self.close_spread)

    @property
    def our_field_share(self) -> float | None:
        side = self.picks[Strategy.US]
        total = self.field_home + self.field_away
        if side is None or total == 0:
            return None
        return (self.field_home if side is Side.HOME else self.field_away) / total

    @property
    def against_field(self) -> bool:
        share = self.our_field_share
        return share is not None and share <= AGAINST_FIELD_SHARE


def grade_game(
    *,
    game: Game,
    pool_week: int,
    league_spread: float,
    close_spread: float | None,
    our_side: Side | None,
    field_home: int,
    field_away: int,
    history: Sequence[RecommendationRecord],
) -> GradedGame:
    model = last_before(history, game.kickoff_utc)
    first = earliest(history, game.kickoff_utc)
    return GradedGame(
        game=game,
        pool_week=pool_week,
        league_spread=league_spread,
        close_spread=close_spread,
        field_home=field_home,
        field_away=field_away,
        model=model,
        picks={
            Strategy.US: our_side,
            Strategy.MODEL: model.side if model is not None else None,
            Strategy.FIRST_SHEET: first.side if first is not None else None,
            Strategy.CLOSE_DIVERGENCE: close_divergence_side(league_spread, close_spread),
            Strategy.FAVORITES: favorite_side(league_spread),
            Strategy.HOME: Side.HOME,
            Strategy.FIELD: field_consensus_side(field_home, field_away),
        },
    )


@dataclass(frozen=True)
class Record:
    wins: int = 0
    losses: int = 0
    pushes: int = 0

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def rate(self) -> float | None:
        return self.wins / self.decided if self.decided else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson_interval(self.wins, self.decided) if self.decided else None

    def __str__(self) -> str:
        head = f"{self.wins}–{self.losses} ({self.pushes})"
        if not self.decided:
            return f"{head} = n/a, n=0"
        low, high = self.interval
        return f"{head} = {self.rate:.1%} [{low:.0%}–{high:.0%}], n={self.decided}"


def record_for(
    games: Iterable[GradedGame],
    strategy: Strategy,
    *,
    sport: Sport | None = None,
    tier: Tier | None = None,
) -> Record:
    """W-L-P for one strategy; ``tier`` filters on the model's tier for the game."""
    counts = {Result.WIN: 0, Result.LOSS: 0, Result.PUSH: 0}
    for game in games:
        if sport is not None and game.game.sport is not sport:
            continue
        if tier is not None and (game.model is None or game.model.tier is not tier):
            continue
        result = game.result(strategy)
        if result is not None:
            counts[result] += 1
    return Record(counts[Result.WIN], counts[Result.LOSS], counts[Result.PUSH])


@dataclass(frozen=True)
class ClvSummary:
    n: int
    mean: float | None
    positive_share: float | None
    interval: tuple[float, float] | None


def clv_summary(games: Iterable[GradedGame], *, sport: Sport | None = None) -> ClvSummary:
    """Mean CLV of our picks, with a normal-approximation 95% interval (n >= 2)."""
    values = [
        game.clv
        for game in games
        if (sport is None or game.game.sport is sport) and game.clv is not None
    ]
    if not values:
        return ClvSummary(0, None, None, None)
    mean = statistics.fmean(values)
    positive = sum(1 for value in values if value > 0) / len(values)
    if len(values) < 2:
        return ClvSummary(len(values), mean, positive, None)
    half = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return ClvSummary(len(values), mean, positive, (mean - half, mean + half))


@dataclass(frozen=True)
class BoardStanding:
    sport: Sport
    our_points: int
    median_points: float
    best_points: int


@dataclass(frozen=True)
class WeekStanding:
    pool_week: int
    entrants: int
    our_rank: int
    our_points: int
    median_points: float
    winner_points: int
    beat_share: float
    boards: tuple[BoardStanding, ...]

    @property
    def gap_to_winner(self) -> int:
        return self.winner_points - self.our_points


@dataclass(frozen=True)
class ResultsReport:
    season: int
    pool_week: int
    entry_name: str
    standings: tuple[WeekStanding, ...]
    week_games: tuple[GradedGame, ...]
    season_games: tuple[GradedGame, ...]

    @property
    def current(self) -> WeekStanding:
        return self.standings[-1]

    @property
    def unknown_model_games(self) -> int:
        return sum(1 for game in self.season_games if game.model is None)


def build_results_report(
    store: Store, *, season: int, pool_week: int, entry_name: str
) -> ResultsReport:
    """Grade every imported pool week of ``season`` up to and including ``pool_week``."""
    weeks = [week for week in store.pool_weeks(season) if week <= pool_week]
    if pool_week not in weeks:
        raise ResultsReportError(
            f"pool week {pool_week} of {season} is not imported; run import-results first"
        )
    standings: list[WeekStanding] = []
    season_games: list[GradedGame] = []
    for week in weeks:
        standing, graded = _grade_week(store, season, week, entry_name)
        standings.append(standing)
        season_games.extend(graded)
    return ResultsReport(
        season=season,
        pool_week=pool_week,
        entry_name=entry_name,
        standings=tuple(standings),
        week_games=tuple(game for game in season_games if game.pool_week == pool_week),
        season_games=tuple(season_games),
    )


def _grade_week(
    store: Store, season: int, week: int, entry_name: str
) -> tuple[WeekStanding, list[GradedGame]]:
    results = store.pool_results(season, week)
    ours = next((r for r in results if r.name.strip() == entry_name), None)
    if ours is None:
        names = ", ".join(sorted(r.name for r in results))
        raise ResultsReportError(
            f"no entrant named {entry_name!r} in pool week {week}; names found: {names}"
        )

    picks = store.pool_picks(season, week)
    game_ids = sorted({pick.game_id for pick in picks})
    games = {game.game_id: game for game in store.games_by_ids(game_ids)}
    spreads = {ln.game_id: ln.spread_home for ln in store.league_lines_by_ids(game_ids)}
    lines: dict[str, list[MarketLine]] = defaultdict(list)
    for market_line in store.market_lines_by_ids(game_ids):
        lines[market_line.game_id].append(market_line)
    history: dict[str, list[RecommendationRecord]] = defaultdict(list)
    for record in store.recommendation_history(game_ids):
        history[record.game_id].append(record)
    by_game: dict[str, list[PoolPick]] = defaultdict(list)
    for pick in picks:
        by_game[pick.game_id].append(pick)

    graded: list[GradedGame] = []
    for game_id in game_ids:
        game = games[game_id]
        our_side = next((p.side for p in by_game[game_id] if p.entry_id == ours.entry_id), None)
        field = [
            p.side for p in by_game[game_id] if p.entry_id != ours.entry_id and p.side is not None
        ]
        graded.append(
            grade_game(
                game=game,
                pool_week=week,
                league_spread=spreads[game_id],
                close_spread=closing_spread(lines[game_id], game.kickoff_utc),
                our_side=our_side,
                field_home=field.count(Side.HOME),
                field_away=field.count(Side.AWAY),
                history=history[game_id],
            )
        )
    graded.sort(key=lambda g: (g.game.kickoff_utc, g.game.game_id))
    return _standing(week, results, ours, picks, games), graded


def _standing(
    week: int,
    results: Sequence[PoolResult],
    ours: PoolResult,
    picks: Sequence[PoolPick],
    games: dict[str, Game],
) -> WeekStanding:
    points = [r.points for r in results]
    boards: list[BoardStanding] = []
    for sport in sorted({game.sport for game in games.values()}):
        per_entrant = {r.entry_id: 0 for r in results}
        for pick in picks:
            if pick.cbs_correct and games[pick.game_id].sport is sport:
                per_entrant[pick.entry_id] += 1
        values = list(per_entrant.values())
        boards.append(
            BoardStanding(sport, per_entrant[ours.entry_id], statistics.median(values), max(values))
        )
    beaten = sum(1 for value in points if value < ours.points)
    return WeekStanding(
        pool_week=week,
        entrants=len(results),
        our_rank=ours.rank,
        our_points=ours.points,
        median_points=statistics.median(points),
        winner_points=max(points),
        beat_share=beaten / max(len(results) - 1, 1),
        boards=tuple(boards),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results_report.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pickem/report/results.py tests/test_results_report.py
git add src/pickem/report/results.py tests/test_results_report.py
git commit -m "feat: grade each pool week against the model, the field and baselines

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 8: "What the data says" and the Markdown report

**Files:**
- Modify: `src/pickem/report/results.py` (append `Findings`, `findings`)
- Create: `src/pickem/report/results_markdown.py`
- Test: `tests/test_results_markdown.py`

**Interfaces:**
- Consumes: everything Task 7 produces.
- Produces:
  - `@dataclass(frozen=True) Findings(claims: tuple[str, ...], not_yet: tuple[str, ...])`
  - `findings(games: Sequence[GradedGame]) -> Findings`
  - `render_results_report(report: ResultsReport, *, generated_at: datetime) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_results_markdown.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from results_helpers import KICK, imported_store

from pickem.models import HISTORY_MONITOR, Game, RecommendationRecord, Side, Sport, Tier
from pickem.report.results import build_results_report, findings, grade_game
from pickem.report.results_markdown import render_results_report


def synthetic(index: int, *, home_covers: bool, our_side: Side) -> object:
    game = Game(
        game_id=f"cfb-2026-02-A{index}-at-H{index}", sport=Sport.CFB, season=2026, week=2,
        kickoff_utc=KICK, home_team_id=f"H{index}", away_team_id=f"A{index}",
        home_score=20 if home_covers else 10, away_score=10 if home_covers else 20,
    )
    # A +3.5 home underdog: the favorites baseline always takes the away team.
    return grade_game(game=game, pool_week=2, league_spread=3.5, close_spread=None,
                      our_side=our_side, field_home=0, field_away=0, history=[])


def test_findings_claim_only_when_intervals_separate():
    games = [synthetic(i, home_covers=True, our_side=Side.HOME) for i in range(30)]
    result = findings(games)
    assert any("vs favorites" in claim and "us ahead" in claim for claim in result.claims)
    assert not any("vs home" in claim for claim in result.claims)
    assert any("vs home" in line and "not distinguishable yet" in line for line in result.not_yet)


def test_findings_make_no_claim_on_a_small_even_sample():
    games = [synthetic(i, home_covers=i % 2 == 0, our_side=Side.HOME) for i in range(4)]
    assert findings(games).claims == ()


@pytest.fixture
def store():
    s = imported_store()
    s.append_recommendation_history([
        RecommendationRecord(
            game_id="cfb-2026-02-PSU-at-TEM", sport=Sport.CFB, season=2026, week=2,
            side=Side.HOME, tier=Tier.COINFLIP, edge_points=0.5,
            generated_at=KICK - timedelta(hours=1), source=HISTORY_MONITOR,
        )
    ])
    yield s
    s.close()


def test_rendered_report_has_every_section_and_the_game_rows(store):
    report = build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    text = render_results_report(report, generated_at=datetime(2026, 9, 15, 12, tzinfo=UTC))

    for heading in (
        "# Pool week 2 results — 2026",
        "## Headline",
        "## This week",
        "### CFB board",
        "### NFL board",
        "## Season to date",
        "### Model by tier",
        "### Us against baselines",
        "### Closing-line value (our picks)",
        "### Against the field",
        "## What the data says",
    ):
        assert heading in text
    assert "**Jota: 16 pts, rank 17 of 4**" in text
    assert "| CFB | 1 | 0.5 | 2 |" in text
    psu_row = next(row for row in text.splitlines() if "PSU at TEM" in row)
    assert "PSU ✗" in psu_row
    assert "TEM (coinflip)" in psu_row
    assert "differs from model" in psu_row
    assert "Games with no recommendation stored before kickoff" in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results_markdown.py -v`
Expected: collection error, `ImportError: cannot import name 'findings'`.

- [ ] **Step 3: Add `findings` to `src/pickem/report/results.py`**

Append:

```python
@dataclass(frozen=True)
class Findings:
    claims: tuple[str, ...]
    not_yet: tuple[str, ...]


def _separate(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[1] < b[0] or b[1] < a[0]


def findings(games: Sequence[GradedGame]) -> Findings:
    """Statements the numbers support, and comparisons they cannot settle yet.

    A difference is claimed only when the two 95% intervals do not overlap.
    That test is deliberately conservative: comparisons on the same games are
    paired, and it errs toward saying nothing.
    """
    claims: list[str] = []
    not_yet: list[str] = []
    sports = sorted({game.game.sport for game in games})
    scopes: list[tuple[Sport | None, str]] = [(None, "all boards")]
    scopes += [(sport, sport.value.upper()) for sport in sports]

    for sport, label in scopes:
        ours = record_for(games, Strategy.US, sport=sport)
        for baseline in BASELINES:
            other = record_for(games, baseline, sport=sport)
            if not ours.decided or not other.decided:
                continue
            text = (
                f"{label}: us {ours.rate:.1%} (n={ours.decided}) vs {baseline.value} "
                f"{other.rate:.1%} (n={other.decided})"
            )
            if _separate(ours.interval, other.interval):
                claims.append(f"{text} — us {'ahead' if ours.rate > other.rate else 'behind'}")
            else:
                not_yet.append(f"{text} — not distinguishable yet")

        for tier, expected in BACKTEST_TIER_RATES.items():
            model = record_for(games, Strategy.MODEL, sport=sport, tier=tier)
            if not model.decided:
                continue
            low, high = model.interval
            text = (
                f"{label}: model {tier.value} {model.rate:.1%} (n={model.decided}) vs NFL "
                f"backtest {expected:.1%}"
            )
            if expected < low:
                claims.append(f"{text} — above expectation")
            elif expected > high:
                claims.append(f"{text} — below expectation")
            else:
                not_yet.append(f"{text} — not distinguishable yet")

        clv = clv_summary(games, sport=sport)
        if not clv.n:
            continue
        text = f"{label}: mean CLV {clv.mean:+.2f} pts (n={clv.n})"
        if clv.interval is not None and (clv.interval[0] > 0 or clv.interval[1] < 0):
            direction = "toward" if clv.mean > 0 else "against"
            claims.append(f"{text} — the market moved {direction} our picks")
        else:
            not_yet.append(f"{text} — not distinguishable from zero yet")
    return Findings(tuple(claims), tuple(not_yet))
```

- [ ] **Step 4: Implement the renderer**

`src/pickem/report/results_markdown.py`:

```python
"""Render a ResultsReport as the weekly Markdown report."""

from __future__ import annotations

from datetime import datetime

from pickem.backtest.stats import Result
from pickem.models import Side, Sport
from pickem.report.results import (
    AGAINST_FIELD_SHARE,
    BACKTEST_TIER_RATES,
    BASELINES,
    GradedGame,
    ResultsReport,
    Strategy,
    clv_summary,
    findings,
    record_for,
)

_MARKS = {Result.WIN: "✓", Result.LOSS: "✗", Result.PUSH: "push"}
_WEEK_STRATEGIES = (Strategy.US, Strategy.MODEL, Strategy.FIELD, Strategy.CLOSE_DIVERGENCE)


def render_results_report(report: ResultsReport, *, generated_at: datetime) -> str:
    week_sports = sorted({game.game.sport for game in report.week_games})
    season_sports = sorted({game.game.sport for game in report.season_games})
    lines = [
        f"# Pool week {report.pool_week} results — {report.season}",
        f"> Generated {generated_at:%Y-%m-%d %H:%M UTC} from stored CBS standings, league "
        "lines, market snapshots and recommendation history.",
        "",
        *_headline(report),
        "## This week",
        "",
    ]
    for sport in week_sports:
        games = [game for game in report.week_games if game.game.sport is sport]
        lines += [f"### {sport.value.upper()} board", "", "| Strategy | Record |", "|---|---|"]
        lines += [f"| {st.value} | {record_for(games, st)} |" for st in _WEEK_STRATEGIES]
        lines += [
            "",
            "| Kickoff (UTC) | Matchup | Line | Final | Covered | Us | Model | Field home % "
            "| Close | CLV | Flags |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
            *(_game_row(game) for game in games),
            "",
        ]
    lines += _season(report, season_sports)
    lines += _findings(report)
    return "\n".join(lines) + "\n"


def _headline(report: ResultsReport) -> list[str]:
    week = report.current
    return [
        "## Headline",
        "",
        f"**{report.entry_name}: {week.our_points} pts, rank {week.our_rank} of "
        f"{week.entrants}** — field median {week.median_points:g}, winner "
        f"{week.winner_points} (gap {week.gap_to_winner}); beat {week.beat_share:.0%} of the "
        "field.",
        "",
        "| Board | Us | Field median | Best |",
        "|---|---|---|---|",
        *(
            f"| {b.sport.value.upper()} | {b.our_points} | {b.median_points:g} | {b.best_points} |"
            for b in week.boards
        ),
        "",
    ]


def _season(report: ResultsReport, sports: list[Sport]) -> list[str]:
    games = report.season_games
    lines = [
        "## Season to date",
        "",
        "### Model by tier",
        "",
        "| Board | Tier | Record | NFL backtest |",
        "|---|---|---|---|",
    ]
    for sport in sports:
        for tier, expected in BACKTEST_TIER_RATES.items():
            record = record_for(games, Strategy.MODEL, sport=sport, tier=tier)
            lines.append(f"| {sport.value.upper()} | {tier.value} | {record} | {expected:.1%} |")
    lines += [
        "",
        "Games with no recommendation stored before kickoff (excluded from model grading): "
        f"{report.unknown_model_games}",
        "",
        "### Us against baselines",
        "",
        "| Board | Strategy | Record |",
        "|---|---|---|",
    ]
    for sport in [None, *sports]:
        label = "All" if sport is None else sport.value.upper()
        for strategy in (Strategy.US, *BASELINES):
            lines.append(f"| {label} | {strategy.value} | {record_for(games, strategy, sport=sport)} |")
    lines += [
        "",
        "### Closing-line value (our picks)",
        "",
        "| Board | n | Mean | 95% interval | Share > 0 |",
        "|---|---|---|---|---|",
    ]
    for sport in [None, *sports]:
        label = "All" if sport is None else sport.value.upper()
        clv = clv_summary(games, sport=sport)
        if not clv.n:
            lines.append(f"| {label} | 0 | n/a | n/a | n/a |")
            continue
        interval = (
            "n/a" if clv.interval is None else f"{clv.interval[0]:+.2f} to {clv.interval[1]:+.2f}"
        )
        lines.append(
            f"| {label} | {clv.n} | {clv.mean:+.2f} | {interval} | {clv.positive_share:.0%} |"
        )
    against = [game for game in games if game.against_field]
    lines += [
        "",
        f"### Against the field (≤{AGAINST_FIELD_SHARE:.0%} of other entrants on our side)",
        "",
        f"Record: {record_for(against, Strategy.US)}",
        "",
    ]
    return lines


def _findings(report: ResultsReport) -> list[str]:
    result = findings(report.season_games)
    lines = ["## What the data says", ""]
    lines += [f"- {claim}" for claim in result.claims] or ["- Nothing is distinguishable yet."]
    if result.not_yet:
        lines += ["", "Not distinguishable yet:", ""]
        lines += [f"- {line}" for line in result.not_yet]
    return lines


def _team(game: GradedGame, side: Side | None) -> str:
    if side is None:
        return "—"
    return game.game.home_team_id if side is Side.HOME else game.game.away_team_id


def _game_row(game: GradedGame) -> str:
    g = game.game
    home_result = game.result(Strategy.HOME)
    covered = "push" if home_result is Result.PUSH else _team(
        game, Side.HOME if home_result is Result.WIN else Side.AWAY
    )
    ours = game.picks[Strategy.US]
    us = "blank" if ours is None else f"{_team(game, ours)} {_MARKS[game.result(Strategy.US)]}"
    model = "—" if game.model is None else f"{_team(game, game.model.side)} ({game.model.tier.value})"
    total = game.field_home + game.field_away
    field = "—" if not total else f"{game.field_home / total:.0%}"
    close = "n/a" if game.close_spread is None else f"{game.close_spread:+.1f}"
    clv = "n/a" if game.clv is None else f"{game.clv:+.1f}"
    flags = []
    if game.model is not None and ours is not None and game.model.side is not ours:
        flags.append("differs from model")
    if game.against_field:
        flags.append("against field")
    return (
        f"| {g.kickoff_utc:%a %H:%M} | {g.away_team_id} at {g.home_team_id} | "
        f"{game.league_spread:+.1f} | {g.away_score}-{g.home_score} | {covered} | {us} | "
        f"{model} | {field} | {close} | {clv} | {', '.join(flags)} |"
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results_markdown.py tests/test_results_report.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

Wrap any line ruff reports as over 100 characters; do not change behavior.

```bash
uv run ruff check src/pickem/report/results.py src/pickem/report/results_markdown.py tests/test_results_markdown.py
git add src/pickem/report/results.py src/pickem/report/results_markdown.py tests/test_results_markdown.py
git commit -m "feat: render the weekly results report with only supported claims

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 9: Discord results DM

**Files:**
- Create: `src/pickem/notify/__init__.py` (a one-line module docstring)
- Create: `src/pickem/notify/discord_dm.py`
- Test: `tests/test_discord_dm.py`

**Interfaces:**
- Consumes: `ResultsReport`, `Strategy`, `BACKTEST_TIER_RATES`, `record_for`, `clv_summary` (Tasks 7–8).
- Produces:
  - `build_results_embed(report: ResultsReport, report_path: Path) -> discord.Embed`
  - `async send_owner_dm(embed: discord.Embed, *, token: str, owner_id: int, client_factory: Callable[[], Any] = _default_client) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_discord_dm.py`:

```python
import asyncio
from pathlib import Path

import discord
import pytest
from results_helpers import imported_store

from pickem.notify.discord_dm import _cap, build_results_embed, send_owner_dm
from pickem.report.results import build_results_report


@pytest.fixture
def report():
    store = imported_store()
    try:
        yield build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    finally:
        store.close()


def test_embed_carries_headline_boards_tiers_clv_and_path(report):
    embed = build_results_embed(report, Path("data/cbs/results/week2-report.md"))
    assert embed.title == "🏈 Pool week 2 results"
    assert "Jota: 16 pts, rank 17 of 4" in embed.description
    names = [field.name for field in embed.fields]
    assert "CFB board — 1 pts (median 0.5, best 2)" in names
    assert "NFL board — 0 pts (median 0, best 2)" in names
    assert "Season to date — model by tier" in names
    assert "Closing-line value (season)" in names
    assert embed.fields[-1].value == "data/cbs/results/week2-report.md"
    assert all(len(field.value) <= 1024 for field in embed.fields)


def test_cap_truncates_to_discords_field_limit():
    assert _cap("short") == "short"
    capped = _cap("x" * 2000)
    assert len(capped) == 1024
    assert capped.endswith("…")


class FakeUser:
    def __init__(self, calls, fail):
        self.calls = calls
        self.fail = fail

    async def send(self, *, embed):
        self.calls.append(("send", embed))
        if self.fail:
            raise RuntimeError("cannot DM")


class FakeClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def login(self, token):
        self.calls.append(("login", token))

    async def fetch_user(self, owner_id):
        self.calls.append(("fetch_user", owner_id))
        return FakeUser(self.calls, self.fail)

    async def close(self):
        self.calls.append(("close",))


def test_send_owner_dm_logs_in_sends_and_closes():
    client = FakeClient()
    embed = discord.Embed(title="t")
    asyncio.run(send_owner_dm(embed, token="tok", owner_id=123, client_factory=lambda: client))
    assert client.calls == [("login", "tok"), ("fetch_user", 123), ("send", embed), ("close",)]


def test_send_owner_dm_closes_the_session_when_sending_fails():
    client = FakeClient(fail=True)
    with pytest.raises(RuntimeError, match="cannot DM"):
        asyncio.run(send_owner_dm(discord.Embed(title="t"), token="tok", owner_id=123,
                                  client_factory=lambda: client))
    assert client.calls[-1] == ("close",)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_discord_dm.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'pickem.notify'`.

- [ ] **Step 3: Implement the module**

`src/pickem/notify/__init__.py`:

```python
"""Outbound notifications that do not need the running bot."""
```

`src/pickem/notify/discord_dm.py`:

```python
"""One-shot Discord DM for a finished results report.

Uses discord.py's REST session only — log in, fetch the owner, send, close —
with no gateway connection. It works whether or not the bot service is running
and never touches that process. The report file stays the record; the DM is a
pointer to it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import discord

from pickem.report.results import (
    BACKTEST_TIER_RATES,
    ResultsReport,
    Strategy,
    clv_summary,
    record_for,
)

_FIELD_VALUE_LIMIT = 1024
_BOARD_STRATEGIES = (Strategy.US, Strategy.MODEL, Strategy.FIELD, Strategy.CLOSE_DIVERGENCE)


def _cap(text: str) -> str:
    if len(text) <= _FIELD_VALUE_LIMIT:
        return text
    return text[: _FIELD_VALUE_LIMIT - 1] + "…"


def build_results_embed(report: ResultsReport, report_path: Path) -> discord.Embed:
    week = report.current
    embed = discord.Embed(
        title=f"🏈 Pool week {report.pool_week} results",
        description=(
            f"{report.entry_name}: {week.our_points} pts, rank {week.our_rank} of "
            f"{week.entrants} · median {week.median_points:g} · winner {week.winner_points} "
            f"(gap {week.gap_to_winner})"
        ),
        color=discord.Color.blue(),
    )
    for board in week.boards:
        games = [game for game in report.week_games if game.game.sport is board.sport]
        value = "\n".join(f"{st.value}: {record_for(games, st)}" for st in _BOARD_STRATEGIES)
        embed.add_field(
            name=(
                f"{board.sport.value.upper()} board — {board.our_points} pts "
                f"(median {board.median_points:g}, best {board.best_points})"
            ),
            value=_cap(value),
            inline=False,
        )
    tiers = "\n".join(
        f"{tier.value}: {record_for(report.season_games, Strategy.MODEL, tier=tier)} "
        f"(NFL backtest {rate:.1%})"
        for tier, rate in BACKTEST_TIER_RATES.items()
    )
    embed.add_field(name="Season to date — model by tier", value=_cap(tiers), inline=False)
    clv = clv_summary(report.season_games)
    clv_text = (
        "no closing lines stored"
        if not clv.n
        else f"mean {clv.mean:+.2f} pts over {clv.n} picks, {clv.positive_share:.0%} positive"
    )
    embed.add_field(name="Closing-line value (season)", value=_cap(clv_text), inline=False)
    embed.add_field(name="Full report", value=_cap(str(report_path)), inline=False)
    return embed


def _default_client() -> discord.Client:
    return discord.Client(intents=discord.Intents.none())


async def send_owner_dm(
    embed: discord.Embed,
    *,
    token: str,
    owner_id: int,
    client_factory: Callable[[], Any] = _default_client,
) -> None:
    """Deliver one embed to the owner over REST, always closing the session."""
    client = client_factory()
    try:
        await client.login(token)
        user = await client.fetch_user(owner_id)
        await user.send(embed=embed)
    finally:
        await client.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_discord_dm.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/pickem/notify tests/test_discord_dm.py
git add src/pickem/notify/__init__.py src/pickem/notify/discord_dm.py tests/test_discord_dm.py
git commit -m "feat: DM a results summary over a one-shot Discord REST session

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 10: CLI commands

**Files:**
- Modify: `src/pickem/config.py` (constants under `DEFAULT_DB`)
- Modify: `src/pickem/cli.py` (imports; three new commands after `sync-results`)
- Test: `tests/test_results_cli.py`

**Interfaces:**
- Consumes: `parse_cbs_results_html` (T2), `import_results`, `ResultsImportError` (T4), `backfill_history` (T5), `build_results_report`, `ResultsReportError` (T7), `render_results_report` (T8), `build_results_embed`, `send_owner_dm` (T9).
- Produces:
  - `config.DEFAULT_RESULTS_DIR = Path("data/cbs/results")`, `config.DEFAULT_ENTRY_NAME = "Jota"`
  - `pickem import-results --season S --pool-week N [--file PATH] [--entry-name NAME] [--out-dir DIR] [--notify/--no-notify] [--db PATH]` (DM on by default)
  - `pickem results-report --season S [--pool-week N] [--entry-name NAME] [--out-dir DIR] [--notify/--no-notify] [--db PATH]` (DM off by default)
  - `pickem backfill-recommendations --season S [--logs DIR] [--db PATH]`
  - Exit codes: 0 success; 1 read/parse/import/report failure (nothing written by a failed import); 2 DM failure (import and report kept).

- [ ] **Step 1: Write the failing tests**

`tests/test_results_cli.py`:

```python
import json
import shutil

import pytest
from results_helpers import FIXTURE, seed
from typer.testing import CliRunner

from pickem.cli import app
from pickem.store.db import Store

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path):
    db = tmp_path / "pickem.duckdb"
    with Store(db) as store:
        store.init_schema()
        seed(store)
    results = tmp_path / "results"
    results.mkdir()
    shutil.copy(FIXTURE, results / "week2.html")
    return db, results


def invoke_import(db, results, *extra):
    return runner.invoke(app, [
        "import-results", "--season", "2026", "--pool-week", "2", "--db", str(db),
        "--file", str(results / "week2.html"), "--out-dir", str(results), *extra,
    ])


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send(embed, *, token, owner_id):
        calls.append((embed.title, token, owner_id))

    monkeypatch.setattr("pickem.cli.send_owner_dm", fake_send)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    return calls


def test_import_results_stores_the_week_and_writes_the_report(workspace, sent):
    db, results = workspace
    result = invoke_import(db, results, "--no-notify")
    assert result.exit_code == 0, result.output
    assert "imported pool week 2: 4 entrants" in result.output
    report = results / "week2-report.md"
    assert "## Headline" in report.read_text()
    assert sent == []
    with Store(db) as store:
        assert store.pool_weeks(2026) == [2]


def test_import_results_sends_the_dm_by_default(workspace, sent):
    db, results = workspace
    result = invoke_import(db, results)
    assert result.exit_code == 0, result.output
    assert sent == [("🏈 Pool week 2 results", "test-token", 123)]
    assert "Discord DM sent" in result.output


def test_dm_failure_exits_2_and_keeps_the_report(workspace, sent, monkeypatch):
    db, results = workspace
    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    result = invoke_import(db, results)
    assert result.exit_code == 2, result.output
    assert "the Discord DM failed" in result.output
    assert (results / "week2-report.md").exists()


def test_unparseable_page_exits_1(workspace):
    db, results = workspace
    (results / "week2.html").write_text("<html><body>Lobby</body></html>")
    result = invoke_import(db, results, "--no-notify")
    assert result.exit_code == 1
    assert "no Weekly Standings table" in result.output


def test_page_that_does_not_link_exits_1_without_writing(tmp_path):
    db = tmp_path / "empty.duckdb"
    results = tmp_path / "results"
    results.mkdir()
    shutil.copy(FIXTURE, results / "week2.html")
    result = invoke_import(db, results, "--no-notify")
    assert result.exit_code == 1
    assert "was not imported" in result.output
    assert not (results / "week2-report.md").exists()


def test_results_report_rebuilds_the_latest_week_without_a_dm(workspace, sent):
    db, results = workspace
    assert invoke_import(db, results, "--no-notify").exit_code == 0
    (results / "week2-report.md").unlink()
    result = runner.invoke(app, [
        "results-report", "--season", "2026", "--db", str(db), "--out-dir", str(results),
    ])
    assert result.exit_code == 0, result.output
    assert (results / "week2-report.md").exists()
    assert sent == []


def test_results_report_with_nothing_imported_exits_1(workspace):
    db, results = workspace
    result = runner.invoke(app, ["results-report", "--season", "2026", "--db", str(db)])
    assert result.exit_code == 1
    assert "run import-results first" in result.output


def test_backfill_recommendations_reports_what_it_added(workspace, tmp_path):
    db, _ = workspace
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "pickem.jsonl").write_text(json.dumps({
        "ts": "2026-09-12T10:00:00-04:00", "event": "edge_decided",
        "game_id": "cfb-2026-02-PSU-at-TEM", "side": "away", "tier": "coinflip", "delta": 0.5,
    }) + "\n")
    result = runner.invoke(app, [
        "backfill-recommendations", "--season", "2026", "--db", str(db), "--logs", str(logs),
    ])
    assert result.exit_code == 0, result.output
    assert "0 history rows from report batches and 1 from logs" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_results_cli.py -v`
Expected: FAIL — typer reports `No such command 'import-results'` (exit code 2), and `monkeypatch.setattr("pickem.cli.send_owner_dm", ...)` raises `AttributeError`.

- [ ] **Step 3: Add the config constants**

In `src/pickem/config.py`, directly under `DEFAULT_DB = Path("data/pickem.duckdb")`:

```python
DEFAULT_RESULTS_DIR = Path("data/cbs/results")
# The owner's display name on the CBS standings page.
DEFAULT_ENTRY_NAME = "Jota"
```

- [ ] **Step 4: Add the commands**

In `src/pickem/cli.py`, add `import asyncio` and `import os` to the stdlib imports, `from loguru import logger` to the third-party imports, and these first-party imports (keep ruff's sort order):

```python
from pickem.ingest.cbs_results import parse_cbs_results_html
from pickem.notify.discord_dm import build_results_embed, send_owner_dm
from pickem.operations.recommendation_history import backfill_history
from pickem.operations.results_import import ResultsImportError, import_results
from pickem.report.results import ResultsReport, ResultsReportError, build_results_report
from pickem.report.results_markdown import render_results_report
```

`CbsParseError` and `TeamResolver` are already imported. Add after the `sync-results` command:

```python
def _write_results_report(
    store: Store, season: int, pool_week: int, entry_name: str, out_dir: Path
) -> tuple[ResultsReport, Path]:
    try:
        report = build_results_report(
            store, season=season, pool_week=pool_week, entry_name=entry_name
        )
    except ResultsReportError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=1) from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"week{pool_week}-report.md"
    path.write_text(render_results_report(report, generated_at=datetime.now(tz=UTC)))
    logger.bind(event="results_report_written", pool_week=pool_week, path=str(path)).info(
        f"results report written to {path}"
    )
    typer.echo(f"report written to {path}")
    return report, path


def _notify_results(report: ResultsReport, path: Path) -> None:
    """Send the DM; on failure keep everything and exit 2 so the miss is visible."""
    try:
        asyncio.run(
            send_owner_dm(
                build_results_embed(report, path),
                token=config.discord_bot_token(),
                owner_id=config.discord_owner_id(),
            )
        )
    except Exception as exc:
        logger.bind(
            event="results_dm_failed",
            pool_week=report.pool_week,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        ).error(f"results DM not sent: {exc}")
        typer.secho(
            f"report kept at {path}, but the Discord DM failed: {exc}", fg="red", err=True
        )
        raise typer.Exit(code=2) from exc
    logger.bind(event="results_dm_sent", pool_week=report.pool_week).info("results DM sent")
    typer.echo("Discord DM sent")


@app.command("import-results")
def import_results_cmd(
    season: int = typer.Option(...),
    pool_week: int = typer.Option(..., help="Pool week, not league week: CFB N + NFL N-1"),
    file: Path = typer.Option(
        None, help="Saved CBS Weekly Standings page (default: <out-dir>/week<N>.html)"
    ),
    entry_name: str = typer.Option(config.DEFAULT_ENTRY_NAME),
    out_dir: Path = typer.Option(config.DEFAULT_RESULTS_DIR),
    notify: bool = typer.Option(True, "--notify/--no-notify"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Import a saved Weekly Standings page, write its report, and DM a summary."""
    with run_context("cli:import-results", season=season, pool_week=pool_week, db=str(db)):
        source = file or out_dir / f"week{pool_week}.html"
        try:
            parsed = parse_cbs_results_html(source.read_text(encoding="utf-8"))
        except OSError as exc:
            typer.secho(f"cannot read {source}: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
        except CbsParseError as exc:
            typer.secho(f"cannot parse {source}: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc

        with _store(db) as store:
            try:
                summary = import_results(
                    store,
                    parsed,
                    season=season,
                    pool_week=pool_week,
                    resolver=TeamResolver.default(),
                    imported_at=datetime.now(tz=UTC),
                )
            except ResultsImportError as exc:
                typer.secho(str(exc), fg="red", err=True)
                raise typer.Exit(code=1) from exc
            boards = ", ".join(
                f"{count} {sport.value}" for sport, count in sorted(summary.games_by_sport.items())
            )
            typer.echo(
                f"imported pool week {pool_week}: {summary.entrants} entrants, {boards} games, "
                f"{summary.picks} picks ({summary.blank_picks} blank)"
            )
            report, path = _write_results_report(store, season, pool_week, entry_name, out_dir)
        if notify:
            _notify_results(report, path)


@app.command("results-report")
def results_report_cmd(
    season: int = typer.Option(...),
    pool_week: int = typer.Option(None, help="Default: the latest imported pool week"),
    entry_name: str = typer.Option(config.DEFAULT_ENTRY_NAME),
    out_dir: Path = typer.Option(config.DEFAULT_RESULTS_DIR),
    notify: bool = typer.Option(False, "--notify/--no-notify"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Rebuild a results report from the store; DM it only with --notify."""
    with run_context("cli:results-report", season=season, pool_week=pool_week, db=str(db)):
        with _store(db) as store:
            weeks = store.pool_weeks(season)
            if not weeks:
                typer.secho(
                    f"no pool weeks of {season} are imported; run import-results first",
                    fg="red",
                    err=True,
                )
                raise typer.Exit(code=1)
            week = pool_week if pool_week is not None else weeks[-1]
            report, path = _write_results_report(store, season, week, entry_name, out_dir)
        if notify:
            _notify_results(report, path)


@app.command("backfill-recommendations")
def backfill_recommendations_cmd(
    season: int = typer.Option(...),
    logs: Path = typer.Option(None, help="Log directory (default: $PICKEM_LOG_DIR or ~/LOGS/pickem)"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Rebuild recommendation history from recorded report batches and the JSONL logs."""
    with run_context("cli:backfill-recommendations", season=season, db=str(db)):
        log_dir = logs or Path(os.environ.get("PICKEM_LOG_DIR") or Path.home() / "LOGS" / "pickem")
        with _store(db) as store:
            summary = backfill_history(store, season=season, log_dir=log_dir)
        typer.echo(
            f"added {summary.from_picks} history rows from report batches and "
            f"{summary.from_logs} from logs ({summary.log_decisions_seen} logged decisions "
            f"seen, {summary.log_decisions_unmatched} not matched to a stored game)"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results_cli.py tests/test_cli.py -v`
Expected: all PASS.

If `test_page_that_does_not_link_exits_1_without_writing` shows several problems in the output, that is expected: an empty store fails every game's link.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/pickem/cli.py src/pickem/config.py tests/test_results_cli.py
git add src/pickem/cli.py src/pickem/config.py tests/test_results_cli.py
git commit -m "feat: import-results, results-report and backfill-recommendations commands

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Real-page check, README, and acceptance

> **Gate:** `README.md` carries uncommitted user work. Ask the user before committing it (Step 4).

**Files:**
- Create: `tests/test_results_real_pages.py`
- Modify: `README.md` (weekly workflow)

**Interfaces:**
- Consumes: everything above.
- Produces: a local-only regression test over the real saved pages; user-facing docs; a verified run on real data.

- [ ] **Step 1: Write the real-page test**

`tests/test_results_real_pages.py`:

```python
"""Checks against the real saved standings pages.

`data/` is gitignored, so these skip anywhere the pages are not present.
"""

from pathlib import Path

import pytest

from pickem.backtest.stats import Result, grade_pick
from pickem.ingest.cbs_results import parse_cbs_results_html
from pickem.models import Side
from pickem.resolve.resolver import TeamResolver

PAGES = Path("data/cbs/results")

pytestmark = pytest.mark.skipif(
    not (PAGES / "week2.html").exists(), reason="real CBS pages are local-only"
)


@pytest.mark.parametrize(
    ("week", "games", "jota_points", "jota_rank"), [(1, 15, 8, 21), (2, 31, 16, 17)]
)
def test_real_standings_page(week, games, jota_points, jota_rank):
    parsed = parse_cbs_results_html((PAGES / f"week{week}.html").read_text(encoding="utf-8"))

    assert len(parsed.games) == games
    assert len(parsed.entrants) == 53
    jota = next(e for e in parsed.entrants if e.name == "Jota")
    assert (jota.points, jota.rank) == (jota_points, jota_rank)

    by_game = {game.cbs_event_id: game for game in parsed.games}
    for pick in parsed.picks:
        if pick.picked_abbrev is None:
            continue
        game = by_game[pick.cbs_event_id]
        side = Side.HOME if pick.picked_abbrev == game.home_abbrev else Side.AWAY
        result = grade_pick(side, game.home_score - game.away_score, game.spread_home)
        assert (result is Result.WIN) == pick.cbs_correct, (game, pick)

    # CBS's points column is exactly its own count of green marks.
    for entrant in parsed.entrants:
        greens = sum(1 for p in parsed.picks if p.entry_id == entrant.entry_id and p.cbs_correct)
        assert entrant.points == greens, entrant.name

    resolver = TeamResolver.default()
    for game in parsed.games:
        resolver.resolve(game.away_abbrev, game.sport)
        resolver.resolve(game.home_abbrev, game.sport)
```

Run: `uv run pytest tests/test_results_real_pages.py -v`
Expected: 2 PASS (on this machine).

- [ ] **Step 2: Commit the test**

```bash
uv run ruff check tests/test_results_real_pages.py
git add tests/test_results_real_pages.py
git commit -m "test: check the parser against the real saved standings pages

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 3: Document the weekly step**

In `README.md`, add a new section directly after the `### 6. Sync results after the week` section (read the file first; keep the user's pending edits intact):

````markdown
### 7. Import the week's results

After every game in the pool week is final, open CBS **Standings → Weekly**,
pick the week, let the page finish loading, and save it (Save Page As →
"Webpage, Complete" or "HTML only") as `data/cbs/results/week<N>.html`, where
N is the **pool** week. Then:

```bash
uv run pickem import-results --season 2026 --pool-week 2
```

It links every game to the stored boards, checks CBS's green and red marks
against the scores and lines, and refuses the whole page if anything disagrees.
It then writes `data/cbs/results/week2-report.md` and DMs a summary. Use
`--no-notify` to skip the DM. If the DM fails, the command exits 2 but keeps the
import and the report; `uv run pickem results-report --season 2026 --notify`
resends it.

The report grades four things side by side: your submitted picks, the model's
last recommendation before each kickoff, the field's consensus, and simple
baselines (favorites, home teams, the closing market). A comparison is called a
difference only once its 95% intervals separate; until then it says "not
distinguishable yet".

An unknown CBS abbreviation stops the import with the name to add to
`src/pickem/resolve/aliases.yaml`.

One-off, for weeks played before recommendation history existed:

```bash
uv run pickem backfill-recommendations --season 2026
```
````

- [ ] **Step 4: Commit the README (after the gate)**

Ask the user whether to commit `README.md` with their pending edits included. Only on a yes:

```bash
git add README.md
git commit -m "docs: document the weekly results import

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest` then `uv run ruff check src tests`
Expected: all tests PASS, no lint findings. Report any failure with its output; do not claim completion without this.

- [ ] **Step 6: Acceptance on real data**

Back up the database first: `cp data/pickem.duckdb data/backups/pickem.pre-results.$(date +%Y%m%dT%H%M).duckdb`.

Run, in order, and record the output of each:

1. `uv run pickem backfill-recommendations --season 2026`
   Expected: non-zero rows from report batches (CFB week 1, CFB week 2 and NFL week 1 `report` runs) and from logs.
2. `uv run pickem import-results --season 2026 --pool-week 1 --no-notify`
   Expected: `imported pool week 1: 53 entrants, 15 cfb games, …`.
3. `uv run pickem import-results --season 2026 --pool-week 2`
   Expected: `imported pool week 2: 53 entrants, 15 cfb, 16 nfl games, …`, `report written to data/cbs/results/week2-report.md`, `Discord DM sent`, and one DM arriving.
4. Check the reports: `week1-report.md` headline shows `Jota: 8 pts, rank 21 of 53`; `week2-report.md` shows `Jota: 16 pts, rank 17 of 53`.
5. In `week2-report.md`, the CFB rows for PSU at TEM, DUKE at ILL and MSST at MINN show the model on PSU, ILL and MSST respectively (the refreshed recommendations), with no `differs from model` flag on those rows.
6. `uv run pytest tests/test_results_real_pages.py -v` still passes.

If step 5 does not hold, stop and report what the rows show instead. Do not adjust grading to make it pass: it means the history or the kickoff comparison is wrong, which is a finding for the user.

If the bot service holds the DuckDB file lock while a command runs, retry the command; the bot opens the file only briefly per refresh.
