# Pick'em Edge Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python backend that ranks weekly CFB/NFL pick'em selections by the divergence between the league's frozen spread and the current market spread.

**Architecture:** One-directional pipeline — `ingest → resolve → store → edge → report`, with `backtest` hanging off `edge`. Adapters normalize every source into shared pydantic records; `edge/` is pure functions over those records with no I/O, making the strategy exhaustively testable offline.

**Tech Stack:** Python 3.12+, uv, pydantic, duckdb, polars, httpx, typer, pyyaml, nflreadpy, cfbd, pytz. Tests: pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-11-pickem-edge-design.md`

## Global Constraints

- Python 3.12+; all dependencies and the virtualenv managed by **uv**. Never invoke `pip` directly.
- Package layout is `src/pickem/`, tests in `tests/`.
- **All spreads are stored and passed home-perspective.** Home favored by 3 is `-3.0`. Every adapter normalizes at ingest.
- The `lines` table is **append-only**. Never `UPDATE` or `DELETE` a row in it.
- **Degrade visibly, never silently.** Unknown teams raise. Missing market lines are reported, not skipped. Never fabricate or interpolate a line. **Human ruling, 2026-08-11 (Task 9a amendment):** a market-line row with no usable spread is counted and named in `MarketLinesResult.skipped`, not silently `continue`d past — the same discipline Task 5's ruling applied to `ingest.cbs.ParseResult.skipped`. This binds Tasks 8, 9, 10 and the `backfill`/`poll-odds` CLI commands in Task 14.
- `edge/` performs no I/O — no network, no database, no filesystem.
- `pydantic` models at module boundaries; `polars` frames only inside adapters and backtest internals, never across a public interface.
- The test suite runs fully offline. Network calls are stubbed at the adapter boundary.
- Every task ends with a commit.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | uv project config, deps, pytest/ruff settings |
| `src/pickem/models.py` | All shared records + `make_game_id`. No logic beyond validation. |
| `src/pickem/resolve/aliases.yaml` | Curated source-name → canonical team_id mapping |
| `src/pickem/resolve/resolver.py` | `TeamResolver`, `UnknownTeamError`. Fail-loud identity. |
| `src/pickem/store/schema.sql` | DuckDB DDL |
| `src/pickem/store/db.py` | `Store` — the only module that talks to DuckDB |
| `src/pickem/ingest/cbs.py` | Paste-block parser |
| `src/pickem/ingest/nflverse.py` | NFL games/results/closing lines via nflreadpy |
| `src/pickem/ingest/cfbd_source.py` | CFB games/results/lines via CFBD |
| `src/pickem/ingest/odds.py` | The Odds API client for live market spreads |
| `src/pickem/edge/divergence.py` | Consensus + delta + tier. The strategy. |
| `src/pickem/edge/elo.py` | Minimal Elo margin rating for coinflip tiebreak |
| `src/pickem/report/sheet.py` | Terminal + markdown rendering |
| `src/pickem/backtest/runner.py` | Historical replay |
| `src/pickem/backtest/stats.py` | Wilson intervals |
| `src/pickem/cli.py` | typer CLI wiring all commands |

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `src/pickem/__init__.py`, `tests/test_smoke.py`, `.python-version`

**Interfaces:**
- Consumes: nothing
- Produces: a working `uv run pytest` and the `pickem` package importable as `pickem`

- [ ] **Step 1: Initialize the uv project**

```bash
uv init --package --name pickem --python 3.12 .
```

If `uv init` complains the directory is not empty, that is fine — it will still
write `pyproject.toml`. Do not delete existing files.

- [ ] **Step 2: Add dependencies**

```bash
uv add pydantic duckdb polars httpx typer pyyaml nflreadpy cfbd pytz
uv add --dev pytest pytest-cov ruff
```

- [ ] **Step 3: Configure pytest and ruff**

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 4: Write the smoke test**

Create `tests/test_smoke.py`:

```python
def test_package_imports():
    import pickem

    assert pickem is not None
```

- [ ] **Step 5: Run it**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: scaffold uv project with pytest and ruff"
```

---

### Task 2: Domain models

**Files:**
- Create: `src/pickem/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Sport` (enum: `NFL="nfl"`, `CFB="cfb"`)
  - `Side` (enum: `HOME="home"`, `AWAY="away"`)
  - `Tier` (enum: `STRONG="strong"`, `LEAN="lean"`, `COINFLIP="coinflip"`, `NO_MARKET="no_market"`)
  - `make_game_id(sport: Sport, season: int, week: int, away_team_id: str, home_team_id: str) -> str`
  - `Game`, `LeagueLine`, `MarketLine`, `Edge` pydantic models (fields below)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models.py`:

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pickem.models import Edge, Game, LeagueLine, MarketLine, Side, Sport, Tier, make_game_id


def test_game_id_is_deterministic_and_source_independent():
    a = make_game_id(Sport.NFL, 2025, 3, "BUF", "MIA")
    b = make_game_id(Sport.NFL, 2025, 3, "BUF", "MIA")
    assert a == b
    assert a == "nfl-2025-03-BUF-at-MIA"


def test_game_id_distinguishes_home_and_away():
    assert make_game_id(Sport.NFL, 2025, 3, "BUF", "MIA") != make_game_id(
        Sport.NFL, 2025, 3, "MIA", "BUF"
    )


def test_game_rejects_a_team_playing_itself():
    with pytest.raises(ValidationError):
        Game(
            game_id="nfl-2025-03-BUF-at-BUF",
            sport=Sport.NFL,
            season=2025,
            week=3,
            kickoff_utc=datetime(2025, 9, 21, 17, 0, tzinfo=UTC),
            home_team_id="BUF",
            away_team_id="BUF",
        )


def test_game_scores_default_to_none_until_played():
    game = Game(
        game_id="nfl-2025-03-BUF-at-MIA",
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=datetime(2025, 9, 21, 17, 0, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    assert game.home_score is None
    assert game.away_score is None


def test_league_line_holds_home_perspective_spread():
    line = LeagueLine(
        game_id="nfl-2025-03-BUF-at-MIA",
        season=2025,
        week=3,
        spread_home=-3.0,
        posted_at=datetime(2025, 9, 16, 12, 0, tzinfo=UTC),
    )
    assert line.spread_home == -3.0


def test_market_line_total_is_optional():
    line = MarketLine(
        game_id="nfl-2025-03-BUF-at-MIA",
        source="oddsapi",
        book="pinnacle",
        spread_home=-6.0,
        total=None,
        captured_at=datetime(2025, 9, 21, 12, 0, tzinfo=UTC),
    )
    assert line.total is None


def test_edge_records_both_numbers_that_produced_it():
    edge = Edge(
        game_id="nfl-2025-03-BUF-at-MIA",
        side=Side.HOME,
        delta=3.0,
        tier=Tier.STRONG,
        league_spread=-3.0,
        market_spread=-6.0,
        rationale="market moved 3.0 toward home",
    )
    assert edge.league_spread == -3.0
    assert edge.market_spread == -6.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.models'`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/models.py`:

```python
"""Shared records passed between modules.

Every spread in this system is home-perspective: home favored by 3 is -3.0.
Adapters normalize to this convention at ingest; nothing downstream re-checks it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, model_validator


class Sport(StrEnum):
    NFL = "nfl"
    CFB = "cfb"


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"


class Tier(StrEnum):
    STRONG = "strong"
    LEAN = "lean"
    COINFLIP = "coinflip"
    NO_MARKET = "no_market"


def make_game_id(
    sport: Sport, season: int, week: int, away_team_id: str, home_team_id: str
) -> str:
    """Deterministic game identity, independent of any source's own IDs.

    Two sources describing the same matchup produce the same string, which is
    what lets CBS, CFBD, nflverse and the odds feed be joined without a
    per-source ID crosswalk.
    """
    return f"{sport.value}-{season}-{week:02d}-{away_team_id}-at-{home_team_id}"


class Game(BaseModel):
    game_id: str
    sport: Sport
    season: int
    week: int
    kickoff_utc: datetime
    home_team_id: str
    away_team_id: str
    home_score: int | None = None
    away_score: int | None = None

    @model_validator(mode="after")
    def _teams_must_differ(self) -> Game:
        if self.home_team_id == self.away_team_id:
            raise ValueError(f"a team cannot play itself: {self.home_team_id}")
        return self


class LeagueLine(BaseModel):
    """The frozen CBS spread. One per game per week."""

    game_id: str
    season: int
    week: int
    spread_home: float
    posted_at: datetime


class MarketLine(BaseModel):
    """One book's spread at one moment. Append-only; never updated in place."""

    game_id: str
    source: str
    book: str
    spread_home: float
    total: float | None = None
    captured_at: datetime


class Edge(BaseModel):
    """The output of the strategy for a single game.

    Carries the two numbers it was derived from so any pick can be audited
    without opening the database.
    """

    game_id: str
    side: Side
    delta: float
    tier: Tier
    league_spread: float
    market_spread: float | None
    rationale: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/models.py tests/test_models.py
git commit -m "feat: add shared domain records with home-perspective spread convention"
```

---

### Task 3: Team identity resolver

The most important module in the system. A silent identity mismatch produces a
system that looks like it works and is quietly wrong.

**Files:**
- Create: `src/pickem/resolve/__init__.py`, `src/pickem/resolve/resolver.py`, `src/pickem/resolve/aliases.yaml`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Sport` from `pickem.models`
- Produces:
  - `UnknownTeamError(Exception)`
  - `TeamResolver.from_yaml(path: Path) -> TeamResolver`
  - `TeamResolver.default() -> TeamResolver` (loads the packaged `aliases.yaml`)
  - `TeamResolver.resolve(name: str, sport: Sport) -> str` returning a canonical `team_id`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resolver.py`:

```python
import pytest

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError


@pytest.fixture
def resolver():
    return TeamResolver.default()


def test_resolves_canonical_name(resolver):
    assert resolver.resolve("Miami Dolphins", Sport.NFL) == "MIA"


def test_resolves_across_source_naming_conventions(resolver):
    # Different feeds spell the same franchise differently.
    for name in ["LA Rams", "Los Angeles Rams", "LAR", "Rams"]:
        assert resolver.resolve(name, Sport.NFL) == "LAR"


def test_resolution_is_case_and_whitespace_insensitive(resolver):
    assert resolver.resolve("  miami DOLPHINS ", Sport.NFL) == "MIA"


def test_resolves_cfb_naming_mismatch(resolver):
    # CBS says "Ole Miss", CFBD says "Mississippi".
    assert resolver.resolve("Ole Miss", Sport.CFB) == "MISS"
    assert resolver.resolve("Mississippi", Sport.CFB) == "MISS"


def test_same_string_can_mean_different_teams_in_different_sports(resolver):
    # There is an NFL and a college team that share a nickname; sport disambiguates.
    assert resolver.resolve("Miami Dolphins", Sport.NFL) == "MIA"
    assert resolver.resolve("Miami (FL)", Sport.CFB) == "MIAFL"


def test_unknown_team_raises_rather_than_guessing(resolver):
    with pytest.raises(UnknownTeamError):
        resolver.resolve("Fictional State Aardvarks", Sport.CFB)


def test_unknown_team_error_suggests_close_matches(resolver):
    with pytest.raises(UnknownTeamError) as exc:
        resolver.resolve("Ole Mis", Sport.CFB)
    # The suggestion helps the user extend aliases.yaml; it never auto-resolves.
    assert "Ole Miss" in str(exc.value) or "MISS" in str(exc.value)


def test_never_falls_back_to_fuzzy_resolution(resolver):
    # A near-miss must still raise. Fuzzy matching informs the human, never the data.
    with pytest.raises(UnknownTeamError):
        resolver.resolve("Miami Dolphin", Sport.NFL)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.resolve'`

- [ ] **Step 3: Write the alias file**

Create `src/pickem/resolve/aliases.yaml`. Start with the teams the tests need
plus a representative sample; the file is expected to grow as real CBS pastes
surface new spellings.

