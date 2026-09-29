# Interactive Results Dashboard

## Purpose

Replace the static weekly results page with one interactive page: tabs with a
summary up top, charts that can be tapped to open the games behind them, a
game table that can be filtered and sorted, a detail view for any game, and
switching between weeks without leaving the page.

This supersedes the page layout and the "static SVG, no scripts" decisions of
`2026-09-23-results-dashboard-design.md`. Its audience, access (Tailscale
`serve`, never `funnel`), the Tuesday job that writes it, and its failure
handling are unchanged.

A second project, designed separately, will have the Tuesday job ask Claude
for a short written analysis of each week: what went well, what didn't,
confident picks that lost, and ideas for improvement. This project only leaves
a place on the page for it.

## Decisions already made

| Question | Decision |
|---|---|
| Devices | Phone and laptop about equally; designed phone-first. |
| Build | Approach A: the page carries the season's data and draws itself in the browser. No server, no outside requests. |
| Charts | Drawn by the page's own code as SVG. No charting library. |
| Layout | Four tabs: Week, Season, Model, Games, plus a game detail panel. |
| Drill-down | Tap a chart mark to list its games; open any game; filters apply to every tab. No side-by-side week comparison. |
| Written analysis | A separate later project, generated automatically by the Tuesday job (option A). This page shows an empty box for it. |

## Architecture

```
pickem-results timer → fetch-results.sh → import-results / results-report
                                               │
                                    build_results_report()   (gains line history)
                                               │
                        ┌──────────────────────┴──────────────────────┐
             results_markdown.py (unchanged)                dashboard.py (new)
             <NAS>/results/week{N}-report.md                 builds the page data
                                                             + inlines page assets
                                                                      │
                                                     <dashboard dir>/index.html
                                                     <dashboard dir>/week-{N}.html (forwarders)
```

`ResultsReport` stays the only interface between grading and rendering. The
new builder reads it and nothing else.

## Component 1: line history on the report

`GradedGame` gains `line_history: tuple[LinePoint, ...]`, where a `LinePoint`
is `(captured_at, source, spread_home)`. It is built in `_grade_week` from the
market lines that function already loads, keeping only quotes captured before
kickoff:

- one point per poll for the live US books, using the same consensus function
  as the closing line, so the last point equals `close_spread`;
- one point per poll for Pinnacle, kept separate, when any were recorded.

No other field of the report changes. The Markdown report ignores the new
field.

## Component 2: page data — `src/pickem/report/dashboard.py`

```python
def build_dashboard_data(report: ResultsReport) -> dict
def render_dashboard(report: ResultsReport, *, generated_at: datetime) -> str
def render_week_forwarder(pool_week: int) -> str
```

`report` is always built through the latest imported pool week, so the page
covers the whole season to date.

### What the data holds

Grading stays in Python. The page never decides whether a pick won; it only
counts results Python already graded. The data holds:

- **Season:** season, entry name, latest pool week, generation time.
- **Standings:** every `WeekStanding` with its `BoardStanding`s, as today.
- **Games:** one entry per game in `season_games`: id, sport, pool week,
  kickoff, teams, final score, CBS line, closing line, field split,
  `against_field`, CLV, and, for every `Strategy`, its side and its result
  (win, loss, push, or none).
- **Model:** for each game, the last pre-kickoff recommendation (side, tier,
  edge) and the full pre-kickoff history of recommendations in time order.
- **Line history:** the points from Component 1.
- **Reference:** `BACKTEST_TIER_RATES`, `STRATEGY_DEFINITIONS`,
  `GLOSSARY_INTRO`, and the week's findings (below).
- **Analysis:** `null`. The analysis project fills this in later.

### Findings

"What the data says" comes from `findings()` in Python over the season through
each week, one set per week, and is shown for the selected week. It is not
recomputed when filters change; the section says it covers all games through
that week.

### Page assembly

