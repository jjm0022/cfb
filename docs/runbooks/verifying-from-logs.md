# Verify Pickem from logs

Pickem writes one correlated record stream to three files. The default
directory is `~/LOGS/pickem`; set `PICKEM_LOG_DIR` when the service uses a
different directory.

```bash
LOG_DIR="${PICKEM_LOG_DIR:-$HOME/LOGS/pickem}"
L="$LOG_DIR/pickem.jsonl"
TEXT="$LOG_DIR/pickem.log"
ERRORS="$LOG_DIR/errors.log"
```

The files have different jobs:

| File | Level and retention | Use |
| --- | --- | --- |
| `pickem.log` | INFO and above, 30 days | Human-readable decisions, run boundaries, and warnings. |
| `pickem.jsonl` | DEBUG and above, 90 days | One flat JSON object per record for `jq` and DuckDB, including intermediate math. |
| `errors.log` | ERROR and above, 180 days | Failures and their redacted tracebacks. |

All sinks rotate daily and compress rotated files. Flush queued records before
reading a file when checking a process you control; a running service will
flush its queue as records are written.

## The three supported questions

These are the standard JSONL questions. Run them against the active file (use
the compressed-file command below for rotated archives).

### Did the scheduled job run?

The exact supported predicate is
`select(.entry=="sched:refresh" and .event=="run_started")`:

```bash
jq -r 'select(.entry=="sched:refresh" and .event=="run_started")' "$L"
```

Each matching row includes the `run_id`, configured database path, and
timestamp. Resolved sports and weeks are emitted later as `scope_resolved`
within that run, not on `run_started`. To count fires by day:

```bash
jq -r 'select(.entry=="sched:refresh" and .event=="run_started") | .ts[0:10]' "$L" \
  | sort | uniq -c
```

For one UTC day, add a timestamp filter without changing the supported event
predicate:

```bash
DAY=2026-09-04
jq -r --arg day "$DAY" \
  'select(.entry=="sched:refresh" and .event=="run_started" and .ts[0:10]==$day)' "$L"
```

The scheduler's own intercepted pre-call record is independent evidence that
APScheduler started the job. It is emitted before the refresh callback opens
its `run_context`, so it may have the sentinel `run_id` and is not part of the
scheduled run's replay or join. Inspect it with:

```bash
jq -r 'select(.event=="apscheduler_log" and (.message | contains("Running job"))) | "\(.ts)  \(.message)"' "$L"
```

### Why was this pick made?

The exact supported predicate is
`select(.game_id=="..." and .event=="edge_decided")`. It carries the final
side, tier, delta, and the rationale in `message`:

```bash
GAME_ID="cfb:away:home"
jq -r --arg game "$GAME_ID" \
  'select(.game_id==$game and .event=="edge_decided")' "$L"
```

Take the returned row's `run_id` and widen the query to the intermediate
decision records from that same run:

```bash
RUN_ID="a3f1b2c9"
jq -r --arg r "$RUN_ID" \
  'select(.run_id==$r and (.event=="edge_decided" or .event=="edge_measured" or .event=="consensus_computed"))' "$L"
```

### Why is this game missing?

The standard compatibility predicate is
`select(.event=="odds_event_filtered" or .event=="odds_row_skipped")`.
Pickem follows the unified vocabulary ruling: every dropped odds row is
emitted exactly once as a WARNING with `event="odds_row_skipped"` and a
`guard` field. It does not emit a duplicate `odds_event_filtered` record.
Therefore the canonical Pickem query is:

```bash
jq -r 'select(.event=="odds_row_skipped") | "\(.ts)  \(.guard // "-")  \(.message)"' "$L"
```

To inspect one game's drops, add its identifier when present:

```bash
GAME_ID="cfb:away:home"
jq -r --arg game "$GAME_ID" \
  'select(.event=="odds_row_skipped" and .game_id==$game) | "\(.guard)  \(.message)"' "$L"
```

The `guard` identifies the decision (`commence_time`, `kickoff_window`,
`unknown_team`, `slate`, `no_spreads_market`, or `no_spread_point`). A skip in
`MarketLinesResult.skipped` has one corresponding WARNING row; do not count a
second event under the old compatibility name.

## Find and replay a run

List run starts to discover the short eight-character identifier:

```bash
jq -r 'select(.event=="run_started") | "\(.ts)  \(.run_id)  \(.entry)"' "$L"
```

Replay every application record in order across modules and worker threads:

```bash
RUN_ID="a3f1b2c9"
jq -r --arg r "$RUN_ID" \
  'select(.run_id==$r) | "\(.level[0:4])  \(.event)  \(.message)"' "$L"
```

The replay should normally begin with `run_started` and end with
`run_finished`; a failed run ends with one `run_failed` ERROR instead. The
`run_id` is the join key for records emitted after the application opens the
run context, including intermediate decision, ingest, store, and
`scope_resolved` records. The scheduler's pre-call `apscheduler_log` record is
independent firing evidence and is intentionally outside that join.

Rotated JSONL archives can be replayed by decompressing them first:

```bash
zcat "$LOG_DIR"/*.jsonl.zip 2>/dev/null \
  | jq -r --arg r "$RUN_ID" 'select(.run_id==$r) | "\(.level[0:4])  \(.event)  \(.message)"'
```

## DuckDB coverage and duration trends

Use DuckDB when the question needs grouping or aggregation. The following
recipes use the same typed fields as the JSONL queries. The glob includes
rotated uncompressed JSONL files; query compressed archives separately after
`zcat` if needed.

```bash
duckdb -c "
SELECT tier, count(*) AS picks
FROM read_json_auto('$LOG_DIR/*.jsonl')
WHERE event = 'edge_decided'
GROUP BY tier ORDER BY picks DESC;
"
```

To find polls below the three-book preflight floor:

```bash
duckdb -c "
SELECT ts, sport, week, books
FROM read_json_auto('$LOG_DIR/*.jsonl')
WHERE event = 'odds_polled' AND books < 3
ORDER BY ts DESC;
"
```

To trend scheduled refresh duration by day:

```bash
duckdb -c "
SELECT ts[1:10] AS day,
       round(avg(duration_ms)) AS avg_ms,
       max(duration_ms) AS worst_ms
FROM read_json_auto('$LOG_DIR/*.jsonl')
WHERE event = 'run_finished' AND entry = 'sched:refresh'
GROUP BY day ORDER BY day;
"
```

The quota and tiebreak queries from the standard are also useful during an
investigation. Two rules emit `tiebreak_applied`, so `method` says which one
decided: `frozen_line_favorite` for `COINFLIP`, `elo` for `NO_MARKET`. Only the
Elo records carry `projected_margin`, hence the `// "-"` fallback:

```bash
jq -r 'select(.event=="odds_quota") | "\(.ts[0:16])  \(.remaining)"' "$L"
jq -r 'select(.event=="tiebreak_applied") | "\(.game_id)  \(.tier)  \(.method)  \(.side)  \(.projected_margin // "-")"' "$L"
```

## Check secret absence safely

Never paste a credential into a command, interpolate it into a shell
substitution, print it, or include it in a support ticket. Run the following
from the project root. It reads configured values only inside the process and
reports variable names and file paths, never values. It checks active and
rotated (ZIP) sink files without writing to the log directory.

```bash
uv run python - <<'PY'
from pathlib import Path
import os
import zipfile

names = ("ODDS_API_KEY", "CFBD_API_KEY", "DISCORD_BOT_TOKEN")
log_dir = Path(os.environ.get("PICKEM_LOG_DIR", Path.home() / "LOGS" / "pickem"))
files = [p for p in log_dir.glob("*") if p.suffix in {".log", ".jsonl", ".zip"}]
if not files:
    print(f"ERROR: no Pickem sink files found in {log_dir}")
    raise SystemExit(2)

configured = {name: os.environ.get(name) for name in names}
env_file = Path(".env")
if env_file.exists():
    for raw_line in env_file.read_text().splitlines():
        key, separator, value = raw_line.partition("=")
        if separator and key.strip() in names and not configured[key.strip()]:
            configured[key.strip()] = value.strip().strip("'\"")

if not any(configured.values()):
    print("ERROR: no configured secret values were available for comparison")
    raise SystemExit(2)

found = False

def check(name, secret, path, payload):
    global found
    if secret and secret.encode() in payload:
        print(f"FAIL: {name} appears in {path}")
        found = True

for name in names:
    secret = configured[name]
    if not secret:
        continue
    for path in files:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    check(name, secret, f"{path}!{member}", archive.read(member))
        else:
            check(name, secret, path, path.read_bytes())

if found:
    raise SystemExit(1)
print("OK: configured secret values were absent from Pickem sink files")
PY
```

If a value was exposed, stop using the affected credential, rotate it through
the provider, preserve the redacted failure evidence, and investigate how it
reached the sink. Do not print the old or new value while diagnosing the
incident.
