# CBS Pick Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** About an hour before each kickoff, read the owner's entered picks from CBS, compare them with the model's current picks for the games starting then, and DM the owner when a pick differs or is missing, or when the check could not run.

**Architecture:** A pure parser reads the owner's entry (`entryPicks`) out of the CBS board page the project already fetches. A pure comparison module turns those picks, the model's snapshot, and the games to check into per-game outcomes and message text. The Discord bot runs the check straight after the smallest-offset kickoff poll (T-1h) and DMs through its existing owner DM. A `check-picks` CLI command runs the same comparison for the whole week by hand.

**Tech Stack:** Python 3.12, typer, discord.py, APScheduler, zendriver (headless Chrome, already used by `fetch-cbs`), DuckDB store, loguru, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-24-cbs-pick-check-design.md`

## Global Constraints

- Nothing is ever submitted to CBS. The check only reads the page.
- Silent when every checked pick matches and the model pick was fresh. Every other outcome, including every failure, DMs the owner.
- The check runs only after the poll whose `offset_hours` equals `min(settings.poll_offsets_hours)` (1h with today's config).
- Tiebreaker answers are not checked.
- The bot's CBS fetch writes no file to the NAS.
- Log events: `pick_check_completed` and `pick_check_failed`. DM delivery failures reuse the existing `owner_dm_failed`.
- Pool week = CFB week N = NFL week N-1 (`league_week` in `operations/results_import.py`).
- Ruff line length 100. Run `uv run pytest -q` and `uv run ruff check .` before each commit.

## Review Focus

1. **CFB and NFL games kicking off at the same instant.** The two scopes' T-1h polls fire together, and both would open the one Chrome profile. The second would fail with "profile busy" and falsely DM a failure. Expected: fetches are serialized by a bot-wide lock. Pinned in Task 4, `test_concurrent_checks_never_open_chrome_at_once`.
2. **The owner's entry appears twice on the page.** The real page repeats the same entry in two Apollo blobs. Expected: deduped by entry id, with no "two entries" error. Pinned in Task 1, `test_a_repeated_copy_of_the_same_entry_is_not_a_second_entry`.
3. **A Monday-night check after CBS has rolled to next week.** Expected: `CbsWeekNotReady` becomes a readable failure DM, not a crash or silence. Pinned in Task 2, `test_failure_reason_for_a_week_cbs_is_not_showing`.
4. **The DM itself fails to send.** Expected: the failure is logged as `owner_dm_failed`, the poll job does not raise, and `pick_check_completed` records `dm_sent=False`. Pinned in Task 4, `test_a_failed_dm_is_logged_and_does_not_break_the_poll`.
5. **A stored game that is not on the CBS page** (removed from the board, or its team names don't resolve). Expected: reported as "couldn't match", never counted as a match. Pinned in Task 2, `test_a_game_missing_from_the_cbs_page_is_unmatched`.

---

## File Structure

- Create `src/pickem/ingest/cbs_entry.py`: parse the owner's entry from a board page into `EntryBoard`.
- Modify `src/pickem/ingest/cbs_fetch.py`: add `fetch_board_html` (fetch and verify the week, save nothing). `fetch_board` reuses it.
- Create `src/pickem/operations/pick_check.py`: comparison, message text, failure reasons, and the two log events. Pure except logging.
- Modify `src/pickem/cli.py`: add the `check-picks` command.
- Modify `src/pickem/discord_bot.py`: run the check after the T-1h poll, add the CBS lock, and add the injectable board fetch.
- Create `tests/cbs_entry_helpers.py`: synthetic board pages with an owner entry, shaped like the real page captured on 2026-09-24.
- Create `tests/test_cbs_entry.py`, `tests/test_pick_check.py`, `tests/test_pick_check_cli.py`. Extend `tests/test_cbs_fetch.py` and `tests/test_discord_bot.py`.
- Docs: `CLAUDE.md`, `docs/runbooks/verifying-from-logs.md`, `docs/runbooks/cbs-fetch.md`, `docs/runbooks/discord-pick-reminder.md`.

**Spec deviation:** the spec proposes a fixture trimmed from the real page. The real page also holds other entrants' data and is 1.2 MB. The tests instead build pages in code, the way `tests/cbs_fetch_helpers.py` already does, using the field names verified on the real page (`isMine`, `entryPicks`, `cbsSlotId`, `cbsItemId`, `cbsEventId`, `cbsTeamId`). Task 5 then checks the real, live page with `check-picks`.

---

### Task 1: Read the owner's picks from a CBS board page

**Files:**
- Create: `tests/cbs_entry_helpers.py`
- Create: `src/pickem/ingest/cbs_entry.py`
- Modify: `src/pickem/ingest/cbs_fetch.py` (add `fetch_board_html`; rewrite `fetch_board` to use it)
- Test: `tests/test_cbs_entry.py`, `tests/test_cbs_fetch.py`

**Interfaces:**
- Produces:
  - `pickem.ingest.cbs_entry.EntryBoard`: a frozen dataclass with `picks: dict[str, Side | None]` (every resolved game on the page, keyed by `game_id`, `None` = no pick entered) and `unresolved: tuple[str, ...]` (`"Away at Home"` for page events whose teams did not resolve).
  - `parse_entry_picks(html: str, *, resolver: TeamResolver, season: int, pool_week: int) -> EntryBoard`, which raises `CbsParseError`.
  - `pickem.ingest.cbs_fetch.fetch_board_html(session: PageSession, *, pool_url: str, pool_week: int) -> str`, which raises `CbsSessionExpired` or `CbsWeekNotReady`.
  - Test helpers: `ATL_GB` and `WAKE_LOU` (event dicts), `ATL`, `GB`, `WAKE`, `LOU` (CBS team ids), `entry(picks, *, entry_id="ENTRY", mine=True)`, and `entry_page(*entries, events=(ATL_GB, WAKE_LOU), pool_week=4)`.

- [ ] **Step 1: Write the test helper**

`tests/cbs_entry_helpers.py`:

```python
"""Synthetic CBS board pages that carry the owner's entry.

Field names match the logged-in board fetched on 2026-09-24: each event has a
`cbsEventId` and teams with a `cbsTeamId`; the owner's entry is the one marked
`isMine`, and each of its `entryPicks` names the game (`cbsSlotId`) and the
team picked (`cbsItemId`).
"""

from __future__ import annotations

from cbs_fetch_helpers import _blob, pool_page

ATL, GB = 405, 414
WAKE, LOU = 668, 669
# 2026-09-25 00:15 UTC, Thursday 8:15 PM Eastern.
KICKOFF_MS = 1790295300000


def event(
    cbs_id: int, *, sport: str, away: str, home: str, away_id: int, home_id: int
) -> dict:
    def team(name: str, team_id: int) -> dict:
        return {"__typename": "Team", "cbsTeamId": team_id, "mediumName": name}

    return {
        "__typename": "Event",
        "id": f"event-{cbs_id}=",
        "cbsEventId": cbs_id,
        "sportType": sport,
        "startsAt": KICKOFF_MS,
        "homeTeamSpread": -6.5,
        "awayTeam": team(away, away_id),
        "homeTeam": team(home, home_id),
    }


ATL_GB = event(50029231, sport="NFL", away="Atlanta", home="Green Bay", away_id=ATL, home_id=GB)
WAKE_LOU = event(
    50027863, sport="NCAAF", away="Wake Forest", home="Louisville", away_id=WAKE, home_id=LOU
)


def entry(picks: dict[int, int], *, entry_id: str = "ENTRY", mine: bool = True) -> dict:
    """An entry whose picks map CBS event id -> CBS team id picked."""
    return {
        "__typename": "FootballPickemEntry",
        "id": entry_id,
        "isMine": mine,
        "entryPicks": [
            {"__typename": "EntryPick", "cbsSlotId": slot, "cbsItemId": item}
            for slot, item in picks.items()
        ],
    }