The page's code and styles live in the repo as ordinary files under
`src/pickem/report/dashboard_assets/`:

- `app.css`: all styles.
- `stats.js`, `filters.js`, `scales.js`: pure logic, no DOM access.
- `ui.js`: tabs, charts, tables, detail panel.

These are classic scripts that attach to one shared namespace, so the builder
can inline them in order and Node tests can load the same files. The builder
inlines every asset and the data, producing one self-contained `index.html`
that makes no outside requests.

The data is embedded as JSON in `<script type="application/json">`, with
`<`, `>` and `&` escaped as `<`, `>` and `&`, so no string in
the data can close the tag. The page inserts all data into the page as text,
never as HTML.

## Component 3: in-page logic

### State and address

The page state is the selected week, tab, filters, and open game. It is kept
in the address after `#` (for example
`#week=4&tab=games&sport=cfb&tier=strong&result=loss&game=<id>`), so reloading
or sharing a link restores it. With no `#` the page opens on the latest week's
Week tab with no filters. An unknown or out-of-range value falls back to the
default for that setting.

### Selected week

The week picker sets which week the Week tab shows. The Season, Model and
Games tabs cover the season **through** the selected week, the same way each
old weekly page covered the season up to its week. Picking the latest week
shows the whole season.

### Filters

- **Board:** CFB, NFL, or both.
- **Tier:** the model's last pre-kickoff tier (strong, lean, coinflip,
  no market), plus "no model" for games the model never covered. Several may
  be selected.
- **Result:** all, our wins, our losses.

Filters narrow the games that every record, chart and table is computed from.
Standings (points, rank, board medians) are whole-week facts and are not
filtered. Each active filter shows as a removable chip.

### Math that must match Python

`stats.js` reimplements three things from Python:

- a W–L–P record from a set of games and a strategy (`record_for`);
- the 95% Wilson interval (`backtest.stats.wilson_interval`);
- the CLV summary: mean, share above zero, and normal-approximation interval
  for n ≥ 2 (`clv_summary`).

A rate with n = 0 is shown as "—", never 0%. Rates print in the existing
format: `W–L (P) = xx.x% [lo–hi], n`.

## Component 4: tabs

**Top bar (every tab):** week picker, filters, filter chips, tab strip, and a
light/dark/auto theme switch. The theme choice is remembered in browser
storage when available. Without storage, the page follows the device setting.

**Week tab**
1. Stat tiles: our points out of games, rank of entrants, points against the
   field median, gap to the winner.
2. Analysis box. It says "No analysis for this week yet" until the analysis
   project fills it.
3. One line per board: our points, board median, board best.
4. Confident picks that lost: the week's games where the model's last tier
   was strong and the model's pick lost. Each opens its game. It says "None"
   when empty.
5. A table per board of `WEEK_STRATEGIES` records for the week, filtered.

**Season tab**
1. Points by week: ours, the field median, the winner's.
2. Share of the field beaten by week.
3. Tapping a week's point selects that week.
4. A single week draws points without lines.

**Model tab**
1. Model win rate by board and tier, with Wilson whiskers and a tick at the
   backtest rate (CFB rows labeled "NFL backtest", as today).
2. Us and each baseline in `BASELINES`: dot and interval, with n.
3. CLV tiles: mean with interval, share above zero, n. Also the record of our
   against-the-field picks.
4. What the data says (findings for the selected week).
5. Glossary, folded.
6. Tapping a bar or dot opens a list of the games behind it (the games that
   strategy was graded on, within the current filters). Each opens its game.

**Games tab**
1. A table of every game within the filters and selected-week range: week,
   kickoff, matchup, CBS line, final score, our pick and result, the model's
   pick, tier and result, field split, and markers for "disagreed with the
   model" and "against the field".
2. Sort by any column; search by team name.
3. Tapping a row opens the game.