```yaml
# canonical team_id -> every spelling any source uses for it.
# Matching is case-insensitive and whitespace-normalized.
# QUOTE any ID or alias PyYAML would read as a YAML 1.1 scalar:
# NO, ON, OFF, YES, Y, N, TRUE, FALSE, NULL. Bare NO becomes boolean false.
nfl:
  MIA: ["Miami Dolphins", "Miami", "MIA", "Dolphins"]
  BUF: ["Buffalo Bills", "Buffalo", "BUF", "Bills"]
  LAR: ["Los Angeles Rams", "LA Rams", "LAR", "Rams", "St. Louis Rams", "STL"]
  LAC: ["Los Angeles Chargers", "LA Chargers", "LAC", "Chargers", "San Diego Chargers", "SD"]
  LV:  ["Las Vegas Raiders", "Raiders", "LV", "LVR", "OAK", "Oakland Raiders"]
  WAS: ["Washington Commanders", "Washington", "WAS", "WSH", "Commanders"]
  NE:  ["New England Patriots", "New England", "NE", "NWE", "Patriots"]
  KC:  ["Kansas City Chiefs", "Kansas City", "KC", "KAN", "Chiefs"]
  SF:  ["San Francisco 49ers", "San Francisco", "SF", "SFO", "49ers", "Niners"]
  TB:  ["Tampa Bay Buccaneers", "Tampa Bay", "TB", "TAM", "Buccaneers", "Bucs"]
  GB:  ["Green Bay Packers", "Green Bay", "GB", "GNB", "Packers"]
  "NO": ["New Orleans Saints", "New Orleans", "NO", "NOR", "Saints"]  # quoted: bare NO is YAML 1.1 boolean false
  NYG: ["New York Giants", "NY Giants", "NYG", "Giants"]
  NYJ: ["New York Jets", "NY Jets", "NYJ", "Jets"]
cfb:
  MISS:  ["Ole Miss", "Mississippi", "Ole Miss Rebels"]
  MSST:  ["Mississippi State", "Mississippi St", "Miss State", "Mississippi State Bulldogs"]
  MIAFL: ["Miami (FL)", "Miami FL", "Miami Hurricanes", "Miami (Fla.)"]
  MIAOH: ["Miami (OH)", "Miami OH", "Miami RedHawks", "Miami (Ohio)"]
  OSU:   ["Ohio State", "Ohio St", "Ohio State Buckeyes"]
  MICH:  ["Michigan", "Michigan Wolverines"]
  BAMA:  ["Alabama", "Alabama Crimson Tide"]
  UGA:   ["Georgia", "Georgia Bulldogs"]
  TEX:   ["Texas", "Texas Longhorns"]
  OU:    ["Oklahoma", "Oklahoma Sooners"]
  ND:    ["Notre Dame", "Notre Dame Fighting Irish"]
  PSU:   ["Penn State", "Penn St", "Penn State Nittany Lions"]
  LSU:   ["LSU", "Louisiana State", "LSU Tigers"]
  USC:   ["USC", "Southern California", "Southern Cal", "USC Trojans"]
```

- [ ] **Step 4: Write the resolver**

Create `src/pickem/resolve/__init__.py` (empty file) and
`src/pickem/resolve/resolver.py`:

```python
"""Canonical team identity across sources.

Fail-loud by design: an unrecognized name raises rather than guessing. Fuzzy
matching exists only to suggest an alias to a human in the error message, and
never resolves data.
"""

from __future__ import annotations

import difflib
from importlib import resources
from pathlib import Path

import yaml

from pickem.models import Sport


class UnknownTeamError(LookupError):
    """A team name appeared that is not in aliases.yaml."""


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


class TeamResolver:
    def __init__(self, mapping: dict[Sport, dict[str, str]]) -> None:
        # mapping: sport -> normalized alias -> team_id
        self._mapping = mapping

    @classmethod
    def from_yaml(cls, path: Path) -> TeamResolver:
        raw = yaml.safe_load(path.read_text())
        return cls._build(raw)

    @classmethod
    def default(cls) -> TeamResolver:
        source = resources.files("pickem.resolve").joinpath("aliases.yaml")
        return cls._build(yaml.safe_load(source.read_text()))

    @classmethod
    def _build(cls, raw: dict) -> TeamResolver:
        mapping: dict[Sport, dict[str, str]] = {}
        for sport_key, teams in (raw or {}).items():
            sport = Sport(sport_key)
            table: dict[str, str] = {}
            for team_id, aliases in teams.items():
                for alias in [team_id, *aliases]:
                    key = _normalize(alias)
                    existing = table.get(key)
                    if existing is not None and existing != team_id:
                        raise ValueError(
                            f"alias {alias!r} is claimed by both {existing} and {team_id} "
                            f"in {sport_key}"
                        )
                    table[key] = team_id
            mapping[sport] = table
        return cls(mapping)

    def resolve(self, name: str, sport: Sport) -> str:
        table = self._mapping.get(sport, {})
        key = _normalize(name)
        team_id = table.get(key)
        if team_id is not None:
            return team_id
        raise UnknownTeamError(self._error_message(name, sport, table))

    @staticmethod
    def _error_message(name: str, sport: Sport, table: dict[str, str]) -> str:
        close = difflib.get_close_matches(_normalize(name), table.keys(), n=3, cutoff=0.6)
        msg = f"unknown {sport.value} team {name!r}"
        if close:
            hints = ", ".join(f"{c!r} -> {table[c]}" for c in close)
            msg += f"; did you mean {hints}? Add the spelling to aliases.yaml"
        else:
            msg += "; add it to aliases.yaml"
        return msg
```

- [ ] **Step 5: Verify the YAML is reachable as package data**

The project builds with `uv_build`, which includes non-Python files under
`src/pickem/` automatically — no `pyproject.toml` change is needed. Do NOT add
hatchling config; this project does not use hatchling.

Confirm the resource actually loads:

```bash
uv run python -c "
from pickem.resolve.resolver import TeamResolver
from pickem.models import Sport
print(TeamResolver.default().resolve('Ole Miss', Sport.CFB))
"
```

Expected output: `MISS`

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_resolver.py -v`
Expected: PASS (8 tests)

- [ ] **Step 7: Commit**

```bash
git add src/pickem/resolve tests/test_resolver.py pyproject.toml
git commit -m "feat: add fail-loud team identity resolver"
```

---

### Task 4: DuckDB store

> **Amendment (pytz is required, and duckdb will not tell you so).** duckdb's
> Python client imports `pytz` dynamically to materialize `TIMESTAMPTZ` columns
> on read, but does not declare it in its own package metadata — not as a
> dependency, not even as an extra. Without `pytz` installed, every read method
> below raises `InvalidInputException: Required module 'pytz' failed to import`,
> while writes succeed. `pytz` is therefore declared explicitly in `pyproject.toml`;
> do not remove it as "unused" because nothing imports it in our source. Note that
> timestamps come back with a `pytz` UTC tzinfo rather than `datetime.UTC` — equal
> by instant, but not identical by `is`. `tests/test_store.py` pins round-trip
> equality and tz-awareness on all three timestamp paths to catch a regression here.

**Files:**
- Create: `src/pickem/store/__init__.py`, `src/pickem/store/schema.sql`, `src/pickem/store/db.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Game`, `LeagueLine`, `MarketLine`, `Sport` from `pickem.models`
- Produces:
  - `Store(path: Path | str)` — pass `":memory:"` in tests
  - `Store.init_schema() -> None`
  - `Store.upsert_games(games: Sequence[Game]) -> None`
  - `Store.upsert_league_lines(lines: Sequence[LeagueLine]) -> None`
  - `Store.append_market_lines(lines: Sequence[MarketLine]) -> None`
  - `Store.market_lines_for(game_id: str, before: datetime | None = None) -> list[MarketLine]`
  - `Store.league_lines_for_week(sport: Sport, season: int, week: int) -> list[LeagueLine]`
  - `Store.games_for_week(sport: Sport, season: int, week: int) -> list[Game]`
  - `Store.close() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store.py`:

```python
from datetime import UTC, datetime

import pytest

from pickem.models import Game, LeagueLine, MarketLine, Sport
from pickem.store.db import Store

