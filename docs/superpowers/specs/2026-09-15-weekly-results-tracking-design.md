# Weekly Results Tracking

## Purpose

Find out, week by week, **why** a pool week went the way it did, so the
strategy can be improved on evidence rather than on the feeling of a bad week.

Every week has three separate questions, and a bad week can come from any of
them:

1. **Was the model wrong?** Grade the model's last recommendation before each
   kickoff against the result.
2. **Were the submitted picks the model's?** Grade what was actually entered in
   CBS, and flag every game where it differs from the model.
3. **How did the field do?** Rank, gap to the winner, how the field's
   consensus did, and how our picks against the field did. The pool pays per
   week, so beating the field matters, not only beating 50%.

The priority is model first, field second.

## What prompted this, and what the first look found

Pool weeks 1 and 2 of 2026 were saved from CBS's "Weekly Standings" page as
`data/cbs/results/week1.html` and `week2.html`. A throwaway parse of those
pages found (the user's entry is `Jota`):

| Pool week | Board | Jota | Rank of 53 | Field median / best |
|---|---|---|---|---|
| 1 | CFB | 8/15 | 21st | 8 / 10 |
| 2 | CFB | 7/15 | — | — |
| 2 | NFL | 9/16 | — | — |
| 2 | both | 16/31 | 17th | 15 / 20 |

The pool-week 2 CFB picks entered in CBS differ from the archived sheet
`data/cbs/weeks/week2-cfb-v1-picks.md` (generated 2026-09-08) on 9 of 15 games.
The logs under `~/LOGS/pickem` show those 9 picks (PSU, ILL, MSST, IOWA, TAM,
UK, BYU, TENN, TEX) as later recommendations from the Discord refresh, after
the coinflip tiebreak changed to the frozen-board favorite on 2026-09-09. The
submissions followed the model; the archived sheet was stale. The v1 sheet
would have gone 8/15.

That is the core design constraint: **the recommendation that counts is the
last one before each game's kickoff**, and today nothing durable stores it. The
`picks` table holds only `report` batches, and the logs rotate.

## Scope

- Parse a saved CBS Weekly Standings page into structured data for every
  entrant and every game.
- Store the model's recommendation history durably from now on, and backfill
  pool weeks 1 and 2.
- Grade us, the model, the field, and several baselines. Report weekly and
  season-to-date with honest uncertainty.
- Write a Markdown report per pool week and DM a summary on Discord.

## Out of scope

- Fetching the CBS page automatically. The user saves it by hand, as with pick
  pages.
- Changing pick strategy. The reports inform later, separate decisions.
- Seasons before 2026.
- Changing the `picks` table or the rule that only `report` records pick
  batches.

## Source: the CBS Weekly Standings page

One saved page covers one **pool week**, which is both league boards: CFB week
N and NFL week N−1 (pool week 1 is CFB only). The standings table is rendered
DOM. It is not in the Apollo blob: that blob on the saved results page carries
the *current* period's events, not the week displayed.

The parser keys only on `data-testid` attributes, never on CSS classes. The
`mui-*` classes are hashed per CBS build, the same reason `cbs_html.py` keys on
JSON field names. All anchors below were verified against both saved pages.

| Anchor | Carries |
|---|---|
| `event-hdr-cell-<cbsEventId>` | One header per game. Contains a gametracker link `.../{nfl,college-football}/gametracker/.../{NFL,NCAAF}_YYYYMMDD_AWAY@HOME/`, `event-status` (`FINAL`, `FINAL/OT`), `team-abbrev` (away, home), `team-score` (away, home), and `team-spread` (home line, e.g. `(-3.5)`) |
| `pickem-weekly-table-row-<entryId>` | One row per entrant. `entryId` is CBS's stable entry id |
| `standings-player-name-cell` | Rank text (`1st`, `21st`, ties repeat) and display name |
| `weekly-pts-cell`, `weekly-ytd-cell` | Week points and season-to-date points (YTD reflects the save time, not the week) |
| `weekly-tb-cell` | Tiebreaker guess (integer) |
| `pickem-weekly-table-cell-<cbsEventId>` | The team abbreviation picked, and CBS's grade as `MuiSvgIcon-colorSuccess` or `MuiSvgIcon-colorError`. A blank pick renders a `-` span with no icon (9 cells in week 1, 21 in week 2) |

Both saved pages hold 53 entrant rows. Week 1 has 15 games; week 2 has 31.

## Architecture

```
data/cbs/results/weekN.html
        │
        ▼
ingest/cbs_results.py  ──parse──▶  ParsedStandings (games, entrants, picks)
        │
        ▼
link + validate (TeamResolver, stored games, grading cross-check)
        │
        ▼
store: pool_results, pool_picks   ◀── recommendation_history ◀── monitor (live)
        │                                                    ◀── backfill (logs, picks)
        ▼
report/results.py  ──grade/aggregate──▶  ResultsReport
        │
        ├──▶ data/cbs/results/weekN-report.md
        └──▶ notify/discord_dm.py ──REST──▶ owner DM
```

Each unit has one job and a plain-data interface. The parser knows HTML and
nothing about the database. The grader knows stored rows and nothing about
HTML or Discord. The notifier knows an embed and a token.

## Component 1: parser — `src/pickem/ingest/cbs_results.py`

`parse_cbs_results_html(text: str) -> ParsedStandings`, a pure function.

`ParsedStandings` holds:

- `games`: for each header, `cbs_event_id`, `sport` (from the link's
  `NFL`/`NCAAF` tag), `game_date`, `away_abbrev`, `home_abbrev`, `status`,
  `away_score`, `home_score`, `spread_home` (float).
- `entrants`: `entry_id`, `name` (whitespace-trimmed), `rank` (integer from the
  ordinal), `points`, `ytd`, `tiebreak` (integer or `None`).
- `picks`: `entry_id`, `cbs_event_id`, `picked_abbrev` (or `None` for `-`),
  `cbs_grade` (`correct`, `incorrect`, or `None` when blank).

It raises `CbsParseError` with a specific message when:

- there is no standings table, or no header cells;
- a header lacks a link, abbreviations, scores, or a spread;
- any game's status is not final (`FINAL` or `FINAL/OT`); a mid-week save must
  not be graded;
- a row's pick cells do not cover exactly the header event ids;
- a picked abbreviation is neither of that game's two teams;
- a non-blank cell's grade icon is neither success nor error. Current boards
  use half-point lines, so no real push markup has been seen, and guessing it
  would be fabrication.

## Component 2: link, validate, store

### Pool week → league weeks

`league_week(sport, pool_week)` is CFB `pool_week` and NFL `pool_week − 1`,
matching the README and `scripts/import-cbs-week.sh`. An NFL game on pool week
1 is an error.

### Linking games

For each parsed game:

1. Resolve `away_abbrev` and `home_abbrev` with `TeamResolver` for its sport.
   CBS abbreviations not yet known (e.g. `OREGST`, `WASHST`, `OKLAST`) are
   added to `resolve/aliases.yaml`.
2. Match a stored `games` row with the same sport, season, league week, and
   home/away team ids.
3. Check the stored scores, when present, against the page's scores. A
   difference is an error naming both.

The import aborts before writing if any team is unresolved, any game is
unmatched, or a board's game count differs from the number of stored games
that have a league line for that league week.

### Grading cross-check

For every non-blank pick, compute the result with `backtest.stats.grade_pick`
from the page's scores and CBS line, and compare it to `cbs_grade`. Any
mismatch aborts the import and names the game and entrant. This catches both a
grading misunderstanding and a misparsed column.

### Tables (added to `store/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS pool_results (
    season      INTEGER NOT NULL,
    pool_week   INTEGER NOT NULL,
    entry_id    VARCHAR NOT NULL,
    name        VARCHAR NOT NULL,
    rank        INTEGER NOT NULL,
    points      INTEGER NOT NULL,
    ytd         INTEGER NOT NULL,
    tiebreak    INTEGER,
    imported_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (season, pool_week, entry_id)
);

