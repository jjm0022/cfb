# Decision Logging and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every pick and every unattended run a durable, queryable record on disk, so "did the refresh fire" and "why did it choose this side" are answerable from `~/LOGS/pickem/` without reading code or querying DuckDB.

**Architecture:** A leaf module `pickem.obs.log` owns sink configuration, secret redaction, standard-library interception, and a run-context manager. Entry points configure it once; every other module only calls `logger` with an `event` name and bound fields. Instrumentation surfaces decision artifacts the code already computes — `Edge.rationale`, `MarketLinesResult.skipped`, `PreflightGame.reasons` — rather than inventing new ones.

**Tech Stack:** Python 3.12, loguru 0.7.3, Typer, discord.py, APScheduler, DuckDB, Pydantic, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-04-logging-observability-design.md`

**Standard:** `~/.agents/skills/logging-standards/` — the machine-wide conventions this plan implements. Read `SKILL.md` and `python-loguru.md` before Task 1.

## Global Constraints

- Log directory is `~/LOGS/pickem`, overridable by `PICKEM_LOG_DIR`. Sinks: `pickem.log` (INFO, 30d), `pickem.jsonl` (DEBUG, 90d), `errors.log` (ERROR, 180d). All rotate daily at `00:00` with `compression="zip"`.
- Every sink sets `enqueue=True`, `backtrace=False`, `diagnose=False`, `catch=True`.
- Every log call binds a stable snake_case `event` name. Variable facts go in bound fields, never interpolated into the message string.
- Levels: DEBUG = intermediate math; INFO = a decision or state change; WARNING = dropped/degraded/retried/fell back; ERROR = an operation failed; CRITICAL = the service cannot continue.
- **Every `skipped` one-liner is a WARNING.**
- **No log-and-throw.** A function that raises does not log. Only the frame that handles the exception logs it, exactly once.
- `configure_logging()` is called only from `cli.py`'s Typer callback and `discord_bot.main()`. Never at import time, never in a library module.
- Secrets (`ODDS_API_KEY`, `CFBD_API_KEY`, `DISCORD_BOT_TOKEN`) must never appear in any sink.
- No existing function signature changes and no existing behavior changes. Instrumentation only observes.
- Tests reading a sink file must call `logger.complete()` first — `enqueue=True` defers writes to a background thread.
- Run `uv run pytest` and `uv run ruff check src tests` before each commit.

## File Structure

- `src/pickem/obs/__init__.py`, `src/pickem/obs/log.py` — the only module that configures logging. No dependency on any other project module.
- `src/pickem/cli.py` — Typer callback calls `configure_logging`; each command body wrapped in `run_context`.
- `src/pickem/discord_bot.py` — `main()` calls `configure_logging`; commands, scheduler jobs, and scope resolution instrumented.
- `src/pickem/ingest/odds.py` — request/retry/quota records; a `_skip` helper that appends to `skipped` and logs in one place.
- `src/pickem/ingest/cbs.py` — parse and skip records.
- `src/pickem/edge/divergence.py`, `src/pickem/edge/pipeline.py` — consensus, measurement, tiebreak, final decision.
- `src/pickem/operations/recommendations.py`, `src/pickem/operations/preflight.py` — snapshot and readiness records.
- `src/pickem/automation/monitor.py` — refresh lifecycle, change diff, state writes, notification suppression.
- `src/pickem/store/db.py` — schema and write records at DEBUG.
- `src/pickem/backtest/runner.py` — run-boundary and totals records only.
- `tests/test_obs_log.py` — new. `tests/conftest.py` — new shared `records` fixture.
- `docs/runbooks/verifying-from-logs.md` — new.

---

### Task 1: The logging module

**Files:**
- Create: `src/pickem/obs/__init__.py`, `src/pickem/obs/log.py`
- Create: `tests/test_obs_log.py`, `tests/conftest.py`
- Modify: `pyproject.toml:11-24` (dependencies)

**Interfaces:**
- Produces: `configure_logging(project: str = "pickem", *, log_dir: Path | str | None = None, level: str | None = None, console: str | None = None) -> Path`
- Produces: `run_context(entry: str, **facts: object) -> Iterator[str]` — a context manager yielding an 8-character `run_id`.
- Produces: pytest fixture `records` in `tests/conftest.py` yielding `list[dict]` of captured loguru records.
- Consumes: nothing from this codebase.

- [ ] **Step 1: Add the dependency**

Add to `pyproject.toml` `[project].dependencies`, keeping alphabetical order (after `httpx`):

```toml
    "loguru>=0.7.3,<0.8",
```

Run: `uv sync`

- [ ] **Step 2: Write the failing tests**

Create `tests/conftest.py`:

```python
import pytest
from loguru import logger


@pytest.fixture
def records():
    """Capture loguru records as dicts. Loguru does not route through caplog."""
    captured: list[dict] = []
    logger.remove()
    sink_id = logger.add(lambda message: captured.append(message.record), level="DEBUG")
    yield captured
    logger.remove(sink_id)
```

Create `tests/test_obs_log.py`:

```python
import json

import pytest
from loguru import logger

from pickem.obs.log import configure_logging, run_context

SECRET = "sk-live-abcdef0123456789"


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", SECRET)
    monkeypatch.setenv("PICKEM_LOG_CONSOLE", "off")
    directory = tmp_path / "logs"
    configure_logging("pickem", log_dir=directory)
    yield directory
    logger.remove()


def _rows(log_dir):
    logger.complete()
    return [json.loads(line) for line in (log_dir / "pickem.jsonl").read_text().splitlines() if line.strip()]


def test_creates_the_three_sinks(log_dir):
    logger.bind(event="probe").info("hello")
    logger.complete()
    assert (log_dir / "pickem.log").exists()
    assert (log_dir / "pickem.jsonl").exists()
    assert (log_dir / "errors.log").exists()


