# Interactive Results Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static weekly results page with one self-contained interactive page (tabs, filters, drill-down lists, game detail, week switching) that the Tuesday job writes.

**Architecture:** Python grades the season as today, gains per-game line and model history, and serialises the whole season to JSON. One `index.html` inlines that JSON, one stylesheet, and a set of small classic scripts that attach to a shared `Pickem` namespace: pure logic (`stats.js`, `filters.js`, `scales.js`) tested under Node, and DOM code (`ui_core.js`, `charts.js`, `panel.js`, `tab_*.js`, `app.js`) smoke-tested in headless Chrome. `week-N.html` become forwarders to `index.html#week=N`.

**Tech Stack:** Python 3.12, pytest, typer, loguru; vanilla JavaScript (no libraries), inline SVG; Node 22 built-in test runner (`node --test`) and `node:vm`; headless Google Chrome (`--dump-dom`) for boot tests.

**Spec:** `docs/superpowers/specs/2026-09-29-interactive-dashboard-design.md`

## Global Constraints

- The page makes no outside requests: no fonts, scripts, styles or images from any host. Everything is inlined in `index.html`.
- No charting or UI library. Charts are SVG drawn by the page's own code.
- Grading stays in Python. The page never decides whether a pick won; it only counts `results` Python wrote.
- Data is embedded as JSON in `<script type="application/json" id="pickem-data">` with `<`, `>`, `&` escaped as `\u003c`, `\u003e`, `\u0026`. The page inserts data only as text (`textContent` / text nodes), never as HTML.
- A rate with n = 0 prints "—", never 0%. Rates print as `W–L (P) = xx.x% [lo–hi], n=N`.
- A chart with nothing to draw shows "No games yet".
- Readable at 360 px wide with a 16 px side gutter; the body never scrolls sideways; wide tables scroll in their own container; tap targets ≥ 44 px.
- At ≥ 900 px the detail panel docks to the right.
- Light and dark from `prefers-color-scheme`, overridable by a theme button (auto / light / dark) remembered in `localStorage` inside try/catch.
- Win/loss colour is always paired with W / L / P text.
- Kickoffs display in US Eastern time.
- If Node or Chrome is missing, the tests that need them fail with a message saying so; they never skip.
- Styling-only changes get a render check, not new tests (owner preference).
- Commit messages end with the session's attribution lines.

## Review Focus

1. **Filters that leave zero games** — every tab must still render: tables say "No games match", charts say "No games yet", the confident-losses list says "None". (Test: Task 11 boot test `#tab=games&tier=lean`, Task 9 `#tier=lean&result=win`.)
2. **A game with no model, no close and no line history opened in the detail panel** — shows "The model never covered this game.", "Not captured", "No line history", never a JS error. (Test: Task 8 boot test for the NFL game.)
3. **A stale or hand-edited address** (unknown week, removed game id, bogus tier) — falls back to defaults instead of breaking. (Test: Task 4 `parseHash` fallback tests.)
4. **A week or filter with only one board** — board tables and model tier rows appear only for boards that have games. (Test: Task 9 `#sport=nfl` shows one board table; Task 10 tier rows under `#sport=cfb`.)
5. **A season with a single imported week** — trend charts draw points without lines. (Test: Task 10 boot test on the single-week fixture asserts no `<polyline`.)

---

## File map

| File | Responsibility |
|---|---|
| `src/pickem/report/results.py` (modify) | `LinePoint`, `line_history()`, `GradedGame.history` and `.line_history` |
| `src/pickem/report/dashboard.py` (create) | `build_dashboard_data`, `render_dashboard`, `render_week_forwarder` |
| `src/pickem/report/dashboard_assets/app.css` | All styles and theme tokens |
| `.../stats.js` | Records, Wilson interval, CLV summary, record text |
| `.../filters.js` | Address state, filtering, drill-down sets, search, sort, chips |
| `.../scales.js` | Linear scale, nice max, extent, rate-mark geometry |
| `.../ui_core.js` | DOM/SVG element helpers, labels, formatters |
| `.../charts.js` | Rate rows, trend chart, line-movement chart |
| `.../panel.js` | Detail panel: drill-down list and game detail |
| `.../tab_week.js`, `tab_season.js`, `tab_model.js`, `tab_games.js` | One tab each |
| `.../app.js` | App state, top bar, routing, theme, boot |
| `src/pickem/cli.py` (modify) | `_write_dashboard` writes `index.html` + forwarders |
| `src/pickem/report/results_html.py`, `charts.py` (delete) | Old static renderer |
| `tests/dashboard_helpers.py` (create) | Synthetic season, page fixture, Node and Chrome runners |
| `tests/js/load.mjs`, `*.test.mjs`, `parity.mjs` (create) | Node harness and unit tests |
| `tests/test_dashboard_data.py`, `test_dashboard_js.py`, `test_dashboard_page.py` (create) | Python, Node-wrapper, and page/boot tests |
| `docs/runbooks/dashboard.md` (modify) | One page, forwarders, rebuild semantics |

Every script file uses the same wrapper so the files can be concatenated into one `<script>` and also loaded one by one in `node:vm`:

```js
(function (P) {
  "use strict";
  // ...
})(globalThis.Pickem = globalThis.Pickem || {});
```

---

### Task 1: Line and model history on graded games

**Files:**
- Modify: `src/pickem/report/results.py`
- Test: `tests/test_results_report.py` (append)

**Interfaces:**
- Produces:
  - `US_LINE = "us"`, `PINNACLE_LINE = "pinnacle"`
  - `@dataclass(frozen=True) class LinePoint: captured_at: datetime; source: str; spread_home: float`
  - `def line_history(lines: Iterable[MarketLine], kickoff: datetime) -> tuple[LinePoint, ...]`
  - `GradedGame.history: tuple[RecommendationRecord, ...] = ()` (pre-kickoff, oldest first)
  - `GradedGame.line_history: tuple[LinePoint, ...] = ()`
  - `grade_game(..., line_history: Sequence[LinePoint] = ())`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_results_report.py`:

```python
from pickem.models import LIVE_SOURCE, PINNACLE_SOURCE, MarketLine
from pickem.report.results import PINNACLE_LINE, US_LINE, LinePoint, closing_spread, line_history


def _quote(book, spread, minutes_before, source=LIVE_SOURCE):
    return MarketLine(
        game_id="g", source=source, book=book, spread_home=spread,
        captured_at=KICK - timedelta(minutes=minutes_before),
    )


def test_line_history_is_the_running_us_consensus_and_ends_at_the_close():
    quotes = [
        _quote("dk", -3.0, 300), _quote("fd", -3.5, 300),
        _quote("dk", -4.0, 60), _quote("fd", -4.5, 60),
        _quote("dk", -9.0, -10),  # after kickoff: ignored
    ]
    points = line_history(quotes, KICK)
    assert [(p.source, p.spread_home) for p in points] == [(US_LINE, -3.25), (US_LINE, -4.25)]
    assert points[-1].spread_home == closing_spread(quotes, KICK)


def test_pinnacle_is_its_own_series_and_never_moves_the_us_consensus():
    quotes = [
        _quote("dk", -3.0, 300),
        _quote("pinnacle", -6.0, 200, source=PINNACLE_SOURCE),
    ]
    assert line_history(quotes, KICK) == (
        LinePoint(KICK - timedelta(minutes=300), US_LINE, -3.0),
        LinePoint(KICK - timedelta(minutes=200), PINNACLE_LINE, -6.0),
    )


def test_no_quotes_means_no_history():
    assert line_history([], KICK) == ()


def test_graded_game_keeps_the_pre_kickoff_model_history_oldest_first():
    game = Game(
        game_id="cfb-2026-02-a-at-h", sport=Sport.CFB, season=2026, week=2,
        kickoff_utc=KICK, home_team_id="H", away_team_id="A", home_score=20, away_score=10,
    )

    def rec(hours, side, tier):
        return RecommendationRecord(
            game_id=game.game_id, sport=Sport.CFB, season=2026, week=2, side=side, tier=tier,
            edge_points=1.0, generated_at=KICK - timedelta(hours=hours), source=HISTORY_MONITOR,
        )

    late, early, after = rec(1, Side.AWAY, Tier.LEAN), rec(5, Side.HOME, Tier.STRONG), rec(-1, Side.HOME, Tier.LEAN)
    graded = grade_game(
        game=game, pool_week=2, league_spread=-3.5, close_spread=None, our_side=Side.HOME,
        field_home=1, field_away=1, history=[late, after, early],
    )
    assert graded.history == (early, late)
    assert graded.line_history == ()


def test_the_built_report_carries_each_games_line_history():
    store = imported_store()
    game_id = "cfb-2026-02-PSU-at-TEM"
    store.append_market_lines([
        MarketLine(game_id=game_id, source=LIVE_SOURCE, book="dk", spread_home=24.0,
                   captured_at=KICK - timedelta(hours=3)),
    ])
    report = build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    graded = next(g for g in report.season_games if g.game.game_id == game_id)
    assert [(p.source, p.spread_home) for p in graded.line_history] == [(US_LINE, 24.0)]
```

Before running, check the top of `tests/test_results_report.py` for the imports these tests use (`KICK`, `imported_store`, `timedelta`, `Game`, `RecommendationRecord`, `HISTORY_MONITOR`, `Side`, `Sport`, `Tier`, `grade_game`, `build_results_report`) and add any that are missing. Check the store's method name for inserting market lines with `grep -n "def .*market_lines" src/pickem/store/db.py` and use that name in the last test if it differs from `append_market_lines`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_results_report.py -q -k "line_history or pinnacle or no_quotes or model_history"`
Expected: FAIL with `ImportError: cannot import name 'PINNACLE_LINE'`.

- [ ] **Step 3: Implement**

In `src/pickem/report/results.py`:

1. Add `PINNACLE_SOURCE` to the `pickem.models` import.
2. After `closing_spread`, add:

```python
US_LINE = "us"
PINNACLE_LINE = "pinnacle"


@dataclass(frozen=True)
class LinePoint:
    captured_at: datetime
    source: str  # US_LINE or PINNACLE_LINE
    spread_home: float


def line_history(lines: Iterable[MarketLine], kickoff: datetime) -> tuple[LinePoint, ...]:
    """The market's spread through the week, from quotes captured before kickoff.

    The US series is the consensus after each poll, computed the same way as
    the closing line, so its last point is ``closing_spread``. Pinnacle is its
    own series and never enters the consensus.
    """
    before = [ln for ln in lines if ln.captured_at < kickoff]
    live = [ln for ln in before if ln.source == LIVE_SOURCE]
    points: list[LinePoint] = []
    with suppress_decision_logging():
        for at in sorted({ln.captured_at for ln in live}):
            spread = consensus_spread([ln for ln in live if ln.captured_at <= at])
            points.append(LinePoint(at, US_LINE, spread))
    points += [
        LinePoint(ln.captured_at, PINNACLE_LINE, ln.spread_home)
        for ln in before
        if ln.source == PINNACLE_SOURCE
    ]
    return tuple(sorted(points, key=lambda p: (p.captured_at, p.source)))
```

3. `GradedGame` is declared before `LinePoint` in the file; move the `LinePoint` block (and the two constants) above `class GradedGame` so the annotation resolves at import time without quotes. Add two fields at the end of `GradedGame`:

```python
    history: tuple[RecommendationRecord, ...] = ()
    line_history: tuple[LinePoint, ...] = ()
```

4. In `grade_game`, add the keyword parameter `line_history: Sequence[LinePoint] = (),` and pass to the constructor:

```python
        history=tuple(
            sorted(
                (r for r in history if r.generated_at < game.kickoff_utc),
                key=lambda r: (r.generated_at, r.source),
            )
        ),
        line_history=tuple(line_history),
```

The parameter name `line_history` shadows the module function inside `grade_game`; that is fine because `grade_game` does not call it.

5. In `_grade_week`, pass the history to `grade_game`:

```python
                line_history=line_history(lines[game_id], game.kickoff_utc),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_results_report.py tests/test_results_markdown.py tests/test_results_html.py -q`
Expected: all PASS (the Markdown and old HTML renderers ignore the new fields).

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/results.py tests/test_results_report.py
git commit -m "feat: carry each game's line history and model history on the graded report"
```

---

### Task 2: Page data builder

**Files:**
- Create: `src/pickem/report/dashboard.py`
- Create: `tests/dashboard_helpers.py`
- Test: `tests/test_dashboard_data.py`

**Interfaces:**
- Consumes: `GradedGame.history`, `GradedGame.line_history`, `LinePoint` (Task 1).
- Produces:
  - `build_dashboard_data(report: ResultsReport, *, generated_at: datetime) -> dict` with exactly these keys:
    `season, entry, latest_week, generated_at, strategies, baselines, week_strategies, definitions, glossary_intro, backtest, standings, findings, games, analysis`.
  - Each `standings[i]`: `{week, entrants, rank, points, median, winner, beat_share, boards: [{sport, points, median, best}]}`.
  - `findings`: `{"<week>": {"claims": [...], "not_yet": [...]}}` for every standings week.
  - Each `games[i]`: `{id, sport, week, kickoff, home, away, home_score, away_score, line, close, field_home, field_away, against_field, clv, picks: {strategy: "home"|"away"|null}, results: {strategy: "win"|"loss"|"push"|null}, model: {at, side, tier, edge}|null, history: [{at, side, tier, edge}], lines: [{at, source, spread}]}`. Times are UTC ISO strings ending in `Z`. Strategy keys are `Strategy` values (`"us"`, `"model"`, `"first sheet"`, …).
  - `tests/dashboard_helpers.py`: `GENERATED`, `synthetic_season(seed=7, per_week=30) -> ResultsReport`.

- [ ] **Step 1: Write the helpers and failing tests**

`tests/dashboard_helpers.py`:

```python
"""Shared fixtures for the dashboard: a synthetic season, and runners for Node and Chrome."""

import random
from datetime import UTC, datetime, timedelta

from pickem.models import HISTORY_MONITOR, Game, RecommendationRecord, Side, Sport, Tier
from pickem.report.results import BoardStanding, ResultsReport, WeekStanding, grade_game

GENERATED = datetime(2026, 9, 29, 13, tzinfo=UTC)
KICK = datetime(2026, 9, 5, 16, tzinfo=UTC)


