# Invariants — can I change this?

Rules that are load-bearing, and decisions already settled. Several obvious
improvements are known to be wrong and are recorded here with their reasons.
**Check an instinct to "improve the picks" against this file first.**

Split out of `HANDOFF.md` on 2026-09-09; content unchanged.

## Load-bearing conventions — do not "improve" these

- **All spreads are home-perspective.** Home favored by 3 is `-3.0`. Adapters
  normalize at ingest; nothing downstream re-checks. Per-source conventions:
  nflverse is home-favorite-POSITIVE (negate it); CFBD is home-favorite-negative
  (no flip); Odds API home `point` is already correct (no flip). Each is pinned
  by a test.
- **`make_game_id` format:** `nfl-2025-03-BUF-at-MIA` — zero-padded week,
  `-at-` separator, away before home. Every source joins on this string.
- **The `lines` table is append-only.** `INSERT OR IGNORE`, never `OR REPLACE`.
  Line movement is the signal; overwriting destroys it. Because a bad row can
  never be cleaned up, nothing writes to it without knowing the game id is
  right — hence the odds `window` and `slate`.
- **`upsert_games` never blind-replaces.** Scores COALESCE, so a source that
  does not carry them cannot erase what `sync-results` wrote. `ingest-cbs` uses
  `insert_games_if_absent`, because the CBS paste has a placeholder kickoff and
  no scores.
- **`edge/` performs no I/O.** Pure functions, which is what makes the strategy
  testable offline and replayable in the backtest.
- **Fail loud, but per source.** An unknown team in the CBS paste RAISES —
  there every line is a game we must pick. An unknown team in the odds feed is
  REPORTED in `skipped` and passed over — that feed is a firehose whose NCAAF
  coverage includes every FCS matchup with a posted line, and aborting a poll
  over a game we never pick would make live CFB unusable. Such an event cannot
  be in the slate anyway, since slate ids are built from names that already
  resolved. Both paths are visible; neither is silent. Missing market lines are
  reported as `NO_MARKET`, never skipped. Never fabricate or interpolate a line.
- **Source adapters own source-specific intake behavior.** Canonical matchup
  identity is shared, while each adapter retains its own skip/fail policy and
  spread normalization convention.
- **Team-name matching folds diacritics and punctuation.** CFBD writes
  "San José State" and "Hawai'i" where the odds feed writes "San Jose State"
  and "Hawaii". `_normalize` folds these so the alias table does not need a row
  per decoration. It cannot merge two real schools — none differ only by an
  accent. **Do NOT extend this to prefix or fuzzy matching:** "Arkansas Pine
  Bluff", "Houston Baptist", "Indiana State", "North Carolina A&T",
  "Northwestern State", "Tennessee State" and "Utah Tech" are all FCS schools
  whose names begin with an FBS school's name, and a prefix match would silently
  map picks to the wrong team. This was tried during the sweep and rejected.
- **Nothing a source could not give us is dropped in silence.** Two parallel
  result types carry this: `ParseResult.skipped` (CBS paste lines that would not
  parse) and `MarketLinesResult.skipped` (rows with no spread, teams we do not
  track, events outside the window or off the slate). Both are `list[str]` of
  human-readable one-liners. **Any new line loader returns `MarketLinesResult`,
  never a bare list.** The CLI prints both kinds: CBS skips abort ingestion;
  market-row skips are yellow warnings.
- **Every rendered pick sheet names its provenance and age.** Callers must pass
  `provenance`; absent market timing renders as `unknown/unavailable`, never as
  a missing status line.
- **`Thresholds.strong` is a display parameter, not a tuning knob.** Internal
  divergence measurement sets `side` from the sign of `delta` alone;
  `decide_edges` resolves only COINFLIP and NO_MARKET — so a STRONG and a LEAN
  edge produce the SAME pick. Moving the STRONG/LEAN boundary
  relabels games; it never changes one, and no backtest can tune it toward
  correct picks. Only `lean` moves games between the two deciders (divergence
  vs the tiebreak — the frozen-board favorite since 2026-09-09, Elo before
  that). Verified 2026-08-19 across a 56-cell sweep: every cell
  sharing a `lean` value had a byte-identical win/loss/push record.
