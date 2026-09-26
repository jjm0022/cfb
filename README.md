# pickem

A command-line tool for a CBS Sports pick'em pool: every NFL game plus a handful
of marquee college games, picked against the spread.

**The idea, in one sentence:** CBS freezes its spreads early in the week but
accepts picks until kickoff, so the edge is the gap between that stale frozen
number and the live market line at submission time.

It is not a prediction model and should not become one. The tool measures
divergence between two real numbers — the frozen CBS line and the current
market consensus — and ranks picks by that gap. Where there is no gap, it falls
back to a deliberately dumb tiebreak rather than a forecast.

**It does not submit anything.** The pipeline ends at a markdown pick sheet.
You type the sides and the tiebreaker into CBS yourself.

---

## Requirements

| | |
|---|---|
| Python | 3.12 or newer (`.python-version` pins 3.12) |
| Package manager | [uv](https://docs.astral.sh/uv/) — the project uses `uv.lock` and the `uv_build` backend |
| Odds API key | [the-odds-api.com](https://the-odds-api.com) — live market spreads. Free tier is enough for weekly use (2 credits per poll: US books, then Pinnacle) |
| CFBD API key | [collegefootballdata.com](https://collegefootballdata.com) — college schedules and final scores |
| Discord bot token | Optional, only for the reminder bot |

Everything is local: the database is a single DuckDB file, and no service other
than the two data APIs is contacted.

## Install

```bash
git clone git@github.com:jjm0022/cfb.git
cd cfb
uv sync                 # creates .venv and installs from uv.lock
```

`uv run pickem --help` should print 12 commands. There is no separate install
step — `uv run` resolves the `pickem` entry point from the project.

If you prefer an activated environment, `source .venv/bin/activate` then use
`pickem` directly; every example below uses `uv run` so it works either way.

## Configure

Create `.env` in the project root. It is gitignored, and real environment
variables always win over it.

```dotenv
ODDS_API_KEY=your-odds-api-key
CFBD_API_KEY=your-cfbd-api-key

# Only if you run the Discord bot:
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_OWNER_ID=123456789012345678
```

```bash
chmod 600 .env
```

Optional environment variables:

| Variable | Default | Effect |
|---|---|---|
| `PICKEM_LOG_DIR` | `~/LOGS/pickem` | Where the three log files are written |
| `PICKEM_LOG_LEVEL` | `DEBUG` | Level for the file sinks |
| `PICKEM_LOG_CONSOLE` | `WARNING` | Level for the terminal; `off` silences it |

Saved CBS pages, the reports built from them, and backups live on the NAS at
`/mnt/nas/Betting/pickem/` (`results/`, `weeks/`, `backups/`), so a page saved
from any machine is readable here without an rsync.

The database is the exception: it defaults to `data/pickem.duckdb`, local to
the repo, and is created on first write. **Do not move it to the NAS.** The
share is CIFS, which does not enforce DuckDB's lock — two processes can open
the same file for writing with no error and corrupt it. Every command takes
`--db` if you want a different path (use it for rehearsals — see the weekly
workflow below).

## Verify the install

```bash
uv run pytest -q        # the full suite, offline; no API keys needed
uv run ruff check .
uv run pickem --help
```

Tests never touch the network or your real database: HTTP transports are faked
and logs are redirected to a temp directory.

---

## Concepts you need before running anything

**Spreads are always home-perspective.** Home favored by 3 is `-3.0`, home
getting 3 is `+3.0`. Each source has its own convention and the adapters
normalize at ingest; nothing downstream re-checks, so a sign error would be
silent. This is pinned by tests — don't "fix" it.

**Tiers.** Each game is labelled by how far the market has moved off the frozen
CBS line:

| Tier | Meaning |
|---|---|
| `strong` | ≥ 2.0 points of divergence |
| `lean` | ≥ 1.0 point |
| `coinflip` | Less than that — no real edge. Resolved by taking the frozen-board favorite (home when the frozen spread is ≤ 0) |
| `no_market` | No market line stored for the game at all. Resolved by an Elo rating built from stored results |

A coinflip pick is a coinflip. The tiebreak exists so every game on the sheet
has a side, not because it is expected to win.

**Pool week ≠ league week.** The pool runs two boards per week — one CFB, one
NFL — and their league week numbers differ, because the college season starts
first. Pool week 2 was CFB week 2 *and* NFL week 1. **CLI flags take the league
week**; the saved sheets under `/mnt/nas/Betting/pickem/weeks/` are named by
the pool week.
Translate before you type `--week`.

**`report` writes.** Every `report` run records a pick batch in the `picks`
table, reruns included — it is a recorded decision, not a preview. Use
`preflight` when you want to look without writing.

---

## The weekly workflow

Run this once per league board (so twice per pool week, often days apart,
because CBS posts the two boards on different schedules). The examples use CFB
2026 week 2 with 15 games; substitute your own.

### 1. Back up the live database

Do this before any ingest or poll. The `lines` table is append-only and a bad
row can never be cleaned up.

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUPS=/mnt/nas/Betting/pickem/backups
mkdir -p "$BACKUPS"
cp -n data/pickem.duckdb "$BACKUPS/pickem.${RUN_ID}.duckdb"
sha256sum data/pickem.duckdb > "$BACKUPS/pickem.${RUN_ID}.sha256"
```

### 2. Capture the CBS sheet

Two input paths, both offline — nothing in this tool fetches CBS:

- **Saved page (preferred).** In the browser, save the CBS pick sheet as HTML to
  e.g. `/mnt/nas/Betting/pickem/weeks/week2.html`, then pass `--html`. This
  path also gives real
  kickoff times.
- **Pasted text.** Copy the game list into a file, one game per line, away team
  first:

  ```
  Buffalo Bills at Miami Dolphins -3.0
  Kansas City Chiefs -6.5 at New York Jets
  Green Bay Packers at Chicago Bears +2.5
  ```

  The number may sit on either team; `PK` and `EVEN` are accepted. Lines that
  don't parse are reported, and an unrecognized team name aborts the ingest —
  on the CBS sheet every line is a game you must pick, so it fails loud rather
  than dropping one.

```bash
uv run pickem ingest-cbs --html \
  --file /mnt/nas/Betting/pickem/weeks/week2.html \
  --sport cfb --season 2026 --week 2
```

For a whole pool week, `scripts/start-week.sh 3` starts both boards from
`/mnt/nas/Betting/pickem/weeks/week3.html`: it ingests CFB week 3 and NFL week 2,
then takes each league's first market snapshot (step 3) with an 11-day window.
A league whose ingest fails is not polled, and never blocks the other league.
`--sport`, `--season`, `--days`, `--file` and `--db` override the defaults.
Preflight and the report (step 4) remain manual.

Omit `--file` to read the pasted block from stdin. Re-ingesting a week is safe:
it will not erase scores a later `sync-results` wrote.

### 3. Poll the live market

```bash
uv run pickem poll-odds --sport cfb --season 2026 --week 2 --days 7
```

`--days` is the kickoff window ahead of now that this week occupies, and it
matters: the odds feed returns every event with posted odds across several
weeks, and the window is the only guard that can catch a repeat matchup at the
same venue in a different week. Widen it (e.g. `--days 11`) for an early poll,
but keep it wide enough to cover the *last* game of the slate — a two-day window
would silently omit the Sunday and Monday games.

`poll-odds` refuses to run before `ingest-cbs`, because it derives the slate of
games it will accept from the stored CBS league lines.

Poll again close to the first kickoff. Each poll appends history rather than
overwriting; the line movement *is* the signal.

### 4. Preflight, then exactly one report

```bash
uv run pickem preflight \
  --sport cfb --season 2026 --week 2 --expected-games 15 \
  --max-age-minutes 60 --min-books 3 \
&& uv run pickem report \
  --sport cfb --season 2026 --week 2 \
  --out /mnt/nas/Betting/pickem/weeks/week2-cfb-v1-picks.md
```

`preflight` is read-only. It checks the exact game and league-line counts, that
kickoffs are still in the future, live market coverage, snapshot freshness, the
number of distinct books behind each game's latest snapshot, and that completed
history exists for the Elo tiebreak. It must print `Overall: READY`;
`NOT READY` is a stop gate, not a warning. It exits nonzero when not ready,
which is what the `&&` above relies on.

`report` prints the ranked sheet and, with `--out`, writes it to markdown. Each
row carries the pick, tier, divergence, both spreads, and the rationale, plus a
provenance header and the market snapshot's age.

If CBS changes the slate or a line before you submit, don't overwrite version 1:
re-ingest, re-poll, re-preflight, and write `...-v2-picks.md`, marking v1
superseded in your notes. Keep both artifacts and both recorded pick batches.

### 5. Submit by hand

Read the sheet, enter the sides in CBS, submit the tiebreaker total, and record
the total and the submission time in your notes alongside the report. Nothing
here does that for you, by design.

### 6. Sync results after the week

```bash
uv run pickem sync-results --sport cfb --season 2026 --week 2
```

This pulls final scores in, which trains the Elo tiebreak for `no_market` games
and lets `preflight` pass next week. `--week` is required for CFB (fetched a
week at a time) and optional for NFL. It fetches the whole league week, which
will contain more games than your CBS slate — that count mismatch is expected.

### 7. Import the week's results

After every game in the pool week is final, open CBS **Standings → Weekly**,
pick the week, let the page finish loading, and save it (Save Page As →
"Webpage, Complete" or "HTML only") as
`/mnt/nas/Betting/pickem/results/week<N>.html`, where
N is the **pool** week. Then:

```bash
uv run pickem import-results --season 2026 --pool-week 2
```

It links every game to the stored boards, checks CBS's green and red marks
against the scores and lines, and refuses the whole page if anything disagrees.
It then writes `/mnt/nas/Betting/pickem/results/week2-report.md` and DMs a
summary. Use
`--no-notify` to skip the DM. If the DM fails, the command exits 2 but keeps the
import and the report; `uv run pickem results-report --season 2026 --notify`
resends it.

Each import also writes a visual dashboard page, served privately to the
owner's devices over Tailscale at `https://sandbox.tail750bff.ts.net/pickem/`.
Setup, rebuilding and troubleshooting are in `docs/runbooks/dashboard.md`.

The report grades four things side by side: your submitted picks, the model's
last recommendation before each kickoff, the field's consensus, and simple
baselines (favorites, home teams, the closing market). A comparison is called a
difference only once its 95% intervals separate; until then it says "not
distinguishable yet".

An unknown CBS abbreviation stops the import with the name to add to
`src/pickem/resolve/aliases.yaml`.

One-off, for weeks played before recommendation history existed:

```bash
uv run pickem backfill-recommendations --season 2026
```

### Stop conditions

Stop and investigate rather than working around any of these:

- CBS parse skips, unresolved team names, or a game count other than
  `--expected-games`.
- Odds API quota exhaustion, an unavailable feed, or fewer than `--min-books`
  books.
- A stale market snapshot. Cached stale odds are not a ready report.
- No completed history — Elo is untrained and `report` will say so.

Never hand-edit the database or guess an alias to get past one of these.

---

## Command reference

Run `uv run pickem <command> --help` for the full option list. All commands take
`--db` (default `data/pickem.duckdb`).

**Weekly**

| Command | Purpose |
|---|---|
| `ingest-cbs` | Parse the CBS sheet (`--html` for a saved page, stdin or `--file` for pasted text) into frozen league lines |
| `poll-odds` | Append a live market snapshot for the week (`--days` kickoff window) |
| `preflight` | Read-only readiness check (`--expected-games`, `--max-age-minutes`, `--min-books`) |
| `report` | Render and record the ranked pick sheet (`--out` to save markdown) |
| `sync-results` | Pull final scores into the store |

**Historical / analysis** — these are one-time or occasional; you do not need
them for a normal week.

| Command | Purpose |
|---|---|
| `backfill` | Load NFL schedules and closing lines from nflverse (free) |
| `backfill-cfb` | Load historical CFB results from CFBD — this is what trains the Elo tiebreak (`--weeks` per season) |
| `backfill-history` | Buy both backtest proxies from the Odds API archive. **Costs credits.** Dry-run by default; `--execute` also requires `--expected-credits`, and `--max-credits` / `--max-new-requests` bound the spend |
| `backtest` | Replay stored history through the live edge code |
| `calibrate` | Measure how closely the frozen CBS line tracks the market behind it |
| `evaluate-coinflip` | Frozen CFB Candidate 1 experiment (result recorded; NULL) |
| `evaluate-coinflip-residual` | Frozen CFB Candidate 2 experiment (result recorded; NULL) |

`backfill-history` is the only command that can spend real money. It plans and
prints the cost before doing anything, and refuses to execute unless the number
you pass matches the plan.

---

## Optional: the Discord reminder bot

An owner-only bot that DMs you a Tuesday reminder, refreshes recommendations on
the other days, polls the market again as each kickoff approaches, and answers
`/status` and `/refresh` in DM. It never submits picks either.

```bash
uv run pickem-discord-bot
```

Schedules, timezone, and database path live in `config/discord-bot.yaml`; the
token and owner ID come from `.env`. The bot discovers each sport's current
stored pick week automatically, so no weekly config edit is needed. Both
commands accept optional `season`/`week` arguments and a `details: True` flag
that appends each pick's rationale.

Besides the daily refresh it polls at `12, 6, 2, 1` hours before **each
kickoff on the board** — a fixed daily time is hours stale for a night game,
and the market keeps moving. One poll covers every game in the sport, so this
costs about 60 polls per pool week, at 2 credits each (the US books, then
Pinnacle alone). It DMs you only when a pick's side flips or a game is added;
a tier-only move such as lean to strong stays quiet. See `docs/runbooks/discord-pick-reminder.md` for the
offsets, the cost model, and what happens on a restart.

To run it as a user systemd service, `deploy/pickem-discord-bot.service` is
ready to copy into `~/.config/systemd/user/` — it assumes the checkout is at
`/home/jmiller/cfb`, so update the unit if yours differs. Full setup, including
creating the Discord application with the right install context, is in
`docs/runbooks/discord-pick-reminder.md`.

## Logs

Three correlated files under `~/LOGS/pickem` (override with `PICKEM_LOG_DIR`),
all rotated daily and compressed:

| File | Level / retention | Use |
|---|---|---|
| `pickem.log` | INFO, 30 days | Human-readable decisions and run boundaries |
| `pickem.jsonl` | DEBUG, 90 days | One JSON object per record, for `jq` and DuckDB |
| `errors.log` | ERROR, 180 days | Failures with redacted tracebacks |

API keys and the Discord token are redacted wherever they appear, including
inside URLs and tracebacks. Every run gets a `run_id` that ties its records
together. `docs/runbooks/verifying-from-logs.md` has the standard queries for
confirming an unattended run did what it should.

## Development

```bash
uv run pytest -q          # full suite
uv run pytest tests/test_end_to_end.py
uv run ruff check .
uv run ruff format .
```

Line length is 100; lint rules are `E, F, I, UP, B`. Tests live in `tests/`,
source under `src/pickem/`, with `pythonpath = ["src"]` configured for pytest.

Read `docs/invariants.md` before changing behavior. It records the load-bearing
conventions and, more usefully, several obvious-looking improvements that were
tried and are known to be wrong.

## Where things live

```
src/pickem/
  cli.py            Typer command surface — wiring only, no strategy
  models.py         Sport/Side/Tier, make_game_id, Game, LeagueLine, MarketLine, Edge
  config.py         .env loading and required-key policy
  discord_bot.py    Owner-only Discord service
  ingest/           cbs.py, cbs_html.py, odds.py, nflverse.py, cfbd_source.py
  resolve/          Team name -> canonical id; aliases.yaml (32 NFL, 136 FBS)
  edge/             divergence.py, favorite.py, elo.py, pipeline.py — pure, no I/O
  report/sheet.py   Markdown pick sheet rendering
  store/            DuckDB schema and safe reads/writes
  backtest/         Replay, calibration, archive purchasing policy
  operations/       preflight.py, recommendations.py
  obs/log.py        Logging configuration and redaction
```

Further documentation, by the question you arrived with:

| Question | Document |
|---|---|
| Can I change this? | `docs/invariants.md` |
| What do we actually know? | `docs/results.md` |
| Where does this live? | `docs/system-map.md` |
| What data do we have? | `docs/data-inventory.md` |
| How did we get here? | `docs/deployment-history.md` |
| How do I run a week? | `docs/runbooks/week1-operations.md` |
| Did the scheduled job run? | `docs/runbooks/verifying-from-logs.md` |
| Where do I pick up? | `docs/HANDOFF.md` |