def synthetic_season(seed: int = 7, per_week: int = 30) -> ResultsReport:
    """Two pool weeks, both boards, with pushes, blank picks, every tier, and games the model skipped."""
    rng = random.Random(seed)
    games = []
    for week in (1, 2):
        for i in range(per_week):
            sport = Sport.CFB if i % 2 else Sport.NFL
            line = rng.choice([-7.0, -3.5, -3.0, 1.5, 3.0, 6.5])
            margin = -int(line) if i % 7 == 0 and line == int(line) else rng.randint(-14, 14)
            game = Game(
                game_id=f"{sport.value}-2026-{week:02d}-a{i}-at-h{i}", sport=sport, season=2026,
                week=week, kickoff_utc=KICK + timedelta(days=7 * (week - 1), hours=i),
                home_team_id=f"H{week}{i}", away_team_id=f"A{week}{i}",
                home_score=20 + margin, away_score=20,
            )
            tier = rng.choice([None, Tier.STRONG, Tier.LEAN, Tier.COINFLIP, Tier.NO_MARKET])
            history = [] if tier is None else [
                RecommendationRecord(
                    game_id=game.game_id, sport=sport, season=2026, week=week,
                    side=rng.choice([Side.HOME, Side.AWAY]), tier=tier,
                    edge_points=round(rng.uniform(-3, 3), 1),
                    generated_at=game.kickoff_utc - timedelta(hours=2), source=HISTORY_MONITOR,
                )
            ]
            games.append(grade_game(
                game=game, pool_week=week, league_spread=line,
                close_spread=rng.choice([None, line - 1, line, line + 0.5]),
                our_side=rng.choice([None, Side.HOME, Side.AWAY, Side.HOME]),
                field_home=rng.randint(0, 20), field_away=rng.randint(0, 20), history=history,
            ))
    standings = tuple(
        WeekStanding(
            pool_week=week, entrants=20, our_rank=5, our_points=15, median_points=14.0,
            winner_points=20, beat_share=0.75,
            boards=(BoardStanding(Sport.CFB, 8, 7.0, 11), BoardStanding(Sport.NFL, 7, 7.0, 10)),
        )
        for week in (1, 2)
    )
    return ResultsReport(
        season=2026, pool_week=2, entry_name="Jota", standings=standings,
        week_games=tuple(g for g in games if g.pool_week == 2), season_games=tuple(games),
    )
```

`tests/test_dashboard_data.py`:

```python
import json
from datetime import timedelta

from dashboard_helpers import GENERATED, KICK, synthetic_season

from pickem.report.dashboard import build_dashboard_data
from pickem.report.results import (
    BACKTEST_TIER_RATES,
    STRATEGY_DEFINITIONS,
    US_LINE,
    LinePoint,
    Strategy,
    findings,
)


def test_data_is_plain_json():
    data = build_dashboard_data(synthetic_season(), generated_at=GENERATED)
    assert json.loads(json.dumps(data)) == data
    assert data["generated_at"] == "2026-09-29T13:00:00Z"
    assert data["latest_week"] == 2 and data["season"] == 2026 and data["entry"] == "Jota"
    assert data["analysis"] is None


def test_every_game_carries_every_strategys_side_and_graded_result():
    report = synthetic_season()
    data = build_dashboard_data(report, generated_at=GENERATED)
    assert [g["id"] for g in data["games"]] == [g.game.game_id for g in report.season_games]
    for graded, game in zip(report.season_games, data["games"], strict=True):
        for strategy in Strategy:
            side = graded.picks.get(strategy)
            result = graded.result(strategy)
            assert game["picks"][strategy.value] == (side.value if side else None)
            assert game["results"][strategy.value] == (result.value if result else None)
        assert game["week"] == graded.pool_week
        assert game["line"] == graded.league_spread and game["close"] == graded.close_spread
        assert game["clv"] == graded.clv and game["against_field"] == graded.against_field
        assert (game["model"] is None) == (graded.model is None)
        if graded.model is not None:
            assert game["model"]["tier"] == graded.model.tier.value
            assert game["model"]["side"] == graded.model.side.value
        assert len(game["history"]) == len(graded.history)


def test_line_history_is_serialised_in_order():
    report = synthetic_season()
    first = report.season_games[0]
    points = (LinePoint(KICK - timedelta(hours=5), US_LINE, -3.0),
              LinePoint(KICK - timedelta(hours=1), US_LINE, -3.5))
    report.season_games[0].__dict__["line_history"] = points  # frozen dataclass: test-only override
    data = build_dashboard_data(report, generated_at=GENERATED)
    assert data["games"][0]["id"] == first.game.game_id
    assert data["games"][0]["lines"] == [
        {"at": "2026-09-05T11:00:00Z", "source": "us", "spread": -3.0},
        {"at": "2026-09-05T15:00:00Z", "source": "us", "spread": -3.5},
    ]


def test_standings_and_reference_tables():
    report = synthetic_season()
    data = build_dashboard_data(report, generated_at=GENERATED)
    assert data["standings"][1] == {
        "week": 2, "entrants": 20, "rank": 5, "points": 15, "median": 14.0, "winner": 20,
        "beat_share": 0.75,
        "boards": [{"sport": "cfb", "points": 8, "median": 7.0, "best": 11},
                   {"sport": "nfl", "points": 7, "median": 7.0, "best": 10}],
    }
    assert data["strategies"] == [s.value for s in Strategy]
    assert data["definitions"] == {s.value: t for s, t in STRATEGY_DEFINITIONS.items()}
    assert data["backtest"] == {t.value: r for t, r in BACKTEST_TIER_RATES.items()}


def test_findings_are_computed_through_each_week():
    report = synthetic_season()
    data = build_dashboard_data(report, generated_at=GENERATED)
    week1 = findings([g for g in report.season_games if g.pool_week <= 1])
    assert data["findings"]["1"] == {"claims": list(week1.claims), "not_yet": list(week1.not_yet)}
    season = findings(report.season_games)
    assert data["findings"]["2"] == {"claims": list(season.claims), "not_yet": list(season.not_yet)}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_data.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.report.dashboard'`.

- [ ] **Step 3: Implement `src/pickem/report/dashboard.py`**

```python
"""The interactive results dashboard: the season's data, and the one page that draws it.

The page is self-contained: its data, styles and scripts are inlined, and it
makes no outside requests. Grading happens here in Python; the page only
counts what this module wrote.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pickem.models import RecommendationRecord
from pickem.report.results import (
    BACKTEST_TIER_RATES,
    BASELINES,
    GLOSSARY_INTRO,
    STRATEGY_DEFINITIONS,
    WEEK_STRATEGIES,
    GradedGame,
    ResultsReport,
    Strategy,
    WeekStanding,
    findings,
)


def build_dashboard_data(report: ResultsReport, *, generated_at: datetime) -> dict:
    """Everything the page shows, as plain JSON-ready values."""
    return {
        "season": report.season,
        "entry": report.entry_name,
        "latest_week": report.pool_week,
        "generated_at": _iso(generated_at),
        "strategies": [s.value for s in Strategy],
        "baselines": [s.value for s in BASELINES],
        "week_strategies": [s.value for s in WEEK_STRATEGIES],
        "definitions": {s.value: text for s, text in STRATEGY_DEFINITIONS.items()},
        "glossary_intro": GLOSSARY_INTRO,
        "backtest": {tier.value: rate for tier, rate in BACKTEST_TIER_RATES.items()},
        "standings": [_standing(s) for s in report.standings],
        "findings": {
            str(s.pool_week): _findings(report, s.pool_week) for s in report.standings
        },
        "games": [_game(g) for g in report.season_games],
        "analysis": None,
    }


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _value(member: Enum | None) -> str | None:
    return None if member is None else member.value


def _standing(standing: WeekStanding) -> dict:
    return {
        "week": standing.pool_week,
        "entrants": standing.entrants,
        "rank": standing.our_rank,
        "points": standing.our_points,
        "median": standing.median_points,
        "winner": standing.winner_points,
        "beat_share": standing.beat_share,
        "boards": [
            {"sport": b.sport.value, "points": b.our_points, "median": b.median_points,
             "best": b.best_points}
            for b in standing.boards
        ],
    }


def _findings(report: ResultsReport, week: int) -> dict:
    found = findings([g for g in report.season_games if g.pool_week <= week])
    return {"claims": list(found.claims), "not_yet": list(found.not_yet)}


def _recommendation(record: RecommendationRecord) -> dict:
    return {
        "at": _iso(record.generated_at),
        "side": record.side.value,
        "tier": record.tier.value,
        "edge": record.edge_points,
    }


def _game(graded: GradedGame) -> dict:
    game = graded.game
    return {
        "id": game.game_id,
        "sport": game.sport.value,
        "week": graded.pool_week,
        "kickoff": _iso(game.kickoff_utc),
        "home": game.home_team_id,
        "away": game.away_team_id,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "line": graded.league_spread,
        "close": graded.close_spread,
        "field_home": graded.field_home,
        "field_away": graded.field_away,
        "against_field": graded.against_field,
        "clv": graded.clv,
        "picks": {s.value: _value(graded.picks.get(s)) for s in Strategy},
        "results": {s.value: _value(graded.result(s)) for s in Strategy},
        "model": None if graded.model is None else _recommendation(graded.model),
        "history": [_recommendation(r) for r in graded.history],
        "lines": [
            {"at": _iso(p.captured_at), "source": p.source, "spread": p.spread_home}
            for p in graded.line_history
        ],
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_data.py -q && uv run ruff check src/pickem/report/dashboard.py tests/dashboard_helpers.py tests/test_dashboard_data.py`
Expected: PASS, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/dashboard.py tests/dashboard_helpers.py tests/test_dashboard_data.py
git commit -m "feat: serialise the graded season for the interactive dashboard"
```

---

### Task 3: Node harness, stats.js, and parity with Python

**Files:**
- Create: `src/pickem/report/dashboard_assets/stats.js`
- Create: `tests/js/load.mjs`, `tests/js/stats.test.mjs`, `tests/js/parity.mjs`
- Create: `tests/test_dashboard_js.py`
- Modify: `tests/dashboard_helpers.py` (add `run_node`)

**Interfaces:**
- Consumes: `build_dashboard_data`, `synthetic_season`, `GENERATED` (Task 2).
- Produces:
  - `Pickem.stats.record(games, strategy) -> {wins, losses, pushes, decided, rate|null, interval|[lo,hi]|null}`
  - `Pickem.stats.wilson(successes, trials) -> [lo, hi]`
  - `Pickem.stats.clvSummary(games) -> {n, mean|null, positive|null, interval|null}`
  - `Pickem.stats.recordText(record) -> string`
  - `Pickem.stats.pct(x, digits) -> string` ("—" for null)
  - `tests/js/load.mjs`: `load(...names) -> Pickem`, `plain(value)`, `game(overrides)`
  - `tests/dashboard_helpers.py`: `run_node(*args) -> str`
  - `parity.mjs` imports `filters.js` too; it is created in Task 4, so the parity test is added in Task 4, Step 1.

- [ ] **Step 1: Write the harness and failing tests**

`tests/js/load.mjs`:

```js
// Load the dashboard's classic scripts into a fresh context, the way the page concatenates them.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ASSETS = new URL("../../src/pickem/report/dashboard_assets/", import.meta.url);

export function load(...names) {
  const context = vm.createContext({ URLSearchParams });
  for (const name of names) {
    vm.runInContext(readFileSync(new URL(name, ASSETS), "utf8"), context, { filename: name });
  }
  return context.Pickem;
}

// Values built inside the vm carry that context's prototypes; compare them as plain data.
export const plain = (value) => JSON.parse(JSON.stringify(value));

const STRATEGIES = ["us", "model", "first sheet", "close divergence", "favorites", "home", "field consensus"];

export function game(overrides = {}) {
  const base = {
    id: "g1", sport: "cfb", week: 1, kickoff: "2026-09-05T16:00:00Z",
    home: "MICH", away: "OU", home_score: 17, away_score: 10,
    line: -3.5, close: -4.0, field_home: 10, field_away: 5, against_field: false, clv: 0.5,
    picks: Object.fromEntries(STRATEGIES.map((s) => [s, "home"])),
    results: Object.fromEntries(STRATEGIES.map((s) => [s, "win"])),
    model: { at: "2026-09-05T14:00:00Z", side: "home", tier: "strong", edge: 2.5 },
    history: [], lines: [],
  };
  return {
    ...base, ...overrides,
    picks: { ...base.picks, ...(overrides.picks || {}) },
    results: { ...base.results, ...(overrides.results || {}) },
  };
}
```

`tests/js/stats.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { load, plain, game } from "./load.mjs";

const P = load("stats.js");

test("record counts wins, losses and pushes, and rates only decided games", () => {
  const games = [
    game({ results: { us: "win" } }), game({ results: { us: "loss" } }),
    game({ results: { us: "push" } }), game({ results: { us: null } }),
  ];
  const r = plain(P.stats.record(games, "us"));
  assert.deepEqual(
    { wins: r.wins, losses: r.losses, pushes: r.pushes, decided: r.decided, rate: r.rate },
    { wins: 1, losses: 1, pushes: 1, decided: 2, rate: 0.5 },
  );
  assert.deepEqual(r.interval, plain(P.stats.wilson(1, 2)));
});

test("a record with nothing decided has no rate and no interval", () => {
  const r = P.stats.record([game({ results: { us: "push" } })], "us");
  assert.equal(r.rate, null);
  assert.equal(r.interval, null);
  assert.equal(P.stats.recordText(r), "—");
});

test("wilson interval", () => {
  const [lo, hi] = P.stats.wilson(7, 10);
  assert.ok(Math.abs(lo - 0.3968) < 1e-4, lo);
  assert.ok(Math.abs(hi - 0.8922) < 1e-4, hi);
  assert.deepEqual(plain(P.stats.wilson(0, 0)), [0, 1]);
});

test("record text matches the Markdown format", () => {
  const games = [
    ...Array(7).fill(game({ results: { us: "win" } })),
    ...Array(3).fill(game({ results: { us: "loss" } })),
    game({ results: { us: "push" } }),
  ];
  assert.equal(P.stats.recordText(P.stats.record(games, "us")), "7–3 (1) = 70.0% [40%–89%], n=10");
});

test("clv summary: mean, share above zero, and a normal interval from n=2", () => {
  const s = plain(P.stats.clvSummary([game({ clv: 1 }), game({ clv: -1 }), game({ clv: 2 }), game({ clv: null })]));
  assert.equal(s.n, 3);
  assert.ok(Math.abs(s.mean - 2 / 3) < 1e-12);
  assert.ok(Math.abs(s.positive - 2 / 3) < 1e-12);
  const half = 1.96 * Math.sqrt(42 / 18) / Math.sqrt(3);
  assert.ok(Math.abs(s.interval[0] - (2 / 3 - half)) < 1e-12);
  assert.deepEqual(plain(P.stats.clvSummary([game({ clv: 1 })])).interval, null);
  assert.deepEqual(plain(P.stats.clvSummary([])), { n: 0, mean: null, positive: null, interval: null });
});