def entry_page(
    *entries: dict, events: tuple[dict, ...] = (ATL_GB, WAKE_LOU), pool_week: int = 4
) -> str:
    board = _blob({"events": list(events)})
    owners = "".join(_blob({"commonEntry": one}) for one in entries)
    page = pool_page(weeks=pool_week, shown=pool_week, current=pool_week)
    return page.replace("</body>", board + owners + "</body>")
```

- [ ] **Step 2: Write the failing parser tests**

`tests/test_cbs_entry.py`:

```python
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
    board = parse(entry_page(entry({ATL_GB["cbsEventId"]: GB}, entry_id="THEM", mine=False),
                             entry({ATL_GB["cbsEventId"]: ATL})))

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
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_cbs_entry.py -q`
Expected: FAIL. Collection errors with `ModuleNotFoundError: No module named 'pickem.ingest.cbs_entry'`.

- [ ] **Step 4: Implement the parser**

`src/pickem/ingest/cbs_entry.py`:

```python
"""Read the owner's own entered picks from a CBS board page.

The board page embeds the viewer's entry beside the events: the entry marked
`isMine` lists `entryPicks`, each naming a game by `cbsSlotId` (the event's
`cbsEventId`) and the team picked by `cbsItemId` (that team's `cbsTeamId`).
An entry with nothing picked yet has `entryPicks: []`, so a page saved on
Tuesday, before picking, reads as every game unpicked.

Games are keyed by the same `game_id` the board ingest stores, derived the same
way, so a pick lines up with the model's recommendation for that game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_html import _SPORT_TYPES, _events, _payloads, _team_name
from pickem.models import Side
from pickem.operations.results_import import ResultsImportError, league_week
from pickem.resolve.matchup import resolve_matchup
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

_LEAGUES = {code: sport for sport, code in _SPORT_TYPES.items()}


@dataclass(frozen=True)
class EntryBoard:
    """The owner's entry as CBS holds it right now."""

    # Every game on the page whose teams resolved; None means nothing is picked.
    picks: dict[str, Side | None]
    # "Away at Home" for page events that could not be tied to a stored game.
    unresolved: tuple[str, ...]


def _walk(node: Any, found: dict[Any, dict]) -> None:
    if isinstance(node, dict):
        if node.get("isMine") is True and "entryPicks" in node:
            # The page repeats the same entry in more than one blob.
            found.setdefault(node.get("id"), node)
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found)


def _my_entry(blobs: list[Any]) -> dict:
    found: dict[Any, dict] = {}
    for blob in blobs:
        _walk(blob, found)
    if not found:
        raise CbsParseError(
            "the CBS page carries no entry marked as yours — the login may belong to "
            "another account, or CBS changed the page"
        )
    if len(found) > 1:
        raise CbsParseError(
            f"the CBS page marks {len(found)} entries as yours ({sorted(map(str, found))}); "
            "expected one"
        )
    return next(iter(found.values()))


def _side(event: dict, item: Any) -> Side:
    if item == (event.get("homeTeam") or {}).get("cbsTeamId"):
        return Side.HOME
    if item == (event.get("awayTeam") or {}).get("cbsTeamId"):
        return Side.AWAY
    raise CbsParseError(
        f"your pick on CBS event {event.get('cbsEventId')} names team {item}, "
        "which is neither side of that game"
    )


def parse_entry_picks(
    html: str, *, resolver: TeamResolver, season: int, pool_week: int
) -> EntryBoard:
    """Return the owner's pick, or its absence, for every game on the page."""
    blobs = _payloads(html)
    found: list[dict] = []
    for blob in blobs:
        _events(blob, found)
    events: dict[int, dict] = {}
    for event in found:
        cbs_id = event.get("cbsEventId")
        if isinstance(cbs_id, int) and not isinstance(cbs_id, bool):
            events.setdefault(cbs_id, event)
    if not events:
        raise CbsParseError("no CBS event payload found on the board page")
    mine = _my_entry(blobs)

    game_ids: dict[int, str] = {}
    unresolved: list[str] = []
    for cbs_id, event in events.items():
        away = _team_name(event.get("awayTeam"))
        home = _team_name(event.get("homeTeam"))
        sport = _LEAGUES.get(event.get("sportType"))
        if away is None or home is None or sport is None:
            unresolved.append(f"{away} at {home}")
            continue
        try:
            matchup = resolve_matchup(
                resolver=resolver,
                sport=sport,
                season=season,
                week=league_week(sport, pool_week),
                away_name=away,
                home_name=home,
            )
        except (UnknownTeamError, ResultsImportError, ValueError):
            unresolved.append(f"{away} at {home}")
            continue
        game_ids[cbs_id] = matchup.game_id

    picks: dict[str, Side | None] = {game_id: None for game_id in game_ids.values()}
    for pick in mine.get("entryPicks") or []:
        slot = pick.get("cbsSlotId")
        event = events.get(slot)
        if event is None:
            raise CbsParseError(
                f"your pick on CBS event {slot} names a game that is not on the page"
            )
        side = _side(event, pick.get("cbsItemId"))
        if slot in game_ids:
            picks[game_ids[slot]] = side

    logger.bind(
        event="cbs_entry_parsed",
        games=len(picks),
        picked=sum(1 for side in picks.values() if side is not None),
        unresolved=len(unresolved),
    ).debug(f"read {len(picks)} games from the owner's CBS entry")
    return EntryBoard(picks=picks, unresolved=tuple(unresolved))


__all__ = ["EntryBoard", "parse_entry_picks"]
```

- [ ] **Step 5: Run the parser tests and confirm they pass**

Run: `uv run pytest tests/test_cbs_entry.py -q`
Expected: all 11 pass. If `import pickem.operations.results_import` from `ingest` causes a circular import, move `league_week` and `ResultsImportError` into `src/pickem/operations/pool_weeks.py`, re-export both from `results_import`, and import from `pool_weeks` here.

- [ ] **Step 6: Write the failing fetch tests**

Append to `tests/test_cbs_fetch.py`. Use the file's existing imports. Add `fetch_board_html` to the `pickem.ingest.cbs_fetch` import block. `StubSession`, `pool_page`, `POOL`, `JOIN_PAGE`, `FetchedPage`, `asyncio`, and `pytest` are already imported there. Add any that are missing.

```python
def test_board_html_is_returned_without_saving_anything(tmp_path):
    page = pool_page(weeks=4, shown=4, current=4)
    session = StubSession({POOL: page})

    html = asyncio.run(fetch_board_html(session, pool_url=POOL, pool_week=4))

    assert html == page
    assert list(tmp_path.iterdir()) == []


def test_board_html_for_a_week_cbs_is_not_showing_is_refused():
    session = StubSession({POOL: pool_page(weeks=5, shown=5, current=5)})

    with pytest.raises(CbsWeekNotReady):
        asyncio.run(fetch_board_html(session, pool_url=POOL, pool_week=4))


def test_board_html_when_logged_out_is_refused():
    session = StubSession({POOL: FetchedPage(f"{POOL}/join", JOIN_PAGE)})

    with pytest.raises(CbsSessionExpired):
        asyncio.run(fetch_board_html(session, pool_url=POOL, pool_week=4))