KICK = datetime(2025, 9, 21, 17, 0, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    yield s
    s.close()


def game(**overrides) -> Game:
    base = dict(
        game_id=GID,
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=KICK,
        home_team_id="MIA",
        away_team_id="BUF",
    )
    return Game(**{**base, **overrides})


def market(spread: float, at: datetime, book: str = "pinnacle") -> MarketLine:
    return MarketLine(
        game_id=GID, source="oddsapi", book=book, spread_home=spread, total=41.5, captured_at=at
    )


def test_roundtrips_a_game(store):
    store.upsert_games([game()])
    got = store.games_for_week(Sport.NFL, 2025, 3)
    assert len(got) == 1
    assert got[0].home_team_id == "MIA"


def test_upserting_a_game_updates_scores_rather_than_duplicating(store):
    store.upsert_games([game()])
    store.upsert_games([game(home_score=24, away_score=17)])
    got = store.games_for_week(Sport.NFL, 2025, 3)
    assert len(got) == 1
    assert got[0].home_score == 24


def test_roundtrips_a_league_line(store):
    store.upsert_games([game()])  # league_lines_for_week joins games for the sport filter
    store.upsert_league_lines(
        [LeagueLine(game_id=GID, season=2025, week=3, spread_home=-3.0, posted_at=KICK)]
    )
    got = store.league_lines_for_week(Sport.NFL, 2025, 3)
    assert got[0].spread_home == -3.0


def test_market_lines_are_append_only_and_preserve_movement(store):
    store.append_market_lines([market(-3.0, datetime(2025, 9, 16, tzinfo=UTC))])
    store.append_market_lines([market(-6.0, datetime(2025, 9, 21, tzinfo=UTC))])
    got = store.market_lines_for(GID)
    # Both snapshots survive. Overwriting would destroy the signal we exist to measure.
    assert sorted(line.spread_home for line in got) == [-6.0, -3.0]


def test_identical_snapshot_appended_twice_is_stored_once(store):
    at = datetime(2025, 9, 16, tzinfo=UTC)
    store.append_market_lines([market(-3.0, at)])
    store.append_market_lines([market(-3.0, at)])
    # Re-polling without a line change must not inflate the history.
    assert len(store.market_lines_for(GID)) == 1


def test_market_lines_can_be_cut_off_at_a_deadline(store):
    store.append_market_lines([market(-3.0, datetime(2025, 9, 16, tzinfo=UTC))])
    store.append_market_lines([market(-6.0, datetime(2025, 9, 22, tzinfo=UTC))])
    got = store.market_lines_for(GID, before=datetime(2025, 9, 21, tzinfo=UTC))
    # Backtests must not see lines captured after the game started.
    assert [line.spread_home for line in got] == [-3.0]


def test_week_queries_do_not_leak_across_weeks(store):
    store.upsert_games([game()])
    assert store.games_for_week(Sport.NFL, 2025, 4) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.store'`

- [ ] **Step 3: Write the schema**

Create `src/pickem/store/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS games (
    game_id       VARCHAR PRIMARY KEY,
    sport         VARCHAR NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    kickoff_utc   TIMESTAMPTZ NOT NULL,
    home_team_id  VARCHAR NOT NULL,
    away_team_id  VARCHAR NOT NULL,
    home_score    INTEGER,
    away_score    INTEGER
);

CREATE TABLE IF NOT EXISTS league_lines (
    game_id      VARCHAR PRIMARY KEY,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    spread_home  DOUBLE NOT NULL,
    posted_at    TIMESTAMPTZ NOT NULL
);

-- APPEND-ONLY. Never UPDATE or DELETE. Line movement is the signal.
-- The primary key deduplicates identical re-polls without destroying history.
CREATE TABLE IF NOT EXISTS lines (
    game_id      VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    book         VARCHAR NOT NULL,
    spread_home  DOUBLE NOT NULL,
    total        DOUBLE,
    captured_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (game_id, source, book, captured_at)
);

CREATE TABLE IF NOT EXISTS picks (
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    game_id       VARCHAR NOT NULL,
    side          VARCHAR NOT NULL,
    edge_points   DOUBLE NOT NULL,
    tier          VARCHAR NOT NULL,
    generated_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, week, game_id, generated_at)
);
```

- [ ] **Step 4: Write the store**

Create `src/pickem/store/__init__.py` (empty) and `src/pickem/store/db.py`:

```python
"""The only module that talks to DuckDB.

`lines` is append-only: its primary key absorbs duplicate polls, and nothing
here issues UPDATE or DELETE against it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from importlib import resources
from pathlib import Path

import duckdb

from pickem.models import Game, LeagueLine, MarketLine, Sport


class Store:
    def __init__(self, path: Path | str) -> None:
        self._con = duckdb.connect(str(path))

    def init_schema(self) -> None:
        ddl = resources.files("pickem.store").joinpath("schema.sql").read_text()
        self._con.execute(ddl)

    def close(self) -> None:
        self._con.close()

    def upsert_games(self, games: Sequence[Game]) -> None:
        rows = [
            (
                g.game_id,
                g.sport.value,
                g.season,
                g.week,
                g.kickoff_utc,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score,
            )
            for g in games
        ]
        self._con.executemany(
            "INSERT OR REPLACE INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )

    def upsert_league_lines(self, lines: Sequence[LeagueLine]) -> None:
        rows = [(x.game_id, x.season, x.week, x.spread_home, x.posted_at) for x in lines]
        self._con.executemany("INSERT OR REPLACE INTO league_lines VALUES (?, ?, ?, ?, ?)", rows)

    def append_market_lines(self, lines: Sequence[MarketLine]) -> None:
        rows = [
            (x.game_id, x.source, x.book, x.spread_home, x.total, x.captured_at) for x in lines
        ]
        # INSERT OR IGNORE, never REPLACE: an existing snapshot is history.
        self._con.executemany("INSERT OR IGNORE INTO lines VALUES (?, ?, ?, ?, ?, ?)", rows)

    def market_lines_for(self, game_id: str, before: datetime | None = None) -> list[MarketLine]:
        sql = "SELECT game_id, source, book, spread_home, total, captured_at FROM lines WHERE game_id = ?"
        params: list = [game_id]
        if before is not None:
            sql += " AND captured_at < ?"
            params.append(before)
        rows = self._con.execute(sql, params).fetchall()
        return [
            MarketLine(
                game_id=r[0], source=r[1], book=r[2], spread_home=r[3], total=r[4], captured_at=r[5]
            )
            for r in rows
        ]

    def league_lines_for_week(self, sport: Sport, season: int, week: int) -> list[LeagueLine]:
        rows = self._con.execute(
            """
            SELECT l.game_id, l.season, l.week, l.spread_home, l.posted_at
            FROM league_lines l JOIN games g USING (game_id)
            WHERE g.sport = ? AND l.season = ? AND l.week = ?
            """,
            [sport.value, season, week],
        ).fetchall()
        return [
            LeagueLine(game_id=r[0], season=r[1], week=r[2], spread_home=r[3], posted_at=r[4])
            for r in rows
        ]

    def games_for_week(self, sport: Sport, season: int, week: int) -> list[Game]:
        rows = self._con.execute(
            """
            SELECT game_id, sport, season, week, kickoff_utc,
                   home_team_id, away_team_id, home_score, away_score
            FROM games WHERE sport = ? AND season = ? AND week = ?
            """,
            [sport.value, season, week],
        ).fetchall()
        return [
            Game(
                game_id=r[0],
                sport=Sport(r[1]),
                season=r[2],
                week=r[3],
                kickoff_utc=r[4],
                home_team_id=r[5],
                away_team_id=r[6],
                home_score=r[7],
                away_score=r[8],
            )
            for r in rows
        ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add src/pickem/store tests/test_store.py
git commit -m "feat: add DuckDB store with append-only market line history"
```

---

### Task 5: CBS paste parser

**Files:**
- Create: `src/pickem/ingest/__init__.py`, `src/pickem/ingest/cbs.py`
- Test: `tests/test_cbs_parser.py`, `tests/fixtures/cbs_week3.txt`

**Interfaces:**
- Consumes: `TeamResolver`, `UnknownTeamError`, `LeagueLine`, `Sport`, `make_game_id`
- Produces:
  - `ParseResult` pydantic model with fields `lines: list[LeagueLine]`, `matchups: list[tuple[str, str]]` (away_id, home_id, parallel to `lines`), `skipped: list[str]`
  - `parse_cbs_block(text, *, resolver, sport, season, week, posted_at) -> ParseResult`
  - `CbsParseError(Exception)`

The CBS format will change. Fixtures make that a five-minute fix instead of a
debugging session — always add a fixture from a real paste before adjusting the regex.

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/cbs_week3.txt`. This mirrors the shape of a CBS pick
sheet copy: one game per line, away team first, favorite carrying the number.

```
Buffalo Bills at Miami Dolphins -3.0
Kansas City Chiefs -6.5 at New York Jets
Green Bay Packers at Chicago Bears +2.5
Ole Miss at Alabama -7.5
Ohio State -14.0 at Michigan

Bye: Cleveland Browns, Denver Broncos
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_cbs_parser.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pickem.ingest.cbs import parse_cbs_block
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
    assert result.lines[0].spread_home == -3.0


def test_away_favorite_becomes_positive_home_spread(resolver):
    # KC favored by 6.5 on the road: the home team is the +6.5 underdog.
    result = parse("Kansas City Chiefs -6.5 at New York Jets", resolver)
    assert result.lines[0].spread_home == 6.5


def test_explicit_home_underdog_sign_is_preserved(resolver):
    result = parse("Green Bay Packers at Chicago Bears +2.5", resolver)
    assert result.lines[0].spread_home == 2.5


def test_game_id_matches_canonical_construction(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0", resolver)
    assert result.lines[0].game_id == "nfl-2025-03-BUF-at-MIA"


def test_matchups_are_parallel_to_lines(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0", resolver)
    assert result.matchups == [("BUF", "MIA")]


def test_noise_lines_are_reported_not_silently_dropped(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0\nBye: Cleveland Browns", resolver)
    assert len(result.lines) == 1
    assert any("Bye" in s for s in result.skipped)


def test_unknown_team_raises(resolver):
    with pytest.raises(UnknownTeamError):
        parse("Fictional State at Miami Dolphins -3.0", resolver, sport=Sport.CFB)


def test_parses_the_nfl_portion_of_the_fixture(resolver):
    # A real sheet mixes sports; each is ingested with its own --sport run.
    nfl_lines = [
        line for line in FIXTURE.read_text().splitlines() if "at" in line and "Ole Miss" not in line
        and "Ohio State" not in line and "Michigan" not in line
    ]
    result = parse("\n".join(nfl_lines), resolver)
    assert len(result.lines) == 3
    assert [line.spread_home for line in result.lines] == [-3.0, 6.5, 2.5]


def test_parses_the_cfb_portion_of_the_fixture(resolver):
    result = parse("Ole Miss at Alabama -7.5\nOhio State -14.0 at Michigan", resolver, sport=Sport.CFB)
    assert [line.spread_home for line in result.lines] == [-7.5, 14.0]


def test_pickem_pushes_are_allowed(resolver):
    # A pick'em game (no favorite) is a legitimate 0.0 line, not a parse failure.
    result = parse("Buffalo Bills at Miami Dolphins PK", resolver)
    assert result.lines[0].spread_home == 0.0


def test_dual_number_lines_are_reported_as_ambiguous(resolver):
    # A line with numbers on both sides (e.g., game line + total) is ambiguous
    # and must be reported in skipped, not silently resolved to the home number.
    text = "Buffalo Bills at Miami Dolphins -3.0\nKansas City Chiefs -6.5 at New York Jets 45.5"
    result = parse(text, resolver)
    assert len(result.lines) == 1  # only the first line parses
    assert result.lines[0].spread_home == -3.0
    assert any("45.5" in s for s in result.skipped)  # second line is reported
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cbs_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.ingest'`

- [ ] **Step 4: Write the parser**

Create `src/pickem/ingest/__init__.py` (empty) and `src/pickem/ingest/cbs.py`:

```python
"""Parser for the text block copied off the CBS pick sheet.

A partially-ingested week is worse than a failed one, because it looks like
success. Anything not parsed is reported in `skipped`; anything parsed but
unresolvable raises.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel

from pickem.models import LeagueLine, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

# "<away> [spread] at <home> [spread]" — the number may sit on either team.
_NUM = r"(?:[+-]?\d+(?:\.\d+)?|PK|EVEN|pk)"
_GAME_RE = re.compile(
    rf"^\s*(?P<away>.+?)\s*(?P<away_num>{_NUM})?\s+at\s+(?P<home>.+?)\s*(?P<home_num>{_NUM})?\s*$",
    re.IGNORECASE,
)


class CbsParseError(ValueError):
    """The block contained no parsable games at all."""


class ParseResult(BaseModel):
    lines: list[LeagueLine]
    matchups: list[tuple[str, str]]
    skipped: list[str]


def _to_spread(token: str | None) -> float | None:
    if token is None:
        return None
    if token.upper() in {"PK", "EVEN"}:
        return 0.0
    return float(token)


def parse_cbs_block(
    text: str,
    *,
    resolver: TeamResolver,
    sport: Sport,
    season: int,
    week: int,
    posted_at: datetime,
) -> ParseResult:
    lines: list[LeagueLine] = []
    matchups: list[tuple[str, str]] = []
    skipped: list[str] = []

    for raw in text.splitlines():
        if not raw.strip():
            continue
        match = _GAME_RE.match(raw)
        if match is None:
            skipped.append(raw)
            continue

        away_num = _to_spread(match.group("away_num"))
        home_num = _to_spread(match.group("home_num"))
        if away_num is None and home_num is None:
            skipped.append(raw)
            continue

        if away_num is not None and home_num is not None:
            skipped.append(raw)  # ambiguous: numbers on both sides
            continue

        # Exactly one side carries the number. If the away team does, flip its
        # sign to express the same line from the home team's perspective.
        spread_home = home_num if home_num is not None else -away_num

        away_id = resolver.resolve(match.group("away"), sport)
        home_id = resolver.resolve(match.group("home"), sport)

        lines.append(
            LeagueLine(
                game_id=make_game_id(sport, season, week, away_id, home_id),
                season=season,
                week=week,
                spread_home=spread_home,
                posted_at=posted_at,
            )
        )
        matchups.append((away_id, home_id))

    if not lines:
        raise CbsParseError("no games parsed from the pasted block")

    return ParseResult(lines=lines, matchups=matchups, skipped=skipped)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cbs_parser.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/pickem/ingest tests/test_cbs_parser.py tests/fixtures
git commit -m "feat: parse CBS pick sheet paste into home-perspective league lines"
```

---

### Task 6: Divergence engine

The strategy. Pure functions, no I/O, exhaustively tested.

**Files:**
- Create: `src/pickem/edge/__init__.py`, `src/pickem/edge/divergence.py`
- Test: `tests/test_divergence.py`

**Interfaces:**
- Consumes: `Edge`, `LeagueLine`, `MarketLine`, `Side`, `Tier`
- Produces:
  - `Thresholds` pydantic model, fields `strong: float = 2.0`, `lean: float = 1.0`
  - `consensus_spread(lines: Sequence[MarketLine]) -> float | None` — median of the latest snapshot per book
  - `compute_edge(league: LeagueLine, market: Sequence[MarketLine], thresholds: Thresholds | None = None) -> Edge`
  - `rank_edges(edges: Sequence[Edge]) -> list[Edge]` — descending by `abs(delta)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_divergence.py`:

```python
from datetime import UTC, datetime

from pickem.edge.divergence import Thresholds, compute_edge, consensus_spread, rank_edges
from pickem.models import LeagueLine, MarketLine, Side, Tier

GID = "nfl-2025-03-BUF-at-MIA"
T0 = datetime(2025, 9, 16, tzinfo=UTC)
T1 = datetime(2025, 9, 21, tzinfo=UTC)


def league(spread: float) -> LeagueLine:
    return LeagueLine(game_id=GID, season=2025, week=3, spread_home=spread, posted_at=T0)


def market(spread: float, book: str = "pinnacle", at: datetime = T1) -> MarketLine:
    return MarketLine(game_id=GID, source="oddsapi", book=book, spread_home=spread, captured_at=at)


def test_consensus_is_the_median_across_books():
    lines = [market(-6.0, "a"), market(-6.5, "b"), market(-7.0, "c")]
    assert consensus_spread(lines) == -6.5


def test_consensus_median_resists_a_single_broken_book():
    lines = [market(-6.0, "a"), market(-6.5, "b"), market(-60.0, "stale")]
    # A mean would be dragged to -24.2; the median holds.
    assert consensus_spread(lines) == -6.5


def test_consensus_uses_only_the_latest_snapshot_per_book():
    lines = [market(-3.0, "a", at=T0), market(-7.0, "a", at=T1)]
    assert consensus_spread(lines) == -7.0


def test_consensus_of_nothing_is_none():
    assert consensus_spread([]) is None


def test_market_moving_toward_home_makes_home_the_value_side():
    # We hold home at -3 while the market has repriced to -6: 3 points of value on home.
    edge = compute_edge(league(-3.0), [market(-6.0)])
    assert edge.side == Side.HOME
    assert edge.delta == 3.0


def test_market_moving_toward_away_makes_away_the_value_side():
    # We hold home at -6 while the market says -3: the away dog is the value.
    edge = compute_edge(league(-6.0), [market(-3.0)])
    assert edge.side == Side.AWAY
    assert edge.delta == -3.0


def test_sign_convention_holds_when_the_line_crosses_zero():
    edge = compute_edge(league(2.0), [market(-1.0)])
    assert edge.side == Side.HOME
    assert edge.delta == 3.0


def test_large_divergence_is_strong():
    assert compute_edge(league(-3.0), [market(-6.0)]).tier is Tier.STRONG


def test_moderate_divergence_is_a_lean():
    assert compute_edge(league(-3.0), [market(-4.5)]).tier is Tier.LEAN


def test_small_divergence_is_a_coinflip():
    assert compute_edge(league(-3.0), [market(-3.5)]).tier is Tier.COINFLIP


def test_tier_boundaries_are_inclusive_at_the_threshold():
    assert compute_edge(league(-3.0), [market(-5.0)]).tier is Tier.STRONG   # exactly 2.0
    assert compute_edge(league(-3.0), [market(-4.0)]).tier is Tier.LEAN     # exactly 1.0


def test_thresholds_are_configuration_not_logic():
    strict = Thresholds(strong=5.0, lean=3.0)
    assert compute_edge(league(-3.0), [market(-6.0)], strict).tier is Tier.LEAN


def test_missing_market_is_reported_not_skipped():
    edge = compute_edge(league(-3.0), [])
    assert edge.tier is Tier.NO_MARKET
    assert edge.market_spread is None
    assert edge.delta == 0.0


def test_edge_carries_both_source_numbers_for_audit():
    edge = compute_edge(league(-3.0), [market(-6.0)])
    assert edge.league_spread == -3.0
    assert edge.market_spread == -6.0
    assert edge.rationale


def test_ranking_orders_by_absolute_divergence():
    edges = [
        compute_edge(league(-3.0), [market(-3.5)]),
        compute_edge(league(-3.0), [market(-9.0)]),
        compute_edge(league(-3.0), [market(-1.0)]),
    ]
    assert [abs(e.delta) for e in rank_edges(edges)] == [6.0, 2.0, 0.5]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_divergence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.edge'`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/edge/__init__.py` (empty) and `src/pickem/edge/divergence.py`:

```python
"""The strategy: exploit the gap between a frozen league line and the live market.

Pure functions only. No network, no database, no filesystem — which is what
makes the whole strategy testable offline and replayable in the backtest.

Sign convention (everything is home-perspective):

    delta = league_spread - market_spread

    delta > 0  ->  we hold the home side at a better price than the market's
    delta < 0  ->  we hold the away side at a better price
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from pydantic import BaseModel

from pickem.models import Edge, LeagueLine, MarketLine, Side, Tier


class Thresholds(BaseModel):
    """Tier cutoffs in points of divergence.

    These defaults are initial guesses, to be replaced by backtested values.
    They are configuration, not logic.
    """

    strong: float = 2.0
    lean: float = 1.0


def consensus_spread(lines: Sequence[MarketLine]) -> float | None:
    """Median spread across books, using each book's most recent snapshot.

    Median rather than mean so one stale or erroneous book cannot drag the
    consensus. Collapsing per book first stops a frequently-polled book from
    outvoting the rest.
    """
    if not lines:
        return None
    latest: dict[str, MarketLine] = {}
    for line in lines:
        current = latest.get(line.book)
        if current is None or line.captured_at > current.captured_at:
            latest[line.book] = line
    return median(line.spread_home for line in latest.values())


def _tier(delta: float, thresholds: Thresholds) -> Tier:
    magnitude = abs(delta)
    if magnitude >= thresholds.strong:
        return Tier.STRONG
    if magnitude >= thresholds.lean:
        return Tier.LEAN
    return Tier.COINFLIP


def compute_edge(
    league: LeagueLine,
    market: Sequence[MarketLine],
    thresholds: Thresholds | None = None,
) -> Edge:
    thresholds = thresholds or Thresholds()
    consensus = consensus_spread(market)

    if consensus is None:
        # Never skipped. A game with no market is surfaced as NO_MARKET so the
        # caller can fall through to the tiebreak rating with its eyes open.
        return Edge(
            game_id=league.game_id,
            side=Side.HOME,
            delta=0.0,
            tier=Tier.NO_MARKET,
            league_spread=league.spread_home,
            market_spread=None,
            rationale="no market line available; falls through to tiebreak rating",
        )

    delta = league.spread_home - consensus
    side = Side.HOME if delta > 0 else Side.AWAY
    moved_toward = "home" if delta > 0 else "away"
    return Edge(
        game_id=league.game_id,
        side=side,
        delta=delta,
        tier=_tier(delta, thresholds),
        league_spread=league.spread_home,
        market_spread=consensus,
        rationale=(
            f"league {league.spread_home:+.1f} vs market {consensus:+.1f}: "
            f"{abs(delta):.1f} pts toward {moved_toward}"
        ),
    )


def rank_edges(edges: Sequence[Edge]) -> list[Edge]:
    """Most divergent first — the order the pick sheet is read in."""
    return sorted(edges, key=lambda e: abs(e.delta), reverse=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_divergence.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/edge tests/test_divergence.py
git commit -m "feat: add divergence engine ranking picks by frozen-vs-market gap"
```

---

### Task 7: Elo tiebreak rating

Deliberately unsophisticated. It exists to order coinflips, not to beat the
market. Any temptation to elaborate it belongs in Phase B.

**Files:**
- Create: `src/pickem/edge/elo.py`
- Test: `tests/test_elo.py`

**Interfaces:**
- Consumes: `Game`, `Side`
- Produces:
  - `EloConfig` pydantic model: `k: float = 20.0`, `home_field: float = 2.0`, `points_per_elo: float = 0.04`, `season_regression: float = 0.25`, `initial: float = 1500.0`
  - `build_ratings(games: Sequence[Game], config: EloConfig = EloConfig()) -> dict[str, float]`
  - `projected_margin(ratings, home_team_id, away_team_id, config) -> float` (positive = home favored)
  - `tiebreak_side(projected_margin: float, league_spread: float) -> Side`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_elo.py`:

```python
from datetime import UTC, datetime

from pickem.edge.elo import EloConfig, build_ratings, projected_margin, tiebreak_side
from pickem.models import Game, Side, Sport


def played(away: str, home: str, away_score: int, home_score: int, week: int) -> Game:
    return Game(
        game_id=f"nfl-2025-{week:02d}-{away}-at-{home}",
        sport=Sport.NFL,
        season=2025,
        week=week,
        kickoff_utc=datetime(2025, 9, 7 + week, tzinfo=UTC),
        home_team_id=home,
        away_team_id=away,
        home_score=home_score,
        away_score=away_score,
    )


def test_all_teams_start_equal():
    ratings = build_ratings([])
    assert ratings == {}


def test_winning_raises_a_rating_and_losing_lowers_it():
    ratings = build_ratings([played("BUF", "MIA", 10, 30, 1)])
    assert ratings["MIA"] > ratings["BUF"]


def test_unplayed_games_are_ignored():
    unplayed = Game(
        game_id="nfl-2025-01-BUF-at-MIA",
        sport=Sport.NFL,
        season=2025,
        week=1,
        kickoff_utc=datetime(2025, 9, 8, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    assert build_ratings([unplayed]) == {}


def test_repeated_wins_compound():
    one = build_ratings([played("BUF", "MIA", 10, 30, 1)])
    two = build_ratings([played("BUF", "MIA", 10, 30, 1), played("BUF", "MIA", 10, 30, 2)])
    assert two["MIA"] > one["MIA"]


def test_projected_margin_favors_the_stronger_team_at_home():
    ratings = {"MIA": 1600.0, "BUF": 1500.0}
    assert projected_margin(ratings, "MIA", "BUF", EloConfig()) > 0


def test_home_field_advantage_is_applied():
    ratings = {"MIA": 1500.0, "BUF": 1500.0}
    config = EloConfig(home_field=2.5)
    assert projected_margin(ratings, "MIA", "BUF", config) == 2.5


def test_unrated_team_is_treated_as_average():
    ratings = {"MIA": 1500.0}
    # A team with no history must not crash or be treated as infinitely weak.
    assert projected_margin(ratings, "MIA", "NEWTEAM", EloConfig()) == EloConfig().home_field


def test_tiebreak_picks_home_when_projection_beats_the_line():
    # Home favored by 3 on the board; we project home by 7 -> home covers.
    assert tiebreak_side(projected_margin=7.0, league_spread=-3.0) is Side.HOME


def test_tiebreak_picks_away_when_projection_falls_short_of_the_line():
    # Home laying 10 but we only project home by 3 -> away covers.
    assert tiebreak_side(projected_margin=3.0, league_spread=-10.0) is Side.AWAY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_elo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.edge.elo'`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/edge/elo.py`:

```python
"""Minimal Elo margin rating, used only to order games the market has not moved.

This is intentionally shallow. It is not trying to beat the closing line; it is
trying to be better than a coin flip on the handful of games where divergence
gives no answer. Sophistication here belongs in Phase B.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from pickem.models import Game, Side


class EloConfig(BaseModel):
    k: float = 20.0
    home_field: float = 2.0
    points_per_elo: float = 0.04
    season_regression: float = 0.25
    initial: float = 1500.0


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def build_ratings(games: Sequence[Game], config: EloConfig | None = None) -> dict[str, float]:
    """Walk completed games in chronological order, updating ratings.

    Only games with both scores present are counted; scheduled-but-unplayed
    games must not move a rating.
    """
    config = config or EloConfig()
    ratings: dict[str, float] = {}
    played = [g for g in games if g.home_score is not None and g.away_score is not None]

    for game in sorted(played, key=lambda g: (g.season, g.week, g.kickoff_utc)):
        home = ratings.setdefault(game.home_team_id, config.initial)
        away = ratings.setdefault(game.away_team_id, config.initial)

        home_won = 1.0 if game.home_score > game.away_score else 0.0
        if game.home_score == game.away_score:
            home_won = 0.5

        elo_home = home + config.home_field / config.points_per_elo
        expected_home = _expected(elo_home, away)

        # Margin of victory multiplier, dampened so blowouts do not dominate.
        margin = abs(game.home_score - game.away_score)
        multiplier = ((margin + 1) ** 0.5) if margin else 1.0

        change = config.k * multiplier * (home_won - expected_home)
        ratings[game.home_team_id] = home + change
        ratings[game.away_team_id] = away - change

    return ratings


def projected_margin(
    ratings: dict[str, float],
    home_team_id: str,
    away_team_id: str,
    config: EloConfig | None = None,
) -> float:
    """Projected home margin in points. Positive means home is favored."""
    config = config or EloConfig()
    home = ratings.get(home_team_id, config.initial)
    away = ratings.get(away_team_id, config.initial)
    return (home - away) * config.points_per_elo + config.home_field


def tiebreak_side(projected_margin: float, league_spread: float) -> Side:
    """Pick the side our rating favors relative to the frozen line.

    `league_spread` is home-perspective, so the home team's implied margin is
    its negation. Home is the pick when we project a bigger margin than the
    board requires.
    """
    implied_home_margin = -league_spread
    return Side.HOME if projected_margin > implied_home_margin else Side.AWAY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_elo.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/edge/elo.py tests/test_elo.py
git commit -m "feat: add minimal Elo rating for coinflip tiebreaks"
```

---

### Task 8: nflverse adapter

> **Amendment (Task 9a, human ruling 2026-08-11): surface dropped rows, don't
> silently skip them.** The reviewer flagged that dropping a row with no
> `spread_line` via a bare `continue` reads against the Global Constraint
> "missing market lines are reported, not skipped." `load_nfl_closing_lines`
> now returns `MarketLinesResult` (`lines` + `skipped`), mirroring
> `ingest.cbs.ParseResult`. The text below is amended in place to reflect this.

**Files:**
- Create: `src/pickem/ingest/nflverse.py`
- Test: `tests/test_nflverse_adapter.py`

**Interfaces:**
- Consumes: `Game`, `MarketLine`, `MarketLinesResult`, `Sport`, `make_game_id`, `TeamResolver`
- Produces:
  - `load_nfl_games(seasons: Sequence[int], *, resolver: TeamResolver, loader=...) -> list[Game]`
  - `load_nfl_closing_lines(seasons, *, resolver, loader=...) -> MarketLinesResult`

`loader` is injected so tests never hit the network. Its default is
`nflreadpy.load_schedules`; it must return a polars DataFrame with columns
`season, week, gameday, home_team, away_team, home_score, away_score, spread_line, total_line`.

**nflverse sign convention warning:** nflverse `spread_line` is expressed from
the **home team's point of view as a positive favorite** (home favored by 3 is
`3.0`), which is the opposite of this project's convention. The adapter negates
it. This is asserted in the tests below because getting it backwards silently
inverts every historical result.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_nflverse_adapter.py`:

```python
import polars as pl

from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.resolve.resolver import TeamResolver

FRAME = pl.DataFrame(
    {
        "season": [2025],
        "week": [3],
        "gameday": ["2025-09-21"],
        "home_team": ["MIA"],
        "away_team": ["BUF"],
        "home_score": [24],
        "away_score": [17],
        "spread_line": [3.0],
        "total_line": [41.5],
    }
)


def fake_loader(seasons):
    return FRAME


def test_builds_canonical_game_ids():
    games = load_nfl_games([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert games[0].game_id == "nfl-2025-03-BUF-at-MIA"


def test_carries_final_scores():
    games = load_nfl_games([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert (games[0].home_score, games[0].away_score) == (24, 17)


def test_negates_nflverse_spread_to_home_perspective():
    # nflverse says home favored by 3 as +3.0; we store -3.0.
    result = load_nfl_closing_lines([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert result.lines[0].spread_home == -3.0


def test_closing_lines_are_tagged_as_such():
    result = load_nfl_closing_lines([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert result.lines[0].source == "nflverse"
    assert result.lines[0].book == "close"


def test_rows_without_a_spread_are_surfaced_not_silently_dropped():
    # Human ruling, 2026-08-11 (Task 9a): a missing per-book spread is counted
    # and named in `skipped`, never silently dropped. See MarketLinesResult.
    frame = FRAME.with_columns(pl.lit(None, dtype=pl.Float64).alias("spread_line"))
    result = load_nfl_closing_lines(
        [2025], resolver=TeamResolver.default(), loader=lambda s: frame
    )
    # A missing line must never become 0.0 — that would read as a pick'em.
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "nfl-2025-03-BUF-at-MIA" in result.skipped[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_nflverse_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/ingest/nflverse.py`:

```python
"""NFL games, results and closing lines from nflverse via nflreadpy.

nflverse expresses `spread_line` as a positive number when the home team is
favored. This project stores home-perspective spreads, where a home favorite is
negative. The negation below is the only place that conversion happens.

A row with no spread is never silently dropped: `load_nfl_closing_lines`
returns a `MarketLinesResult` and records a one-liner in `skipped` naming the
game, mirroring `ingest.cbs.ParseResult`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import polars as pl

from pickem.models import Game, MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

Loader = Callable[[Sequence[int]], pl.DataFrame]


def _default_loader(seasons: Sequence[int]) -> pl.DataFrame:
    import nflreadpy

    return nflreadpy.load_schedules(seasons=list(seasons))


def _kickoff(gameday: str) -> datetime:
    return datetime.fromisoformat(str(gameday)).replace(tzinfo=UTC)


def load_nfl_games(
    seasons: Sequence[int], *, resolver: TeamResolver, loader: Loader | None = None
) -> list[Game]:
    frame = (loader or _default_loader)(seasons)
    games: list[Game] = []
    for row in frame.iter_rows(named=True):
        home = resolver.resolve(row["home_team"], Sport.NFL)
        away = resolver.resolve(row["away_team"], Sport.NFL)
        games.append(
            Game(
                game_id=make_game_id(Sport.NFL, row["season"], row["week"], away, home),
                sport=Sport.NFL,
                season=row["season"],
                week=row["week"],
                kickoff_utc=_kickoff(row["gameday"]),
                home_team_id=home,
                away_team_id=away,
                home_score=row["home_score"],
                away_score=row["away_score"],
            )
        )
    return games


def load_nfl_closing_lines(
    seasons: Sequence[int], *, resolver: TeamResolver, loader: Loader | None = None
) -> MarketLinesResult:
    frame = (loader or _default_loader)(seasons)
    lines: list[MarketLine] = []
    skipped: list[str] = []
    for row in frame.iter_rows(named=True):
        # Resolve teams first (unknown teams must still raise, never become a
        # skipped line) so a missing spread can be named by game_id below.
        home = resolver.resolve(row["home_team"], Sport.NFL)
        away = resolver.resolve(row["away_team"], Sport.NFL)
        game_id = make_game_id(Sport.NFL, row["season"], row["week"], away, home)
        if row["spread_line"] is None:
            # never default a missing line to 0.0; that reads as a pick'em
            skipped.append(f"{game_id}: close — no spread")
            continue
        lines.append(
            MarketLine(
                game_id=game_id,
                source="nflverse",
                book="close",
                spread_home=-float(row["spread_line"]),
                total=row["total_line"],
                captured_at=_kickoff(row["gameday"]),
            )
        )
    return MarketLinesResult(lines=lines, skipped=skipped)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_nflverse_adapter.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Verify the sign convention against reality once, manually**

Run this and confirm a known home favorite comes out negative:

```bash
uv run python -c "
from pickem.ingest.nflverse import load_nfl_closing_lines
from pickem.resolve.resolver import TeamResolver
result = load_nfl_closing_lines([2024], resolver=TeamResolver.default())
print(result.lines[0])
"
```

If this raises `UnknownTeamError`, add the missing abbreviations to
`aliases.yaml` — that is the expected way the alias file grows.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/ingest/nflverse.py tests/test_nflverse_adapter.py src/pickem/resolve/aliases.yaml
git commit -m "feat: add nflverse adapter for NFL games and closing lines"
```

---

### Task 9: CFBD adapter

> **Amendment (Task 9a, human ruling 2026-08-11): surface dropped rows, don't
> silently skip them.** Same ruling as Task 8: `load_cfb_lines` now returns
> `MarketLinesResult` and records a one-liner in `skipped` for every provider
> row with no spread instead of a bare `continue`. The text below is amended
> in place.

**Files:**
- Create: `src/pickem/ingest/cfbd_source.py`
- Test: `tests/test_cfbd_adapter.py`

**Interfaces:**
- Consumes: `Game`, `MarketLine`, `MarketLinesResult`, `Sport`, `make_game_id`, `TeamResolver`
- Produces:
  - `CfbdConfig` pydantic model: `api_key: str`
  - `load_cfb_games(season, week, *, resolver, fetcher) -> list[Game]`
  - `load_cfb_lines(season, week, *, resolver, fetcher) -> MarketLinesResult`

`fetcher` is a callable injected for testing, returning plain dicts shaped like
the CFBD REST payload. Named `cfbd_source.py` rather than `cfbd.py` to avoid
shadowing the installed `cfbd` package.

**CFBD sign convention:** CFBD `spread` is negative when the **home** team is
favored, which already matches this project. No negation. The tests pin this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cfbd_adapter.py`:

```python
import pytest

from pickem.ingest.cfbd_source import load_cfb_games, load_cfb_lines
from pickem.resolve.resolver import TeamResolver

GAMES = [
    {
        "id": 401,
        "season": 2025,
        "week": 3,
        "start_date": "2025-09-20T23:30:00.000Z",
        "home_team": "Alabama",
        "away_team": "Ole Miss",
        "home_points": 27,
        "away_points": 24,
    }
]

LINES = [
    {
        "season": 2025,
        "week": 3,
        "home_team": "Alabama",
        "away_team": "Ole Miss",
        "lines": [
            {"provider": "DraftKings", "spread": -7.5, "over_under": 52.5},
            {"provider": "Bovada", "spread": -7.0, "over_under": 52.0},
        ],
    }
]


@pytest.fixture
def resolver():
    return TeamResolver.default()


def test_builds_canonical_game_ids(resolver):
    games = load_cfb_games(2025, 3, resolver=resolver, fetcher=lambda s, w: GAMES)
    assert games[0].game_id == "cfb-2025-03-MISS-at-BAMA"


def test_resolves_source_specific_school_naming(resolver):
    games = load_cfb_games(2025, 3, resolver=resolver, fetcher=lambda s, w: GAMES)
    assert games[0].away_team_id == "MISS"


def test_preserves_cfbd_home_negative_convention(resolver):
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: LINES)
    # CFBD already uses home-negative; no flip.
    assert {line.spread_home for line in result.lines} == {-7.5, -7.0}


def test_each_provider_becomes_its_own_book_row(resolver):
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: LINES)
    assert {line.book for line in result.lines} == {"DraftKings", "Bovada"}


def test_providers_without_a_spread_are_surfaced_not_silently_dropped(resolver):
    # Human ruling, 2026-08-11 (Task 9a): a missing per-book spread is counted
    # and named in `skipped`, never silently dropped. See MarketLinesResult.
    payload = [{**LINES[0], "lines": [{"provider": "X", "spread": None, "over_under": 50.0}]}]
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: payload)
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "cfb-2025-03-MISS-at-BAMA" in result.skipped[0]
    assert "X" in result.skipped[0]


def test_usable_providers_survive_alongside_skipped_ones(resolver):
    payload = [
        {
            **LINES[0],
            "lines": [
                {"provider": "DraftKings", "spread": -7.5, "over_under": 52.5},
                {"provider": "X", "spread": None, "over_under": 50.0},
            ],
        }
    ]
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: payload)
    assert [line.book for line in result.lines] == ["DraftKings"]
    assert len(result.skipped) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cfbd_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/ingest/cfbd_source.py`:

```python
"""CFB games, results and betting lines from CollegeFootballData.

CFBD already expresses spreads home-negative, matching this project's
convention, so no sign flip happens here. The tests pin that.

A provider row with no spread is never silently dropped: `load_cfb_lines`
returns a `MarketLinesResult` and records a one-liner in `skipped` naming the
game and book, mirroring `ingest.cbs.ParseResult`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from pickem.models import Game, MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

Fetcher = Callable[[int, int], list[dict]]


class CfbdConfig(BaseModel):
    api_key: str

    @classmethod
    def from_env(cls) -> CfbdConfig:
        key = os.environ.get("CFBD_API_KEY")
        if not key:
            raise RuntimeError("CFBD_API_KEY is not set")
        return cls(api_key=key)


def _client(config: CfbdConfig):
    import cfbd

    configuration = cfbd.Configuration(access_token=config.api_key)
    return cfbd.ApiClient(configuration)


def default_games_fetcher(config: CfbdConfig) -> Fetcher:
    import cfbd

    def fetch(season: int, week: int) -> list[dict]:
        api = cfbd.GamesApi(_client(config))
        return [g.to_dict() for g in api.get_games(year=season, week=week)]

    return fetch


def default_lines_fetcher(config: CfbdConfig) -> Fetcher:
    import cfbd

    def fetch(season: int, week: int) -> list[dict]:
        api = cfbd.BettingApi(_client(config))
        return [g.to_dict() for g in api.get_lines(year=season, week=week)]

    return fetch


def _kickoff(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def load_cfb_games(
    season: int, week: int, *, resolver: TeamResolver, fetcher: Fetcher
) -> list[Game]:
    games: list[Game] = []
    for row in fetcher(season, week):
        home = resolver.resolve(row["home_team"], Sport.CFB)
        away = resolver.resolve(row["away_team"], Sport.CFB)
        games.append(
            Game(
                game_id=make_game_id(Sport.CFB, season, week, away, home),
                sport=Sport.CFB,
                season=season,
                week=week,
                kickoff_utc=_kickoff(row["start_date"]),
                home_team_id=home,
                away_team_id=away,
                home_score=row.get("home_points"),
                away_score=row.get("away_points"),
            )
        )
    return games


def load_cfb_lines(
    season: int, week: int, *, resolver: TeamResolver, fetcher: Fetcher
) -> MarketLinesResult:
    lines: list[MarketLine] = []
    skipped: list[str] = []
    for row in fetcher(season, week):
        home = resolver.resolve(row["home_team"], Sport.CFB)
        away = resolver.resolve(row["away_team"], Sport.CFB)
        game_id = make_game_id(Sport.CFB, season, week, away, home)
        for provider in row.get("lines") or []:
            if provider.get("spread") is None:
                skipped.append(f"{game_id}: {provider.get('provider')} — no spread")
                continue
            lines.append(
                MarketLine(
                    game_id=game_id,
                    source="cfbd",
                    book=provider["provider"],
                    spread_home=float(provider["spread"]),
                    total=provider.get("over_under"),
                    captured_at=datetime.now(tz=UTC),
                )
            )
    return MarketLinesResult(lines=lines, skipped=skipped)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cfbd_adapter.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/ingest/cfbd_source.py tests/test_cfbd_adapter.py
git commit -m "feat: add CFBD adapter for college games and betting lines"
```

---

### Task 10: The Odds API adapter

> **Amendment (Task 9a, human ruling 2026-08-11): surface dropped rows, don't
> silently skip them.** Same ruling as Tasks 8 and 9, applied here before this
> task is ever built so it starts consistent: `fetch_spreads` returns
> `MarketLinesResult` and records a one-liner in `skipped` for a bookmaker with
> no `spreads` market and for an outcome with no `point`, instead of the two
> bare `continue`s the plan originally specified. The text below is amended in
> place.

**Files:**
- Create: `src/pickem/ingest/odds.py`
- Test: `tests/test_odds_adapter.py`

**Interfaces:**
- Consumes: `MarketLine`, `MarketLinesResult`, `Sport`, `make_game_id`, `TeamResolver`
- Produces:
  - `OddsApiError(Exception)`, `QuotaExhausted(OddsApiError)`
  - `OddsClient(api_key: str, *, transport: httpx.BaseTransport | None = None)`
  - `OddsClient.fetch_spreads(sport_key, *, resolver, sport, season, week, now) -> MarketLinesResult`
  - Module constants `NFL_KEY = "americanfootball_nfl"`, `CFB_KEY = "americanfootball_ncaaf"`

The Odds API returns each outcome as a team name plus a `point`. The home team's
`point` is already home-perspective (home favored by 3 is `-3.0`), so no flip.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_odds_adapter.py`:

```python
from datetime import UTC, datetime

import httpx
import pytest

from pickem.ingest.odds import NFL_KEY, OddsClient, QuotaExhausted
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

NOW = datetime(2025, 9, 21, 12, 0, tzinfo=UTC)

PAYLOAD = [
    {
        "id": "abc",
        "commence_time": "2025-09-21T17:00:00Z",
        "home_team": "Miami Dolphins",
        "away_team": "Buffalo Bills",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Miami Dolphins", "point": -6.0},
                            {"name": "Buffalo Bills", "point": 6.0},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Miami Dolphins", "point": -6.5},
                            {"name": "Buffalo Bills", "point": 6.5},
                        ],
                    }
                ],
            },
        ],
    }
]


def client_returning(payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)

    return OddsClient("key", transport=httpx.MockTransport(handler))


def fetch(client):
    return client.fetch_spreads(
        NFL_KEY,
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        now=NOW,
    )


def test_one_market_line_per_bookmaker():
    assert len(fetch(client_returning(PAYLOAD)).lines) == 2


def test_takes_the_home_teams_point_without_flipping():
    lines = {line.book: line.spread_home for line in fetch(client_returning(PAYLOAD)).lines}
    assert lines == {"pinnacle": -6.0, "draftkings": -6.5}


def test_builds_canonical_game_ids():
    assert fetch(client_returning(PAYLOAD)).lines[0].game_id == "nfl-2025-03-BUF-at-MIA"


def test_snapshot_is_stamped_with_capture_time():
    assert fetch(client_returning(PAYLOAD)).lines[0].captured_at == NOW


def test_quota_exhaustion_raises_a_distinct_error():
    # The caller must be able to fall back to cached snapshots on quota, but not on a bug.
    with pytest.raises(QuotaExhausted):
        fetch(client_returning({"message": "out of credits"}, status=401))


def test_bookmaker_without_a_spreads_market_is_surfaced_not_silently_dropped():
    # Human ruling, 2026-08-11 (Task 9a): a missing spreads market or missing
    # point is counted and named in `skipped`, never silently dropped.
    payload = [{**PAYLOAD[0], "bookmakers": [{"key": "x", "markets": [{"key": "totals", "outcomes": []}]}]}]
    result = fetch(client_returning(payload))
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "nfl-2025-03-BUF-at-MIA" in result.skipped[0]
    assert "x" in result.skipped[0]


def test_outcome_missing_a_point_is_surfaced_not_silently_dropped():
    payload = [
        {
            **PAYLOAD[0],
            "bookmakers": [
                {
                    "key": "y",
                    "markets": [
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Miami Dolphins", "point": None},
                                {"name": "Buffalo Bills", "point": None},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    result = fetch(client_returning(payload))
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "nfl-2025-03-BUF-at-MIA" in result.skipped[0]
    assert "y" in result.skipped[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_odds_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/ingest/odds.py`:

```python
"""The Odds API client for live market spreads.

