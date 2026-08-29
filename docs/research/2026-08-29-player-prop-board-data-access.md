# Player-prop board data access research

Date: 2026-08-29

## Decision

Do not build a direct PrizePicks or Underdog collector. Neither operator
publishes a supported developer API, export, or partner feed for player-pick
boards. PrizePicks prohibits robots, spiders, and other automated processes
used to monitor or copy its site or app in its
[Terms of Service](https://www.prizepicks.com/help-center/terms-of-service).
Underdog's [Fantasy Terms](https://legal.underdogsports.com/) similarly
restrict automated scraping/copying except through an approved API or client;
no public approval route or API documentation was found.

The platform board, contest type, and payout displayed to a user can change.
PrizePicks documents dynamic player-pick and payout behavior in its
[Player Picks](https://www.prizepicks.com/help-center/player-picks) and
[Potential Outcomes](https://www.prizepicks.com/help-center/potential-outcomes)
help articles. An exact recommended entry therefore must be reconciled against
the board the user can see before it is placed.

## Supported vendor candidates

| Provider | PrizePicks and Underdog support | Football coverage | Key limitation | Pilot role |
| --- | --- | --- | --- | --- |
| [Prop Professor](https://api-docs.propprofessor.com/) | Explicit support | Documents NFL and NCAAF | Exact lineup payout rules and historical-board API not documented | First trial |
| [SportsGameOdds](https://sportsgameodds.com/bookmakers/prizepicks-odds-api) | Documents both books | CFB book coverage needs live validation | Historical DFS retention and exact payout semantics are not guaranteed by the public docs | Independent fallback |
| [OpticOdds](https://developer.opticodds.com/reference/get_sportsbooks) | Documents both sportsbook identifiers | Documents NFL and NCAAF league identifiers | Historical rolling retention is documented, but exact CFB market coverage and payout semantics need trial validation | Independent fallback |
| [The Odds API](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html) | Documents both books and DFS multiplier handling | Documents NFL player props and generic NCAAF prop keys | Its documentation labels DFS odds as indicative because they can vary by selected entry; CFB book-specific coverage requires trial validation | NFL fallback |

The Odds API documents a `us_dfs` region containing PrizePicks and Underdog.
It explicitly notes that DFS odds can vary by the user’s selections, and says
that non-default multipliers are represented in alternate markets. Its
[historical event-odds documentation](https://the-odds-api.com/historical-odds-data/)
also supports player-prop snapshots from May 2023 and its API documents an
`includeMultipliers` option for DFS sites. This is useful for model validation,
but it does not replace final in-app entry confirmation.

## Pilot recommendation

Run a 4–6 week, read-only vendor trial before building the `props` project:

1. Trial Prop Professor first and retain SportsGameOdds or OpticOdds as a
   second independent feed.
2. Obtain written confirmation that the selected vendor license permits private
   storage and use of PrizePicks and Underdog data.
3. During each gameday, compare the feed with an immutable manual board
   snapshot for both platforms.
4. Measure NFL and Power 4 plus Notre Dame CFB coverage, player/name mapping,
   refresh latency, line/variant changes, multiplier/payout agreement, and
   missing/stale data rates.
5. Reject automated gameday messages unless the vendor passes the stated gates;
   manual board snapshots remain the reconciliation source for every shadow
   entry.

## Conclusion

There is a no-hack, supported path through licensed third-party odds vendors,
but it must be accepted only after a paid/read-only coverage trial. Direct
operator scraping is not an acceptable source. No vendor evidence reviewed
proves that its feed alone can reproduce each account's final, exact lineup
payout, so user-visible confirmation remains required.
