# CFB/NFL Pick'em Edge Engine — Design

**Date:** 2026-08-11
**Status:** Approved design, pending implementation plan
**Phase:** A (market-first). Phases B and C are explicitly out of scope for this spec.

## 1. Goal

Win a CBS Sports pick'em league in which the user picks all NFL games and a
small slate of CFB games against the spread.

**Success criteria, in priority order:**

1. A ranked weekly pick sheet is produced in under a minute of user effort.
2. The backtest harness reports an honest, out-of-sample ATS hit rate with a
   confidence interval, so the strategy's value is measured rather than assumed.
3. No game is ever silently dropped or mismatched between data sources.

A hit rate is a *measurement*, not a target. The system is successful if it
tells the truth about its own edge, including when that edge is zero.

## 2. League rules (the binding constraints)

| Rule | Value | Design consequence |
|---|---|---|
| Scoring | Flat, 1 point per correct pick, plus a tiebreaker | Maximize expected correct picks. No confidence-point allocation problem. |
| Spread source | CBS posts spreads early in the week and **freezes** them | The frozen number is a stale price. |
| Submission deadline | Any time up to kickoff | The market keeps moving after the price is locked. **This gap is the entire edge.** |
| Slate | All NFL games + ~5-15 marquee FBS games | ~20-30 games/week, all well-covered by data sources. |
| Picks required | Every game | No abstention. The system must emit an opinion on every game, and must be honest about which opinions are coin flips. |

## 3. Strategy rationale

The closing line is a very strong forecast; beating it consistently with a
homebrew model is hard. But the user is not betting into the closing line — they
are betting into a **frozen Tuesday line** with a Sunday deadline.

When the market moves from the frozen number, that movement reflects
information the frozen number does not contain: injuries, weather, sharp money.
Taking the stale side of a moved line is a well-documented positive-expectation
position, and it requires no forecasting skill of our own — only accurate
measurement of two numbers.

Phase A therefore treats **line divergence as the primary signal** and uses a
deliberately minimal power rating only to rank games the market has not moved.

## 4. Data sources

| Source | Cost | Used for |
|---|---|---|
| **nflreadpy** (nflverse) | Free (MIT) | NFL schedules/results 1999+, closing `spread_line`/`total_line`, play-by-play w/ EPA |
| **CFBD** (cfbd-python) | Free key; Patreon tiers for higher limits | CFB games, results, betting lines by provider |
| **The Odds API** | Free tier (500 credits/mo) | Current market spreads across books during the week |
| **CBS pick sheet** | Manual paste | The frozen league line |

The Odds API free tier is sufficient for live use: polling two sports a few
times daily over a weekend costs a few dozen credits of 500.

**One deliberate paid exception — see §9.** Reconstructing historical opening
lines requires one month of a paid Odds API tier (~$29). This is a one-time
backfill, cached locally, then cancelled.

## 5. Tech stack and CLI surface

Python 3.12+, dependencies and virtualenv managed by **uv**. Package layout is
`src/pickem/`, tests in `tests/`. Runtime dependencies: `nflreadpy`, `cfbd`,
`duckdb`, `polars`, `httpx`, `typer`, `pydantic`, `pyyaml`. Dev: `pytest`,
`pytest-cov`, `ruff`.

Records are `pydantic` models at module boundaries; `polars` frames only inside
adapters and the backtest, never across a public interface.

A single `typer` CLI, `pickem`:

| Command | Purpose |
|---|---|
| `pickem ingest-cbs [--file PATH]` | Parse the pasted CBS block (or stdin) into `league_lines`; prints a parse summary and exits non-zero on any unresolved game |
| `pickem poll-odds` | Append a current market snapshot to `lines` for the active week |
| `pickem report [--week N]` | Render the ranked pick sheet to terminal and markdown |
| `pickem sync-results` | Pull final scores from nflverse/CFBD into `games` |
| `pickem backfill --from 2020` | One-time historical load for the backtest |
| `pickem backtest [--from 2020] [--to 2025]` | Replay history through `edge/` and report hit rate with Wilson intervals |

