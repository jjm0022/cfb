# Handoff — CFB/NFL Pick'em Edge Engine

**Written:** 2026-08-11
**Last updated:** 2026-08-19, after the historical backfill and the first real backtest
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

- **Branch:** all work is on `master`. `phase-a-edge-engine` was merged and
  deleted on 2026-08-11. There is no remote configured.
- **Tests:** 174 passing, `uv run pytest -q` (the suite is fully offline)
- **Lint:** `uv run ruff check src tests` and `uv run ruff format --check src tests`
  are both clean repo-wide.
- **Phase A is code-complete.** Tasks 1-15, amendments 9a/12a/13a/14a, the final
  whole-branch review and its fix wave, the scoped re-review and its fix, an
  end-to-end integration test, and the live CFB alias sweep are all done.
- **The historical backfill is done and the backtest has a real number.** See
  "The phase-exit result" below. What remains is a real CBS paste and the
  Phase B decision it informs.
- **Do not re-run the SDD task loop.** Every task is committed; `git log` is the
  record.

## What is built

```
src/pickem/models.py              Sport/Side/Tier StrEnums, make_game_id, Game,
                                  LeagueLine, MarketLine, MarketLinesResult,
                                  Edge
src/pickem/resolve/resolver.py    TeamResolver, UnknownTeamError (fail-loud).
                                  _normalize folds case, whitespace, diacritics
                                  and punctuation.
src/pickem/resolve/aliases.yaml   canonical team IDs -> every source's spelling.
                                  All 32 NFL teams and all 136 FBS schools.
src/pickem/store/db.py            Store — the only module that talks to DuckDB.
                                  Context manager. upsert_games COALESCEs
                                  scores; insert_games_if_absent for CBS.
src/pickem/store/schema.sql       DuckDB DDL; `lines` is append-only
src/pickem/ingest/cbs.py          parse_cbs_block -> ParseResult (lines,
                                  matchups, skipped); CbsParseError
src/pickem/edge/divergence.py     Thresholds, consensus_spread, compute_edge,
                                  rank_edges. Pure; no I/O.
src/pickem/edge/elo.py            EloConfig, build_ratings, projected_margin,
                                  tiebreak_side. Pure; no I/O.
src/pickem/edge/pipeline.py       apply_tiebreaks resolves only COINFLIP and
                                  NO_MARKET edges with Elo; strong/lean edges
                                  pass through, and an unmatched game raises
                                  MissingGameError. predict_tiebreaker_total
                                  returns the median available market total.
src/pickem/ingest/nflverse.py     load_nfl_games, load_nfl_closing_lines.
                                  `loader` is injected so tests stay offline.
src/pickem/ingest/cfbd_source.py  CfbdConfig, load_cfb_games, load_cfb_lines.
                                  `fetcher` is injected. Talks to the CFBD REST
                                  API over httpx — NOT the `cfbd` SDK, which
                                  pins pydantic<2. Retries 429 with backoff.
                                  Both loaders keep FBS-vs-FBS games only.
src/pickem/ingest/odds.py         OddsClient.fetch_spreads for live book lines;
                                  injectable HTTP transport keeps tests offline.
                                  Requires BOTH a kickoff `window` and a `slate`
                                  of game ids. QuotaExhausted is distinct from
                                  feed errors. Retries transport/5xx with
                                  backoff. Context manager.
src/pickem/backtest/stats.py      Result StrEnum, ATS grade_pick, and bounded
                                  Wilson score intervals. Pure; no I/O.
src/pickem/backtest/runner.py     run_backtest replays opener/closer proxies
                                  through compute_edge AND apply_tiebreaks;
                                  split_proxies classifies stored lines;
                                  BacktestReport carries tier records,
                                  assumptions and skip reasons. Pure; no I/O.
src/pickem/report/sheet.py        render_sheet ranks edges into auditable
                                  markdown with named picks, both spreads,
                                  visible NO_MARKET rows, required provenance,
                                  and numeric or explicitly unknown data age.
src/pickem/config.py              Default DuckDB path and environment-sourced
                                  Odds API / CFBD keys. Loads `.env` on import
                                  with override=False, so an exported variable
                                  always wins over the file.
src/pickem/cli.py                 Typer commands: ingest-cbs, poll-odds
                                  (--days for the kickoff window), report,
                                  sync-results (--sport nfl|cfb), backfill, and
                                  backtest. Prints every source skip visibly;
                                  CBS ingest is atomic.
```