Quota exhaustion is a distinct exception so the CLI can fall back to the most
recent cached snapshot and stamp the report with its age, while a genuine bug
still fails loudly.

A bookmaker with no `spreads` market, or an outcome with no `point`, is never
silently dropped: `fetch_spreads` returns a `MarketLinesResult` and records a
one-liner in `skipped` naming the game and book, mirroring
`ingest.cbs.ParseResult`.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from pickem.models import MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

BASE_URL = "https://api.the-odds-api.com/v4"
NFL_KEY = "americanfootball_nfl"
CFB_KEY = "americanfootball_ncaaf"


class OddsApiError(RuntimeError):
    """The odds feed could not be read."""


class QuotaExhausted(OddsApiError):
    """The API key is out of credits or unauthorized."""


class OddsClient:
    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._api_key = api_key
        self._client = httpx.Client(base_url=BASE_URL, transport=transport, timeout=20.0)

    def fetch_spreads(
        self,
        sport_key: str,
        *,
        resolver: TeamResolver,
        sport: Sport,
        season: int,
        week: int,
        now: datetime,
    ) -> MarketLinesResult:
        response = self._client.get(
            f"/sports/{sport_key}/odds",
            params={
                "apiKey": self._api_key,
                "regions": "us",
                "markets": "spreads",
                "oddsFormat": "american",
            },
        )
        if response.status_code in (401, 429):
            raise QuotaExhausted(f"odds api returned {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise OddsApiError(f"odds api returned {response.status_code}: {response.text}")

        lines: list[MarketLine] = []
        skipped: list[str] = []
        for event in response.json():
            home_name = event["home_team"]
            home = resolver.resolve(home_name, sport)
            away = resolver.resolve(event["away_team"], sport)
            game_id = make_game_id(sport, season, week, away, home)

            for book in event.get("bookmakers", []):
                book_key = book.get("key")
                spreads = next(
                    (m for m in book.get("markets", []) if m.get("key") == "spreads"), None
                )
                if spreads is None:
                    skipped.append(f"{game_id}: {book_key} — no spreads market")
                    continue
                outcome = next(
                    (o for o in spreads.get("outcomes", []) if o.get("name") == home_name), None
                )
                if outcome is None or outcome.get("point") is None:
                    skipped.append(f"{game_id}: {book_key} — no spread")
                    continue
                lines.append(
                    MarketLine(
                        game_id=game_id,
                        source="oddsapi",
                        book=book_key,
                        # Already home-perspective; do not flip.
                        spread_home=float(outcome["point"]),
                        captured_at=now,
                    )
                )
        return MarketLinesResult(lines=lines, skipped=skipped)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_odds_adapter.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/ingest/odds.py tests/test_odds_adapter.py
git commit -m "feat: add Odds API adapter with distinct quota-exhaustion error"
```

---

### Task 11: Backtest statistics

**Files:**
- Create: `src/pickem/backtest/__init__.py`, `src/pickem/backtest/stats.py`
- Test: `tests/test_backtest_stats.py`

**Interfaces:**
- Consumes: `Side`
- Produces:
  - `Result` (enum: `WIN="win"`, `LOSS="loss"`, `PUSH="push"`)
  - `grade_pick(side: Side, home_margin: int, spread_home: float) -> Result`
  - `wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]`

A 54% hit rate on 200 games is indistinguishable from noise. The interval is
what stops the report from flattering the strategy.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backtest_stats.py`:

```python
import pytest

from pickem.backtest.stats import Result, grade_pick, wilson_interval
from pickem.models import Side


def test_home_favorite_covering_is_a_home_win():
    # Home laying 3, wins by 7.
    assert grade_pick(Side.HOME, home_margin=7, spread_home=-3.0) is Result.WIN


def test_home_favorite_failing_to_cover_is_a_home_loss():
    assert grade_pick(Side.HOME, home_margin=1, spread_home=-3.0) is Result.LOSS


def test_away_dog_covering_is_an_away_win():
    assert grade_pick(Side.AWAY, home_margin=1, spread_home=-3.0) is Result.WIN


def test_exact_landing_on_the_number_is_a_push_for_both_sides():
    assert grade_pick(Side.HOME, home_margin=3, spread_home=-3.0) is Result.PUSH
    assert grade_pick(Side.AWAY, home_margin=3, spread_home=-3.0) is Result.PUSH


def test_home_underdog_covering_by_losing_close():
    # Home getting 7, loses by 3 -> home covers.
    assert grade_pick(Side.HOME, home_margin=-3, spread_home=7.0) is Result.WIN


def test_half_point_lines_never_push():
    assert grade_pick(Side.HOME, home_margin=3, spread_home=-3.5) is Result.LOSS
    assert grade_pick(Side.AWAY, home_margin=3, spread_home=-3.5) is Result.WIN


def test_wilson_interval_brackets_the_point_estimate():
    low, high = wilson_interval(54, 100)
    assert low < 0.54 < high


def test_wilson_interval_is_wide_on_small_samples():
    low, high = wilson_interval(54, 100)
    # 54/100 cannot be distinguished from a coin flip.
    assert low < 0.5


def test_wilson_interval_narrows_as_evidence_accumulates():
    small = wilson_interval(540, 1000)
    large = wilson_interval(5400, 10000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_interval_of_no_trials_is_the_full_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_rejects_impossible_input():
    with pytest.raises(ValueError):
        wilson_interval(10, 5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backtest_stats.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/backtest/__init__.py` (empty) and `src/pickem/backtest/stats.py`:

```python
"""Grading and honest interval estimation for backtest results."""

from __future__ import annotations

import math
from enum import StrEnum

from pickem.models import Side


class Result(StrEnum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"


def grade_pick(side: Side, home_margin: int, spread_home: float) -> Result:
    """Grade a pick against a home-perspective spread.

    `home_margin` is home_score - away_score. The home side covers when
    `home_margin + spread_home > 0`; exactly zero is a push.
    """
    cover = home_margin + spread_home
    if cover == 0:
        return Result.PUSH
    home_covered = cover > 0
    picked_home = side is Side.HOME
    return Result.WIN if home_covered == picked_home else Result.LOSS


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because it stays sane at small
    samples and near the boundaries — exactly the regime a season of picks
    lives in.
    """
    if successes > trials or successes < 0 or trials < 0:
        raise ValueError(f"impossible input: {successes} successes in {trials} trials")
    if trials == 0:
        return (0.0, 1.0)

    p = successes / trials
    denominator = 1 + z**2 / trials
    center = (p + z**2 / (2 * trials)) / denominator
    spread = z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / denominator
    return (max(0.0, center - spread), min(1.0, center + spread))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backtest_stats.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest tests/test_backtest_stats.py
git commit -m "feat: add ATS grading and Wilson interval estimation"
```

---

### Task 12: Backtest runner

**Files:**
- Create: `src/pickem/backtest/runner.py`
- Test: `tests/test_backtest_runner.py`

**Interfaces:**
- Consumes: `Game`, `LeagueLine`, `MarketLine`, `Tier`, `compute_edge`, `Thresholds`, `grade_pick`, `Result`, `wilson_interval`
- Produces:
  - `TierRecord` pydantic model: `tier: Tier | None` (None on the overall row), `wins: int`, `losses: int`, `pushes: int`, `hit_rate: float`, `ci_low: float`, `ci_high: float`
  - `BacktestReport` pydantic model: `overall: TierRecord`, `by_tier: list[TierRecord]`, `assumptions: list[str]`, `skipped: list[str]` (each excluded game ID and reason, deterministically ordered)
  - `run_backtest(games, openers, closers, thresholds: Thresholds | None = None) -> BacktestReport`

`openers` and `closers` are `Sequence[MarketLine]`; openers stand in for the
frozen CBS line and closers for the market at submission time. That proxy is
stated in `assumptions` and printed with every report, not buried in the spec.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backtest_runner.py`:

```python
from datetime import UTC, datetime

from pickem.backtest.runner import run_backtest
from pickem.models import Game, MarketLine, Sport, Tier

T_OPEN = datetime(2025, 9, 16, tzinfo=UTC)
T_CLOSE = datetime(2025, 9, 21, tzinfo=UTC)


def game(gid: str, home_score: int, away_score: int, week: int = 3) -> Game:
    return Game(
        game_id=gid,
        sport=Sport.NFL,
        season=2025,
        week=week,
        kickoff_utc=T_CLOSE,
        home_team_id="MIA",
        away_team_id="BUF",
        home_score=home_score,
        away_score=away_score,
    )


def line(gid: str, spread: float, book: str, at: datetime) -> MarketLine:
    return MarketLine(
        game_id=gid, source="test", book=book, spread_home=spread, captured_at=at
    )


