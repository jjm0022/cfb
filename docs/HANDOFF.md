# Handoff — CFB/NFL Pick'em Edge Engine

**Written:** 2026-08-11
**Last updated:** 2026-09-09, after the COINFLIP tiebreak change and the split
of this file into topic documents
**Purpose:** resume work after a context reset. Read this first, then follow the
map below.

## What we're building

A Python backend that helps win a CBS Sports pick'em league (all NFL games +
~5-15 marquee CFB games, picked against the spread).

**The strategy, in one sentence:** CBS freezes its spreads early in the week but
accepts picks until kickoff, so the edge is the gap between that stale frozen
number and the live market line at submission time — not a homebrew prediction
model. Line movement is a stronger ATS signal than anything we could forecast
ourselves.

Scoring is flat (1 point per correct pick) with a tiebreaker, so the incumbent
decision rule maximizes expected correct picks and has no confidence-point
allocation problem. The 2026-08-28 strategy research additionally considers
the user's stated objective—winning at least one weekly pool—where opponent
duplication and prize share matter.

## Start here

You have a working system with a real result. Everything described in these
documents is integrated in `master`, which is at `b0cb542` as of 2026-09-10.
It has taken, in order, the CFB COINFLIP and Week 1 preflight work (`6ed6ed7`),
the Discord bot, the logging/observability deployment (`177900c`), later
CBS-parsing and pick-sheet fixes (`3b184ff`), and the 2026-09-09 COINFLIP
tiebreak change with its documentation. Orient yourself in about two minutes:

```bash
uv run pytest -q                              # verify current integrated suite
uv run pickem backtest --from 2020 --to 2025  # expect the phase-exit result
uv run pickem --help                          # the whole surface, 12 commands
```

The authoritative checkout is `master`, now at `b0cb542`. The Week 1 readiness
implementation was integrated at `6ed6ed7` by fast-forward from
`feature/week1-preflight`, adding the `preflight` command and its tests; the
help surface has since grown to 12 commands (verified 2026-09-09).

**The remote is `origin`, `git@github.com:jjm0022/cfb.git`.** It had drifted 29
commits behind — its `master` sat at `a4bb432`, predating the logging
deployment — and was brought back in sync on 2026-09-10. Check with
`git rev-list --left-right --count origin/master...master` rather than trusting
this line; the gap had gone unnoticed for weeks once before.

Then read **"The phase-exit result"** in `docs/results.md` — it is the finding
everything else now serves — and "Remaining work" at the bottom of this file
for what to do next.

**The strategy is not a prediction model and should not become one by
accident.** Every instinct to "improve the picks" should be checked against
`docs/invariants.md` first; several obvious improvements are known to be wrong
and are recorded there with their reasons.

## Where things live

This file was 968 lines until 2026-09-09. It is now an index; the content moved
into topic documents, unchanged. Pick by the question you arrived with:

| Question | Document |
|---|---|
| **Can I change this?** | `docs/invariants.md` — load-bearing conventions, decisions not to re-litigate, the two odds-poll guards |
| **What do we actually know?** | `docs/results.md` — the phase-exit result and tier volume, the calibration result, why the tiebreak cannot rescue coinflips, and the bugs real data found |
| **Where does this live?** | `docs/system-map.md` — module-by-module map and the tests worth knowing about |
| **What data do we have?** | `docs/data-inventory.md` — database state, the paid archive acquisition, live API and archive verification |
| **How did we get here?** | `docs/deployment-history.md` — dated deployment records and their verification |

Supporting material, roughly in reading order:

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
7. **Weekly-win research:**
   `docs/research/2026-08-28-weekly-win-coinflip-strategy.md` — the no-cost
   opponent-aware experiment, paid-data ranking, and purchase break-even gate.
8. **Week 1 operations runbook:**
   `docs/runbooks/week1-operations.md` — backup, rehearsal, preflight-gated
   report generation, manual submission, incremental observation, and results
   auditing.
9. **Log verification runbook:** `docs/runbooks/verifying-from-logs.md` — how to
   confirm from `~/LOGS/pickem` that an unattended run did what it should.

## Current state

### COINFLIP tiebreak changed from Elo to the frozen-board favorite (2026-09-09)

