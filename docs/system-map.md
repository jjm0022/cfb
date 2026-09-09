# System map — where does this live?

Module-by-module map of the package, and the tests worth knowing about.

Split out of `HANDOFF.md` on 2026-09-09; content unchanged.

## What is built

```
src/pickem/models.py              Sport/Side/Tier StrEnums, make_game_id, Game,
                                  LeagueLine, MarketLine, MarketLinesResult,
                                  Edge. Also owns the market `source` labels
                                  (LIVE/FROZEN/SUBMISSION) so ingest and
                                  backtest share a vocabulary without either
                                  importing the other.
src/pickem/resolve/resolver.py    TeamResolver, UnknownTeamError (fail-loud).
                                  _normalize folds case, whitespace, diacritics
                                  and punctuation.
src/pickem/resolve/aliases.yaml   canonical team IDs -> every source's spelling.
                                  All 32 NFL teams and all 136 FBS schools.
src/pickem/store/db.py            Safe writes plus coherent week/range reads.
src/pickem/store/schema.sql       DuckDB DDL; `lines` is append-only
src/pickem/ingest/cbs.py          Complete CBS game + league-line intake records.
src/pickem/ingest/cbs_html.py     parse_cbs_html — the saved CBS page. Reads the
                                  Apollo SSR blob the page server-renders, not
                                  the DOM. Same ParseResult, plus real kickoff
                                  instants. Deduplicates: CBS emits every game
                                  in two blobs.
src/pickem/edge/divergence.py     Internal divergence measurement plus
                                  consensus and ranking helpers. Pure; no I/O.
src/pickem/edge/elo.py            EloConfig, build_ratings, projected_margin,
                                  tiebreak_side. Pure; no I/O. Decides
                                  NO_MARKET only, since 2026-09-09.
src/pickem/edge/favorite.py       favorite_side: the frozen-board favorite,
                                  HOME when the spread is <= 0. Decides
                                  COINFLIP. Pure; no I/O, no fitted state.
src/pickem/edge/pipeline.py       `decide_edges`, the only public edge-decision
                                  interface; it returns final picks and resolves
                                  only COINFLIP and NO_MARKET internally,
                                  routing the two tiers to the two rules above.
src/pickem/ingest/nflverse.py     load_nfl_games, load_nfl_closing_lines.
                                  `loader` is injected so tests stay offline.
                                  _kickoff combines gameday + gametime through
                                  US/Eastern for the true UTC instant.
src/pickem/ingest/cfbd_source.py  CfbdConfig, load_cfb_games, load_cfb_lines.
                                  `fetcher` is injected. Talks to the CFBD REST
                                  API over httpx — NOT the `cfbd` SDK, which
                                  pins pydantic<2. Retries 429 with backoff.
                                  Both loaders keep FBS-vs-FBS games only.
src/pickem/ingest/odds.py         OddsClient.fetch_spreads (live) and
                                  fetch_historical_spreads (archive, 10 credits
                                  a call). Both share _parse_events, so the two
                                  paths cannot drift. Both require a kickoff
                                  `window` and a `slate` of game ids.
                                  QuotaExhausted is distinct from feed errors.
                                  Retries transport/5xx. Context manager.
src/pickem/backtest/stats.py      Result StrEnum, ATS grade_pick, and bounded
                                  Wilson score intervals. Pure; no I/O.
src/pickem/backtest/snapshots.py  Pure planner: a stored schedule becomes the
                                  exact archive requests to make, plus their
                                  credit cost, before anything is spent.
src/pickem/backtest/archive.py    Paid archive planning/execution policy.
src/pickem/backtest/calibration.py
                                  calibrate — the check on the substitution the
                                  whole backtest rests on. Residual is the CBS
                                  line minus the market consensus at or before
                                  its posted_at; bias and dispersion are
                                  reported separately. Pure; no I/O.
src/pickem/backtest/runner.py     run_backtest replays the frozen and submission
                                  proxies through decide_edges; both ends
                                  collapse through consensus_spread.
                                  split_proxies classifies
                                  stored lines by `source`. BacktestReport
                                  carries tier records, assumptions and skip
                                  reasons. Pure; no I/O.
src/pickem/report/sheet.py        render_sheet consumes final picks from
                                  decide_edges and ranks them into auditable
                                  markdown with named picks, both spreads,
                                  visible NO_MARKET rows, required provenance,
                                  and numeric or explicitly unknown data age.
src/pickem/config.py              Default DuckDB path and environment-sourced
                                  Odds API / CFBD keys. Loads `.env` on import
                                  with override=False, so an exported variable
                                  always wins over the file.
src/pickem/resolve/matchup.py     Canonical matchup identity shared by all adapters.
src/pickem/cli.py                 Typer commands: ingest-cbs, poll-odds
                                  (--days for the kickoff window), report,
                                  sync-results (--sport nfl|cfb), backfill,
                                  backfill-history (dry run by default,
                                  --execute to spend, --max-credits ceiling),
                                  backtest, backfill-cfb, and calibrate. Prints
                                  every source skip
                                  visibly; CBS ingest is atomic.
```

Tests worth knowing about, beyond the per-module ones:

- `tests/test_end_to_end.py` — the real CLI against a real DuckDB file, faking
  only the HTTP transport. Covers paste -> poll -> sheet, out-of-week events,
  re-polls appending history, and re-ingest not destroying synced scores.
- `tests/test_game_id_contract.py` — CBS, nflverse, CFBD and the odds feed must
  all produce the SAME id for the same matchup. A one-character disagreement
  makes the join silently return nothing.
- `tests/test_cfb_aliases.py` — guards the generated FBS table offline.

## How it was built

`superpowers:subagent-driven-development`: per task, a brief to a fresh
implementer subagent, then a scoped reviewer over that task's diff, then a
ledger entry. Closed out with a whole-branch review, one fix wave, and one
scoped re-review. That process is complete — it is recorded here only so the
ledger's structure makes sense.