def test_a_correct_strong_pick_is_recorded_as_a_win():
    gid = "nfl-2025-03-BUF-at-MIA"
    # Opener -3, close -6 (market moved to home), home wins by 10 -> home covers -3.
    report = run_backtest(
        games=[game(gid, home_score=27, away_score=17)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.wins == 1
    assert report.overall.losses == 0


def test_an_incorrect_pick_is_recorded_as_a_loss():
    gid = "nfl-2025-03-BUF-at-MIA"
    # Market moved to home, but home only wins by 1 -> fails to cover -3.
    report = run_backtest(
        games=[game(gid, home_score=21, away_score=20)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.losses == 1


def test_pushes_are_excluded_from_the_hit_rate():
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, home_score=20, away_score=17)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.pushes == 1
    assert report.overall.wins + report.overall.losses == 0


def test_unplayed_games_are_excluded():
    gid = "nfl-2025-03-BUF-at-MIA"
    unplayed = Game(
        game_id=gid,
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=T_CLOSE,
        home_team_id="MIA",
        away_team_id="BUF",
    )
    report = run_backtest(
        games=[unplayed],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.wins + report.overall.losses + report.overall.pushes == 0


def test_games_without_an_opener_are_excluded():
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, 27, 17)], openers=[], closers=[line(gid, -6.0, "close", T_CLOSE)]
    )
    assert report.overall.wins == 0


def test_excluded_games_report_each_reason_in_deterministic_order():
    unplayed_gid = "nfl-2025-01-BUF-at-MIA"
    missing_opener_gid = "nfl-2025-02-BUF-at-MIA"
    missing_market_gid = "nfl-2025-03-BUF-at-MIA"
    unplayed = Game(
        game_id=unplayed_gid,
        sport=Sport.NFL,
        season=2025,
        week=1,
        kickoff_utc=T_CLOSE,
        home_team_id="MIA",
        away_team_id="BUF",
    )
    report = run_backtest(
        games=[
            game(missing_market_gid, 27, 17, 3),
            game(missing_opener_gid, 27, 17, 2),
            unplayed,
        ],
        openers=[
            line(unplayed_gid, -3.0, "open", T_OPEN),
            line(missing_market_gid, -3.0, "open", T_OPEN),
        ],
        closers=[line(unplayed_gid, -6.0, "close", T_CLOSE)],
    )
    assert [entry.split(":", maxsplit=1)[0] for entry in report.skipped] == [
        unplayed_gid,
        missing_opener_gid,
        missing_market_gid,
    ]
    reasons = [entry.lower() for entry in report.skipped]
    assert "unplayed" in reasons[0]
    assert "missing" in reasons[1] and "open" in reasons[1]
    assert "missing" in reasons[2] and "clos" in reasons[2]


def test_results_are_broken_out_by_tier():
    strong = "nfl-2025-03-BUF-at-MIA"
    lean = "nfl-2025-04-BUF-at-MIA"
    report = run_backtest(
        games=[game(strong, 27, 17), game(lean, 27, 17, week=4)],
        openers=[line(strong, -3.0, "open", T_OPEN), line(lean, -3.0, "open", T_OPEN)],
        closers=[line(strong, -6.0, "close", T_CLOSE), line(lean, -4.5, "close", T_CLOSE)],
    )
    tiers = {record.tier for record in report.by_tier}
    assert Tier.STRONG in tiers
    assert Tier.LEAN in tiers


def test_report_states_its_proxy_assumption():
    report = run_backtest(games=[], openers=[], closers=[])
    joined = " ".join(report.assumptions).lower()
    assert "open" in joined and "frozen" in joined


def test_backtest_is_deterministic():
    gid = "nfl-2025-03-BUF-at-MIA"
    args = dict(
        games=[game(gid, 27, 17)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert run_backtest(**args) == run_backtest(**args)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backtest_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/backtest/runner.py`:

```python
"""Replay historical weeks through the live strategy code.

The backtest calls the same `compute_edge` the weekly report calls. If the two
ever diverge, the backtest stops being evidence about the thing being shipped.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from pydantic import BaseModel

from pickem.backtest.stats import Result, grade_pick, wilson_interval
from pickem.edge.divergence import Thresholds, compute_edge
from pickem.models import Game, LeagueLine, MarketLine, Tier

ASSUMPTIONS = [
    "The frozen league line is proxied by the market OPENING line; the real CBS "
    "number was never recorded historically and tracks the opener closely but is "
    "not identical.",
    "The market at submission time is proxied by the CLOSING line.",
    "Pushes are excluded from the hit rate rather than counted as half-wins.",
]


class TierRecord(BaseModel):
    tier: Tier | None
    wins: int
    losses: int
    pushes: int
    hit_rate: float
    ci_low: float
    ci_high: float


class BacktestReport(BaseModel):
    overall: TierRecord
    by_tier: list[TierRecord]
    assumptions: list[str]
    skipped: list[str]


def _record(tier: Tier | None, results: Sequence[Result]) -> TierRecord:
    wins = sum(1 for r in results if r is Result.WIN)
    losses = sum(1 for r in results if r is Result.LOSS)
    pushes = sum(1 for r in results if r is Result.PUSH)
    decided = wins + losses
    low, high = wilson_interval(wins, decided)
    return TierRecord(
        tier=tier,
        wins=wins,
        losses=losses,
        pushes=pushes,
        hit_rate=(wins / decided) if decided else 0.0,
        ci_low=low,
        ci_high=high,
    )


def run_backtest(
    games: Sequence[Game],
    openers: Sequence[MarketLine],
    closers: Sequence[MarketLine],
    thresholds: Thresholds | None = None,
) -> BacktestReport:
    openers_by_game: dict[str, MarketLine] = {line.game_id: line for line in openers}
    closers_by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in closers:
        closers_by_game[line.game_id].append(line)

    all_results: list[Result] = []
    by_tier: dict[Tier, list[Result]] = defaultdict(list)
    skipped: list[str] = []

    for game in sorted(games, key=lambda g: (g.season, g.week, g.game_id)):
        if game.home_score is None or game.away_score is None:
            skipped.append(f"{game.game_id}: unplayed game (missing final score)")
            continue
        opener = openers_by_game.get(game.game_id)
        if opener is None:
            skipped.append(f"{game.game_id}: missing opening line for frozen proxy")
            continue

        frozen = LeagueLine(
            game_id=game.game_id,
            season=game.season,
            week=game.week,
            spread_home=opener.spread_home,
            posted_at=opener.captured_at,
        )
        edge = compute_edge(frozen, closers_by_game.get(game.game_id, []), thresholds)
        if edge.tier is Tier.NO_MARKET:
            skipped.append(f"{game.game_id}: missing closing market line")
            continue

        result = grade_pick(
            edge.side, game.home_score - game.away_score, frozen.spread_home
        )
        all_results.append(result)
        by_tier[edge.tier].append(result)

    return BacktestReport(
        overall=_record(None, all_results),
        by_tier=[_record(tier, by_tier[tier]) for tier in sorted(by_tier, key=lambda t: t.value)],
        assumptions=list(ASSUMPTIONS),
        skipped=skipped,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backtest_runner.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/runner.py tests/test_backtest_runner.py
git commit -m "feat: add backtest runner replaying history through live edge code"
```

---

### Task 13: Pick sheet report

**Files:**
- Create: `src/pickem/report/__init__.py`, `src/pickem/report/sheet.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Edge`, `Game`, `Tier`, `Side`, `rank_edges`
- Produces:
  - `render_sheet(edges: Sequence[Edge], games: Sequence[Game], *, generated_at: datetime, provenance: str, snapshot_age_minutes: float | None = None) -> str` returning markdown; every sheet displays provenance and either snapshot age or an explicit unknown/unavailable state

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report.py`:

```python
from datetime import UTC, datetime

from pickem.models import Edge, Game, Side, Sport, Tier
from pickem.report.sheet import render_sheet

NOW = datetime(2025, 9, 21, 12, 0, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"
PROVENANCE = "CBS frozen league lines vs latest stored market consensus"

GAME = Game(
    game_id=GID,
    sport=Sport.NFL,
    season=2025,
    week=3,
    kickoff_utc=NOW,
    home_team_id="MIA",
    away_team_id="BUF",
)


def edge(tier: Tier = Tier.STRONG, delta: float = 3.0, side: Side = Side.HOME) -> Edge:
    return Edge(
        game_id=GID,
        side=side,
        delta=delta,
        tier=tier,
        league_spread=-3.0,
        market_spread=-6.0,
        rationale="league -3.0 vs market -6.0: 3.0 pts toward home",
    )


def test_names_the_picked_team_not_just_a_side():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "MIA" in sheet


def test_shows_both_numbers_so_a_pick_can_be_audited():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "-3.0" in sheet and "-6.0" in sheet


def test_orders_by_divergence_strongest_first():
    strong = edge(Tier.STRONG, delta=6.0)
    weak = edge(Tier.COINFLIP, delta=0.5)
    sheet = render_sheet([weak, strong], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert sheet.index("6.0") < sheet.index("0.5")


def test_stamps_snapshot_age_when_data_is_stale():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE, snapshot_age_minutes=180.0)
    assert "180" in sheet


def test_flags_games_with_no_market_line():
    sheet = render_sheet([edge(Tier.NO_MARKET, delta=0.0)], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "no_market" in sheet.lower() or "no market" in sheet.lower()


def test_displays_provenance_in_every_sheet():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert PROVENANCE in sheet


def test_stamps_unknown_snapshot_age_when_unavailable():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "market snapshot age: **unknown" in sheet.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL until `render_sheet` accepts provenance and renders provenance plus unknown snapshot age.

- [ ] **Step 3: Write the implementation**

Create `src/pickem/report/__init__.py` (empty) and `src/pickem/report/sheet.py`:

```python
"""Render the weekly pick sheet.

Every row carries the two numbers that produced the pick, so the user can audit
any selection without opening the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pickem.edge.divergence import rank_edges
from pickem.models import Edge, Game, Side


def render_sheet(
    edges: Sequence[Edge],
    games: Sequence[Game],
    *,
    generated_at: datetime,
    provenance: str,
    snapshot_age_minutes: float | None = None,
) -> str:
    by_id = {game.game_id: game for game in games}

    header = [
        f"# Pick Sheet — generated {generated_at:%Y-%m-%d %H:%M UTC}",
        f"> Provenance: {provenance}",
    ]
    if snapshot_age_minutes is not None:
        header.append(
            f"> Market snapshot is **{snapshot_age_minutes:.0f} minutes old**. "
            "Re-run `pickem poll-odds` for fresher numbers."
        )
    else:
        header.append("> Market snapshot age: **unknown/unavailable**.")
    header.append("")

    rows = [
        "| # | Matchup | Pick | Tier | Edge | League | Market |",
        "|---|---------|------|------|------|--------|--------|",
    ]
    for index, edge in enumerate(rank_edges(edges), start=1):
        game = by_id.get(edge.game_id)
        if game is None:
            matchup, pick = edge.game_id, edge.side.value
        else:
            matchup = f"{game.away_team_id} at {game.home_team_id}"
            pick = game.home_team_id if edge.side is Side.HOME else game.away_team_id
        market = "—" if edge.market_spread is None else f"{edge.market_spread:+.1f}"
        rows.append(
            f"| {index} | {matchup} | **{pick}** | {edge.tier.value} | "
            f"{abs(edge.delta):.1f} | {edge.league_spread:+.1f} | {market} |"
        )

    return "\n".join([*header, *rows, ""])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report tests/test_report.py
git commit -m "feat: render auditable ranked pick sheet as markdown"
```

---

### Task 14: CLI wiring

> **Amendment (Task 9a, human ruling 2026-08-11): surface dropped rows, don't
> silently skip them.** `load_nfl_closing_lines` and `OddsClient.fetch_spreads`
> now return `MarketLinesResult` rather than a bare list (Tasks 8 and 10). The
> `backfill` and `poll-odds` commands below consume `.lines` and print the
> skipped count loudly (`typer.secho(..., fg="yellow")`) when non-empty —
> surfacing dropped rows to a human is the entire point of the ruling. The text
> below is amended in place.
>
> **Amendment (Task 14 Fix Round 1, human ruling):** CBS parsing is atomic at
> the command boundary. After parsing, `ingest-cbs` prints the parse summary
> and every `ParseResult.skipped` row in yellow, then exits non-zero before
> opening or mutating the database. A mixed valid/ambiguous paste must never
> partially ingest a week.

**Files:**
- Create: `src/pickem/cli.py`, `src/pickem/config.py`
- Modify: `pyproject.toml` (add the console script entry point)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: every module above
- Produces: `app` (typer app) with commands `ingest-cbs`, `poll-odds`, `report`, `sync-results`, `backfill`, `backtest`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from pickem.cli import app

runner = CliRunner()


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["ingest-cbs", "poll-odds", "report", "sync-results", "backfill", "backtest"]:
        assert command in result.stdout


def test_ingest_cbs_reports_a_parse_summary(tmp_path):
    paste = tmp_path / "week3.txt"
    paste.write_text("Buffalo Bills at Miami Dolphins -3.0\n")
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        ["ingest-cbs", "--file", str(paste), "--sport", "nfl", "--season", "2025",
         "--week", "3", "--db", str(db)],
    )
    assert result.exit_code == 0
    assert "1" in result.stdout


def test_ingest_cbs_exits_nonzero_on_an_unknown_team(tmp_path):
    paste = tmp_path / "bad.txt"
    paste.write_text("Fictional State Aardvarks at Miami Dolphins -3.0\n")
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        ["ingest-cbs", "--file", str(paste), "--sport", "nfl", "--season", "2025",
         "--week", "3", "--db", str(db)],
    )
    # A partially-ingested week looks like success. It must not be allowed.
    assert result.exit_code != 0


def test_ingest_cbs_rejects_a_partially_parsed_block_without_creating_a_database(tmp_path):
    paste = tmp_path / "mixed.txt"
    paste.write_text(
        "Buffalo Bills at Miami Dolphins -3.0\n"
        "Kansas City Chiefs -6.5 at New York Jets 45.5\n"
    )
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        ["ingest-cbs", "--file", str(paste), "--sport", "nfl", "--season", "2025",
         "--week", "3", "--db", str(db)],
    )
    assert result.exit_code != 0
    assert "skipped" in result.stdout
    assert "Kansas City Chiefs -6.5 at New York Jets 45.5" in result.stdout
    assert not db.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.cli'`

- [ ] **Step 3: Write the config module**

Create `src/pickem/config.py`:

```python
"""Environment-sourced settings. Secrets never live in the repo."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DB = Path("data/pickem.duckdb")


def odds_api_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY is not set")
    return key


def cfbd_api_key() -> str:
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        raise RuntimeError("CFBD_API_KEY is not set")
    return key
```

- [ ] **Step 4: Write the CLI**

Create `src/pickem/cli.py`:

```python
"""Command-line surface. Wiring only — no strategy logic lives here."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from pickem import config
from pickem.backtest.runner import run_backtest
from pickem.edge.divergence import compute_edge
from pickem.ingest.cbs import parse_cbs_block
from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.ingest.odds import CFB_KEY, NFL_KEY, OddsClient, QuotaExhausted
from pickem.models import Game, Sport, make_game_id
from pickem.report.sheet import render_sheet
from pickem.resolve.resolver import TeamResolver, UnknownTeamError
from pickem.store.db import Store

app = typer.Typer(help="Rank pick'em selections by frozen-line vs market divergence.")


def _store(db: Path) -> Store:
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db)
    store.init_schema()
    return store


@app.command("ingest-cbs")
def ingest_cbs(
    file: Path = typer.Option(None, help="File containing the pasted block; omit to read stdin"),
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Parse the CBS pick sheet paste into frozen league lines."""
    text = file.read_text() if file else sys.stdin.read()
    resolver = TeamResolver.default()
    now = datetime.now(tz=UTC)

    try:
        parsed = parse_cbs_block(
            text, resolver=resolver, sport=sport, season=season, week=week, posted_at=now
        )
    except UnknownTeamError as exc:
        typer.secho(f"unresolved team: {exc}", fg="red", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"parsed {len(parsed.lines)} games for {sport.value} {season} week {week}")
    for skipped in parsed.skipped:
        typer.secho(f"  skipped: {skipped!r}", fg="yellow")
    if parsed.skipped:
        raise typer.Exit(code=1)

    store = _store(db)
    games = [
        Game(
            game_id=make_game_id(sport, season, week, away, home),
            sport=sport,
            season=season,
            week=week,
            kickoff_utc=now,
            home_team_id=home,
            away_team_id=away,
        )
        for away, home in parsed.matchups
    ]
    store.upsert_games(games)
    store.upsert_league_lines(parsed.lines)

    typer.echo(f"ingested {len(parsed.lines)} games for {sport.value} {season} week {week}")
    store.close()


@app.command("poll-odds")
def poll_odds(
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Append a market snapshot for the active week."""
    client = OddsClient(config.odds_api_key())
    key = NFL_KEY if sport is Sport.NFL else CFB_KEY
    try:
        result = client.fetch_spreads(
            key,
            resolver=TeamResolver.default(),
            sport=sport,
            season=season,
            week=week,
            now=datetime.now(tz=UTC),
        )
    except QuotaExhausted as exc:
        typer.secho(f"odds quota exhausted: {exc}; reports will use cached snapshots",
                    fg="yellow", err=True)
        raise typer.Exit(code=2) from exc

    store = _store(db)
    store.append_market_lines(result.lines)
    typer.echo(f"appended {len(result.lines)} market lines")
    if result.skipped:
        typer.secho(f"skipped {len(result.skipped)} book rows with no spread:", fg="yellow")
        for skipped in result.skipped:
            typer.secho(f"  skipped: {skipped}", fg="yellow")
    store.close()


@app.command("report")
def report(
    sport: Sport = typer.Option(...),
    season: int = typer.Option(...),
    week: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
    out: Path = typer.Option(None, help="Also write the sheet to this markdown file"),
) -> None:
    """Render the ranked pick sheet."""
    store = _store(db)
    now = datetime.now(tz=UTC)
    league_lines = store.league_lines_for_week(sport, season, week)
    games = store.games_for_week(sport, season, week)

    edges = []
    newest: datetime | None = None
    for line in league_lines:
        market = store.market_lines_for(line.game_id)
        for snapshot in market:
            if newest is None or snapshot.captured_at > newest:
                newest = snapshot.captured_at
        edges.append(compute_edge(line, market))

    age = (now - newest).total_seconds() / 60 if newest else None
    sheet = render_sheet(
        edges,
        games,
        generated_at=now,
        provenance="CBS frozen league lines vs latest stored market consensus",
        snapshot_age_minutes=age,
    )
    typer.echo(sheet)
    if out:
        out.write_text(sheet)
    store.close()


@app.command("sync-results")
def sync_results(
    season: int = typer.Option(...),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Pull final scores into the store."""
    store = _store(db)
    games = load_nfl_games([season], resolver=TeamResolver.default())
    store.upsert_games(games)
    typer.echo(f"synced {len(games)} NFL games for {season}")
    store.close()


@app.command("backfill")
def backfill(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """One-time historical load for the backtest."""
    store = _store(db)
    resolver = TeamResolver.default()
    seasons = list(range(start, end + 1))
    games = load_nfl_games(seasons, resolver=resolver)
    closers = load_nfl_closing_lines(seasons, resolver=resolver)
    store.upsert_games(games)
    store.append_market_lines(closers.lines)
    typer.echo(f"backfilled {len(games)} games and {len(closers.lines)} closing lines")
    if closers.skipped:
        typer.secho(
            f"skipped {len(closers.skipped)} rows with no spread — see below", fg="yellow"
        )
        for skipped in closers.skipped:
            typer.secho(f"  skipped: {skipped}", fg="yellow")
    typer.secho(
        "openers are still missing; run the Odds API historical backfill to complete the pair",
        fg="yellow",
    )
    store.close()


@app.command("backtest")
def backtest(
    start: int = typer.Option(2020, "--from"),
    end: int = typer.Option(2025, "--to"),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Replay history through the live edge code."""
    store = _store(db)
    games: list[Game] = []
    for season in range(start, end + 1):
        for week in range(1, 23):
            games.extend(store.games_for_week(Sport.NFL, season, week))

    openers, closers = [], []
    for game in games:
        for line in store.market_lines_for(game.game_id):
            (openers if line.book == "open" else closers).append(line)

    result = run_backtest(games, openers, closers)
    typer.echo(
        f"overall: {result.overall.wins}-{result.overall.losses}-{result.overall.pushes} "
        f"({result.overall.hit_rate:.1%}, 95% CI "
        f"{result.overall.ci_low:.1%}–{result.overall.ci_high:.1%})"
    )
    for record in result.by_tier:
        typer.echo(
            f"  {record.tier.value:<10} {record.wins}-{record.losses}-{record.pushes} "
            f"({record.hit_rate:.1%}, CI {record.ci_low:.1%}–{record.ci_high:.1%})"
        )
    typer.echo("\nAssumptions:")
    for assumption in result.assumptions:
        typer.echo(f"  - {assumption}")
    store.close()


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Register the console script**

Add to `pyproject.toml`:

```toml
[project.scripts]
pickem = "pickem.cli:app"
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS, all tests across every module

- [ ] **Step 7: Lint**

Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean. Run `uv run ruff format src tests` if formatting fails.

- [ ] **Step 8: Commit**

```bash
git add src/pickem/cli.py src/pickem/config.py tests/test_cli.py pyproject.toml
git commit -m "feat: add typer CLI wiring ingest, polling, report and backtest"
```

---

### Task 15: Wire tiebreaks into the pick sheet

Task 6 emits `COINFLIP` and `NO_MARKET` edges; Task 7 built the rating that
resolves them. Nothing connects the two yet, so those games would reach the
sheet with an arbitrary side. This task closes that loop and adds the league
tiebreaker projection.

**Files:**
- Create: `src/pickem/edge/pipeline.py`
- Modify: `src/pickem/cli.py` (the `report` command)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Edge`, `Game`, `Side`, `Tier`, `LeagueLine`, `MarketLine`, `build_ratings`, `projected_margin`, `tiebreak_side`, `EloConfig`
- Produces:
  - `apply_tiebreaks(edges: Sequence[Edge], games: Sequence[Game], history: Sequence[Game], config: EloConfig | None = None) -> list[Edge]`
  - `predict_tiebreaker_total(market_lines: Sequence[MarketLine]) -> float | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
from datetime import UTC, datetime

from pickem.edge.pipeline import apply_tiebreaks, predict_tiebreaker_total
from pickem.models import Edge, Game, MarketLine, Side, Sport, Tier

NOW = datetime(2025, 9, 21, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


def game(gid=GID, week=3, home_score=None, away_score=None) -> Game:
    return Game(
        game_id=gid,
        sport=Sport.NFL,
        season=2025,
        week=week,
        kickoff_utc=NOW,
        home_team_id="MIA",
        away_team_id="BUF",
        home_score=home_score,
        away_score=away_score,
    )


def edge(tier: Tier, side: Side = Side.HOME, league_spread: float = -3.0) -> Edge:
    return Edge(
        game_id=GID,
        side=side,
        delta=0.0,
        tier=tier,
        league_spread=league_spread,
        market_spread=None,
        rationale="original",
    )


# MIA has beaten BUF repeatedly, so the rating strongly favors MIA.
HISTORY = [game(gid=f"nfl-2025-{w:02d}-BUF-at-MIA", week=w, home_score=30, away_score=10)
           for w in range(1, 4)]


def test_strong_edges_are_left_untouched():
    original = edge(Tier.STRONG, side=Side.AWAY)
    result = apply_tiebreaks([original], [game()], HISTORY)
    # A real divergence signal must never be overridden by the weak rating.
    assert result[0].side is Side.AWAY
    assert result[0].rationale == "original"


def test_lean_edges_are_left_untouched():
    result = apply_tiebreaks([edge(Tier.LEAN, side=Side.AWAY)], [game()], HISTORY)
    assert result[0].side is Side.AWAY


def test_coinflip_is_resolved_by_the_rating():
    # Rating loves MIA; the board only asks MIA to win by 3.
    result = apply_tiebreaks([edge(Tier.COINFLIP, side=Side.AWAY)], [game()], HISTORY)
    assert result[0].side is Side.HOME


def test_no_market_is_resolved_by_the_rating():
    result = apply_tiebreaks([edge(Tier.NO_MARKET, side=Side.AWAY)], [game()], HISTORY)
    assert result[0].side is Side.HOME


def test_resolved_edges_say_the_rating_decided_them():
    result = apply_tiebreaks([edge(Tier.COINFLIP)], [game()], HISTORY)
    assert "rating" in result[0].rationale.lower()


def test_a_heavy_number_flips_the_rating_to_the_dog():
    # Same strong MIA rating, but the board asks MIA to win by 40.
    result = apply_tiebreaks(
        [edge(Tier.COINFLIP, side=Side.HOME, league_spread=-40.0)], [game()], HISTORY
    )
    assert result[0].side is Side.AWAY


def test_edge_for_an_unknown_game_is_passed_through_unchanged():
    result = apply_tiebreaks([edge(Tier.COINFLIP)], [], HISTORY)
    assert result[0].rationale == "original"


def test_tiebreaker_total_uses_the_market():
    lines = [
        MarketLine(game_id=GID, source="oddsapi", book="a", spread_home=-3.0,
                   total=44.5, captured_at=NOW),
        MarketLine(game_id=GID, source="oddsapi", book="b", spread_home=-3.0,
                   total=45.5, captured_at=NOW),
    ]
    assert predict_tiebreaker_total(lines) == 45.0


def test_tiebreaker_total_is_none_without_a_market():
    assert predict_tiebreaker_total([]) is None


def test_tiebreaker_total_ignores_books_without_a_total():
    lines = [
        MarketLine(game_id=GID, source="oddsapi", book="a", spread_home=-3.0,
                   total=None, captured_at=NOW),
        MarketLine(game_id=GID, source="oddsapi", book="b", spread_home=-3.0,
                   total=45.0, captured_at=NOW),
    ]
    assert predict_tiebreaker_total(lines) == 45.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.edge.pipeline'`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/edge/pipeline.py`:

```python
"""Resolve the games divergence cannot answer.

The rating is only ever allowed to decide COINFLIP and NO_MARKET games. A real
divergence signal is never overridden by it — the rating is deliberately weaker
than the market, and letting it outvote a moved line would throw away the edge
this system exists to capture.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from pickem.edge.elo import EloConfig, build_ratings, projected_margin, tiebreak_side
from pickem.models import Edge, Game, MarketLine, Tier

_TIEBREAK_TIERS = {Tier.COINFLIP, Tier.NO_MARKET}


def apply_tiebreaks(
    edges: Sequence[Edge],
    games: Sequence[Game],
    history: Sequence[Game],
    config: EloConfig | None = None,
) -> list[Edge]:
    config = config or EloConfig()
    ratings = build_ratings(history, config)
    by_id = {game.game_id: game for game in games}

    resolved: list[Edge] = []
    for edge in edges:
        game = by_id.get(edge.game_id)
        if edge.tier not in _TIEBREAK_TIERS or game is None:
            resolved.append(edge)
            continue

        margin = projected_margin(ratings, game.home_team_id, game.away_team_id, config)
        side = tiebreak_side(margin, edge.league_spread)
        resolved.append(
            edge.model_copy(
                update={
                    "side": side,
                    "rationale": (
                        f"{edge.rationale}; rating projects home by {margin:+.1f} "
                        f"vs a board of {edge.league_spread:+.1f}"
                    ),
                }
            )
        )
    return resolved


def predict_tiebreaker_total(market_lines: Sequence[MarketLine]) -> float | None:
    """Median market total. The market total is the estimate; we do not model it."""
    totals = [line.total for line in market_lines if line.total is not None]
    return median(totals) if totals else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Wire it into the report command**

In `src/pickem/cli.py`, add the import:

```python
from pickem.edge.pipeline import apply_tiebreaks
```

Then in the `report` command, replace this line:

```python
    age = (now - newest).total_seconds() / 60 if newest else None
```

with:

```python
    # Games the market never repriced fall through to the rating, built from
    # every completed game already in the store.
    history: list[Game] = []
    for past_week in range(1, week):
        history.extend(store.games_for_week(sport, season, past_week))
    edges = apply_tiebreaks(edges, games, history)

    age = (now - newest).total_seconds() / 60 if newest else None
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/pickem/edge/pipeline.py tests/test_pipeline.py src/pickem/cli.py
git commit -m "feat: resolve coinflip and no-market games with the Elo tiebreak"
```

---

## Done criteria

Phase A is complete when:

- [ ] `uv run pytest` passes with every module covered
- [ ] `uv run ruff check src tests` is clean
- [ ] A real CBS paste ingests end to end without an `UnknownTeamError`
- [ ] `pickem report` renders a ranked sheet from live odds, with no game left on an
      unresolved COINFLIP or NO_MARKET side
- [ ] `pickem backtest` prints a hit rate with a Wilson interval and its assumptions

Then make the Phase B decision described in §12 of the spec: if divergence-only
backtests meaningfully above the noise floor, modeling may be unnecessary.
