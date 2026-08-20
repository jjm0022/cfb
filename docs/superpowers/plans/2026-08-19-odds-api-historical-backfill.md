# Odds API Historical Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill 2020-2025 NFL frozen-line and submission-time market snapshots from The Odds API archive, sourced identically at both ends, so `pickem backtest` produces a phase-exit number.

**Architecture:** A pure planner turns the stored NFL schedule into a list of timestamped snapshot requests — one early-week "frozen" anchor per week, plus one per distinct kickoff slot. A new `OddsClient.fetch_historical_spreads` unwraps the archive's snapshot envelope and reuses the existing per-event parsing, including both guards. The backtest then classifies the two proxies by `source` rather than by book name.

**Tech Stack:** Python 3.12, httpx, pydantic 2, polars, DuckDB, typer, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-08-11-pickem-edge-design.md` (§9 is the section this plan discharges)
**Research:** `docs/research/2026-08-19-odds-api-historical.md` — verified endpoint facts, credit costs, and the same-source decision. Do not restate it here; read it first.

## Global Constraints

Copied from the repo's standing conventions. Every task's requirements include these.

- **All spreads are home-perspective.** Home favored by 3 is `-3.0`. The Odds API home `point` is already correct — **do not flip it**.
- **The `lines` table is append-only.** `INSERT OR IGNORE`, never `OR REPLACE`. A bad row can never be cleaned up. Nothing writes without knowing the game id is right.
- **Both odds guards are required and are not redundant.** A kickoff `window` checked against `commence_time` **before any team name is resolved**, and a `slate` of canonical game ids checked after resolution. Removing either reopens a distinct hole.
- **Nothing a source could not give us is dropped in silence.** Every line loader returns `MarketLinesResult`, never a bare list.
- **`edge/` and `backtest/` perform no I/O.** Pure functions only.
- **The test suite is fully offline.** HTTP is injected via `httpx.MockTransport`; loaders are injected. No test may touch the network.
- **StrEnum, never `str, Enum`** (ruff UP042).
- **`uv run pytest -q`, `uv run ruff check src tests`, and `uv run ruff format --check src tests` must all be clean before any commit.**

## Constraints specific to this plan

- **Historical requests cost 10 credits each** (10 per region per market; we use one region, one market). Budget is 20,000/month.
- **The archive starts 2020-06-06.** Requests earlier than that return nothing.
- **The archive returns the closest snapshot at or earlier than `date`.** This is what makes the frozen-line proxy structurally incapable of peeking forward. Never work around it.
- **The database already contains 1,693 games and 1,693 `nflverse`/`close` lines** for 2020-2025 from a prior `backfill` run. Task 1 changes what `captured_at` those closers would be built with — see its warning.

## File structure

| File | Responsibility |
|---|---|
| `src/pickem/ingest/nflverse.py` (modify) | `_kickoff` gains real clock times from `gametime` |
| `src/pickem/backtest/snapshots.py` (create) | Pure planner: schedule -> timestamped snapshot requests + credit estimate |
| `src/pickem/ingest/odds.py` (modify) | `fetch_historical_spreads`; per-event parsing extracted and shared |
| `src/pickem/backtest/runner.py` (modify) | Classify proxies by `source`; consensus on both ends; corrected assumptions |
| `src/pickem/cli.py` (modify) | `backfill-history` with a mandatory dry run |
| `tests/test_nflverse_adapter.py`, `tests/test_snapshots.py`, `tests/test_odds_adapter.py`, `tests/test_backtest_runner.py`, `tests/test_cli.py` | Coverage per task |
| `tests/fixtures/odds_historical_nfl.json` (create, Task 6) | One real archive response, saved so tests stay offline |

---

### Task 1: Real NFL kickoff times

`_kickoff` parses `gameday` alone, so every NFL `Game.kickoff_utc` is midnight UTC. The submission-time proxy is defined by kickoff, so this blocks everything downstream. Phase A deferred it as "operationally inert" — that was true when nflverse contributed a single book and nothing read the clock time.

**⚠️ Do not re-run `load_nfl_closing_lines` into the database after this change.** `captured_at` is part of the `lines` primary key, so the 1,693 existing `nflverse`/`close` rows would be joined by 1,693 near-duplicates at the corrected timestamps, permanently, in an append-only table. Those closers are now only a cross-check (Task 4), so their stale timestamps are harmless. `upsert_games` updates `kickoff_utc` in place by `game_id`, so re-running `load_nfl_games` is safe and is what Task 6 does.

**Files:**
- Modify: `src/pickem/ingest/nflverse.py:31`
- Test: `tests/test_nflverse_adapter.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Game.kickoff_utc` carries the true UTC kickoff instant for NFL games.

- [ ] **Step 1: Verify the assumption before writing code**

`gametime` is believed to be `"HH:MM"` in US Eastern. Confirm against the real feed:

```bash
uv run python -c "
import nflreadpy
f = nflreadpy.load_schedules(seasons=[2024])
print(f.columns)
print(f.select(['game_id','gameday','gametime']).head(8))
print('null gametimes:', f.select(f['gametime'].is_null().sum()).item())
"
```

Expected: a `gametime` column of `HH:MM` strings. **If it is absent, or nulls are widespread, stop and report** — the whole submission-time proxy rests on it and a different source would be needed.

- [ ] **Step 2: Write the failing test**

```python
from datetime import UTC, datetime
import polars as pl
from pickem.ingest.nflverse import load_nfl_games
from pickem.resolve.resolver import TeamResolver


def _frame(rows):
    return pl.DataFrame(rows)


