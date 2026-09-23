# CBS page fetch

The board and Weekly Standings pages are fetched by headless Chrome using a
dedicated, logged-in profile at `~/.local/share/pickem/cbs-chrome`, and saved
where they used to be saved by hand: `weeks/weekN.html` and
`results/weekN.html` on the NAS.

## Schedule

| Timer | When (America/New_York) | Runs |
|---|---|---|
| `pickem-results.timer` | Tue 09:00, retry Wed 09:00 | `scripts/fetch-results.sh` — fetch the finished week's standings, `import-results` (report + DM) |
| `pickem-board.timer` | Tue 17:00, retry Wed 09:00 | `scripts/start-week.sh --auto` — fetch the current board, ingest, poll, DM the pick reminder |

Both are no-ops once their week is done, so the retries are harmless.

## Install or update

    cp deploy/pickem-board.* deploy/pickem-results.* ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now pickem-board.timer pickem-results.timer
    systemctl --user restart pickem-discord-bot.service   # drops the old 10:00 reminder
    systemctl --user list-timers 'pickem-*'

## Log in again (session-expired DM)

1. On the desktop, run `scripts/cbs-login.sh`.
2. Sign in to CBS in the window it opens; decline saving the password.
3. Close the window (the fetch cannot use the profile while it is open).
4. Re-run the failed job: `systemctl --user start pickem-board.service` or
   `pickem-results.service`.

## Manual use

- `uv run pickem fetch-cbs --page board --pool-week N` — save a board page.
- `uv run pickem fetch-cbs --page standings --pool-week N` — save standings
  (refused until every game is final).
- `--force` replaces a saved page; `scripts/start-week.sh N --refetch` does the
  same for the board.
- Saving a page by hand still works: a page already at the default path is used
  and never overwritten.

## Failures

| DM says | Meaning | Do |
|---|---|---|
| login has expired / join page | CBS session gone | Log in again (above) |
| profile is open in another Chrome | login window left open | Close it; re-run the job |
| has not posted pool week N / still shows pool week N | CBS not ready | Nothing; Wednesday retry |
| status is ..., not final | late or postponed game | Nothing; Wednesday retry |
| CBS fetch failed (…) | Chrome or network | Check `~/LOGS/pickem` for `cbs_fetch_failed`; re-run |

Logs: `event` values `cbs_fetch_started`, `cbs_fetch_saved`, `cbs_fetch_retry`,
`cbs_fetch_failed` in `~/LOGS/pickem` (see `verifying-from-logs.md`).