def test_jsonl_rows_are_flat_with_bound_fields_at_top_level(log_dir):
    logger.bind(event="edge_decided", game_id="cfb-2026-01-BAY-at-AUB", tier="STRONG", delta=2.0).info(
        "league +3.5 vs market +1.5: 2.0 pts toward away"
    )
    row = next(r for r in _rows(log_dir) if r["event"] == "edge_decided")
    assert "record" not in row  # loguru's nested serialize=True format
    assert row["tier"] == "STRONG"
    assert row["delta"] == 2.0
    assert row["message"] == "league +3.5 vs market +1.5: 2.0 pts toward away"


def test_run_context_correlates_every_record_and_brackets_the_run(log_dir):
    with run_context("test:probe", sport="cfb", season=2026, week=1) as run_id:
        logger.bind(event="edge_decided").info("picked")
    rows = _rows(log_dir)
    assert {r["run_id"] for r in rows} == {run_id}
    assert rows[0]["event"] == "run_started"
    assert rows[-1]["event"] == "run_finished"
    assert rows[-1]["duration_ms"] >= 0
    assert rows[0]["sport"] == "cfb"


def test_run_context_logs_one_error_and_reraises(log_dir):
    with pytest.raises(ValueError), run_context("test:boom"):
        raise ValueError("nope")
    rows = _rows(log_dir)
    failures = [r for r in rows if r["level"] == "ERROR"]
    assert len(failures) == 1
    assert failures[0]["event"] == "run_failed"
    assert failures[0]["error"] == "ValueError"


def test_secret_is_redacted_even_when_it_arrives_via_exception_text(log_dir):
    try:
        raise RuntimeError(f"odds api unreachable: https://api/x?apiKey={SECRET}")
    except RuntimeError as exc:
        logger.bind(event="odds_request_failed").opt(exception=True).error(str(exc))
    logger.complete()
    for name in ("pickem.log", "pickem.jsonl", "errors.log"):
        text = (log_dir / name).read_text()
        assert SECRET not in text
    errors = (log_dir / "errors.log").read_text()
    assert "***REDACTED***" in errors
    assert "Traceback (most recent call last)" in errors


def test_debug_reaches_jsonl_but_not_the_text_sink(log_dir):
    logger.bind(event="consensus_computed").debug("median of 6 books")
    logger.complete()
    assert "median of 6 books" not in (log_dir / "pickem.log").read_text()
    assert any(r["event"] == "consensus_computed" for r in _rows(log_dir))


def test_configure_is_idempotent(log_dir, tmp_path):
    configure_logging("pickem", log_dir=log_dir)
    logger.bind(event="probe").info("once")
    assert sum(r["event"] == "probe" for r in _rows(log_dir)) == 1


def test_stdlib_records_are_intercepted(log_dir):
    import logging

    logging.getLogger("apscheduler.executors").info("Running job 'refresh'")
    assert any("Running job" in r["message"] for r in _rows(log_dir))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_obs_log.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'pickem.obs'`

- [ ] **Step 4: Create the module**

Create empty `src/pickem/obs/__init__.py`.

Create `src/pickem/obs/log.py` by copying the module code block verbatim from the `## The module` section of `~/.agents/skills/logging-standards/python-loguru.md`. That code is already verified against loguru 0.7.3. Make no changes to it: its `_SECRET_ENV_VARS` tuple (`ODDS_API_KEY`, `CFBD_API_KEY`, `DISCORD_BOT_TOKEN`) and its default project name (`pickem`) are already correct for this project.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_obs_log.py -v`

Expected: 8 passed.

- [ ] **Step 6: Confirm the existing suite is unaffected**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass. The new `tests/conftest.py` `records` fixture is opt-in and must not alter existing tests.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/pickem/obs tests/test_obs_log.py tests/conftest.py
git commit -m "feat: add loguru logging module with redaction and run context"
```

---

### Task 2: Wire the entry points

**Files:**
- Modify: `src/pickem/cli.py:61` (after `app = typer.Typer(...)`)
- Modify: `src/pickem/discord_bot.py:610-612` (`main`)
- Test: `tests/test_obs_log.py` (append)

**Interfaces:**
- Consumes: `configure_logging`, `run_context` from Task 1.
- Produces: a Typer callback `_configure(ctx: typer.Context) -> None` that runs before every command.
- Produces: `entry` naming convention — `cli:<command-name>`, `discord:/<command>`, `sched:refresh`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_log.py`:

```python
def test_cli_callback_configures_logging(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pickem.cli import app

    monkeypatch.setenv("PICKEM_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PICKEM_LOG_CONSOLE", "off")
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert (tmp_path / "logs").is_dir()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_obs_log.py::test_cli_callback_configures_logging -v`

Expected: FAIL — the log directory is not created.

- [ ] **Step 3: Add the CLI callback**

In `src/pickem/cli.py`, add the import alongside the other `pickem` imports:

```python
from pickem.obs.log import configure_logging, run_context
```

Immediately after `app = typer.Typer(...)`:

```python
@app.callback()
def _configure() -> None:
    """Configure logging once, before any command body runs."""
    configure_logging("pickem")
```

- [ ] **Step 4: Wrap the two commands that run unattended work**

In `poll_odds` (`src/pickem/cli.py:130`), wrap the existing body — from the first statement through the final `_warn_skipped(...)` — in:

```python
    with run_context("cli:poll-odds", sport=sport.value, season=season, week=week):
        ...  # existing body, indented one level
```

Do the same in `report` (`src/pickem/cli.py:222`) with `run_context("cli:report", sport=sport.value, season=season, week=week)`. Leave the other commands to the callback's configuration only; they gain run context in Task 9.

- [ ] **Step 5: Configure the bot entry point**

In `src/pickem/discord_bot.py`, add to the imports:

```python
from pickem.obs.log import configure_logging
```

Change `main`:

```python
def main() -> None:
    configure_logging("pickem")
    settings = DiscordSettings.from_env()
    logger.bind(event="bot_starting", db=str(settings.db)).info("starting discord bot")
    create_bot(settings).run(settings.token)
```

Replace the module's `logger = logging.getLogger(__name__)` (`src/pickem/discord_bot.py:33`) with `from loguru import logger` and drop the now-unused `import logging`.

- [ ] **Step 6: Update the two existing scheduled-failure log calls**

The `logger.error("scheduled recommendation refresh failed: %s", ...)` calls at `src/pickem/discord_bot.py:118` and `:125` use stdlib `%s` interpolation, which loguru does not apply. Change both to:

```python
                logger.bind(
                    event="refresh_failed",
                    scope=f"{scope.sport.value}/{scope.season}/wk{scope.week}",
                ).error(_scheduled_error_message(settings, scope_result.error))
```

For the single-`RefreshResult` branch at `:118`, omit the `scope` field.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass, including the existing `tests/test_discord_bot.py` scheduled-failure tests. If those assert on `caplog`, update them to use the `records` fixture and assert `record["extra"]["event"] == "refresh_failed"`.

- [ ] **Step 8: Commit**

```bash
git add src/pickem/cli.py src/pickem/discord_bot.py tests/
git commit -m "feat: configure logging at the CLI and bot entry points"
```

---

### Task 3: Instrument the odds feed

**Files:**
- Modify: `src/pickem/ingest/odds.py:84-186` (`_parse_events`), `:220-241` (`_get`), `:245-300` (`fetch_spreads`)
- Test: `tests/test_odds.py`

**Interfaces:**
- Produces: `_skip(skipped: list[str], *, guard: str, message: str, **fields: object) -> None` — appends to `skipped` and emits one WARNING with `event="odds_row_skipped"` and a `guard` field.
- Produces events: `odds_request`, `odds_retry`, `odds_quota`, `odds_event_filtered`, `odds_row_skipped`, `odds_polled`.
- Consumes: `run_context` bindings from Task 2 (no parameters passed; context is ambient).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_odds.py`:

```python
def test_events_outside_the_window_are_logged_with_the_guard_that_dropped_them(records):
    result = _parse_events(
        [
            {
                "home_team": "Auburn",
                "away_team": "Baylor",
                "commence_time": "2026-12-25T00:00:00Z",
                "bookmakers": [],
            }
        ],
        resolver=TeamResolver.default(),
        sport=Sport.CFB,
        season=2026,
        week=1,
        slate=set(),
        window=(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC)),
        captured_at=datetime(2026, 9, 4, tzinfo=UTC),
        source=LIVE_SOURCE,
    )

    assert len(result.skipped) == 1
    filtered = [r for r in records if r["extra"]["event"] == "odds_row_skipped"]
    assert len(filtered) == 1
    assert filtered[0]["level"].name == "WARNING"
    assert filtered[0]["extra"]["guard"] == "kickoff_window"


def test_unmapped_team_is_logged_once_not_twice(records):
    result = _parse_events(
        [
            {
                "home_team": "Nowhere State",
                "away_team": "Baylor",
                "commence_time": "2026-09-04T00:00:00Z",
                "bookmakers": [],
            }
        ],
        resolver=TeamResolver.default(),
        sport=Sport.CFB,
        season=2026,
        week=1,
        slate=set(),
        window=(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC)),
        captured_at=datetime(2026, 9, 4, tzinfo=UTC),
        source=LIVE_SOURCE,
    )

    assert len(result.skipped) == 1
    emitted = [r for r in records if r["extra"]["event"] == "odds_row_skipped"]
    assert len(emitted) == 1  # no log-and-throw duplicate from the resolver
    assert emitted[0]["extra"]["guard"] == "unknown_team"
```

Add `records` to the test module's imports only if a local conftest shadows the root one; otherwise the root fixture applies.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_odds.py -k "guard or logged_once" -v`

Expected: FAIL — no records with `event="odds_row_skipped"`.

- [ ] **Step 3: Add the skip helper and route every skip through it**

At the top of `src/pickem/ingest/odds.py`:

```python
from loguru import logger
```

Add above `_parse_events`:

```python
def _skip(skipped: list[str], *, guard: str, message: str, **fields: object) -> None:
    """Record a dropped row in one place: the result list and the log agree."""
    skipped.append(message)
    logger.bind(event="odds_row_skipped", guard=guard, **fields).warning(message)
```

Replace each of the six `skipped.append(...)` calls in `_parse_events` with a `_skip` call carrying the guard name, preserving the existing message text exactly:

| Existing site | `guard` value | Extra fields |
| --- | --- | --- |
| unreadable `commence_time` | `"commence_time"` | `raw=event.get("commence_time")` |
| outside window | `"kickoff_window"` | `kickoff=kickoff.isoformat()` |
| `UnknownTeamError` | `"unknown_team"` | `away=away_name, home=home_name` |
| not in slate | `"slate"` | `game_id=game_id` |
| no spreads market | `"no_spreads_market"` | `game_id=game_id, book=book_key` |
| no spread point | `"no_spread_point"` | `game_id=game_id, book=book_key` |

Do **not** add a log inside `TeamResolver.resolve` — the handler here logs it, once.

- [ ] **Step 4: Instrument the request path**

In `_get` (`src/pickem/ingest/odds.py:220`), inside the retry loop before the call:

```python
            logger.bind(
                event="odds_request", path=path, attempt=attempt + 1, max_attempts=MAX_ATTEMPTS
            ).debug(f"GET {path}")
```

After a transport error or 5xx, before the backoff sleep:

```python
            logger.bind(
                event="odds_retry", path=path, attempt=attempt + 1, error=str(last_error)
            ).warning(f"{path} failed; retrying")
```

`_get` raises on final failure and must not log there — the caller logs it.

- [ ] **Step 5: Record quota and the poll result**

