# Architecture Deepening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deepen the edge decision, CBS intake, Store reads, archive acquisition, and canonical game identity modules so correctness rules live behind small interfaces instead of being assembled by CLI callers.

**Architecture:** Preserve the existing one-directional `ingest → resolve → store → edge → report` flow and every load-bearing behavior. Replace shallow interfaces rather than layering new ones beside them: callers receive final picks, complete CBS games, and coherent stored datasets; paid archive coordination moves behind one module; all source adapters reuse one canonical matchup module while retaining their intentional differences.

**Tech Stack:** Python 3.12+, uv, pydantic 2, DuckDB, polars, httpx, Typer, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-11-pickem-edge-design.md`

**Standing decisions:** `docs/HANDOFF.md` — especially “Load-bearing conventions,” “The odds poll has two guards,” and the phase-exit result.

## Global Constraints

- Python 3.12+; use `uv` for every Python, test, and lint command.
- **All spreads remain home-perspective.** Home favored by 3 is `-3.0`; adapters normalize once at ingest.
- **The `lines` table remains append-only.** Use `INSERT OR IGNORE`; never update, replace, or delete market history.
- **`edge/` and `backtest/` remain pure except the new archive acquisition module**, whose purpose is coordinating the existing Store and external odds adapter. Strategy computation itself performs no I/O.
- **Elo resolves only `COINFLIP` and `NO_MARKET`.** It never overrides `STRONG` or `LEAN` divergence.
- **The live report and backtest run the same edge decision interface.** No parallel implementation is allowed.
- **CBS ingestion remains atomic.** Any skipped CBS row exits non-zero before the database is opened.
- **Source-specific failure behavior remains intentional.** CBS, CFBD, and nflverse fail on unknown teams; the nationwide odds feed records unknown off-slate teams in `MarketLinesResult.skipped`.
- **Both odds guards remain required.** Check kickoff `window` before team resolution and canonical `slate` after resolution.
- **Nothing is dropped silently.** Missing or unclassified inputs remain visible in result records and CLI output.
- **The test suite remains fully offline.** External calls use injected loaders, transports, or fake adapters.
- **Pydantic records cross module interfaces.** Polars frames remain inside source adapters.
- Use `StrEnum`, never `str, Enum`.
- Use replace-don’t-layer testing: once callers use a deep interface, delete tests of superseded shallow interfaces.
- Before every commit run `uv run pytest -q`, `uv run ruff check src tests`, and `uv run ruff format --check src tests`.

## Target File Structure

| File | Responsibility after this plan |
|---|---|
| `src/pickem/edge/divergence.py` | Internal consensus, delta, and tier calculations; no public unfinished-pick interface |
| `src/pickem/edge/pipeline.py` | Public pure edge decision interface; returns only final `Edge` records |
| `src/pickem/ingest/cbs.py` | Shared CBS intake records plus pasted-text adapter |
| `src/pickem/ingest/cbs_html.py` | Saved-page adapter producing the same complete CBS intake records |
| `src/pickem/store/db.py` | DuckDB adapter; coherent week/range reads plus existing safe writes |
| `src/pickem/backtest/archive.py` | Paid archive planning and execution policy, including limits, role labels, progress, and interruption |
| `src/pickem/resolve/matchup.py` | Source-independent name resolution, away/home ordering, and canonical game identity |
| `src/pickem/cli.py` | Typer option parsing and terminal rendering only |
| `src/pickem/backtest/runner.py` | Pure historical replay through the public edge decision interface |
| `tests/test_pipeline.py` | Tests final decisions through the edge interface |
| `tests/test_cbs_parser.py`, `tests/test_cbs_html.py` | Tests complete CBS intake records |
| `tests/test_store.py` | Tests coherent Store read datasets and write invariants |
| `tests/test_archive.py` | Tests paid acquisition through its public interface with fake adapters |
| `tests/test_matchup.py`, `tests/test_game_id_contract.py` | Tests canonical identity and source adapter agreement |

---

### Task 1: Return only final picks from the edge decision module

**Files:**
- Modify: `src/pickem/edge/divergence.py`
- Modify: `src/pickem/edge/pipeline.py`
- Modify: `src/pickem/backtest/runner.py`
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_divergence.py`
- Modify: `tests/test_pipeline.py`
- Test: `tests/test_backtest_runner.py`
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `Sequence[LeagueLine]`, `Sequence[MarketLine]`, `Sequence[Game]`, historical `Sequence[Game]`, optional `Thresholds`, optional `EloConfig`.
- Produces: `decide_edges(league_lines, market_lines, games, history, thresholds=None, elo_config=None) -> list[Edge]`; every returned `Edge.side` is a real decision.
- Retains: `consensus_spread(lines)`, `rank_edges(edges)`, and `predict_tiebreaker_total(market_lines)`.
- Removes: public `compute_edge(league, market, thresholds=None)` and public `apply_tiebreaks(edges, games, history, config=None)`.

- [ ] **Step 1: Replace shallow pipeline tests with failing final-decision tests**

In `tests/test_pipeline.py`, keep the three total-prediction tests and replace the tests that construct placeholder `Edge` records with tests through `decide_edges`:

```python
from datetime import UTC, datetime

import pytest

from pickem.edge.divergence import Thresholds
from pickem.edge.pipeline import MissingGameError, decide_edges, predict_tiebreaker_total
from pickem.models import Game, LeagueLine, MarketLine, Side, Sport, Tier

NOW = datetime(2025, 9, 21, tzinfo=UTC)
POSTED = datetime(2025, 9, 16, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


def game(gid: str = GID, week: int = 3, home_score=None, away_score=None) -> Game:
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


def league(spread: float = -3.0) -> LeagueLine:
    return LeagueLine(
        game_id=GID, season=2025, week=3, spread_home=spread, posted_at=POSTED
    )


def market(spread: float) -> MarketLine:
    return MarketLine(
        game_id=GID,
        source="oddsapi",
        book="pinnacle",
        spread_home=spread,
        captured_at=NOW,
    )


HISTORY = [
    game(gid=f"nfl-2025-{week:02d}-BUF-at-MIA", week=week, home_score=30, away_score=10)
    for week in range(1, 4)
]


def test_strong_divergence_returns_its_final_side_without_rating_override():
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.STRONG


def test_coinflip_returns_the_rating_side_not_a_placeholder():
    [edge] = decide_edges([league(-3.0)], [market(-3.5)], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.COINFLIP
    assert "rating" in edge.rationale.lower()


def test_no_market_returns_the_rating_side_not_a_placeholder():
    [edge] = decide_edges([league(-3.0)], [], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.NO_MARKET
    assert edge.market_spread is None


def test_a_required_tiebreak_without_a_game_fails_loudly():
    with pytest.raises(MissingGameError):
        decide_edges([league(-3.0)], [], [], HISTORY)
```