test("pct prints a dash for no value", () => {
  assert.equal(P.stats.pct(null, 1), "—");
  assert.equal(P.stats.pct(0.637, 1), "63.7%");
});
```

`tests/test_dashboard_js.py`:

```python
"""Run the dashboard's JavaScript unit tests under Node as part of pytest."""

from dashboard_helpers import run_node


def test_dashboard_javascript_unit_tests():
    run_node("--test", "tests/js/*.test.mjs")
```

Append to `tests/dashboard_helpers.py`:

```python
import shutil
import subprocess

NODE = shutil.which("node")


def run_node(*args: str) -> str:
    """Run Node from the repo root and return stdout; fail loudly when Node is missing."""
    assert NODE, "Node is required for the dashboard's JavaScript tests: install nodejs"
    result = subprocess.run(
        [NODE, *args], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout
```

(Move the two new imports to the top of the file with the others.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_js.py -q`
Expected: FAIL — Node reports `ENOENT ... stats.js`.

- [ ] **Step 3: Implement `stats.js`**

```js
// Records, intervals and CLV, computed the same way as pickem.report.results.
(function (P) {
  "use strict";
  const Z = 1.96;

  function wilson(successes, trials) {
    if (trials === 0) return [0, 1];
    const p = successes / trials;
    const z2 = Z * Z;
    const denominator = 1 + z2 / trials;
    const center = (p + z2 / (2 * trials)) / denominator;
    const spread = (Z * Math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))) / denominator;
    return [Math.max(0, center - spread), Math.min(1, center + spread)];
  }

  function record(games, strategy) {
    let wins = 0, losses = 0, pushes = 0;
    for (const game of games) {
      const result = game.results[strategy];
      if (result === "win") wins++;
      else if (result === "loss") losses++;
      else if (result === "push") pushes++;
    }
    const decided = wins + losses;
    return {
      wins, losses, pushes, decided,
      rate: decided ? wins / decided : null,
      interval: decided ? wilson(wins, decided) : null,
    };
  }

  function clvSummary(games) {
    const values = games.map((g) => g.clv).filter((v) => v !== null && v !== undefined);
    const n = values.length;
    if (!n) return { n: 0, mean: null, positive: null, interval: null };
    const mean = values.reduce((a, b) => a + b, 0) / n;
    const positive = values.filter((v) => v > 0).length / n;
    if (n < 2) return { n, mean, positive, interval: null };
    const variance = values.reduce((a, v) => a + (v - mean) ** 2, 0) / (n - 1);
    const half = (Z * Math.sqrt(variance)) / Math.sqrt(n);
    return { n, mean, positive, interval: [mean - half, mean + half] };
  }

  function pct(x, digits) {
    return x === null || x === undefined ? "—" : (x * 100).toFixed(digits) + "%";
  }

  function recordText(r) {
    if (!r.decided) return "—";
    return `${r.wins}–${r.losses} (${r.pushes}) = ${pct(r.rate, 1)} ` +
      `[${pct(r.interval[0], 0)}–${pct(r.interval[1], 0)}], n=${r.decided}`;
  }

  P.stats = { wilson, record, clvSummary, pct, recordText };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_js.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/dashboard_assets/stats.js tests/js tests/test_dashboard_js.py tests/dashboard_helpers.py
git commit -m "feat: dashboard record, interval and CLV math in the page, tested under Node"
```

---

### Task 4: filters.js — address state, filters, drill-down, search, sort, chips

**Files:**
- Create: `src/pickem/report/dashboard_assets/filters.js`
- Create: `tests/js/filters.test.mjs`, `tests/js/parity.mjs`
- Modify: `tests/test_dashboard_js.py` (add parity test)

**Interfaces:**
- Consumes: `Pickem.stats.record`, `clvSummary` (Task 3); data shape (Task 2).
- Produces (`Pickem.filters`):
  - `TABS = ["week","season","model","games"]`, `TIERS = ["strong","lean","coinflip","no_market","none"]`, `TIER_LABELS`
  - `defaults(data) -> state` where `state = {week, tab, sport: "all"|"cfb"|"nfl", tiers: string[], result: "all"|"win"|"loss", game: string|null}`
  - `parseHash(hash, data) -> state`, `toHash(state, data) -> string` (`""` or `"#..."`)
  - `tierOf(game) -> tier | "none"`
  - `applyFilters(games, state) -> games` (week ≤ state.week, sport, tiers, our result)
  - `confidentLosses(games) -> games` (model tier strong and model lost)
  - `gradedFor(games, strategy) -> games` (result not null; exactly the games `record` counts)
  - `search(games, text)`, `sortGames(games, key, dir)` with keys `week, kickoff, matchup, line, us, model, tier, field`
  - `chips(state) -> [{key, label}]`, `clearFilter(state, key) -> state`

- [ ] **Step 1: Write the failing tests**

`tests/js/filters.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { load, plain, game } from "./load.mjs";

const P = load("stats.js", "filters.js");
const F = P.filters;
const data = {
  latest_week: 3,
  standings: [{ week: 1 }, { week: 2 }, { week: 3 }],
  games: [game({ id: "a" }), game({ id: "b" })],
};

test("no hash opens the latest week's Week tab with no filters", () => {
  assert.deepEqual(plain(F.parseHash("", data)),
    { week: 3, tab: "week", sport: "all", tiers: [], result: "all", game: null });
});

test("a full hash round-trips", () => {
  const state = { week: 2, tab: "games", sport: "cfb", tiers: ["strong", "none"], result: "loss", game: "b" };
  assert.deepEqual(plain(F.parseHash(F.toHash(state, data), data)), state);
});

test("defaults are left out of the address", () => {
  assert.equal(F.toHash(F.defaults(data), data), "");
});

test("bad or stale values fall back to defaults one by one", () => {
  const state = plain(F.parseHash("#week=9&tab=nope&sport=mlb&tier=strong,bogus,strong&result=tie&game=gone", data));
  assert.deepEqual(state, { week: 3, tab: "week", sport: "all", tiers: ["strong"], result: "all", game: null });
});

test("tier of a game the model never covered is 'none'", () => {
  assert.equal(F.tierOf(game({ model: null })), "none");
  assert.equal(F.tierOf(game()), "strong");
});

test("filters combine: week range, board, tiers and our result", () => {
  const games = [
    game({ id: "w1", week: 1 }),
    game({ id: "w3", week: 3 }),
    game({ id: "nfl", week: 1, sport: "nfl" }),
    game({ id: "lean", week: 1, model: { side: "home", tier: "lean", edge: 1, at: "" } }),
    game({ id: "nomodel", week: 1, model: null }),
    game({ id: "lost", week: 1, results: { us: "loss" } }),
    game({ id: "blank", week: 1, results: { us: null } }),
  ];
  const ids = (state) => F.applyFilters(games, { ...F.defaults(data), ...state }).map((g) => g.id);
  assert.deepEqual(ids({ week: 2 }), ["w1", "nfl", "lean", "nomodel", "lost", "blank"]);
  assert.deepEqual(ids({ week: 2, sport: "nfl" }), ["nfl"]);
  assert.deepEqual(ids({ week: 2, tiers: ["lean", "none"] }), ["lean", "nomodel"]);
  assert.deepEqual(ids({ week: 2, result: "loss" }), ["lost"]);
  assert.deepEqual(ids({ week: 2, result: "win", sport: "cfb", tiers: ["strong"] }), ["w1"]);
  assert.deepEqual(ids({ week: 2, tiers: ["coinflip"] }), []);
});

test("confident losses are strong-tier games the model lost", () => {
  const games = [
    game({ id: "hit" }),
    game({ id: "miss", results: { model: "loss" } }),
    game({ id: "leanmiss", results: { model: "loss" }, model: { side: "home", tier: "lean", edge: 1, at: "" } }),
  ];
  assert.deepEqual(F.confidentLosses(games).map((g) => g.id), ["miss"]);
});

test("a drill-down lists exactly the games its record counts", () => {
  const games = [
    game({ id: "w", results: { model: "win" } }), game({ id: "l", results: { model: "loss" } }),
    game({ id: "p", results: { model: "push" } }), game({ id: "n", results: { model: null } }),
  ];
  const listed = F.gradedFor(games, "model");
  const r = P.stats.record(games, "model");
  assert.deepEqual(listed.map((g) => g.id), ["w", "l", "p"]);
  assert.equal(listed.length, r.wins + r.losses + r.pushes);
});

test("search matches either team, ignoring case and spaces", () => {
  const games = [game({ id: "a", home: "MICH", away: "OU" }), game({ id: "b", home: "TEM", away: "PSU" })];
  assert.deepEqual(F.search(games, "  psu ").map((g) => g.id), ["b"]);
  assert.deepEqual(F.search(games, "").map((g) => g.id), ["a", "b"]);
});

test("sorting puts missing values last in both directions and breaks ties by id", () => {
  const games = [
    game({ id: "b", results: { us: "win" } }), game({ id: "a", results: { us: "win" } }),
    game({ id: "c", results: { us: null } }), game({ id: "d", results: { us: "loss" } }),
  ];
  assert.deepEqual(F.sortGames(games, "us", "asc").map((g) => g.id), ["d", "a", "b", "c"]);
  assert.deepEqual(F.sortGames(games, "us", "desc").map((g) => g.id), ["a", "b", "d", "c"]);
  assert.deepEqual(F.sortGames(games, "nope", "asc").map((g) => g.id), ["a", "b", "c", "d"]);
});

test("chips name each active filter and clear one at a time", () => {
  const state = { ...F.defaults(data), sport: "cfb", tiers: ["strong", "none"], result: "win" };
  assert.deepEqual(plain(F.chips(state)), [
    { key: "sport", label: "CFB" },
    { key: "tiers", label: "Strong + No model" },
    { key: "result", label: "Our wins" },
  ]);
  assert.deepEqual(plain(F.clearFilter(state, "tiers")).tiers, []);
  assert.deepEqual(plain(F.chips(F.defaults(data))), []);
});
```

`tests/js/parity.mjs`:

```js
// Print the page's records and CLV summaries for every board × tier, for comparison with Python.
import { readFileSync } from "node:fs";
import { load } from "./load.mjs";

const P = load("stats.js", "filters.js");
const data = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = { records: {}, clv: {} };
for (const sport of ["all", "cfb", "nfl"]) {
  for (const tier of ["any", "strong", "lean", "coinflip", "no_market"]) {
    const state = { ...P.filters.defaults(data), sport, tiers: tier === "any" ? [] : [tier] };
    const games = P.filters.applyFilters(data.games, state);
    for (const strategy of data.strategies) {
      out.records[`${strategy}|${sport}|${tier}`] = P.stats.record(games, strategy);
    }
  }
  out.clv[sport] = P.stats.clvSummary(
    P.filters.applyFilters(data.games, { ...P.filters.defaults(data), sport }),
  );
}
process.stdout.write(JSON.stringify(out));
```

Append to `tests/test_dashboard_js.py`:

```python
import json

import pytest
from dashboard_helpers import GENERATED, run_node, synthetic_season

from pickem.models import Sport, Tier
from pickem.report.dashboard import build_dashboard_data
from pickem.report.results import Strategy, clv_summary, record_for

SPORTS = {"all": None, "cfb": Sport.CFB, "nfl": Sport.NFL}
TIERS = {"any": None, "strong": Tier.STRONG, "lean": Tier.LEAN, "coinflip": Tier.COINFLIP,
         "no_market": Tier.NO_MARKET}


def test_page_math_matches_python(tmp_path):
    report = synthetic_season()
    games = report.season_games
    # Guard against a vacuous comparison: the fixture must exercise the awkward cases.
    assert any(g.result(Strategy.US) and g.result(Strategy.US).value == "push" for g in games)
    assert any(g.model is None for g in games)
    assert any(g.picks[Strategy.US] is None for g in games)

    path = tmp_path / "data.json"
    path.write_text(json.dumps(build_dashboard_data(report, generated_at=GENERATED)))
    out = json.loads(run_node("tests/js/parity.mjs", str(path)))

    for sport_key, sport in SPORTS.items():
        for tier_key, tier in TIERS.items():
            for strategy in Strategy:
                expected = record_for(games, strategy, sport=sport, tier=tier)
                got = out["records"][f"{strategy.value}|{sport_key}|{tier_key}"]
                label = (strategy.value, sport_key, tier_key)
                assert (got["wins"], got["losses"], got["pushes"]) == (
                    expected.wins, expected.losses, expected.pushes), label
                if expected.decided:
                    assert got["rate"] == pytest.approx(expected.rate, abs=1e-9), label
                    assert got["interval"] == pytest.approx(list(expected.interval), abs=1e-9)
                else:
                    assert got["rate"] is None and got["interval"] is None, label
        clv = clv_summary(games, sport=sport)
        got = out["clv"][sport_key]
        assert got["n"] == clv.n
        assert got["mean"] == pytest.approx(clv.mean, abs=1e-9)
        assert got["positive"] == pytest.approx(clv.positive_share, abs=1e-9)
        if clv.interval is None:
            assert got["interval"] is None
        else:
            assert got["interval"] == pytest.approx(list(clv.interval), abs=1e-9)
```

Move the new imports to the top of the file.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_js.py -q`
Expected: FAIL — `filters.js` not found.

- [ ] **Step 3: Implement `filters.js`**

```js
// Page state in the address, and which games each view counts.
(function (P) {
  "use strict";
  const TABS = ["week", "season", "model", "games"];
  const SPORTS = ["all", "cfb", "nfl"];
  const TIERS = ["strong", "lean", "coinflip", "no_market", "none"];
  const RESULTS = ["all", "win", "loss"];
  const TIER_LABELS = {
    strong: "Strong", lean: "Lean", coinflip: "Coinflip", no_market: "No market", none: "No model",
  };

  function defaults(data) {
    return { week: data.latest_week, tab: "week", sport: "all", tiers: [], result: "all", game: null };
  }

  function parseHash(hash, data) {
    const state = defaults(data);
    const params = new URLSearchParams(String(hash || "").replace(/^#/, ""));
    const week = Number(params.get("week"));
    if (data.standings.some((s) => s.week === week)) state.week = week;
    if (TABS.includes(params.get("tab"))) state.tab = params.get("tab");
    if (SPORTS.includes(params.get("sport"))) state.sport = params.get("sport");
    if (RESULTS.includes(params.get("result"))) state.result = params.get("result");
    const tiers = (params.get("tier") || "").split(",");
    state.tiers = TIERS.filter((t) => tiers.includes(t));
    const id = params.get("game");
    if (id && data.games.some((g) => g.id === id)) state.game = id;
    return state;
  }

  function toHash(state, data) {
    const params = new URLSearchParams();
    if (state.week !== data.latest_week) params.set("week", String(state.week));
    if (state.tab !== "week") params.set("tab", state.tab);
    if (state.sport !== "all") params.set("sport", state.sport);
    if (state.tiers.length) params.set("tier", state.tiers.join(","));
    if (state.result !== "all") params.set("result", state.result);
    if (state.game) params.set("game", state.game);
    const text = params.toString();
    return text ? "#" + text : "";
  }

  function tierOf(game) {
    return game.model ? game.model.tier : "none";
  }

  function applyFilters(games, state) {
    return games.filter((g) =>
      g.week <= state.week &&
      (state.sport === "all" || g.sport === state.sport) &&
      (!state.tiers.length || state.tiers.includes(tierOf(g))) &&
      (state.result === "all" || g.results.us === state.result));
  }

  function confidentLosses(games) {
    return games.filter((g) => g.model && g.model.tier === "strong" && g.results.model === "loss");
  }

  function gradedFor(games, strategy) {
    return games.filter((g) => g.results[strategy] !== null && g.results[strategy] !== undefined);
  }

  function search(games, text) {
    const q = String(text || "").trim().toLowerCase();
    if (!q) return games;
    return games.filter((g) => g.home.toLowerCase().includes(q) || g.away.toLowerCase().includes(q));
  }

  function homeShare(g) {
    const total = g.field_home + g.field_away;
    return total ? g.field_home / total : null;
  }

  const SORT_KEYS = {
    week: (g) => g.week,
    kickoff: (g) => g.kickoff,
    matchup: (g) => g.away + " " + g.home,
    line: (g) => g.line,
    us: (g) => g.results.us,
    model: (g) => g.results.model,
    tier: (g) => TIERS.indexOf(tierOf(g)),
    field: homeShare,
  };

  function sortGames(games, key, dir) {
    const value = SORT_KEYS[key];
    const sign = dir === "desc" ? -1 : 1;
    const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    if (!value) return games.slice().sort(byId);
    return games.slice().sort((a, b) => {
      const x = value(a), y = value(b);
      const xMissing = x === null || x === undefined, yMissing = y === null || y === undefined;
      if (xMissing || yMissing) return xMissing && yMissing ? byId(a, b) : xMissing ? 1 : -1;
      if (x === y) return byId(a, b);
      return (x < y ? -1 : 1) * sign;
    });
  }

  function chips(state) {
    const out = [];
    if (state.sport !== "all") out.push({ key: "sport", label: state.sport.toUpperCase() });
    if (state.tiers.length) out.push({ key: "tiers", label: state.tiers.map((t) => TIER_LABELS[t]).join(" + ") });
    if (state.result !== "all") out.push({ key: "result", label: state.result === "win" ? "Our wins" : "Our losses" });
    return out;
  }

  function clearFilter(state, key) {
    const cleared = { sport: "all", tiers: [], result: "all" };
    return Object.assign({}, state, { [key]: cleared[key] });
  }

  P.filters = {
    TABS, TIERS, TIER_LABELS, defaults, parseHash, toHash, tierOf, applyFilters,
    confidentLosses, gradedFor, search, sortGames, homeShare, chips, clearFilter,
  };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_js.py -q && uv run ruff check tests/test_dashboard_js.py`
Expected: PASS (both the unit suite and the parity test).

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/dashboard_assets/filters.js tests/js tests/test_dashboard_js.py
git commit -m "feat: dashboard filters, address state and drill-down sets, with Python parity"
```

---

### Task 5: scales.js — chart geometry

**Files:**
- Create: `src/pickem/report/dashboard_assets/scales.js`
- Create: `tests/js/scales.test.mjs`

**Interfaces:**
- Produces (`Pickem.scales`):
  - `linear(d0, d1, r0, r1) -> (v) => number` (zero-width domain maps to the range midpoint)
  - `niceMax(values) -> number` (≥ 1; next 1/2/…/10 × power of ten at or above the max)
  - `extent(values, pad) -> [lo, hi]`
  - `rateMark(record, x) -> {x, lo, hi} | null` (null when `record.decided === 0`)

- [ ] **Step 1: Write the failing tests**

`tests/js/scales.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { load, plain } from "./load.mjs";

const P = load("stats.js", "scales.js");
const S = P.scales;

test("linear maps the domain onto the range", () => {
  const x = S.linear(0, 1, 10, 510);
  assert.equal(x(0), 10);
  assert.equal(x(0.5), 260);
  assert.equal(x(1), 510);
  const y = S.linear(0, 20, 150, 10);
  assert.equal(y(20), 10);
});

test("a zero-width domain maps to the middle of the range", () => {
  assert.equal(S.linear(3, 3, 0, 100)(3), 50);
});

test("niceMax rounds up to a readable axis top and never returns zero", () => {
  assert.equal(S.niceMax([13, 7]), 20);
  assert.equal(S.niceMax([31]), 40);
  assert.equal(S.niceMax([0, 0]), 1);
  assert.equal(S.niceMax([]), 1);
  assert.equal(S.niceMax([0.75]), 0.8);
});

test("extent pads both ends and widens a flat series", () => {
  assert.deepEqual(plain(S.extent([-3, -4.5], 0.5)), [-5, -2.5]);
  assert.deepEqual(plain(S.extent([-3, -3], 0.5)), [-3.5, -2.5]);
});

test("rate mark sits on the scale, with whiskers at the interval", () => {
  const x = S.linear(0, 1, 0, 100);
  const record = P.stats.record(
    [...Array(7).fill({ results: { m: "win" } }), ...Array(3).fill({ results: { m: "loss" } })], "m");
  const mark = S.rateMark(record, x);
  assert.ok(Math.abs(mark.x - 70) < 1e-9);
  assert.ok(Math.abs(mark.lo - 39.68) < 0.01 && Math.abs(mark.hi - 89.22) < 0.01);
  assert.equal(S.rateMark(P.stats.record([], "m"), x), null);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_js.py -q`
Expected: FAIL — `scales.js` not found.

- [ ] **Step 3: Implement `scales.js`**

```js
// One linear scale per chart places every mark, whisker, tick and label.
(function (P) {
  "use strict";

  function linear(d0, d1, r0, r1) {
    const span = d1 - d0;
    return (v) => (span === 0 ? (r0 + r1) / 2 : r0 + ((v - d0) / span) * (r1 - r0));
  }

  function niceMax(values) {
    const finite = values.filter((v) => Number.isFinite(v));
    const max = finite.length ? Math.max(...finite) : 0;
    if (max <= 0) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(max)));
    for (const step of [1, 2, 2.5, 4, 5, 6, 8, 10]) {
      const top = Number((step * power).toPrecision(12));
      if (top >= max) return top;
    }
    return 10 * power;
  }

  function extent(values, pad) {
    const lo = Math.min(...values), hi = Math.max(...values);
    return [lo - pad, hi + pad];
  }

  function rateMark(record, x) {
    if (!record.decided) return null;
    return { x: x(record.rate), lo: x(record.interval[0]), hi: x(record.interval[1]) };
  }

  P.scales = { linear, niceMax, extent, rateMark };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_js.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/dashboard_assets/scales.js tests/js/scales.test.mjs
git commit -m "feat: dashboard chart scales"
```

---

### Task 6: Page shell — assembly, forwarders, styles, app boot

**Files:**
- Modify: `src/pickem/report/dashboard.py` (add `SCRIPTS`, `render_dashboard`, `render_week_forwarder`)
- Create: `src/pickem/report/dashboard_assets/app.css`, `ui_core.js`, `app.js`
- Modify: `tests/dashboard_helpers.py` (add `page_fixture`, `rendered_dom`)
- Test: `tests/test_dashboard_page.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - `SCRIPTS: tuple[str, ...]` — inline order. This task: `("stats.js", "filters.js", "scales.js", "ui_core.js", "app.js")`. Later tasks insert their files **before** `"app.js"`.
  - `render_dashboard(report, *, generated_at) -> str`, `render_week_forwarder(pool_week: int) -> str`
  - `Pickem.ui`: `h(tag, attrs, ...children)`, `s(tag, attrs, ...children)`, `card(title, ...children)`, `resultTag(result)`, `LABELS = {strategy, sport}`, `fmt = {spread, sideLine, homeLine, team, kickoff, time, signed, pct}`
  - `Pickem.app`: `{data, state, sort, search, list, tierOpen, set(patch, opts), openGame(id), showList(title, games, strategy), closePanel(), render()}`
  - `Pickem.tabs` — registry `{week, season, model, games}` filled by Tasks 9–11; `Pickem.panel(view)` from Task 8.
  - `view = {data, state, games, weekGames}` passed to every tab and to the panel.
  - Boot marker: `<html data-boot="ok">` after a successful render, `data-boot="error"` plus `<pre id="boot-error">` otherwise.
  - `tests/dashboard_helpers.py`: `page_fixture(tmp_path, report=None) -> Path` and `rendered_dom(page: Path, fragment: str, tmp_path) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/dashboard_helpers.py`:

```python
from pathlib import Path

from results_helpers import KICK as STORE_KICK
from results_helpers import imported_store

from pickem.models import LIVE_SOURCE, PINNACLE_SOURCE, MarketLine, Sport, make_game_id
from pickem.report.dashboard import render_dashboard
from pickem.report.results import build_results_report

CHROME = shutil.which("google-chrome") or shutil.which("chromium")
MODEL_GAME = "cfb-2026-02-PSU-at-TEM"
BARE_GAME = make_game_id(Sport.NFL, 2026, 1, "NE", "SEA")


def fixture_report():
    """The CBS fixture week, with one modelled game that has US and Pinnacle line history."""
    store = imported_store()
    store.append_recommendation_history([
        RecommendationRecord(
            game_id=MODEL_GAME, sport=Sport.CFB, season=2026, week=2, side=side, tier=tier,
            edge_points=edge, generated_at=STORE_KICK - timedelta(hours=hours),
            source=HISTORY_MONITOR,
        )
        for side, tier, edge, hours in [
            (Side.HOME, Tier.LEAN, 1.0, 30), (Side.HOME, Tier.STRONG, 2.5, 6),
            (Side.AWAY, Tier.STRONG, 2.1, 1),
        ]
    ])
    store.append_market_lines([
        MarketLine(game_id=MODEL_GAME, source=source, book=book, spread_home=spread,
                   captured_at=STORE_KICK - timedelta(hours=hours))
        for source, book, spread, hours in [
            (LIVE_SOURCE, "dk", 24.0, 30), (LIVE_SOURCE, "fd", 24.5, 30),
            (LIVE_SOURCE, "dk", 25.5, 2), (PINNACLE_SOURCE, "pinnacle", 25.0, 3),
        ]
    ])
    try:
        return build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    finally:
        store.close()


def page_fixture(tmp_path: Path, report=None) -> Path:
    path = tmp_path / "index.html"
    path.write_text(render_dashboard(report or fixture_report(), generated_at=GENERATED))
    return path


def rendered_dom(page: Path, fragment: str, tmp_path: Path) -> str:
    """The page's DOM after its scripts ran in headless Chrome, opened at ``#fragment``."""
    assert CHROME, "Google Chrome is required for the dashboard page tests"
    result = subprocess.run(
        [
            CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", f"--user-data-dir={tmp_path / 'chrome-profile'}",
            "--virtual-time-budget=3000", "--dump-dom", f"{page.as_uri()}#{fragment}",
        ],
        capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stderr
    dom = result.stdout
    assert 'data-boot="ok"' in dom, dom[-2000:]
    return dom
```

Merge the imports at the top of the file (`HISTORY_MONITOR`, `RecommendationRecord`, `Side`, `Tier` are already imported; add `timedelta` if not yet). Use the store's actual market-line insert method name as found in Task 1.

`tests/test_dashboard_page.py`:

```python
import json
import re

from dashboard_helpers import GENERATED, page_fixture, rendered_dom, synthetic_season
from results_helpers import assert_well_formed

from pickem.report.dashboard import ASSETS, SCRIPTS, render_dashboard, render_week_forwarder


def data_block(page: str) -> dict:
    match = re.search(r'<script type="application/json" id="pickem-data">(.*?)</script>', page, re.S)
    return json.loads(match.group(1))


def test_page_is_one_well_formed_self_contained_document():
    page = render_dashboard(synthetic_season(), generated_at=GENERATED)
    assert page.startswith("<!doctype html>")
    assert_well_formed(page)
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert "@import" not in page and "url(" not in page


def test_every_script_and_the_stylesheet_are_inlined_in_order():
    page = render_dashboard(synthetic_season(), generated_at=GENERATED)
    positions = [page.index((ASSETS / name).read_text(encoding="utf-8")) for name in SCRIPTS]
    assert positions == sorted(positions)
    assert (ASSETS / "app.css").read_text(encoding="utf-8") in page


def test_no_asset_can_close_its_own_tag():
    for name in (*SCRIPTS, "app.css"):
        text = (ASSETS / name).read_text(encoding="utf-8").lower()
        assert "</script" not in text and "</style" not in text, name


def test_data_cannot_break_out_of_its_script_block():
    report = synthetic_season()
    evil = report.season_games[0].game.model_copy(update={"home_team_id": "</script><b>x&y"})
    report.season_games[0].__dict__["game"] = evil  # frozen dataclass: test-only override
    page = render_dashboard(report, generated_at=GENERATED)
    assert "</script><b>" not in page
    assert "\\u003c/script\\u003e\\u003cb\\u003ex\\u0026y" in page
    assert data_block(page)["games"][0]["home"] == "</script><b>x&y"


def test_forwarder_opens_the_page_at_its_week():
    page = render_week_forwarder(3)
    assert_well_formed(page)
    assert 'location.replace("index.html#week=3")' in page
    assert 'href="index.html#week=3"' in page


def test_the_page_boots_in_a_real_browser(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "", tmp_path)
    assert 'role="tablist"' in dom
    assert "Pick'em 2026" in dom
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_page.py -q`
Expected: FAIL — `ImportError: cannot import name 'ASSETS'`.

- [ ] **Step 3: Implement assembly in `dashboard.py`**

Add at the top (after the docstring and `from __future__`):

```python
import html
import json
from pathlib import Path
```

Add after the imports:

```python
ASSETS = Path(__file__).with_name("dashboard_assets")
# Inline order: pure logic first, then helpers, charts, panel and tabs, and app.js last (it boots).
SCRIPTS = ("stats.js", "filters.js", "scales.js", "ui_core.js", "app.js")
```

Add at the end of the module:

```python
def render_dashboard(report: ResultsReport, *, generated_at: datetime) -> str:
    """One self-contained page for the whole season in ``report``."""
    data = build_dashboard_data(report, generated_at=generated_at)
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    js = "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in SCRIPTS)
    title = html.escape(f"Pick'em {report.season}")
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{title}</title><style>{css}</style></head>"
        '<body><div id="app"><noscript>This page needs JavaScript.</noscript></div>'
        f'<script type="application/json" id="pickem-data">{_script_json(data)}</script>'
        f"<script>{js}</script></body></html>\n"
    )


def render_week_forwarder(pool_week: int) -> str:
    """A tiny page that opens the dashboard at ``pool_week``, so old links keep working."""
    target = f"index.html#week={int(pool_week)}"
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Week {int(pool_week)}</title>"
        f'<script>location.replace("{target}")</script></head>'
        f'<body><p><a href="{target}">Open week {int(pool_week)}</a></p></body></html>\n'
    )


def _script_json(data: dict) -> str:
    """JSON that no string inside it can use to close the surrounding script tag."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
```

- [ ] **Step 4: Write `app.css`**

```css
:root{
  --bg:#f4f6f3;--surface:#fff;--raised:#fbfcfb;--ink:#17211c;--muted:#5a675f;--line:#d9e0db;
  --accent:#2f7a55;--accent-ink:#fff;--median:#3b6fb6;--winner:#a8741f;--pin:#8a4fb4;
  --good:#2f7a55;--good-bg:#e1f1e8;--bad:#b4432f;--bad-bg:#f7e2dd;--push-bg:#ecefed;
  --shadow:0 1px 2px rgba(20,30,25,.06),0 4px 16px rgba(20,30,25,.06);color-scheme:light;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#0f1512;--surface:#171f1b;--raised:#1c2521;--ink:#e3eae5;--muted:#97a59d;--line:#2b3631;
    --accent:#6cc497;--accent-ink:#0f1512;--median:#86aee8;--winner:#e0b25c;--pin:#c49be3;
    --good:#6cc497;--good-bg:#1d3a2b;--bad:#e7826d;--bad-bg:#43241d;--push-bg:#2a332f;
    --shadow:none;color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --bg:#0f1512;--surface:#171f1b;--raised:#1c2521;--ink:#e3eae5;--muted:#97a59d;--line:#2b3631;
  --accent:#6cc497;--accent-ink:#0f1512;--median:#86aee8;--winner:#e0b25c;--pin:#c49be3;
  --good:#6cc497;--good-bg:#1d3a2b;--bad:#e7826d;--bad-bg:#43241d;--push-bg:#2a332f;
  --shadow:none;color-scheme:dark;
}
*{box-sizing:border-box}
html,body{overflow-x:hidden}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body.locked{overflow:hidden}
button,input,select{font:inherit;color:inherit}
button{cursor:pointer}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}

/* top bar */
.top{max-width:1100px;margin:0 auto;padding:16px 16px 0}
.title-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.title-row h1{margin:0;font-size:22px;flex:1 1 auto}
.week-pick select,.theme{min-height:44px;border:1px solid var(--line);border-radius:10px;
  background:var(--surface);padding:0 12px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;
  background:var(--surface)}
.seg button{min-height:44px;padding:0 12px;border:0;background:none}
.seg button[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink)}
.tier-pick{position:relative}
.tier-pick summary{list-style:none;min-height:44px;display:flex;align-items:center;padding:0 12px;
  border:1px solid var(--line);border-radius:10px;background:var(--surface);cursor:pointer}
.tier-pick summary::-webkit-details-marker{display:none}
.tier-pick .menu{position:absolute;z-index:5;margin-top:4px;padding:6px;min-width:180px;
  background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow)}
.tier-pick label{display:flex;align-items:center;gap:8px;min-height:40px;padding:0 6px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{display:inline-flex;align-items:center;gap:6px;min-height:32px;padding:0 6px 0 12px;
  border-radius:999px;background:var(--good-bg);border:0}
.chip span{font-size:15px;line-height:1}
.tabs{position:sticky;top:0;z-index:4;display:flex;gap:4px;margin:12px -16px 0;padding:6px 16px;
  background:var(--bg);border-bottom:1px solid var(--line);overflow-x:auto}
.tabs button{min-height:44px;padding:0 14px;border:0;border-radius:10px;background:none;
  color:var(--muted);font-weight:600;white-space:nowrap}
.tabs button[aria-selected="true"]{background:var(--surface);color:var(--ink);box-shadow:var(--shadow)}

/* content */
main.tab{max-width:1100px;margin:0 auto;padding:16px 16px 64px;display:grid;gap:14px}
@media (min-width:900px){main.tab.two{grid-template-columns:1fr 1fr;align-items:start}
  main.tab.two .wide{grid-column:1/-1}}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;
  box-shadow:var(--shadow);min-width:0}
.card h2{margin:0 0 10px;font-size:17px}
.card h3{margin:14px 0 6px;font-size:15px}
.muted,.note{color:var(--muted);font-size:13px}
.empty{color:var(--muted);font-style:italic;margin:6px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 14px;
  box-shadow:var(--shadow);display:flex;flex-direction:column}
.tile .value{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.tile .label{color:var(--muted);font-size:12.5px}
.analysis p{margin:0 0 8px}
.list{list-style:none;margin:0;padding:0}
.list li+li{border-top:1px solid var(--line)}
.row-button{display:flex;width:100%;min-height:44px;align-items:center;gap:8px;padding:8px 2px;
  border:0;background:none;text-align:left}
.row-button:hover{background:var(--raised)}
.row-button .grow{flex:1 1 auto;min-width:0}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;width:100%}
th,td{padding:8px 10px 8px 0;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{color:var(--muted);font-weight:500;font-size:13px}
.linkish{border:0;background:none;padding:0;min-height:44px;color:var(--accent);font-weight:600;text-align:left}
.res{display:inline-block;min-width:24px;padding:1px 6px;border-radius:6px;font-weight:700;
  font-size:12.5px;text-align:center}
.res-win{background:var(--good-bg);color:var(--good)}
.res-loss{background:var(--bad-bg);color:var(--bad)}
.res-push,.res-none{background:var(--push-bg);color:var(--muted)}
.flag{display:inline-block;padding:0 7px;margin-right:4px;border-radius:999px;font-size:12px;
  background:var(--push-bg)}
details.fold summary{cursor:pointer;color:var(--accent);font-weight:600;min-height:44px;
  display:flex;align-items:center}
dl dt{font-weight:600;margin-top:8px}
dl dd{margin:0;color:var(--muted)}
.claims li{margin:4px 0}

/* games table */
.games-head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.games-head h2{margin:0}
.games-head input{min-height:44px;flex:1 1 200px;max-width:320px;padding:0 12px;
  border:1px solid var(--line);border-radius:10px;background:var(--raised)}
table.games{white-space:nowrap;font-size:14px;margin-top:10px}
table.games th button{border:0;background:none;padding:0;min-height:36px;color:inherit;font-weight:inherit}
table.games tbody tr{cursor:pointer}
table.games tbody tr:hover{background:var(--raised)}
.tier{color:var(--muted);font-size:12px;margin-left:4px}

/* charts */
.rates{display:grid;gap:2px}
.rate-row{display:grid;grid-template-columns:minmax(8.5rem,13rem) 1fr;gap:10px;align-items:center;
  width:100%;min-height:48px;padding:4px 2px;border:0;border-radius:8px;background:none;text-align:left}
.rate-row:not(:disabled):hover{background:var(--raised)}
.rate-row:disabled{cursor:default}
.rate-label{font-size:14px;line-height:1.25}
.rate-label small{display:block;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
.rate-axis{display:grid;grid-template-columns:minmax(8.5rem,13rem) 1fr;gap:10px;font-size:12px;
  color:var(--muted)}
.rate-axis .ticks{display:flex;justify-content:space-between}
svg{display:block;width:100%;height:auto;overflow:visible}
.track{stroke:var(--line);stroke-width:8;stroke-linecap:round}
.ref{stroke:var(--muted);stroke-dasharray:3 4;stroke-width:1.5}
.grid{stroke:var(--line);stroke-width:1}
.axis{fill:var(--muted);font-size:15px}
.mark{fill:var(--accent)}
.whisker,.cap{stroke:var(--ink);stroke-width:2}
.expected{stroke:var(--winner);stroke-width:4}
.line{fill:none;stroke-width:3}
.dot{stroke:var(--surface);stroke-width:2}
.s-us{stroke:var(--accent);fill:var(--accent);background:var(--accent)}
.s-median{stroke:var(--median);fill:var(--median);background:var(--median)}
.s-winner{stroke:var(--winner);fill:var(--winner);background:var(--winner)}
.s-pinnacle{stroke:var(--pin);fill:var(--pin);background:var(--pin)}
.s-cbs{stroke:var(--muted);background:var(--muted)}
.line.s-us,.line.s-median,.line.s-winner,.line.s-pinnacle{fill:none}
.dot.s-us,.dot.s-median,.dot.s-winner,.dot.s-pinnacle{stroke:var(--surface)}
.hit{fill:transparent;cursor:pointer}
.hit:focus-visible{outline:none;fill:var(--push-bg);fill-opacity:.6}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:4px 14px;padding:0;margin:8px 0 0;
  font-size:13px;color:var(--muted)}
.key{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
figure{margin:0}
figcaption{color:var(--muted);font-size:13px;margin-bottom:6px}

/* detail panel */
.backdrop{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.35)}
.panel{position:fixed;z-index:21;left:0;right:0;bottom:0;max-height:88vh;display:flex;
  flex-direction:column;background:var(--surface);border-radius:16px 16px 0 0;box-shadow:var(--shadow)}
@media (min-width:900px){
  .backdrop{background:rgba(0,0,0,.15)}
  .panel{left:auto;top:0;width:440px;max-height:none;border-radius:0;border-left:1px solid var(--line)}
}
.panel-head{display:flex;align-items:center;gap:8px;padding:10px 12px 10px 16px;
  border-bottom:1px solid var(--line)}
.panel-head h2{flex:1 1 auto;margin:0;font-size:17px}
.close{min-width:44px;min-height:44px;border:0;border-radius:10px;background:none;font-size:18px}
.panel-body{overflow-y:auto;padding:12px 16px 32px}
.facts{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;margin:8px 0}
.facts dt{margin:0;font-weight:500;color:var(--muted)}
.facts dd{margin:0;color:var(--ink)}
.timeline{list-style:none;margin:0;padding:0}
.timeline li{padding:6px 0;border-bottom:1px solid var(--line);font-size:14px}
.timeline li.changed{font-weight:600}
.badge{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;font-size:11.5px;
  background:var(--bad-bg);color:var(--bad)}
#boot-error{white-space:pre-wrap;color:var(--bad);padding:16px}
```

- [ ] **Step 5: Write `ui_core.js`**

```js
// Element builders and formatters shared by every tab. Data always enters the page as text.
(function (P) {
  "use strict";
  const SVG_NS = "http://www.w3.org/2000/svg";

  function setAttrs(el, attrs) {
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : String(value));
    }
  }

  function append(el, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    setAttrs(el, attrs);
    append(el, children);
    return el;
  }

  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    setAttrs(el, attrs);
    append(el, children);
    return el;
  }

  function card(title, ...children) {
    return h("section", { class: "card" }, title ? h("h2", null, title) : null, children);
  }

  const LABELS = {
    strategy: {
      us: "Us", model: "Model", "first sheet": "First sheet", "close divergence": "Close divergence",
      favorites: "Favorites", home: "Home", "field consensus": "Field consensus",
    },
    sport: { cfb: "CFB", nfl: "NFL" },
  };

  function resultTag(result) {
    const text = { win: "W", loss: "L", push: "P" }[result];
    return text
      ? h("span", { class: "res res-" + result, title: result }, text)
      : h("span", { class: "res res-none", title: "not graded" }, "—");
  }

  const EASTERN = { timeZone: "America/New_York" };
  const kickoffFormat = new Intl.DateTimeFormat("en-US",
    { ...EASTERN, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const timeFormat = new Intl.DateTimeFormat("en-US",
    { ...EASTERN, weekday: "short", hour: "numeric", minute: "2-digit" });

  const fmt = {
    spread(x) {
      if (x === null || x === undefined) return "—";
      if (x === 0) return "PK";
      return (x > 0 ? "+" : "−") + String(Math.abs(x));
    },
    homeLine(g, value) { return `${g.home} ${fmt.spread(value)}`; },
    sideLine(g, side) {
      return side === "away" ? `${g.away} ${fmt.spread(-g.line)}` : `${g.home} ${fmt.spread(g.line)}`;
    },
    team(g, side) { return side === "home" ? g.home : side === "away" ? g.away : "—"; },
    kickoff(iso) { return kickoffFormat.format(new Date(iso)) + " ET"; },
    time(iso) { return timeFormat.format(new Date(iso)) + " ET"; },
    signed(x, digits) {
      if (x === null || x === undefined) return "—";
      const text = Math.abs(x).toFixed(digits);
      return (x > 0 ? "+" : x < 0 ? "−" : "") + text;
    },
    pct(x, digits) { return P.stats.pct(x, digits); },
  };

  P.ui = { h, s, card, LABELS, resultTag, fmt };
  P.tabs = P.tabs || {};
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 6: Write `app.js`**

```js
// App state, the top bar, routing between tabs, and boot.
(function (P) {
  "use strict";
  const { h, LABELS } = P.ui;
  const F = P.filters;
  const TAB_LABELS = { week: "Week", season: "Season", model: "Model", games: "Games" };
  const THEMES = ["auto", "light", "dark"];
  const THEME_KEY = "pickem-theme";

  const app = {
    data: null, state: null, sort: { key: "kickoff", dir: "asc" }, search: "",
    list: null, tierOpen: false, pushedGame: false,
  };
  P.app = app;

  function addressFor(state) {
    return location.pathname + location.search + F.toHash(state, app.data);
  }

  app.set = function (patch, opts) {
    const push = opts && opts.push;
    if (Object.keys(patch).some((k) => k !== "game")) app.list = null;
    app.state = Object.assign({}, app.state, patch);
    if (push) { history.pushState(null, "", addressFor(app.state)); app.pushedGame = true; }
    else history.replaceState(null, "", addressFor(app.state));
    app.render();
  };

  app.openGame = (id) => app.set({ game: id }, { push: true });

  app.showList = function (title, games, strategy) {
    app.list = { title, games, strategy };
    app.render();
  };

  app.closePanel = function () {
    if (app.state.game) {
      if (app.pushedGame) { app.pushedGame = false; history.back(); }
      else app.set({ game: null });
    } else if (app.list) {
      app.list = null;
      app.render();
    }
  };

  function readTheme() {
    try { return THEMES.includes(localStorage.getItem(THEME_KEY)) ? localStorage.getItem(THEME_KEY) : "auto"; }
    catch (e) { return "auto"; }
  }

  function applyTheme(theme) {
    if (theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
  }

  function themeButton() {
    const current = readTheme();
    const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
    return h("button", {
      class: "theme", type: "button", "aria-label": `Theme: ${current}. Switch to ${next}.`,
      onclick: () => {
        try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* storage blocked: this visit only */ }
        applyTheme(next);
        app.render();
      },
    }, { auto: "◐ Auto", light: "☀ Light", dark: "☾ Dark" }[current]);
  }

  function segmented(label, options, value, onPick) {
    return h("div", { class: "seg", role: "group", "aria-label": label },
      options.map(([key, text]) => h("button", {
        type: "button", "aria-pressed": String(value === key), onclick: () => onPick(key),
      }, text)));
  }

  function tierPicker(state) {
    const summary = state.tiers.length ? state.tiers.map((t) => F.TIER_LABELS[t]).join(", ") : "All tiers";
    const details = h("details", { class: "tier-pick", open: app.tierOpen },
      h("summary", null, "Tier: " + summary + " ▾"),
      h("div", { class: "menu" }, F.TIERS.map((tier) => h("label", null,
        h("input", {
          type: "checkbox", checked: state.tiers.includes(tier),
          onchange: (e) => {
            const tiers = e.target.checked
              ? F.TIERS.filter((t) => t === tier || state.tiers.includes(t))
              : state.tiers.filter((t) => t !== tier);
            app.set({ tiers });
          },
        }),
        F.TIER_LABELS[tier]))));
    details.addEventListener("toggle", () => { app.tierOpen = details.open; });
    return details;
  }

  function topBar(view) {
    const { data, state } = view;
    return h("header", { class: "top" },
      h("div", { class: "title-row" },
        h("h1", null, `Pick'em ${data.season}`),
        h("label", { class: "week-pick" }, h("span", { class: "sr" }, "Week"),
          h("select", { onchange: (e) => app.set({ week: Number(e.target.value) }) },
            data.standings.map((s) => h("option", { value: s.week, selected: s.week === state.week }, `Week ${s.week}`)))),
        themeButton()),
      h("div", { class: "filters" },
        segmented("Board", [["all", "Both"], ["cfb", "CFB"], ["nfl", "NFL"]], state.sport, (v) => app.set({ sport: v })),
        tierPicker(state),
        segmented("Result", [["all", "All"], ["win", "Our wins"], ["loss", "Our losses"]], state.result, (v) => app.set({ result: v }))),
      h("div", { class: "chips" }, F.chips(state).map((chip) => h("button", {
        class: "chip", type: "button", "aria-label": `Clear ${chip.label}`,
        onclick: () => app.set(F.clearFilter(state, chip.key)),
      }, chip.label, h("span", { "aria-hidden": "true" }, "✕")))),
      h("nav", { class: "tabs", role: "tablist" }, F.TABS.map((key) => h("button", {
        type: "button", role: "tab", "aria-selected": String(state.tab === key),
        onclick: () => app.set({ tab: key }),
      }, TAB_LABELS[key]))));
  }

  function notBuilt() {
    return [P.ui.card(null, h("p", { class: "empty" }, "This tab is not available."))];
  }

  app.render = function () {
    const root = document.getElementById("app");
    const games = F.applyFilters(app.data.games, app.state);
    const view = {
      data: app.data, state: app.state, games,
      weekGames: games.filter((g) => g.week === app.state.week),
    };
    const tab = P.tabs[app.state.tab] || notBuilt;
    const panel = P.panel ? P.panel(view) : null;
    const hadPanel = Boolean(root.querySelector(".panel"));
    root.replaceChildren(...[
      topBar(view),
      h("main", { class: "tab" + (app.state.tab === "games" ? "" : " two"), id: "tab-" + app.state.tab }, tab(view)),
      panel,
    ].filter(Boolean));
    document.body.classList.toggle("locked", Boolean(panel));
    if (panel && !hadPanel) { const close = root.querySelector(".panel .close"); if (close) close.focus(); }
  };

  function boot() {
    try {
      app.data = JSON.parse(document.getElementById("pickem-data").textContent);
      app.state = F.parseHash(location.hash, app.data);
      applyTheme(readTheme());
      addEventListener("popstate", () => {
        app.pushedGame = false;  // a drill-down list stays open under a closed game
        app.state = F.parseHash(location.hash, app.data);
        app.render();
      });
      addEventListener("keydown", (e) => { if (e.key === "Escape") app.closePanel(); });
      app.render();
      document.documentElement.dataset.boot = "ok";
    } catch (error) {
      document.documentElement.dataset.boot = "error";
      document.getElementById("app").replaceChildren(
        h("pre", { id: "boot-error" }, "The dashboard could not start: " + (error && error.stack || error)));
    }
  }

  boot();
})(globalThis.Pickem = globalThis.Pickem || {});
```

`LABELS` is imported for use by later edits; if ruff-like JS linting is not in place this is harmless, but remove it from the destructuring if it stays unused after Task 11.

- [ ] **Step 7: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_page.py tests/test_dashboard_js.py -q && uv run ruff check src tests`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/pickem/report/dashboard.py src/pickem/report/dashboard_assets tests/dashboard_helpers.py tests/test_dashboard_page.py
git commit -m "feat: self-contained dashboard page shell with top bar, filters and boot"
```

---

### Task 7: Switch the results commands to the new page

**Files:**
- Modify: `src/pickem/cli.py` (imports, `_write_dashboard`)
- Modify: `tests/test_results_cli.py`
- Modify: `tests/test_results_real_pages.py`
- Delete: `src/pickem/report/results_html.py`, `src/pickem/report/charts.py`, `tests/test_results_html.py`, `tests/test_charts.py`
- Modify: `docs/runbooks/dashboard.md`; check `docs/runbooks/verifying-from-logs.md`

**Interfaces:**
- Consumes: `render_dashboard`, `render_week_forwarder` (Task 6); `build_results_report`.
- Produces: `_write_dashboard(store, report, dashboard_dir) -> Path | None` returning the `index.html` path; logs `dashboard_written` with `pool_week`, `path`, `bytes`, `weeks`.

- [ ] **Step 1: Update the CLI tests (failing)**

In `tests/test_results_cli.py`, replace `test_import_writes_the_week_page_and_the_index`, `test_dashboard_defaults_to_the_configured_directory` and `test_rebuilding_an_older_week_leaves_the_index_alone` with:

```python
def test_import_writes_the_page_and_a_forwarder_per_week(workspace, tmp_path):
    db, results = workspace
    dash = tmp_path / "dash"
    result = invoke_import(db, results, "--no-notify", "--dashboard-dir", str(dash))
    assert result.exit_code == 0, result.output
    page = (dash / "index.html").read_text()
    assert page.startswith("<!doctype html>")
    assert 'id="pickem-data"' in page
    assert 'location.replace("index.html#week=2")' in (dash / "week-2.html").read_text()
    assert not list(dash.glob(".*.tmp"))
    assert "dashboard written to" in result.output


def test_dashboard_defaults_to_the_configured_directory(workspace, tmp_path):
    db, results = workspace
    assert invoke_import(db, results, "--no-notify").exit_code == 0
    assert (tmp_path / "dashboard" / "index.html").exists()  # conftest's PICKEM_DASHBOARD_DIR
    assert (tmp_path / "dashboard" / "week-2.html").exists()


def test_rebuilding_any_week_rewrites_the_whole_page(workspace, tmp_path):
    db, results = workspace
    dash = tmp_path / "dash"
    assert invoke_import(db, results, "--no-notify", "--dashboard-dir", str(dash)).exit_code == 0
    (dash / "index.html").write_text("stale")
    result = runner.invoke(app, [
        "results-report", "--season", "2026", "--pool-week", "2", "--db", str(db),
        "--out-dir", str(results), "--dashboard-dir", str(dash),
    ])
    assert result.exit_code == 0, result.output
    assert 'id="pickem-data"' in (dash / "index.html").read_text()
```

Then run `grep -n "render_results_dashboard\|results_html" tests/test_results_cli.py` and repoint any monkeypatch of the old renderer (the render-failure test) at `pickem.cli.render_dashboard`.

In `tests/test_results_real_pages.py`, replace the last test's rendering with the new page, built once from the latest week:

```python
from pickem.report.dashboard import render_dashboard
...
def test_the_real_season_renders_a_complete_page():
    ...
    with store:
        weeks = store.pool_weeks(2026)
        assert weeks, "no imported pool weeks in the live database"
        report = build_results_report(
            store, season=2026, pool_week=weeks[-1], entry_name=DEFAULT_ENTRY_NAME
        )
        assert_well_formed(render_dashboard(report, generated_at=datetime.now(tz=UTC)))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_results_cli.py -q`
Expected: FAIL (`index.html` has no `pickem-data`; forwarder missing).

- [ ] **Step 3: Implement**

In `src/pickem/cli.py` replace `from pickem.report.results_html import render_results_dashboard` with `from pickem.report.dashboard import render_dashboard, render_week_forwarder`, and replace `_write_dashboard` with:

```python
def _write_dashboard(store: Store, report: ResultsReport, dashboard_dir: Path) -> Path | None:
    """Write index.html for the whole season, and a forwarder week-N.html for every week.

    The page always covers the season through the latest imported week, whichever
    week ``report`` is. The import and the Markdown are already saved, so a failure
    here is logged and reported, never raised: the DM still goes out before the
    command exits 3.
    """
    try:
        weeks = store.pool_weeks(report.season)
        latest = report
        if report.pool_week != weeks[-1]:
            latest = build_results_report(
                store, season=report.season, pool_week=weeks[-1], entry_name=report.entry_name
            )
        page = render_dashboard(latest, generated_at=datetime.now(tz=UTC))
        dashboard_dir.mkdir(parents=True, exist_ok=True)
        path = dashboard_dir / "index.html"
        _write_atomic(path, page)
        for week in weeks:
            _write_atomic(dashboard_dir / f"week-{week}.html", render_week_forwarder(week))
    except Exception as exc:  # a render bug or a filesystem error alike must not stop the DM
        logger.bind(
            event="dashboard_write_failed",
            pool_week=report.pool_week,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        ).error(f"dashboard not written: {exc}")
        typer.secho(f"dashboard not written: {exc}", fg="red", err=True)
        return None
    logger.bind(
        event="dashboard_written",
        pool_week=report.pool_week,
        path=str(path),
        bytes=len(page.encode("utf-8")),
        weeks=len(weeks),
    ).info(f"dashboard written to {path}")
    typer.echo(f"dashboard written to {path}")
    return path
```

Confirm `build_results_report` is already imported in `cli.py` (it is used by `_write_results_report`).

Delete the old renderer and its tests:

```bash
git rm src/pickem/report/results_html.py src/pickem/report/charts.py tests/test_results_html.py tests/test_charts.py
```

Run `grep -rn "results_html\|report.charts\|describe_record\|index_updated" src tests docs/runbooks` and fix every remaining hit (for `index_updated` in a runbook query, replace it with the `weeks` field).

Update `docs/runbooks/dashboard.md`:
- Opening paragraph: the job writes one page, `index.html`, covering the season through the latest imported week, plus `week-N.html` for each week, which only forward to `index.html#week=N` so older DM links still land on the right week.
- "Rebuild a page": `uv run pickem results-report --season 2026 --pool-week N` rewrites week N's Markdown and always rebuilds the whole page (`index.html` and every forwarder) from everything imported. Keep the note about the `dashboard not written` failure and the Wednesday retry.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all PASS, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add -A src/pickem/cli.py src/pickem/report tests docs/runbooks
git commit -m "feat: results commands write the interactive page and week forwarders"
```

---

### Task 8: Detail panel — drill-down list and game detail

**Files:**
- Create: `src/pickem/report/dashboard_assets/charts.js` (line-movement chart only; Task 10 adds the rest)
- Create: `src/pickem/report/dashboard_assets/panel.js`
- Modify: `src/pickem/report/dashboard.py` (`SCRIPTS`)
- Test: `tests/test_dashboard_page.py` (append)

**Interfaces:**
- Consumes: `Pickem.ui`, `Pickem.app.closePanel/openGame/list`, `Pickem.scales`.
- Produces:
  - `Pickem.charts.lineMove(game) -> Element`, `Pickem.charts.W = 520`
  - `Pickem.panel(view) -> Element | null`
  - `SCRIPTS = ("stats.js", "filters.js", "scales.js", "ui_core.js", "charts.js", "panel.js", "app.js")`

- [ ] **Step 1: Write the failing boot tests**

Append to `tests/test_dashboard_page.py`:

```python
from dashboard_helpers import BARE_GAME, MODEL_GAME


def test_a_game_opens_in_the_detail_panel_with_its_history(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), f"game={MODEL_GAME}", tmp_path)
    assert 'class="panel"' in dom
    assert "PSU @ TEM" in dom
    assert "The model through the week" in dom
    assert dom.count('class="changed"') == 2  # tier change, then side change
    assert "Line movement" in dom and "<polyline" in dom
    assert "Pinnacle" in dom


def test_a_game_the_model_never_covered_still_opens(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), f"game={BARE_GAME}", tmp_path)
    assert "The model never covered this game." in dom
    assert "No line history" in dom
    assert "Not captured" in dom
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_page.py -q -k "detail_panel or never_covered"`
Expected: FAIL (`class="panel"` not in DOM).

- [ ] **Step 3: Write `charts.js`**

```js
// SVG charts. Text that must stay legible on a phone is HTML beside the drawing.
(function (P) {
  "use strict";
  const { h, s, fmt } = P.ui;
  const W = 520;

  function legend(items) {
    return h("ul", { class: "legend" }, items.map(([key, text]) =>
      h("li", null, h("span", { class: "key s-" + key }), text)));
  }

  function lineMove(g) {
    if (!g.lines.length) return h("p", { class: "empty" }, "No line history");
    const series = [
      ["us", "US books", g.lines.filter((p) => p.source === "us")],
      ["pinnacle", "Pinnacle", g.lines.filter((p) => p.source === "pinnacle")],
    ].filter(([, , points]) => points.length);
    const H = 170, L = 10, R = 10, T = 12, B = 12;
    const times = g.lines.map((p) => Date.parse(p.at));
    const [lo, hi] = P.scales.extent(g.lines.map((p) => p.spread).concat([g.line]), 0.5);
    const x = P.scales.linear(Math.min(...times), Math.max(...times), L, W - R);
    const y = P.scales.linear(lo, hi, T, H - B);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Line movement" },
      s("line", { class: "ref", x1: L, x2: W - R, y1: y(g.line), y2: y(g.line) }),
      series.map(([key, , points]) => [
        points.length > 1 && s("polyline", {
          class: "line s-" + key,
          points: points.map((p) => `${x(Date.parse(p.at))},${y(p.spread)}`).join(" "),
        }),
        points.map((p) => s("circle", { class: "dot s-" + key, cx: x(Date.parse(p.at)), cy: y(p.spread), r: 4.5 },
          s("title", null, `${fmt.time(p.at)}: ${fmt.homeLine(g, p.spread)}`))),
      ]));
    const items = series.map(([key, label, points]) => [key,
      `${label}: opened ${fmt.spread(points[0].spread)}, last ${fmt.spread(points[points.length - 1].spread)}`]);
    items.push(["cbs", `CBS line ${fmt.spread(g.line)} (dashed)`]);
    return h("figure", null,
      h("figcaption", null, `Spreads for ${g.home}; lower means ${g.home} more favored.`),
      svg, legend(items));
  }

  P.charts = { W, legend, lineMove };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 4: Write `panel.js`**

```js
// The detail panel: a drill-down list of games, or one game in full.
(function (P) {
  "use strict";
  const { h, LABELS, resultTag, fmt } = P.ui;
  const TIER = () => P.filters.TIER_LABELS;

  function shell(title, body) {
    return h("div", { class: "panel-wrap" },
      h("div", { class: "backdrop", onclick: () => P.app.closePanel() }),
      h("aside", { class: "panel", role: "dialog", "aria-modal": "true", "aria-label": title },
        h("div", { class: "panel-head" }, h("h2", null, title),
          h("button", { class: "close", type: "button", "aria-label": "Close", onclick: () => P.app.closePanel() }, "✕")),
        h("div", { class: "panel-body" }, body)));
  }

  function gameButton(g, strategy) {
    const side = g.picks[strategy];
    return h("button", { class: "row-button", type: "button", onclick: () => P.app.openGame(g.id) },
      h("span", { class: "grow" },
        `W${g.week} · ${g.away} @ ${g.home}`,
        h("br"),
        h("span", { class: "muted" }, `${LABELS.strategy[strategy]}: ${side ? fmt.sideLine(g, side) : "no pick"}`)),
      resultTag(g.results[strategy]));
  }

  function gameList(list) {
    if (!list.games.length) return h("p", { class: "empty" }, "No games");
    return h("ul", { class: "list" }, list.games.map((g) => h("li", null, gameButton(g, list.strategy))));
  }

  function fact(label, value) {
    return [h("dt", null, label), h("dd", null, value)];
  }

  function timeline(g) {
    if (!g.history.length) return h("p", { class: "empty" }, "The model never covered this game.");
    return h("ol", { class: "timeline" }, g.history.map((r, i) => {
      const prev = g.history[i - 1];
      const changed = prev && (prev.side !== r.side || prev.tier !== r.tier);
      return h("li", { class: changed ? "changed" : null },
        `${fmt.time(r.at)} · ${fmt.team(g, r.side)} · ${TIER()[r.tier]} · edge ${fmt.signed(r.edge, 1)}`,
        changed ? h("span", { class: "badge" }, prev.side !== r.side ? "side changed" : "tier changed") : null);
    }));
  }

  function gameDetail(g, data) {
    return [
      h("p", { class: "muted" }, `${LABELS.sport[g.sport]} · Week ${g.week} · ${fmt.kickoff(g.kickoff)}`),
      h("dl", { class: "facts" },
        fact("Final", `${g.away} ${g.away_score} – ${g.home} ${g.home_score}`),
        fact("CBS line", fmt.homeLine(g, g.line)),
        fact("Closing line", g.close === null ? "Not captured" : fmt.homeLine(g, g.close)),
        fact("Field", `${g.field_away} took ${g.away} · ${g.field_home} took ${g.home}`)),
      h("h3", null, "Every strategy"),
      h("table", null, h("tbody", null, data.strategies.map((st) => h("tr", null,
        h("th", null, LABELS.strategy[st]),
        h("td", null, g.picks[st] ? fmt.sideLine(g, g.picks[st]) : "no pick"),
        h("td", null, resultTag(g.results[st])))))),
      h("h3", null, "The model through the week"),
      timeline(g),
      h("h3", null, "Line movement"),
      P.charts.lineMove(g),
    ];
  }

  P.panel = function (view) {
    const g = view.state.game && view.data.games.find((x) => x.id === view.state.game);
    if (g) return shell(`${g.away} @ ${g.home}`, gameDetail(g, view.data));
    if (P.app.list) return shell(P.app.list.title, gameList(P.app.list));
    return null;
  };
})(globalThis.Pickem = globalThis.Pickem || {});
```

The boot test expects exactly two `class="changed"` entries for the fixture history (lean→strong is a tier change; home→away is a side change).

- [ ] **Step 5: Add the scripts to `SCRIPTS`**

```python
SCRIPTS = (
    "stats.js", "filters.js", "scales.js", "ui_core.js", "charts.js", "panel.js", "app.js",
)
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_page.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/report/dashboard.py src/pickem/report/dashboard_assets tests/test_dashboard_page.py
git commit -m "feat: dashboard detail panel with model timeline and line movement"
```

---

### Task 9: Week tab

**Files:**
- Create: `src/pickem/report/dashboard_assets/tab_week.js`
- Modify: `src/pickem/report/dashboard.py` (`SCRIPTS`: insert `"tab_week.js"` before `"app.js"`)
- Test: `tests/test_dashboard_page.py` (append)

**Interfaces:**
- Consumes: `view`, `Pickem.ui`, `Pickem.stats`, `Pickem.filters`, `Pickem.app.openGame/showList`.
- Produces: `Pickem.tabs.week(view) -> Element[]`.

- [ ] **Step 1: Write the failing boot tests**

```python
def test_week_tab_shows_the_headline_and_every_section(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "", tmp_path)
    assert dom.count('class="tile"') == 4
    assert "No analysis for this week yet." in dom
    assert "Confident picks that lost" in dom
    assert dom.count('class="board-table"') == 2  # both boards played in pool week 2


def test_week_tab_shows_only_boards_with_games(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "sport=nfl", tmp_path)
    assert dom.count('class="board-table"') == 1


def test_week_tab_survives_filters_that_leave_nothing(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tier=lean&result=win", tmp_path)
    assert dom.count('class="tile"') == 4  # standings are not filtered
    assert "No games match the filters." in dom
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_page.py -q -k week_tab`
Expected: FAIL (the fallback "This tab is not available." renders).

- [ ] **Step 3: Write `tab_week.js`**

```js
// Week tab: the headline, the analysis, each board, and the confident picks that lost.
(function (P) {
  "use strict";
  const { h, card, LABELS, resultTag, fmt } = P.ui;

  function tile(value, label) {
    return h("div", { class: "tile" }, h("span", { class: "value" }, value), h("span", { class: "label" }, label));
  }

  function tiles(view, standing) {
    const games = view.data.games.filter((g) => g.week === standing.week).length;
    const gap = standing.winner - standing.points;
    return h("div", { class: "tiles wide" },
      tile(`${standing.points}/${games}`, "our points"),
      tile(`#${standing.rank}`, `of ${standing.entrants} entrants`),
      tile(fmt.signed(standing.points - standing.median, 1), `vs field median (${standing.median})`),
      tile(gap ? String(gap) : "Top", gap ? `behind the winner (${standing.winner})` : "top score this week"));
  }

  function analysis(data, week) {
    const text = data.analysis && data.analysis[String(week)];
    const body = typeof text === "string" && text.trim()
      ? text.trim().split(/\n\s*\n/).map((para) => h("p", null, para))
      : h("p", { class: "empty" }, "No analysis for this week yet.");
    return h("section", { class: "card analysis wide" }, h("h2", null, "Analysis"), body);
  }

  function boards(standing) {
    return card("By board", h("ul", { class: "list" }, standing.boards.map((b) => h("li", null,
      h("div", { class: "row-button" },
        h("strong", null, LABELS.sport[b.sport]),
        h("span", { class: "grow" }, ` ${b.points} pts · median ${b.median} · best ${b.best}`))))));
  }

  function confidentLosses(view) {
    const games = P.filters.confidentLosses(view.weekGames);
    const body = games.length
      ? h("ul", { class: "list" }, games.map((g) => h("li", null,
          h("button", { class: "row-button", type: "button", onclick: () => P.app.openGame(g.id) },
            h("span", { class: "grow" }, fmt.sideLine(g, g.model.side),
              h("br"), h("span", { class: "muted" }, `Final ${g.away} ${g.away_score}–${g.home_score} ${g.home} · edge ${fmt.signed(g.model.edge, 1)}`)),
            resultTag("loss")))))
      : h("p", { class: "empty" }, "None");
    return card("Confident picks that lost", h("p", { class: "note" }, "Strong-tier model picks that lost this week."), body);
  }

  function boardTables(view) {
    const sports = ["cfb", "nfl"].filter((sp) => view.weekGames.some((g) => g.sport === sp));
    if (!sports.length) return [card("This week", h("p", { class: "empty" }, "No games match the filters."))];
    return sports.map((sport) => {
      const games = view.weekGames.filter((g) => g.sport === sport);
      return card(`${LABELS.sport[sport]} this week`,
        h("div", { class: "scroll" }, h("table", { class: "board-table" }, h("tbody", null,
          view.data.week_strategies.map((st) => {
            const record = P.stats.record(games, st);
            const graded = P.filters.gradedFor(games, st);
            return h("tr", null,
              h("th", null, graded.length
                ? h("button", { class: "linkish", type: "button",
                    onclick: () => P.app.showList(`${LABELS.sport[sport]} week ${view.state.week} · ${LABELS.strategy[st]}`, graded, st) },
                    LABELS.strategy[st])
                : LABELS.strategy[st]),
              h("td", null, P.stats.recordText(record)));
          })))));
    });
  }

  P.tabs.week = function (view) {
    const standing = view.data.standings.find((s) => s.week === view.state.week);
    return [tiles(view, standing), analysis(view.data, view.state.week), boards(standing),
      confidentLosses(view), ...boardTables(view)];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 4: Insert `"tab_week.js"` before `"app.js"` in `SCRIPTS`**

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_page.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/report/dashboard.py src/pickem/report/dashboard_assets/tab_week.js tests/test_dashboard_page.py
git commit -m "feat: dashboard Week tab"
```

---

### Task 10: Season and Model tabs, with rate rows and trend charts

**Files:**
- Modify: `src/pickem/report/dashboard_assets/charts.js` (add `rateRows`, `trend`)
- Create: `src/pickem/report/dashboard_assets/tab_season.js`, `tab_model.js`
- Modify: `src/pickem/report/dashboard.py` (`SCRIPTS`: insert `"tab_season.js", "tab_model.js"` before `"app.js"`)
- Test: `tests/test_dashboard_page.py` (append)

**Interfaces:**
- Consumes: `Pickem.charts.W/legend`, `Pickem.scales`, `Pickem.stats`, `Pickem.filters`, `Pickem.app`.
- Produces:
  - `Pickem.charts.rateRows(rows, onPick) -> Element`, where `row = {label, note?, record, expected?, games, strategy}`
  - `Pickem.charts.trend(weeks, series, opts) -> Element`, `series = [{key, label, values}]`, `opts = {caption, label, format, max?, onPick(week)}`
  - `Pickem.tabs.season`, `Pickem.tabs.model`

- [ ] **Step 1: Write the failing boot tests**

```python
def test_season_tab_draws_both_trends(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=season", tmp_path)
    assert dom.count('<figure class="trend"') == 2
    assert "<polyline" not in dom  # one imported week: points, no lines


def test_season_trends_draw_lines_across_weeks(tmp_path):
    page = page_fixture(tmp_path, synthetic_season())
    dom = rendered_dom(page, "tab=season", tmp_path)
    assert dom.count("<polyline") == 4  # us, median, winner; and share beaten


def test_model_tab_has_tiers_baselines_clv_findings_and_glossary(tmp_path):
    page = page_fixture(tmp_path, synthetic_season())
    dom = rendered_dom(page, "tab=model", tmp_path)
    assert dom.count('class="rate-row"') == 6 + 7  # 2 boards × 3 tiers, us + 6 baselines
    assert "NFL backtest" in dom
    assert "Closing-line value" in dom
    assert "What the data says" in dom
    assert "What the terms mean" in dom


def test_model_tier_rows_follow_the_board_filter(tmp_path):
    page = page_fixture(tmp_path, synthetic_season())
    dom = rendered_dom(page, "tab=model&sport=cfb", tmp_path)
    assert dom.count('class="rate-row"') == 3 + 7
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_page.py -q -k "season or model"`
Expected: FAIL.

- [ ] **Step 3: Add `rateRows` and `trend` to `charts.js`**

Inside the `charts.js` IIFE, before `P.charts = ...`:

```js
  function rateRows(rows, onPick) {
    if (!rows.length) return h("p", { class: "empty" }, "No games yet");
    const RH = 28, x = P.scales.linear(0, 1, 8, W - 8);
    return h("div", { class: "rates" },
      h("div", { class: "rate-axis", "aria-hidden": "true" }, h("span"),
        h("span", { class: "ticks" }, h("span", null, "0%"), h("span", null, "50%"), h("span", null, "100%"))),
      rows.map((row) => {
        const mark = P.scales.rateMark(row.record, x);
        const svg = s("svg", { viewBox: `0 0 ${W} ${RH}`, "aria-hidden": "true" },
          s("line", { class: "track", x1: x(0), x2: x(1), y1: RH / 2, y2: RH / 2 }),
          s("line", { class: "ref", x1: x(0.5), x2: x(0.5), y1: 2, y2: RH - 2 }),
          row.expected !== undefined && row.expected !== null &&
            s("line", { class: "expected", x1: x(row.expected), x2: x(row.expected), y1: 4, y2: RH - 4 }),
          mark && [
            s("line", { class: "whisker", x1: mark.lo, x2: mark.hi, y1: RH / 2, y2: RH / 2 }),
            s("line", { class: "cap", x1: mark.lo, x2: mark.lo, y1: RH / 2 - 6, y2: RH / 2 + 6 }),
            s("line", { class: "cap", x1: mark.hi, x2: mark.hi, y1: RH / 2 - 6, y2: RH / 2 + 6 }),
            s("circle", { class: "mark", cx: mark.x, cy: RH / 2, r: 7 }),
          ]);
        return h("button", {
          class: "rate-row", type: "button", disabled: !row.games.length,
          "aria-label": `${row.label}: ${P.stats.recordText(row.record)}. Show games.`,
          onclick: () => onPick(row),
        },
          h("span", { class: "rate-label" }, row.label,
            h("small", null, P.stats.recordText(row.record), row.note ? ` · ${row.note}` : "")),
          svg);
      }));
  }

  function trend(weeks, series, opts) {
    if (!weeks.length) return h("p", { class: "empty" }, "No games yet");
    const H = 190, L = 44, R = 14, T = 12, B = 30;
    const x = weeks.length === 1 ? () => (L + W - R) / 2 : P.scales.linear(weeks[0], weeks[weeks.length - 1], L, W - R);
    const max = opts.max || P.scales.niceMax(series.flatMap((se) => se.values));
    const y = P.scales.linear(0, max, H - B, T);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label },
      [0, max / 2, max].map((v) => [
        s("line", { class: "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }),
        s("text", { class: "axis", x: L - 8, y: y(v) + 5, "text-anchor": "end" }, opts.format(v)),
      ]),
      weeks.map((w) => s("text", { class: "axis", x: x(w), y: H - 8, "text-anchor": "middle" }, `W${w}`)),
      series.slice().reverse().map((se) => [
        weeks.length > 1 && s("polyline", {
          class: "line s-" + se.key, points: weeks.map((w, i) => `${x(w)},${y(se.values[i])}`).join(" "),
        }),
        weeks.map((w, i) => s("circle", { class: "dot s-" + se.key, cx: x(w), cy: y(se.values[i]), r: 5 })),
      ]),
      weeks.map((w, i) => s("rect", {
        class: "hit", x: x(w) - 16, y: T, width: 32, height: H - B - T, tabindex: 0, role: "button",
        "aria-label": `Week ${w}: ` + series.map((se) => `${se.label} ${opts.format(se.values[i])}`).join(", "),
        onclick: () => opts.onPick(w),
        onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); opts.onPick(w); } },
      }, s("title", null, `Week ${w}: ` + series.map((se) => `${se.label} ${opts.format(se.values[i])}`).join(", ")))));
    return h("figure", { class: "trend" }, h("figcaption", null, opts.caption), svg,
      legend(series.map((se) => [se.key, se.label])));
  }
```

and export them: `P.charts = { W, legend, lineMove, rateRows, trend };`

- [ ] **Step 4: Write `tab_season.js`**

```js
// Season tab: how we have stood against the field, week by week, through the selected week.
(function (P) {
  "use strict";
  const { h, card, fmt } = P.ui;

  P.tabs.season = function (view) {
    const standings = view.data.standings.filter((s) => s.week <= view.state.week);
    const weeks = standings.map((s) => s.week);
    const pick = (week) => P.app.set({ week, tab: "week" });
    return [
      card("Points by week", P.charts.trend(weeks, [
        { key: "us", label: "Us", values: standings.map((s) => s.points) },
        { key: "median", label: "Field median", values: standings.map((s) => s.median) },
        { key: "winner", label: "Winner", values: standings.map((s) => s.winner) },
      ], { caption: "Tap a week to open it.", label: "Points by week", format: (v) => String(Math.round(v)), onPick: pick })),
      card("Share of the field we beat", P.charts.trend(weeks, [
        { key: "us", label: "Beaten", values: standings.map((s) => s.beat_share) },
      ], { caption: "50% is the middle of the pool.", label: "Share of the field beaten", max: 1,
        format: (v) => fmt.pct(v, 0), onPick: pick })),
      h("p", { class: "note wide" }, "Standings are whole-week facts; the filters do not change them."),
    ];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 5: Write `tab_model.js`**

```js
// Model tab: tier hit rates, us against baselines, closing-line value, findings, glossary.
(function (P) {
  "use strict";
  const { h, card, LABELS, fmt } = P.ui;
  const F = P.filters;
  const TIERS = ["strong", "lean", "coinflip"];

  function drill(prefix) {
    return (row) => P.app.showList(`${prefix}${row.label}: ${P.stats.recordText(row.record)}`, row.games, row.strategy);
  }

  function tierRows(view) {
    const sports = ["cfb", "nfl"].filter((sp) => view.games.some((g) => g.sport === sp));
    return sports.flatMap((sport) => TIERS.map((tier) => {
      const games = view.games.filter((g) => g.sport === sport && F.tierOf(g) === tier);
      const expected = view.data.backtest[tier];
      return {
        label: `${LABELS.sport[sport]} ${F.TIER_LABELS[tier].toLowerCase()}`,
        note: `${sport === "cfb" ? "NFL backtest" : "backtest"} ${fmt.pct(expected, 1)}`,
        record: P.stats.record(games, "model"), expected,
        games: F.gradedFor(games, "model"), strategy: "model",
      };
    }));
  }

  function baselineRows(view) {
    return ["us", ...view.data.baselines].map((st) => ({
      label: LABELS.strategy[st], record: P.stats.record(view.games, st),
      games: F.gradedFor(view.games, st), strategy: st,
    }));
  }

  function tile(value, label) {
    return h("div", { class: "tile" }, h("span", { class: "value" }, value), h("span", { class: "label" }, label));
  }

  function clv(view) {
    const summary = P.stats.clvSummary(view.games);
    const against = view.games.filter((g) => g.against_field);
    const interval = summary.interval
      ? `[${fmt.signed(summary.interval[0], 2)}, ${fmt.signed(summary.interval[1], 2)}]` : "no interval yet";
    return card("Closing-line value",
      h("p", { class: "note" }, "Points the market moved toward our pick after CBS froze its line. Positive is good even when the game lost."),
      h("div", { class: "tiles" },
        tile(summary.n ? fmt.signed(summary.mean, 2) : "—", `mean pts ${interval}`),
        tile(fmt.pct(summary.positive, 0), "picks the line moved toward"),
        tile(String(summary.n), "picks with a close"),
        tile(P.stats.record(against, "us").decided ? fmt.pct(P.stats.record(against, "us").rate, 0) : "—",
          `against the field: ${P.stats.recordText(P.stats.record(against, "us"))}`)));
  }

  function findingsCard(view) {
    const found = view.data.findings[String(view.state.week)] || { claims: [], not_yet: [] };
    return card("What the data says",
      h("p", { class: "note" }, `All games through week ${view.state.week}; the filters do not apply here.`),
      found.claims.length ? h("ul", { class: "claims" }, found.claims.map((c) => h("li", null, c)))
        : h("p", { class: "empty" }, "No difference is clear yet."),
      found.not_yet.length ? h("details", { class: "fold" }, h("summary", null, "Not distinguishable yet"),
        h("ul", { class: "claims" }, found.not_yet.map((c) => h("li", null, c)))) : null);
  }

  function glossary(data) {
    return h("section", { class: "card wide" }, h("details", { class: "fold" },
      h("summary", null, "What the terms mean"),
      h("p", null, data.glossary_intro),
      h("dl", null, data.strategies.map((st) => [h("dt", null, LABELS.strategy[st]), h("dd", null, data.definitions[st])]))));
  }

  P.tabs.model = function (view) {
    return [
      card("Model by tier", h("p", { class: "note" }, "Dot: win rate. Whisker: 95% range. Gold tick: backtest. Tap a row for its games."),
        P.charts.rateRows(tierRows(view), drill("Model · "))),
      card("Us against baselines", P.charts.rateRows(baselineRows(view), drill(""))),
      clv(view),
      findingsCard(view),
      glossary(view.data),
    ];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 6: Update `SCRIPTS`** to `("stats.js", "filters.js", "scales.js", "ui_core.js", "charts.js", "panel.js", "tab_week.js", "tab_season.js", "tab_model.js", "app.js")`.

- [ ] **Step 7: Run to verify pass**

Run: `uv run pytest tests/test_dashboard_page.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/pickem/report/dashboard.py src/pickem/report/dashboard_assets tests/test_dashboard_page.py
git commit -m "feat: dashboard Season and Model tabs with tappable charts"
```

---

### Task 11: Games tab

**Files:**
- Create: `src/pickem/report/dashboard_assets/tab_games.js`
- Modify: `src/pickem/report/dashboard.py` (`SCRIPTS`: insert `"tab_games.js"` before `"app.js"`)
- Test: `tests/test_dashboard_page.py` (append)

**Interfaces:**
- Consumes: `Pickem.filters.search/sortGames/tierOf/homeShare`, `Pickem.app.sort/search/openGame`.
- Produces: `Pickem.tabs.games(view) -> Element[]`.

- [ ] **Step 1: Write the failing boot tests**

```python
def test_games_tab_lists_every_game(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=games", tmp_path)
    assert '<table class="games"' in dom
    assert dom.count('<tr class="game-row"') == 4
    assert 'aria-sort="ascending"' in dom  # kickoff, by default


def test_games_tab_with_no_matches_says_so(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=games&tier=lean", tmp_path)
    assert "No games match" in dom
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_dashboard_page.py -q -k games_tab`
Expected: FAIL.

- [ ] **Step 3: Write `tab_games.js`**

```js
// Games tab: every game within the filters, searchable and sortable; a row opens the game.
(function (P) {
  "use strict";
  const { h, resultTag, fmt } = P.ui;
  const F = P.filters;
  const COLUMNS = [
    ["week", "Wk"], ["kickoff", "Kickoff"], ["matchup", "Matchup"], ["line", "Line"], [null, "Final"],
    ["us", "Us"], ["model", "Model"], ["field", "Field"], [null, "Flags"],
  ];

  function header(key, label, redraw) {
    if (!key) return h("th", { scope: "col" }, label);
    const sort = P.app.sort;
    const active = sort.key === key;
    return h("th", { scope: "col", "aria-sort": active ? (sort.dir === "asc" ? "ascending" : "descending") : null },
      h("button", { type: "button", onclick: () => {
        P.app.sort = { key, dir: active && sort.dir === "asc" ? "desc" : "asc" };
        redraw();
      } }, label + (active ? (sort.dir === "asc" ? " ▲" : " ▼") : "")));
  }

  function row(g) {
    const share = F.homeShare(g);
    const disagreed = g.picks.us && g.picks.model && g.picks.us !== g.picks.model;
    return h("tr", {
      class: "game-row", tabindex: 0, onclick: () => P.app.openGame(g.id),
      onkeydown: (e) => { if (e.key === "Enter") P.app.openGame(g.id); },
    },
      h("td", null, g.week),
      h("td", null, fmt.kickoff(g.kickoff)),
      h("td", null, `${g.away} @ ${g.home}`),
      h("td", null, fmt.homeLine(g, g.line)),
      h("td", null, `${g.away_score}–${g.home_score}`),
      h("td", null, fmt.team(g, g.picks.us), " ", resultTag(g.results.us)),
      h("td", null, fmt.team(g, g.picks.model),
        g.model ? h("span", { class: "tier" }, F.TIER_LABELS[g.model.tier]) : null, " ", resultTag(g.results.model)),
      h("td", null, share === null ? "—" : `${fmt.pct(share, 0)} ${g.home}`),
      h("td", null,
        disagreed ? h("span", { class: "flag" }, "vs model") : null,
        g.against_field ? h("span", { class: "flag" }, "vs field") : null));
  }

  function table(view, redraw) {
    const games = F.sortGames(F.search(view.games, P.app.search), P.app.sort.key, P.app.sort.dir);
    if (!games.length) return h("p", { class: "empty" }, "No games match");
    return h("table", { class: "games" },
      h("thead", null, h("tr", null, COLUMNS.map(([key, label]) => header(key, label, redraw)))),
      h("tbody", null, games.map(row)));
  }

  P.tabs.games = function (view) {
    const wrap = h("div", { class: "scroll" });
    const redraw = () => wrap.replaceChildren(table(view, redraw));
    redraw();
    return [h("section", { class: "card" },
      h("div", { class: "games-head" }, h("h2", null, `Games through week ${view.state.week}`),
        h("input", {
          type: "search", placeholder: "Search teams", "aria-label": "Search teams", value: P.app.search,
          oninput: (e) => { P.app.search = e.target.value; redraw(); },
        })),
      wrap)];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
```

- [ ] **Step 4: Update `SCRIPTS`** to insert `"tab_games.js"` after `"tab_model.js"`.

- [ ] **Step 5: Remove the fallback-tab code path's dead import.** In `app.js`, drop `LABELS` from `const { h, LABELS } = P.ui;` if it is unused.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/report/dashboard.py src/pickem/report/dashboard_assets tests/test_dashboard_page.py
git commit -m "feat: dashboard Games tab with search and sorting"
```

---

### Task 12: Look at it, then rebuild the live page

**Files:**
- Modify (styling only, if the check finds problems): `src/pickem/report/dashboard_assets/app.css`

- [ ] **Step 1: Render the real season to the scratchpad**

```bash
PICKEM_LOG_DIR=<scratchpad>/logs uv run pickem results-report --season 2026 \
  --out-dir <scratchpad>/results --dashboard-dir <scratchpad>/dash
```

Expected: `dashboard written to <scratchpad>/dash/index.html`.

- [ ] **Step 2: Screenshot every view at phone and laptop width, light and dark**

For each fragment in `""`, `tab=season`, `tab=model`, `tab=games`, `game=<an id from week 4 with line history>`, and each size `390,1600` and `1280,1000`:

```bash
google-chrome --headless=new --disable-gpu --user-data-dir=<scratchpad>/chrome \
  --hide-scrollbars --window-size=390,1600 --virtual-time-budget=3000 \
  --screenshot=<scratchpad>/shots/week-phone.png "file://<scratchpad>/dash/index.html#"
```

Repeat with `--force-dark-mode` for the dark set. Open each PNG and check: no sideways scrolling, tap targets full height, chart labels legible at 390 px, dark theme has no light-on-light text, panel docks right at 1280 and slides up at 390. Fix any problems in `app.css` only, then re-render.

- [ ] **Step 3: Send the screenshots to the owner** (phone and laptop Week tab, Model tab, and a game panel), for a look before shipping.

- [ ] **Step 4: Commit any styling fixes**

```bash
git add src/pickem/report/dashboard_assets/app.css
git commit -m "style: dashboard fixes from the render check"
```

- [ ] **Step 5: Hand off to shipping.** Rebuilding the live page (`uv run pickem results-report --season 2026` into the default directory) is part of the `ship` checklist after merging, because the change touches the results dashboard.
