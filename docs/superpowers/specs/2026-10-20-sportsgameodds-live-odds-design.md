# Live Odds from SportsGameOdds

## Purpose

Get the live market spreads that every pick depends on from SportsGameOdds
(SGO) instead of The Odds API, whose key was deactivated when the paid plan
was cancelled on 2026-10-16. Pool week 8 (CFB week 8, NFL week 7) currently has
no market lines at all; its first kickoff is Thursday 2026-10-22 20:15 ET.

Success: picks, the dashboard, the change DMs and the pick check work as they
did before, from SGO lines, and pick'em uses no more than its share of the SGO
free plan.

Props already pulls SGO with the same account (as a side-by-side test of
player props, `props/src/props/vendors/sportsgameodds.py`). Pick'em gets its own
adapter for game spreads; nothing is shared in code.

## The constraint that shapes the design: SGO bills per game

The Odds API billed per request, and one request returned every game, so the
poll schedule was built on "cost scales with instants, not games"
(`automation/poll_plan.py`, `config/discord-bot.yaml`). SGO's free Amateur plan
bills **one object per event returned**, 2,500 objects per rolling 30 days,
for the whole account. Props uses the same account and estimates its own need
at 1,500–3,400.

Measured from stored polls, today's schedule pulls 600–700 games a week
(about 2,600–3,000 a month) — more than the whole account on its own. So the
schedule changes as well as the source.

**Pick'em's share: 800 games per 30 days.** Planned use, about 745:

| Pull | Games asked for | Games / month |
|---|---|---|
| Tuesday week start (`start-week.sh --auto`) | every game on both boards (~29) | ~125 |
| Thursday 10:00 daily refresh | every game not yet started | ~125 |
| Kickoff polls at 12, 6, 2 and 1 hours before | **only the games kicking off then** | ~495 |
| Manual `/refresh` | every game not yet started | 15–30 each |

The daily refresh keeps running Wednesday–Monday at 10:00, but pulls odds only
on Thursday. On the other days it regenerates picks from stored lines and
rebuilds the dashboard.

## The SGO adapter

New module `ingest/sgo.py`, a client used the same way `OddsClient` is used
for live polls today.

**Request.** `GET https://api.sportsgameodds.com/v2/events` with header
`x-api-key` (the key never appears in a URL):

