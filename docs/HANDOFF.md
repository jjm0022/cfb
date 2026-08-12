# Handoff — CFB/NFL Pick'em Edge Engine

**Written:** 2026-08-11
**Last updated:** 2026-08-11, after the final whole-branch review and its fix wave
**Purpose:** resume work after a context reset. Read this first, then the ledger.

## What we're building

A Python backend that helps win a CBS Sports pick'em league (all NFL games +
~5-15 marquee CFB games, picked against the spread).

**The strategy, in one sentence:** CBS freezes its spreads early in the week but
accepts picks until kickoff, so the edge is the gap between that stale frozen
number and the live market line at submission time — not a homebrew prediction
model. Line movement is a stronger ATS signal than anything we could forecast
ourselves.

Scoring is flat (1 point per correct pick) with a tiebreaker, so the goal is
simply to maximize expected correct picks. There is no confidence-point
allocation problem.

## Read these, in order

1. **Spec:** `docs/superpowers/specs/2026-08-11-pickem-edge-design.md`
2. **Plan:** `docs/superpowers/plans/2026-08-11-pickem-edge-engine.md` — 15 TDD
   tasks, each with complete test and implementation code
3. **Ledger:** `.superpowers/sdd/2026-08-11-pickem-edge-engine/progress.md` —
   authoritative record of what is done. Trust it and `git log` over memory.

## Current state

- **Branch:** `phase-a-edge-engine` (NOT master — master has only spec + plan)
- **Tests:** 141 passing, `uv run pytest -q`
- **Lint:** clean, `uv run ruff check src tests`; `ruff format --check` is now
  clean repo-wide too (the six pre-existing drifted files were formatted)
- **Done:** Tasks 1-15 plus amendments 9a, 12a, 13a, 14a, the final
  whole-branch review, and its single fix wave (`821ae5c`)
- **Next:** `superpowers:finishing-a-development-branch`, once the scoped
  re-review of `821ae5c` is clean.
- **Do not start Task 14 or 15 again.** Their committed implementations are
  `e45b724` + atomic-ingest fix `62f4023`, and `63f54b3`, respectively.
- **Review range:** always re-derive it with `git merge-base master HEAD` and
  `git rev-parse HEAD`; handoff updates change HEAD after the fact.

Built so far:

```
src/pickem/models.py              Sport/Side/Tier StrEnums, make_game_id, Game,
                                  LeagueLine, MarketLine, MarketLinesResult,
                                  Edge
src/pickem/resolve/resolver.py    TeamResolver, UnknownTeamError (fail-loud)
src/pickem/resolve/aliases.yaml   canonical team IDs -> every source's spelling
src/pickem/store/db.py            Store — the only module that talks to DuckDB
src/pickem/store/schema.sql       DuckDB DDL; `lines` is append-only
src/pickem/ingest/cbs.py          parse_cbs_block -> ParseResult (lines,
                                  matchups, skipped); CbsParseError
src/pickem/edge/divergence.py     Thresholds, consensus_spread, compute_edge,
                                  rank_edges. Pure; no I/O.
src/pickem/edge/elo.py            EloConfig, build_ratings, projected_margin,
                                  tiebreak_side. Pure; no I/O.
src/pickem/ingest/nflverse.py     load_nfl_games, load_nfl_closing_lines.
                                  `loader` is injected so tests stay offline.
src/pickem/ingest/cfbd_source.py  CfbdConfig, load_cfb_games, load_cfb_lines.
                                  `fetcher` is injected; named `_source` so it
                                  does not shadow the installed `cfbd` package.
                                  Both line loaders return MarketLinesResult.
src/pickem/ingest/odds.py         OddsClient.fetch_spreads for live book lines;
                                  injectable HTTP transport keeps tests offline.
                                  QuotaExhausted is distinct from feed errors;
                                  missing spreads are surfaced in skipped.
                                  Requires a `slate` of game ids; retries
                                  transport/5xx with backoff; context manager.
src/pickem/backtest/stats.py      Result StrEnum, ATS grade_pick, and bounded
                                  Wilson score intervals. Pure; no I/O.
src/pickem/backtest/runner.py     run_backtest replays opener/closer proxies
                                  through compute_edge; BacktestReport includes
                                  tier records, assumptions, and deterministic
                                  skipped-game reasons. Pure; no I/O.
src/pickem/report/sheet.py        render_sheet ranks edges into auditable
                                  markdown with named picks, both spreads,
                                  visible NO_MARKET rows, required provenance,
                                  and numeric or explicitly unknown data age.
src/pickem/edge/pipeline.py       apply_tiebreaks resolves only COINFLIP and
                                  NO_MARKET edges with Elo; strong/lean edges
                                  pass through. predict_tiebreaker_total returns
                                  the median available market total.
src/pickem/config.py              Default DuckDB path and environment-sourced
                                  Odds API / CFBD keys.
src/pickem/cli.py                 Typer commands: ingest-cbs, poll-odds, report,
                                  sync-results, backfill, and backtest. Prints
                                  source skips visibly; CBS ingest is atomic.
```

