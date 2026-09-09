# Week 1 operations runbook

This runbook is for the live weekly pick workflow. It is intentionally
manual at the CBS boundary: the tool prepares and records a sheet, while the
operator submits picks and the tiebreak in CBS. The examples use the canonical
working directory `/Users/jmiller/Dropbox/Personal/Betting/cfb` and CFB 2026
week 1 (15 games); replace the season, week, and expected count for another
slate.

## 1. Back up and hash the live database

Do this before any ingest or polling command. Never use the live database as a
rehearsal target. Keep this shell open so the unique paths are reused below;
rerunning with an existing `RUN_ID` is a stop condition, not permission to
overwrite the recovery point.

```bash
set -eu
ROOT=/Users/jmiller/Dropbox/Personal/Betting/cfb
LIVE_DB="$ROOT/data/pickem.duckdb"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DB="$ROOT/data/pickem.week1.backup.${RUN_ID}.duckdb"
HASH_FILE="$ROOT/data/pickem.week1.${RUN_ID}.sha256"
REHEARSAL_DB="$ROOT/data/pickem.week1.rehearsal.${RUN_ID}.duckdb"
export ROOT LIVE_DB RUN_ID BACKUP_DB HASH_FILE REHEARSAL_DB

if [ -e "$BACKUP_DB" ] || [ -e "$HASH_FILE" ] || [ -e "$REHEARSAL_DB" ]; then
  echo "refusing to overwrite an existing Week 1 artifact" >&2
  exit 1
fi
cp -n "$LIVE_DB" "$BACKUP_DB"
set -C
shasum -a 256 "$LIVE_DB" > "$HASH_FILE"
set +C
```

The timestamped backup, hash, and rehearsal paths are unique recovery points;
`cp -n` and shell `noclobber` make a rerun fail closed rather than destroy an
earlier artifact. Stop if the source database does not exist, the copy fails,
or the checksum cannot be recorded. Keep the backup and checksum until the
week is graded.

## 2. Rehearse against a database copy

Copy the live database to a separate path and run the complete preparation
sequence there first. This checks the saved CBS page, aliases, game count, and
market join without changing live history.

```bash
cp -n "$LIVE_DB" "$REHEARSAL_DB"

uv run pickem ingest-cbs \
  --html \
  --file "$ROOT/data/cbs/week1.html" \
  --sport cfb --season 2026 --week 1 \
  --db "$REHEARSAL_DB"

# Wide first poll: cover games that are still up to 11 days from kickoff.
uv run pickem poll-odds \
  --sport cfb --season 2026 --week 1 --days 11 \
  --db "$REHEARSAL_DB"

# Near kickoff, poll again with a shorter window that still covers the complete
# Sep 5–7 slate; a two-day window would omit the later Sunday/Monday games.
uv run pickem poll-odds \
  --sport cfb --season 2026 --week 1 --days 7 \
  --db "$REHEARSAL_DB"

uv run pickem preflight \
  --sport cfb --season 2026 --week 1 --expected-games 15 \
  --max-age-minutes 60 --min-books 3 \
  --db "$REHEARSAL_DB"
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

# Repeat close to the first kickoff with a window that still covers every
# Sep 5–7 game; keep the full-week bound so later games are not left stale.
uv run pickem poll-odds \
  --sport cfb --season 2026 --week 1 --days 7 \
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
Elo tiebreak. Elo now backs `NO_MARKET` only — `COINFLIP` takes the
frozen-board favorite and needs no history — but preflight still gates on that
history, so treat it as a hard requirement.

```bash
uv run pickem preflight \
  --sport cfb --season 2026 --week 1 --expected-games 15 \
  --max-age-minutes 60 --min-books 3 \
  --db "$LIVE_DB" \
&& uv run pickem report \
  --sport cfb --season 2026 --week 1 \
  --db "$LIVE_DB" \
  --out "$ROOT/data/cbs/week1-v1-picks.md"
```

The shell `&&` is deliberate: a nonzero preflight exits before `report` can
run. Run one report for each confirmed slate version and retain the versioned
markdown artifact. Every `report` invocation writes a pick batch to the
`picks` table, including a rerun, so a second report is a second recorded
decision rather than a harmless preview. `preflight` is the preview/readiness
probe when no write is wanted.

If CBS changes the slate or line before submission, do not overwrite version 1.
Ingest the new saved page, poll, and preflight again; after it is READY, write a
new artifact such as `week1-v2-picks.md` and mark version 1 **superseded** in
the notes. Preserve both markdown artifacts and both recorded pick batches for
audit history. If CBS changes after submission, record the change and the
supersession without deleting the original batch.

## 5. Submit manually and record the tiebreak

Review the final markdown sheet, then enter all game sides in CBS manually.
Submit the CBS pick sheet and its tiebreak total manually; there is no automated
CBS submission. Capture the submitted tiebreak total and the submission time in
the week's notes alongside the report hash. If CBS changes or locks the sheet
while reviewing, stop and repeat the saved-page ingest, poll, preflight, and
single final report sequence rather than mixing artifacts from two versions.

As each individual game starts, capture every participant's visible pick and
tiebreak incrementally in the same notes; picks become visible game by game,
not as one batch when CBS locks. This is an external manual record and is
intentionally not written to the pick database.

## 6. Post-week sync

Once final scores are available, pull them into the database so the next
week's Elo history is trained (it backs `NO_MARKET`, and preflight gates on it)
and the completed week can be audited.

```bash
uv run pickem sync-results \
  --sport cfb --season 2026 --week 1 \
  --db "$LIVE_DB"
```

`sync-results` fetches the complete CFBD week, which can contain more games
than the 15-game CBS slate. Do not compare its fetch total with 15. Instead,
run this exact read-only audit against the 15 league-line IDs and verify every
one has both final scores:

```bash
uv run python - <<'PY'
import duckdb

db = "/Users/jmiller/Dropbox/Personal/Betting/cfb/data/pickem.duckdb"
con = duckdb.connect(db, read_only=True)
rows = con.execute(
    """
    SELECT l.game_id, g.home_score, g.away_score
    FROM league_lines AS l
    JOIN games AS g USING (game_id)
    WHERE g.sport = 'cfb' AND g.season = 2026 AND g.week = 1
    ORDER BY l.game_id
    """
).fetchall()
con.close()
if len(rows) != 15:
    raise SystemExit(f"expected 15 league-line IDs, found {len(rows)}")
missing = [game_id for game_id, home, away in rows if home is None or away is None]
if missing:
    raise SystemExit(f"missing final scores for: {', '.join(missing)}")
print(f"audited {len(rows)} CBS league-line IDs; all have final scores")
PY
```

Stop and investigate if sync reports unresolved teams, or if this audit finds
fewer/more than 15 league-line IDs or any missing final score. Keep the
original unique backup, checksum, saved CBS HTML, versioned final report(s),
submission notes, and post-week sync/audit output together.

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