- [ ] **Step 2: Run the new interface tests and verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`

Expected: collection fails because `decide_edges` does not exist.

- [ ] **Step 3: Implement the deep edge decision interface**

In `src/pickem/edge/pipeline.py`, add the imports and public function below. Rename `apply_tiebreaks` to `_apply_tiebreaks`; its existing implementation remains internal.

```python
from collections import defaultdict

from pickem.edge.divergence import Thresholds, _compute_edge
from pickem.models import Edge, Game, LeagueLine, MarketLine, Tier


def decide_edges(
    league_lines: Sequence[LeagueLine],
    market_lines: Sequence[MarketLine],
    games: Sequence[Game],
    history: Sequence[Game],
    thresholds: Thresholds | None = None,
    elo_config: EloConfig | None = None,
) -> list[Edge]:
    """Return final auditable picks; no placeholder side crosses this interface."""
    market_by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in market_lines:
        market_by_game[line.game_id].append(line)

    measured = [
        _compute_edge(league, market_by_game.get(league.game_id, []), thresholds)
        for league in league_lines
    ]
    return _apply_tiebreaks(measured, games, history, elo_config)
```

In `src/pickem/edge/divergence.py`, rename `compute_edge` to `_compute_edge`. Keep its calculation unchanged and add this comment above the function:

```python
# Internal measurement stage. COINFLIP and NO_MARKET carry a temporary side
# until pipeline.decide_edges resolves them. Callers must use decide_edges.
```

- [ ] **Step 4: Migrate the live report and backtest to `decide_edges`**

In `src/pickem/cli.py`, replace the report command’s per-line `compute_edge` loop and `apply_tiebreaks` call with:

```python
market = [
    snapshot
    for league in league_lines
    for snapshot in store.market_lines_for(league.game_id)
]
newest = max((line.captured_at for line in market), default=None)
history = store.games_before(sport, season, week)
untrained = not any(
    game.home_score is not None and game.away_score is not None for game in history
)
try:
    edges = decide_edges(league_lines, market, games, history)
except MissingGameError as exc:
    typer.secho(f"cannot resolve a tiebreak: {exc}", fg="red", err=True)
    raise typer.Exit(code=1) from exc
```

In `src/pickem/backtest/runner.py`, replace the weekly `compute_edge` list construction plus `apply_tiebreaks` call with:

```python
week_submission = [
    line
    for game in week_games
    for line in submission_by_game.get(game.game_id, [])
]
decided = decide_edges(
    list(league_by_game.values()),
    week_submission,
    week_games,
    history,
    thresholds,
    elo_config,
)
for edge in decided:
    game = games_by_id[edge.game_id]
    result = grade_pick(
        edge.side,
        game.home_score - game.away_score,
        league_by_game[edge.game_id].spread_home,
    )
    all_results.append(result)
    by_tier[edge.tier].append(result)
```

Update imports so both callers import `decide_edges` and neither imports `compute_edge` or `apply_tiebreaks`.

- [ ] **Step 5: Replace divergence tests that use the removed interface**

Keep the `consensus_spread` and `rank_edges` tests in `tests/test_divergence.py`. Add these observable-behavior tests to `tests/test_pipeline.py`, then delete every direct `compute_edge` test:

```python
@pytest.mark.parametrize(
    ("league_spread", "market_spread", "expected_side", "expected_delta"),
    [
        (-3.0, -6.0, Side.HOME, 3.0),
        (-6.0, -3.0, Side.AWAY, -3.0),
        (2.0, -1.0, Side.HOME, 3.0),
    ],
)
def test_final_pick_obeys_the_home_perspective_sign_convention(
    league_spread, market_spread, expected_side, expected_delta
):
    [edge] = decide_edges(
        [league(league_spread)], [market(market_spread)], [game()], HISTORY
    )
    assert edge.side is expected_side
    assert edge.delta == expected_delta


@pytest.mark.parametrize(
    ("market_spread", "expected_tier"),
    [(-5.0, Tier.STRONG), (-4.0, Tier.LEAN), (-3.5, Tier.COINFLIP)],
)
def test_final_pick_uses_inclusive_tier_thresholds(market_spread, expected_tier):
    [edge] = decide_edges([league(-3.0)], [market(market_spread)], [game()], HISTORY)
    assert edge.tier is expected_tier


def test_threshold_configuration_changes_tiers_without_changing_the_interface():
    strict = Thresholds(strong=5.0, lean=3.0)
    [edge] = decide_edges(
        [league(-3.0)], [market(-6.0)], [game()], HISTORY, thresholds=strict
    )
    assert edge.tier is Tier.LEAN


def test_final_pick_carries_both_source_numbers_for_audit():
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY)
    assert edge.league_spread == -3.0
    assert edge.market_spread == -6.0
    assert edge.rationale
```

Run: `uv run pytest tests/test_divergence.py tests/test_pipeline.py tests/test_backtest_runner.py tests/test_end_to_end.py -v`

Expected: all pass.

- [ ] **Step 6: Prove the shallow interface is gone and run the full verification gate**

Run: `rg -n '\bcompute_edge\b|\bapply_tiebreaks\b' src tests`

Expected: no matches. `_compute_edge` and `_apply_tiebreaks` are allowed because the expression requires a word boundary before the unprefixed names.

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: 246 or more tests pass; ruff reports no errors or formatting changes.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/edge/divergence.py src/pickem/edge/pipeline.py \
  src/pickem/backtest/runner.py src/pickem/cli.py \
  tests/test_divergence.py tests/test_pipeline.py \
  tests/test_backtest_runner.py tests/test_end_to_end.py
git commit -m "refactor: return only final edge decisions"
```

---

### Task 2: Return complete games from both CBS adapters

