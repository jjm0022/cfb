# Handoff — CFB/NFL Pick'em Edge Engine

**Written:** 2026-08-11
**Last updated:** 2026-08-26, after the CFB COINFLIP NULL handoff verification
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

## Start here

You have a working system with a real result. Nothing is half-finished; the
work is on `feature/cfb-coinflip-model` pending its integration. Orient
yourself in about two minutes:

```bash
uv run pytest -q                              # expect 313 passed
uv run pickem backtest --from 2020 --to 2025  # expect the result below
uv run pickem --help                          # the whole surface, 10 commands
```

Then read "The phase-exit result" at the bottom of this file — it is the
finding everything else now serves — and "Remaining work" for what to do next.

**The strategy is not a prediction model and should not become one by
accident.** Every instinct to "improve the picks" should be checked against
"Load-bearing conventions" first; several obvious improvements are known to be
wrong and are recorded there with their reasons.

## Read these, in order

1. **Spec:** `docs/superpowers/specs/2026-08-11-pickem-edge-design.md`
2. **Plan (Phase A, complete):**
   `docs/superpowers/plans/2026-08-11-pickem-edge-engine.md` — 15 TDD tasks
3. **Plan (backfill, complete):**
   `docs/superpowers/plans/2026-08-19-odds-api-historical-backfill.md` — 6 tasks
4. **Research:** `docs/research/2026-08-19-odds-api-historical.md` — verified
   Odds API archive facts, credit costs, and why both proxies share a source
5. **Research:** `docs/research/2026-08-19-threshold-tuning.md` — why the tier
   thresholds are staying at 2.0/1.0, and why `strong` is not a tuning knob
6. **Ledger:** `.superpowers/sdd/2026-08-11-pickem-edge-engine/progress.md` —
   Phase A only. Trust it and `git log` over memory.

## Current state

### Candidate 1 CFB COINFLIP result (frozen 2026-08-26)

Candidate 1 is a **NULL** result. On its fixed 2022–2025 walk-forward
evaluation, it improved paired accuracy by 1.74 percentage points and was
positive in three seasons, but its Brier score was 0.2502 and therefore missed
the predeclared `< 0.25` acceptance gate. Elo remains the live CFB `COINFLIP`
method; Tasks 10–11 (artifact creation and live integration) are not
authorized. The committed report is
`docs/research/2026-08-25-cfb-coinflip-result.md`; its gitignored prediction
artifact SHA-256 is
`5b2a3cff74d7009b3982a71ecbbe1bd49fbd4d3da5bf9b00f98a7bc7cf6b6d5c`.

Coverage was 2,036 eligible feature rows, 1,577 outer-fold predictions, and
1,549 decided predictions (28 pushes); 1,907 games were explicitly excluded.
The first result and its byte-identical rerun are frozen. No 2026 result may
cause a midseason refit, hyperparameter change, scaling change, or reconsidered
ship/no-ship decision.

#### Task 12 offline handoff verification (2026-08-26)

Candidate 1 remains **NULL**: Elo is still the live CFB `COINFLIP` decider;
there is no accepted model artifact and reporting loads no model code. Do not
begin Candidate 2 without a new design approval. The gitignored prediction
stream has 1,577 rows, zero from season 2026, and SHA-256
`5b2a3cff74d7009b3982a71ecbbe1bd49fbd4d3da5bf9b00f98a7bc7cf6b6d5c`.

Fresh clean-process checks were:

```text
uv run pytest -q                                           313 passed; 254 expected sklearn penalty="l2" warnings
uv run ruff check src tests                                 All checks passed!
uv run ruff format --check src tests                        54 files already formatted
uv run pickem --help                                        10 commands, including backfill-history and evaluate-coinflip
uv run pickem backfill-history --sport cfb --from 2021 --to 2025 --max-snapshot-age-minutes 90 --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb
                                                            1018 planned, 1018 complete, 0 pending = 0 credits
```

The final dry run constructs no client and has no pending archive spend. The
current vendor balance was deliberately **not** queried: even the free quota
endpoint is a network call, and this handoff ran under a no-network
authorization boundary. Thus no current credits-remaining value is claimed.