- **`calibrate`'s cutoff is a tolerance, not a strict at-or-before.**
  `poll-odds` derives its slate from `league_lines`, so the market snapshot is
  always captured AFTER the paste it is compared against — by construction,
  never before. The first live run missed a strict cutoff by 11 seconds. The
  window admits the same-sitting poll; widening it far enough to admit the next
  day's number would readmit exactly the line movement the strategy trades on.
- **A tiebreak never overrides a real divergence signal.** The two rules may
  resolve only `COINFLIP` (frozen-board favorite) and `NO_MARKET` (Elo);
  `STRONG` and `LEAN` edges pass through unchanged.
  An edge that needs a tiebreak but has no `Game` record raises
  `MissingGameError` — it is never passed through carrying internal
  measurement's placeholder `side=HOME`.
- **The backtest and live report run the pipeline that ships.** Both consume
  `decide_edges`, with backtest history growing only after each replayed week.
  If the two ever diverge again, the backtest stops being
  evidence about the thing being shipped.
- **Both ends of the divergence collapse through `consensus_spread`.** A
  per-game dict is last-wins and would grade against whichever book sorted
  last while the other end took a median, manufacturing and erasing edges
  silently. A regression test pins this: books at -1.0/-3.0/-9.0 against a
  -3.0 submission line is a COINFLIP, not a 6-point STRONG edge.
- **Pinnacle is recorded, never used.** Each live poll also asks for Pinnacle
  alone (one more credit) and stores it as `oddsapi:pinnacle`.
  `generate_recommendations` drops that source before the consensus, so the
  market line stays the US books' median. A 2026-09-24 spike over 2021–2025
  found no US book sharper than that median; Pinnacle is being collected so
  the same comparison can include it. Using it needs that comparison first,
  and a fallback for the college games Pinnacle does not price.
- **Proxies are classified by `source`, never by book name.** Both carry real
  bookmaker keys. `oddsapi:frozen` and `oddsapi:submit` are the two proxies;
  anything else — an in-season `oddsapi` poll, an `nflverse` closer — lands in
  `split_proxies`' explicit catch-all and is reported as ungraded. That
  catch-all is what stops live polling from contaminating a replay, so never
  give it a default branch.
- **The submission snapshot leads kickoff by 15 minutes, and 5 is not enough.**
  The Odds API's `commence_time` and nflverse's kickoff disagree by -5 to +2
  minutes (measured, 2026-08-19). At a 5-minute lead the request lands on the
  real kickoff for the worst case, and the archive's at-or-earlier rule can
  then return in-play odds into a proxy that must predate the game. Verified
  after the fact: every stored `oddsapi:submit` row sits exactly 20 minutes
  before its kickoff, and none at or after.
- **"Nothing to store" is not an error.** DuckDB's `executemany` rejects an
  empty parameter list, so an unguarded writer turns a fully-skipped snapshot
  into a crash mid-backfill, after the credits for it are spent.

## Decisions and amendments — do not re-litigate

- **StrEnum, not `str, Enum`.** Human ruling; `str, Enum` trips ruff UP042 and
  contradicts the plan's own lint config. Plan amended at Tasks 2 and 11.
- **`uv_build`, not hatchling.** `uv_build` ships package data under
  `src/pickem/` automatically.
- **YAML 1.1 boolean trap in `aliases.yaml`.** Bare `NO:` (New Orleans) parses
  as boolean `False`, crashing the resolver. Any ID or alias in
  {NO, ON, OFF, YES, Y, N, TRUE, FALSE, NULL} must be quoted; the generated CFB
  block quotes everything unconditionally. A regression test guards this.
- **The `cfbd` SDK is NOT usable here.** Every published `cfbd` 5.x release
  pins `pydantic<2` and this project is built on pydantic 2; the 4.x line
  predates the current API and silently deserializes its camelCase fields to
  None, which made every team name on `/games` come back null while `/lines`
  looked fine. `cfbd_source.py` calls the REST API over httpx instead and the
  dependency was removed. Do not "restore" the SDK.
- **`pytz` is a real dependency.** duckdb imports it dynamically to read
  TIMESTAMPTZ but does not declare it. Do not prune it as unused. Reads return
  pytz UTC tzinfo, not `datetime.UTC`.