Tests worth knowing about, beyond the per-module ones:

- `tests/test_end_to_end.py` — the real CLI against a real DuckDB file, faking
  only the HTTP transport. Covers paste -> poll -> sheet, out-of-week events,
  re-polls appending history, and re-ingest not destroying synced scores.
- `tests/test_game_id_contract.py` — CBS, nflverse, CFBD and the odds feed must
  all produce the SAME id for the same matchup. A one-character disagreement
  makes the join silently return nothing.
- `tests/test_cfb_aliases.py` — guards the generated FBS table offline.

## How it was built

`superpowers:subagent-driven-development`: per task, a brief to a fresh
implementer subagent, then a scoped reviewer over that task's diff, then a
ledger entry. Closed out with a whole-branch review, one fix wave, and one
scoped re-review. That process is complete — it is recorded here only so the
ledger's structure makes sense.

## Decisions and amendments — do not re-litigate

- **StrEnum, not `str, Enum`.** Human ruling; `str, Enum` trips ruff UP042 and
  contradicts the plan's own lint config. Plan amended at Tasks 2 and 11.
- **`uv_build`, not hatchling.** `uv_build` ships package data under
  `src/pickem/` automatically.
- **YAML 1.1 boolean trap in `aliases.yaml`.** Bare `NO:` (New Orleans) parses
  as boolean `False`, crashing the resolver. Any ID or alias in
  {NO, ON, OFF, YES, Y, N, TRUE, FALSE, NULL} must be quoted; the generated CFB
  block quotes everything unconditionally. A regression test guards this.
- **The `cfbd` SDK is NOT usable here.** Every published `cfbd` 5.x release
  pins `pydantic<2` and this project is built on pydantic 2; the 4.x line
  predates the current API and silently deserializes its camelCase fields to
  None, which made every team name on `/games` come back null while `/lines`
  looked fine. `cfbd_source.py` calls the REST API over httpx instead and the
  dependency was removed. Do not "restore" the SDK.
- **`pytz` is a real dependency.** duckdb imports it dynamically to read
  TIMESTAMPTZ but does not declare it. Do not prune it as unused. Reads return
  pytz UTC tzinfo, not `datetime.UTC`.
- **nflverse team abbreviations vary by era.** The Rams appear as both `LA` and
  `LAR` across seasons (likewise `OAK`/`LV`, `SD`/`LAC`, `STL`). Task 8 swept
  1999-2025 through both loaders until the resolver stopped raising, adding the
  missing spellings as aliases of existing canonical ids — never as new ids.
  Do the same when a new source arrives.
- **CBS lines carrying two numbers are skipped, not resolved.** A paste line
  with a number on both sides (a total, a stray trailing digit) matches both
  regex groups and would otherwise silently resolve to the home-side number.
- **Backtests report every excluded game** (human ruling 12a).
- **Every pick sheet carries provenance and age** (human ruling 13a).
- **CBS ingestion is atomic** (human ruling 14a): on any skipped row the CLI
  prints the offending rows, exits non-zero, and never opens the database.

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
  right — hence the odds `window` and `slate`.
- **`upsert_games` never blind-replaces.** Scores COALESCE, so a source that
  does not carry them cannot erase what `sync-results` wrote. `ingest-cbs` uses
  `insert_games_if_absent`, because the CBS paste has a placeholder kickoff and
  no scores.
- **`edge/` performs no I/O.** Pure functions, which is what makes the strategy
  testable offline and replayable in the backtest.
- **Fail loud, but per source.** An unknown team in the CBS paste RAISES —
  there every line is a game we must pick. An unknown team in the odds feed is
  REPORTED in `skipped` and passed over — that feed is a firehose whose NCAAF
  coverage includes every FCS matchup with a posted line, and aborting a poll
  over a game we never pick would make live CFB unusable. Such an event cannot
  be in the slate anyway, since slate ids are built from names that already
  resolved. Both paths are visible; neither is silent. Missing market lines are
  reported as `NO_MARKET`, never skipped. Never fabricate or interpolate a line.