Stored-data checks against the operational database found zero orphaned CFB
archive lines, zero CFB submission lines captured at or after kickoff, and
1,018 completed archive ledger rows totaling 101,262 lines (with zero returned
after request and zero negative ledger counts). Completed-game coverage by
season was `2021 770/39/21/730`, `2022 776/44/2/732`,
`2023 792/47/6/745`, `2024 797/46/1/751`, and `2025 807/48/0/759`, where each
tuple is completed / missing frozen / missing submission / both proxies.
Submission snapshot age (minimum / median / maximum minutes) was
`2021 0/0/9`, `2022 0/4.35/9`, `2023 3.32/4.32/4.37`,
`2024 3.37/4.37/4.38`, and `2025 3.35/4.37/4.38`. The full invariant SQL and
book-depth distributions are preserved in the Task 12 report. The exact
book-depth distribution for modeled games (completed CFB games with both
proxies), as minimum / median / maximum distinct books per game, was:

| Season | Source | Minimum | Median | Maximum |
|---:|---|---:|---:|---:|
| 2021 | frozen | 7 | 16 | 18 |
| 2021 | submission | 7 | 15 | 18 |
| 2022 | frozen | 9 | 19 | 21 |
| 2022 | submission | 11 | 19 | 21 |
| 2023 | frozen | 9 | 14 | 16 |
| 2023 | submission | 10 | 15 | 16 |
| 2024 | frozen | 6 | 9 | 10 |
| 2024 | submission | 6 | 9 | 10 |
| 2025 | frozen | 5 | 10 | 11 |
| 2025 | submission | 8 | 11 | 11 |

The requested stored 2026 CFB week-1 report was rendered once without polling
or syncing: it had 15 games, 15 sides, provenance, a 9,541-minute market age,
and zero scored games. `report` also records a prospective pick audit batch;
the 2026-08-26 18:56:22 UTC render appended 15 rows, changing `picks` from 30
to 45 while leaving `games` (6,166), `lines` (149,690),
`archive_requests` (1,018), and `league_lines` (15) unchanged. Preserve that
batch; do not delete or revise it. The current operational database SHA-256 is
`2fcfd7765824817cc8a3640136325508f70f37bb830484d5491822a30241e612`.

The sheet now renders every `Edge.rationale` in a Rationale column. For the
stored NULL-path week, the three divergence picks name their market movement
and every COINFLIP rationale names the Elo rating tiebreak; no outcomes were
graded. Do not invoke `sync-results`, a paid archive command, or any 2026
refit without separate authorization.

#### Task 12 authorization completion (2026-08-26)

The explicitly authorized live command completed once:

```bash
uv run pickem poll-odds --sport cfb --season 2026 --week 1 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb
```

It exited `0`, consumed the one authorized live request, and reported `appended
0 market lines`. Eight feed events were visibly skipped: UNC–TCU, SJSU–USC, NCSU–UVA,
NMSU–FSU, HAW–STAN, and MEM–UNLV were outside the stored 2026 week-1 slate;
Jacksonville State–North Dakota State and Sacramento State–Eastern Michigan
contained untracked FCS teams. Post-poll read-only counts remained `games`
6,166, `lines` 149,690 (including the pre-existing 147 week-1 live rows),
`archive_requests` 1,018, `league_lines` 15, and `picks` 45. The database
SHA-256 remains
`2fcfd7765824817cc8a3640136325508f70f37bb830484d5491822a30241e612`.

`report` was intentionally run only against an isolated temporary copy of the
post-poll database and then that copy was removed. The rendered sheet had all
15 sides, CBS-vs-market provenance, a 9,573-minute snapshot age, and 15
unscored games. Its three `STRONG`/`LEAN` rows named divergence, and its 12
`COINFLIP` rows explicitly named `Elo rating projects`; no results were
synced or graded. Task 12 is terminal: Candidate 1 remains NULL, Elo remains
live, no model artifact exists, and Candidate 2 remains blocked on new design
approval.

- **Branch:** the CFB COINFLIP work is on `feature/cfb-coinflip-model`, pending
  integration. There is no remote configured, so `git log` is the only history
  and nothing is pushed anywhere.
- **Tests:** 313 passing, `uv run pytest -q`. The suite is fully offline — HTTP
  is injected via `httpx.MockTransport` and loaders are injected. Keep it that
  way; no test may touch the network.
- **Lint:** `uv run ruff check src tests` and `uv run ruff format --check src tests`
  are both clean repo-wide.
- **Phase A is code-complete** (tasks 1-15, amendments 9a/12a/13a/14a, a final
  whole-branch review, its fix wave, and a scoped re-review).
- **The NFL archive backfill is complete and the backtest has a real number.**
  See "The phase-exit result" at the bottom.
- **Do not re-run either SDD task loop.** Every task in both plans is
  committed; `git log` is the record.

