# Results dashboard

The pick'em jobs keep one interactive page, `index.html`, in the dashboard
directory (`$PICKEM_DASHBOARD_DIR`, default
`~/.local/share/pickem/dashboard`). It covers the season through the latest
imported week, and has a week picker for earlier weeks. It also has a "This
week" tab showing the current week's boards: each game's line, the model's
pick and why, and how the pick has moved. The Tuesday results job also writes
a small `week-N.html` for every imported week; each one only forwards to
`index.html#week=N`, so links in older DMs still land on the right week.
Tailscale serves that directory to the owner's own devices only.

Three things rebuild the page, so it stays current without anyone running
anything:

- The bot rebuilds it after every refresh of the picks.
- The week-start job rebuilds it after loading a new board.
- The Tuesday results job rebuilds it, as before, once the results are in.

The page shows when it was last updated at the top, so you can tell how old
what you are looking at is.

The page is self-contained: its data, styles and scripts are all inside
`index.html`, and it makes no outside requests. It needs JavaScript. Team
logos are the one exception to "inside the file": they are image files in a
`logos/` folder beside the page, served from the same directory, so the page
still never reaches outside it.

Address: `https://sandbox.tail750bff.ts.net/pickem/`

## Add a device

Install Tailscale on it and sign in with the same account as this machine.
Nothing else is needed; the page is not reachable from devices outside the
tailnet.

## Turn serving on (once)

```
sudo tailscale serve --bg --set-path /pickem /home/jmiller/.local/share/pickem/dashboard
tailscale serve status
```

Serving a directory needs root even for the operator user (`tailscale set
--operator` is not enough), and `tailscaled` resolves the path itself, so give
it absolute.

Tailscale keeps this across reboots. Never use `tailscale funnel` for this
directory: Funnel publishes to the internet, and the page carries every
entrant's results.

## Download logos (once)

```
uv run pickem fetch-logos
```

This saves college logos (including dark-mode versions, which the page swaps
in when the device is in dark mode) from the college football data service,
and NFL logos from ESPN, into `$PICKEM_DASHBOARD_DIR/logos/`. Running it
again fills in any logos that are missing and keeps the ones already saved.

It ends by listing the college teams it found no logo for. Those teams show a
plain placeholder instead (a blank space beside the name in lists, a plain
badge with the team's name in a game's detail panel), so nothing breaks.
Rebuild the page afterwards (next section) so it starts using the new files.

## Link the page from Discord

Add to `.env`:

```
PICKEM_DASHBOARD_URL=https://sandbox.tail750bff.ts.net/pickem/
```

With it, the Tuesday results DM links to that week's results, and the bot adds
a "📊 Open this week's picks" link to the end of its status and
"Recommendations Updated" messages. The bot's link opens the "This week" tab.
Without it, those messages are sent as before, with no link.

Restart the bot after changing `.env`
(`systemctl --user restart pickem-discord-bot.service`); it only reads the
file when it starts.

## Rebuild a page

To rebuild the page from whatever is stored, without touching any weekly
report:

```
uv run pickem publish-dashboard
```

This is the quick one: use it after downloading logos, after changing the
page's styling, or whenever the page looks stale.

To also rewrite a week's Markdown report:

```
uv run pickem results-report --season 2026 --pool-week N
```

This rewrites week N's Markdown and always rebuilds the whole page
(`index.html` and every `week-N.html` forwarder) from everything imported,
whichever week N is. Use it after a `dashboard not written` failure:
the Tuesday job exits 3 in that case and DMs the owner the exact command to
run, and its Wednesday retry does not redo an imported week (the week is
already imported, so `pending-results-week` skips it).

`--dashboard-dir` writes elsewhere for a one-off (both commands accept it),
but the Discord links always point at `PICKEM_DASHBOARD_URL`, which serves the
default directory.

## Turn serving off

```
sudo tailscale serve --set-path /pickem off
```

## When the page does not load, or is stale

- This machine is off or asleep, or Tailscale is disconnected on it or on the
  viewing device (`tailscale status` on each).
- `tailscale serve status` shows no `/pickem` entry: turn serving on again.
- The page is stale: the "Updated" time at the top shows how old it is. A
  failed rebuild is logged as `dashboard_write_failed`, with a `trigger` that
  says which job tried: `bot`, `week-start`, `results` or `manual`. Check that
  job's log (see `docs/runbooks/verifying-from-logs.md`), then rebuild the
  page.
- Logos are missing (plain placeholders everywhere): run the logo download
  above, then rebuild the page.

The DM and the Markdown report on the NAS stay available either way.
