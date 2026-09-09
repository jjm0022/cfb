# Data inventory — what data do we have?

Database contents, the paid archive acquisition, and the sweeps that verified
both against live sources.

Split out of `HANDOFF.md` on 2026-09-09; content unchanged.

## Data state (2026-08-26)

`data/pickem.duckdb` is gitignored and exists only on this machine. It now
contains both the earlier 9,390-credit NFL archive and the complete 10,180-credit
CFB acquisition described below. The database SHA-256 after the CFB run is
`5d692c92262267501920e4f9597e9cf4d1d7a3e78e5d93a809d2077d3468f31d`.

A recoverable pre-CFB backup exists at
`/Users/jmiller/Dropbox/Personal/Betting/cfb/data/backups/pickem-2026-08-25-pre-cfb.duckdb`.
Its SHA-256 is
`a4bcbd71e64cd5af14ad8c7b7db6a05f731f5c1228d84ad376a3f0635866250a`.

| table/source | rows | note |
|---|---:|---|
| `games` | 6,166 | NFL 2020-2025 (1,693) + CFB 2020-2025 and 2026 week 1 (4,473; 4,457 scored) |
| `archive_requests` | 1,018 | completed CFB 2021-2025 request ledger; all 1,018 planned requests complete |
| `lines` | 149,690 | all stored sources |
| `lines` / `oddsapi:frozen` | 71,300 | NFL plus CFB early-week proxy |
| `lines` / `oddsapi:submit` | 76,550 | NFL plus CFB pre-kickoff proxy |
| `lines` / `nflverse` | 1,693 | closing lines, kept as a cross-check only |
| `lines` / `oddsapi` | 147 | live poll, CFB 2026 week 1 |
| `league_lines` | 15 | CFB 2026 week 1, from the saved CBS page (2026-08-20) |
| `picks` | 45 | three stored CFB 2026 week 1 prospective pick-audit batches; the last was appended by the 2026-08-26 Task 12 report render |

### CFB archive acquisition

The final dry plan reported exactly `1018 planned, 1018 complete, 0 pending =
0 credits`. The paid operation completed 1,018 historical requests at 10
credits each: **10,180 credits total**. That was the 10-credit probe plus the
remaining seasonal batches of 1,980, 2,020, 1,990, 2,060, and 2,120 credits.
The request ledger's `line_count` total is 101,262.

| season | frozen requests | frozen rows | submit requests | submit rows | total requests | total rows |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 15 | 10,900 | 184 | 11,047 | 199 | 21,947 |
| 2022 | 15 | 13,405 | 187 | 14,324 | 202 | 27,729 |
| 2023 | 15 | 10,366 | 184 | 11,275 | 199 | 21,641 |
| 2024 | 15 | 6,655 | 191 | 7,171 | 206 | 13,826 |
| 2025 | 15 | 7,623 | 197 | 8,496 | 212 | 16,119 |
| **Total** | **75** | **48,949** | **943** | **52,313** | **1,018** | **101,262** |

Stored proxy coverage is nonzero for both sources in every season:

| season | source | rows | games | distinct books |
|---:|---|---:|---:|---:|
| 2021 | `oddsapi:frozen` | 10,900 | 731 | 18 |
| 2021 | `oddsapi:submit` | 11,047 | 749 | 18 |
| 2022 | `oddsapi:frozen` | 13,405 | 732 | 22 |
| 2022 | `oddsapi:submit` | 14,324 | 774 | 22 |
| 2023 | `oddsapi:frozen` | 10,366 | 745 | 16 |
| 2023 | `oddsapi:submit` | 11,275 | 786 | 16 |
| 2024 | `oddsapi:frozen` | 6,655 | 751 | 10 |
| 2024 | `oddsapi:submit` | 7,171 | 796 | 10 |
| 2025 | `oddsapi:frozen` | 7,623 | 759 | 11 |
| 2025 | `oddsapi:submit` | 8,496 | 807 | 11 |

Completed-game coverage gaps are explicit rather than silently discarded:

| season | completed games | missing frozen | missing submit | with both proxies |
|---:|---:|---:|---:|---:|
| 2021 | 770 | 39 | 21 | 730 |
| 2022 | 776 | 44 | 2 | 732 |
| 2023 | 792 | 47 | 6 | 745 |
| 2024 | 797 | 46 | 1 | 751 |
| 2025 | 807 | 48 | 0 | 759 |

Book depth and archive timestamp age are recorded as minimum / average /
maximum. Age is `requested_at - returned_at` in minutes:

| season | source | books/game min/avg/max | age minutes min/avg/max |
|---:|---|---:|---:|
| 2021 | frozen | 7 / 14.91 / 18 | 5 / 5 / 5 |
| 2021 | submit | 7 / 14.75 / 18 | 0 / 0.32 / 9 |
| 2022 | frozen | 9 / 18.31 / 21 | 4.33 / 4.48 / 5 |
| 2022 | submit | 11 / 18.51 / 21 | 0 / 3.58 / 9 |
| 2023 | frozen | 9 / 13.91 / 16 | 4.28 / 4.31 / 4.35 |
| 2023 | submit | 10 / 14.34 / 16 | 3.32 / 4.31 / 4.37 |
| 2024 | frozen | 6 / 8.86 / 10 | 4.33 / 4.36 / 4.37 |
| 2024 | submit | 6 / 9.01 / 10 | 3.37 / 4.36 / 4.38 |
| 2025 | frozen | 5 / 10.04 / 11 | 4.33 / 4.36 / 4.38 |
| 2025 | submit | 8 / 10.53 / 11 | 3.35 / 4.35 / 4.38 |

Final integrity checks returned zero for all four conditions: lines orphaned
from `games`, CFB `oddsapi:submit` rows captured at or after kickoff, archive
responses with `returned_at > requested_at`, and negative ledger `line_count`.
The coverage query must use `AS row_count`; bare `rows` is reserved by the
installed DuckDB parser.

**Odds API:** historical archive requests are accounted at 10 credits each;
live ones cost 1. `/v4/sports` is free and reports the current vendor balance
in `x-requests-remaining`. Query that endpoint before any future paid run rather
than relying on the pre-acquisition balance recorded in older notes.

## Verified against the live APIs (2026-08-11)

- **CFBD:** swept 2022-2025, weeks 1-15, through `load_cfb_games` and
  `load_cfb_lines`. **3,173 games and 10,197 lines resolved, zero
  `UnknownTeamError`**, 5 rows skipped for null spreads — confirming the CFBD
  null-spread branch is the one that actually fires.
- **The Odds API:** the real NCAAF feed replayed through `fetch_spreads` ->
  `decide_edges` -> `render_sheet` with a season-opener window stored 53 market
  lines across 6 in-window games and reported all 105 skips by reason (103 out
  of window, 2 untracked FCS teams). Live CFB works end to end.
- Both keys live in `.env` at the repo root (gitignored) and are read
  automatically. This was on the free tier (500/month); 4 credits were used.
- Note: polling before the season opens returns nothing, correctly — the
  default window is 7 days. Use `--days` to widen it.

## Verified against the live archive (2026-08-19)

- **926 snapshots fetched, 1,693 NFL games covered, zero orphaned rows.** Every
  game 2020-2025 has both proxies except 30 with no frozen snapshot — see
  "Known gaps" in `HANDOFF.md`.
- **The archive agrees with nflverse.** Comparing the `oddsapi:submit` consensus
  against nflverse closers on 2024: 71% exact, 97% within 0.5 pts, 100% within
  1.0 pt, mean absolute difference **0.141**. This is what validates the
  cross-source joins and the sign convention on real data — a flipped sign
  would show a mean difference near 9 points, not 0.14.
- **`tests/fixtures/odds_historical_nfl.json`** is a real captured archive
  response (2024-09-22T16:55Z, 31 events, 10 books, 10 credits). Two tests
  replay it offline. Prefer extending it over hand-writing new fixtures: the
  bugs that cost the most here were all in assumptions a hand-built fixture
  would have encoded rather than caught.

