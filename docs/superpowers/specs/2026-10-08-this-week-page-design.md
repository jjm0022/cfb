# "This Week" Picks Page

## Purpose

Give the owner one page that lays out the current week's matchups and the
model's picks. Clicking a matchup shows the details, including a chart of how
the spread moved through the week. The Discord messages stay as they are; the
page is where the detail lives.

Success: from a link in Discord, on a phone, the owner can see every pick on
both boards, tap any game to see why the model picked it and how the line got
there, and trust that the page reflects the latest refresh.

## Decisions already made

- The page is a new **"This week"** tab in the existing results dashboard
  (`docs/runbooks/dashboard.md`), not a separate page. It shares the
  dashboard's address, hosting (Tailscale, owner's devices only), detail panel
  and chart code.
- The Discord messages do not change, except for one link line at the end of
  the status message and the "Recommendations Updated" message.
- Team logos are downloaded once and served from the dashboard folder. The
  page never fetches anything from outside.

## What the tab shows

### The list

Two sections, CFB board and NFL board, each in kickoff order. The week is
the latest pool week with a CBS board for either league (pool week N = CFB
week N + NFL week N-1).

Each row:

- Kickoff time, and **Away @ Home** with both logos.
- The CBS line (the spread the pool grades against).
- The pick, with the same tier badge as Discord: Strong, Lean, Slight,
  Coinflip, No market.
- A "locked" mark once the game has kicked off. Once a game is final, the
  score and whether the pick won.

### The detail panel (on click)

- Header with both logos, kickoff, and league.
- **Lines now:** the CBS line, the US books' consensus (the median of their
  spreads, computed exactly as the model does), Pinnacle's spread, and the
  gap in points between the CBS line and the consensus.
- **The pick and why:** side, tier, and the model's one-line rationale.
- **Key number note:** only for Slight picks whose gap crosses 3 or 7.
- **How the pick changed this week:** the existing timeline (time, side,
  tier, edge, with "side changed" / "tier changed" badges).
- **Spread chart:** the existing line-movement chart (US consensus and
  Pinnacle over time, dashed CBS line), plus a marker at each point where the
  model's side or tier changed.

### Which pick is shown

- Game not yet started: the model's current pick, computed from the stored
  lines the same way the bot's refresh computes it.
- Game kicked off: the last pick recorded before kickoff in the
  recommendation history, since that is the one that counts. Its rationale
  comes from the same computation restricted to lines captured before
  kickoff. If the model never covered the game, the row says "No model".

## Logos

- A new one-time CLI command downloads logos into
  `<dashboard dir>/logos/<sport>/<TEAM>.png`, plus `<TEAM>-dark.png` for
  college teams.
  - College: the college football data service's team list (FBS, via the
    existing API key). Each team is matched to our team id through the
    existing alias table. Uses the 64-pixel size and its dark-mode version.
  - NFL: ESPN's logo address by lowercase abbreviation. Our ids match ESPN's
    except `WAS`, which is `wsh`.
- Re-running fills in missing logos and leaves existing ones alone. A team
  with no match (e.g. an FCS opponent) is reported and skipped.
- The page builder checks which logo files exist and tells the page. Teams
  without one get their abbreviation in a plain badge, never a broken image.
- Logos also appear in the existing tabs wherever a matchup is named.
- The runbook gets a "Download logos" section.

## When the page is rebuilt

All rebuilds write the same complete page (season results through the latest
imported week, plus "This week") through one shared routine, written
atomically as today. Whichever runs last leaves a full, correct page.

- **The bot**, after every refresh: the daily 10:00 refresh, each kickoff
  poll, and `/refresh`. It rebuilds whether or not a pick changed, because the
  lines still moved. The rebuild runs off the bot's event loop.
- **The week-start job** (`scripts/start-week.sh`), after fetching the CBS
  board and the first odds poll, through a new CLI command that rebuilds the
  page.
- **The Tuesday results job**, as today.

The page shows "Updated <time>" at the top.

The page builds even when no week has been imported yet (start of season):
the results tabs show as empty and "This week" still works.

## The Discord link

When `PICKEM_DASHBOARD_URL` is set, the status embed and the "Recommendations
Updated" embed end with a "📊 Open this week's picks" link to
`<PICKEM_DASHBOARD_URL>?v=<unix seconds>#tab=thisweek`. The `v` stamp makes
each link new to the phone's browser, so it fetches the latest page instead of
reusing a cached one. When it is not set, the messages are unchanged. Nothing else in either message changes. Other DMs are unchanged.

## When a rebuild fails

- A failed rebuild never blocks or delays a Discord message. The link then
  points at the last good page.
- It is logged with the existing `dashboard_write_failed` event, with the
  trigger (`bot`, `week-start`, `results`) in the record. There is no DM, to
  avoid repeated pings across the week's many refreshes. The "Updated" time
  shows staleness.
- A failure in the week-start rebuild does not fail the week-start job.
- A failed logo download for one team does not stop the others.

## Testing

TDD for the logic:

- **This week's data:** which week is chosen; pick and tier for pending
  games; the at-kickoff pick for locked games; rationale; line history;
  pick-change markers; final score and result; which logos are available;
  a week with only one board; no imported results weeks.
- **Logo download:** CFB matching through aliases, the `WAS` exception,
  skipping existing files, reporting unmatched teams. The HTTP calls are faked.
- **Discord link:** present at the end of both embeds when the URL is set,
  absent when it is not, and no other change to either embed.
- **Rebuild wiring:** the bot rebuilds after a refresh, and a rebuild failure
  is logged without stopping the notification.
- **Front end:** the existing JS test setup (`tests/js`) covers the new tab's
  rows, the panel's sections, the chart markers, the logo fallback, and
  `#tab=thisweek` routing.

Visual styling gets a render check, not new tests: build the real page from
the current week's data and look at it at phone width, in light and dark
mode.

## Out of scope

- Changing the Discord messages beyond the link.
- Comparing the owner's entered CBS picks against the model on the page (the
  pick-check DM already does this).
- Individual sportsbooks on the chart (the chart shows the consensus and
  Pinnacle only).
- Making the page reachable outside the tailnet.
