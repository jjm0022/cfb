# Decision Logging and Observability

## Purpose

Make the pick'em system explain itself. Every recommendation it produces and
every unattended run it performs must leave a durable record that answers two
questions without a database query or a code reading: *did this run do what it
was supposed to do*, and *why did it choose this side*.

The system already computes these answers and discards them. `Edge.rationale`
carries a human sentence for every pick. `MarketLinesResult.skipped` and
`ParseResult.skipped` carry a one-liner for every row that did not survive
ingestion. `PreflightGame.reasons` explains every game that is not ready. The
CLI prints some of this to a terminal nobody is watching; the Discord bot drops
nearly all of it. This design gives those artifacts a home on disk.

## Scope

- Emit a dual-sink log — human-readable text and machine-parseable JSON lines —
  under `~/LOGS/pickem/`.
- Cover the live weekly path in depth: CBS ingest, odds polling, team
  resolution, divergence and Elo decisions, recommendation generation,
  preflight, the automation monitor, and the Discord bot with its scheduler.
- Correlate every record produced by one run under a shared run identifier.
- Absorb `discord.py`, `apscheduler`, and `httpx` standard-library logging into
  the same sinks.
- Establish the conventions as a machine-wide standard in a shared agent skill
  so sibling projects log identically.

Backtest and calibration modules receive the logger and emit run-boundary and
totals records only. Per-game instrumentation of those sweeps is deliberately
out of scope: a single sweep iterates thousands of games and would dominate the
sinks for a workflow that is already reproducible on demand.

This release does not add log shipping, a metrics backend, alerting, or any
change to what the Discord bot reports to its owner.

## Architecture

`pickem.obs.log` is a leaf module with no dependency on any other project
module. It owns sink configuration, the redaction filter, the standard-library
intercept, and the run-context manager. Nothing else in the codebase configures
logging; every other module only calls `logger`.

Configuration happens exactly once per process, at an entry point: the Typer
application callback in `cli.py` and `discord_bot.main()`. A library that
configures logging at import time steals the decision from its caller and makes
test output unpredictable, so `configure_logging()` is never called at module
scope. It is idempotent: a second call replaces handlers rather than
duplicating them.

Records reach the sinks through loguru's `bind`/`contextualize` mechanism
rather than through formatted message strings, which is what keeps the text and
JSON sinks in agreement — the same record renders as a sentence in one and as
typed fields in the other.

## Sinks

Three files under `$PICKEM_LOG_DIR`, defaulting to `~/LOGS/pickem`, created at
startup if absent:

| File | Level | Retention | Contents |
| --- | --- | --- | --- |
| `pickem.log` | INFO | 30 days | Human-readable. Decisions, run boundaries, warnings. |
| `pickem.jsonl` | DEBUG | 90 days | One JSON object per line. Every record, including the intermediate math. |
| `errors.log` | ERROR | 180 days | Failures only, with tracebacks. |

All three rotate daily at midnight and compress on rotation. `errors.log`
exists so that "did anything break this month" is answerable by reading one
short file rather than filtering a large one.

Two loguru settings are load-bearing:

- `enqueue=True` on every sink. The Discord bot writes from the event loop, from
  APScheduler's executor thread, and from `asyncio.to_thread` workers at the
  same time. Without a queue those writes interleave mid-line and corrupt the
  JSON sink.
- `diagnose=False` on every sink. Loguru's `diagnose` option expands local
  variable values into tracebacks. `OddsClient` holds the value of
  `config.odds_api_key()` in a local, so enabling it would write the API key
  into `errors.log` the first time an HTTP call raised.

Tracebacks are rendered into the message by the redaction stage rather than by
loguru, and the sinks therefore set `backtrace=False`. The reason is specific:
loguru renders `record["exception"]` after and outside anything a filter can
rewrite, so a secret carried in an exception message would reach disk
unredacted. `OddsClient._get` passes the key as an `apiKey` query parameter and
folds `httpx` transport failures into `OddsApiError` messages, which is exactly
the path by which a secret arrives in a record the call site never wrote.
Formatting the traceback into the message and clearing `exception` puts the
frames back under redaction; they are preserved in full.