- **Team-name matching folds diacritics and punctuation.** CFBD writes
  "San José State" and "Hawai'i" where the odds feed writes "San Jose State"
  and "Hawaii". `_normalize` folds these so the alias table does not need a row
  per decoration. It cannot merge two real schools — none differ only by an
  accent. **Do NOT extend this to prefix or fuzzy matching:** "Arkansas Pine
  Bluff", "Houston Baptist", "Indiana State", "North Carolina A&T",
  "Northwestern State", "Tennessee State" and "Utah Tech" are all FCS schools
  whose names begin with an FBS school's name, and a prefix match would silently
  map picks to the wrong team. This was tried during the sweep and rejected.
- **Nothing a source could not give us is dropped in silence.** Two parallel
  result types carry this: `ParseResult.skipped` (CBS paste lines that would not
  parse) and `MarketLinesResult.skipped` (rows with no spread, teams we do not
  track, events outside the window or off the slate). Both are `list[str]` of
  human-readable one-liners. **Any new line loader returns `MarketLinesResult`,
  never a bare list.** The CLI prints both kinds: CBS skips abort ingestion;
  market-row skips are yellow warnings.
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

## The odds poll has two guards, and both are needed

This was the Critical finding of the final review and is the most dangerous
part of the system, because its failure mode is silent and permanent.

The endpoint returns every event with posted odds, spanning several weeks, and
every one was being stamped with the caller's week — writing wrong game ids
permanently into the append-only `lines` table while the real week reported
`NO_MARKET`. `fetch_spreads` now requires:

1. a kickoff **`window`**, checked against `commence_time` **before any team
   name is resolved**. It catches out-of-week events and spares us resolving the
   hundreds of schools we do not track. It is also the ONLY guard that can catch
   a repeat matchup at the same site in another week, because `make_game_id`
   embeds the week being *asserted*, not the week observed.
2. a **`slate`** of canonical game ids, checked after resolution. It catches
   in-window events that are simply not on our sheet.

`poll-odds` derives the slate from `league_lines_for_week`, takes `--days`
(default 7) for the window, and refuses to run before `ingest-cbs`.

## Verified against the live APIs (2026-08-11)

- **CFBD:** swept 2022-2025, weeks 1-15, through `load_cfb_games` and
  `load_cfb_lines`. **3,173 games and 10,197 lines resolved, zero
  `UnknownTeamError`**, 5 rows skipped for null spreads — confirming the CFBD
  null-spread branch is the one that actually fires.
- **The Odds API:** the real NCAAF feed replayed through `fetch_spreads` ->
  `compute_edge` -> `render_sheet` with a season-opener window stored 53 market
  lines across 6 in-window games and reported all 105 skips by reason (103 out
  of window, 2 untracked FCS teams). Live CFB works end to end.
- Both keys live in `.env` at the repo root (gitignored) and are read
  automatically. Free tier is 500 credits/month; 4 were used verifying this.
- Note: polling before the season opens returns nothing, correctly — the
  default window is 7 days. Use `--days` to widen it.

## Remaining work

1. **Paste a real CBS block** through `ingest-cbs`, then `poll-odds`, then
   `report`, and read the sheet for anything that looks wrong. This is the last
   unverified path — the parser has only ever seen fixtures. It is also the
   only way to test the assumption the whole backtest rests on: that CBS's
   frozen number tracks an early-week market snapshot.
2. **DONE (2026-08-19).** NFL 2020-2025 is backfilled from the Odds API
   archive: 22,351 frozen-line and 24,237 submission-time rows over 1,693
   games, 9,390 credits spent of 20,000. **CFB is still open** — it needs
   `cfbd_source` kickoff times checked the way Task 1 checked nflverse's, and
   ~4,500 credits. Decide after reading the NFL result below.
3. **Make the Phase B decision** from the backtest's Wilson intervals.

## Known gaps

- **Backtest data gap (spec §9) — CLOSED for NFL, still open for CFB.**
  Historical frozen CBS lines do not exist, so both ends are proxied from the
  Odds API archive: an early-week snapshot and a per-kickoff pre-game snapshot,
  same feed and same books, so measured divergence is line movement rather than
  a difference between sources. See
  `docs/research/2026-08-19-odds-api-historical.md` for the verified endpoint
  facts and `docs/superpowers/plans/2026-08-19-odds-api-historical-backfill.md`
  for how it was built. The nflverse closers stay as a cross-check, not an
  input: they agree with the archive to 0.141 pts mean absolute difference,
  100% within a point, which is what validates the joins and the sign
  convention on real data.