def test_kickoff_uses_gametime_in_eastern():
    frame = _frame([
        {
            "season": 2024, "week": 3, "gameday": "2024-09-22", "gametime": "13:00",
            "home_team": "MIA", "away_team": "BUF",
            "home_score": 20, "away_score": 17, "spread_line": 3.0, "total_line": 44.0,
        }
    ])
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda _: frame)
    # 13:00 EDT is 17:00 UTC.
    assert games[0].kickoff_utc == datetime(2024, 9, 22, 17, 0, tzinfo=UTC)


def test_kickoff_handles_standard_time():
    frame = _frame([
        {
            "season": 2024, "week": 18, "gameday": "2025-01-05", "gametime": "13:00",
            "home_team": "MIA", "away_team": "BUF",
            "home_score": 20, "away_score": 17, "spread_line": 3.0, "total_line": 44.0,
        }
    ])
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda _: frame)
    # 13:00 EST is 18:00 UTC — the offset must come from the date, not a constant.
    assert games[0].kickoff_utc == datetime(2025, 1, 5, 18, 0, tzinfo=UTC)


def test_missing_gametime_falls_back_to_midnight_utc():
    frame = _frame([
        {
            "season": 2024, "week": 3, "gameday": "2024-09-22", "gametime": None,
            "home_team": "MIA", "away_team": "BUF",
            "home_score": 20, "away_score": 17, "spread_line": 3.0, "total_line": 44.0,
        }
    ])
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda _: frame)
    assert games[0].kickoff_utc == datetime(2024, 9, 22, 0, 0, tzinfo=UTC)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_nflverse_adapter.py -k kickoff -v`
Expected: FAIL — the first two assert 17:00/18:00 but get 00:00.

- [ ] **Step 4: Implement**

```python
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def _kickoff(gameday: str, gametime: str | None = None) -> datetime:
    """The true kickoff instant.

    nflverse gives the date in `gameday` and the local clock time in
    `gametime`, always US Eastern. The offset must be derived from the date so
    a January playoff game gets EST and a September game gets EDT.

    A missing `gametime` degrades to midnight UTC rather than raising. That is
    a poor timestamp but a legible one, and it keeps a single malformed row
    from aborting a whole-season load.
    """
    day = datetime.fromisoformat(str(gameday))
    if not gametime:
        return day.replace(tzinfo=UTC)
    hour, _, minute = str(gametime).partition(":")
    local = day.replace(hour=int(hour), minute=int(minute), tzinfo=EASTERN)
    return local.astimezone(UTC)
```

Update both call sites. In `load_nfl_games`:

```python
kickoff_utc=_kickoff(row["gameday"], row.get("gametime")),
```

In `load_nfl_closing_lines`, leave `captured_at=_kickoff(row["gameday"])` **unchanged** — see the warning above. Add a comment saying so:

```python
# Deliberately date-only: captured_at is part of the lines primary key, and
# these 1,693 rows are already stored. Correcting it here would append a
# near-duplicate of every one of them to an append-only table. These closers
# are a cross-check now, not an input, so the coarse timestamp is harmless.
captured_at=_kickoff(row["gameday"]),
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all pass. Watch for other tests asserting midnight-UTC kickoffs; update any that encode the old behavior, and say so in the commit.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/ingest/nflverse.py tests/test_nflverse_adapter.py
git commit -m "fix: derive real NFL kickoff instants from nflverse gametime"
```

---

### Task 2: Snapshot planner

Pure translation from a stored schedule to the exact archive requests to make. Isolated here so the credit budget, the anchor rule, and the slot grouping are all testable without spending a credit or touching the network.

**Files:**
- Create: `src/pickem/backtest/snapshots.py`
- Test: `tests/test_snapshots.py`

**Interfaces:**
- Consumes: `Game` (with real kickoffs, Task 1)
- Produces:
  - `SnapshotKind` StrEnum: `FROZEN = "frozen"`, `SUBMISSION = "submission"`
  - `SnapshotRequest` BaseModel: `kind: SnapshotKind`, `at: datetime`, `window: tuple[datetime, datetime]`, `slate: frozenset[str]`, `season: int`, `week: int`
  - `plan_snapshots(games: Sequence[Game]) -> list[SnapshotRequest]`
  - `estimate_credits(requests: Sequence[SnapshotRequest]) -> int`
  - `ARCHIVE_START: datetime`, `CREDITS_PER_REQUEST: int = 10`

- [ ] **Step 1: Write the failing test**

```python
from datetime import UTC, datetime, timedelta

import pytest

from pickem.backtest.snapshots import (
    ARCHIVE_START,
    SnapshotKind,
    estimate_credits,
    plan_snapshots,
)
from pickem.models import Game, Sport, make_game_id


def game(week, kickoff, away, home, season=2024):
    return Game(
        game_id=make_game_id(Sport.NFL, season, week, away, home),
        sport=Sport.NFL, season=season, week=week, kickoff_utc=kickoff,
        home_team_id=home, away_team_id=away, home_score=20, away_score=17,
    )


SUN_EARLY = datetime(2024, 9, 22, 17, 0, tzinfo=UTC)   # Sun 13:00 ET
SUN_LATE = datetime(2024, 9, 22, 20, 25, tzinfo=UTC)   # Sun 16:25 ET
MON_NIGHT = datetime(2024, 9, 24, 0, 15, tzinfo=UTC)   # Mon 20:15 ET