Normal in-season use is `ingest-cbs` once, `poll-odds` on a schedule, and
`report` before submitting.

## 6. Architecture

Strictly one-directional dependencies. No module imports anything downstream.

```
CBS paste ──┐
            ├─→ [ingest] ──→ [resolve] ──→ [store] ──→ [edge] ──→ [report]
Odds API ───┤                    ↑            │           ↑
CFBD ───────┤                    │            │           │
nflverse ───┘              team_aliases    DuckDB    [backtest]
```

### 6.1 `ingest/`
One adapter per source, each returning uniform records. Adapters own their
source's quirks and export none of them.

- `cbs.py` — parses the pasted text block into `LeagueLine` records
- `odds.py` — The Odds API client, current spreads per book
- `cfbd.py` — CFBD client for CFB games/results/lines
- `nflverse.py` — nflreadpy loader for NFL games/results/closing lines

### 6.2 `resolve/`
Canonical team identity. A curated YAML maps every source's naming onto a
canonical `team_id`.

**Fail-loud policy:** an unrecognized team name raises `UnknownTeamError`. The
system never guesses, never fuzzy-matches into production, and never drops a
game. Fuzzy matching may be used *only* to generate suggested alias additions
in the error message.

This module is written first and tested hardest. Silent identity mismatches are
the most likely cause of a system that appears to work and is quietly wrong.

### 6.3 `store/`
A single DuckDB file. SQL over Parquet, no server.

```
teams(team_id PK, sport, canonical_name, conference)
team_aliases(alias, source, team_id)          -- (alias, source) unique
games(game_id PK, sport, season, week, kickoff_utc,
      home_team_id, away_team_id, home_score, away_score, status)
league_lines(game_id, season, week, spread_home, posted_at)   -- frozen CBS baseline
lines(line_id PK, game_id, source, book, spread_home,
      total, captured_at)                     -- APPEND-ONLY
picks(season, week, game_id, side, edge_points, tier, generated_at)
```

`lines` is append-only and never updated. Line *movement* is the signal;
overwriting a row destroys the thing we are trying to measure.

All spreads are stored **from the home team's perspective** (home favored by 3
is `-3.0`). Every adapter normalizes to this convention at ingest. This is the
single most error-prone convention in the system and is asserted in tests.

### 6.4 `edge/`
Pure functions over records. No I/O, no database, no network — which makes it
exhaustively testable and is where the strategy lives.

Given a frozen league spread `L` and current market consensus `M` (both
home-perspective):

```
delta = L - M
delta > 0  →  pick HOME   (we hold home at a better price than the market's)
delta < 0  →  pick AWAY
```

Market consensus `M` is the **median** spread across available books at the
latest snapshot before the deadline. Median, not mean, to resist one stale or
erroneous book.

**Confidence tiers** (Phase A, magnitude-based):

| Tier | Condition |
|---|---|
| STRONG | \|delta\| >= 2.0 |
| LEAN | 1.0 <= \|delta\| < 2.0 |
| COINFLIP | \|delta\| < 1.0 → fall through to the tiebreak rating |

These thresholds are **initial guesses to be replaced by backtested values.**
They are configuration, not logic, and the backtest exists to correct them.

**Key-number handling is deliberately excluded from Phase A.** Movement across
3 and 7 in the NFL is worth more than movement elsewhere, but weighting it
correctly requires the backtest to already exist. It is the first refinement to
evaluate once the harness runs.

**COINFLIP tiebreak:** a minimal Elo margin rating (NFL from nflverse results,
CFB from CFBD results) projects a margin; we pick the side the projection
favors relative to the frozen line. This rating is intentionally unsophisticated
— it exists to order coin flips, not to beat the market. Any temptation to
elaborate it belongs in Phase B.

**Tiebreaker prediction:** a separate small function projecting total points for
the league's designated tiebreaker game, from the market total. Low stakes; the
market total is the estimate.

### 6.5 `report/`
Renders the ranked sheet to terminal and a markdown file: every game, chosen
side, delta, tier, and the frozen-vs-market numbers that produced it. The user
must be able to audit any pick without opening the database.