In `fetch_spreads`, after the status-code guards and before `_parse_events`:

```python
        logger.bind(
            event="odds_quota",
            remaining=response.headers.get("x-requests-remaining"),
            used=response.headers.get("x-requests-used"),
        ).info("odds api quota")
```

After `_parse_events` returns, replace the bare `return` with:

```python
        result = _parse_events(...)  # existing call unchanged
        logger.bind(
            event="odds_polled",
            sport=sport.value,
            season=season,
            week=week,
            lines=len(result.lines),
            books=len({line.book for line in result.lines}),
            games=len({line.game_id for line in result.lines}),
            skipped=len(result.skipped),
        ).info(f"polled {len(result.lines)} market lines")
        return result
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_odds.py -v && uv run ruff check src tests`

Expected: PASS, with the existing odds tests unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/ingest/odds.py tests/test_odds.py
git commit -m "feat: log odds requests, quota, and every dropped row with its guard"
```

---

### Task 4: Instrument the decision layer

**Files:**
- Modify: `src/pickem/edge/divergence.py:35-96` (`consensus_spread`, `_compute_edge`)
- Modify: `src/pickem/edge/pipeline.py:46-90` (`_apply_tiebreaks`)
- Test: `tests/test_divergence.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces events: `consensus_computed` (DEBUG), `edge_measured` (DEBUG), `tiebreak_applied` (INFO), `edge_decided` (INFO), `edges_ranked` (INFO).
- Consumes: `_skip` conventions from Task 3 only by example; no code dependency.

These modules are documented as pure — "no network, no database, no filesystem". Logging does not violate that: `logger` is an in-process sink-agnostic call, and with no sinks configured (the default in unit tests) it is a no-op. Preserve the docstring's intent by keeping every log call free of side effects on the returned values.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_divergence.py`:

```python
def test_edge_measurement_records_the_thresholds_in_effect(records):
    edge = _compute_edge(
        LeagueLine(game_id="g1", source=FROZEN_SOURCE, spread_home=-3.5, posted_at=NOW),
        [MarketLine(game_id="g1", source=LIVE_SOURCE, book="dk", spread_home=-1.5, captured_at=NOW)],
    )

    measured = next(r for r in records if r["extra"]["event"] == "edge_measured")
    assert measured["extra"]["delta"] == pytest.approx(-2.0)
    assert measured["extra"]["tier"] == edge.tier.value
    assert measured["extra"]["threshold_strong"] == 2.0
    assert measured["extra"]["threshold_lean"] == 1.0


def test_consensus_records_the_books_it_collapsed(records):
    lines = [
        MarketLine(game_id="g1", source=LIVE_SOURCE, book="dk", spread_home=-1.0, captured_at=EARLIER),
        MarketLine(game_id="g1", source=LIVE_SOURCE, book="dk", spread_home=-2.0, captured_at=NOW),
        MarketLine(game_id="g1", source=LIVE_SOURCE, book="fd", spread_home=-3.0, captured_at=NOW),
    ]

    assert consensus_spread(lines) == pytest.approx(-2.5)
    computed = next(r for r in records if r["extra"]["event"] == "consensus_computed")
    assert computed["extra"]["books_used"] == 2
    assert computed["extra"]["snapshots_collapsed"] == 1
```

Append to `tests/test_pipeline.py`:

```python
def test_every_decided_edge_is_logged_with_its_rationale(records):
    edges = decide_edges(LEAGUE_LINES, MARKET_LINES, GAMES, HISTORY)

    decided = [r for r in records if r["extra"]["event"] == "edge_decided"]
    assert len(decided) == len(edges)
    by_game = {r["extra"]["game_id"]: r for r in decided}
    for edge in edges:
        assert by_game[edge.game_id]["message"] == edge.rationale
        assert by_game[edge.game_id]["extra"]["side"] == edge.side.value
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_divergence.py tests/test_pipeline.py -k "records or logged or rationale" -v`

Expected: FAIL — `StopIteration` from the `next(...)` lookups.

- [ ] **Step 3: Instrument `consensus_spread`**

Add `from loguru import logger` to `src/pickem/edge/divergence.py`. Before the `return` in `consensus_spread`:

```python
    result = median(line.spread_home for line in latest.values())
    logger.bind(
        event="consensus_computed",
        game_id=next(iter(latest.values())).game_id,
        consensus=result,
        books_used=len(latest),
        snapshots_collapsed=len(lines) - len(latest),
        per_book={book: line.spread_home for book, line in latest.items()},
    ).debug(f"consensus {result:+.1f} across {len(latest)} books")
    return result
```

- [ ] **Step 4: Instrument `_compute_edge`**

In the `consensus is None` branch, before returning:

```python
        logger.bind(
            event="edge_measured",
            game_id=league.game_id,
            tier=Tier.NO_MARKET.value,
            league_spread=league.spread_home,
            market_spread=None,
            delta=0.0,
        ).debug("no market line available")
```

In the measured branch, after computing `delta` and the tier:

```python
    tier = _tier(delta, thresholds)
    logger.bind(
        event="edge_measured",
        game_id=league.game_id,
        tier=tier.value,
        side=side.value,
        league_spread=league.spread_home,
        market_spread=consensus,
        delta=delta,
        threshold_strong=thresholds.strong,
        threshold_lean=thresholds.lean,
    ).debug(f"{abs(delta):.1f} pts toward {moved_toward}")
```

Use the local `tier` in the returned `Edge` rather than calling `_tier` twice.

- [ ] **Step 5: Instrument the tiebreak and the final decision**

Add `from loguru import logger` to `src/pickem/edge/pipeline.py`. After computing `side` in `_apply_tiebreaks`:

```python
        logger.bind(
            event="tiebreak_applied",
            game_id=edge.game_id,
            tier=edge.tier.value,
            projected_margin=margin,
            league_spread=edge.league_spread,
            side=side.value,
        ).info(f"Elo tiebreak projects home by {margin:+.1f}")
```