**This supersedes every "Elo remains the live COINFLIP decider" statement in
`docs/deployment-history.md`.** `COINFLIP` now resolves through
`edge/favorite.py`: `Side.HOME` when the frozen spread is `<= 0`, so an exact
pick'em resolves home. `NO_MARKET` still resolves through `edge/elo.py`.

No new experiment was run. The evidence was a diagnostic already inside the
Candidate 2 report — over the same 1,549 evaluated coinflips, Elo scored 48.93%,
the frozen-board favorite 51.00%, and always-pick-home 50.94%. The incumbent was
losing to two one-line heuristics. That 2.07-point gap is roughly one standard
deviation on this sample, so **this bought simplicity and one less fitted
component, not demonstrated accuracy**; do not cite 51% as skill.

Behavioural consequence, and the reason it matters operationally: Elo chose
ATS-relative and flipped to the underdog once the board outran its projected
margin — the mechanism described in "Why the tiebreak cannot rescue coinflips"
in `docs/results.md`, which put 11 of 15 week 1 coinflips on the dog. The
favorite rule has no such crossover, so a -40 coinflip now returns home where
Elo returned away.

Both rules emit `tiebreak_applied` carrying a `method` field
(`frozen_line_favorite` or `elo`) that distinguishes them in the JSONL.

Scope limit: every evaluated game has a market line by definition, so the
evaluation says nothing about `NO_MARKET`, which is why Elo was left in place
there. Preflight still gates on completed Elo history.

Integrated into `master` on 2026-09-10 by fast-forward from
`feature/coinflip-favorite-tiebreak` (`01e6479`, the code) and then
`docs/tiebreak-and-handoff-accuracy` (`b0cb542`, these documents). Verified on
`master` after the merge: 526 tests pass, Ruff clean.

### Everything before that

Both CFB COINFLIP candidates returned NULL, the CFB 2021–2025 archive is
complete, and the Week 1 operational readiness deployment is integrated. The
dated records, with their verification evidence and hashes, are in
`docs/deployment-history.md`.

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
  submission proxies for the explicit coverage population recorded in
  `docs/data-inventory.md`. The phase-exit backtest result remains NFL-only,
  but the separate CFB COINFLIP evaluation is frozen as a NULL result.
- **The CBS paste gap is CLOSED for CFB 2026 week 1, still open for NFL.**
  The saved page ingested, polled, rendered and calibrated on 2026-08-20 — see
  "The calibration result" in `docs/results.md`. The NFL weekly path still has
  never run against a real CBS sheet, and the archive proxy the phase-exit
  result rests on is NFL. The CFB agreement is strong evidence for the method
  but is not the same slate.
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
  it — see "Why the tiebreak cannot rescue coinflips" in `docs/results.md`.
  `report` no longer warns, because there is real history; the picks are
  informed but weak. Since 2026-09-09 this reaches only `NO_MARKET`: `COINFLIP`
  takes the frozen-board favorite, which has no underdog crossover.
- **nflverse's null-spread branch is defensive only.** A real 1999-2025 sweep
  loaded 7,276 closing lines with ZERO null spreads and no `UnknownTeamError`,
  so only the injected-loader unit test covers it. Kept as a guard. Relevant
  because resolution now precedes the null check: a row with both a null spread
  and an unknown abbreviation raises where it once skipped. The sweep proves
  that combination does not occur in 1999-2025.

## Remaining work

Task 12 is complete, and the Candidate 2 team-residual family is closed. Do not
retune Candidate 1, refit on 2026, alter thresholds, or treat either frozen
result as permission for a feature search. The CFB 2021–2025 archive is
complete. The Week 1 operational-readiness deployment
`week1_preflight_20260828` is complete and integrated at `master` commit
`6ed6ed7`. The next work is the no-cost, COINFLIP-only,
opponent-aware paper experiment described in
`docs/research/2026-08-28-weekly-win-coinflip-strategy.md`; do not buy a new
backfill until its predeclared gate and pool-specific break-even calculation
support the spend.

Season timing, for context: the CFB season opens in late August 2026 and NFL
week 1 is early September 2026. In-season polling is cheap (1 credit per call),
so the free tier would cover weekly use if the subscription is cancelled.