## Process being followed

`superpowers:subagent-driven-development`. Per task:

1. `scripts/task-brief PLAN N` -> brief file; record BASE (`git rev-parse HEAD`)
2. Dispatch a fresh implementer subagent with the brief path (never the whole plan)
3. `scripts/review-package PLAN BASE HEAD` -> diff file; dispatch a task reviewer
4. On Critical/Important findings: resume the implementer (rounds 1-3), then
   scoped re-review. Max 5 rounds.
5. Append completion to the ledger, then next task

Scripts live at:
`/Users/jmiller/.claude/plugins/cache/claude-plugins-official/superpowers/6.2.0/skills/subagent-driven-development/scripts/`

Model selection follows the skill's cost/capability guidance. The final
whole-branch review must use the most capable available model.

## Decisions and amendments made so far

These are already reflected in the plan document — do not re-litigate them.

- **StrEnum, not `str, Enum`.** Human ruling. The plan originally mandated
  `str, Enum`, which trips ruff UP042 and contradicts the plan's own lint
  config. Plan amended at Tasks 2 and 11.
- **`uv_build`, not hatchling.** Task 3's original step added
  `[tool.hatch.build...]` config, which is inert under this project's build
  backend. Removed; `uv_build` ships package data under `src/pickem/`
  automatically.
- **YAML 1.1 boolean trap in `aliases.yaml`.** Bare `NO:` (New Orleans) parses
  as boolean `False`, crashing the resolver. Any ID or alias in
  {NO, ON, OFF, YES, Y, N, TRUE, FALSE, NULL} must be quoted. A regression test
  now guards this. **This file is hand-edited every time CBS spells a team a
  new way, so this trap will recur.**
- Two pre-flight plan defects fixed before Task 1 (Task 5 fixture test, Task 4
  league-line join).
- **`pytz` is a real dependency.** duckdb imports it dynamically to read
  TIMESTAMPTZ but does not declare it. Do not prune it as unused. Reads return
  pytz UTC tzinfo, not `datetime.UTC`. Plan amended at Task 4.
- **nflverse team abbreviations vary by era.** The Rams appear as both `LA` and
  `LAR` across seasons (likewise `OAK`/`LV`, `SD`/`LAC`, `STL`). Task 8 swept
  1999-2025 through both loaders until the resolver stopped raising, and added
  the missing spellings as aliases of existing canonical ids — never as new ids.
  Do the same when a new source arrives.
- **CBS lines carrying two numbers are skipped, not resolved.** A paste line
  with a number on both sides (a total, a stray trailing digit) matches both
  regex groups; the plan's original code silently took the home-side one as the
  spread. Such lines now go to `ParseResult.skipped`. Plan amended at Task 5.
- **Backtests report every excluded game.** Task 12's original literal code
  silently continued past unplayed games, missing opening proxies, and missing
  closing markets. Human ruling 12a added deterministic
  `BacktestReport.skipped` entries naming every game and reason, and amended the
  Task 12 plan text.
- **Every pick sheet carries provenance and age.** Task 13's original exact
  interface made age optional in the output and had no provenance input,
  conflicting with spec §8. Human ruling 13a made `provenance` a required
  keyword argument and requires every sheet to show either numeric snapshot age
  or `unknown/unavailable`. Tasks 13 and 14 were amended in the plan.