- `leagueID`: `NFL` or `NCAAF`
- `teamID`: the SGO ids of both teams of every requested game, comma-separated
- `startsAfter` / `startsBefore`: earliest and latest requested kickoff, ±6 hours
  (free: the team filter bills only our games; catches a start time that
  differs from the board's by a TV slot)
- `oddID`: `points-home-game-sp-home` (payload size only; billing is per event)
- `limit=100`, paging through `nextCursor`

Filtering by team is what keeps the bill to our games: a time-range-only query
is billed for every college game in that slot (props' saved test pulls returned
122 different teams in 12 pulls, most of them FCS or lower).

**Parsing.** Each SGO event is converted to the shape the existing feed parser
reads (`home_team`, `away_team`, `commence_time`, and per book a `spreads`
market with the home team's `point`), then passed through the shared
`_parse_events`. All guards therefore stay identical: kickoff window, slate,
unknown team reported not raised, neutral-site reorientation, per-book skips
listed. SGO specifics handled before that:

- Home spread = the book's `spread` on `points-home-game-sp-home`. Already
  home-perspective (home getting 16.5 is `+16.5`), so no sign flip. Pinned by a
  test on a real saved response.
- A book with `available: false` is skipped and listed ("not open for
  betting").
- Kickoff = `status.startsAt`; team names = `teams.home/away.names.long`.
- Book keys are kept as SGO sends them (`draftkings`, `caesars`, ...).
- `captured_at` is the poll time, as today.

**Requested games with no event.** Each requested game that comes back without
an event is listed in `skipped` ("<game id>: no SportsGameOdds lines (team ids X, Y)") and so shows as `NO_MARKET`, exactly like a game a feed had no line
for today. A wrong team id costs nothing: it matches no event.

**Team ids.** SGO ids look like `ALABAMA_NCAAF` and `KANSAS_CITY_CHIEFS_NFL`.
Rule: take the team's first spelling in `aliases.yaml`, fold accents to ASCII,
drop apostrophes, replace every other run of non-alphanumerics with `_`,
uppercase, and append `_NCAAF` or `_NFL`. A small override table (in the same
module) covers exceptions. Checked offline against props' saved responses:
the rule reproduces 81 of the 82 FBS ids seen there (Hawai'i included, because
apostrophes are dropped); the one miss is Miami (FL), which SGO calls
`MIAMI_NCAAF`, so it starts as the first override. Unverified ids (55 FBS
teams, all 32 NFL) show up at the first pull that includes them, as a "no
SportsGameOdds lines" skip, days before kickoff; the fix is an override entry.

**Errors.** Transport errors and 5xx retry with the existing policy. 401/403
raise a new `KeyRejected(OddsApiError)` whose message says the key was
rejected (today a 401 is reported as "quota exhausted", which misled the
2026-10-20 check). 429 raises `QuotaExhausted`. Other 4xx raise
`OddsApiError`. `SPORTSGAMEODDS_API_KEY` joins the redacted environment
variables in `obs/log.py`.

**Source label.** Rows are stored as `source = "sportsgameodds"`. The readers
that today test `source == LIVE_SOURCE` (results closing line, line history,
preflight) test membership in `LIVE_SOURCES = {"oddsapi", "sportsgameodds"}`,
so past weeks keep rendering. The backtest's `split_proxies` catch-all must
report SGO rows as ungraded, like any other in-season poll; a test pins that.

## Which games each pull asks for

`poll_odds_snapshot` gains two arguments:

- `kickoffs`: for a kickoff poll, the kickoffs it covers. Requested games are
  the slate games whose kickoff is still in the future, inside the window, and
  (when `kickoffs` is given) at one of those kickoffs. Started games are never
  requested (they would return in-play odds and cost objects).
- `trigger`: `week_start`, `midweek`, `kickoff`, `manual` or `cli`, plus the
  kickoff offset for kickoff polls. Used for the budget and the logs.

Kickoff polls ask only for their own games. `PollInstant` gains `kickoffs`, every
kickoff that asked for that instant (the planner already merges instants two
slots share; the merged poll must cover both slots' games). The kickoff poll
requests the slate games whose kickoff is in `instant.kickoffs`.

The daily refresh config gains `odds_days: [thu]`; on other days the monitor's
refresh calls `generate_recommendations` without polling. Change detection,
history and the dashboard rebuild are unchanged.

`pickem poll-odds` (used by `start-week.sh`) keeps its interface and polls SGO
with `trigger=cli`, or `week_start` when called from the week-start script
(new `--trigger` option, default `cli`).

## Keeping to 800

New append-only table `odds_pulls`:

| column | meaning |
|---|---|
| `pulled_at` | when |
| `sport`, `season`, `week` | scope |
| `trigger`, `offset_hours` | why (offset only for kickoff polls) |
| `games_requested` | games asked for |
| `games_returned` | events SGO returned, i.e. objects billed |
| `skipped` | true if the budget stopped it |

Pick'em's usage = `sum(games_returned)` over the last 30 days. The account's
own counter includes props, so pick'em counts its own.

Before each pull: if usage + `games_requested` > 800 and the pull is not
protected, it is **skipped**: no request, a `skipped` row, an
`odds_budget_skipped` log event, and the refresh carries on from stored lines.
A skip also DMs the owner when no other skip is recorded in the 30 days before it. **Protected:** kickoff polls at
2 and 1 hours before, because they set the picks; they always run, so in the
worst case pick'em overshoots by part of their ~250 a month rather than losing
picks.

Settings (`games_per_30_days: 800`, `protected_offsets_hours: [2, 1]`) live in
one module read by both the bot and the CLI.

Every pull logs `odds_usage` with games requested, returned, and the 30-day
total. The week-start run also reads SGO's `/account/usage` once and logs the
account-wide figure (`odds_account_usage`), for visibility only.

## Pinnacle

The SGO free plan has no Pinnacle. The Pinnacle poll is removed. Rows already
stored (2026-09-25 to 2026-10-19) stay, and so does the filter that keeps them
out of the consensus. Recorded with it: a 91-game replay found Pinnacle-only
picks no better than the US median (47–44 vs 49–42; picks differed on 4 games).

## What stays

The Odds API client stays for the historical archive and the backtest. No
backtest data or logic changes. The consensus is unchanged: median of each
book's latest spread.

## Invariant changes (to `docs/invariants.md`)

- Live odds come from SGO; poll cost scales with **games**, not instants (also
  fix the `poll_plan.py` docstring and the `discord-bot.yaml` comment).
- Pinnacle is no longer recorded (replaces "Pinnacle is recorded, never used").
- **Exception to "lines is append-only":** SGO's terms require deleting all
  data from the service within 30 days if the account ends. Rows with
  `source = "sportsgameodds"` are then deleted. Nothing else ever deletes from
  `lines`.

`CLAUDE.md` `## Jobs` alert events gain `odds_budget_skipped`. The runbook
gains a short "Odds source" section: the key's env var, the share, what a skip
DM means, how to add a team-id override.

## Rollout

1. Ship by Wednesday 2026-10-21 evening.
2. Copy props' key into `.env` as `SPORTSGAMEODDS_API_KEY`; restart the bot.
3. Poll pool week 8 by hand (CFB week 8, NFL week 7): the Wednesday retry of
   `start-week.sh --auto` exits on a week that is already started.
4. Check every game has lines; add an override for any "no SportsGameOdds
   event" skip.

During implementation, one real NFL request for a single game (1–2 objects)
is captured as a test fixture, since no NFL SGO response exists yet.

## Testing

- Adapter, on a trimmed real NCAAF response (from props' saved pulls, spread
  odds only) and the captured NFL one: home spread sign, unavailable book
  skipped, out-of-slate event skipped, neutral-site reorientation, requested
  game with no event listed.
- Team ids: every FBS id in props' saved data reproduces; Hawaiʻi; overrides.
- Budget: under and over 800, protected offsets run over it, DM once per 30
  days, rolling window.
- Kickoff poll asks only for its kickoffs' games; a merged instant asks for
  both slots'.
- Daily refresh pulls only on `odds_days`.
- 401 → key rejected message; 429 → quota.
- `split_proxies` reports SGO rows as ungraded.

## Out of scope

- Sharing code with props.
- Making the Wednesday retry re-poll a started week with no lines.
- Any change to the consensus, tiers, or backtest.