def test_frozen_anchor_is_the_tuesday_before_the_first_kickoff():
    plan = plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA")])
    frozen = [r for r in plan if r.kind is SnapshotKind.FROZEN]
    assert len(frozen) == 1
    # Tuesday 2024-09-17 at 14:00 UTC, strictly before the first kickoff.
    assert frozen[0].at == datetime(2024, 9, 17, 14, 0, tzinfo=UTC)
    assert frozen[0].slate == {"nfl-2024-03-BUF-at-MIA"}


def test_one_submission_snapshot_per_distinct_kickoff_slot():
    games = [
        game(3, SUN_EARLY, "BUF", "MIA"),
        game(3, SUN_EARLY, "NYJ", "NE"),
        game(3, SUN_LATE, "SF", "SEA"),
        game(3, MON_NIGHT, "DAL", "NYG"),
    ]
    plan = plan_snapshots(games)
    submissions = [r for r in plan if r.kind is SnapshotKind.SUBMISSION]
    # Three slots, not four games.
    assert len(submissions) == 3
    early = next(r for r in submissions if r.at == SUN_EARLY - timedelta(minutes=5))
    # A slot's slate holds only the games kicking at that slot, so a game can
    # never be graded against a snapshot taken after it started.
    assert early.slate == {"nfl-2024-03-BUF-at-MIA", "nfl-2024-03-NYJ-at-NE"}


def test_submission_snapshot_precedes_its_own_kickoff():
    plan = plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA")])
    for request in plan:
        assert request.at < SUN_EARLY


def test_weeks_are_planned_independently():
    plan = plan_snapshots([
        game(3, SUN_EARLY, "BUF", "MIA"),
        game(4, SUN_EARLY + timedelta(days=7), "NYJ", "NE"),
    ])
    assert len({(r.season, r.week) for r in plan}) == 2


def test_games_before_the_archive_start_are_rejected():
    with pytest.raises(ValueError, match="archive"):
        plan_snapshots([game(1, ARCHIVE_START - timedelta(days=1), "BUF", "MIA", season=2019)])


def test_unplayed_games_are_excluded():
    unplayed = game(3, SUN_EARLY, "BUF", "MIA")
    unplayed = unplayed.model_copy(update={"home_score": None, "away_score": None})
    assert plan_snapshots([unplayed]) == []