- **CBS ingestion is atomic.** Task 14's literal CLI code persisted valid rows,
  printed `ParseResult.skipped`, and exited zero. Human ruling 14a made the spec
  govern: if any row is skipped, the CLI prints the parse summary and offending
  rows, exits non-zero, and never opens or mutates the database. The plan and a
  mixed valid/ambiguous-row regression test were amended in commit `62f4023`.

## Load-bearing conventions — do not "improve" these

- **All spreads are home-perspective.** Home favored by 3 is `-3.0`. Adapters
  normalize at ingest; nothing downstream re-checks. Per-source conventions:
  nflverse is home-favorite-POSITIVE (negate it); CFBD is home-favorite-negative
  (no flip); Odds API home `point` is already correct (no flip). Each is pinned
  by a test.
- **`make_game_id` format:** `nfl-2025-03-BUF-at-MIA` — zero-padded week,
  `-at-` separator, away before home. Every source joins on this string.
- **The `lines` table is append-only.** `INSERT OR IGNORE`, never `OR REPLACE`.
  Line movement is the signal; overwriting destroys it. Because a bad row can
  never be cleaned up, nothing writes to it without knowing the game id is
  right — hence the odds `slate`.
- **`upsert_games` never blind-replaces.** Scores COALESCE, so a source that
  does not carry them cannot erase what `sync-results` wrote. `ingest-cbs` uses
  `insert_games_if_absent`, because the CBS paste has a placeholder kickoff and
  no scores.
- **`edge/` performs no I/O.** Pure functions, which is what makes the strategy
  testable offline and replayable in the backtest.
- **Fail loud.** Unknown teams raise. Missing market lines are reported as
  `NO_MARKET`, never skipped. Never fabricate or interpolate a line.
- **Nothing a source could not give us is dropped in silence.** Two parallel
  result types carry this, and they are the same idea in two places:
  `ParseResult.skipped` (CBS paste lines that would not parse) and
  `MarketLinesResult.skipped` (per-book rows with no spread). Both are
  `list[str]` of human-readable one-liners naming the game and the book.
  **Any new line loader returns `MarketLinesResult`, never a bare list** —
  this was a human ruling at Task 5 and again at 9a. The CLI now prints both
  kinds: CBS skips abort ingestion; market-row skips are yellow warnings.
- **Every rendered pick sheet names its provenance and age.** Callers must pass
  `provenance`; absent market timing renders as `unknown/unavailable`, never as
  a missing status line.
- **Elo never overrides a real divergence signal.** It may resolve only
  `COINFLIP` and `NO_MARKET`; `STRONG` and `LEAN` edges pass through unchanged.
  An edge that needs a tiebreak but has no `Game` record raises
  `MissingGameError` — it is never passed through carrying `compute_edge`'s
  placeholder `side=HOME`.
- **The backtest runs the pipeline that ships.** It applies the same
  `apply_tiebreaks` the report does, with history that grows only after each
  replayed week. If the two ever diverge again, the backtest stops being
  evidence about the thing being shipped.

## Final review outcome (2026-08-11)

The whole-branch review over `bed73dd..22a842a` returned 1 Critical and 8
Important findings and adjudicated all 17 deferred/parked ledger items. All
were addressed in the single allowed fix wave, commit `821ae5c`. Full detail
is in the ledger; the decisions that changed previously-ruled behavior are:

- **The odds feed is now week-scoped by an explicit slate.** The endpoint
  returns events across several weeks, and every one was being stamped with the
  caller's week — writing wrong game ids permanently into the append-only
  `lines` table while the real week silently reported `NO_MARKET`.
  `fetch_spreads` now requires `slate`, a collection of canonical game ids;
  `poll-odds` derives it from `league_lines_for_week` and refuses to run before
  `ingest-cbs`. **This is the fix that matters most before any live run.**
- **The backtest now grades NO_MARKET games instead of excluding them.** The
  report ships a pick on them via the Elo tiebreak, so excluding them made the
  backtest measure something the system does not do. Ruling 12a still holds:
  every genuinely excluded game is still named in `BacktestReport.skipped`,
  which the CLI now prints.
- **The backtest applies the same tiebreak the report does**, with history that
  grows only after each replayed week, so no future result informs its own pick.
- **`apply_tiebreaks` raises rather than passing through an unresolved edge**
  carrying `compute_edge`'s placeholder `side=HOME`.