**Game detail panel.** Slides up from the bottom on narrow screens and sits
at the side on wide ones. It closes with ✕, Escape, or the back button (the
open game is part of the address). It shows:
1. Matchup, kickoff, CBS line, closing line, final score.
2. Every strategy's side and result.
3. The field's split.
4. The model's timeline: each recommendation before kickoff, with time,
   side, tier and edge, and changes of side or tier highlighted.
5. A line-movement chart: the US consensus over time, Pinnacle as a second
   series when present, and the CBS line as a reference line. It says
   "No line history" when empty.

## Component 5: charts

- One linear scale per chart places every mark, whisker, tick and label
  (`scales.js`). Rate charts run 0–100% with a 50% reference line.
- Labels and record text are HTML beside the SVG where they must stay legible
  on a phone.
- Tapping or clicking a mark shows its value, and where a mark stands for a
  set of games it also opens that list. The same gesture works with mouse and
  touch, and marks are reachable by keyboard.
- A chart with nothing to draw shows "No games yet".
- Colors come from CSS tokens defined for light and dark. Win and loss use
  green and red, always paired with W/L or ✓/✗ text.

## Component 6: writing the files

`_write_dashboard` in `cli.py` changes:

1. Build the report through the latest imported pool week (reusing the one
   already built when it is the latest).
2. Write `index.html` atomically.
3. Write `week-{N}.html` for every imported week as a small page that
   forwards to `index.html#week=N`, with a plain link as a fallback.
   This overwrites the old full weekly pages.
4. Log `dashboard_written` with the pool week, path and size, as today.

Failure handling is unchanged: on any render or write error, log
`dashboard_write_failed`, send the DM without the link, and exit 3. The DM
link (`<url>week-{N}.html`) is unchanged and now lands on the right week
through the forwarder.

`results-report --pool-week N` still rewrites week N's Markdown and now
always rebuilds the whole page. The runbook's "Rebuild a page" section is
updated to say so.

`results_html.py` and `charts.py` are removed along with their tests once
the new page replaces them.

## Presentation

- System font stack. A card layout on a neutral background.
- Readable at 360 px wide with a 16 px side gutter. The body never scrolls
  sideways; wide tables scroll in their own container.
- At 900 px and wider, the tab content uses two columns where it helps, and
  the detail panel docks to the right.
- Tap targets are at least 44 px.

## Testing

**Python (pytest)**
- `line_history`: pre-kickoff only, US consensus per poll, Pinnacle kept
  separate, and the last US point equals `close_spread`.
- `build_dashboard_data`: every game, strategy result, standing and
  recommendation in the report appears in the data.
- Escaping: a team name containing `</script>` cannot break out of the data
  block.
- Forwarder pages point at the right week.
- `_write_dashboard`: writes `index.html` and a forwarder per week, and the
  failure path still logs, still sends the DM, and exits 3.

**JavaScript (Node's built-in test runner, launched from one pytest test so
`uv run pytest -q` covers it)**
- Filters: board, tier (including "no model"), result, and selected-week
  range, alone and combined.
- Parity: pytest builds a report from fixture games, writes its data to a
  temp file, and has Node compute records, Wilson intervals and CLV summaries
  for every strategy × sport × tier. The results must match `record_for`,
  `wilson_interval` and `clv_summary` exactly for counts and to 1e-9 for
  rates.
- Drill-down: the games listed for a tapped mark are exactly the games
  counted in that mark's record.
- Scales: marks, whiskers and ticks land where expected; n = 0 gives "—".
- Address state: parsing, defaults, and fallback for bad values.

If Node is missing, the pytest wrapper fails with a message saying so rather
than skipping.

**By eye:** the rendered page with real data at 390 px and 1280 px wide, in
light and dark, checking every tab and the detail panel. No tests for purely
visual details.

## Out of scope

- Generating the written analysis (the next project).
- Side-by-side week comparison.
- Changes to the Markdown report or the DM text.
- An in-week live view.