def test_credit_estimate_is_ten_per_request():
    plan = plan_snapshots([game(3, SUN_EARLY, "BUF", "MIA"), game(3, MON_NIGHT, "DAL", "NYG")])
    assert estimate_credits(plan) == 10 * len(plan)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_snapshots.py -v`
Expected: FAIL — `ModuleNotFoundError: pickem.backtest.snapshots`.

- [ ] **Step 3: Implement**

```python
"""Turn a stored schedule into the exact archive requests to make.

Pure. No network, no database — so the credit budget can be read off a plan
before a single credit is spent.

The two proxies are timed differently on purpose:

* the FROZEN anchor imitates CBS, which freezes its number early in the week,
  so it is one snapshot per week at a fixed early-week instant. A true "opening
  line" would be whenever each book first posted, which is a different and less
  relevant moment.
* the SUBMISSION anchor imitates pressing submit, so it is the last snapshot
  before each game's own kickoff. Kickoffs are staggered, so this is one
  request per distinct kickoff slot — never one per week, and never one per
  game, since a single snapshot carries every game with posted odds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from pickem.models import Game

# The Odds API archive begins here; earlier requests return nothing.
ARCHIVE_START = datetime(2020, 6, 6, 10, 5, tzinfo=UTC)

CREDITS_PER_REQUEST = 10

# Tuesday. Fixed in UTC rather than tracking US Eastern so that a re-run is
# byte-identical: ~09:00 ET in summer, ~10:00 ET in winter. Precision does not
# matter here, determinism does — captured_at is part of the lines primary key.
_ANCHOR_WEEKDAY = 1
_ANCHOR_HOUR = 14

# Far enough before kickoff to be a real snapshot, close enough to be the
# submission-time market. The archive returns the closest snapshot at or
# earlier, so this can only ever resolve backwards.
_SUBMISSION_LEAD = timedelta(minutes=5)


class SnapshotKind(StrEnum):
    FROZEN = "frozen"
    SUBMISSION = "submission"


class SnapshotRequest(BaseModel):
    kind: SnapshotKind
    at: datetime
    window: tuple[datetime, datetime]
    slate: frozenset[str]
    season: int
    week: int


def _frozen_anchor(first_kickoff: datetime) -> datetime:
    """The most recent anchor weekday, strictly before the first kickoff."""
    candidate = first_kickoff.replace(hour=_ANCHOR_HOUR, minute=0, second=0, microsecond=0)
    while candidate.weekday() != _ANCHOR_WEEKDAY or candidate >= first_kickoff:
        candidate -= timedelta(days=1)
    return candidate


def plan_snapshots(games: Sequence[Game]) -> list[SnapshotRequest]:
    """Every archive request needed to backfill both proxies for `games`.

    Unplayed games are excluded: the backtest cannot grade them, so paying to
    snapshot them is waste.
    """
    playable = [g for g in games if g.home_score is not None and g.away_score is not None]
    if not playable:
        return []

    earliest = min(g.kickoff_utc for g in playable)
    if earliest < ARCHIVE_START:
        raise ValueError(
            f"kickoff {earliest.isoformat()} precedes the archive start "
            f"{ARCHIVE_START.isoformat()}; no snapshot exists"
        )

    by_week: dict[tuple[int, int], list[Game]] = defaultdict(list)
    for game in playable:
        by_week[(game.season, game.week)].append(game)

    requests: list[SnapshotRequest] = []
    for (season, week), week_games in sorted(by_week.items()):
        kickoffs = [g.kickoff_utc for g in week_games]
        first, last = min(kickoffs), max(kickoffs)
        # The window guard is what keeps an out-of-week event from being
        # stamped with this week's number. Pad it enough to cover a whole
        # week's slate and no more.
        window = (first - timedelta(hours=1), last + timedelta(hours=6))

        requests.append(
            SnapshotRequest(
                kind=SnapshotKind.FROZEN,
                at=_frozen_anchor(first),
                window=window,
                slate=frozenset(g.game_id for g in week_games),
                season=season,
                week=week,
            )
        )

        by_slot: dict[datetime, list[Game]] = defaultdict(list)
        for game in week_games:
            by_slot[game.kickoff_utc].append(game)
        for slot, slot_games in sorted(by_slot.items()):
            requests.append(
                SnapshotRequest(
                    kind=SnapshotKind.SUBMISSION,
                    at=slot - _SUBMISSION_LEAD,
                    # Narrowed to this slot so a game can never be graded
                    # against a snapshot taken after it kicked off.
                    window=window,
                    slate=frozenset(g.game_id for g in slot_games),
                    season=season,
                    week=week,
                )
            )
    return requests


def estimate_credits(requests: Sequence[SnapshotRequest]) -> int:
    """One region, one market — so 10 credits per request, flat."""
    return CREDITS_PER_REQUEST * len(requests)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_snapshots.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/snapshots.py tests/test_snapshots.py
git commit -m "feat: plan archive snapshots from the stored schedule"
```

---

### Task 3: `fetch_historical_spreads`

The archive wraps its events in a snapshot envelope. Everything beneath that — window guard, resolution, slate guard, per-book spread extraction, skip reporting — is identical to `fetch_spreads` and must be *shared*, not copied. A divergence between the live and historical parsers would mean the backtest measures something the weekly report does not.

**Files:**
- Modify: `src/pickem/ingest/odds.py`
- Test: `tests/test_odds_adapter.py`

**Interfaces:**
- Consumes: `SnapshotKind` (Task 2)
- Produces:
  - `OddsClient.fetch_historical_spreads(sport_key, *, resolver, sport, season, week, at, slate, window, source) -> MarketLinesResult`
  - `FROZEN_SOURCE = "oddsapi:frozen"`, `SUBMISSION_SOURCE = "oddsapi:submit"`

The two proxies are distinguished by `source`, leaving `book` holding the real bookmaker key so per-book detail survives and `consensus_spread` keeps working. In-season `poll-odds` rows keep `source="oddsapi"` and are therefore neither proxy — which is exactly the protection `split_proxies`' explicit classification exists to provide.

`captured_at` is the envelope's own `timestamp`, not the requested `at`: the archive returns the closest snapshot at or earlier, so storing the request time would misdate the row by up to ten minutes and make re-runs non-idempotent against the `lines` primary key.

- [ ] **Step 1: Write the failing test**

```python
from pickem.ingest.odds import FROZEN_SOURCE, SUBMISSION_SOURCE

SNAPSHOT_AT = datetime(2025, 9, 21, 16, 55, tzinfo=UTC)

ENVELOPE = {
    "timestamp": "2025-09-21T16:50:00Z",
    "previous_timestamp": "2025-09-21T16:40:00Z",
    "next_timestamp": "2025-09-21T17:00:00Z",
    "data": PAYLOAD,
}


def historical(client, slate=SLATE, window=WINDOW, source=SUBMISSION_SOURCE):
    return client.fetch_historical_spreads(
        NFL_KEY, resolver=TeamResolver.default(), sport=Sport.NFL, season=2025, week=3,
        at=SNAPSHOT_AT, slate=slate, window=window, source=source,
    )


def test_historical_unwraps_the_envelope():
    result = historical(client_returning(ENVELOPE))
    assert len(result.lines) == 2
    assert {line.book for line in result.lines} == {"pinnacle", "draftkings"}


def test_captured_at_is_the_snapshot_timestamp_not_the_request():
    result = historical(client_returning(ENVELOPE))
    # The archive answers with the closest snapshot at or earlier; storing the
    # requested time would misdate the row and break re-run idempotency.
    assert all(line.captured_at == datetime(2025, 9, 21, 16, 50, tzinfo=UTC) for line in result.lines)


def test_source_labels_the_proxy():
    frozen = historical(client_returning(ENVELOPE), source=FROZEN_SOURCE)
    assert all(line.source == FROZEN_SOURCE for line in frozen.lines)


def test_spread_is_not_flipped():
    result = historical(client_returning(ENVELOPE))
    # Odds API home point is already home-perspective.
    assert sorted(line.spread_home for line in result.lines) == [-6.5, -6.0]


def test_out_of_slate_event_is_reported_not_stored():
    result = historical(client_returning(ENVELOPE), slate={"nfl-2025-03-XXX-at-YYY"})
    assert result.lines == []
    assert any("slate" in row for row in result.skipped)


def test_out_of_window_event_is_reported_not_stored():
    far = (SNAPSHOT_AT + timedelta(days=30), SNAPSHOT_AT + timedelta(days=37))
    result = historical(client_returning(ENVELOPE), window=far)
    assert result.lines == []
    assert any("window" in row for row in result.skipped)


def test_empty_snapshot_is_not_an_error():
    result = historical(client_returning({"timestamp": "2025-09-21T16:50:00Z", "data": []}))
    assert result.lines == []


def test_unreadable_envelope_timestamp_raises():
    with pytest.raises(OddsApiError, match="timestamp"):
        historical(client_returning({"timestamp": "not-a-time", "data": PAYLOAD}))


def test_quota_exhaustion_is_distinct():
    with pytest.raises(QuotaExhausted):
        historical(client_returning({}, status=401))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_odds_adapter.py -k historical -v`
Expected: FAIL — `AttributeError: 'OddsClient' object has no attribute 'fetch_historical_spreads'`.

- [ ] **Step 3: Extract the shared parser**

Move the body of `fetch_spreads`' event loop into a module-level function. `fetch_spreads` must then call it and keep behaving identically — its existing tests are the proof.

```python
FROZEN_SOURCE = "oddsapi:frozen"
SUBMISSION_SOURCE = "oddsapi:submit"
LIVE_SOURCE = "oddsapi"


def _parse_events(
    events: list[dict],
    *,
    resolver: TeamResolver,
    sport: Sport,
    season: int,
    week: int,
    slate: Collection[str],
    window: tuple[datetime, datetime],
    captured_at: datetime,
    source: str,
) -> MarketLinesResult:
    """Both guards, resolution and per-book extraction, in one place.

    Shared by the live and historical paths deliberately. If the two ever
    parsed differently, the backtest would stop being evidence about the code
    that ships.
    """
```

The body is the existing loop verbatim, with `source=source` and `captured_at=captured_at` in the `MarketLine` construction. `fetch_spreads` becomes:

```python
        return _parse_events(
            response.json(),
            resolver=resolver, sport=sport, season=season, week=week,
            slate=slate, window=window, captured_at=now, source=LIVE_SOURCE,
        )
```

- [ ] **Step 4: Run the existing odds tests to prove the extraction changed nothing**

Run: `uv run pytest tests/test_odds_adapter.py tests/test_game_id_contract.py -v`
Expected: PASS, unchanged. **If any existing test now fails, the extraction is wrong — fix it before continuing.**

- [ ] **Step 5: Implement the historical method**

```python
    def fetch_historical_spreads(
        self,
        sport_key: str,
        *,
        resolver: TeamResolver,
        sport: Sport,
        season: int,
        week: int,
        at: datetime,
        slate: Collection[str],
        window: tuple[datetime, datetime],
        source: str,
    ) -> MarketLinesResult:
        """One archived snapshot, at or earlier than `at`.

        Costs 10 credits per call — ten times a live request — so callers plan
        their requests before making them.
        """
        window_start, window_end = window
        response = self._get(
            f"/historical/sports/{sport_key}/odds",
            params={
                "apiKey": self._api_key,
                "regions": "us",
                "markets": "spreads",
                "oddsFormat": "american",
                "date": _api_time(at),
            },
        )
        if response.status_code in (401, 429):
            raise QuotaExhausted(f"odds api returned {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise OddsApiError(f"odds api returned {response.status_code}: {response.text}")

        envelope = response.json()
        # The snapshot's own timestamp, not `at`: the archive answers with the
        # closest snapshot at or earlier, so `at` would misdate the row and
        # make a re-run append near-duplicates to an append-only table.
        captured_at = _commence_time(envelope.get("timestamp"))
        if captured_at is None:
            raise OddsApiError(f"unreadable snapshot timestamp: {envelope.get('timestamp')!r}")

        return _parse_events(
            envelope.get("data", []),
            resolver=resolver, sport=sport, season=season, week=week,
            slate=slate, window=window, captured_at=captured_at, source=source,
        )
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/ingest/odds.py tests/test_odds_adapter.py
git commit -m "feat: read archived spread snapshots through the shared parser"
```

---

### Task 4: Classify the proxies by source, and take a consensus at both ends

Two changes to `runner.py`, both required before any number it prints can be trusted.

`split_proxies` classifies by book name, which cannot work now that both proxies carry real bookmaker keys. And `run_backtest` builds `openers_by_game` as `{line.game_id: line for line in openers}` — last-wins, so with a multi-book snapshot it silently keeps whichever book happened to sort last, while the closing side correctly takes a `consensus_spread`. The two ends of the divergence would be computed by different rules, which quietly manufactures or erases edges.

**Files:**
- Modify: `src/pickem/backtest/runner.py:22` (ASSUMPTIONS), `:33` (constants), `:75` (`split_proxies`), `:145` (`openers_by_game`)
- Test: `tests/test_backtest_runner.py`

**Interfaces:**
- Consumes: `FROZEN_SOURCE`, `SUBMISSION_SOURCE` (Task 3)
- Produces: unchanged public signatures for `split_proxies` and `run_backtest`

- [ ] **Step 1: Write the failing test**

```python
from pickem.backtest.runner import ASSUMPTIONS, run_backtest, split_proxies
from pickem.ingest.odds import FROZEN_SOURCE, SUBMISSION_SOURCE


def market(game_id, source, book, spread, at):
    return MarketLine(game_id=game_id, source=source, book=book, spread_home=spread, captured_at=at)


T0 = datetime(2024, 9, 17, 14, 0, tzinfo=UTC)
T1 = datetime(2024, 9, 22, 16, 55, tzinfo=UTC)


def test_split_classifies_by_source_across_books():
    lines = [
        market("g", FROZEN_SOURCE, "pinnacle", -3.0, T0),
        market("g", FROZEN_SOURCE, "draftkings", -3.5, T0),
        market("g", SUBMISSION_SOURCE, "pinnacle", -6.0, T1),
    ]
    frozen, submission, skipped = split_proxies(lines)
    assert len(frozen) == 2
    assert len(submission) == 1
    assert skipped == []


def test_live_poll_rows_are_neither_proxy():
    frozen, submission, skipped = split_proxies([market("g", "oddsapi", "pinnacle", -3.0, T1)])
    # An in-season poll graded as a proxy would contaminate the result.
    assert (frozen, submission) == ([], [])
    assert len(skipped) == 1


def test_nflverse_closers_are_no_longer_an_input():
    frozen, submission, skipped = split_proxies([market("g", "nflverse", "close", -3.0, T1)])
    # Demoted to a cross-check: mixing sources across the two ends would make
    # book composition look like line movement.
    assert (frozen, submission) == ([], [])
    assert len(skipped) == 1


def test_frozen_side_takes_a_consensus_not_an_arbitrary_book():
    game = Game(
        game_id="nfl-2024-03-BUF-at-MIA", sport=Sport.NFL, season=2024, week=3,
        kickoff_utc=datetime(2024, 9, 22, 17, 0, tzinfo=UTC),
        home_team_id="MIA", away_team_id="BUF", home_score=30, away_score=20,
    )
    frozen = [
        market(game.game_id, FROZEN_SOURCE, "a", -1.0, T0),
        market(game.game_id, FROZEN_SOURCE, "b", -3.0, T0),
        market(game.game_id, FROZEN_SOURCE, "c", -9.0, T0),
    ]
    submission = [market(game.game_id, SUBMISSION_SOURCE, "a", -3.0, T1)]
    report = run_backtest([game], frozen, submission)
    # Median -3.0 against a -3.0 close is a coinflip. Last-wins would have
    # taken -9.0 and manufactured a 6-point STRONG edge.
    assert report.overall.wins + report.overall.losses == 1
    assert [r.tier for r in report.by_tier] == [Tier.COINFLIP]


def test_assumptions_describe_the_shipped_proxies():
    joined = " ".join(ASSUMPTIONS).lower()
    assert "opening line" not in joined
    assert "early-week" in joined or "frozen" in joined
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_backtest_runner.py -v`
Expected: FAIL — classification by `book`, arbitrary opener, stale assumption text.

- [ ] **Step 3: Implement**

Replace the constants:

```python
# Both proxies come from the same feed, distinguished by source. Sourcing the
# two ends differently would put book composition inside the measured
# divergence, and divergence is the entire strategy.
FROZEN_PROXY_SOURCE = FROZEN_SOURCE
SUBMISSION_PROXY_SOURCE = SUBMISSION_SOURCE
```

In `split_proxies`, classify on `line.source` against those two constants, keeping the explicit catch-all `else` and its message. In `run_backtest`, collapse the frozen side per game with the same function the submission side uses:

```python
    frozen_by_game_lines: dict[str, list[MarketLine]] = defaultdict(list)
    for line in openers:
        frozen_by_game_lines[line.game_id].append(line)
```

and in the loop:

```python
            frozen_lines = frozen_by_game_lines.get(game.game_id, [])
            frozen_spread = consensus_spread(frozen_lines)
            if frozen_spread is None:
                skipped.append(f"{game.game_id}: missing frozen-line snapshot for the proxy")
                continue
            frozen = LeagueLine(
                game_id=game.game_id,
                season=game.season,
                week=game.week,
                spread_home=frozen_spread,
                posted_at=max(line.captured_at for line in frozen_lines),
            )
```

Import `consensus_spread` from `pickem.edge.divergence`. Rewrite `ASSUMPTIONS[0]` and `[1]`:

```python
ASSUMPTIONS = [
    "The frozen league line is proxied by an early-week market snapshot taken "
    "the Tuesday before kickoff; the real CBS number was never recorded "
    "historically and is set early in the week, but is not identical.",
    "The market at submission time is proxied by the last archived snapshot "
    "before each game's own kickoff.",
    "Both proxies come from the same feed and the same books, so measured "
    "divergence is line movement rather than a difference between sources.",
    ...
]
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_backtest_runner.py -v && uv run pytest -q`
Expected: PASS. Existing runner tests built on `book="open"` will need their fixtures moved to the new sources — that is the point of the task, not a regression.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/runner.py tests/test_backtest_runner.py
git commit -m "feat: take both backtest proxies from the same source and a consensus"
```

---

### Task 5: `backfill-history` CLI, dry run first

**Files:**
- Modify: `src/pickem/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `plan_snapshots`, `estimate_credits`, `fetch_historical_spreads`
- Produces: `pickem backfill-history --from N --to N [--execute] [--max-credits N]`

**Default is a dry run.** Spending is opt-in via `--execute`, because ~1,200 requests of wrong game ids cannot be removed from an append-only table and ~12,000 credits cannot be refunded.

- [ ] **Step 1: Write the failing test**

```python
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import Game, MarketLinesResult, Sport, make_game_id
from pickem.store.db import Store


def _seed_games(db):
    """Two 2024 week-3 games at two kickoff slots, both with final scores.

    That shape yields exactly three planned snapshots: one frozen anchor plus
    one per slot — enough to assert the credit arithmetic without a fixture
    the size of a season.
    """
    games = [
        Game(
            game_id=make_game_id(Sport.NFL, 2024, 3, "BUF", "MIA"),
            sport=Sport.NFL, season=2024, week=3,
            kickoff_utc=datetime(2024, 9, 22, 17, 0, tzinfo=UTC),
            home_team_id="MIA", away_team_id="BUF", home_score=30, away_score=20,
        ),
        Game(
            game_id=make_game_id(Sport.NFL, 2024, 3, "DAL", "NYG"),
            sport=Sport.NFL, season=2024, week=3,
            kickoff_utc=datetime(2024, 9, 24, 0, 15, tzinfo=UTC),
            home_team_id="NYG", away_team_id="DAL", home_score=17, away_score=21,
        ),
    ]
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(games)


def test_backfill_history_dry_run_spends_nothing(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    _seed_games(db)
    monkeypatch.setattr(
        "pickem.cli.OddsClient",
        lambda *a, **k: pytest.fail("a dry run must never construct a client"),
    )
    result = CliRunner().invoke(
        app, ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db)]
    )
    assert result.exit_code == 0
    assert "dry run" in result.output.lower()
    # 3 snapshots x 10 credits.
    assert "30 credits" in result.output


def test_backfill_history_refuses_to_exceed_the_credit_ceiling(tmp_path):
    db = tmp_path / "t.duckdb"
    _seed_games(db)
    result = CliRunner().invoke(
        app,
        ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db),
         "--execute", "--max-credits", "1"],
    )
    assert result.exit_code == 1
    assert "exceeds" in result.output.lower()


def test_backfill_history_with_no_stored_games_exits_nonzero(tmp_path):
    db = tmp_path / "empty.duckdb"
    with Store(db) as store:
        store.init_schema()
    result = CliRunner().invoke(
        app, ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "backfill first" in result.output.lower()


def test_backfill_history_reports_skipped_rows(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    _seed_games(db)

    class StubClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch_historical_spreads(self, *args, **kwargs):
            return MarketLinesResult(lines=[], skipped=["some-game: not in the slate"])

    monkeypatch.setattr("pickem.cli.OddsClient", StubClient)
    monkeypatch.setattr("pickem.config.odds_api_key", lambda: "test-key")
    result = CliRunner().invoke(
        app,
        ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db), "--execute"],
    )
    assert result.exit_code == 0
    # Nothing a source could not give us is dropped in silence.
    assert "not in the slate" in result.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli.py -k backfill_history -v`
Expected: FAIL — no such command.

- [ ] **Step 3: Implement**

```python
@app.command("backfill-history")
def backfill_history(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    execute: bool = typer.Option(False, "--execute", help="Actually spend credits and write rows"),
    max_credits: int = typer.Option(20_000, help="Refuse to start if the plan costs more"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Backfill both backtest proxies from the Odds API archive.

    Dry run by default. The `lines` table is append-only and credits are not
    refundable, so spending is opt-in.
    """
    with _store(db) as store:
        games: list[Game] = []
        for season in range(start, end + 1):
            for week in range(1, 23):
                games.extend(store.games_for_week(Sport.NFL, season, week))
        if not games:
            typer.secho(f"no NFL games stored for {start}-{end}; run backfill first", fg="red", err=True)
            raise typer.Exit(code=1)

        plan = plan_snapshots(games)
        cost = estimate_credits(plan)
        frozen = sum(1 for r in plan if r.kind is SnapshotKind.FROZEN)
        typer.echo(
            f"{len(plan)} snapshots ({frozen} frozen, {len(plan) - frozen} submission) "
            f"across {len({(r.season, r.week) for r in plan})} weeks = {cost} credits"
        )
        if cost > max_credits:
            typer.secho(f"plan exceeds the {max_credits}-credit ceiling", fg="red", err=True)
            raise typer.Exit(code=1)
        if not execute:
            typer.secho("dry run — pass --execute to spend credits and write rows", fg="yellow")
            return

        with OddsClient(config.odds_api_key()) as client:
            for index, request in enumerate(plan, start=1):
                source = FROZEN_SOURCE if request.kind is SnapshotKind.FROZEN else SUBMISSION_SOURCE
                try:
                    result = client.fetch_historical_spreads(
                        NFL_KEY, resolver=TeamResolver.default(), sport=Sport.NFL,
                        season=request.season, week=request.week, at=request.at,
                        slate=request.slate, window=request.window, source=source,
                    )
                except QuotaExhausted as exc:
                    typer.secho(f"stopped at snapshot {index}/{len(plan)}: {exc}", fg="red", err=True)
                    raise typer.Exit(code=1) from exc
                store.append_market_lines(result.lines)
                typer.echo(
                    f"[{index}/{len(plan)}] {request.season} wk{request.week:02d} "
                    f"{request.kind.value}: {len(result.lines)} lines"
                )
                _warn_skipped(f"snapshot {index} rows not stored", result.skipped)