### Data state (2026-08-26)

`data/pickem.duckdb` is gitignored and exists only on this machine. It now
contains both the earlier 9,390-credit NFL archive and the complete 10,180-credit
CFB acquisition described below. The database SHA-256 after the CFB run is
`5d692c92262267501920e4f9597e9cf4d1d7a3e78e5d93a809d2077d3468f31d`.

A recoverable pre-CFB backup exists at
`/Users/jmiller/Dropbox/Personal/Betting/cfb/data/backups/pickem-2026-08-25-pre-cfb.duckdb`.
Its SHA-256 is
`a4bcbd71e64cd5af14ad8c7b7db6a05f731f5c1228d84ad376a3f0635866250a`.

| table/source | rows | note |
|---|---:|---|
| `games` | 6,166 | NFL 2020-2025 (1,693) + CFB 2020-2025 and 2026 week 1 (4,473; 4,457 scored) |
| `archive_requests` | 1,018 | completed CFB 2021-2025 request ledger; all 1,018 planned requests complete |
| `lines` | 149,690 | all stored sources |
| `lines` / `oddsapi:frozen` | 71,300 | NFL plus CFB early-week proxy |
| `lines` / `oddsapi:submit` | 76,550 | NFL plus CFB pre-kickoff proxy |
| `lines` / `nflverse` | 1,693 | closing lines, kept as a cross-check only |
| `lines` / `oddsapi` | 147 | live poll, CFB 2026 week 1 |
| `league_lines` | 15 | CFB 2026 week 1, from the saved CBS page (2026-08-20) |
| `picks` | 45 | three stored CFB 2026 week 1 prospective pick-audit batches; the last was appended by the 2026-08-26 Task 12 report render |

#### CFB archive acquisition

The final dry plan reported exactly `1018 planned, 1018 complete, 0 pending =
0 credits`. The paid operation completed 1,018 historical requests at 10
credits each: **10,180 credits total**. That was the 10-credit probe plus the
remaining seasonal batches of 1,980, 2,020, 1,990, 2,060, and 2,120 credits.
The request ledger's `line_count` total is 101,262.

| season | frozen requests | frozen rows | submit requests | submit rows | total requests | total rows |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 15 | 10,900 | 184 | 11,047 | 199 | 21,947 |
| 2022 | 15 | 13,405 | 187 | 14,324 | 202 | 27,729 |
| 2023 | 15 | 10,366 | 184 | 11,275 | 199 | 21,641 |
| 2024 | 15 | 6,655 | 191 | 7,171 | 206 | 13,826 |
| 2025 | 15 | 7,623 | 197 | 8,496 | 212 | 16,119 |
| **Total** | **75** | **48,949** | **943** | **52,313** | **1,018** | **101,262** |

Stored proxy coverage is nonzero for both sources in every season:

| season | source | rows | games | distinct books |
|---:|---|---:|---:|---:|
| 2021 | `oddsapi:frozen` | 10,900 | 731 | 18 |
| 2021 | `oddsapi:submit` | 11,047 | 749 | 18 |
| 2022 | `oddsapi:frozen` | 13,405 | 732 | 22 |
| 2022 | `oddsapi:submit` | 14,324 | 774 | 22 |
| 2023 | `oddsapi:frozen` | 10,366 | 745 | 16 |
| 2023 | `oddsapi:submit` | 11,275 | 786 | 16 |
| 2024 | `oddsapi:frozen` | 6,655 | 751 | 10 |
| 2024 | `oddsapi:submit` | 7,171 | 796 | 10 |
| 2025 | `oddsapi:frozen` | 7,623 | 759 | 11 |
| 2025 | `oddsapi:submit` | 8,496 | 807 | 11 |

Completed-game coverage gaps are explicit rather than silently discarded:

| season | completed games | missing frozen | missing submit | with both proxies |
|---:|---:|---:|---:|---:|
| 2021 | 770 | 39 | 21 | 730 |
| 2022 | 776 | 44 | 2 | 732 |
| 2023 | 792 | 47 | 6 | 745 |
| 2024 | 797 | 46 | 1 | 751 |
| 2025 | 807 | 48 | 0 | 759 |

Book depth and archive timestamp age are recorded as minimum / average /
maximum. Age is `requested_at - returned_at` in minutes:

| season | source | books/game min/avg/max | age minutes min/avg/max |
|---:|---|---:|---:|
| 2021 | frozen | 7 / 14.91 / 18 | 5 / 5 / 5 |
| 2021 | submit | 7 / 14.75 / 18 | 0 / 0.32 / 9 |
| 2022 | frozen | 9 / 18.31 / 21 | 4.33 / 4.48 / 5 |
| 2022 | submit | 11 / 18.51 / 21 | 0 / 3.58 / 9 |
| 2023 | frozen | 9 / 13.91 / 16 | 4.28 / 4.31 / 4.35 |
| 2023 | submit | 10 / 14.34 / 16 | 3.32 / 4.31 / 4.37 |
| 2024 | frozen | 6 / 8.86 / 10 | 4.33 / 4.36 / 4.37 |
| 2024 | submit | 6 / 9.01 / 10 | 3.37 / 4.36 / 4.38 |
| 2025 | frozen | 5 / 10.04 / 11 | 4.33 / 4.36 / 4.38 |
| 2025 | submit | 8 / 10.53 / 11 | 3.35 / 4.35 / 4.38 |

Final integrity checks returned zero for all four conditions: lines orphaned
from `games`, CFB `oddsapi:submit` rows captured at or after kickoff, archive
responses with `returned_at > requested_at`, and negative ledger `line_count`.
The coverage query must use `AS row_count`; bare `rows` is reserved by the
installed DuckDB parser.

**Odds API:** historical archive requests are accounted at 10 credits each;
live ones cost 1. `/v4/sports` is free and reports the current vendor balance
in `x-requests-remaining`. Query that endpoint before any future paid run rather
than relying on the pre-acquisition balance recorded in older notes.

## What is built

