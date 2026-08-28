# Week 1 operations runbook

This runbook is for the live weekly pick workflow. It is intentionally
manual at the CBS boundary: the tool prepares and records a sheet, while the
operator submits picks and the tiebreak in CBS. The examples use the canonical
working directory `/Users/jmiller/Dropbox/Personal/Betting/cfb` and CFB 2026
week 1 (15 games); replace the season, week, and expected count for another
slate.

## 1. Back up and hash the live database

Do this before any ingest or polling command. Never use the live database as a
rehearsal target.

```bash
cp /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb \
  /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.week1.backup.duckdb
shasum -a 256 /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb \
  | tee /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.week1.sha256
```

Stop if the source database does not exist, the copy fails, or the checksum
cannot be recorded. Keep the backup and checksum until the week is graded.

## 2. Rehearse against a database copy

Copy the live database to a separate path and run the complete preparation
sequence there first. This checks the saved CBS page, aliases, game count, and
market join without changing live history.

```bash
cp /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb \
  /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.week1.rehearsal.duckdb

uv run pickem ingest-cbs \
  --html \
  --file /Users/jmiller/Dropbox/Personal/Betting/cfb/data/cbs/week1.html \
  --sport cfb --season 2026 --week 1 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.week1.rehearsal.duckdb

# Wide first poll: cover games that are still up to 11 days from kickoff.
uv run pickem poll-odds \
  --sport cfb --season 2026 --week 1 --days 11 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.week1.rehearsal.duckdb

# Near kickoff, poll again with a shorter window to avoid unrelated events.
uv run pickem poll-odds \
  --sport cfb --season 2026 --week 1 --days 2 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.week1.rehearsal.duckdb

uv run pickem preflight \
  --sport cfb --season 2026 --week 1 --expected-games 15 \
  --max-age-minutes 60 --min-books 3 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.week1.rehearsal.duckdb
```

The rehearsal preflight must print `Overall: READY` and one ready row for each
expected game. A `NOT READY` result is a stop gate, not a warning. Do not
generate a report from a rehearsal copy as a substitute for the final live
run; the final report must be built from the database whose final poll was
verified.

## 3. Prepare the live database

After the rehearsal succeeds, repeat the saved-page ingest and polling against
the live path. `ingest-cbs --html` reads the saved HTML and stores CBS frozen
league lines; it does not fetch CBS. `poll-odds` reads the live Odds API and
appends market history, so confirm the API key and quota before starting.

```bash
uv run pickem ingest-cbs \
  --html \
  --file /Users/jmiller/Dropbox/Personal/Betting/cfb/data/cbs/week1.html \
  --sport cfb --season 2026 --week 1 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb

uv run pickem poll-odds \
  --sport cfb --season 2026 --week 1 --days 11 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb

# Repeat close to the first kickoff with a short window.
uv run pickem poll-odds \
  --sport cfb --season 2026 --week 1 --days 2 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb
```

If CBS reports skipped or unresolved rows, if the Odds API reports quota
exhaustion or an unavailable feed, or if the poll stores fewer rows than the
slate needs, stop. Preserve the terminal output and use the backup while
repairing the source data; do not guess aliases or hand-edit the database.

## 4. Run the final preflight, then exactly one report

Preflight is deliberately read-only: it opens the existing database without
initializing a schema and never writes picks. It checks exact game and league
line counts, future kickoffs, live `oddsapi` coverage, freshness, distinct
book count at each game's latest snapshot, and completed prior history for the
Elo tiebreak.

```bash
uv run pickem preflight \
  --sport cfb --season 2026 --week 1 --expected-games 15 \
  --max-age-minutes 60 --min-books 3 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb

uv run pickem report \
  --sport cfb --season 2026 --week 1 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb \
  --out /Users/jmiller/Dropbox/Personal/Betting/cfb/data/cbs/week1-picks.md
```

Only run `report` after `Overall: READY`. Run it once for the final sheet and
retain the markdown artifact. Every `report` invocation writes a pick batch to
the `picks` table, including a rerun, so a second report is a second recorded
decision rather than a harmless preview. `preflight` is the preview/readiness
probe when no write is wanted.

## 5. Submit manually and record the tiebreak

Review the final markdown sheet, then enter all game sides in CBS manually.
Submit the CBS pick sheet and its tiebreak total manually; there is no automated
CBS submission. Capture the submitted tiebreak total and the submission time in
the week's notes alongside the report hash. If CBS changes or locks the sheet
while reviewing, stop and repeat the saved-page ingest, poll, preflight, and
single final report sequence rather than mixing artifacts from two versions.

After CBS locks, capture the opponent's visible picks and tiebreak in the same
notes. This is an external manual record and is intentionally not written to
the pick database.

## 6. Post-week sync

Once final scores are available, pull them into the database so the next
week's Elo history is trained and the completed week can be audited.

```bash
uv run pickem sync-results \
  --sport cfb --season 2026 --week 1 \
  --db /Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb
```

Stop and investigate if sync reports unresolved teams, zero final scores, or a
game count that does not match the submitted slate. Keep the original backup,
checksum, saved CBS HTML, final report, submission notes, and post-week sync
output together.

## Stop and fallback gates

- No backup/hash, no existing database, or a rehearsal mismatch: stop before
  touching the live path.
- CBS parse skips, unknown teams, missing league lines, or a count other than
  the declared `--expected-games`: stop and obtain a corrected saved page.
- Odds API errors, quota exhaustion, missing live coverage, future/stale
  snapshots, or fewer than `--min-books` books: stop polling/reporting and
  preserve the last known-good artifacts. Do not treat cached stale odds as a
  ready report.
- Missing completed history means Elo is untrained: stop until the prior-week
  `sync-results` (or an approved historical load) is complete.
- If the live command path remains unavailable near lock, use the CBS website
  manually only with an explicitly recorded operator decision; do not claim the
  database report was ready and do not fabricate market values. Re-run the
  full sequence when the data path is restored.
