# The Odds API historical endpoint — verified facts and backfill plan

**Written:** 2026-08-19
**Verified against:** the-odds-api.com live documentation, 2026-08-19
**Why this exists:** closing the backtest data gap (spec §9) costs money, and the
`lines` table is append-only. Both facts reward getting the plan right on paper
before spending a credit.

## Verified facts

| Fact | Value | Source |
|---|---|---|
| Endpoint | `GET /v4/historical/sports/{sport}/odds?date={iso8601}` | v4 guide |
| Per-event variant | `GET /v4/historical/sports/{sport}/events/{eventId}/odds` | v4 guide |
| Date semantics | returns the closest snapshot **at or earlier than** `date` | v4 guide |
| Response shape | envelope with `timestamp`, `previous_timestamp`, `next_timestamp`; events under `data` | v4 guide |
| Snapshot interval | 10 min before Sept 2022; 5 min after | v4 guide |
| Archive start, NFL | `2020-06-06T10:05:00Z` | historical-odds-data |
| Archive start, NCAAF | `2020-06-06T10:05:00Z` | historical-odds-data |
| Credit cost | **10 per region per market** (live is 1) | v4 guide |
| Tier eligibility | **paid plans only** — "Historical odds data is only available for paid subscriptions at this time" | historical-odds-data |
| Relevant tier | 20K — $30/month, 20,000 credits | pricing page |

Two consequences worth stating plainly:

- **The archive covers the full 2020-2025 span we wanted.** No shrunken sample,
  no widened Wilson intervals on account of coverage.
- **"At or earlier" means the proxy cannot peek forward.** A request stamped
  Tuesday 09:00 ET never returns a later snapshot, so the frozen-line proxy is
  structurally incapable of leaking submission-time information. This is a
  stronger guarantee than the spec assumed.

## Decision: both ends of the divergence come from the same source

Spec §9 listed three routes to historical openers and the third — reconstructing
**both** open and close from the Odds API archive — is the one we are taking.
This is not a departure from the spec; it is the spec's own third bullet.

**Why not pair an Odds API opener against the free nflverse closer.** The two
numbers would come from different book compositions. Some fraction of what the
backtest then scores as "line movement" would be a source artifact, and nothing
in the result could separate the artifact from the signal. Since divergence *is*
the entire strategy, contaminating its measurement defeats the point of running
the backtest at all.

**The nflverse closers already loaded stay** — as a free cross-check rather than
as an input. If the two closing numbers disagree materially on the same game,
the new loader is wrong and we want to know before reading any hit rate.

## Snapshot timing

**Frozen-line proxy:** one snapshot per week at a fixed early-week offset
(**Tuesday 09:00 ET**). This is a better analog than a true "opening line": CBS
freezes its number early in the week, which is what we are imitating, whereas a
true opener is whenever each book first posted — a different and less relevant
moment.

**Submission-time proxy:** the last snapshot before each game's kickoff.
Kickoffs are staggered, so this is **not** one snapshot per week. One snapshot
contains every game with posted odds, so the requests needed per week equal the
number of *distinct kickoff slots*, not the number of games:

- NFL: roughly 5 slots — Thu night, Sun early, Sun late, Sun night, Mon night
- NCAAF: roughly 4 slots — Thu, Fri, Sat afternoon, Sat night

Request `date = kickoff - 5 minutes` per slot and let "at or earlier" resolve it.

## Credit budget

| Pass | Weeks | Snapshots/wk | Requests | Credits |
|---|---|---|---|---|
| NFL frozen-line | ~126 | 1 | 126 | 1,260 |
| NFL submission | ~126 | ~5 | ~630 | ~6,300 |
| CFB frozen-line | ~90 | 1 | 90 | 900 |
| CFB submission | ~90 | ~4 | ~360 | ~3,600 |
| **One full pass** | | | **~1,200** | **~12,060** |

Against the 20K tier that is roughly 40% headroom — enough for one corrective
re-run, not several. **Therefore: run NFL first (~7,560 credits), verify it,
then decide on CFB.** NFL alone is the ~1,600-game sample spec §9 sized the
phase-exit decision around; CFB is upside.

The earlier working estimate of ~4,200 credits was wrong. It assumed one closing
snapshot per week, which staggered kickoffs make impossible.

## What this requires in code

- **`OddsClient` needs a historical method.** `fetch_spreads` cannot be reused
  unchanged — the historical response wraps its events in the snapshot envelope.
  The per-event parsing beneath that is identical and should be shared, not
  duplicated.
- **`split_proxies` classifies by book name** (`runner.py:87`), and
  `CLOSER_SOURCE = "nflverse"` (`runner.py:33`) is currently the only recognized
  closer. Both need to admit same-source Odds API pairs. The classification must
  stay explicit in both directions — the existing catch-all `skipped` bucket is
  what stops in-season `poll-odds` rows from being graded as closers, and that
  protection must survive.
- **`ASSUMPTIONS[0]` (`runner.py:22`) says "opening line"** and will be false
  once the frozen-line proxy is a Tuesday snapshot. The backtest states its
  assumptions in its own output; a stale string there is a lie in the artifact
  the phase-exit decision gets read from.
- **The window/slate guards still apply.** The historical feed is the same
  nationwide firehose as the live one. Backfilling without a kickoff window
  would write wrong game ids permanently into the append-only table — the exact
  Critical finding from the final review, in a path where it is *harder* to
  notice because no one is reading a weekly sheet afterward.
- **Get book naming right before the first write.** Append-only means a
  mislabeled backfill cannot be cleaned up, only worked around.

## Open question, deliberately not answered here

Whether CBS's frozen number actually tracks a Tuesday-morning market snapshot
closely is still an assumption — an untested one, and the load-bearing
assumption of the whole backtest. It becomes checkable in a couple of weeks,
once real CBS pastes start accumulating alongside live `poll-odds` snapshots.
Until then the backtest measures market-open-to-close divergence, which is the
right proxy but is not the same claim.