```

Add the imports: `plan_snapshots`, `estimate_credits`, `SnapshotKind` from `pickem.backtest.snapshots`; `FROZEN_SOURCE`, `SUBMISSION_SOURCE` from `pickem.ingest.odds`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

- [ ] **Step 5: Commit**

```bash
git add src/pickem/cli.py tests/test_cli.py
git commit -m "feat: add backfill-history with a dry run and a credit ceiling"
```

---

### Task 6: Verify against the live archive, then backfill for real

Everything to this point is offline. This task spends credits, in a deliberate order: cheapest verification first, largest commitment last. **Stop and report at any step whose expected result does not appear.**

**Files:**
- Create: `tests/fixtures/odds_historical_nfl.json`
- Test: `tests/test_odds_adapter.py` (one fixture-backed test)

- [ ] **Step 1: Refresh the stored schedule with real kickoff times**

Task 1 corrected `_kickoff`, but the 1,693 stored games still hold midnight-UTC values. `upsert_games` updates in place by `game_id`, so this rewrites them without touching scores or appending any line.

```bash
uv run pickem backfill --from 2020 --to 2025
```

Then confirm the clock times are real, not midnight:

```bash
uv run python -c "
import duckdb
c = duckdb.connect('data/pickem.duckdb', read_only=True)
print(c.execute('select count(*) from games where hour(kickoff_utc)=0 and minute(kickoff_utc)=0').fetchone())
print(c.execute('select distinct hour(kickoff_utc) from games order by 1').fetchall())
"
```

Expected: near-zero midnight rows, and a spread of hours (17, 20, 21, 00, 01…). **If everything is still midnight, Task 1 did not take effect — stop.**

Note this re-runs `load_nfl_closing_lines` too. Because Task 1 deliberately left that `captured_at` date-only, its rows are byte-identical to the stored ones and `INSERT OR IGNORE` makes it a no-op. Verify: `select count(*) from lines` must still be 1693.

- [ ] **Step 2: Dry run the whole plan and read the real credit cost**

```bash
uv run pickem backfill-history --from 2020 --to 2025
```

Expected: a snapshot count and credit total. The research doc estimated ~8,800 credits for NFL using ~6 slots per week; the true number comes from real kickoff data and may differ. **If it exceeds ~15,000, stop and re-scope** — narrow the season range rather than spending the month's budget in one pass.

- [ ] **Step 3: Spend 10 credits on one real snapshot and save it as a fixture**

```bash
uv run python -c "
import json, os
import httpx
from pickem import config
r = httpx.get(
    'https://api.the-odds-api.com/v4/historical/sports/americanfootball_nfl/odds',
    params={'apiKey': config.odds_api_key(), 'regions': 'us', 'markets': 'spreads',
            'oddsFormat': 'american', 'date': '2024-09-22T16:55:00Z'},
    timeout=30,
)
print(r.status_code, r.headers.get('x-requests-remaining'), r.headers.get('x-requests-last'))
open('tests/fixtures/odds_historical_nfl.json','w').write(json.dumps(r.json(), indent=2))
"
```

Expected: `200`, remaining ≈ 19,990, last = 10. **Confirm the response has `timestamp` and `data` keys and that `data[0]` carries `home_team`, `away_team`, `commence_time`, and `bookmakers`.** If the shape differs from Task 3's assumption, fix the parser before going further — this is the whole reason for buying one snapshot before buying twelve hundred.

- [ ] **Step 4: Pin the real shape with an offline test**

```python
import json
from pathlib import Path

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "odds_historical_nfl.json").read_text())


