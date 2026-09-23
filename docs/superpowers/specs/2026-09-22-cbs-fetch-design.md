# CBS Page Fetch — Design

Date: 2026-09-22
Status: approved in brainstorming; awaiting written-spec review

## Goal

Fetch the two CBS pages the pool workflow depends on — the weekly board and
the Weekly Standings — unattended on this Linux box, instead of saving them by
hand in a browser. Everything downstream of a saved page stays unchanged.

Success means: on a normal week, the board for the new pool week is on the NAS
and ingested by Tuesday 17:00 America/New_York with no manual step, last
week's standings are imported and reported by Tuesday 09:00, and any failure
reaches the owner as a Discord DM that says what to do.

## Decisions

- **Both pages.** The board (`weeks/weekN.html`) and weekly standings
  (`results/weekN.html`). In-week partial standings are deferred (see Out of
  scope) but the fetcher must not preclude them.
- **Unattended on this box**, via systemd user timers, matching the existing
  `dfs-*` and `props-*` jobs.
- **Authentication: a dedicated Chrome profile** at
  `~/.local/share/pickem/cbs-chrome`, logged in once by the owner in a headed
  window, reused headless. Launched with `--password-store=basic` so cookies do
  not depend on an unlocked GNOME keyring. No CBS password is stored. The
  owner's everyday profile cannot be used: Chrome locks a profile in use, and
  Chrome 136+ refuses remote debugging on the default profile.
- **zendriver for every fetch**, not httpx-with-cookies. One code path that
  survives bot checks; speed is irrelevant for a few pages a week.

## Spike findings (2026-09-22, throwaway)

- Logged out, both pool URLs redirect to `<pool>/join?device=desktop`, whose
  page carries no board events. Login is required.
- Headless zendriver with the dedicated profile stays logged in and is not
  bot-blocked. The raw server HTML (in-page `fetch(location.href)`) and the
  hydrated DOM both parse identically to the hand-saved pages: `week4.html`
  board (CFB 15 games, NFL 16) and `results/week3.html` standings (31 games,
  53 entrants).
- Plain httpx with the profile's cookies also returns the logged-in board. The
  likely auth cookies (`pid`, `ppid`) expire 2027-09-22.
- A week is selected with `?poolPeriodId=<id>`, where `<id>` is lowercase
  base32 of `PoolPeriod:<n>` (week 3 = `PoolPeriod:18860893`). The pool id is
  base32 of `Pool:16583158`.
- The default `standings/weekly` page shows the current period; for an
  unstarted week the standings parser correctly refuses it.
- Raw server HTML renders kickoff times in UTC (`Fri 12:15 AM`); the DOM shows
  local time (`Thu 8:15 PM`). Neither parser is affected.

## Architecture

The fetcher writes the same files the owner saves today. `ingest-cbs --html`,
`import-results`, and `start-week.sh`'s ingest and poll steps consume them
unchanged; manual saving remains a fallback.

### Module `pickem/ingest/cbs_fetch.py`

- `ChromeSession` — the only code that touches Chrome. An async context manager
  that starts zendriver headless with the dedicated profile, the Chrome binary
  at `/opt/google/chrome/chrome`, and `--password-store=basic`. `fetch(url)`
  navigates, waits for load, and returns `(final_url, raw_html)` where
  `raw_html` comes from an in-page `fetch(location.href)`. Always stops Chrome
  on exit, including on error. Tests replace it with a stub.
- Pure functions, tested against fixtures:
  - `pool_periods(html) -> dict[int, str]` — pool week → `poolPeriodId`, read
    from the page's Apollo payload. Never computed from `PoolPeriod` numbers.
    Raises `CbsParseError` on a missing or malformed period list.
  - `current_pool_week(html) -> int` — the period CBS marks current.
  - `require_logged_in(final_url, html)` — raises `CbsSessionExpired` on a
    `/join` redirect or when the pool payload is absent.
  - `require_week(html, pool_week)` — raises `CbsWeekNotReady` unless the page
    shows the requested pool week.
- `fetch_board(session, pool_week, out, force)` and
  `fetch_standings(session, pool_week, out, force)` — resolve the period id,
  build the URL, fetch, run the checks, and write atomically (temp file in the
  target directory, then rename). They refuse to overwrite an existing file
  unless `force`, and write nothing when any check fails.

### Configuration (`config.py`)

`CBS_POOL_URL` and `CBS_CHROME_PROFILE`, each overridable by environment
variable. The Chrome binary path is likewise a setting.

### CLI

`pickem fetch-cbs --page board|standings --pool-week N [--out PATH] [--force]`

`--pool-week current` (board) resolves
the week from CBS's period list, for the timer. Default `--out` is
`DEFAULT_WEEKS_DIR/weekN.html` or `DEFAULT_RESULTS_DIR/weekN.html`.

### Dependency

Add `zendriver` to `pyproject.toml`.

## Scheduling and integration