A stderr sink at WARNING and above is added for the CLI only. `typer.echo`
remains the user-facing command output and must not compete with log records.

## Run Correlation

Every entry point opens a run context that binds, for the duration of the call,
a short `run_id` and the identifying facts of the run: `entry`
(`cli:poll-odds`, `discord:/refresh`, `sched:refresh`), and where known `sport`,
`season`, and `week`.

The binding uses `logger.contextualize()`, which is backed by context
variables. Context variables propagate into asyncio tasks and across
`asyncio.to_thread`, so a Discord `/refresh` stamps its identifier onto every
record emitted beneath it — through the odds poll running in a worker thread and
down into the Elo tiebreak — without any module passing a logger around.

This is what makes the JSON sink interrogable. Selecting one `run_id` replays
that run in order, across module and thread boundaries.

## Event Vocabulary

Every record carries a stable `event` field. The name is the query key, so it
is a fixed identifier rather than a sentence, and it never changes once
published. Levels follow the contract in the next section.

**Run lifecycle.** `run_started` with the resolved parameters and database
path; `run_finished` with an outcome and duration; `run_failed` with the
exception type and message.

**Ingest.** `ingest_parsed` with game and skip counts; `ingest_row_skipped`, one
per row, carrying the existing `ParseResult.skipped` text; `ingest_stored` with
rows written.

**Odds polling.** `odds_request` with endpoint, sport key, window, and attempt
number; `odds_retry` with attempt, status, and backoff; `odds_quota` with the
credits used and remaining reported by the response headers, which is currently
invisible to the operator; `odds_event_filtered` naming the event dropped, the
guard that dropped it — kickoff window or slate membership — and the values that
decided it; `odds_row_skipped` carrying the existing `MarketLinesResult.skipped`
text; `odds_polled` with lines appended, distinct books, and games covered.

**Decision layer.** `consensus_computed` with each book's latest spread, the
stale per-book snapshots that were collapsed, and the resulting median;
`edge_measured` with the league spread, market consensus, delta, resulting
tier, and the threshold values in effect; `tiebreak_applied` with the Elo
ratings, the projected margin, the board, and the resulting side;
`edge_decided`, one per pick, carrying the final side, tier, delta, and the
existing `Edge.rationale`; `edges_ranked` with the ordered result.

`edge_measured` records the thresholds in effect alongside the delta, which is
what makes the log sufficient to answer whether a different cutoff would have
produced a different board.

**Preflight.** `preflight_evaluated` with the overall verdict, and each
not-ready reason as its own warning.

**Automation.** `refresh_started`, `refresh_succeeded` with the changed flag,
and `refresh_failed`; `recommendation_changed` carrying the difference text the
monitor already builds; `state_saved` with the persisted timestamp and
signature; `notify_sent` and `notify_suppressed`, the latter recording the
fingerprint match that deduplicated a repeat failure.

**Discord and scheduling.** `bot_ready`; `command_invoked` with the command
name and parameters; `command_completed` with duration; `scope_resolved` naming
the sports and weeks the bot resolved as active and the reason each qualified;
`job_scheduled` with the next fire time; `job_fired`.

`scope_resolved` and `notify_suppressed` are the two records that close the
current blind spots. Silence in the present system is ambiguous — it cannot
distinguish a scope that succeeded from one that was never attempted, or a
suppressed duplicate notification from a notification that was never generated.

**Store.** `schema_initialized`, `db_opened`, and `rows_written` with table and
count, all at debug level.

## Level Contract

| Level | Meaning |
| --- | --- |
| DEBUG | Inputs and intermediate math — how a number was reached. |
| INFO | A decision or state change: every pick, every run boundary, every write. |
| WARNING | Something was dropped, degraded, retried, or fell back. Every `skipped` one-liner is a warning. |
| ERROR | An operation failed. Exactly one record per failure. |
| CRITICAL | The unattended service cannot continue. |