```
src/pickem/models.py              Sport/Side/Tier StrEnums, make_game_id, Game,
                                  LeagueLine, MarketLine, MarketLinesResult,
                                  Edge. Also owns the market `source` labels
                                  (LIVE/FROZEN/SUBMISSION) so ingest and
                                  backtest share a vocabulary without either
                                  importing the other.
src/pickem/resolve/resolver.py    TeamResolver, UnknownTeamError (fail-loud).
                                  _normalize folds case, whitespace, diacritics
                                  and punctuation.
src/pickem/resolve/aliases.yaml   canonical team IDs -> every source's spelling.
                                  All 32 NFL teams and all 136 FBS schools.
src/pickem/store/db.py            Safe writes plus coherent week/range reads.
src/pickem/store/schema.sql       DuckDB DDL; `lines` is append-only
src/pickem/ingest/cbs.py          Complete CBS game + league-line intake records.
src/pickem/ingest/cbs_html.py     parse_cbs_html — the saved CBS page. Reads the
                                  Apollo SSR blob the page server-renders, not
                                  the DOM. Same ParseResult, plus real kickoff
                                  instants. Deduplicates: CBS emits every game
                                  in two blobs.
src/pickem/edge/divergence.py     Internal divergence measurement plus
                                  consensus and ranking helpers. Pure; no I/O.
src/pickem/edge/elo.py            EloConfig, build_ratings, projected_margin,
                                  tiebreak_side. Pure; no I/O.
src/pickem/edge/pipeline.py       `decide_edges`, the only public edge-decision
                                  interface; it returns final picks and resolves
                                  only COINFLIP and NO_MARKET internally.
src/pickem/ingest/nflverse.py     load_nfl_games, load_nfl_closing_lines.
                                  `loader` is injected so tests stay offline.
                                  _kickoff combines gameday + gametime through
                                  US/Eastern for the true UTC instant.
src/pickem/ingest/cfbd_source.py  CfbdConfig, load_cfb_games, load_cfb_lines.
                                  `fetcher` is injected. Talks to the CFBD REST
                                  API over httpx — NOT the `cfbd` SDK, which
                                  pins pydantic<2. Retries 429 with backoff.
                                  Both loaders keep FBS-vs-FBS games only.
src/pickem/ingest/odds.py         OddsClient.fetch_spreads (live) and
                                  fetch_historical_spreads (archive, 10 credits
                                  a call). Both share _parse_events, so the two
                                  paths cannot drift. Both require a kickoff
                                  `window` and a `slate` of game ids.
                                  QuotaExhausted is distinct from feed errors.
                                  Retries transport/5xx. Context manager.
src/pickem/backtest/stats.py      Result StrEnum, ATS grade_pick, and bounded
                                  Wilson score intervals. Pure; no I/O.
src/pickem/backtest/snapshots.py  Pure planner: a stored schedule becomes the
                                  exact archive requests to make, plus their
                                  credit cost, before anything is spent.
src/pickem/backtest/archive.py    Paid archive planning/execution policy.
src/pickem/backtest/calibration.py
                                  calibrate — the check on the substitution the
                                  whole backtest rests on. Residual is the CBS
                                  line minus the market consensus at or before
                                  its posted_at; bias and dispersion are
                                  reported separately. Pure; no I/O.
src/pickem/backtest/runner.py     run_backtest replays the frozen and submission
                                  proxies through decide_edges; both ends
                                  collapse through consensus_spread.
                                  split_proxies classifies
                                  stored lines by `source`. BacktestReport
                                  carries tier records, assumptions and skip
                                  reasons. Pure; no I/O.
src/pickem/report/sheet.py        render_sheet consumes final picks from
                                  decide_edges and ranks them into auditable
                                  markdown with named picks, both spreads,
                                  visible NO_MARKET rows, required provenance,
                                  and numeric or explicitly unknown data age.
src/pickem/config.py              Default DuckDB path and environment-sourced
                                  Odds API / CFBD keys. Loads `.env` on import
                                  with override=False, so an exported variable
                                  always wins over the file.
src/pickem/resolve/matchup.py     Canonical matchup identity shared by all adapters.
src/pickem/cli.py                 Typer commands: ingest-cbs, poll-odds
                                  (--days for the kickoff window), report,
                                  sync-results (--sport nfl|cfb), backfill,
                                  backfill-history (dry run by default,
                                  --execute to spend, --max-credits ceiling),
                                  backtest, backfill-cfb, and calibrate. Prints
                                  every source skip
                                  visibly; CBS ingest is atomic.
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
- **Both backtest proxies come from the Odds API archive, never one from
  nflverse** (2026-08-19). Mixing sources across the two ends puts differing
  book composition inside the measured divergence, and divergence is the entire
  strategy. The nflverse closers stay as an independent cross-check.
- **The frozen proxy is a fixed Tuesday 14:00 UTC anchor, not a market
  "opening line."** CBS freezes early in the week, which is what is being
  imitated; a true opener is whenever each book first posted, a different and
  less relevant moment. The anchor is fixed in UTC rather than tracked against
  Eastern so re-runs are byte-identical — `captured_at` is part of the `lines`
  primary key.
- **`captured_at` for an archived row is the snapshot's own timestamp, never
  the requested one.** The archive returns the closest snapshot at or earlier
  than `date`, so stamping the request misdates rows by up to ten minutes and
  makes a re-run append near-duplicates instead of being a no-op.
- **The tier thresholds stay at 2.0/1.0** (2026-08-19). Tuning `lean` gains +9
  correct picks in-sample and +1 out-of-sample against ~15 picks of paired
  noise, and +8 of the in-sample +9 come from 2020 alone. Every walk-forward
  fold picks `lean = 0.5`, but stability of a zero-magnitude effect is not
  evidence. Do not re-run this sweep unless the tiebreak improves — see
  `docs/research/2026-08-19-threshold-tuning.md`.
- **The CBS page is read as data, not scraped.** The pick'em page
  server-renders its GraphQL result into an Apollo SSR blob, so `--html` parses
  that rather than the DOM. Its CSS classes are hashed per build
  (`mui-1m0pb6d`) and change on every CBS deploy; the JSON field names are what
  their own client consumes. The blob contains the JS literal `undefined`,
  which is not valid JSON and is rewritten outside string literals only. Every
  game appears in two blobs and is deduplicated on CBS's event id.
- **CBS's `homeTeamSpread` is already home-perspective favourite-negative**, so
  it passes through unflipped. Pinned by the one away-favourite in the slate —
  the only game in a week where a sign error is visible at all.
- **CBS writes `Boise St.` where CFBD writes `Boise State`** (2026-08-20). Added
  as aliases of the existing ids for all 30 State schools, not just the three
  that failed. Same convention as era-varying NFL abbreviations.
- **Franchise renames are aliases, exactly like era-varying abbreviations.**
  "Washington Football Team" (2020-2021) was missing and silently dropped that
  team's games, because the odds feed reports unknown teams rather than
  raising. Whenever the archive range spans a rename, the old names are live
  data.

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
- **Source adapters own source-specific intake behavior.** Canonical matchup
  identity is shared, while each adapter retains its own skip/fail policy and
  spread normalization convention.
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
- **`Thresholds.strong` is a display parameter, not a tuning knob.** Internal
  divergence measurement sets `side` from the sign of `delta` alone;
  `decide_edges` resolves only COINFLIP and NO_MARKET — so a STRONG and a LEAN
  edge produce the SAME pick. Moving the STRONG/LEAN boundary
  relabels games; it never changes one, and no backtest can tune it toward
  correct picks. Only `lean` moves games between the two deciders (divergence
  vs the Elo tiebreak). Verified 2026-08-19 across a 56-cell sweep: every cell
  sharing a `lean` value had a byte-identical win/loss/push record.
- **`calibrate`'s cutoff is a tolerance, not a strict at-or-before.**
  `poll-odds` derives its slate from `league_lines`, so the market snapshot is
  always captured AFTER the paste it is compared against — by construction,
  never before. The first live run missed a strict cutoff by 11 seconds. The
  window admits the same-sitting poll; widening it far enough to admit the next
  day's number would readmit exactly the line movement the strategy trades on.
- **Elo never overrides a real divergence signal.** It may resolve only
  `COINFLIP` and `NO_MARKET`; `STRONG` and `LEAN` edges pass through unchanged.
  An edge that needs a tiebreak but has no `Game` record raises
  `MissingGameError` — it is never passed through carrying internal
  measurement's placeholder `side=HOME`.
- **The backtest and live report run the pipeline that ships.** Both consume
  `decide_edges`, with backtest history growing only after each replayed week.
  If the two ever diverge again, the backtest stops being
  evidence about the thing being shipped.
- **Both ends of the divergence collapse through `consensus_spread`.** A
  per-game dict is last-wins and would grade against whichever book sorted
  last while the other end took a median, manufacturing and erasing edges
  silently. A regression test pins this: books at -1.0/-3.0/-9.0 against a
  -3.0 submission line is a COINFLIP, not a 6-point STRONG edge.
- **Proxies are classified by `source`, never by book name.** Both carry real
  bookmaker keys. `oddsapi:frozen` and `oddsapi:submit` are the two proxies;
  anything else — an in-season `oddsapi` poll, an `nflverse` closer — lands in
  `split_proxies`' explicit catch-all and is reported as ungraded. That
  catch-all is what stops live polling from contaminating a replay, so never
  give it a default branch.
- **The submission snapshot leads kickoff by 15 minutes, and 5 is not enough.**
  The Odds API's `commence_time` and nflverse's kickoff disagree by -5 to +2
  minutes (measured, 2026-08-19). At a 5-minute lead the request lands on the
  real kickoff for the worst case, and the archive's at-or-earlier rule can
  then return in-play odds into a proxy that must predate the game. Verified
  after the fact: every stored `oddsapi:submit` row sits exactly 20 minutes
  before its kickoff, and none at or after.
- **"Nothing to store" is not an error.** DuckDB's `executemany` rejects an
  empty parameter list, so an unguarded writer turns a fully-skipped snapshot
  into a crash mid-backfill, after the credits for it are spent.

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

`poll-odds` derives the slate from `load_week(...).league_lines`, takes `--days`
(default 7) for the window, and refuses to run before `ingest-cbs`.

## Verified against the live APIs (2026-08-11)

- **CFBD:** swept 2022-2025, weeks 1-15, through `load_cfb_games` and
  `load_cfb_lines`. **3,173 games and 10,197 lines resolved, zero
  `UnknownTeamError`**, 5 rows skipped for null spreads — confirming the CFBD
  null-spread branch is the one that actually fires.
- **The Odds API:** the real NCAAF feed replayed through `fetch_spreads` ->
  `decide_edges` -> `render_sheet` with a season-opener window stored 53 market
  lines across 6 in-window games and reported all 105 skips by reason (103 out
  of window, 2 untracked FCS teams). Live CFB works end to end.
- Both keys live in `.env` at the repo root (gitignored) and are read
  automatically. This was on the free tier (500/month); 4 credits were used.
- Note: polling before the season opens returns nothing, correctly — the
  default window is 7 days. Use `--days` to widen it.

## Verified against the live archive (2026-08-19)

- **926 snapshots fetched, 1,693 NFL games covered, zero orphaned rows.** Every
  game 2020-2025 has both proxies except 30 with no frozen snapshot (below).
- **The archive agrees with nflverse.** Comparing the `oddsapi:submit` consensus
  against nflverse closers on 2024: 71% exact, 97% within 0.5 pts, 100% within
  1.0 pt, mean absolute difference **0.141**. This is what validates the
  cross-source joins and the sign convention on real data — a flipped sign
  would show a mean difference near 9 points, not 0.14.
- **`tests/fixtures/odds_historical_nfl.json`** is a real captured archive
  response (2024-09-22T16:55Z, 31 events, 10 books, 10 credits). Two tests
  replay it offline. Prefer extending it over hand-writing new fixtures: the
  bugs that cost the most here were all in assumptions a hand-built fixture
  would have encoded rather than caught.

## Remaining work

Task 12 is complete. Candidate 2 remains **blocked** on a new design approval.
Do not retune Candidate 1, refit on 2026, alter thresholds, or treat the NULL
result as permission for a feature search. The CFB 2021–2025 archive is
complete, so there is no CFB historical backfill remaining to buy.

Season timing, for context: the CFB season opens in late August 2026 and NFL
week 1 is early September 2026. In-season polling is cheap (1 credit per call),
so the free tier would cover weekly use if the subscription is cancelled.

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
- **30 of 1,693 games have no frozen snapshot and are reported ungraded.**
  No line was posted at their Tuesday anchor. They cluster in the 2020 weeks
  5-6 and 2021 week 16 COVID postponements, where the game had no confirmed
  date to price. This is correct behaviour, not a gap to close: no early-week
  market existed, which is the same situation CBS would have faced.
- **The CFB 2021–2025 archive is complete;** it now contains both frozen and
  submission proxies for the explicit coverage population recorded above. The
  phase-exit backtest result remains NFL-only, but the separate CFB COINFLIP
  evaluation is frozen as a NULL result.
- **The CBS paste gap is CLOSED for CFB 2026 week 1, still open for NFL.**
  The saved page ingested, polled, rendered and calibrated on 2026-08-20 — see
  "The calibration result" below. The NFL weekly path still has never run
  against a real CBS sheet, and the archive proxy the phase-exit result rests
  on is NFL. The CFB agreement is strong evidence for the method but is not
  the same slate.
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
- **CFB Elo is now trained (4,457 results, 2020-2025) and still takes the
  underdog on most big spreads.** This is not a bug and refitting will not fix
  it — see "Why the tiebreak cannot rescue coinflips" below. `report` no longer
  warns, because there is real history; the picks are informed but weak.
- **nflverse's null-spread branch is defensive only.** A real 1999-2025 sweep
  loaded 7,276 closing lines with ZERO null spreads and no `UnknownTeamError`,
  so only the injected-loader unit test covers it. Kept as a guard. Relevant
  because resolution now precedes the null check: a row with both a null spread
  and an unknown abbreviation raises where it once skipped. The sweep proves
  that combination does not occur in 1999-2025.

## Why the tiebreak cannot rescue coinflips (2026-08-20)

Measured after the CFB backfill, training on 2020-2024 and holding out 2025
(807 games):

```
straight-up winner picked:  64.8%      <- the rating knows who is better
mean |projected margin|:     9.35
mean |actual margin|:       16.40      <- projections are compressed
mean signed error:          -2.23
```

The compression looks like a mis-scaled constant, and it is not. A
least-squares fit of actual margin on rating difference over the training
seasons returns `points_per_elo = 0.0341` against the current default of
`0.0400` — i.e. the honest fit is *more* compressed, not less, and on held-out
2025 it moves mean absolute error only 14.45 -> 14.25. **Do not "fix" the
compression by rescaling.** A conditional mean from a weak predictor shrinks
toward zero; that is correct behaviour, not a calibration error.

The consequence is structural. `tiebreak_side` compares a shrunken projection
against a sharp market spread, so on any sizeable spread the projection sits
inside the number and the rule resolves to "take the underdog" — 11 of 15
coinflips on the 2026 CFB week 1 sheet. Since ATS underdogs are close to a coin
flip, this reproduces exactly the 49.5% COINFLIP rate the NFL backtest measured.

**So the coinflip problem is not a tuning problem and cannot be closed by a
better-fitted Elo.** Beating it needs a predictor strong enough that its
conditional mean can cross a sharp line — which is the Phase B modelling
question, not a parameter change.

## The calibration result (2026-08-20)

**The assumption the backtest rests on survived its first real test.** CFB 2026
week 1: the saved CBS page ingested, polled against the live odds feed 11
seconds later, and calibrated on all 15 games.

```
bias (mean residual):         -0.07 pts
dispersion (mean |residual|):  0.33 pts
agreement: 73.3% exact, 80.0% within 0.5, 93.3% within 1.0
```

A bias of -0.07 is as close to unbiased as 15 games can show. For comparison,
the archive-vs-nflverse cross-check that validated the joins came in at 0.141
mean ABSOLUTE difference; this is 0.33 absolute, wider but on a fifteenth of
the sample and on a sport with thinner books.

**What it does NOT establish.** (1) It is CFB, and the phase-exit result is
NFL; the two slates have different book coverage. (2) It is one week, n=15 —
one more UCLA-sized disagreement would move the bias by 0.17. (3) It compares
CBS against the market *at paste time*, which is the right comparison for the
proxy but says nothing about whether CBS's number was frozen days earlier or
minutes earlier. (4) Most of the agreement is CBS simply copying the market:
11 of 15 matched exactly, so the sample of informative disagreements is 4.

The one real disagreement was UCLA at California — CBS -1.5, market +1.0, a
2.5-point gap and the only STRONG edge on the sheet. That is the strategy
firing exactly as designed, on the first real week it ever saw.

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

### Tier volume is the other half of the result

The hit rates alone overstate what this buys, because the league makes you pick
**every** game. What matters is how much of the sheet each tier covers:

| tier | share of sheet | rate | extra correct picks/season |
|---|---|---|---|
| strong | 16.8% (273 decided) | 63.7% | **+6.2** |
| lean | 27.6% (450) | 54.2% | +3.2 |
| coinflip | **55.6% (905)** | 49.5% | -0.8 |
| | | | **+8.7 total** |

STRONG fires on about **2.1 games per week** out of ~12.4 decided. So the
realized season is the overall 53.2%, worth roughly **+8.7 correct picks over a
coin flip per season**. Against the ~8.2-pick standard deviation of a 272-game
season that is about one sigma: a real edge that wins more often than not, not
a dominant one.

**What this implies for Phase B — read this carefully, an earlier version of
this document got it wrong.** The tempting reading is "divergence clears the
noise floor, so the modeling stack is unnecessary." That is too quick.
Divergence works where it fires but is **silent on 55.6% of the sheet**, and
those coinflip games sit at 49.5% — pure noise. That silent majority is now the
largest untapped pool: moving coinflips from 49.5% to even 52.5% would add
roughly what the entire STRONG tier contributes today.

So the honest framing is that Phase A succeeded at what it measured and left
the bigger half of the problem untouched. Modeling (opponent-adjusted EPA,
injury, weather) is best justified **as a replacement for the Elo tiebreak on
coinflip games**, not as a competitor to divergence — which it should never
override (see "Load-bearing conventions").

Threshold tuning was the cheaper thing to try first, and it has now been tried
and returned nothing (2026-08-19): the boundary between the tiers cannot be
moved to any profit, because `strong` changes no pick at all and `lean` only
trades games between divergence and a tiebreak that is itself a coin flip.
That closes the cheap option and leaves the tiebreak as the only lever with
headroom.

**What the number does NOT establish.** The frozen line is proxied by a Tuesday
market snapshot, not by a real CBS line, because historical CBS numbers were
never recorded. If CBS's number sits systematically off the Tuesday market, the
real edge differs from this one in a direction this backtest cannot see. That
becomes checkable within weeks, once real pastes accumulate next to live
`poll-odds` snapshots — and it is the single most valuable thing left to do.

## Bugs the real data found, and what they teach

All four were found by running against real APIs and real stored data, not by
tests. Three of them could not have been caught by fixtures, because the
fixtures encoded the same wrong assumption as the code.

1. **`_kickoff` parsed `gameday` only**, so every NFL kickoff was midnight UTC.
   Phase A deferred this as "operationally inert" and it genuinely was, until a
   feature was built that depended on kickoff times. *A deferral is only valid
   against the code that exists when it is made.*
2. **`run_backtest` kept one arbitrary opener per game** (a last-wins dict
   comprehension). Correct with one nflverse line per game; silently wrong the
   moment a multi-book snapshot arrived. *Found by reading the code while
   planning, not by any test — the plan document paid for itself here.*
3. **The 5-minute submission lead** collided with the sources' kickoff
   disagreement. *Found by buying one snapshot for 10 credits and diffing it
   against the schedule, before spending 9,000 more.*
4. **The missing "Washington Football Team" alias** silently dropped that
   team's games, and only surfaced because an unrelated empty-writer crash
   halted the run. *The loud crash was lucky; the silent alias gap was the real
   bug. Prefer checks that fail loudly over ones that produce a
   complete-looking result.*

The general lesson, worth keeping: **spend a small amount of real money early to
check assumptions, before spending a large amount.** Task 6 of the backfill plan
was ordered cheapest-verification-first for this reason, and it is why the total
damage from all four bugs was ~110 wasted credits.