### 6.6 `backtest/`
Replays historical weeks through `edge/` and scores them. Depends only on
`edge/` and `store/`.

Reports ATS hit rate overall and per tier, with **Wilson confidence
intervals** — a 54% hit rate on 200 games is not distinguishable from noise, and
the report must make that obvious rather than flattering.

## 7. CBS paste parser

Input is whatever the user copies from the CBS pick sheet. The parser:

1. Extracts candidate `(away, home, spread)` triples by line-oriented regex
2. Resolves both team names through `resolve/`
3. Normalizes the spread to home perspective
4. Reports a **parse summary** — games found, games resolved, anything skipped

If any line fails to parse or resolve, the command exits non-zero with the
offending text. A partially-ingested week is worse than a failed one, because
it looks like success.

Real pasted samples are captured as test fixtures. The CBS format will change;
fixtures make that a five-minute fix rather than a debugging session.

## 8. Error handling

- **Unknown team** → `UnknownTeamError`, non-zero exit, suggested alias in message
- **Missing market line for a game** → game reported as `NO_MARKET`, falls to tiebreak rating; never silently skipped
- **Odds API quota exhausted** → warn, proceed with the most recent cached snapshot, stamp the report with snapshot age
- **Network failure** → retry with backoff, then fail loudly; never fabricate or interpolate a line

The consistent principle: **degrade visibly, never silently.** Every output
carries the provenance and age of the data behind it.

## 9. Backtest data gap (known constraint)

The user's *historical* frozen CBS lines do not exist anywhere — past weeks were
never recorded. The backtest therefore uses a proxy:

- **Proxy for the frozen line:** the market opening line
- **Proxy for submission-time market:** the closing line

This is a defensible stand-in, since CBS's frozen number is set early in the
week and tracks the opener closely. It is *not* identical, and the backtest
report must state this assumption in its output rather than burying it here.

Obtaining historical openers:

- nflverse `load_schedules()` provides **closing** lines back to 1999 — half the pair
- CFBD betting lines expose multiple providers, sometimes with opener and current
- The Odds API historical endpoint offers 5-minute snapshots back to June 2020,
  from which both open and close can be reconstructed

**Recommendation:** subscribe to The Odds API for one month, backfill 2020-2025
snapshots into local Parquet, then cancel. One-time ~$29 for a durable local
dataset, rather than an ongoing subscription.

Starting from 2020 yields roughly 1,600 NFL games. That is enough to detect a
large effect and not enough to certify a small one — the Wilson intervals will
say so honestly.

## 10. Testing strategy

TDD throughout. Tests are written before implementation.

- `resolve/` — every source's naming quirks; unknown names raise; no silent fallback
- `cbs.py` — fixture-driven parsing of real pasted blocks, including malformed input
- `edge/` — table-driven tests over the sign convention; both pick directions;
  tier boundaries; the `NO_MARKET` path
- `store/` — append-only invariant on `lines` is enforced and tested
- `backtest/` — deterministic on fixed input; no network in tests

Network calls are stubbed at the adapter boundary. The test suite runs offline.

## 11. Out of scope (YAGNI)

Explicitly excluded from Phase A:

- Any frontend or web UI (backend only, per the user)
- Automated submission of picks to CBS
- Scraping CBS with stored credentials
- Player props, totals, moneyline, or any market other than the spread
- Opponent-adjusted EPA models, injury modeling, weather (all Phase B)
- Key-number weighting (evaluate after the backtest exists)
- Multi-user or multi-league support

## 12. Phase exit criteria

Phase A is complete when a full week runs end to end — paste, poll, report — and
the backtest produces a hit rate with a confidence interval over 2020-2025.

**The decision that follows:** if line-divergence alone backtests meaningfully
above 52.4% (the break-even rate against standard -110 juice, used here as a
reference point for a real edge even though this league has no juice), Phase B
modeling may be unnecessary. If it lands near 50%, that is learned cheaply,
before writing a modeling stack.