**Files:**
- Modify: `src/pickem/ingest/cbs.py`
- Modify: `src/pickem/ingest/cbs_html.py`
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_cbs_parser.py`
- Modify: `tests/test_cbs_html.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Produces: `ParsedCbsGame(game: Game, league_line: LeagueLine)`.
- Produces: `ParseResult(games: list[ParsedCbsGame], skipped: list[str])`.
- Removes: `ParseResult.lines`, `ParseResult.matchups`, and `ParseResult.kickoffs`.
- Preserves: `parse_cbs_block(text, resolver, sport, season, week, posted_at) -> ParseResult`, `parse_cbs_html(text, resolver, sport, season, week, posted_at) -> ParseResult`, and atomic CLI persistence.

- [ ] **Step 1: Write failing cohesive-result tests**

In `tests/test_cbs_parser.py`, replace `test_matchups_are_parallel_to_lines` with:

```python
def test_text_result_keeps_game_and_league_line_together(resolver):
    result = parse("Buffalo Bills at Miami Dolphins -3.0", resolver)
    [parsed] = result.games
    assert parsed.game.game_id == "nfl-2025-03-BUF-at-MIA"
    assert parsed.game.away_team_id == "BUF"
    assert parsed.game.home_team_id == "MIA"
    assert parsed.game.kickoff_utc == POSTED
    assert parsed.league_line.game_id == parsed.game.game_id
    assert parsed.league_line.spread_home == -3.0
```

In `tests/test_cbs_html.py`, replace the kickoff dictionary assertion with:

```python
def test_html_result_keeps_real_kickoff_with_its_game(resolver):
    result = parse(resolver=resolver)
    by_id = {parsed.game.game_id: parsed for parsed in result.games}
    parsed = by_id["cfb-2026-01-ECU-at-BAMA"]
    assert parsed.game.kickoff_utc == datetime(2026, 9, 5, 16, 0, tzinfo=UTC)
    assert parsed.league_line.game_id == parsed.game.game_id
```

Update existing parser assertions from `result.lines` to `result.games`, accessing `parsed.league_line` where the assertion concerns a spread.

- [ ] **Step 2: Run parser tests and verify they fail**

Run: `uv run pytest tests/test_cbs_parser.py tests/test_cbs_html.py -v`

Expected: failures because `ParseResult` has no `games` field.

- [ ] **Step 3: Implement cohesive CBS intake records**

In `src/pickem/ingest/cbs.py`, replace `ParseResult` and add the shared constructor:

```python
class ParsedCbsGame(BaseModel):
    game: Game
    league_line: LeagueLine


class ParseResult(BaseModel):
    games: list[ParsedCbsGame]
    skipped: list[str]


def _parsed_game(
    *,
    sport: Sport,
    season: int,
    week: int,
    away_team_id: str,
    home_team_id: str,
    spread_home: float,
    posted_at: datetime,
    kickoff_utc: datetime | None = None,
) -> ParsedCbsGame:
    game_id = make_game_id(sport, season, week, away_team_id, home_team_id)
    return ParsedCbsGame(
        game=Game(
            game_id=game_id,
            sport=sport,
            season=season,
            week=week,
            kickoff_utc=kickoff_utc or posted_at,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        ),
        league_line=LeagueLine(
            game_id=game_id,
            season=season,
            week=week,
            spread_home=spread_home,
            posted_at=posted_at,
        ),
    )
```

Change the text parser’s successful-row block to:

```python
games.append(
    _parsed_game(
        sport=sport,
        season=season,
        week=week,
        away_team_id=away_id,
        home_team_id=home_id,
        spread_home=spread_home,
        posted_at=posted_at,
    )
)
```

Return `ParseResult(games=games, skipped=skipped)`.

In `src/pickem/ingest/cbs_html.py`, import `_parsed_game` and replace the successful event construction with:

```python
kickoff_utc = None
starts_at = event.get("startsAt")
if isinstance(starts_at, int | float) and not isinstance(starts_at, bool):
    kickoff_utc = datetime.fromtimestamp(starts_at / 1000, tz=UTC)

games.append(
    _parsed_game(
        sport=sport,
        season=season,
        week=week,
        away_team_id=away_id,
        home_team_id=home_id,
        spread_home=float(spread),
        posted_at=posted_at,
        kickoff_utc=kickoff_utc,
    )
)
```

When `startsAt` is missing, `_parsed_game` deliberately uses `posted_at` as the visible placeholder.

- [ ] **Step 4: Simplify atomic CLI ingestion**

Replace the reconstructed `Game` list in `ingest_cbs` with:

```python
typer.echo(f"parsed {len(parsed.games)} games for {sport.value} {season} week {week}")
for skipped in parsed.skipped:
    typer.secho(f"  skipped: {skipped!r}", fg="yellow")
if parsed.skipped:
    raise typer.Exit(code=1)

store = _store(db)
with store:
    store.insert_games_if_absent([parsed_game.game for parsed_game in parsed.games])
    store.upsert_league_lines(
        [parsed_game.league_line for parsed_game in parsed.games]
    )
    typer.echo(f"ingested {len(parsed.games)} games for {sport.value} {season} week {week}")
```

Remove `Game` construction and `make_game_id` imports that become unused. The database must still be opened only after the skipped-row check.

- [ ] **Step 5: Verify parser behavior and atomicity**

Run: `uv run pytest tests/test_cbs_parser.py tests/test_cbs_html.py tests/test_cli.py tests/test_end_to_end.py -v`

Expected: all pass, including unknown-team failure, real HTML kickoff persistence, dual-number rejection, and “database does not exist after partial parse.”

Run: `rg -n 'parsed\.(lines|matchups|kickoffs)|result\.(lines|matchups|kickoffs)' src tests/test_cbs_parser.py tests/test_cbs_html.py`

Expected: no matches.

- [ ] **Step 6: Run the full verification gate and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all pass.

```bash
git add src/pickem/ingest/cbs.py src/pickem/ingest/cbs_html.py src/pickem/cli.py \
  tests/test_cbs_parser.py tests/test_cbs_html.py tests/test_cli.py tests/test_end_to_end.py
git commit -m "refactor: keep CBS game facts together"
```

---

### Task 3: Add coherent week and range reads to Store