- **nflverse team abbreviations vary by era.** The Rams appear as both `LA` and
  `LAR` across seasons (likewise `OAK`/`LV`, `SD`/`LAC`, `STL`). Task 8 swept
  1999-2025 through both loaders until the resolver stopped raising, adding the
  missing spellings as aliases of existing canonical ids — never as new ids.
  Do the same when a new source arrives.
- **CBS lines carrying two numbers are skipped, not resolved.** A paste line
  with a number on both sides (a total, a stray trailing digit) matches both
  regex groups and would otherwise silently resolve to the home-side number.
- **Backtests report every excluded game** (human ruling 12a).
- **Every pick sheet carries provenance and age** (human ruling 13a).
- **CBS ingestion is atomic** (human ruling 14a): on any skipped row the CLI
  prints the offending rows, exits non-zero, and never opens the database.
- **Both backtest proxies come from the Odds API archive, never one from
  nflverse** (2026-08-19). Mixing sources across the two ends puts differing
  book composition inside the measured divergence, and divergence is the entire
  strategy. The nflverse closers stay as an independent cross-check.
- **The frozen proxy is a fixed Tuesday 14:00 UTC anchor, not a market
  "opening line."** CBS freezes early in the week, which is what is being
  imitated; a true opener is whenever each book first posted, a different and
  less relevant moment. The anchor is fixed in UTC rather than tracked against
  Eastern so re-runs are byte-identical — `captured_at` is part of the `lines`
  primary key.
- **`captured_at` for an archived row is the snapshot's own timestamp, never
  the requested one.** The archive returns the closest snapshot at or earlier
  than `date`, so stamping the request misdates rows by up to ten minutes and
  makes a re-run append near-duplicates instead of being a no-op.
- **The tier thresholds stay at 2.0/1.0** (2026-08-19). Tuning `lean` gains +9
  correct picks in-sample and +1 out-of-sample against ~15 picks of paired
  noise, and +8 of the in-sample +9 come from 2020 alone. Every walk-forward
  fold picks `lean = 0.5`, but stability of a zero-magnitude effect is not
  evidence. Do not re-run this sweep unless the tiebreak improves — see
  `docs/research/2026-08-19-threshold-tuning.md`.
- **The CBS page is read as data, not scraped.** The pick'em page
  server-renders its GraphQL result into an Apollo SSR blob, so `--html` parses
  that rather than the DOM. Its CSS classes are hashed per build
  (`mui-1m0pb6d`) and change on every CBS deploy; the JSON field names are what
  their own client consumes. The blob contains the JS literal `undefined`,
  which is not valid JSON and is rewritten outside string literals only. Every
  game appears in two blobs and is deduplicated on CBS's event id.
- **CBS's `homeTeamSpread` is already home-perspective favourite-negative**, so
  it passes through unflipped. Pinned by the one away-favourite in the slate —
  the only game in a week where a sign error is visible at all.
- **CBS writes `Boise St.` where CFBD writes `Boise State`** (2026-08-20). Added
  as aliases of the existing ids for all 30 State schools, not just the three
  that failed. Same convention as era-varying NFL abbreviations.
- **Franchise renames are aliases, exactly like era-varying abbreviations.**
  "Washington Football Team" (2020-2021) was missing and silently dropped that
  team's games, because the odds feed reports unknown teams rather than
  raising. Whenever the archive range spans a rename, the old names are live
  data.

## The odds poll has two guards, and both are needed

This was the Critical finding of the final review and is the most dangerous
part of the system, because its failure mode is silent and permanent.

The endpoint returns every event with posted odds, spanning several weeks, and
every one was being stamped with the caller's week — writing wrong game ids
permanently into the append-only `lines` table while the real week reported
`NO_MARKET`. `fetch_spreads` now requires:

1. a kickoff **`window`**, checked against `commence_time` **before any team
   name is resolved**. It catches out-of-week events and spares us resolving the
   hundreds of schools we do not track. It is also the ONLY guard that can catch
   a repeat matchup at the same site in another week, because `make_game_id`
   embeds the week being *asserted*, not the week observed.
2. a **`slate`** of canonical game ids, checked after resolution. It catches
   in-window events that are simply not on our sheet.

`poll-odds` derives the slate from `load_week(...).league_lines`, takes `--days`
(default 7) for the window, and refuses to run before `ingest-cbs`.