```

- [ ] **Step 7: Run and confirm they fail**

Run: `uv run pytest tests/test_cbs_fetch.py -q -k board_html`
Expected: FAIL with `ImportError: cannot import name 'fetch_board_html'`.

- [ ] **Step 8: Implement `fetch_board_html` and reuse it in `fetch_board`**

In `src/pickem/ingest/cbs_fetch.py`, replace `fetch_board` with:

```python
async def fetch_board_html(session: PageSession, *, pool_url: str, pool_week: int) -> str:
    """The board for ``pool_week`` as page text, saved nowhere.

    The pick check reads it fresh before each kickoff; a saved copy would be
    the Tuesday page, taken before any picks were entered.
    """
    logger.bind(event="cbs_fetch_started", page="board", pool_week=pool_week).info(
        f"fetching the CBS board for pool week {pool_week}"
    )
    page = await _fetch_logged_in(session, pool_url)
    require_week(page.html, pool_week)
    return page.html


async def fetch_board(
    session: PageSession, *, pool_url: str, pool_week: int, out: Path, force: bool = False
) -> SavedPage:
    """Save the board for ``pool_week``; CBS must be showing that week."""
    _refuse_overwrite(out, force)
    html = await fetch_board_html(session, pool_url=pool_url, pool_week=pool_week)
    return _save(out, html, pool_week, pool_periods(html)[pool_week])
```

- [ ] **Step 9: Run the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass, including the existing `fetch_board` tests, and ruff clean.

- [ ] **Step 10: Commit**

```bash
git add tests/cbs_entry_helpers.py tests/test_cbs_entry.py tests/test_cbs_fetch.py \
  src/pickem/ingest/cbs_entry.py src/pickem/ingest/cbs_fetch.py
git commit -m "feat: read the owner's entered picks from the CBS board page"
```

---

### Task 2: Compare picks and word the messages

**Files:**
- Create: `src/pickem/operations/pick_check.py`
- Test: `tests/test_pick_check.py`

**Interfaces:**
- Consumes: `EntryBoard` from Task 1. `Edge`, `Game`, `Side`, `Sport` from `pickem.models`. `TeamResolver`. The `CbsFetchError` subclasses from `cbs_fetch`. `CbsParseError`.
- Produces:
  - `Outcome` (StrEnum): `MATCH="match"`, `DIFFERENT_SIDE="different_side"`, `NO_PICK="no_pick"`, `UNMATCHED="unmatched"`.
  - `GameCheck`: a frozen dataclass with `game: Game`, `outcome: Outcome`, `model_side: Side`, `cbs_side: Side | None`, and `league_spread: float` (home-perspective, favourite negative).
  - `compare_picks(board: EntryBoard, edges: Iterable[Edge], games: Iterable[Game]) -> tuple[GameCheck, ...]`, ordered by kickoff then `game_id`. It skips games with no edge.
  - `format_pick_check(checks, *, kickoff_utc: datetime, stale: bool, pool_url: str, resolver: TeamResolver) -> tuple[str, str] | None` returns `(title, body)`, or `None` when nothing needs saying.
  - `failure_reason(error: BaseException) -> str`.
  - `format_failure(reason: str, games: Sequence[Game], *, kickoff_utc: datetime, resolver: TeamResolver) -> tuple[str, str]`.
  - `format_check_line(check: GameCheck, resolver: TeamResolver) -> str`, used by the CLI.
  - `log_pick_check(*, sport: Sport, season: int, week: int, kickoff_utc: datetime | None, checks: Sequence[GameCheck], dm_sent: bool, stale: bool) -> None` emits `pick_check_completed`.
  - `log_pick_check_failed(*, sport: Sport, season: int, week: int, kickoff_utc: datetime | None, reason: str, error: BaseException, unchecked: int) -> None` emits `pick_check_failed`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pick_check.py`:

```python
from datetime import UTC, datetime

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_entry import EntryBoard
from pickem.ingest.cbs_fetch import CbsProfileBusy, CbsSessionExpired, CbsWeekNotReady
from pickem.models import Edge, Game, Side, Sport, Tier
from pickem.operations.pick_check import (
    GameCheck,
    Outcome,
    compare_picks,
    failure_reason,
    format_check_line,
    format_failure,
    format_pick_check,
    log_pick_check,
    log_pick_check_failed,
)
from pickem.resolve.resolver import TeamResolver

KICKOFF = datetime(2026, 9, 25, 0, 15, tzinfo=UTC)  # Thu 8:15 PM Eastern
POOL = "https://cbs.test/pool"
RESOLVER = TeamResolver.default()


def game(away: str, home: str, kickoff: datetime = KICKOFF) -> Game:
    return Game(
        game_id=f"nfl-2026-03-{away}-at-{home}",
        sport=Sport.NFL,
        season=2026,
        week=3,
        kickoff_utc=kickoff,
        home_team_id=home,
        away_team_id=away,
    )


def edge(g: Game, side: Side, spread: float = -6.5) -> Edge:
    return Edge(
        game_id=g.game_id,
        side=side,
        delta=1.0,
        tier=Tier.LEAN,
        league_spread=spread,
        market_spread=spread - 1,
        rationale="test",
    )


ATL_GB = game("ATL", "GB")
DAL_PHI = game("DAL", "PHI")


def test_each_outcome_is_classified():
    board = EntryBoard(picks={ATL_GB.game_id: Side.HOME, DAL_PHI.game_id: None}, unresolved=())
    other = game("BUF", "MIA")
    checks = compare_picks(
        board,
        [edge(ATL_GB, Side.HOME), edge(DAL_PHI, Side.AWAY), edge(other, Side.HOME)],
        [ATL_GB, DAL_PHI, other],
    )

    assert {c.game.game_id: c.outcome for c in checks} == {
        ATL_GB.game_id: Outcome.MATCH,
        DAL_PHI.game_id: Outcome.NO_PICK,
        other.game_id: Outcome.UNMATCHED,
    }


def test_a_different_side_is_flagged_with_both_sides():
    board = EntryBoard(picks={ATL_GB.game_id: Side.AWAY}, unresolved=())

    (check,) = compare_picks(board, [edge(ATL_GB, Side.HOME)], [ATL_GB])

    assert (check.outcome, check.cbs_side, check.model_side) == (
        Outcome.DIFFERENT_SIDE, Side.AWAY, Side.HOME
    )


def test_a_game_missing_from_the_cbs_page_is_unmatched():
    board = EntryBoard(picks={}, unresolved=("Nowhere at Green Bay",))

    (check,) = compare_picks(board, [edge(ATL_GB, Side.HOME)], [ATL_GB])

    assert check.outcome is Outcome.UNMATCHED


def test_only_the_games_passed_in_are_checked():
    board = EntryBoard(picks={ATL_GB.game_id: Side.HOME, DAL_PHI.game_id: None}, unresolved=())

    checks = compare_picks(board, [edge(ATL_GB, Side.HOME), edge(DAL_PHI, Side.HOME)], [ATL_GB])

    assert [c.game.game_id for c in checks] == [ATL_GB.game_id]


def test_a_game_the_model_has_no_pick_for_is_skipped():
    board = EntryBoard(picks={ATL_GB.game_id: None}, unresolved=())

    assert compare_picks(board, [], [ATL_GB]) == ()


def _check(outcome: Outcome, cbs: Side | None, model: Side = Side.HOME) -> GameCheck:
    return GameCheck(ATL_GB, outcome, model, cbs, -6.5)


def test_all_matching_and_fresh_says_nothing():
    checks = (_check(Outcome.MATCH, Side.HOME),)

    assert format_pick_check(
        checks, kickoff_utc=KICKOFF, stale=False, pool_url=POOL, resolver=RESOLVER
    ) is None


def test_a_mismatch_names_both_picks_with_their_spreads_and_the_pool_link():
    title, body = format_pick_check(
        (_check(Outcome.DIFFERENT_SIDE, Side.AWAY),),
        kickoff_utc=KICKOFF, stale=False, pool_url=POOL, resolver=RESOLVER,
    )

    assert title == "⚠️ Pick check — 1 game kicks off at Thu 8:15 PM"
    atl = RESOLVER.display_name("ATL", Sport.NFL)
    gb = RESOLVER.display_name("GB", Sport.NFL)
    assert f"{atl} at {gb}: CBS has **{atl} +6.5**, model says **{gb} -6.5**" in body
    assert body.endswith(f"Fix on CBS before kickoff: {POOL}")


def test_a_missing_pick_says_so():
    _, body = format_pick_check(
        (_check(Outcome.NO_PICK, None),),
        kickoff_utc=KICKOFF, stale=False, pool_url=POOL, resolver=RESOLVER,
    )

    assert "no pick entered on CBS" in body


def test_unmatched_games_are_listed_as_not_checked():
    _, body = format_pick_check(
        (_check(Outcome.UNMATCHED, None),),
        kickoff_utc=KICKOFF, stale=False, pool_url=POOL, resolver=RESOLVER,
    )

    assert "Couldn't find on CBS, so not checked:" in body
    assert "Fix on CBS" not in body


def test_a_stale_model_pick_is_reported_even_when_everything_matches():
    title, body = format_pick_check(
        (_check(Outcome.MATCH, Side.HOME),),
        kickoff_utc=KICKOFF, stale=True, pool_url=POOL, resolver=RESOLVER,
    )

    assert "could not be refreshed" in body
    assert "All 1 pick matches" in body


def test_a_pick_em_line_reads_pk():
    check = GameCheck(ATL_GB, Outcome.DIFFERENT_SIDE, Side.HOME, Side.AWAY, 0.0)

    _, body = format_pick_check(
        (check,), kickoff_utc=KICKOFF, stale=False, pool_url=POOL, resolver=RESOLVER
    )

    assert " PK**" in body


def test_failure_reason_for_an_expired_login_points_at_the_fix():
    assert "cbs-login.sh" in failure_reason(CbsSessionExpired("gone"))


def test_failure_reason_for_an_open_login_window():
    assert "login window is open" in failure_reason(CbsProfileBusy("busy"))


def test_failure_reason_for_a_week_cbs_is_not_showing():
    reason = failure_reason(CbsWeekNotReady("CBS is showing pool week 5, not 4"))

    assert "showing pool week 5" in reason


def test_failure_reason_for_an_unreadable_page():
    assert "couldn't read your picks" in failure_reason(CbsParseError("no entry"))


def test_failure_reason_for_anything_else_names_the_error_type():
    assert "TimeoutError" in failure_reason(TimeoutError("slow"))


def test_failure_message_lists_the_unchecked_games():
    title, body = format_failure(
        "CBS login has expired", [ATL_GB], kickoff_utc=KICKOFF, resolver=RESOLVER
    )

    assert title == "⚠️ Pick check didn't run — Thu 8:15 PM kickoff"
    assert body.startswith("CBS login has expired")
    assert f"Not checked: {RESOLVER.display_name('ATL', Sport.NFL)} at" in body


def test_cli_line_marks_each_outcome():
    assert format_check_line(_check(Outcome.MATCH, Side.HOME), RESOLVER).startswith("✅")
    assert format_check_line(_check(Outcome.DIFFERENT_SIDE, Side.AWAY), RESOLVER).startswith("❌")
    assert "no pick on CBS" in format_check_line(_check(Outcome.NO_PICK, None), RESOLVER)
    assert "not found on CBS" in format_check_line(_check(Outcome.UNMATCHED, None), RESOLVER)


def test_completed_log_counts_each_outcome(records):
    log_pick_check(
        sport=Sport.NFL, season=2026, week=3, kickoff_utc=KICKOFF,
        checks=(_check(Outcome.MATCH, Side.HOME), _check(Outcome.NO_PICK, None)),
        dm_sent=True, stale=False,
    )

    (row,) = [r for r in records if r["extra"].get("event") == "pick_check_completed"]
    expected = {
        "checked": 2, "matched": 1, "no_pick": 1, "different_side": 0,
        "unmatched": 0, "dm_sent": True, "stale": False,
    }
    for key, value in expected.items():
        assert row["extra"][key] == value, key
    assert row["level"].name == "WARNING"


def test_failed_log_names_the_reason(records):
    log_pick_check_failed(
        sport=Sport.NFL, season=2026, week=3, kickoff_utc=KICKOFF,
        reason="CBS login has expired", error=CbsSessionExpired("gone"), unchecked=3,
    )

    (row,) = [r for r in records if r["extra"].get("event") == "pick_check_failed"]
    assert row["extra"]["reason"] == "CBS login has expired"
    assert row["extra"]["unchecked"] == 3
    assert row["extra"]["error_type"] == "CbsSessionExpired"
```

- [ ] **Step 2: Run and confirm they fail**

Run: `uv run pytest tests/test_pick_check.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.operations.pick_check'`.

- [ ] **Step 3: Implement**

`src/pickem/operations/pick_check.py`:

```python
"""Compare the owner's CBS picks with the model's, and word the result.

Pure apart from logging: the bot and the `check-picks` command supply the
parsed CBS entry, the model's edges and the games to check, and decide who
hears about it. A pick is entered by hand and the model can move after entry,
so this is the last look before each game locks.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from loguru import logger

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_entry import EntryBoard
from pickem.ingest.cbs_fetch import (
    CbsFetchError,
    CbsProfileBusy,
    CbsSessionExpired,
    CbsWeekNotReady,
)
from pickem.models import Edge, Game, Side, Sport
from pickem.resolve.resolver import TeamResolver

EASTERN = ZoneInfo("America/New_York")


class Outcome(StrEnum):
    MATCH = "match"
    DIFFERENT_SIDE = "different_side"
    NO_PICK = "no_pick"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class GameCheck:
    game: Game
    outcome: Outcome
    model_side: Side
    cbs_side: Side | None
    # Home-perspective, favourite negative: the board's frozen line.
    league_spread: float


_TO_FIX = (Outcome.DIFFERENT_SIDE, Outcome.NO_PICK)


def compare_picks(
    board: EntryBoard, edges: Iterable[Edge], games: Iterable[Game]
) -> tuple[GameCheck, ...]:
    """One outcome per game in ``games`` that the model has a pick for."""
    by_game = {edge.game_id: edge for edge in edges}
    checks: list[GameCheck] = []
    for game in sorted(games, key=lambda g: (g.kickoff_utc, g.game_id)):
        edge = by_game.get(game.game_id)
        if edge is None:
            continue
        if game.game_id not in board.picks:
            outcome, cbs_side = Outcome.UNMATCHED, None
        else:
            cbs_side = board.picks[game.game_id]
            if cbs_side is None:
                outcome = Outcome.NO_PICK
            elif cbs_side is edge.side:
                outcome = Outcome.MATCH
            else:
                outcome = Outcome.DIFFERENT_SIDE
        checks.append(GameCheck(game, outcome, edge.side, cbs_side, edge.league_spread))
    return tuple(checks)


def _when(kickoff_utc: datetime) -> str:
    return kickoff_utc.astimezone(EASTERN).strftime("%a %-I:%M %p")


def _matchup(game: Game, resolver: TeamResolver) -> str:
    away = resolver.display_name(game.away_team_id, game.sport)
    home = resolver.display_name(game.home_team_id, game.sport)
    return f"{away} at {home}"


def _pick(check: GameCheck, side: Side, resolver: TeamResolver) -> str:
    game = check.game
    team_id = game.home_team_id if side is Side.HOME else game.away_team_id
    spread = check.league_spread if side is Side.HOME else -check.league_spread
    line = "PK" if spread == 0 else f"{spread:+g}"
    return f"{resolver.display_name(team_id, game.sport)} {line}"


def format_pick_check(
    checks: Sequence[GameCheck],
    *,
    kickoff_utc: datetime,
    stale: bool,
    pool_url: str,
    resolver: TeamResolver,
) -> tuple[str, str] | None:
    """The DM for one kickoff's check, or None when there is nothing to say."""
    fix = [check for check in checks if check.outcome in _TO_FIX]
    unmatched = [check for check in checks if check.outcome is Outcome.UNMATCHED]
    if not fix and not unmatched and not stale:
        return None

    count = len(checks)
    title = (
        f"⚠️ Pick check — {count} game{'' if count == 1 else 's'} "
        f"kick{'s' if count == 1 else ''} off at {_when(kickoff_utc)}"
    )
    lines: list[str] = []
    for check in fix:
        model = _pick(check, check.model_side, resolver)
        cbs = (
            "no pick entered on CBS"
            if check.cbs_side is None
            else f"CBS has **{_pick(check, check.cbs_side, resolver)}**"
        )
        lines.append(f"{_matchup(check.game, resolver)}: {cbs}, model says **{model}**")
    if unmatched:
        lines.append(
            "Couldn't find on CBS, so not checked: "
            + ", ".join(_matchup(check.game, resolver) for check in unmatched)
        )
    if stale:
        lines.append(
            "The model's pick could not be refreshed an hour before kickoff, so this "
            "compares against the last stored pick."
        )
        if not fix and not unmatched:
            lines.append(f"All {count} pick{' matches' if count == 1 else 's match'}.")
    if fix:
        lines.append(f"Fix on CBS before kickoff: {pool_url}")
    return title, "\n".join(lines)


def failure_reason(error: BaseException) -> str:
    """One plain sentence saying why the check could not run."""
    if isinstance(error, CbsSessionExpired):
        return (
            "CBS login has expired — run scripts/cbs-login.sh "
            '(docs/runbooks/cbs-fetch.md, "Log in again")'
        )
    if isinstance(error, CbsProfileBusy):
        return "The CBS login window is open — close it so the check can use the profile"
    if isinstance(error, CbsWeekNotReady):
        return f"CBS isn't showing this pool week ({error})"
    if isinstance(error, CbsParseError | CbsFetchError):
        return f"couldn't read your picks from the CBS page ({error})"
    return f"CBS fetch failed ({type(error).__name__}: {error})"


def format_failure(
    reason: str, games: Sequence[Game], *, kickoff_utc: datetime, resolver: TeamResolver
) -> tuple[str, str]:
    title = f"⚠️ Pick check didn't run — {_when(kickoff_utc)} kickoff"
    unchecked = ", ".join(_matchup(game, resolver) for game in games)
    return title, f"{reason}\nNot checked: {unchecked}"


_MARKS = {
    Outcome.MATCH: "✅",
    Outcome.DIFFERENT_SIDE: "❌",
    Outcome.NO_PICK: "⚠️",
    Outcome.UNMATCHED: "❓",
}


def format_check_line(check: GameCheck, resolver: TeamResolver) -> str:
    model = _pick(check, check.model_side, resolver)
    if check.outcome is Outcome.MATCH:
        detail = f"CBS and model: {model}"
    elif check.outcome is Outcome.DIFFERENT_SIDE:
        detail = f"CBS {_pick(check, check.cbs_side, resolver)} · model {model}"
    elif check.outcome is Outcome.NO_PICK:
        detail = f"no pick on CBS · model {model}"
    else:
        detail = f"not found on CBS · model {model}"
    return (
        f"{_MARKS[check.outcome]} {_when(check.game.kickoff_utc)}  "
        f"{_matchup(check.game, resolver)}: {detail}"
    )


def _scope(sport: Sport, season: int, week: int, kickoff_utc: datetime | None) -> dict:
    return {
        "sport": sport.value,
        "season": season,
        "week": week,
        "kickoff": kickoff_utc.isoformat() if kickoff_utc else None,
    }


def log_pick_check(
    *,
    sport: Sport,
    season: int,
    week: int,
    kickoff_utc: datetime | None,
    checks: Sequence[GameCheck],
    dm_sent: bool,
    stale: bool,
) -> None:
    counts = Counter(check.outcome for check in checks)
    needs_attention = any(counts[o] for o in (*_TO_FIX, Outcome.UNMATCHED))
    logger.bind(
        event="pick_check_completed",
        **_scope(sport, season, week, kickoff_utc),
        checked=len(checks),
        matched=counts[Outcome.MATCH],
        different_side=counts[Outcome.DIFFERENT_SIDE],
        no_pick=counts[Outcome.NO_PICK],
        unmatched=counts[Outcome.UNMATCHED],
        stale=stale,
        dm_sent=dm_sent,
    ).log(
        "WARNING" if needs_attention else "INFO",
        f"pick check {sport.value} wk{week}: {len(checks)} checked, "
        f"{counts[Outcome.MATCH]} match, {counts[Outcome.DIFFERENT_SIDE]} differ, "
        f"{counts[Outcome.NO_PICK]} unpicked, {counts[Outcome.UNMATCHED]} unmatched"
        f"{' (stale model pick)' if stale else ''}",
    )


def log_pick_check_failed(
    *,
    sport: Sport,
    season: int,
    week: int,
    kickoff_utc: datetime | None,
    reason: str,
    error: BaseException,
    unchecked: int,
) -> None:
    logger.bind(
        event="pick_check_failed",
        **_scope(sport, season, week, kickoff_utc),
        reason=reason,
        error_type=type(error).__name__,
        unchecked=unchecked,
    ).error(f"pick check {sport.value} wk{week} did not run: {reason}")


__all__ = [
    "GameCheck",
    "Outcome",
    "compare_picks",
    "failure_reason",
    "format_check_line",
    "format_failure",
    "format_pick_check",
    "log_pick_check",
    "log_pick_check_failed",
]
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_pick_check.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add src/pickem/operations/pick_check.py tests/test_pick_check.py
git commit -m "feat: compare CBS picks with the model and word the pick-check messages"
```

---

### Task 3: `pickem check-picks` command

**Files:**
- Modify: `src/pickem/cli.py` (imports and a new command placed after `fetch-cbs`)
- Test: `tests/test_pick_check_cli.py`

**Interfaces:**
- Consumes: `fetch_board_html` and `parse_entry_picks` (Task 1). `compare_picks`, `format_check_line`, `log_pick_check`, and `Outcome` (Task 2). The existing `_run_cbs`, `open_cbs_session`, and `generate_recommendations` in `cli.py`.
- Produces: the CLI command `check-picks --season S --pool-week N [--db PATH]`. Exit 0 once the comparison completes, whatever the outcomes. Exit 1 on a fetch or parse failure, with the reason as the last stderr line.

- [ ] **Step 1: Write the failing tests**

`tests/test_pick_check_cli.py`:

```python
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from cbs_entry_helpers import ATL, ATL_GB, entry, entry_page
from cbs_fetch_helpers import JOIN_PAGE, POOL, StubSession
from typer.testing import CliRunner

from pickem import config
from pickem.cli import app
from pickem.ingest.cbs_fetch import FetchedPage
from pickem.models import Edge, Game, LeagueLine, Side, Sport, Tier
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import Store

runner = CliRunner()
FUTURE = datetime(2099, 9, 25, 0, 15, tzinfo=UTC)
GAME = Game(
    game_id="nfl-2026-03-ATL-at-GB", sport=Sport.NFL, season=2026, week=3,
    kickoff_utc=FUTURE, home_team_id="GB", away_team_id="ATL",
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pickem.duckdb"
    with Store(path) as store:
        store.init_schema()
        store.upsert_games([GAME])
        store.upsert_league_lines([LeagueLine(
            game_id=GAME.game_id, season=2026, week=3, spread_home=-6.5, posted_at=FUTURE,
        )])
    return path


@pytest.fixture
def cbs(monkeypatch):
    monkeypatch.setattr(config, "CBS_POOL_URL", POOL)
    pages: dict = {}

    @asynccontextmanager
    async def fake_open(*, profile, chrome):
        yield StubSession(pages)

    monkeypatch.setattr("pickem.cli.open_cbs_session", fake_open)
    return pages


@pytest.fixture
def model(monkeypatch):
    """The model picks GB (home) on the one NFL game; CFB has no stored week."""
    def generate(db, sport, season, week, now, **_):
        edges = (
            (Edge(game_id=GAME.game_id, side=Side.HOME, delta=1.0, tier=Tier.LEAN,
                  league_spread=-6.5, market_spread=-7.5, rationale="t"),)
            if sport is Sport.NFL else ()
        )
        return RecommendationSnapshot(sport, season, week, now, edges)

    monkeypatch.setattr("pickem.cli.generate_recommendations", generate)


def invoke(db):
    return runner.invoke(
        app, ["check-picks", "--season", "2026", "--pool-week", "4", "--db", str(db)]
    )


def test_a_differing_pick_is_marked_and_summarised(db, cbs, model):
    cbs[POOL] = entry_page(entry({ATL_GB["cbsEventId"]: ATL}), events=(ATL_GB,))

    result = invoke(db)

    assert result.exit_code == 0, result.output
    assert "❌" in result.output
    assert "1 checked: 0 match, 1 differ, 0 unpicked, 0 unmatched" in result.output


def test_a_matching_pick_is_marked(db, cbs, model):
    cbs[POOL] = entry_page(entry({ATL_GB["cbsEventId"]: 414}), events=(ATL_GB,))

    result = invoke(db)

    assert result.exit_code == 0, result.output
    assert "✅" in result.output


def test_an_expired_login_exits_1_with_the_hint(db, cbs, model):
    cbs[POOL] = FetchedPage(f"{POOL}/join", JOIN_PAGE)

    result = invoke(db)

    assert result.exit_code == 1
    assert "cbs-login.sh" in result.output


def test_an_unreadable_entry_exits_1(db, cbs, model):
    cbs[POOL] = entry_page(entry({}, mine=False), events=(ATL_GB,))

    result = invoke(db)

    assert result.exit_code == 1
    assert "no entry marked as yours" in result.output
```

- [ ] **Step 2: Run and confirm they fail**

Run: `uv run pytest tests/test_pick_check_cli.py -q`
Expected: FAIL. Typer reports `No such command 'check-picks'`, exit code 2.

- [ ] **Step 3: Implement the command**

In `src/pickem/cli.py`, add `fetch_board_html` to the `from pickem.ingest.cbs_fetch import (...)` block, and add these imports alongside the others:

```python
from pickem.ingest.cbs_entry import parse_entry_picks
from pickem.operations.pick_check import (
    Outcome,
    compare_picks,
    format_check_line,
    log_pick_check,
)
from pickem.operations.results_import import league_week
```

(`ResultsImportError, import_results` are already imported from `results_import`. Extend that line instead of adding a second one.)

Add this after `fetch_cbs_cmd`:

```python
@app.command("check-picks")
def check_picks_cmd(
    season: int = typer.Option(...),
    pool_week: int = typer.Option(..., help="Pool week, not league week: CFB N + NFL N-1"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Compare the picks entered on CBS with the model's, for games not yet started."""
    with run_context("cli:check-picks", season=season, pool_week=pool_week, db=str(db)):

        async def work():
            async with open_cbs_session(
                profile=config.CBS_CHROME_PROFILE, chrome=config.CHROME_BINARY
            ) as session:
                html = await fetch_board_html(
                    session, pool_url=config.CBS_POOL_URL, pool_week=pool_week
                )
            # Parsed inside the CBS run so a page it cannot read fails the same
            # way a fetch does: one reason line, exit 1.
            return parse_entry_picks(
                html, resolver=TeamResolver.default(), season=season, pool_week=pool_week
            )

        board = _run_cbs(work, page="board", pool_week=pool_week)
        resolver = TeamResolver.default()
        now = datetime.now(UTC)
        totals: dict[Outcome, int] = dict.fromkeys(Outcome, 0)
        leagues = [Sport.CFB] + ([Sport.NFL] if pool_week > 1 else [])
        for sport in leagues:
            week = league_week(sport, pool_week)
            with Store(db, read_only=True) as store:
                games = [g for g in store.load_week(sport, season, week).games
                         if g.kickoff_utc > now]
            if not games:
                continue
            snapshot = generate_recommendations(
                db, sport, season, week, now, pending_as_of=now
            )
            checks = compare_picks(board, snapshot.edges, games)
            for check in checks:
                typer.echo(format_check_line(check, resolver))
                totals[check.outcome] += 1
            log_pick_check(
                sport=sport, season=season, week=week, kickoff_utc=None,
                checks=checks, dm_sent=False, stale=False,
            )
        typer.echo(
            f"{sum(totals.values())} checked: {totals[Outcome.MATCH]} match, "
            f"{totals[Outcome.DIFFERENT_SIDE]} differ, {totals[Outcome.NO_PICK]} unpicked, "
            f"{totals[Outcome.UNMATCHED]} unmatched"
        )
```

- [ ] **Step 4: Run and confirm the tests pass**

Run: `uv run pytest tests/test_pick_check_cli.py -q`
Expected: all 4 pass. If `Store(db, read_only=True)` fails because a store was never initialised, that is a real gap. The fixture initialises it, so a failure means the path is wrong.

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add src/pickem/cli.py tests/test_pick_check_cli.py
git commit -m "feat: add check-picks to compare CBS picks with the model by hand"
```

---

### Task 4: Run the check after the T-1h kickoff poll

**Files:**
- Modify: `src/pickem/discord_bot.py`
- Test: `tests/test_discord_bot.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1–2. `build_message_embed(title, message)` from `pickem.notify.discord_dm`. `open_session` and `fetch_board_html` from `pickem.ingest.cbs_fetch`.
- Produces:
  - `PickemBot.__init__` gains a keyword `fetch_board_html: Callable[[int], Awaitable[str]] | None = None`, which takes a pool week and returns page text. `None` uses headless Chrome.
  - `PickemBot._check_picks(scope: MonitorScope, instant: PollInstant, results: tuple[tuple[MonitorScope, RefreshResult], ...]) -> None`, which never raises.
  - `_run_kickoff_poll` calls `_check_picks` when `instant.offset_hours == min(settings.poll_offsets_hours)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discord_bot.py`. Add these imports at the top of the file, merged into its existing import blocks:

```python
import asyncio

from cbs_entry_helpers import ATL, ATL_GB, GB, KICKOFF_MS, entry, entry_page

from pickem.ingest.cbs_fetch import CbsSessionExpired
```

Tests:

```python
CHECK_KICKOFF = datetime.fromtimestamp(KICKOFF_MS / 1000, tz=UTC)
ATL_AT_GB = Game(
    game_id="nfl-2026-03-ATL-at-GB", sport=Sport.NFL, season=2026, week=3,
    kickoff_utc=CHECK_KICKOFF, home_team_id="GB", away_team_id="ATL",
)
CHECK_SCOPE = MonitorScope(Sport.NFL, 2026, 3)


def _seed_check_game(settings) -> None:
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games([ATL_AT_GB])
        store.upsert_league_lines([LeagueLine(
            game_id=ATL_AT_GB.game_id, season=2026, week=3,
            spread_home=-6.5, posted_at=CHECK_KICKOFF,
        )])


def _gb_snapshot() -> RecommendationSnapshot:
    return RecommendationSnapshot(
        sport=Sport.NFL, season=2026, week=3, generated_at=CHECK_KICKOFF,
        edges=(Edge(game_id=ATL_AT_GB.game_id, side=Side.HOME, delta=1.0, tier=Tier.LEAN,
                    league_spread=-6.5, market_spread=-7.5, rationale="t"),),
    )


def _instant(offset: float) -> PollInstant:
    return PollInstant(
        at=CHECK_KICKOFF - timedelta(hours=offset), offset_hours=offset,
        kickoff_utc=CHECK_KICKOFF,
    )


def _check_bot(settings, *, picked: int | None = ATL, error: Exception | None = None,
               result: RefreshResult | None = None):
    _seed_check_game(settings)
    fetched: list[int] = []
    dms: list = []

    async def fetch(pool_week: int) -> str:
        fetched.append(pool_week)
        if error is not None:
            raise error
        picks = {} if picked is None else {ATL_GB["cbsEventId"]: picked}
        return entry_page(entry(picks), events=(ATL_GB,))

    bot = PickemBot(
        settings,
        monitor=FakeMonitor(result or RefreshResult(changed=False, snapshot=_gb_snapshot())),
        scheduler=FakeScheduler(),
        fetch_board_html=fetch,
    )

    async def dm(message=None, *, embed=None):
        dms.append(embed)

    bot._send_owner_dm = dm
    return bot, fetched, dms


@pytest.mark.asyncio
async def test_the_last_poll_before_kickoff_flags_a_differing_pick(settings):
    bot, fetched, dms = _check_bot(settings, picked=ATL)

    await bot._run_kickoff_poll(CHECK_SCOPE, _instant(min(settings.poll_offsets_hours)))

    assert fetched == [4]  # NFL week 3 is pool week 4
    (embed,) = dms
    assert embed.title.startswith("⚠️ Pick check — 1 game")
    assert "model says **" in embed.description


@pytest.mark.asyncio
async def test_matching_picks_send_nothing(settings):
    bot, fetched, dms = _check_bot(settings, picked=GB)

    await bot._run_kickoff_poll(CHECK_SCOPE, _instant(min(settings.poll_offsets_hours)))

    assert fetched == [4]
    assert dms == []


@pytest.mark.asyncio
async def test_earlier_polls_do_not_check(settings):
    bot, fetched, dms = _check_bot(settings, picked=ATL)

    await bot._run_kickoff_poll(CHECK_SCOPE, _instant(max(settings.poll_offsets_hours)))

    assert fetched == []
    assert dms == []


@pytest.mark.asyncio
async def test_a_failed_fetch_dms_the_reason_and_the_unchecked_games(settings, records):
    bot, _, dms = _check_bot(settings, error=CbsSessionExpired("gone"))

    await bot._run_kickoff_poll(CHECK_SCOPE, _instant(min(settings.poll_offsets_hours)))

    (embed,) = dms
    assert embed.title.startswith("⚠️ Pick check didn't run")
    assert "cbs-login.sh" in embed.description
    assert "Not checked:" in embed.description
    assert any(r["extra"].get("event") == "pick_check_failed" for r in records)


@pytest.mark.asyncio
async def test_a_failed_poll_checks_against_the_stored_pick_and_says_so(settings, monkeypatch):
    bot, fetched, dms = _check_bot(
        settings, picked=GB, result=RefreshResult(changed=False, error=RuntimeError("odds down"))
    )
    monkeypatch.setattr(
        "pickem.discord_bot.generate_recommendations", lambda *a, **k: _gb_snapshot()
    )

    await bot._run_kickoff_poll(CHECK_SCOPE, _instant(min(settings.poll_offsets_hours)))

    assert fetched == [4]
    (embed,) = dms
    assert "could not be refreshed" in embed.description


@pytest.mark.asyncio
async def test_a_kickoff_with_no_stored_games_fetches_nothing(settings):
    bot, fetched, dms = _check_bot(settings, picked=ATL)
    moved = PollInstant(
        at=CHECK_KICKOFF, offset_hours=min(settings.poll_offsets_hours),
        kickoff_utc=CHECK_KICKOFF + timedelta(hours=1),
    )

    await bot._check_picks(CHECK_SCOPE, moved, ())

    assert fetched == []
    assert dms == []


@pytest.mark.asyncio
async def test_concurrent_checks_never_open_chrome_at_once(settings):
    bot, _, _ = _check_bot(settings, picked=GB)
    active = 0
    peak = 0

    async def slow_fetch(pool_week: int) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return entry_page(entry({ATL_GB["cbsEventId"]: GB}), events=(ATL_GB,))

    bot._fetch_board_html = slow_fetch
    results = ((CHECK_SCOPE, RefreshResult(changed=False, snapshot=_gb_snapshot())),)
    instant = _instant(min(settings.poll_offsets_hours))

    await asyncio.gather(
        bot._check_picks(CHECK_SCOPE, instant, results),
        bot._check_picks(CHECK_SCOPE, instant, results),
    )

    assert peak == 1


@pytest.mark.asyncio
async def test_a_failed_dm_is_logged_and_does_not_break_the_poll(settings, records):
    bot, _, _ = _check_bot(settings, picked=ATL)

    async def broken_dm(message=None, *, embed=None):
        raise RuntimeError("discord down")

    bot._send_owner_dm = broken_dm

    await bot._run_kickoff_poll(CHECK_SCOPE, _instant(min(settings.poll_offsets_hours)))

    events = [r["extra"].get("event") for r in records]
    assert "owner_dm_failed" in events
    (done,) = [r for r in records if r["extra"].get("event") == "pick_check_completed"]
    assert done["extra"]["dm_sent"] is False
```

- [ ] **Step 2: Run and confirm they fail**

Run: `uv run pytest tests/test_discord_bot.py -q -k "check or poll_before or earlier_polls or failed_fetch or failed_poll or failed_dm or concurrent"`
Expected: FAIL with `TypeError: PickemBot.__init__() got an unexpected keyword argument 'fetch_board_html'`.

- [ ] **Step 3: Implement the wiring**

In `src/pickem/discord_bot.py`, add these imports:

```python
from pickem.ingest.cbs_entry import parse_entry_picks
from pickem.ingest.cbs_fetch import fetch_board_html as fetch_cbs_board_html
from pickem.ingest.cbs_fetch import open_session as open_cbs_session
from pickem.notify.discord_dm import build_message_embed
from pickem.operations.pick_check import (
    compare_picks,
    failure_reason,
    format_failure,
    format_pick_check,
    log_pick_check,
    log_pick_check_failed,
)
```

Change the `PickemBot.__init__` signature and add these attributes after `self._refresh_lock = asyncio.Lock()`:

```python
    def __init__(
        self,
        settings: DiscordSettings,
        monitor: RecommendationMonitor | Any | None = None,
        scheduler: Any | None = None,
        *,
        fetch_board_html: Callable[[int], Awaitable[str]] | None = None,
    ) -> None:
```

```python
        # One Chrome profile serves every CBS fetch; two scopes' checks at the
        # same kickoff would otherwise collide and report a false "busy".
        self._cbs_lock = asyncio.Lock()
        self._fetch_board_html = fetch_board_html or self._fetch_cbs_board
```

Replace `_run_kickoff_poll`'s body so it keeps its result and checks after the last poll:

```python
    async def _run_kickoff_poll(
        self, scope: MonitorScope, instant: PollInstant
    ) -> tuple[tuple[MonitorScope, RefreshResult], ...]:
        with run_context("sched:kickoff-poll", db=str(self.settings.db)):
            logger.bind(
                event="kickoff_poll_fired",
                sport=scope.sport.value,
                season=scope.season,
                week=scope.week,
                offset_hours=instant.offset_hours,
                kickoff=instant.kickoff_utc.isoformat(),
            ).info(
                f"polling {scope.sport.value} {instant.offset_hours:g}h before "
                f"{instant.kickoff_utc.isoformat()}"
            )
            results = await self._refresh_scopes(
                scope.season,
                scope.week,
                sport=scope.sport,
                window_start=instant.at,
            )
            # The last poll is the model's final word before the pick locks,
            # so that is when CBS is compared against it.
            if instant.offset_hours == min(self.settings.poll_offsets_hours, default=None):
                await self._check_picks(scope, instant, results)
            return results
```