**Files:**
- Modify: `src/pickem/store/db.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Produces: `StoredDataset(games, league_lines, market_lines)`.
- Produces: `Store.load_week(sport, season, week) -> StoredDataset`.
- Produces: `Store.load_weeks(sport, season, start_week, end_week) -> StoredDataset`.
- Produces: `Store.load_seasons(sport, start_season, end_season) -> StoredDataset`.
- Temporarily retains granular read methods until Task 4 migrates every caller.

- [ ] **Step 1: Write failing Store dataset tests**

Append to `tests/test_store.py`:

```python
def test_load_week_returns_games_league_lines_and_market_history_together(store):
    store.upsert_games([game()])
    store.upsert_league_lines(
        [LeagueLine(game_id=GID, season=2025, week=3, spread_home=-3.0, posted_at=KICK)]
    )
    store.append_market_lines([market(-3.0, KICK), market(-6.0, KICK.replace(day=22))])

    dataset = store.load_week(Sport.NFL, 2025, 3)

    assert [item.game_id for item in dataset.games] == [GID]
    assert [item.game_id for item in dataset.league_lines] == [GID]
    assert sorted(item.spread_home for item in dataset.market_lines) == [-6.0, -3.0]


def test_load_weeks_excludes_neighboring_weeks(store):
    week_three = game()
    week_four = game().model_copy(
        update={"game_id": "nfl-2025-04-BUF-at-MIA", "week": 4}
    )
    store.upsert_games([week_three, week_four])

    dataset = store.load_weeks(Sport.NFL, 2025, 4, 4)

    assert [item.game_id for item in dataset.games] == [week_four.game_id]


def test_load_seasons_excludes_other_sports_and_seasons(store):
    nfl = game()
    cfb = game().model_copy(
        update={
            "game_id": "cfb-2025-03-BUF-at-MIA",
            "sport": Sport.CFB,
        }
    )
    store.upsert_games([nfl, cfb])

    dataset = store.load_seasons(Sport.NFL, 2025, 2025)

    assert [item.game_id for item in dataset.games] == [nfl.game_id]
```

- [ ] **Step 2: Run the Store tests and verify they fail**

Run: `uv run pytest tests/test_store.py -k 'load_week or load_weeks or load_seasons' -v`

Expected: failures because the methods do not exist.

- [ ] **Step 3: Add `StoredDataset` and centralized row decoders**

In `src/pickem/store/db.py`, add:

```python
from pydantic import BaseModel


class StoredDataset(BaseModel):
    games: list[Game]
    league_lines: list[LeagueLine]
    market_lines: list[MarketLine]


def _game_from_row(row: tuple) -> Game:
    return Game(
        game_id=row[0],
        sport=Sport(row[1]),
        season=row[2],
        week=row[3],
        kickoff_utc=row[4],
        home_team_id=row[5],
        away_team_id=row[6],
        home_score=row[7],
        away_score=row[8],
    )


def _league_line_from_row(row: tuple) -> LeagueLine:
    return LeagueLine(
        game_id=row[0], season=row[1], week=row[2], spread_home=row[3], posted_at=row[4]
    )


def _market_line_from_row(row: tuple) -> MarketLine:
    return MarketLine(
        game_id=row[0],
        source=row[1],
        book=row[2],
        spread_home=row[3],
        total=row[4],
        captured_at=row[5],
    )
```

Use these decoders in existing granular reads immediately so row construction has one implementation.

- [ ] **Step 4: Implement one private dataset query and three intent-level reads**

Add this method to `Store`:

```python
def _load_dataset(self, where: str, params: list[object]) -> StoredDataset:
    games = self._con.execute(
        "SELECT game_id, sport, season, week, kickoff_utc, home_team_id, "
        "away_team_id, home_score, away_score FROM games g WHERE " + where,
        params,
    ).fetchall()
    league_lines = self._con.execute(
        "SELECT l.game_id, l.season, l.week, l.spread_home, l.posted_at "
        "FROM league_lines l JOIN games g USING (game_id) WHERE " + where,
        params,
    ).fetchall()
    market_lines = self._con.execute(
        "SELECT l.game_id, l.source, l.book, l.spread_home, l.total, l.captured_at "
        "FROM lines l JOIN games g USING (game_id) WHERE " + where,
        params,
    ).fetchall()
    return StoredDataset(
        games=[_game_from_row(row) for row in games],
        league_lines=[_league_line_from_row(row) for row in league_lines],
        market_lines=[_market_line_from_row(row) for row in market_lines],
    )

def load_week(self, sport: Sport, season: int, week: int) -> StoredDataset:
    return self._load_dataset(
        "g.sport = ? AND g.season = ? AND g.week = ?",
        [sport.value, season, week],
    )

def load_weeks(
    self, sport: Sport, season: int, start_week: int, end_week: int
) -> StoredDataset:
    return self._load_dataset(
        "g.sport = ? AND g.season = ? AND g.week BETWEEN ? AND ?",
        [sport.value, season, start_week, end_week],
    )

def load_seasons(
    self, sport: Sport, start_season: int, end_season: int
) -> StoredDataset:
    return self._load_dataset(
        "g.sport = ? AND g.season BETWEEN ? AND ?",
        [sport.value, start_season, end_season],
    )
```

The `where` fragments are private constants chosen only by the three public methods; never accept caller-provided SQL.

- [ ] **Step 5: Verify Store behavior and existing write invariants**

Run: `uv run pytest tests/test_store.py -v`

Expected: all pass, including append-only market history, score-preserving upserts, empty writes, and the three new coherent reads.

- [ ] **Step 6: Run the full verification gate and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all pass.

```bash
git add src/pickem/store/db.py tests/test_store.py
git commit -m "refactor: add coherent Store read datasets"
```

---

### Task 4: Migrate callers and remove granular Store reads

**Files:**
- Modify: `src/pickem/cli.py`
- Modify: `src/pickem/store/db.py`
- Modify: `tests/test_store.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_backfill_cfb.py`
- Modify: `tests/test_cbs_html.py`
- Modify: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `Store.load_week`, `Store.load_weeks`, `Store.load_seasons` from Task 3.
- Removes: `Store.market_lines_for`, `Store.league_lines_for_week`, and `Store.games_for_week`.
- Retains: `Store.games_before` because the live rating needs all completed prior games; retains `Store.picks_for_week` as the persisted-pick read interface.

- [ ] **Step 1: Migrate CLI commands to coherent reads**

Use these replacements in `src/pickem/cli.py`:

```python
# poll-odds
dataset = store.load_week(sport, season, week)
slate = [line.game_id for line in dataset.league_lines]

# report
dataset = store.load_week(sport, season, week)
league_lines = dataset.league_lines
games = dataset.games
market = dataset.market_lines

# backfill-history and backtest
dataset = store.load_seasons(Sport.NFL, start, end)
games = dataset.games

# backtest only
frozen, submission, unclassified = split_proxies(dataset.market_lines)

