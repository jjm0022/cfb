# Results dashboard

The Tuesday results job writes one page per pool week to the dashboard
directory (`$PICKEM_DASHBOARD_DIR`, default `~/.local/share/pickem/dashboard`):
`week-N.html` for each week and `index.html` for the latest. Tailscale serves
that directory to the owner's own devices only.

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

## Link the page from the Tuesday DM

Add to `.env`:

```
PICKEM_DASHBOARD_URL=https://sandbox.tail750bff.ts.net/pickem/
```

Without it the DM is sent as before, with no link.

## Rebuild a page

```
uv run pickem results-report --season 2026 --pool-week N
```

This rewrites `week-N.html` (and the Markdown), and `index.html` only when N
is the latest imported week. Use it after a `dashboard not written` failure:
the Tuesday job exits 3 in that case and DMs the owner the exact command to
run, and its Wednesday retry does not redo an imported week (the week is
already imported, so `pending-results-week` skips it).

`--dashboard-dir` writes elsewhere for a one-off, but the DM link always
points at `PICKEM_DASHBOARD_URL`, which serves the default directory.

## Turn serving off

```
sudo tailscale serve --set-path /pickem off
```

## When the page does not load

- This machine is off or asleep, or Tailscale is disconnected on it or on the
  viewing device (`tailscale status` on each).
- `tailscale serve status` shows no `/pickem` entry: turn serving on again.
- The page is stale: check the results job's log for `dashboard_write_failed`,
  then rebuild the page.

The DM and the Markdown report on the NAS stay available either way.