At the end of `decide_edges`, replace `return _apply_tiebreaks(...)` with:

```python
    decided = _apply_tiebreaks(measured, games, history, elo_config)
    for edge in decided:
        logger.bind(
            event="edge_decided",
            game_id=edge.game_id,
            side=edge.side.value,
            tier=edge.tier.value,
            delta=round(edge.delta, 3),
            league_spread=edge.league_spread,
            market_spread=edge.market_spread,
        ).info(edge.rationale)
    return decided
```

- [ ] **Step 6: Log the ranking**

In `rank_edges` (`src/pickem/edge/divergence.py:99`), before returning:

```python
    ranked = sorted(edges, key=lambda e: abs(e.delta), reverse=True)
    logger.bind(
        event="edges_ranked",
        count=len(ranked),
        order=[edge.game_id for edge in ranked],
    ).info(f"ranked {len(ranked)} edges")
    return ranked
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/pickem/edge tests/test_divergence.py tests/test_pipeline.py
git commit -m "feat: log consensus, edge measurement, tiebreaks, and every decision"
```

---

### Task 5: Instrument recommendations and preflight

**Files:**
- Modify: `src/pickem/operations/recommendations.py:47-115`
- Modify: `src/pickem/operations/preflight.py:41-` (`evaluate_preflight`)
- Test: `tests/test_recommendations.py`, `tests/test_preflight.py`

**Interfaces:**
- Produces events: `recommendations_generated`, `preflight_evaluated`, `preflight_game_not_ready`.
- Consumes: `Edge` records emitted by Task 4 under the same ambient run context.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recommendations.py`:

```python
def test_generation_logs_a_summary(db, seeded_week, records):
    snapshot = generate_recommendations(db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC))

    summary = next(r for r in records if r["extra"]["event"] == "recommendations_generated")
    assert summary["extra"]["edges"] == len(snapshot.edges)
    assert summary["extra"]["sport"] == "nfl"
    assert summary["extra"]["week"] == 1
```

Append to `tests/test_preflight.py`:

```python
def test_not_ready_games_log_their_reasons(records, stale_dataset):
    result = evaluate_preflight(stale_dataset, [], sport=Sport.CFB, now=NOW, expected_games=1)

    assert not result.ready
    warnings = [r for r in records if r["extra"]["event"] == "preflight_game_not_ready"]
    assert warnings and warnings[0]["level"].name == "WARNING"
    assert warnings[0]["extra"]["reasons"] == result.games[0].reasons
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_recommendations.py tests/test_preflight.py -k "logs or reasons" -v`

Expected: FAIL — no matching records.

- [ ] **Step 3: Instrument `generate_recommendations`**

Add `from loguru import logger` to `src/pickem/operations/recommendations.py`. Before the `return` in `generate_recommendations`:

```python
    ranked = tuple(rank_edges(edges))
    logger.bind(
        event="recommendations_generated",
        sport=sport.value,
        season=season,
        week=week,
        edges=len(ranked),
        tiers={tier.value: sum(1 for e in ranked if e.tier is tier) for tier in Tier},
    ).info(f"generated {len(ranked)} recommendations")
    return RecommendationSnapshot(sport, season, week, now, ranked)
```

Add `Tier` to the imports from `pickem.models`.

- [ ] **Step 4: Instrument `evaluate_preflight`**

Add `from loguru import logger` to `src/pickem/operations/preflight.py`. Before returning `PreflightResult`:

Insert between the `for game_result in game_results:` loop that extends `reasons` (`src/pickem/operations/preflight.py:135-136`) and the `return PreflightResult(...)`:

```python
    for game_result in game_results:
        if not game_result.ready:
            logger.bind(
                event="preflight_game_not_ready",
                game_id=game_result.game_id,
                reasons=game_result.reasons,
                distinct_books=game_result.distinct_books,
                latest_snapshot_at=game_result.latest_snapshot_at,
            ).warning(f"{game_result.game_id} not ready: {'; '.join(game_result.reasons)}")

    logger.bind(
        event="preflight_evaluated",
        sport=target_sport.value,
        ready=not reasons,
        expected_games=expected_games,
        game_count=len(dataset.games),
        league_line_count=len(dataset.league_lines),
        not_ready=sum(1 for game_result in game_results if not game_result.ready),
    ).info("preflight ready" if not reasons else "preflight not ready")
```

Note `target_sport`, not `sport`: the parameter is widened by `Sport(sport)` at
`src/pickem/operations/preflight.py:67`, and `ready` is not a local — the result
is derived from `not reasons`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/operations tests/test_recommendations.py tests/test_preflight.py
git commit -m "feat: log recommendation summaries and preflight readiness reasons"
```

---

### Task 6: Instrument the automation monitor

**Files:**
- Modify: `src/pickem/automation/monitor.py:118-226`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Produces events: `refresh_started`, `refresh_succeeded`, `refresh_failed`, `recommendation_changed`, `state_saved`, `notify_sent`, `notify_suppressed`.
- Consumes: nothing new.

`notify_suppressed` is the record that makes silence unambiguous: it distinguishes "the failure repeated and we deduplicated the DM" from "nothing happened".

- [ ] **Step 1: Write the failing test**

Append to `tests/test_monitor.py`:

```python
async def test_repeat_failure_logs_the_suppressed_notification(records):
    state = AutomationState(error_fingerprint="RuntimeError: boom")
    monitor = RecommendationMonitor(
        refresh_week=_raising(RuntimeError("boom")),
        load_state=lambda scope: state,
        save_state=lambda scope, new: None,
        notify=_fail_if_called,
        scope=SCOPE,
    )

    result = await monitor.refresh()

    assert result.error is not None
    suppressed = next(r for r in records if r["extra"]["event"] == "notify_suppressed")
    assert suppressed["extra"]["fingerprint"] == "RuntimeError: boom"


async def test_successful_refresh_logs_the_change_diff(records, changing_monitor):
    result = await changing_monitor.refresh()

    assert result.changed
    changed = next(r for r in records if r["extra"]["event"] == "recommendation_changed")
    assert changed["message"].startswith("Recommendations changed:")
    succeeded = next(r for r in records if r["extra"]["event"] == "refresh_succeeded")
    assert succeeded["extra"]["changed"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_monitor.py -k "suppressed or change_diff" -v`

