# Odds source: SportsGameOdds

Live spreads come from SportsGameOdds (SGO) since 2026-10-21. The Odds API key
was deactivated when its paid plan was cancelled; its code stays only for the
historical archive the backtest reads.

## The key

`SPORTSGAMEODDS_API_KEY` in `.env`. It is the same account props uses
(`PROPS_SPORTSGAMEODDS_API_KEY` in `~/props/.env`), so the free plan's 2,500
games per rolling 30 days are shared.

## Pick'em's share: 800 games per 30 days

SGO bills one object per game returned. Pick'em asks only for its own games:

| Pull | Games | Per month |
|---|---|---|
| Tuesday week start | every game on both boards | ~125 |
| Thursday 10:00 refresh | every game not yet started | ~125 |
| 12, 6, 2 and 1 hours before each kickoff | only that kickoff's games | ~495 |
| `/refresh` | every game not yet started | 15–30 each |

The 10:00 refresh on the other days re-decides from stored lines without
pulling (`odds_days` in `config/discord-bot.yaml`).

How much has pick'em used?

    uv run python -c "from datetime import UTC, datetime, timedelta; \
    from pickem.store.db import Store; \
    s = Store('data/pickem.duckdb', read_only=True); \
    print(s.odds_games_used(datetime.now(UTC) - timedelta(days=30)))"

The account-wide figure (props included) is logged as `odds_account_usage` by
each Tuesday week start.

## "Skipped an odds pull" DM

A pull would have taken pick'em past 800. Nothing is broken: picks are
re-decided from the lines already stored, and the 2-hour and 1-hour pulls
before each kickoff still run. You hear about this once per 30 days; every skip
is logged as `odds_budget_skipped`, a `/refresh` reply says "No new odds were
pulled", and the Tuesday week-start DM says "No odds pulled" (the board still
loads, so no retry is needed). To change the share, edit
`GAMES_PER_30_DAYS` in `src/pickem/operations/odds_budget.py`.

## "no SportsGameOdds lines" for a game

The pull listed `<game id>: no SportsGameOdds lines (team ids X, Y)`. Look at
the other lines the same pull listed:

- **"X at Y: ... not a team we track"** for that game: SGO returned it, but
  under a name our resolver does not know. Add the name as an alias in
  `aliases.yaml`.
- **Nothing else about that game:** either the books have not priced it yet,
  or both team ids are wrong (a request matches an event through either
  team's id, so one wrong id alone loses nothing). Check the ids against
  SGO's (an event's `teams.home.teamID`), then add a row to
  `TEAM_ID_OVERRIDES` in `src/pickem/ingest/sgo.py`.

## CFBD backup for college games

When a college poll comes back with no SGO lines for a game it asked for, the
poll makes one CollegeFootballData (CFBD) `/lines` call for the week and stores
that game's book spreads under `source = "cfbd"`. It happened first with Utah at
Cincinnati in 2026 week 9, which SGO listed as four empty copies of the game.
NFL never uses it, and it does not count toward the SGO share. CFBD's free tier
allows about 1,000 calls a month; a week uses a handful.

- The log shows `cfbd_fallback` with `games_filled` and `games_missing`.
- `<game id>: no CFBD lines either — shows as NO_MARKET` means neither source
  had the game.
- `cfbd_fallback_failed` means the CFBD call failed (most often a missing or
  expired `CFBD_API_KEY`). The SGO lines from that poll were still stored, and
  the next poll tries again.

CFBD usually carries two or three books, so `preflight --min-books 3` can still
flag a game it filled.

## "odds key rejected"

SGO refused the key (HTTP 401 or 403). Check the account, then the key in both
`.env` files.

## If the SGO account ever ends

SGO's terms require deleting all its data within 30 days. That is the one
exception to "`lines` is append-only": delete the rows with
`source = 'sportsgameodds'` (and the `odds_pulls` table), nothing else.
