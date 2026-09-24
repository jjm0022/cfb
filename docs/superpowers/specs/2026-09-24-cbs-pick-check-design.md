# CBS pick check — design

**Date:** 2026-09-24
**Status:** approved in conversation, awaiting spec review

## Goal

Before each game locks on CBS, confirm that the pick the owner entered on CBS
is the model's current pick, and DM the owner when it is not. Picks are
entered by hand (see `docs/runbooks/week1-operations.md`), and the model can
change its pick after entry when the market moves, so the entered board drifts
from the model without anyone noticing.

Success: for every game, about an hour before its kickoff, the owner either
hears nothing (everything matches and the check ran) or gets one DM naming the
games to fix, or one DM saying the check could not run and which games went
unchecked.

## Decisions (from the brainstorm)

- **One final check per kickoff, not a running watch.** It runs right after the
  existing T-1h kickoff poll, which is the last point the model's pick can
  change. Recommendation-change DMs already cover earlier drift.
- **Silent when everything matches.** No all-clear message.
- **Because silence means "fine", failure is never silent.** Any run that cannot
  finish its comparison DMs the owner.
- Nothing is ever submitted to CBS. Tiebreaker answers are not checked.

## What CBS gives us (verified 2026-09-24)

The board page the tooling already fetches (`fetch-cbs --page board`) embeds
the owner's entry. The entry marked `"isMine": true` carries `picksCount`,
`maxPicksCount`, `pickStatus`, and `entryPicks`: one item per picked game with

- `cbsSlotId`, the CBS event id (the same id `parse_cbs_html` dedupes on and
  the standings import calls `cbs_event_id`), and
- `cbsItemId`, the CBS team id of the side picked, which matches the
  `teamId` on that event's home or away team.

Example from the live week-4 page: slot `50029231` (ATL @ GB), item `405`
(ATL), spread `-6.5` for GB. Before the owner has picked, `entryPicks` is `[]`,
which is why the Tuesday-saved page shows no picks. The check must fetch a
fresh page each run.

## Components

1. **Entry-pick parser** (`ingest/`, next to `cbs_html.py`). Pure function that
   takes page HTML and returns the owner's picks as
   `{cbs_event_id: picked side (HOME/AWAY)}`, plus each event's teams and
   kickoff. It resolves the side by comparing `cbsItemId` against the event's
   home and away team ids. It raises the existing parse-error type when:
   no `isMine` entry is found, a pick names a team not in its event, or a pick
   names an event not on the page.
2. **Comparison** (`operations/`). Pure function that takes the parsed entry,
   the model's current recommendation snapshot for a scope, and the set of
   games to check. Each game comes back as one of `match`, `different_side`,
   `no_pick`, or `unmatched` (a model game that cannot be linked to a CBS event
   by teams, using the same team resolution as the board ingest). It returns
   the per-game outcomes. It does no I/O.
3. **Message formatting.** Builds the mismatch DM and the failure DM from the
   outcomes. Mismatch DM lists only `different_side` and `no_pick` games, with
   the kickoff time, what CBS has (team and CBS spread), what the model says,
   and the pool link:

   > ⚠️ Pick check — 1 game kicks off at 8:15 PM
   > ATL @ GB: CBS has **ATL +6.5**, model says **GB -6.5**
   > Fix on CBS before kickoff: <pool link>

   `no_pick` reads "no pick entered on CBS".
4. **Bot wiring** (`discord_bot.py`). The kickoff poll job for an instant whose
   `offset_hours` is the smallest configured offset (1h today) runs the check
   after the poll completes. It uses the recommendation that poll just
   produced. It checks the scope's games whose kickoff equals the instant's
   `kickoff_utc`. The CBS fetch runs in the bot process through the existing
   headless-Chrome session (`open_session`), writing no file to the NAS.
   `plan_polls` never displaces a smallest-offset instant, so every kickoff
   gets exactly one check.
5. **Manual command** `pickem check-picks --season S --pool-week N`. Fetches
   the board and compares every not-yet-kicked-off game in the pool week
   against the current stored recommendations. Prints the outcomes. It does
   not DM. It uses the same parser and comparison, and it is how the feature
   is verified against the real page.

## Failure handling

Every one of these sends a failure DM naming the reason and the unchecked
games, and logs `pick_check_failed` with a `reason` field:

| Cause | DM says |
|---|---|
| `CbsSessionExpired` | CBS login expired — see `cbs-fetch.md` "Log in again" |
| `CbsProfileBusy` | login window is open — close it; the check did not run |
| Any other fetch error | CBS fetch failed (reason) |
| Parse error | couldn't read your picks from the CBS page (reason) |
| An `unmatched` game | couldn't match these games to CBS: … (others still checked) |
| The poll before it failed | model pick may be stale — check anyway, and say so in the DM |

For the last row: if the T-1h poll fails, the check still runs against the
last stored recommendation. Any mismatch DM, or a separate short DM if all
games match, notes that the model pick came from the previous poll.

A failed check is not retried. The next kickoff's check runs independently.

## Logging

Per the `logging-standards` skill, one decision event per run:

- `pick_check_completed`, with scope, kickoff, `checked`, `matched`,
  `different_side`, `no_pick`, `unmatched`, and `dm_sent`.
- `pick_check_failed`, with scope, kickoff, `reason`, and `unchecked`.

`pick_check_failed` joins the alert events list in `CLAUDE.md`. The
`verifying-from-logs.md` runbook gains a "did the pick check run?" query.

## Testing

- **Parser:** fixture trimmed from the real 2026-09-24 week-4 page, covering
  the owner's entry with picks, an entry with `entryPicks: []`, an item id on
  neither team, and a page with no `isMine` entry.
- **Comparison:** each outcome, including a game kicking off at a different
  instant (not checked) and a stale-recommendation flag.
- **Formatting:** mismatch DM, no-pick wording, failure DM per cause.
- **Wiring:** the smallest-offset poll job triggers a check with the right game
  set and a larger offset does not. It sends no DM on all-match and one DM on
  mismatch. A fetch error produces a failure DM. Fakes for the session, the
  store, and `send_dm`, following the existing bot tests.
- **Real page:** run `pickem check-picks` against the live board before
  shipping.

## Out of scope

- Checking the tiebreaker answer.
- Changing picks on CBS.
- Checks at offsets other than the smallest.
- An all-clear message.
- A Discord slash command for the check. The manual CLI command covers
  on-demand use.