Expected: FAIL — no matching records.

- [ ] **Step 3: Instrument `refresh`**

Add `from loguru import logger` to `src/pickem/automation/monitor.py`. At the top of `refresh`, inside the lock:

```python
            logger.bind(
                event="refresh_started",
                sport=self._scope.sport.value,
                season=self._scope.season,
                week=self._scope.week,
            ).info("refresh started")
```

- [ ] **Step 4: Instrument the failure and suppression paths**

In `_record_failure`, replace the notify guard with:

```python
        try:
            if state.error_fingerprint != fingerprint:
                await _invoke(self._notify, f"Recommendation refresh failed: {error}")
                logger.bind(event="notify_sent", fingerprint=fingerprint).info("failure DM sent")
            else:
                logger.bind(event="notify_suppressed", fingerprint=fingerprint).debug(
                    "duplicate failure; DM suppressed"
                )
```

Then, immediately before `return RefreshResult(False, error=error)` at the end of `_record_failure`:

```python
        logger.bind(
            event="refresh_failed",
            sport=self._scope.sport.value,
            week=self._scope.week,
            error=type(error).__name__,
        ).error(f"refresh failed: {error}")
```

Apply the same `notify_sent` / `notify_suppressed` pairing in `_record_load_failure` and `_record_persistence_failure`, keyed on their `self._load_error_fingerprint` and `self._persistence_error_fingerprint` guards.

- [ ] **Step 5: Instrument the success path**

In `_record_success` (`src/pickem/automation/monitor.py:188-218`), change the notify block to capture the message it already builds:

```python
        try:
            if changed:
                message = _change_message(state.signature, snapshot)
                logger.bind(
                    event="recommendation_changed",
                    sport=self._scope.sport.value,
                    season=self._scope.season,
                    week=self._scope.week,
                ).info(message)
                await _invoke(self._notify, message)
```

After the successful `await _invoke(self._save_state, self._scope, next_state)`:

```python
            logger.bind(
                event="state_saved",
                sport=self._scope.sport.value,
                week=self._scope.week,
                signature=signature,
                checked_at=next_state.checked_at,
            ).debug("automation state persisted")
```

Immediately before the final `return RefreshResult(changed, snapshot=snapshot)`:

```python
        logger.bind(
            event="refresh_succeeded",
            sport=self._scope.sport.value,
            season=self._scope.season,
            week=self._scope.week,
            changed=changed,
            edges=len(snapshot.edges),
        ).info("refresh succeeded" + ("; recommendations changed" if changed else ""))
```

`signature`, `changed`, and `next_state` are all locals already defined at the top of `_record_success`; `state.signature` is the prior signature the diff is computed against.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/automation/monitor.py tests/test_monitor.py
git commit -m "feat: log refresh lifecycle, change diffs, and suppressed notifications"
```

---

### Task 7: Instrument the bot and scheduler

**Files:**
- Modify: `src/pickem/discord_bot.py:111-151` (`refresh_job`, `build_schedule`), `:454-459` (`setup_hook`), `:475-484` (`_resolve_scopes`), `:523-538` (`_refresh_scopes`), `:578-593` (`refresh`), `:545-576` (`status`)
- Test: `tests/test_discord_bot.py`

**Interfaces:**
- Produces events: `bot_ready`, `job_scheduled`, `scope_resolved`, `command_invoked`, `command_rejected`. Job firing and command completion are covered by `run_started` / `run_finished` from `run_context` plus APScheduler's own intercepted records — the spec's `job_fired` and `command_completed` are deliberately not emitted separately, because a second timing record for the same span invites the two to disagree.
- Consumes: `run_context` from Task 2.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_discord_bot.py`:

```python
async def test_scope_resolution_is_logged(records, bot):
    scopes = bot._resolve_scopes()

    resolved = next(r for r in records if r["extra"]["event"] == "scope_resolved")
    assert resolved["extra"]["count"] == len(scopes)
    assert resolved["extra"]["scopes"] == [
        f"{s.sport.value}/{s.season}/wk{s.week}" for s in scopes
    ]


async def test_scheduled_refresh_opens_a_run_context(records, bot):
    await _fire_scheduled_refresh(bot)

    started = next(r for r in records if r["extra"]["event"] == "run_started")
    assert started["extra"]["entry"] == "sched:refresh"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_discord_bot.py -k "scope_resolution or run_context" -v`

Expected: FAIL — no matching records.

- [ ] **Step 3: Wrap the scheduled job in a run context**

In `build_schedule`, change `refresh_job`:

```python
    async def refresh_job() -> None:
        with run_context("sched:refresh"):
            callback = getattr(refresh, "refresh", refresh)
            result = await _invoke(callback)
            ...  # existing error-reporting body unchanged
```

Add `run_context` to the `pickem.obs.log` import in `src/pickem/discord_bot.py`.

After both `scheduler.add_job(...)` calls:

```python
    logger.bind(
        event="job_scheduled",
        job="recommendation-refresh",
        days=settings.refresh_days,
        at=f"{settings.refresh_hour:02d}:{settings.refresh_minute:02d}",
        timezone=str(settings.timezone),
    ).info("refresh job scheduled")
```

Emit the equivalent for `pick-reminder` with its own day and time fields.

- [ ] **Step 4: Log scope resolution**

In `_resolve_scopes`:

```python
    def _resolve_scopes(
        self, season: int | None = None, week: int | None = None
    ) -> tuple[MonitorScope, ...]:
        scopes = tuple(
            MonitorScope(sport, scope_season, scope_week)
            for sport, scope_season, scope_week in resolve_pickem_scopes(
                self.settings.db, datetime.now(UTC), season=season, week=week
            )
        )
        logger.bind(
            event="scope_resolved",
            count=len(scopes),
            scopes=[f"{s.sport.value}/{s.season}/wk{s.week}" for s in scopes],
            explicit=season is not None,
        ).info(f"resolved {len(scopes)} active scopes")
        return scopes
```

This is the record that would have made the stale NFL `automation_state` row self-explanatory: an empty or NFL-less scope list is now stated rather than inferred from silence.

- [ ] **Step 5: Log command lifecycle**

In `setup_hook`, after `start()`:

```python
        logger.bind(event="bot_ready", owner_id=self.settings.owner_id).info("bot ready")
```

In `_reject_private`, before returning `True`:

```python
        logger.bind(event="command_rejected", user_id=getattr(getattr(interaction, "user", None), "id", None)).warning(
            "non-owner command rejected"
        )
```

In `refresh` and `status`, immediately after the `_reject_private` guard, open a run context around the remaining body:

```python
        with run_context("discord:/refresh", season=season, week=week):
            logger.bind(event="command_invoked", command="refresh").info("/refresh invoked")
            ...  # existing body, indented one level
```

Use `"discord:/status"` and `command="status"` for `status`. The `run_context` manager already emits `run_finished` with a duration, so no separate `command_completed` record is needed — drop it from the event list rather than duplicating the timing.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/discord_bot.py tests/test_discord_bot.py
git commit -m "feat: log bot commands, scope resolution, and scheduled job firing"
```

---

### Task 8: Instrument CBS ingest and the store

**Files:**
- Modify: `src/pickem/ingest/cbs.py`, `src/pickem/store/db.py:376-405` and the write methods
- Test: `tests/test_cbs.py`, `tests/test_store.py`

**Interfaces:**
- Produces events: `ingest_parsed`, `ingest_row_skipped`, `schema_initialized`, `rows_written`, `state_loaded`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cbs.py`:

```python
def test_unparseable_rows_are_logged_as_warnings(records):
    result = parse_cbs_block(BLOCK_WITH_ONE_BAD_ROW, sport=Sport.CFB, season=2026, week=1)

    assert result.skipped
    warnings = [r for r in records if r["extra"]["event"] == "ingest_row_skipped"]
    assert len(warnings) == len(result.skipped)
    assert all(r["level"].name == "WARNING" for r in warnings)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cbs.py -k logged -v`

Expected: FAIL — no matching records.

- [ ] **Step 3: Instrument the parser**

Add `from loguru import logger` to `src/pickem/ingest/cbs.py`. Wherever the parser appends to its `skipped` list, mirror the Task 3 pattern — append and log one WARNING with `event="ingest_row_skipped"` and a `reason` field. Before returning `ParseResult`:

```python
    logger.bind(
        event="ingest_parsed",
        sport=sport.value,
        season=season,
        week=week,
        games=len(games),
        skipped=len(skipped),
    ).info(f"parsed {len(games)} games")
```

- [ ] **Step 4: Instrument the store at DEBUG**

Add `from loguru import logger` to `src/pickem/store/db.py`.

`Store.__init__` (`src/pickem/store/db.py:64-68`) keeps only `self._con`, so add the path alongside it:

```python
    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self._path = str(path)
        self._con = duckdb.connect(str(path), read_only=read_only)
```

In `init_schema` (`:70-72`), after `self._con.execute(ddl)`:

```python
        logger.bind(event="schema_initialized", db=self._path).debug("schema ready")
```

Every bulk write funnels through `_executemany` (`:85-93`), so instrument it once there rather than in each of the nine write methods:

```python
        if not rows:
            logger.bind(event="rows_written", rows=0, statement=_statement_head(sql)).debug(
                "nothing to write"
            )
            return
        self._con.executemany(sql, rows)
        logger.bind(event="rows_written", rows=len(rows), statement=_statement_head(sql)).debug(
            f"wrote {len(rows)} rows"
        )
```

with a module-level helper:

```python
def _statement_head(sql: str) -> str:
    """The first line of a statement, for identifying the target table in logs."""
    return " ".join(sql.split())[:80]
```

The empty-rows branch is logged deliberately: "a snapshot in which every row was skipped" is the documented ordinary outcome there, and it is exactly the case an operator would otherwise mistake for a write that never happened.

`save_automation_state` (`:389-405`) uses `self._con.execute` directly rather than `_executemany`, so add one record after its INSERT:

```python
        logger.bind(
            event="rows_written", table="automation_state", rows=1, sport=sport.value, week=week
        ).debug("automation state written")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/ingest/cbs.py src/pickem/store/db.py tests/
git commit -m "feat: log CBS ingest skips and store writes"
```

---

### Task 9: Backtest run boundaries and remaining CLI commands

**Files:**
- Modify: `src/pickem/backtest/runner.py:` (`run_backtest`)
- Modify: `src/pickem/cli.py` — wrap the remaining command bodies in `run_context`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces events: `backtest_started`, `backtest_finished`.
- Consumes: `run_context` from Task 2.

Backtest instrumentation is deliberately summary-only: a sweep iterates thousands of games and per-game records would dominate every sink.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runner.py`:

```python
def test_backtest_logs_totals_but_not_per_game_records(records, backtest_fixture):
    result = run_backtest(*backtest_fixture)

    finished = next(r for r in records if r["extra"]["event"] == "backtest_finished")
    assert finished["extra"]["graded"] == result.graded
    assert finished["extra"]["skipped"] == len(result.skipped)
    assert not any(r["extra"]["event"] == "edge_decided" for r in records)
