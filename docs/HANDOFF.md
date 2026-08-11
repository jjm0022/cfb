# Handoff — CFB/NFL Pick'em Edge Engine

**Written:** 2026-08-11
**Last updated:** 2026-08-11, after Task 12 (backtest runner)
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
- **Tests:** 99 passing, `uv run pytest -q`
- **Lint:** clean, `uv run ruff check src tests`
- **Done:** Tasks 1-12 plus amendments 9a and 12a — all reviewed clean
- **Next:** Task 13 (pick sheet report). Not started; no brief generated yet.
- **BASE for Task 13:** current branch HEAD — the `docs: refresh handoff through
  Task 12` commit. Always re-derive it with `git rev-parse HEAD`; do not trust a
  SHA written here, since the docs commit that records it lands after the fact.

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
src/pickem/backtest/stats.py      Result StrEnum, ATS grade_pick, and bounded
                                  Wilson score intervals. Pure; no I/O.
src/pickem/backtest/runner.py     run_backtest replays opener/closer proxies
                                  through compute_edge; BacktestReport includes
                                  tier records, assumptions, and deterministic
                                  skipped-game reasons. Pure; no I/O.
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

Models used: haiku for pure transcription tasks, sonnet for integration tasks
and all reviewers.

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

## Load-bearing conventions — do not "improve" these

- **All spreads are home-perspective.** Home favored by 3 is `-3.0`. Adapters
  normalize at ingest; nothing downstream re-checks. Per-source conventions:
  nflverse is home-favorite-POSITIVE (negate it); CFBD is home-favorite-negative
  (no flip); Odds API home `point` is already correct (no flip). Each is pinned
  by a test.
- **`make_game_id` format:** `nfl-2025-03-BUF-at-MIA` — zero-padded week,
  `-at-` separator, away before home. Every source joins on this string.
- **The `lines` table is append-only.** `INSERT OR IGNORE`, never `OR REPLACE`.
  Line movement is the signal; overwriting destroys it.
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
  this was a human ruling at Task 5 and again at 9a. Both lists only do their
  job if something downstream prints them — see the known gap below.

## Remaining tasks

13. Pick sheet report — 14. CLI wiring — 15. Wire tiebreaks into the pick sheet

After all 15: a final whole-branch review on the most capable model, pointed at
the ledger's deferred-minor and parked lines, then
`superpowers:finishing-a-development-branch`. Tasks 1-12 have deferred minors
waiting there; the ledger is the only record of them.

## Known gaps to raise with the user later

- **Backtest data gap (spec §9).** Historical frozen CBS lines do not exist —
  nobody recorded them. The backtest proxies the frozen line with the market
  OPENING line and submission-time with the CLOSING line. nflverse gives closing
  lines free back to 1999; openers need one month of a paid Odds API tier
  (~$29) to backfill 2020-2025 snapshots, then cancel. `pickem backfill` loads
  closers only and says so.
- **The `skipped` lists have no reader yet.** Both `ParseResult.skipped` and
  `MarketLinesResult.skipped` are populated, but nothing prints them. Task 14's
  plan text now makes `backfill` echo the market-line skip count in yellow;
  the CBS side still has no reader at all. Until the pick sheet (Task 13)
  prints both loudly, a CBS format change would silently drop games from a
  week and the report would still look complete. Handle it at Task 13 at the
  latest.
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
- Live operation should fit The Odds API free tier (500 credits/month).

## Phase B decision (do not skip)

Phase A is deliberately market-only. When `pickem backtest` runs, it reports a
hit rate with Wilson confidence intervals. If divergence alone lands meaningfully
above the noise floor, the Phase B modeling stack (opponent-adjusted EPA, injury,
weather) may be unnecessary. If it lands near 50%, that was learned cheaply.
~1,600 NFL games from 2020 is enough to detect a large effect and not enough to
certify a small one — the intervals are there to keep the report honest.