- **`predict_tiebreaker_total` is unwired, and it is not a loose wire.**
  `odds.py` requests `markets=spreads` only and never populates
  `MarketLine.total`, so wiring it today returns `None` for all live data. Only
  nflverse and CFBD supply totals. It needs the `totals` market added to the
  odds request (a quota cost) plus a CLI flag naming the tiebreaker game.
  Phase B scope.
- **The alias table is FBS-only, deliberately.** Both CFBD fetchers keep only
  games where BOTH sides are FBS — `classification=fbs` alone still returns
  FBS-hosts-FCS games, whose schools this system never picks. An FCS school that
  genuinely appears on the CBS sheet fails loud at `ingest-cbs`, which is the
  right place to notice it and hand-add the alias.
- **Bare "Miami" resolves to MIAFL (the Hurricanes)**, matching CFBD's own
  naming; `Miami (OH)` stays distinct as MIAOH. If a CBS paste ever writes bare
  "Miami" meaning the RedHawks it will silently resolve to the wrong school —
  the one place in the table where that is possible.
- **CFB Elo ratings are untrained until results are synced.** CFB scores arrive
  only through `sync-results --sport cfb --week N`, which needs a CFBD key and
  runs a week at a time. `report` prints a warning, and folds the caveat into
  the sheet's provenance, when no completed game is in the store.
- **nflverse's null-spread branch is defensive only.** A real 1999-2025 sweep
  loaded 7,276 closing lines with ZERO null spreads and no `UnknownTeamError`,
  so only the injected-loader unit test covers it. Kept as a guard. Relevant
  because resolution now precedes the null check: a row with both a null spread
  and an unknown abbreviation raises where it once skipped. The sweep proves
  that combination does not occur in 1999-2025.

## The phase-exit result (2026-08-19)

`pickem backtest --from 2020 --to 2025`, NFL, 1,663 graded games:

```
overall: 866-762-35 (53.2%, 95% CI 50.8%-55.6%)
  strong     174-99-2    (63.7%, CI 57.9%-69.2%)
  lean       244-206-12  (54.2%, CI 49.6%-58.8%)
  coinflip   448-457-21  (49.5%, CI 46.3%-52.8%)
```

**Read the tiers, not the overall number.** The overall rate mixes every game
including the ones with no signal, which is not how the sheet is played.

- **STRONG (>=2.0 pts of divergence) is the finding.** 273 decided picks, 63.7%,
  and the whole interval sits above 50%. Scoring is flat with no vig, so 50% is
  the real bar, not a juice-adjusted breakeven.
- **COINFLIP landing at 49.5% is the control that makes the rest credible.**
  Those are games where divergence said nothing and the Elo tiebreak picked.
  Sitting on 50% is evidence the measurement carries no systematic bias.
- **The ordering is monotonic and was not fitted.** The 2.0/1.0 thresholds were
  guesses made in Phase A before any data existed.
- **Every season agrees.** STRONG by season: 60.7, 62.1, 62.5, 68.2, 65.6,
  65.1. Six for six above 60%, so no single season carries it.

**What this implies for Phase B:** divergence alone clears the noise floor by a
wide margin in its top tier, which is the condition the spec set for Phase B
being unnecessary. The modeling stack (opponent-adjusted EPA, injury, weather)
is not obviously worth building. Prefer widening what is already working —
CFB coverage, and tuning the STRONG threshold now that there is data to tune
it on — over adding a model.

**What the number does NOT establish.** The frozen line is proxied by a Tuesday
market snapshot, not by a real CBS line, because historical CBS numbers were
never recorded. If CBS's number sits systematically off the Tuesday market, the
real edge differs from this one in a direction this backtest cannot see. That
becomes checkable within weeks, once real pastes accumulate next to live
`poll-odds` snapshots — and it is the single most valuable thing left to do.

30 of 1,693 games are reported ungraded: no line was posted at their Tuesday
anchor. They cluster in the 2020 weeks 5-6 and 2021 week 16 COVID
postponements, where the game had no confirmed date to price.