Two rules preserve the contract's meaning.

**No log-and-throw.** A function that raises does not also log. The resolver
raises `UnknownTeamError` carrying its "did you mean" suggestion; only the
caller that handles that exception logs it. In the odds path that caller is
`_parse_events`, which already converts it to a skip, so an unmapped school
appears once as `odds_row_skipped` and not twice under two levels. Logging at
both sites inflates error counts and makes handled conditions look like
failures.

**One error per failure.** The boundary that decides the operation failed emits
the ERROR record. Intermediate frames that re-raise stay silent.

## Secret Handling

The existing prohibition on logging secrets is now enforced in the logging
layer rather than only observed by convention at call sites.

A filter runs on every record before it reaches a sink and redacts the values of
`ODDS_API_KEY`, `CFBD_API_KEY`, and `DISCORD_BOT_TOKEN` wherever they appear in
a message or a bound field, along with `apiKey` query parameters and
`Authorization` header values. Redaction operates on the configured secret
values, so it holds even when a secret reaches a record through a URL or an
exception message the call site did not construct. The filter reads the values
through `os.environ` at configuration time and tolerates their absence, so a
process that needs only one of the three still redacts that one.

`diagnose=False`, described under Sinks, is the second half of this guarantee.
The existing `test_config_required_still_has_no_secret_logging` generalizes into
a test that induces a failure in each instrumented path and asserts no sink
contains either secret.

## Standard Library Interception

An intercept handler installed on the root standard-library logger forwards
`discord.py`, `apscheduler`, and `httpx` records into loguru, preserving their
level and origin.

This yields two things. The gateway chatter currently filling the journal moves
into files where it can be retained and filtered on its own terms. More
usefully, APScheduler's own job-execution records land in the same sinks under
the same run context, which means the log carries independent confirmation from
the scheduler that a job fired, rather than only the instrumented code's account
of it.

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `PICKEM_LOG_DIR` | `~/LOGS/pickem` | Sink directory. |
| `PICKEM_LOG_LEVEL` | `DEBUG` | Floor for the JSON sink. |
| `PICKEM_LOG_CONSOLE` | `WARNING` | Console sink level; `off` disables it. |

The `<PROJECT>_LOG_DIR` / `_LEVEL` / `_CONSOLE` triple is the machine-wide
convention, not a pickem-specific one.

## Testing

Unit coverage for the logging module: the redaction filter scrubs each secret
from messages, bound fields, and exception text; `configure_logging()` is
idempotent and replaces rather than accumulates handlers; the sink files are
created with the expected levels; the intercept handler preserves the level and
logger name of a standard-library record.

Integration coverage for the instrumentation: a refresh against a fixture
database emits exactly one `edge_decided` per game, each carrying a rationale
equal to the returned `Edge.rationale`, and every record from that refresh
shares a single `run_id`. A failing refresh emits exactly one ERROR and a
`run_failed`, and no ERROR from an intermediate frame.

Tests capture records by adding a list sink in a fixture. Loguru does not route
through the standard `caplog` fixture, and configuring it to do so trades a
clear mechanism for a familiar one. Any test that reads a sink file rather than
a list sink must call `logger.complete()` first, because `enqueue=True` defers
writes to a background thread.

Assertions target `record["extra"]["event"]` and the bound fields, never the
rendered message text, which is written for humans and will be reworded.

## Consequences

The instrumented paths gain a dependency on `loguru` and a call to
`configure_logging()` at two entry points. No existing signature changes and no
existing behavior changes; a record is emitted where a value was previously
computed and discarded.

Disk cost at the current cadence — roughly six scheduled refreshes a week over
a fifteen-game slate, plus manual commands — is on the order of a few megabytes
per month before compression, against retention windows measured in months.

The convention becomes machine-wide through
`~/.agents/skills/logging-standards/`, which is the shared skill root that Codex
discovers automatically and that `~/.claude/skills/` symlinks into. Sibling
projects `props` and `props-dfs-multiplier` adopt the same layout, level
contract, event-naming discipline, and secret rules by following that skill.
