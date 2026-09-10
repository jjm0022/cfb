# Deployment history — how did we get here?

Dated records of completed deployments and their verification. These are
historical: they describe what was true when written. For current behaviour
see `HANDOFF.md`.

Split out of `HANDOFF.md` on 2026-09-09; content unchanged.

## Candidate 1 CFB COINFLIP result (frozen 2026-08-26)

Candidate 1 is a **NULL** result. On its fixed 2022–2025 walk-forward
evaluation, it improved paired accuracy by 1.74 percentage points and was
positive in three seasons, but its Brier score was 0.2502 and therefore missed
the predeclared `< 0.25` acceptance gate. Elo remained the live CFB `COINFLIP`
method at this freeze (superseded 2026-09-09 by the frozen-board favorite);
Tasks 10–11 (artifact creation and live integration) are not
authorized. The committed report is
`docs/research/2026-08-25-cfb-coinflip-result.md`; its gitignored prediction
artifact SHA-256 is
`5b2a3cff74d7009b3982a71ecbbe1bd49fbd4d3da5bf9b00f98a7bc7cf6b6d5c`.

Coverage was 2,036 eligible feature rows, 1,577 outer-fold predictions, and
1,549 decided predictions (28 pushes); 1,907 games were explicitly excluded.
The first result and its byte-identical rerun are frozen. No 2026 result may
cause a midseason refit, hyperparameter change, scaling change, or reconsidered
ship/no-ship decision.

## Candidate 2 CFB COINFLIP residual result (corrected freeze, 2026-08-27)

The Task 6 output committed in `6a01f77` is invalid and superseded: its outer
cutoffs were derived after eligibility filtering and exact-zero inner scores did
not delegate to Elo. Its retained, nonbinding prediction/report SHA-256 values
are `9ea82a2157c15b284c505613ec3ec3f0be9884fea17b17a5f296b66556ede29b` and
`de65aa2be13bcf92d152f0881e0da0d81df2bc5e1bbf9b9583b2de65adc401fb`.

Candidate 2 is NULL. No Candidate 2 model artifact exists. The team-residual family is closed; Tasks 7–9 were not started. Elo remained the COINFLIP decider at this freeze; it was superseded on 2026-09-09 by the frozen-board favorite, on the favorite-versus-Elo diagnostic inside this very report.

## Weekly-win strategy research (2026-08-28)

The Heavy research deployment reframed the improvement target as winning at
least one weekly pool, not merely improving independent ATS accuracy. It
recommends a no-cost, COINFLIP-only, opponent-aware prospective paper test:
retain the incumbent beside a candidate that combines timestamp-safe market
cover estimates with expected opponent duplication, using Action Network `% of
bets` only as an explicitly imperfect public proxy and locked CBS picks when
available. No live routing changed and no 2026 outcomes were fit.

Do not purchase data yet. If the paper test supports a paid experiment, the
best candidate is retaining book-level Odds API price/juice, update time, and
book identity at the existing submission snapshots. The pool currently has 51
entrants with at least a few more expected, approximately $200 weekly prizes,
and the full 18-week NFL regular season starts next week and ends when the
playoffs begin. Its tiebreak is the Monday Night Football total. The research
report contains the predeclared gate and break-even formulas; final entrant
count, exact payout/tie rules, and scoring details are still required before
estimating whether one weekly win can repay a subscription. Opponent picks
unlock only when each particular game starts, so they are useful for later-week
calibration but unavailable for same-week adaptation.

## Week 1 operational readiness (completed 2026-08-28)

The Heavy deployment `week1_preflight_20260828` implemented the operational
guard and was integrated into `master` at `6ed6ed7` by fast-forward from
`feature/week1-preflight`. It adds a read-only `preflight` command, a
read-only DuckDB Store connection, pure readiness evaluation, dedicated tests,
and the operator runbook `docs/runbooks/week1-operations.md`. No live database
state, market poll, result sync, model routing, or pick decision changed.