## Remaining task

Use `superpowers:finishing-a-development-branch` once the scoped re-review of
`821ae5c` is clean. Do not delete the SDD workspace before then; it contains
the ledger and review artifacts needed for that gate.

## Known gaps to raise with the user later

- **Backtest data gap (spec §9).** Historical frozen CBS lines do not exist —
  nobody recorded them. The backtest proxies the frozen line with the market
  OPENING line and submission-time with the CLOSING line. nflverse gives closing
  lines free back to 1999; openers need one month of a paid Odds API tier
  (~$29) to backfill 2020-2025 snapshots, then cancel. `pickem backfill` loads
  closers only and says so.
- **RESOLVED (Task 14): skipped lists now have CLI readers.** `ingest-cbs`
  prints parser skips and aborts atomically; `poll-odds` and `backfill` print
  every missing-spread row in yellow.
- **Repository-wide Ruff formatting has pre-existing drift.** After Task 13,
  `uv run ruff format --check src tests` named six files outside Task 13 that
  would be reformatted. Task 13's three files are clean; the full list and
  verification note are in the ledger for final-review triage.
- **`aliases.yaml` now covers all 32 NFL teams but still only 14 CFB teams.**
  Task 8 extended the NFL side by sweeping nflverse 1999-2025 until it stopped
  raising `UnknownTeamError`. Task 9 could NOT do the same sweep for CFB —
  CFBD needs an API key, and the suite must stay offline — so the CFB side is
  still only the 14 hand-entered schools. **The first real CFBD call will raise
  `UnknownTeamError` on the ~120 missing FBS schools.** Sweep a real week
  through `load_cfb_games` with a key set and add the spellings as aliases of
  existing canonical ids before relying on live CFB data.
- **RESOLVED (amendment 9a, human ruling):** null-spread rows are no longer
  dropped silently. `load_cfb_lines` and `load_nfl_closing_lines` return
  `MarketLinesResult`, and the plan was amended at Tasks 8, 9, 10 and 14 so
  the unbuilt tasks follow suit. Nothing left to decide here.
- **nflverse's null-spread branch is defensive only.** A real 1999-2025 sweep
  during 9a loaded 7,276 closing lines with ZERO null spreads and no
  `UnknownTeamError`, so that branch never fires on historical data and only
  the injected-loader unit test covers it. CFBD's branch is the one that will
  actually fire, since per-book spreads there genuinely go missing. Relevant
  because 9a moved team resolution ahead of the null check in nflverse: a row
  with both a null spread and an unknown abbreviation now raises where it once
  skipped. The sweep proves that combination does not occur in 1999-2025.
- **RESOLVED (final review): CLI output branches and Store handles.**
  `report` and `backtest` output branches now have CLI-level tests, and both
  `Store` and `OddsClient` are context managers used by all six commands.
- **`predict_tiebreaker_total` is still unwired, and it is not a loose wire.**
  `odds.py` requests `markets=spreads` only and never populates
  `MarketLine.total`, so wiring it today returns `None` for all live data. Only
  nflverse and CFBD supply totals. Wiring it needs the `totals` market added to
  the odds request (a quota cost) plus a CLI flag naming the tiebreaker game.
  Phase B scope.
- **CFB is not usable end to end yet.** The alias gap above plus the fact that
  CFB results only arrive through the new `sync-results --sport cfb --week N`
  path (which needs a CFBD key) means a CFB rating is untrained in practice.
  `report` prints a warning when no completed game is in the store.
- **External phase-exit checks are still unverified.** The automated suite is
  offline. A real current CBS paste, a live-odds report, and a data-backed
  2020-2025 backtest still require credentials/data and should be called out
  when finishing the branch rather than claimed complete from unit tests.
- Live operation should fit The Odds API free tier (500 credits/month).

## Phase B decision (do not skip)

Phase A is deliberately market-only. When `pickem backtest` runs, it reports a
hit rate with Wilson confidence intervals. If divergence alone lands meaningfully
above the noise floor, the Phase B modeling stack (opponent-adjusted EPA, injury,
weather) may be unnecessary. If it lands near 50%, that was learned cheaply.
~1,600 NFL games from 2020 is enough to detect a large effect and not enough to
certify a small one — the intervals are there to keep the report honest.