CREATE TABLE IF NOT EXISTS pool_picks (
    season       INTEGER NOT NULL,
    pool_week    INTEGER NOT NULL,
    entry_id     VARCHAR NOT NULL,
    game_id      VARCHAR NOT NULL,
    cbs_event_id BIGINT  NOT NULL,
    side         VARCHAR,          -- 'home' | 'away' | NULL (blank)
    cbs_correct  BOOLEAN,          -- NULL when blank
    PRIMARY KEY (season, pool_week, entry_id, game_id)
);

CREATE TABLE IF NOT EXISTS recommendation_history (
    game_id      VARCHAR NOT NULL,
    sport        VARCHAR NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,   -- league week
    side         VARCHAR NOT NULL,
    tier         VARCHAR NOT NULL,
    edge_points  DOUBLE  NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    source       VARCHAR NOT NULL,   -- 'monitor' | 'report' | 'log_backfill'
    PRIMARY KEY (game_id, generated_at, source)
);
```

Re-importing a pool week deletes and rewrites its `pool_results` and
`pool_picks` rows inside one transaction. `recommendation_history` is
append-only (`INSERT OR IGNORE`).

The page's CBS line and scores are not stored again. `league_lines` and
`games` already hold them, and the linking step checks they agree.

### Recommendation history

`recommendation_history` records *what the model said and when*. That is a
different fact from `picks`, which records a rendered `report` batch.
`generate_recommendations` keeps its documented guarantee that it never calls
`record_picks`.

- **Live:** the automation monitor appends one row per game from each
  refresh's snapshot, with `source = 'monitor'`. The `report` command appends
  its batch with `source = 'report'`, next to its existing `record_picks`
  call. The monitor change goes on top of the uncommitted work in
  `automation/monitor.py` and `discord_bot.py`, never replacing it.
- **Backfill:** `pickem backfill-recommendations --season 2026` reads
  - every `picks` row (these become `source = 'report'`), which is the only
    record for CFB week 1 because logging began on 2026-09-04;
  - every JSONL log record under `~/LOGS/pickem` (current and rotated `.zip`)
    whose message is `"<AWAY> at <HOME>: pick <TEAM> (<tier>) - ..."`, with
    the record's timestamp and structured fields. These become
    `source = 'log_backfill'`.

  It is idempotent. It reports how many rows it added per sport and week, and
  how many log lines it could not resolve to a stored game.

**The model's pick for a game** is the history row with the greatest
`generated_at` that is strictly before the game's `kickoff_utc`, across all
sources. If none exists, the model's pick is "unknown" and the game is excluded
from model grading, with a visible count.

## Component 3: grading and analysis — `src/pickem/report/results.py`

`build_results_report(store, season, through_pool_week) -> ResultsReport`. It
reads only stored data, and it reuses `grade_pick`, `wilson_interval`,
`consensus_spread`, `latest_by_book`, and `favorite_side`.

### Per-game row

| Field | Definition |
|---|---|
| line, score, cover | CBS home line, final score, covering side (or push) |
| us | Jota's submitted side and result |
| model | last pre-kickoff recommendation: side, tier, edge, result; `agrees_with_us` |
| field | share of non-blank entrant picks on each side; consensus side (majority, no consensus on an exact tie); its result |
| close | `consensus_spread` over `latest_by_book` of market lines captured before kickoff; `None` when there are none |
| CLV (us) | points the close moved toward our side relative to the CBS line: `cbs_home − close_home` for a home pick, the negation for an away pick; `None` when close is `None` |
| against field | our side had ≤ 40% of non-blank entrant picks |

The "us" entry is identified by `--entry-name` (default `Jota`) on
`import-results` and `results-report`. It is matched against the display names
of each pool week. If there is no exact match, the command fails and lists the
names it found.

### Aggregates

Computed for the pool week and season to date, split by sport, and for the
model also by tier:

- W–L–P and hit rate over decided games, with a 95% Wilson interval and n.
- The backtest expectation beside each tier: strong 63.7%, lean 54.2%,
  coinflip 49.5% (`docs/results.md`, NFL 2020–2025). These are labeled as NFL
  backtest numbers wherever CFB is shown.
- Mean CLV, and the share of picks with CLV > 0, over games with a close.

### Baselines

Each is graded on the same decided games:

| Baseline | Pick rule |
|---|---|
| model | last recommendation before kickoff |
| first sheet | earliest recommendation in `recommendation_history` for the game |
| close divergence | home when `close_home < cbs_home` (the market makes home a bigger favorite than CBS), away when greater; no pick when equal or no close |
| favorites | `favorite_side(cbs_line)` |
| home | home team |
| field consensus | field majority; no pick on a tie |

A baseline with no pick on a game excludes that game from its own rate and
shows its n.

### Field context

For each pool week: our rank and percentile, the field median, the winner's
points, the gap to the winner, and the record of our against-the-field picks.

### "What the data says"

These statements are generated from the numbers, never hand-written. A
comparison is stated as a difference only when the two 95% intervals do not
overlap. That test is deliberately conservative, since comparisons on the same
games are paired, and it errs toward saying nothing. Examples: a tier against its backtest expectation, us against a
baseline, us against the model. Every other comparison is listed as "not
distinguishable yet (n = …)". Mean CLV is described as positive or negative
only when its normal-approximation 95% interval excludes 0.

## Component 4: outputs

### Commands (in `cli.py`)

```
pickem import-results --season 2026 --pool-week N [--file PATH] [--entry-name NAME] [--no-notify]
pickem results-report --season 2026 [--pool-week N] [--entry-name NAME] [--notify]
pickem backfill-recommendations --season 2026 [--logs DIR]
```

- `import-results` parses, links, validates, stores, writes the report, and
  sends the DM unless `--no-notify`. `--file` defaults to
  `data/cbs/results/week{N}.html`.
- `results-report` rebuilds the report from the database. It sends a DM only
  with `--notify`. `--pool-week` defaults to the latest imported week.
- Exit codes: 0 on success, nonzero on a parse, link, or validation failure
  (nothing written), and nonzero on a DM failure (import and report kept).

### Report file — `data/cbs/results/week{N}-report.md`

1. **Headline:** points, rank of n, field median, winner, gap. Overall and per
   board.
2. **This week, by board:** us / model / field / close-divergence records, then
   the per-game table ordered by kickoff, marking disagreements with the model
   and against-the-field picks.
3. **Season to date:** records by sport × tier with intervals and backtest
   expectations, the CLV summary, and the baselines table.
4. **What the data says.**

Every rate prints as `W–L (P) = xx.x% [lo–hi], n`.

### Discord DM — `src/pickem/notify/discord_dm.py`

- `build_results_embed(report) -> discord.Embed` is pure. It holds the
  headline, one field per board (us vs model vs field), season-to-date by tier,
  CLV, and the report path. It stays within Discord limits via
  `_field_value_chunks`.
- `send_owner_dm(embed, token, owner_id)` uses a one-shot discord.py REST
  session (`login`, `fetch_user`, `send`, `close`) with no gateway connection.
  It works whether or not the bot service is running, and it does not touch
  the bot process. Credentials come from `config.discord_bot_token()` and
  `config.discord_owner_id()`.

### Logging

This follows the `logging-standards` skill and the existing `pickem.obs.log`
conventions. Each command logs its decisions with reasons:

- entrants, games, and picks parsed;
- games linked;
- cross-check result;
- rows written or replaced;
- history rows added per source;
- games with an unknown model pick;
- report path;
- DM sent, or failed with the error.

## Testing

Built test-first.

### Fixture

`tests/fixtures/cbs_results_page.html` is trimmed from the real week-2 page.
It keeps the real markup and `data-testid` anchors, with 4 games spanning both
sports (including a `FINAL/OT`) and 5 entrant rows (including a real blank
`-` pick). Entrant names and entry ids other than `Jota` are replaced with
placeholders, because `data/` is gitignored and real private names do not
belong in a repository with a remote.

### Unit tests

- **Parser (`tests/test_cbs_results.py`):**
  - games, sport tags, scores, lines, and statuses;
  - entrant fields and ordinal ranks, including ties;
  - picks and blanks, and grade icons;
  - every `CbsParseError` condition listed in Component 1, including a
    non-final status and an unknown icon.
- **Linking and store (`tests/test_results_import.py`):**
  - the pool-week → league-week translation, including the NFL-in-pool-week-1
    error;
  - abbreviation resolution;
  - unmatched or unresolved games and count mismatches abort with no rows
    written;
  - a score disagreement aborts;
  - a grading cross-check mismatch aborts;
  - a re-import replaces rows;
  - `recommendation_history` ignores duplicates;
  - `picks` and `record_picks` are unchanged.
- **Backfill (`tests/test_recommendation_backfill.py`):**
  - log lines are parsed, including from `.zip` files;
  - non-matching lines are ignored;
  - unresolvable games are counted;
  - running it twice adds nothing.
- **Grading (`tests/test_results_report.py`):**
  - the model pick is strictly before kickoff, never a later row;
  - an unknown model pick is excluded and counted;
  - the CLV sign is right for home and away picks, and CLV is `None` without a
    close;
  - each baseline is checked on hand-built games;
  - field ties give no consensus;
  - intervals match `wilson_interval`;
  - "What the data says" makes no claim when intervals overlap, and does make
    one when they do not;
  - the Markdown renders the expected sections.
- **Monitor (`tests/test_monitor.py`):**
  - a refresh appends history rows;
  - the existing tests, including the uncommitted ones, still pass.
- **Discord (`tests/test_discord_dm.py`):**
  - embed content and field limits;
  - the sender is tested with a fake client for success, a missing token, and
    a send failure;
  - the CLI exits nonzero on DM failure and the report file still exists.

### Acceptance on real data

This is manual, because `data/` is not in git.

1. `backfill-recommendations --season 2026` reports rows for CFB weeks 1–2 and
   NFL week 1.
2. `import-results` for pool weeks 1 and 2 succeeds.
3. For all 53 entrants in both weeks, stored points and ranks equal the page.
   Jota is 8 points and 21st in week 1, and 16 points and 17th in week 2.
4. Every game's computed result equals CBS's icon (enforced by the import).
5. In pool week 2 CFB, the model's pick for PSU–TEM, DUKE–ILL, and MSST–MINN is
   the later refreshed recommendation (PSU, ILL, MSST), matching Jota's
   submissions, not the 2026-09-08 sheet.
6. `week1-report.md` and `week2-report.md` exist, and one real DM arrives.
7. `uv run pytest` and `uv run ruff check` pass.

## Decisions and rejected alternatives

- **DuckDB tables over re-parsing HTML on demand.** Field data becomes
  queryable and accumulates across the season. Re-parsing about 2 MB per week
  on every report was rejected, and so was a CSV-only export, which cannot drive
  a repeatable report or DM.
- **`recommendation_history` instead of writing `picks` from automation.** The
  diary's rule that only `report` records pick batches stays intact. Submitted
  picks come from CBS itself.
- **Last pre-kickoff recommendation as "the model".** Week 2 showed the
  submissions follow refreshed recommendations. Grading an early sheet would
  blame the model for picks it no longer made.
- **CLV reported next to win–loss.** About 15 games per board make win–loss
  intervals too wide to conclude anything for weeks. Closing-line movement is
  a faster, lower-variance signal of whether the market read is right.
- **One-shot Discord REST instead of adding a job to the bot.** The DM arrives
  right after import, needs no bot restart, and does not couple to the
  uncommitted bot work.
- **Fail closed on unknown markup.** A misread standings page would silently
  corrupt every conclusion drawn from it, so parse and cross-check failures
  abort the import.