def test_real_archive_response_parses():
    """Guards the envelope contract against a real captured response."""
    client = client_returning(FIXTURE)
    kickoff = datetime(2024, 9, 22, 17, 0, tzinfo=UTC)
    result = client.fetch_historical_spreads(
        NFL_KEY, resolver=TeamResolver.default(), sport=Sport.NFL, season=2024, week=3,
        at=datetime(2024, 9, 22, 16, 55, tzinfo=UTC),
        slate={"nfl-2024-03-BUF-at-MIA"},  # replace with a game really in the fixture
        window=(kickoff - timedelta(hours=1), kickoff + timedelta(hours=6)),
        source=SUBMISSION_SOURCE,
    )
    assert result.lines
    assert all(line.source == SUBMISSION_SOURCE for line in result.lines)
```

Run: `uv run pytest tests/test_odds_adapter.py -k real_archive -v` — expected PASS. Commit the fixture and the test.

- [ ] **Step 5: Backfill one season and inspect before committing to six**

```bash
uv run pickem backfill-history --from 2024 --to 2024 --execute
```

Then check the ids joined and the two proxies both landed:

```bash
uv run python -c "
import duckdb
c = duckdb.connect('data/pickem.duckdb', read_only=True)
print(c.execute('select source, count(*), count(distinct game_id) from lines group by 1').fetchall())
print(c.execute(\"select count(*) from lines l left join games g using(game_id) where g.game_id is null\").fetchone())
"
```

Expected: rows under both `oddsapi:frozen` and `oddsapi:submit`, with distinct game counts close to the season's game count, and **zero orphaned lines**. A non-zero orphan count means wrong game ids are already written and cannot be removed — stop and report before touching another season.

- [ ] **Step 6: Grade 2024 alone and sanity-check the number**

```bash
uv run pickem backtest --from 2024 --to 2024
```

Expected: a non-zero record with Wilson intervals, and a short skip list. A hit rate far outside roughly 45-60% on a single season is more likely a sign convention bug than an edge — check `grade_pick` against a hand-worked game before believing it.

- [ ] **Step 7: Backfill the remaining seasons**

```bash
uv run pickem backfill-history --from 2020 --to 2023 --execute
uv run pickem backfill-history --from 2025 --to 2025 --execute
```

- [ ] **Step 8: Run the full backtest and update the handoff**

```bash
uv run pickem backtest --from 2020 --to 2025
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests
```

Record the result in `docs/HANDOFF.md`: the hit rate, the intervals, the credits actually spent, and what the number implies for the Phase B decision. Commit.

---

## Self-review notes

**Spec coverage.** This plan discharges spec §9 (backtest data gap) and unblocks §12 (phase exit criteria). It does not touch §§1-8 or §10-11, which Phase A completed.

**Deliberately out of scope.** CFB backfill — decided in the research doc to follow only after NFL verifies, and it needs `cfbd_source` kickoff times checked the way Task 1 checks nflverse's. `predict_tiebreaker_total` stays unwired: it needs the `totals` market, which is a separate quota cost. Neither belongs in this plan.

**The load-bearing assumption remains untested.** Whether CBS's frozen number tracks a Tuesday-morning market snapshot is what the entire backtest rests on, and no archive data tests it. It becomes checkable once real CBS pastes accumulate next to live `poll-odds` snapshots. Until then this measures early-week-to-kickoff divergence — the right proxy, but not the same claim.