# calibrate
dataset = store.load_weeks(sport, season, from_week, to_week)
result = calibrate(
    dataset.league_lines,
    dataset.market_lines,
    source=source,
    tolerance=timedelta(minutes=tolerance_minutes),
)
```

Delete every `for season`/`for week in range(1, 23)` database traversal and every per-game market query replaced by these datasets.

- [ ] **Step 2: Migrate tests to observe data through coherent reads**

Replace granular reads in tests with the relevant dataset. For example:

```python
dataset = store.load_week(Sport.NFL, 2025, 3)
assert dataset.games[0].home_score == 24
assert sorted(line.spread_home for line in dataset.market_lines) == [-6.0, -3.0]
```

For the archive CLI test, replace `store.market_lines_for("nfl-2024-03-BUF-at-MIA")` with:

```python
dataset = store.load_week(Sport.NFL, 2024, 3)
stored = [
    line
    for line in dataset.market_lines
    if line.game_id == "nfl-2024-03-BUF-at-MIA"
]
assert {line.source for line in stored} == {"oddsapi:frozen", "oddsapi:submit"}
```

- [ ] **Step 3: Run the affected tests before removing old methods**

Run: `uv run pytest tests/test_store.py tests/test_cli.py tests/test_backfill_cfb.py tests/test_cbs_html.py tests/test_end_to_end.py -v`

Expected: all pass using the new Store interface.

- [ ] **Step 4: Delete the superseded granular methods and their direct tests**

Remove `market_lines_for`, `league_lines_for_week`, and `games_for_week` from `Store`. Delete tests that existed only to exercise these old methods after their behavior is covered through `load_week`, `load_weeks`, or `load_seasons`.

Run: `rg -n 'market_lines_for|league_lines_for_week|games_for_week' src tests`

Expected: no matches.

- [ ] **Step 5: Run the full verification gate and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all pass.

```bash
git add src/pickem/cli.py src/pickem/store/db.py \
  tests/test_store.py tests/test_cli.py tests/test_backfill_cfb.py \
  tests/test_cbs_html.py tests/test_end_to_end.py
git commit -m "refactor: read stored data by weekly and historical intent"
```

---

### Task 5: Put paid archive policy behind one module

**Files:**
- Create: `src/pickem/backtest/archive.py`
- Create: `tests/test_archive.py`
- Read, do not modify unless required by a failing test: `src/pickem/backtest/snapshots.py`
- Read, do not modify unless required by a failing test: `src/pickem/ingest/odds.py`

**Interfaces:**
- Produces: `ArchiveBackfill(store, resolver, client_factory)`.
- Produces: `ArchiveBackfill.run(games, max_credits, execute, on_progress) -> ArchiveRunReport`.
- Produces: `ArchiveProgress`, `ArchiveRunReport`, `NoHistoricalGames`, `CreditLimitExceeded`, `ArchiveRunInterrupted`.
- Uses `client_factory` as the true-external seam; dry runs and rejected plans never construct the client adapter.

- [ ] **Step 1: Write failing tests at the archive module interface**

Create `tests/test_archive.py` with these imports, fake adapter, and fixtures:

```python
from datetime import UTC, datetime

import pytest

from pickem.backtest.archive import (
    ArchiveBackfill,
    ArchiveRunInterrupted,
    CreditLimitExceeded,
    NoHistoricalGames,
)
from pickem.ingest.odds import QuotaExhausted
from pickem.models import Game, MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


class StubHistoricalClient:
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def fetch_historical_spreads(self, sport_key, **kwargs):
        self.calls.append({"sport_key": sport_key, **kwargs})
        if self.fail_on_call == len(self.calls):
            raise QuotaExhausted("test quota exhausted")
        return MarketLinesResult(
            lines=[
                MarketLine(
                    game_id=game_id,
                    source=kwargs["source"],
                    book="pinnacle",
                    spread_home=-3.0,
                    captured_at=kwargs["at"],
                )
                for game_id in sorted(kwargs["slate"])
            ],
            skipped=["off-slate game — not stored"],
        )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "archive.duckdb") as value:
        value.init_schema()
        yield value


@pytest.fixture
def games():
    values = [
        Game(
            game_id=make_game_id(Sport.NFL, 2024, 3, "BUF", "MIA"),
            sport=Sport.NFL,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 22, 17, 0, tzinfo=UTC),
            home_team_id="MIA",
            away_team_id="BUF",
            home_score=30,
            away_score=20,
        ),
        Game(
            game_id=make_game_id(Sport.NFL, 2024, 3, "DAL", "NYG"),
            sport=Sport.NFL,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 24, 0, 15, tzinfo=UTC),
            home_team_id="NYG",
            away_team_id="DAL",
            home_score=17,
            away_score=21,
        ),
    ]
    return values
```

Append these interface tests:

```python
def test_dry_run_reports_cost_without_constructing_a_client(store, games):
    def explode():
        raise AssertionError("dry run constructed the paid adapter")

    report = ArchiveBackfill(store, TeamResolver.default(), explode).run(
        games, max_credits=20_000, execute=False
    )

    assert report.total_snapshots == 3
    assert report.credits == 30
    assert report.executed_snapshots == 0


def test_credit_ceiling_fails_before_constructing_a_client(store, games):
    def explode():
        raise AssertionError("rejected plan constructed the paid adapter")

    with pytest.raises(CreditLimitExceeded):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            games, max_credits=1, execute=True
        )


def test_execution_assigns_both_proxy_roles_and_appends_results(store, games):
    client = StubHistoricalClient()
    progress = []
    store.upsert_games(games)

    report = ArchiveBackfill(
        store, TeamResolver.default(), lambda: client
    ).run(games, max_credits=20_000, execute=True, on_progress=progress.append)

    assert report.executed_snapshots == 3
    assert {call["source"] for call in client.calls} == {
        "oddsapi:frozen",
        "oddsapi:submit",
    }
    assert len(progress) == 3
    assert any(item.skipped for item in progress)
    stored = store.load_week(Sport.NFL, 2024, 3)
    assert {line.source for line in stored.market_lines} == {
        "oddsapi:frozen",
        "oddsapi:submit",
    }


def test_no_stored_games_fails_before_constructing_a_client(store):
    def explode():
        raise AssertionError("empty run constructed the paid adapter")

    with pytest.raises(NoHistoricalGames):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            [], max_credits=20_000, execute=True
        )