Before a final report, `preflight` requires an explicit sport and expected
slate size; exact game and league-line counts; future kickoffs; per-game live
Odds API coverage; fresh latest snapshots; minimum distinct-book depth at each
latest snapshot; and completed same-sport history for the Elo tiebreak. It
prints a per-game table and returns nonzero on a failed hard gate. `report`
remains the only command that records a pick batch.

Next, review the integrated implementation and run its preflight tests and the
full suite. Then rehearse `ingest-cbs`, `poll-odds`, `preflight`, `report`, and
post-game `sync-results` against an isolated database copy. Keep opponent picks
out of same-week decisions and keep tiebreak capture requirements explicit
before any live mutation.

The corrected fixed 2022–2025 walk-forward replay produced 1,577 paired
predictions and 1,549 decided outcomes (28 pushes), with zero 2026 rows.
Candidate accuracy was 51.45% (797–752) versus 51.00% (790–759) for the
frozen-line favorite: a 0.45 percentage-point lift (+7 paired correct picks).
It had two positive seasons, a paired season/week bootstrap 95% interval of
[-2.61%, 3.32%], Brier score 0.2505, and log loss 0.6942. It therefore failed
all four frozen performance gates despite passing calibration, leakage,
determinism, and no-2026 checks. Two private corrected runs, followed by the
final write, were byte-identical. The current gitignored prediction stream
SHA-256 is `dc125e4672e7e2e6a144ff6b803636452601107e98e2a9625b0a0364f4e54f79`;
the corrected committed report is
`docs/research/2026-08-27-cfb-coinflip-residual-result.md` (SHA-256
`992202cf6c3eff150dc7860230c154036ad7f93cf0a87df495ed07f8bb024da9`).

### Task 12 offline handoff verification (2026-08-26)

Candidate 1 remains **NULL**: Elo was still the live CFB `COINFLIP` decider as
of this verification (superseded 2026-09-09 by the frozen-board favorite);
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
graded. (That stored sheet predates the 2026-09-09 tiebreak change; a COINFLIP
rationale rendered today reads "no divergence, so we take the frozen-board
favorite".) Do not invoke `sync-results`, a paid archive command, or any 2026
refit without separate authorization.

### Task 12 authorization completion (2026-08-26)

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
synced or graded. Task 12 is terminal: Candidate 1 remains NULL, Elo was live
at that time, no model artifact exists, and Candidate 2 remains blocked on new
design approval. Elo was replaced for `COINFLIP` on 2026-09-09; that change
came from a baseline comparison, not from a Candidate 1 or 2 artifact, so it
does not reopen either candidate.

- **Branch:** the CFB COINFLIP work is integrated in `master` at `1620de3` by
  fast-forward from `feature/cfb-coinflip-model`. There is no remote configured,
  so `git log` is the only history and nothing is pushed anywhere.
  (Correction, 2026-09-10: a remote `origin`
  `git@github.com:jjm0022/cfb.git` does now exist, but its `master` is at
  `a4bb432`, 28 commits behind local. Nothing recent has been pushed, so the
  practical effect described above still holds.)
- **Tests:** the last recorded full-suite run before the Week 1 changes was
  346 passing, `uv run pytest -q`. Run the current integrated suite before live
  execution. The suite is fully offline — HTTP is injected via
  `httpx.MockTransport` and loaders are injected. Keep it that way; no test may
  touch the network.
- **Lint:** `uv run ruff check src tests` and `uv run ruff format --check src tests`
  are both clean repo-wide.
- **Phase A is code-complete** (tasks 1-15, amendments 9a/12a/13a/14a, a final
  whole-branch review, its fix wave, and a scoped re-review).
- **The NFL archive backfill is complete and the backtest has a real number.**
  See "The phase-exit result" in `docs/results.md`.
- **Do not re-run either SDD task loop.** Every task in both plans is
  committed; `git log` is the record.