```

If `run_backtest` reaches `decide_edges`, drop the final assertion — Task 4 legitimately emits those — and assert instead that the count is bounded by the fixture's game count.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_runner.py -k totals -v`

Expected: FAIL — no `backtest_finished` record.

- [ ] **Step 3: Add the run-boundary records**

Add `from loguru import logger` to `src/pickem/backtest/runner.py`. At the top of `run_backtest`, before any iteration:

```python
    logger.bind(
        event="backtest_started",
        sport=sport.value,
        seasons=f"{start_season}-{end_season}",
    ).info("backtest started")
```

Before the `return`, bind the counts the result object exposes — read `run_backtest`'s return type and use its own field names rather than inventing them:

```python
    logger.bind(
        event="backtest_finished",
        graded=result.graded,
        skipped=len(result.skipped),
    ).info(f"backtest graded {result.graded} games")
```

If the parameter or result field names differ from those above, keep the two event names and levels and substitute the real names; do not add per-game records.

- [ ] **Step 4: Wrap the remaining CLI commands**

Give each remaining `@app.command` body a `run_context` with entry `cli:<command-name>` matching the registered command string — `cli:ingest-cbs`, `cli:preflight`, `cli:sync-results`, `cli:backfill`, `cli:backfill-cfb`, `cli:backfill-history`, `cli:backtest`, `cli:calibrate`. Pass the sport, season, and week facts each command already has in scope; omit them where a command takes none.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest && uv run ruff check src tests`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/backtest/runner.py src/pickem/cli.py tests/test_runner.py
git commit -m "feat: log backtest boundaries and wrap remaining CLI commands in run context"
```

---

### Task 10: End-to-end verification and the runbook

**Files:**
- Create: `docs/runbooks/verifying-from-logs.md`
- Test: `tests/test_obs_log.py` (append an end-to-end case)

**Interfaces:**
- Consumes: every event produced by Tasks 3–9.

- [ ] **Step 1: Write the failing end-to-end test**

Append to `tests/test_obs_log.py`:

```python
def test_a_refresh_leaves_a_complete_audit_trail(tmp_path, monkeypatch, seeded_db):
    monkeypatch.setenv("PICKEM_LOG_CONSOLE", "off")
    log_dir = tmp_path / "logs"
    configure_logging("pickem", log_dir=log_dir)

    with run_context("test:refresh", sport="cfb", season=2026, week=1):
        snapshot = generate_recommendations(seeded_db, Sport.CFB, 2026, 1, NOW)

    logger.complete()
    rows = [json.loads(l) for l in (log_dir / "pickem.jsonl").read_text().splitlines() if l.strip()]

    assert len({r["run_id"] for r in rows}) == 1
    decided = [r for r in rows if r["event"] == "edge_decided"]
    assert len(decided) == len(snapshot.edges)
    for row, edge in zip(sorted(decided, key=lambda r: r["game_id"]),
                         sorted(snapshot.edges, key=lambda e: e.game_id), strict=True):
        assert row["message"] == edge.rationale
        assert row["side"] == edge.side.value
    logger.remove()
```

- [ ] **Step 2: Run it to verify it fails, then passes**

Run: `uv run pytest tests/test_obs_log.py::test_a_refresh_leaves_a_complete_audit_trail -v`

If it fails on a missing event, the gap is in Tasks 3–9 — fix there, not by weakening the assertion.

- [ ] **Step 3: Exercise it against the real database**

Run:

```bash
uv run pickem preflight --sport cfb --season 2026 --week 1
ls -la ~/LOGS/pickem/
jq -r 'select(.event=="run_started") | "\(.ts)  \(.run_id)  \(.entry)"' ~/LOGS/pickem/pickem.jsonl
```

Expected: three sink files, and at least one `run_started` row for `cli:preflight`.

Confirm no secret leaked:

```bash
grep -c "$(grep ODDS_API_KEY .env | cut -d= -f2)" ~/LOGS/pickem/*.log ~/LOGS/pickem/*.jsonl
```

Expected: `0` for every file.

- [ ] **Step 4: Write the runbook**

Create `docs/runbooks/verifying-from-logs.md` covering: the three sink files and what each is for; the three questions from the spec with the exact `jq` query for each (copy them from `~/.agents/skills/logging-standards/querying.md`); how to find a run's `run_id` and replay it; how to confirm the scheduled refresh fired on a given day; and the DuckDB recipe for book-coverage and duration trends.

- [ ] **Step 5: Restart the service and confirm live output**

```bash
systemctl --user restart pickem-discord-bot
journalctl --user -u pickem-discord-bot -n 20 --no-pager
jq -r 'select(.event=="bot_ready" or .event=="job_scheduled")' ~/LOGS/pickem/pickem.jsonl | tail -5
```

Expected: `bot_ready` and two `job_scheduled` rows, and the gateway chatter now appearing in `pickem.jsonl` under `event="discord_log"`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_obs_log.py docs/runbooks/verifying-from-logs.md
git commit -m "test: verify the end-to-end audit trail and document log verification"
```

---

## Verification Checklist

After Task 10, all of the following must hold:

- [ ] `uv run pytest` passes; `uv run ruff check src tests` is clean.
- [ ] `~/LOGS/pickem/` holds `pickem.log`, `pickem.jsonl`, and `errors.log`.
- [ ] No value of `ODDS_API_KEY`, `CFBD_API_KEY`, or `DISCORD_BOT_TOKEN` appears in any sink.
- [ ] Every record from one run shares a single `run_id`.
- [ ] Every pick in a refresh has exactly one `edge_decided` row carrying its rationale.
- [ ] Every `skipped` one-liner has a matching WARNING row naming the guard that dropped it.
- [ ] A scheduled refresh produces `run_started` with `entry="sched:refresh"`, plus APScheduler's own intercepted job record.
- [ ] `scope_resolved` states which sports and weeks were active — no scope is skipped in silence.