### Board — `pickem-board.timer`, Tuesday 17:00 America/New_York

Runs `scripts/start-week.sh --auto`:

1. Resolve the pool week from CBS's current period.
2. If `weeks/weekN.html` is missing, fetch it. `--refetch` passes `--force`.
3. Ingest and poll each league as today; a league whose board is not posted
   fails alone without blocking the other.
4. On success, send the pick reminder DM (see below). If only one league
   loaded, the DM names the missing league.

Idempotent: when the page exists and both leagues are already stored for that
week, it does nothing. A retry runs Wednesday 09:00. Running
`start-week.sh N` by hand keeps working and fetches the page if missing.

### Standings — `pickem-results.timer`, Tuesday 09:00 America/New_York

Runs `scripts/fetch-results.sh`: target the latest pool week that has stored
league lines but no imported results, then `fetch-cbs --page standings`, then
`import-results`, which already writes the report and DMs the summary. The
week comes from the database, not from CBS's current period, because CBS may
not have advanced its current period by 09:00 Tuesday and "the period before
current" would then name an already-imported week and silently no-op.
Idempotent: when no stored week lacks results, it does nothing. When a game is not final
the parser refuses, the job exits non-zero and DMs, and a Wednesday 09:00
retry runs.

### Tuesday reminder DM moves out of the bot

The Discord bot's 10:00 Tuesday `pick-reminder` cron job is removed. The board
job sends the same reminder text through `notify.discord_dm.send_owner_dm`
after the board is ingested, so the reminder always follows a loaded board.
The bot's Wednesday–Monday recommendation refreshes are unchanged.

## Failure handling

| Failure | Detection | Outcome |
|---|---|---|
| Session expired | `/join` redirect or no pool payload | `CbsSessionExpired`; DM with the re-login command; no retry |
| Week not posted / wrong week | `require_week` or period list lacks the week | `CbsWeekNotReady`; DM with the week and the retry time |
| Standings not final | existing `CbsParseError` | DM naming the game; Wednesday retry |
| Chrome or network error | zendriver error or 60 s per-page timeout | one in-run retry, then DM with the error |
| Profile locked by a stray Chrome | profile `SingletonLock` held | fail fast with a clear message; never hang |
| NAS offline | existing `_require_reachable` | existing message, DMed |

A file is never written unless it passed the login and week checks. Chrome is
always shut down.

### Re-login helper

`scripts/cbs-login.sh` opens the dedicated profile headed and detached on the
owner's desktop (Wayland `wayland-0`, `XDG_RUNTIME_DIR=/run/user/1000`,
`setsid`) at the pool URL. The session-expired DM names it.

### Logging

All entry points run under `run_context` with the existing loguru setup and
emit `cbs_fetch_started`, `cbs_fetch_saved` (path, bytes, pool week, period
id), and `cbs_fetch_failed` (failure type), so `~/LOGS/pickem` shows whether
each Tuesday job ran and why it failed.

## Testing

No automated test launches Chrome; zendriver is confined to `ChromeSession`.

- `tests/test_cbs_fetch.py` — `pool_periods`, `current_pool_week`,
  `require_logged_in` (join redirect, logged-out page, logged-in page),
  `require_week`; `fetch_board`/`fetch_standings` with a stub session: URL
  with `poolPeriodId`, atomic write, no overwrite without `force`, nothing
  written on any failed check, one retry on a transient error, session always
  closed. Uses new trimmed fixtures: a logged-in page's period list and a
  logged-out `/join` page.
- `tests/test_cli.py` — `fetch-cbs` exit codes and messages per failure type.
- `tests/test_start_week_script.py` — `--auto` fetches only when the page is
  missing, no-ops when both leagues are stored, isolates a failing league, and
  sends the reminder DM only on success.
- `tests/test_fetch_results_script.py` — targets the latest stored week without results, no-op when
  already imported, non-zero exit on non-final games.
- `tests/test_discord_bot.py` — `pick-reminder` is no longer scheduled.
- `tests/test_results_real_pages.py` — period and login checks accept the real
  hand-saved `week3`/`week4` pages; skipped when the NAS is absent.

Live acceptance (manual, not CI):

1. `pickem fetch-cbs --page board --pool-week 4 --out <scratch>`; parsed games
   match `weeks/week4.html`.
2. Same for standings week 3 against `results/week3.html`.
3. `systemctl --user start` each timer's service once; check the JSONL records
   and the DM.
4. Point `CBS_CHROME_PROFILE` at an empty profile; confirm the session-expired
   DM.

## Out of scope

- In-week partial standings (pulls after each kickoff window, written as
  `results/weekN-partial-<timestamp>.html`). Needs a partial-week mode in
  `cbs_results.py`; the fetcher and period mapping already support it.
- Calling CBS's GraphQL API directly.
- Automating the CBS login form or storing CBS credentials.
- Entering picks into CBS; submission stays manual.