def test_quota_failure_names_the_exact_partial_position(store, games):
    client = StubHistoricalClient(fail_on_call=2)

    with pytest.raises(ArchiveRunInterrupted) as caught:
        ArchiveBackfill(
            store, TeamResolver.default(), lambda: client
        ).run(games, max_credits=20_000, execute=True)

    assert caught.value.index == 2
    assert caught.value.total == 3
```

- [ ] **Step 2: Run the archive tests and verify they fail**

Run: `uv run pytest tests/test_archive.py -v`

Expected: collection fails because `pickem.backtest.archive` does not exist.

- [ ] **Step 3: Implement archive report and error records**

Create `src/pickem/backtest/archive.py` with:

```python
from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel

from pickem.backtest.snapshots import SnapshotKind, SnapshotRequest, estimate_credits, plan_snapshots
from pickem.ingest.odds import NFL_KEY, OddsClient, QuotaExhausted
from pickem.models import FROZEN_SOURCE, SUBMISSION_SOURCE, Game
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


class ArchiveProgress(BaseModel):
    index: int
    total: int
    request: SnapshotRequest
    line_count: int
    skipped: list[str]


class ArchiveRunReport(BaseModel):
    total_snapshots: int
    frozen_snapshots: int
    submission_snapshots: int
    weeks: int
    credits: int
    executed_snapshots: int


class NoHistoricalGames(ValueError):
    pass


class CreditLimitExceeded(ValueError):
    def __init__(self, credits: int, maximum: int) -> None:
        self.credits = credits
        self.maximum = maximum
        super().__init__(f"plan costs {credits} credits, above the {maximum}-credit ceiling")


class ArchiveRunInterrupted(RuntimeError):
    def __init__(self, index: int, total: int, reason: str) -> None:
        self.index = index
        self.total = total
        super().__init__(f"stopped at snapshot {index}/{total}: {reason}")
```

- [ ] **Step 4: Implement `ArchiveBackfill.run`**

Add:

```python
ProgressCallback = Callable[[ArchiveProgress], None]
ClientFactory = Callable[[], OddsClient]


class ArchiveBackfill:
    def __init__(
        self,
        store: Store,
        resolver: TeamResolver,
        client_factory: ClientFactory,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._client_factory = client_factory

    def run(
        self,
        games: Sequence[Game],
        *,
        max_credits: int,
        execute: bool,
        on_progress: ProgressCallback | None = None,
    ) -> ArchiveRunReport:
        if not games:
            raise NoHistoricalGames("no historical NFL games are stored")
        plan = plan_snapshots(games)
        credits = estimate_credits(plan)
        frozen = sum(request.kind is SnapshotKind.FROZEN for request in plan)
        report = ArchiveRunReport(
            total_snapshots=len(plan),
            frozen_snapshots=frozen,
            submission_snapshots=len(plan) - frozen,
            weeks=len({(request.season, request.week) for request in plan}),
            credits=credits,
            executed_snapshots=0,
        )
        if credits > max_credits:
            raise CreditLimitExceeded(credits, max_credits)
        if not execute:
            return report

        completed = 0
        with self._client_factory() as client:
            for index, request in enumerate(plan, start=1):
                source = (
                    FROZEN_SOURCE
                    if request.kind is SnapshotKind.FROZEN
                    else SUBMISSION_SOURCE
                )
                try:
                    result = client.fetch_historical_spreads(
                        NFL_KEY,
                        resolver=self._resolver,
                        sport=games[0].sport,
                        season=request.season,
                        week=request.week,
                        at=request.at,
                        slate=request.slate,
                        window=request.window,
                        source=source,
                    )
                except QuotaExhausted as exc:
                    raise ArchiveRunInterrupted(index, len(plan), str(exc)) from exc
                self._store.append_market_lines(result.lines)
                completed = index
                if on_progress is not None:
                    on_progress(
                        ArchiveProgress(
                            index=index,
                            total=len(plan),
                            request=request,
                            line_count=len(result.lines),
                            skipped=result.skipped,
                        )
                    )
        return report.model_copy(update={"executed_snapshots": completed})
```

- [ ] **Step 5: Verify archive behavior directly**

Run: `uv run pytest tests/test_archive.py tests/test_snapshots.py tests/test_odds_adapter.py -v`

Expected: all pass; no test accesses the network.

- [ ] **Step 6: Run the full verification gate and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all pass.

```bash
git add src/pickem/backtest/archive.py tests/test_archive.py
git commit -m "refactor: concentrate paid archive acquisition policy"
```

---

### Task 6: Make `backfill-history` a thin archive caller

**Files:**
- Modify: `src/pickem/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ArchiveBackfill` and its result/error records from Task 5.
- Removes from CLI: snapshot planning, credit arithmetic, source-role mapping, paid execution loop, quota-position tracking, and direct `append_market_lines` calls.
- Retains in CLI: Typer options, terminal messages, and process exit codes.

- [ ] **Step 1: Move archive workflow assertions out of CLI tests**

Delete CLI tests whose behavior is now covered through `tests/test_archive.py`: direct source-role inspection and direct stub-call counting. Keep three CLI translation tests:

```python
from pickem.backtest.archive import ArchiveRunInterrupted


def test_backfill_history_dry_run_prints_plan_without_spending(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)

    def explode():
        raise AssertionError("dry run read the paid-adapter key")

    monkeypatch.setattr("pickem.config.odds_api_key", explode)
    result = runner.invoke(
        app, ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db)]
    )
    assert result.exit_code == 0, result.output
    assert "3 snapshots" in result.output
    assert "30 credits" in result.output
    assert "dry run" in result.output.lower()


def test_backfill_history_translates_credit_limit_to_exit_one(tmp_path):
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    result = runner.invoke(
        app,
        [
            "backfill-history", "--from", "2024", "--to", "2024",
            "--db", str(db), "--execute", "--max-credits", "1",
        ],
    )
    assert result.exit_code == 1
    assert "credit ceiling" in result.output.lower()


def test_backfill_history_translates_interruption_to_exit_one(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    monkeypatch.setattr(
        "pickem.cli.ArchiveBackfill.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ArchiveRunInterrupted(2, 3, "test quota exhausted")
        ),
    )
    result = runner.invoke(
        app,
        ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db), "--execute"],
    )
    assert result.exit_code == 1
    assert "stopped at snapshot 2/3" in result.output