Add these methods beside it:

```python
    async def _fetch_cbs_board(self, pool_week: int) -> str:
        async with open_cbs_session(
            profile=config.CBS_CHROME_PROFILE, chrome=config.CHROME_BINARY
        ) as session:
            return await fetch_cbs_board_html(
                session, pool_url=config.CBS_POOL_URL, pool_week=pool_week
            )

    async def _dm_pick_check(self, title: str, body: str) -> bool:
        try:
            await self._send_owner_dm(embed=build_message_embed(title, body))
        except Exception as error:
            logger.bind(
                event="owner_dm_failed",
                purpose="pick_check",
                error_type=type(error).__name__,
                error_detail=str(error),
            ).error(f"pick-check DM failed: {error}")
            return False
        return True

    async def _check_picks(
        self,
        scope: MonitorScope,
        instant: PollInstant,
        results: tuple[tuple[MonitorScope, RefreshResult], ...],
    ) -> None:
        """Compare CBS with the model for the games starting at this kickoff.

        Silent when everything matches on a fresh pick. Anything else -- a
        pick to fix, a game CBS doesn't show, a stale model pick, a check that
        could not run -- DMs the owner, because silence is read as "fine".
        """
        kickoff = instant.kickoff_utc
        where = {
            "sport": scope.sport,
            "season": scope.season,
            "week": scope.week,
            "kickoff_utc": kickoff,
        }
        games = tuple(
            game
            for game in _stored_week_details(self.settings, scope)[0]
            if game.kickoff_utc == kickoff
        )
        if not games:
            log_pick_check(**where, checks=(), dm_sent=False, stale=False)
            return
        resolver = TeamResolver.default()
        snapshot = next(
            (r.snapshot for s, r in results if s == scope and r.snapshot is not None), None
        )
        stale = snapshot is None
        try:
            if snapshot is None:
                now = datetime.now(UTC)
                snapshot = await asyncio.to_thread(
                    generate_recommendations,
                    self.settings.db,
                    scope.sport,
                    scope.season,
                    scope.week,
                    now,
                    pending_as_of=now,
                )
            pool_week = scope.week if scope.sport is Sport.CFB else scope.week + 1
            async with self._cbs_lock:
                html = await self._fetch_board_html(pool_week)
            board = parse_entry_picks(
                html, resolver=resolver, season=scope.season, pool_week=pool_week
            )
        except Exception as error:
            reason = failure_reason(error)
            log_pick_check_failed(**where, reason=reason, error=error, unchecked=len(games))
            await self._dm_pick_check(
                *format_failure(reason, games, kickoff_utc=kickoff, resolver=resolver)
            )
            return
        checks = compare_picks(board, snapshot.edges, games)
        message = format_pick_check(
            checks,
            kickoff_utc=kickoff,
            stale=stale,
            pool_url=config.CBS_POOL_URL,
            resolver=resolver,
        )
        sent = message is not None and await self._dm_pick_check(*message)
        log_pick_check(**where, checks=checks, dm_sent=sent, stale=stale)
```

- [ ] **Step 4: Run and confirm the tests pass**

Run: `uv run pytest tests/test_discord_bot.py -q`
Expected: all pass, old and new. If an existing kickoff-poll test asserts on `_run_kickoff_poll`'s exact calls and now sees a fetch, give that test's bot a `fetch_board_html` fake. Don't weaken the new behaviour.

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add src/pickem/discord_bot.py tests/test_discord_bot.py
git commit -m "feat: check CBS picks against the model after the last pre-kickoff poll"
```

---

### Task 5: Docs, and a check against the live page

**Files:**
- Modify: `CLAUDE.md` (the "Alert events" line)
- Modify: `docs/runbooks/verifying-from-logs.md` (a new subsection after "Did the scheduled job run?")
- Modify: `docs/runbooks/cbs-fetch.md` (the failures table and a short note)
- Modify: `docs/runbooks/discord-pick-reminder.md` (a new section after "Kickoff-anchored polls")

- [ ] **Step 1: Run the command against the live CBS page**

```bash
PICKEM_LOG_DIR=/tmp/claude-1000/-home-jmiller-cfb/b228ee97-e7e2-4946-a462-1a9a203abd9d/scratchpad/logs \
  uv run pickem check-picks --season 2026 --pool-week <current pool week>
```

Expected: one line per not-yet-started game, then a summary. Every game should be `✅`, `❌`, or `⚠️` and none `❓`. A `❓` means a stored game did not line up with the page, which is a bug to fix before shipping. Record the summary line for the final report.

- [ ] **Step 2: Update `CLAUDE.md`**

In the "Alert events" bullet, add `pick_check_failed` after `dashboard_write_failed`. After the "What alerts mean" list, add:

```markdown
   - A "Pick check" DM an hour before a kickoff means a pick entered on CBS differs from the model's, is missing, or couldn't be checked. Fix it on CBS by hand. "Pick check didn't run" carries the reason; a login reason uses the same fix as above.
```

- [ ] **Step 3: Add the log query to `docs/runbooks/verifying-from-logs.md`**

After the "Did the scheduled job run?" subsection:

````markdown
### Did the pick check run?

Each T-1h kickoff poll ends with one pick-check event: `pick_check_completed`
(with `checked`, `matched`, `different_side`, `no_pick`, `unmatched`, `stale`,
`dm_sent`) or `pick_check_failed` (with `reason`, `unchecked`).

```bash
jq -r 'select(.event=="pick_check_completed" or .event=="pick_check_failed")
  | [.ts, .event, .sport, .kickoff, (.checked // .unchecked), (.reason // "")] | @tsv' "$L"
```

A kickoff with a `kickoff_poll_fired` at `offset_hours` 1 and no pick-check
event after it means the check itself crashed; look for a traceback in that
run.
````

- [ ] **Step 4: Update `docs/runbooks/cbs-fetch.md`**

Under "## Schedule", after the table, add:

```markdown
The Discord bot also reads the board, without saving it, an hour before each
kickoff to check the entered picks (`discord-pick-reminder.md`, "Pre-kickoff
pick check"). Leaving the login window open blocks that check too.
```

Add a row to the Failures table:

```markdown
| Pick check didn't run — … | the pre-kickoff check couldn't read CBS | Follow the reason; the next kickoff's check runs on its own |
```

- [ ] **Step 5: Update `docs/runbooks/discord-pick-reminder.md`**

After the "Kickoff-anchored polls" section:

```markdown
### Pre-kickoff pick check

Right after the last kickoff-anchored poll (the smallest offset, 1 hour), the
bot reads the CBS board with the same Chrome profile as `fetch-cbs` and
compares the owner's entered picks for the games starting at that kickoff with
the model's pick. It DMs only when a pick differs, is missing, a game can't be
found on CBS, the model pick could not be refreshed, or the check could not
run. A quiet kickoff means every pick matched. Nothing is changed on CBS.

Run the same comparison for the whole week by hand:

    uv run pickem check-picks --season 2026 --pool-week N
```

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check .
git add CLAUDE.md docs/runbooks/verifying-from-logs.md docs/runbooks/cbs-fetch.md \
  docs/runbooks/discord-pick-reminder.md
git commit -m "docs: pre-kickoff pick check runbooks and alert"
```

Shipping (merge, bot restart) is the `ship` skill's job and is not part of this plan. The bot must be restarted before the check runs.
