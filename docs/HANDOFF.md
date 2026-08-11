# Handoff — CFB/NFL Pick'em Edge Engine

**Written:** 2026-08-11
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
- **Tests:** 36 passing, `uv run pytest -q`
- **Lint:** clean, `uv run ruff check src tests`
- **Done:** Tasks 1-5 — all reviewed clean
- **Next:** Task 6 (divergence engine). Not started; no brief generated yet.
- **BASE for Task 6:** `796be07`

Built so far:

```
src/pickem/models.py              Sport/Side/Tier StrEnums, make_game_id, Game,
                                  LeagueLine, MarketLine, Edge
src/pickem/resolve/resolver.py    TeamResolver, UnknownTeamError (fail-loud)
src/pickem/resolve/aliases.yaml   canonical team IDs -> every source's spelling
src/pickem/store/db.py            Store — the only module that talks to DuckDB
src/pickem/store/schema.sql       DuckDB DDL; `lines` is append-only
src/pickem/ingest/cbs.py          parse_cbs_block -> ParseResult (lines,
                                  matchups, skipped); CbsParseError
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
- **CBS lines carrying two numbers are skipped, not resolved.** A paste line
  with a number on both sides (a total, a stray trailing digit) matches both
  regex groups; the plan's original code silently took the home-side one as the
  spread. Such lines now go to `ParseResult.skipped`. Plan amended at Task 5.

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

## Remaining tasks

6. Divergence engine — 7. Elo tiebreak —
8. nflverse adapter — 9. CFBD adapter — 10. Odds API adapter —
11. Backtest stats — 12. Backtest runner — 13. Pick sheet report —
14. CLI wiring — 15. Wire tiebreaks into the pick sheet

After all 15: a final whole-branch review on the most capable model, pointed at
the ledger's deferred-minor lines, then `superpowers:finishing-a-development-branch`.

## Known gaps to raise with the user later

- **Backtest data gap (spec §9).** Historical frozen CBS lines do not exist —
  nobody recorded them. The backtest proxies the frozen line with the market
  OPENING line and submission-time with the CLOSING line. nflverse gives closing
  lines free back to 1999; openers need one month of a paid Odds API tier
  (~$29) to backfill 2020-2025 snapshots, then cancel. `pickem backfill` loads
  closers only and says so.
- **`aliases.yaml` ships with ~30 teams** and will raise `UnknownTeamError` on
  real data until extended. That is the intended growth path, not a bug.
- Live operation should fit The Odds API free tier (500 credits/month).

## Phase B decision (do not skip)

Phase A is deliberately market-only. When `pickem backtest` runs, it reports a
hit rate with Wilson confidence intervals. If divergence alone lands meaningfully
above the noise floor, the Phase B modeling stack (opponent-adjusted EPA, injury,
weather) may be unnecessary. If it lands near 50%, that was learned cheaply.
~1,600 NFL games from 2020 is enough to detect a large effect and not enough to
certify a small one — the intervals are there to keep the report honest.