```

- [ ] **Step 2: Run the focused CLI tests before migration**

Run: `uv run pytest tests/test_cli.py -k backfill_history -v`

Expected: at least the monkeypatch target for `ArchiveBackfill` fails because CLI does not import it.

- [ ] **Step 3: Replace the CLI workflow with one module call**

Add an output callback:

```python
def _archive_progress(progress: ArchiveProgress) -> None:
    request = progress.request
    typer.echo(
        f"[{progress.index}/{progress.total}] {request.season} "
        f"wk{request.week:02d} {request.kind.value}: {progress.line_count} lines"
    )
    _warn_skipped(f"snapshot {progress.index} rows not stored", progress.skipped)
```

Replace the body of `backfill_history` after opening Store with:

```python
dataset = store.load_seasons(Sport.NFL, start, end)
client_factory = lambda: OddsClient(config.odds_api_key())
archive = ArchiveBackfill(store, TeamResolver.default(), client_factory)
try:
    report = archive.run(
        dataset.games,
        max_credits=max_credits,
        execute=execute,
        on_progress=_archive_progress,
    )
except NoHistoricalGames as exc:
    typer.secho(
        f"{exc}; run `pickem backfill` first", fg="red", err=True
    )
    raise typer.Exit(code=1) from exc
except CreditLimitExceeded as exc:
    typer.secho(str(exc), fg="red", err=True)
    raise typer.Exit(code=1) from exc
except ArchiveRunInterrupted as exc:
    typer.secho(str(exc), fg="red", err=True)
    raise typer.Exit(code=1) from exc

typer.echo(
    f"{report.total_snapshots} snapshots ({report.frozen_snapshots} frozen, "
    f"{report.submission_snapshots} submission) across {report.weeks} weeks "
    f"= {report.credits} credits"
)
if not execute:
    typer.secho("dry run — pass --execute to spend credits and write rows", fg="yellow")
```

Remove direct imports of `SnapshotKind`, `estimate_credits`, `plan_snapshots`, `FROZEN_SOURCE`, and `SUBMISSION_SOURCE` from CLI.

- [ ] **Step 4: Verify CLI translation and archive behavior**

Run: `uv run pytest tests/test_archive.py tests/test_cli.py -k 'archive or backfill_history' -v`

Expected: all pass.

Run: `rg -n 'plan_snapshots|estimate_credits|SnapshotKind|FROZEN_SOURCE|SUBMISSION_SOURCE' src/pickem/cli.py`

Expected: no matches.

- [ ] **Step 5: Run the full verification gate and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all pass.

```bash
git add src/pickem/cli.py tests/test_cli.py
git commit -m "refactor: make historical backfill a thin CLI command"
```

---

### Task 7: Create the canonical matchup module

**Files:**
- Create: `src/pickem/resolve/matchup.py`
- Create: `tests/test_matchup.py`
- Read, do not modify: `src/pickem/resolve/resolver.py`
- Read, do not modify: `src/pickem/models.py`

**Interfaces:**
- Consumes: `TeamResolver`, source team names, `Sport`, season, and week.
- Produces: `CanonicalMatchup(sport, season, week, away_team_id, home_team_id)` with a `game_id` property.
- Produces: `resolve_matchup(resolver, sport, season, week, away_name, home_name) -> CanonicalMatchup`.
- Preserves: exact `make_game_id` format and `UnknownTeamError` propagation.

- [ ] **Step 1: Write failing canonical matchup tests**

Create `tests/test_matchup.py`:

```python
import pytest

from pickem.models import Sport
from pickem.resolve.matchup import CanonicalMatchup, resolve_matchup
from pickem.resolve.resolver import TeamResolver, UnknownTeamError


def test_resolve_matchup_preserves_away_home_order_and_exact_game_id():
    matchup = resolve_matchup(
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        away_name="Buffalo Bills",
        home_name="Miami Dolphins",
    )
    assert matchup.away_team_id == "BUF"
    assert matchup.home_team_id == "MIA"
    assert matchup.game_id == "nfl-2025-03-BUF-at-MIA"


def test_canonical_matchup_rejects_the_same_team_on_both_sides():
    with pytest.raises(ValueError):
        CanonicalMatchup(
            sport=Sport.NFL,
            season=2025,
            week=3,
            away_team_id="BUF",
            home_team_id="BUF",
        )


def test_resolve_matchup_preserves_fail_loud_resolution():
    with pytest.raises(UnknownTeamError):
        resolve_matchup(
            resolver=TeamResolver.default(),
            sport=Sport.NFL,
            season=2025,
            week=3,
            away_name="Fictional Aardvarks",
            home_name="Miami Dolphins",
        )
```

- [ ] **Step 2: Run the matchup tests and verify they fail**

Run: `uv run pytest tests/test_matchup.py -v`

Expected: collection fails because `pickem.resolve.matchup` does not exist.

- [ ] **Step 3: Implement canonical matchup identity**

Create `src/pickem/resolve/matchup.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, model_validator

from pickem.models import Sport, make_game_id
from pickem.resolve.resolver import TeamResolver


class CanonicalMatchup(BaseModel):
    sport: Sport
    season: int
    week: int
    away_team_id: str
    home_team_id: str

    @model_validator(mode="after")
    def _teams_must_differ(self) -> CanonicalMatchup:
        if self.away_team_id == self.home_team_id:
            raise ValueError(f"a team cannot play itself: {self.away_team_id}")
        return self

    @property
    def game_id(self) -> str:
        return make_game_id(
            self.sport,
            self.season,
            self.week,
            self.away_team_id,
            self.home_team_id,
        )


def resolve_matchup(
    *,
    resolver: TeamResolver,
    sport: Sport,
    season: int,
    week: int,
    away_name: str,
    home_name: str,
) -> CanonicalMatchup:
    return CanonicalMatchup(
        sport=sport,
        season=season,
        week=week,
        away_team_id=resolver.resolve(away_name, sport),
        home_team_id=resolver.resolve(home_name, sport),
    )
```

- [ ] **Step 4: Verify the module through its interface**

Run: `uv run pytest tests/test_matchup.py tests/test_resolver.py tests/test_models.py -v`

Expected: all pass.

- [ ] **Step 5: Run the full verification gate and commit**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all pass.

```bash
git add src/pickem/resolve/matchup.py tests/test_matchup.py
git commit -m "refactor: define canonical matchup identity"
```

---

### Task 8: Migrate every source adapter to canonical matchup identity

**Files:**
- Modify: `src/pickem/ingest/cbs.py`
- Modify: `src/pickem/ingest/cbs_html.py`
- Modify: `src/pickem/ingest/cfbd_source.py`
- Modify: `src/pickem/ingest/nflverse.py`
- Modify: `src/pickem/ingest/odds.py`
- Modify: `tests/test_game_id_contract.py`
- Test: `tests/test_cbs_parser.py`
- Test: `tests/test_cbs_html.py`
- Test: `tests/test_cfbd_adapter.py`
- Test: `tests/test_nflverse_adapter.py`
- Test: `tests/test_odds_adapter.py`

**Interfaces:**
- Consumes: `resolve_matchup(resolver, sport, season, week, away_name, home_name) -> CanonicalMatchup` from Task 7.
- Removes: direct `resolver.resolve(home)`, `resolver.resolve(away)`, and `make_game_id(sport, season, week, away_team_id, home_team_id)` assembly from source adapters.
- Preserves: exact game IDs, source-specific spread normalization, source-specific skip/fail behavior, and both odds guards.

- [ ] **Step 1: Strengthen the cross-source contract test before migration**

In `tests/test_game_id_contract.py`, add one direct identity assertion beside the existing adapter agreement tests:

```python
def test_canonical_matchup_is_the_contract_all_adapters_share():
    matchup = resolve_matchup(
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        away_name="Buffalo Bills",
        home_name="Miami Dolphins",
    )
    assert matchup.game_id == "nfl-2025-03-BUF-at-MIA"
```

Run: `uv run pytest tests/test_game_id_contract.py -v`

Expected: PASS before migration; this pins the destination behavior.

- [ ] **Step 2: Migrate CBS shared construction**

Change `_parsed_game` to accept `matchup: CanonicalMatchup` instead of five identity fields. Construct `Game` and `LeagueLine` from:

```python
game_id = matchup.game_id
sport = matchup.sport
season = matchup.season
week = matchup.week
away_team_id = matchup.away_team_id
home_team_id = matchup.home_team_id
```

Both CBS adapters call `resolve_matchup` after validating that their source row is a priced game. Unknown teams continue to raise before `ParseResult` is returned.

- [ ] **Step 3: Migrate CFBD and nflverse adapters**

Replace each home/away resolution block with:

```python
matchup = resolve_matchup(
    resolver=resolver,
    sport=Sport.CFB,
    season=season,
    week=week,
    away_name=row["away_team"],
    home_name=row["home_team"],
)
```

Use `Sport.NFL`, `row["season"]`, and `row["week"]` for nflverse. Build `Game` and `MarketLine` records from `matchup.game_id`, `matchup.home_team_id`, and `matchup.away_team_id`. Keep CFBD spreads unchanged and continue negating nflverse `spread_line` exactly once.

- [ ] **Step 4: Migrate the odds adapter without moving its guards**

In `_parse_events`, keep kickoff parsing and the `window` rejection before identity resolution. Replace only the existing try block with:

```python
try:
    matchup = resolve_matchup(
        resolver=resolver,
        sport=sport,
        season=season,
        week=week,
        away_name=away_name,
        home_name=home_name,
    )
except UnknownTeamError as exc:
    skipped.append(
        f"{away_name} at {home_name}: not a team we track ({exc}) — not stored"
    )
    continue
game_id = matchup.game_id
```

The canonical slate check remains immediately after this block. Do not move the try block before the kickoff window.

- [ ] **Step 5: Run every adapter and contract test**

Run: `uv run pytest tests/test_matchup.py tests/test_game_id_contract.py tests/test_cbs_parser.py tests/test_cbs_html.py tests/test_cfbd_adapter.py tests/test_nflverse_adapter.py tests/test_odds_adapter.py -v`

Expected: all pass, including sign conventions, unknown-team behavior, out-of-window filtering, slate filtering, and identical IDs across sources.

- [ ] **Step 6: Prove identity assembly is local and run the full verification gate**

Run: `rg -n 'make_game_id|resolver\.resolve' src/pickem/ingest`

Expected: no matches. `make_game_id` remains in `models.py`, `resolve/matchup.py`, tests, and any non-adapter callers that intentionally construct known canonical IDs.

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: all pass.

- [ ] **Step 7: Update the handoff and commit**

Update `docs/HANDOFF.md` “What is built” entries so they state:

```text
src/pickem/edge/pipeline.py       The only public edge decision interface; every
                                  returned Edge is a final pick.
src/pickem/ingest/cbs.py          Complete CBS game + league-line intake records.
src/pickem/store/db.py            Safe writes plus coherent week/range reads.
src/pickem/backtest/archive.py    Paid archive planning/execution policy.
src/pickem/resolve/matchup.py     Canonical matchup identity shared by all adapters.
```

Also record that source-specific skip/fail behavior and spread normalization remain in their adapters.

```bash
git add src/pickem/ingest/cbs.py src/pickem/ingest/cbs_html.py \
  src/pickem/ingest/cfbd_source.py src/pickem/ingest/nflverse.py \
  src/pickem/ingest/odds.py tests/test_game_id_contract.py \
  tests/test_cbs_parser.py tests/test_cbs_html.py tests/test_cfbd_adapter.py \
  tests/test_nflverse_adapter.py tests/test_odds_adapter.py docs/HANDOFF.md
git commit -m "refactor: share canonical matchup identity across adapters"
```

---

## Final Verification

- [ ] Run the entire offline test suite:

```bash
uv run pytest -q
```

Expected: all tests pass; the count may change because superseded shallow-interface tests are deleted and replacement interface tests are added.

- [ ] Run lint and formatting checks:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: both commands exit zero with no findings.

- [ ] Prove the CLI still exposes all nine commands:

```bash
uv run pickem --help
```

Expected: `ingest-cbs`, `poll-odds`, `report`, `sync-results`, `backfill`, `backfill-cfb`, `backfill-history`, `backtest`, and `calibrate` are listed.

- [ ] Prove architecture leakage did not return:

```bash
rg -n '\bcompute_edge\b|\bapply_tiebreaks\b|market_lines_for|league_lines_for_week|games_for_week' src tests
rg -n 'make_game_id|resolver\.resolve' src/pickem/ingest
```

Expected: both commands print no matches.

- [ ] Inspect the final diff for accidental strategy or schema changes:

```bash
git diff --stat HEAD~8..HEAD
git diff HEAD~8..HEAD -- src/pickem/store/schema.sql src/pickem/edge/elo.py
```

Expected: the stat contains only planned files; the focused diff is empty because this plan does not change the schema or Elo implementation.
