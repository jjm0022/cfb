# "This Week" Picks Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "This week" tab to the results dashboard that lists the current week's matchups and picks, opens a detail panel with a spread-movement chart per game, shows team logos, is rebuilt after every bot refresh, and is linked from the bot's status and "Recommendations Updated" messages.

**Architecture:**
- A new read-only builder turns the stored week (games, CBS lines, market quotes, recommendation history) into JSON for the page.
- One shared publisher writes the whole page: results through the latest imported week, this week's board, and the list of saved logos. The bot, the week-start script and the Tuesday results job all call it.
- The front end gets one new tab file. The existing detail panel and line chart are reused, extended with pick-change markers and logos.

**Tech Stack:** Python 3 (Typer CLI, DuckDB store, loguru, httpx, discord.py), vanilla JS with no libraries (hand-rolled SVG), pytest, Node's test runner, headless Chrome DOM tests.

**Spec:** `docs/superpowers/specs/2026-10-08-this-week-page-design.md`

## Global Constraints

- The page makes no outside requests. Logos are files in `<dashboard dir>/logos/<sport>/<TEAM>.png` (and `<TEAM>-dark.png`), referenced by relative path. The test asserting no `http(s)://` in `src`/`href` must keep passing.
- Discord messages change only by one final link field, `[📊 Open this week's picks](<PICKEM_DASHBOARD_URL>#tab=thisweek)`. It is added to the status embed, the "Recommendations Updated" DM, and the `/refresh` reply when it is titled "Recommendations Updated". With `PICKEM_DASHBOARD_URL` unset, the messages are byte-for-byte unchanged.
- A failed page rebuild never blocks or delays a Discord message, and never fails the week-start job. It is logged as `dashboard_write_failed` with a `trigger` field (`bot`, `week-start`, `results`, or `manual`). There is no DM for it.
- Tier badges match Discord exactly: `🔥 Strong`, `✅ Lean`, `🎯 Slight`, `🪙 Coinflip`, `⚠️ No market`.
- Pool week N = CFB week N + NFL week N-1. "This week" is the latest pool week with a CBS line for either league.
- A pending game shows the pick the bot's refresh would make now. A locked game (kickoff ≤ now) shows the last recommendation recorded before kickoff.
- Old links keep working: `week-N.html` forwarders and `#week=N` still open the results tab when results exist.
- Styling gets a render check (phone width, light and dark), not new tests (owner preference). Logic is TDD.
- Checks: `uv run pytest -q` and `uv run ruff check .` pass after every task.
- Don't reference commit hashes in docs or messages.

## Review Focus

1. **A quote captured after kickoff, e.g. a poll that ran late.** Expected: it is ignored by both the pick and the chart, so a locked game's line stops where the pick locked. Pinned in Task 1.
2. **The database is locked by the Tuesday results job while the bot rebuilds.** Expected: the bot logs `dashboard_write_failed` (trigger `bot`) and its reply or DM still goes out. Pinned in Task 8.
3. **Only one board posted so far, and pool week 1 (no NFL board).** CBS posts the boards on different days. Expected: the tab shows just the board that exists. Pinned in Task 1, plus the board-filter DOM test in Task 5.
4. **A status embed already at Discord's 25-field limit.** Adding a field would make Discord reject the whole message. Expected: the link is skipped. Pinned in Task 8.
5. **Old links after the default tab changes.** Expected: `index.html#week=N` and the bare page still open the results tab when results exist. A page with no results yet opens "This week". Pinned in Task 5.

---

## File Structure

- Create `src/pickem/report/this_week.py`: builds this week's board as JSON from the store. Read-only.
- Create `src/pickem/report/publish.py`: the one routine that writes the page and forwarders, plus the atomic write moved from the CLI.
- Create `src/pickem/ingest/logos.py`: logo sources (CFBD, ESPN), download, and the listing of saved logos.
- Create `src/pickem/report/dashboard_assets/tab_thisweek.js`: the tab's list and the detail panel for this week's games.
- Modify:
  - `src/pickem/report/dashboard.py`: data and render take an optional report, this week's board and logos. Two helpers are made public.
  - `src/pickem/store/db.py`: `latest_league_season()`.
  - `src/pickem/resolve/resolver.py`: `team_ids(sport)`.
  - `src/pickem/ingest/cfbd_source.py`: `fetch_fbs_teams()`.
  - `src/pickem/cli.py`: the results path delegates to the publisher; new `publish-dashboard` and `fetch-logos` commands.
  - `src/pickem/discord_bot.py`: the link field, and a rebuild after every refresh.
  - `scripts/start-week.sh`: a rebuild after the polls.
  - JS: `filters.js` (tabs, routing, this-week filter, pick changes), `ui_core.js` (logo and matchup helpers), `app.js` (tab label, top bar, empty-results guard), `panel.js` (delegation, logos, exported helpers), `charts.js` (change markers), `tab_games.js` (logos). Also `app.css`.
  - `docs/runbooks/dashboard.md`.
- Tests:
  - New: `tests/test_this_week.py`, `tests/test_publish.py`, `tests/test_logos.py`, `tests/js/thisweek.test.mjs`.
  - Extended: `tests/test_dashboard_data.py`, `tests/test_dashboard_page.py`, `tests/dashboard_helpers.py`, `tests/js/filters.test.mjs`, `tests/test_results_cli.py`, `tests/test_discord_bot.py`, `tests/test_start_week_script.py`.

---

### Task 1: This week's board as data

**Files:**
- Create: `src/pickem/report/this_week.py`
- Modify: `src/pickem/report/dashboard.py` (rename `_iso` → `iso_utc` and `_recommendation` → `recommendation_json`, updating their callers in that file)
- Test: `tests/test_this_week.py`

**Interfaces:**
- Consumes: `Store.load_week`, `Store.games_before`, `Store.recommendation_history`, `Store.pool_week_last_kickoffs`, `decide_edges`, `LIVE_THRESHOLDS`, `line_history`, `last_before`, `US_LINE`, `PINNACLE_LINE`, `key_number_crossed`, `grade_pick`.
- Produces:
  - `build_this_week(store: Store, season: int, now: datetime) -> dict | None`
    - Returns `{"pool_week": int, "games": [game, ...]}` in kickoff order, or `None` when no CBS line is stored for `season`.
    - Each game: `id, sport, week, kickoff, home, away, home_score, away_score, line, locked, final, model, result, market: {us, pinnacle}, gap, key_number, history, lines`.
    - `model` is `None` or `{at, side, tier, edge, rationale}`, where `rationale` may be `None`.
    - `history` items are `{at, side, tier, edge}`. `lines` items are `{at, source: "us"|"pinnacle", spread}`.
  - `pickem.report.dashboard.iso_utc(moment) -> str` and `recommendation_json(record) -> dict` (public renames).

- [ ] **Step 1: Rename the two dashboard helpers**

In `src/pickem/report/dashboard.py`, rename `_iso` to `iso_utc` and `_recommendation` to `recommendation_json`, including every call inside the file. Run `uv run pytest -q tests/test_dashboard_data.py tests/test_dashboard_page.py`. Expected: PASS.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_this_week.py
"""This week's board: which week, which pick, and what the chart draws."""

from datetime import UTC, datetime, timedelta

import pytest

from pickem.models import (
    LIVE_SOURCE, PINNACLE_SOURCE, Game, LeagueLine, MarketLine, RecommendationRecord,
    Side, Sport, Tier, make_game_id,
)
from pickem.operations.recommendations import generate_recommendations
from pickem.report.this_week import build_this_week
from pickem.store.db import Store

KICK = datetime(2026, 10, 10, 16, tzinfo=UTC)  # Saturday noon ET
NOW = KICK - timedelta(hours=3)


def game(sport, week, away, home, kickoff, **scores) -> Game:
    return Game(
        game_id=make_game_id(sport, 2026, week, away, home), sport=sport, season=2026,
        week=week, kickoff_utc=kickoff, home_team_id=home, away_team_id=away, **scores,
    )


PENDING = game(Sport.CFB, 6, "OU", "MICH", KICK)
LOCKED = game(Sport.CFB, 6, "PSU", "TEM", KICK - timedelta(hours=4))
FINAL = game(Sport.CFB, 6, "AF", "ARMY", KICK - timedelta(days=2), home_score=24, away_score=17)
NFL = game(Sport.NFL, 5, "BUF", "MIA", KICK + timedelta(days=1))
OLDER = game(Sport.CFB, 5, "OU", "TEM", KICK - timedelta(days=7), home_score=10, away_score=20)
LINES = {PENDING: -3.0, LOCKED: 7.0, FINAL: -3.5, NFL: -3.5, OLDER: 1.5}


def quote(g, book, spread, at, source=LIVE_SOURCE) -> MarketLine:
    return MarketLine(game_id=g.game_id, source=source, book=book, spread_home=spread, captured_at=at)


def record(g, side, tier, edge, at) -> RecommendationRecord:
    return RecommendationRecord(
        game_id=g.game_id, sport=g.sport, season=2026, week=g.week, side=side, tier=tier,
        edge_points=edge, generated_at=at, source="monitor",
    )


QUOTES = [
    quote(PENDING, "dk", -6.0, NOW - timedelta(hours=1)),
    quote(PENDING, "fd", -6.0, NOW - timedelta(hours=1)),
    quote(PENDING, "pinnacle", -5.5, NOW - timedelta(hours=1), PINNACLE_SOURCE),
    quote(LOCKED, "dk", 5.5, LOCKED.kickoff_utc - timedelta(hours=2)),
    quote(LOCKED, "dk", -10.0, LOCKED.kickoff_utc + timedelta(hours=1)),  # after kickoff: ignored
    quote(FINAL, "dk", -7.0, FINAL.kickoff_utc - timedelta(hours=3)),
    quote(NFL, "dk", -3.0, NOW - timedelta(hours=1)),
]
HISTORY = [
    record(PENDING, Side.AWAY, Tier.LEAN, -1.5, NOW - timedelta(hours=30)),
    record(PENDING, Side.HOME, Tier.STRONG, 3.0, NOW - timedelta(hours=1)),
    record(LOCKED, Side.HOME, Tier.LEAN, 1.5, LOCKED.kickoff_utc - timedelta(hours=2)),
    record(FINAL, Side.HOME, Tier.STRONG, 3.5, FINAL.kickoff_utc - timedelta(hours=1)),
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pickem.duckdb"
    with Store(path) as store:
        store.init_schema()
        store.upsert_games(list(LINES))
        store.upsert_league_lines([
            LeagueLine(game_id=g.game_id, season=2026, week=g.week, spread_home=spread,
                       posted_at=KICK - timedelta(days=4))
            for g, spread in LINES.items()
        ])
        store.append_market_lines(QUOTES)
        store.append_recommendation_history(HISTORY)
    return path


def board(db, now=NOW) -> dict:
    with Store(db) as store:
        return build_this_week(store, 2026, now)


def by_id(board_data) -> dict:
    return {g["id"]: g for g in board_data["games"]}


def test_the_latest_pool_week_with_both_boards_in_kickoff_order(db):
    data = board(db)
    assert data["pool_week"] == 6
    assert [g["id"] for g in data["games"]] == [
        FINAL.game_id, LOCKED.game_id, PENDING.game_id, NFL.game_id,
    ]


def test_a_pending_pick_is_what_the_bot_would_pick_now(db):
    snapshots = [
        generate_recommendations(db, sport, 2026, week, NOW, pending_as_of=NOW)
        for sport, week in ((Sport.CFB, 6), (Sport.NFL, 5))
    ]
    expected = {e.game_id: e for s in snapshots for e in s.edges}
    games = by_id(board(db))
    for g in (PENDING, NFL):
        model, edge = games[g.game_id]["model"], expected[g.game_id]
        assert (model["side"], model["tier"], model["edge"], model["rationale"]) == (
            edge.side.value, edge.tier.value, edge.delta, edge.rationale)
    assert games[PENDING.game_id]["model"]["tier"] == "strong"
    assert games[PENDING.game_id]["locked"] is False


def test_a_locked_game_shows_the_pick_recorded_before_kickoff(db):
    locked = by_id(board(db))[LOCKED.game_id]
    assert locked["locked"] is True
    assert (locked["model"]["side"], locked["model"]["tier"], locked["model"]["edge"]) == (
        "home", "lean", 1.5)
    assert locked["model"]["rationale"] == "league +7.0 vs market +5.5: 1.5 pts toward home"


def test_a_locked_pick_that_disagrees_with_the_recompute_has_no_rationale(db):
    with Store(db) as store:
        store.append_recommendation_history([
            record(LOCKED, Side.AWAY, Tier.LEAN, -1.5, LOCKED.kickoff_utc - timedelta(minutes=5)),
        ])
    locked = by_id(board(db))[LOCKED.game_id]
    assert locked["model"]["side"] == "away"
    assert locked["model"]["rationale"] is None


def test_a_locked_game_the_model_never_covered_has_no_pick(db):
    later = KICK + timedelta(days=2)  # every game, the NFL one included, has kicked off
    nfl = by_id(board(db, now=later))[NFL.game_id]
    assert nfl["locked"] is True and nfl["model"] is None


def test_a_final_game_is_graded_against_the_cbs_line(db):
    final = by_id(board(db))[FINAL.game_id]
    assert final["final"] is True
    assert final["result"] == "win"  # home by 7 covers -3.5
    assert by_id(board(db))[PENDING.game_id]["result"] is None


def test_lines_stop_at_kickoff_and_give_the_market_now(db):
    games = by_id(board(db))
    locked = games[LOCKED.game_id]
    assert [p["spread"] for p in locked["lines"]] == [5.5]
    pending = games[PENDING.game_id]
    assert {p["source"] for p in pending["lines"]} == {"us", "pinnacle"}
    assert pending["market"] == {"us": -6.0, "pinnacle": -5.5}
    assert pending["gap"] == 3.0
    assert [h["tier"] for h in pending["history"]] == ["lean", "strong"]


def test_a_slight_pick_names_the_key_number_it_crosses(db):
    nfl = by_id(board(db))[NFL.game_id]
    assert nfl["model"]["tier"] == "slight"
    assert nfl["key_number"] == 3
    assert by_id(board(db))[PENDING.game_id]["key_number"] is None


def test_pool_week_one_has_only_the_cfb_board(tmp_path):
    path = tmp_path / "w1.duckdb"
    first = game(Sport.CFB, 1, "OU", "MICH", KICK)
    with Store(path) as store:
        store.init_schema()
        store.upsert_games([first])
        store.upsert_league_lines([LeagueLine(game_id=first.game_id, season=2026, week=1,
                                              spread_home=-3.0, posted_at=KICK)])
        data = build_this_week(store, 2026, NOW)
    assert data["pool_week"] == 1
    assert [g["id"] for g in data["games"]] == [first.game_id]
    assert data["games"][0]["model"]["tier"] == "no_market"


def test_a_cbs_line_without_its_game_is_left_off(db):
    with Store(db) as store:
        store.upsert_league_lines([LeagueLine(game_id="cfb-2026-06-X-at-Y", season=2026, week=6,
                                              spread_home=1.0, posted_at=KICK)])
        data = build_this_week(store, 2026, NOW)
    assert "cfb-2026-06-X-at-Y" not in by_id(data)
    assert len(data["games"]) == 4


def test_nothing_stored_is_none(tmp_path):
    with Store(tmp_path / "empty.duckdb") as store:
        store.init_schema()
        assert build_this_week(store, 2026, NOW) is None


def test_building_the_board_logs_no_pick_decisions(db, records):
    board(db)
    assert not [r for r in records if r["extra"].get("event") in ("edge_decided", "edge_measured")]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_this_week.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.report.this_week'`.

- [ ] **Step 4: Write the implementation**

```python
# src/pickem/report/this_week.py
"""This week's board: every game of the current pool week, its pick, and how its line moved.

Read-only. A game not yet started carries the pick the bot's refresh would make
now, computed the same way. A game already kicked off carries the last pick
recorded before kickoff, the one that counted. Market quotes after a game's
kickoff are ignored everywhere, so the chart and the pick stop where the pick
locked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from pickem.backtest.stats import grade_pick
from pickem.edge.divergence import suppress_decision_logging
from pickem.edge.key_numbers import key_number_crossed
from pickem.edge.pipeline import decide_edges
from pickem.models import (
    PINNACLE_SOURCE, Edge, Game, MarketLine, RecommendationRecord, Side, Sport, Tier,
)
from pickem.operations.recommendations import LIVE_THRESHOLDS
from pickem.report.dashboard import iso_utc, recommendation_json
from pickem.report.results import PINNACLE_LINE, US_LINE, LinePoint, last_before, line_history
from pickem.store.db import Store


def build_this_week(store: Store, season: int, now: datetime) -> dict | None:
    """The latest pool week's games on both boards in kickoff order; None before any board."""
    weeks = store.pool_week_last_kickoffs(season)
    if not weeks:
        return None
    pool_week = max(weeks)
    games: list[dict] = []
    for sport, week in ((Sport.CFB, pool_week), (Sport.NFL, pool_week - 1)):
        if week >= 1:
            games.extend(_board(store, sport, season, week, now))
    games.sort(key=lambda g: (g["kickoff"], g["id"]))
    return {"pool_week": pool_week, "games": games}


def _board(store: Store, sport: Sport, season: int, week: int, now: datetime) -> list[dict]:
    dataset = store.load_week(sport, season, week)
    games = {game.game_id: game for game in dataset.games}
    # load_week joins CBS lines to their games, so every line here has its game.
    league = dataset.league_lines
    if not league:
        return []
    quotes: dict[str, list[MarketLine]] = defaultdict(list)
    for line in dataset.market_lines:
        if line.game_id in games and line.captured_at < games[line.game_id].kickoff_utc:
            quotes[line.game_id].append(line)
    with suppress_decision_logging():
        edges = decide_edges(
            league,
            # The same inputs as the bot's refresh: Pinnacle is recorded, not used.
            [q for lines in quotes.values() for q in lines if q.source != PINNACLE_SOURCE],
            dataset.games,
            store.games_before(sport, season, week),
            LIVE_THRESHOLDS[sport],
        )
    by_game = {edge.game_id: edge for edge in edges}
    history: dict[str, list[RecommendationRecord]] = defaultdict(list)
    for record in store.recommendation_history([line.game_id for line in league]):
        history[record.game_id].append(record)
    return [
        _game(games[line.game_id], line.spread_home, by_game.get(line.game_id),
              history[line.game_id], quotes[line.game_id], now)
        for line in league
    ]


def _game(
    game: Game,
    league_spread: float,
    edge: Edge | None,
    history: Sequence[RecommendationRecord],
    quotes: Sequence[MarketLine],
    now: datetime,
) -> dict:
    locked = game.kickoff_utc <= now
    final = game.home_score is not None and game.away_score is not None
    model = _locked_pick(edge, history, game.kickoff_utc) if locked else _live_pick(edge, now)
    points = line_history(quotes, game.kickoff_utc)
    us, pinnacle = _last(points, US_LINE), _last(points, PINNACLE_LINE)
    shown = sorted(
        (r for r in history if r.generated_at < game.kickoff_utc),
        key=lambda r: (r.generated_at, r.source),
    )
    return {
        "id": game.game_id,
        "sport": game.sport.value,
        "week": game.week,
        "kickoff": iso_utc(game.kickoff_utc),
        "home": game.home_team_id,
        "away": game.away_team_id,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "line": league_spread,
        "locked": locked,
        "final": final,
        "model": model,
        "result": _result(model, game, league_spread) if final else None,
        "market": {"us": us, "pinnacle": pinnacle},
        "gap": None if us is None else round(league_spread - us, 2),
        "key_number": _key_number(model, league_spread),
        "history": [recommendation_json(r) for r in shown],
        "lines": [
            {"at": iso_utc(p.captured_at), "source": p.source, "spread": p.spread_home}
            for p in points
        ],
    }


def _live_pick(edge: Edge | None, now: datetime) -> dict | None:
    if edge is None:
        return None
    return {"at": iso_utc(now), "side": edge.side.value, "tier": edge.tier.value,
            "edge": edge.delta, "rationale": edge.rationale}


def _locked_pick(
    edge: Edge | None, history: Sequence[RecommendationRecord], kickoff: datetime
) -> dict | None:
    record = last_before(history, kickoff)
    if record is None:
        return None
    # The recompute's reason only explains the recorded pick when they agree.
    agrees = edge is not None and edge.side is record.side and edge.tier is record.tier
    pick = recommendation_json(record)
    pick["rationale"] = edge.rationale if agrees else None
    return pick


def _last(points: Sequence[LinePoint], source: str) -> float | None:
    matching = [p.spread_home for p in points if p.source == source]
    return matching[-1] if matching else None


def _result(model: dict | None, game: Game, league_spread: float) -> str | None:
    if model is None:
        return None
    margin = game.home_score - game.away_score
    return grade_pick(Side(model["side"]), margin, league_spread).value


def _key_number(model: dict | None, league_spread: float) -> int | None:
    if model is None or model["tier"] != Tier.SLIGHT.value:
        return None
    return key_number_crossed(league_spread, league_spread - model["edge"])
```

`line_history` returns points sorted by time, so `_last` reads the latest. Its US series uses only live quotes. `recommendation_json` returns `{at, side, tier, edge}`, which `_locked_pick` extends with `rationale`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_this_week.py -q`
Expected: PASS. If `test_a_pending_pick_is_what_the_bot_would_pick_now` fails on the rationale, compare the inputs with `generate_recommendations` in `src/pickem/operations/recommendations.py`. They must match it, minus the pending filter, which does not change a pending game's edge.

- [ ] **Step 6: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/report/this_week.py src/pickem/report/dashboard.py tests/test_this_week.py
git commit -m "feat: build this week's board as dashboard data"
```

---

### Task 2: The page data takes this week's board and logos

**Files:**
- Modify: `src/pickem/report/dashboard.py`
- Modify: `tests/dashboard_helpers.py` (move `data_block` here from `tests/test_dashboard_page.py`; add the this-week fixture)
- Test: `tests/test_dashboard_data.py`, `tests/test_dashboard_page.py`

**Interfaces:**
- Produces:
  - `build_dashboard_data(report: ResultsReport | None, *, generated_at: datetime, season: int | None = None, this_week: dict | None = None, logos: dict | None = None) -> dict`. It adds `"this_week"` and `"logos"` keys. With `report=None`: `entry` and `latest_week` are `None`, and `standings`, `games` and `findings` are empty.
  - `render_dashboard(report: ResultsReport | None, *, generated_at, season=None, this_week=None, logos=None) -> str`.
  - `NO_LOGOS = {"light": {"cfb": [], "nfl": []}, "dark": {"cfb": [], "nfl": []}}`. This is the logo-list shape Task 3 produces.
  - In `tests/dashboard_helpers.py`:
    - `data_block(page: str) -> dict`
    - `this_week_fixture() -> dict`
    - `LOGOS_FIXTURE: dict`
    - `TW_STRONG`, `TW_SLIGHT`, `TW_LOCKED`, `TW_NO_MODEL`: game ids.
    - `page_fixture(tmp_path, report=None, **render_kwargs) -> Path`

- [ ] **Step 1: Add the fixture and move `data_block`**

Move `data_block` from `tests/test_dashboard_page.py` into `tests/dashboard_helpers.py` (it needs `import json`), and import it back in `test_dashboard_page.py`. Make `page_fixture` forward keyword arguments:

```python
def page_fixture(tmp_path: Path, report=None, **render_kwargs) -> Path:
    path = tmp_path / "index.html"
    if report is None and "season" not in render_kwargs:
        report = fixture_report()
    path.write_text(render_dashboard(report, generated_at=GENERATED, **render_kwargs))
    return path
```

Add the this-week fixture. It has one game in every state the page draws:

```python
TW_KICK = datetime(2026, 10, 10, 16, tzinfo=UTC)
TW_STRONG = "cfb-2026-06-OU-at-MICH"
TW_SLIGHT = "nfl-2026-05-BUF-at-MIA"
TW_LOCKED = "cfb-2026-06-PSU-at-TEM"
TW_NO_MODEL = "nfl-2026-05-NE-at-SEA"
LOGOS_FIXTURE = {"light": {"cfb": ["MICH", "OU"], "nfl": ["BUF"]}, "dark": {"cfb": ["OU"], "nfl": []}}


def this_week_fixture() -> dict:
    """This week's board as the publisher writes it: one game in each state the page draws."""
    def at(hours: float) -> str:
        return (TW_KICK - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")

    def rec(hours, side, tier, edge):
        return {"at": at(hours), "side": side, "tier": tier, "edge": edge}

    def us(hours, spread):
        return {"at": at(hours), "source": "us", "spread": spread}

    def base(game_id, sport, week, away, home, kickoff_hours, line, **rest):
        return {"id": game_id, "sport": sport, "week": week, "kickoff": at(kickoff_hours),
                "home": home, "away": away, "home_score": None, "away_score": None,
                "line": line, "locked": False, "final": False, "model": None, "result": None,
                "market": {"us": None, "pinnacle": None}, "gap": None, "key_number": None,
                "history": [], "lines": [], **rest}

    return {"pool_week": 6, "games": [
        base("cfb-2026-06-AF-at-ARMY", "cfb", 6, "AF", "ARMY", 48, -3.5,
             home_score=24, away_score=17, locked=True, final=True, result="win",
             model={**rec(49, "home", "strong", 3.5),
                    "rationale": "league -3.5 vs market -7.0: 3.5 pts toward home"},
             market={"us": -7.0, "pinnacle": None}, gap=3.5,
             history=[rec(49, "home", "strong", 3.5)], lines=[us(50, -7.0)]),
        base(TW_LOCKED, "cfb", 6, "PSU", "TEM", 4, 7.0, locked=True,
             model={**rec(6, "home", "lean", 1.5), "rationale": None},
             market={"us": 5.5, "pinnacle": None}, gap=1.5,
             history=[rec(6, "home", "lean", 1.5)], lines=[us(6, 5.5)]),
        base(TW_STRONG, "cfb", 6, "OU", "MICH", 0, -3.0,
             model={**rec(3, "home", "strong", 3.0),
                    "rationale": "league -3.0 vs market -6.0: 3.0 pts toward home"},
             market={"us": -6.0, "pinnacle": -5.5}, gap=3.0,
             history=[rec(80, "away", "lean", -1.5), rec(30, "home", "lean", 1.5),
                      rec(6, "home", "strong", 3.0)],
             lines=[us(81, -4.0), us(31, -5.0), {"at": at(31), "source": "pinnacle", "spread": -5.5},
                    us(7, -6.0)]),
        base(TW_SLIGHT, "nfl", 5, "BUF", "MIA", -24, -3.5,
             model={**rec(2, "away", "slight", -0.5),
                    "rationale": "league -3.5 vs market -3.0: 0.5 pts toward away; crosses key number 3"},
             market={"us": -3.0, "pinnacle": None}, gap=-0.5, key_number=3,
             history=[rec(2, "away", "slight", -0.5)], lines=[us(3, -3.0)]),
        base(TW_NO_MODEL, "nfl", 5, "NE", "SEA", -27, 2.5),
    ]}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_dashboard_data.py`:

```python
from dashboard_helpers import LOGOS_FIXTURE, data_block, this_week_fixture  # merge into existing imports

from pickem.report.dashboard import NO_LOGOS, render_dashboard  # merge into existing imports


def test_this_weeks_board_and_logos_ride_along():
    data = build_dashboard_data(synthetic_season(), generated_at=GENERATED,
                                this_week=this_week_fixture(), logos=LOGOS_FIXTURE)
    assert data["this_week"]["pool_week"] == 6
    assert data["logos"] == LOGOS_FIXTURE


def test_without_them_the_page_still_has_both_keys():
    data = build_dashboard_data(synthetic_season(), generated_at=GENERATED)
    assert data["this_week"] is None
    assert data["logos"] == NO_LOGOS


def test_before_any_import_the_page_has_only_this_week():
    data = build_dashboard_data(None, generated_at=GENERATED, season=2026,
                                this_week=this_week_fixture())
    assert data["season"] == 2026
    assert data["latest_week"] is None and data["entry"] is None
    assert data["standings"] == [] and data["games"] == [] and data["findings"] == {}
    assert len(data["this_week"]["games"]) == 5


def test_a_page_with_no_report_needs_its_season():
    with pytest.raises(ValueError, match="season"):
        build_dashboard_data(None, generated_at=GENERATED)


def test_a_page_with_no_report_renders_self_contained():
    page = render_dashboard(None, generated_at=GENERATED, season=2026, this_week=this_week_fixture())
    assert "<title>Pick'em 2026</title>" in page
    assert data_block(page)["this_week"]["pool_week"] == 6
```

(If `pytest` is not yet imported in that file, add `import pytest`.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_data.py -q`
Expected: FAIL. `build_dashboard_data()` gets an unexpected keyword argument `this_week`, and `NO_LOGOS` fails to import.

- [ ] **Step 4: Implement**

In `src/pickem/report/dashboard.py`:

```python
# The saved-logo listing, by theme then board: team ids with a file under logos/<sport>/.
NO_LOGOS = {"light": {"cfb": [], "nfl": []}, "dark": {"cfb": [], "nfl": []}}


def build_dashboard_data(
    report: ResultsReport | None,
    *,
    generated_at: datetime,
    season: int | None = None,
    this_week: dict | None = None,
    logos: dict | None = None,
) -> dict:
    """Everything the page shows, as plain JSON-ready values.

    ``report`` is None before the season's first results import; the page then
    carries only this week's board, and ``season`` must say which season it is.
    """
    if report is None and season is None:
        raise ValueError("a page with no results report needs its season")
    return {
        "season": report.season if report is not None else season,
        "entry": report.entry_name if report is not None else None,
        "latest_week": report.pool_week if report is not None else None,
        "generated_at": iso_utc(generated_at),
        "strategies": [s.value for s in Strategy],
        "baselines": [s.value for s in BASELINES],
        "week_strategies": [s.value for s in WEEK_STRATEGIES],
        "definitions": {s.value: text for s, text in STRATEGY_DEFINITIONS.items()},
        "glossary_intro": GLOSSARY_INTRO,
        "backtest": {tier.value: rate for tier, rate in BACKTEST_TIER_RATES.items()},
        "standings": [] if report is None else [_standing(s) for s in report.standings],
        "findings": {} if report is None else {
            str(s.pool_week): _findings(report, s.pool_week) for s in report.standings
        },
        "games": [] if report is None else [_game(g) for g in report.season_games],
        "analysis": None,
        "this_week": this_week,
        "logos": logos if logos is not None else NO_LOGOS,
    }


def render_dashboard(
    report: ResultsReport | None,
    *,
    generated_at: datetime,
    season: int | None = None,
    this_week: dict | None = None,
    logos: dict | None = None,
) -> str:
    """One self-contained page: the season's results, and this week's board."""
    data = build_dashboard_data(
        report, generated_at=generated_at, season=season, this_week=this_week, logos=logos
    )
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    js = "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in SCRIPTS)
    title = html.escape(f"Pick'em {data['season']}")
    # ...the return statement is unchanged
```

Update the module docstring's first line to: `"""The interactive dashboard: the season's results and this week's board, and the one page that draws them.`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dashboard_data.py tests/test_dashboard_page.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/report/dashboard.py tests/dashboard_helpers.py tests/test_dashboard_data.py tests/test_dashboard_page.py
git commit -m "feat: carry this week's board and logos in the dashboard data"
```

---

### Task 3: Team logos: sources, download and listing

**Files:**
- Create: `src/pickem/ingest/logos.py`
- Modify: `src/pickem/resolve/resolver.py` (add `team_ids`), `src/pickem/ingest/cfbd_source.py` (add `fetch_fbs_teams`), `src/pickem/cli.py` (add `fetch-logos`)
- Test: `tests/test_logos.py`

**Interfaces:**
- Produces:
  - `TeamResolver.team_ids(sport: Sport) -> list[str]`, sorted.
  - `fetch_fbs_teams(config: CfbdConfig, season: int) -> list[dict]`, from CFBD `/teams/fbs?year=`.
  - In `pickem.ingest.logos`:
    - `LOGO_DIR = "logos"`
    - `LogoSource(sport, team_id, url, dark=False)`, with `.relative_path` returning `"<sport>/<TEAM>[-dark].png"`
    - `cfb_sources(teams: list[dict], resolver: TeamResolver) -> tuple[list[LogoSource], list[str]]`. The second item is the CFBD schools with no alias.
    - `nfl_sources(team_ids: Iterable[str]) -> list[LogoSource]`
    - `LogoDownload(saved: list[str], kept: list[str], failed: list[str])`
    - `download_logos(sources, logo_dir: Path, fetch: Callable[[str], bytes]) -> LogoDownload`
    - `available_logos(logo_dir: Path) -> dict`, in the `NO_LOGOS` shape from Task 2.
    - `fetch_logo(url: str) -> bytes`, the default fetcher.
  - CLI: `pickem fetch-logos [--season YEAR] [--dashboard-dir DIR]`. It exits 1 if any download failed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_logos.py
"""Logo sources, the one-time download, and the listing the page reads."""

import pytest
from typer.testing import CliRunner

from pickem.cli import app
from pickem.ingest.logos import (
    LOGO_DIR, LogoSource, available_logos, cfb_sources, download_logos, nfl_sources,
)
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
CDN = "https://cdn.collegefootballdata.com"


def cfbd_team(school: str, number: int) -> dict:
    sizes = ("500", "96", "64")
    return {"school": school, "logos": [
        *(f"{CDN}/logos/{size}/{number}.png" for size in sizes),
        *(f"{CDN}/logos-dark/{size}/{number}.png" for size in sizes),
    ]}


def test_cfb_logos_match_through_the_alias_table():
    sources, unmatched = cfb_sources(
        [cfbd_team("Air Force", 2005), cfbd_team("Nowhere State", 1)], TeamResolver.default())
    assert [(s.team_id, s.url, s.dark) for s in sources] == [
        ("AF", f"{CDN}/logos/96/2005.png", False),
        ("AF", f"{CDN}/logos-dark/96/2005.png", True),
    ]
    assert unmatched == ["Nowhere State"]


def test_a_cfbd_team_with_no_logos_is_skipped():
    sources, unmatched = cfb_sources([{"school": "Air Force", "logos": None}], TeamResolver.default())
    assert sources == [] and unmatched == []


def test_nfl_logos_use_espn_codes_with_washingtons_exception():
    urls = {s.team_id: s.url for s in nfl_sources(["BUF", "WAS"])}
    assert urls == {
        "BUF": "https://a.espncdn.com/i/teamlogos/nfl/500/buf.png",
        "WAS": "https://a.espncdn.com/i/teamlogos/nfl/500/wsh.png",
    }


def test_every_nfl_team_has_a_logo_source():
    assert len(nfl_sources(TeamResolver.default().team_ids(Sport.NFL))) == 32


def test_download_saves_new_keeps_existing_and_reports_failures(tmp_path):
    (tmp_path / "nfl").mkdir()
    (tmp_path / "nfl" / "BUF.png").write_bytes(PNG)
    sources = [
        LogoSource(Sport.NFL, "BUF", "u/buf"),
        LogoSource(Sport.CFB, "AF", "u/af"),
        LogoSource(Sport.CFB, "AF", "u/af-dark", dark=True),
        LogoSource(Sport.NFL, "MIA", "u/broken"),
        LogoSource(Sport.NFL, "NE", "u/html"),
    ]
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url == "u/broken":
            raise OSError("timed out")
        return b"<html>not found</html>" if url == "u/html" else PNG

    outcome = download_logos(sources, tmp_path, fetch)
    assert outcome.saved == ["cfb/AF.png", "cfb/AF-dark.png"]
    assert outcome.kept == ["nfl/BUF.png"]
    assert outcome.failed == ["nfl/MIA.png: timed out", "nfl/NE.png: not a PNG image"]
    assert "u/buf" not in fetched
    assert (tmp_path / "cfb" / "AF-dark.png").read_bytes() == PNG
    assert not (tmp_path / "nfl" / "NE.png").exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_available_logos_lists_saved_files_by_theme_and_board(tmp_path):
    for name in ("cfb/AF.png", "cfb/AF-dark.png", "cfb/OU.png", "nfl/BUF.png", "nfl/notes.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(PNG)
    assert available_logos(tmp_path) == {
        "light": {"cfb": ["AF", "OU"], "nfl": ["BUF"]},
        "dark": {"cfb": ["AF"], "nfl": []},
    }


def test_no_logo_folder_lists_nothing(tmp_path):
    assert available_logos(tmp_path / "missing") == {
        "light": {"cfb": [], "nfl": []}, "dark": {"cfb": [], "nfl": []},
    }


def test_fetch_logos_command_fills_the_dashboard_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("pickem.cli.fetch_fbs_teams", lambda config, season: [cfbd_team("Air Force", 2005)])
    monkeypatch.setattr("pickem.cli.fetch_logo", lambda url: PNG)
    monkeypatch.setenv("CFBD_API_KEY", "test-key")
    result = CliRunner().invoke(app, ["fetch-logos", "--season", "2026", "--dashboard-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / LOGO_DIR / "cfb" / "AF.png").exists()
    assert (tmp_path / LOGO_DIR / "nfl" / "WAS.png").exists()
    assert "saved 34" in result.output  # AF light + dark, and 32 NFL teams


def test_fetch_logos_exits_1_when_any_download_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("pickem.cli.fetch_fbs_teams", lambda config, season: [])

    def fetch(url):
        raise OSError("offline")

    monkeypatch.setattr("pickem.cli.fetch_logo", fetch)
    monkeypatch.setenv("CFBD_API_KEY", "test-key")
    result = CliRunner().invoke(app, ["fetch-logos", "--season", "2026", "--dashboard-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "nfl/BUF.png: offline" in result.output
```

Check how `config.cfbd_api_key()` reads its key (env var name). If it isn't `CFBD_API_KEY`, use the name it reads.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_logos.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.ingest.logos'`.

- [ ] **Step 3: Implement**

`src/pickem/resolve/resolver.py`, a method on `TeamResolver`:

```python
    def team_ids(self, sport: Sport) -> list[str]:
        """Every canonical team id for ``sport``, sorted."""
        return sorted(self._display_names.get(sport, {}))
```

`src/pickem/ingest/cfbd_source.py`, after `_get`:

```python
def fetch_fbs_teams(config: CfbdConfig, season: int) -> list[dict]:
    """Every FBS team CFBD lists for ``season``, with its logo addresses."""
    return _get(config, "/teams/fbs", {"year": season})
```

`src/pickem/ingest/logos.py`:

```python
"""Team logos for the dashboard, downloaded once and served beside the page.

College logos come from CFBD's team list, which also has versions drawn for
dark backgrounds. NFL logos come from ESPN's logo address. The page never
fetches a logo from outside: it only names files listed here as saved.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

LOGO_DIR = "logos"
CFBD_SIZE = "96"  # pixels; sharp at the page's 28-44px on a phone screen
ESPN_NFL = "https://a.espncdn.com/i/teamlogos/nfl/500/{code}.png"
# Every other NFL id is ESPN's code in lower case.
ESPN_CODES = {"WAS": "wsh"}
PNG_SIGNATURE = b"\x89PNG"


@dataclass(frozen=True)
class LogoSource:
    sport: Sport
    team_id: str
    url: str
    dark: bool = False

    @property
    def relative_path(self) -> str:
        suffix = "-dark" if self.dark else ""
        return f"{self.sport.value}/{self.team_id}{suffix}.png"


@dataclass
class LogoDownload:
    saved: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def cfb_sources(
    teams: list[dict], resolver: TeamResolver
) -> tuple[list[LogoSource], list[str]]:
    """Light and dark sources for every CFBD team the alias table knows, and the schools it does not."""
    sources: list[LogoSource] = []
    unmatched: list[str] = []
    for team in teams:
        try:
            team_id = resolver.resolve(team["school"], Sport.CFB)
        except UnknownTeamError:
            unmatched.append(team["school"])
            continue
        logos = team.get("logos") or []
        for dark, folder in ((False, "logos"), (True, "logos-dark")):
            url = next((u for u in logos if f"/{folder}/{CFBD_SIZE}/" in u), None)
            if url is not None:
                sources.append(LogoSource(Sport.CFB, team_id, url, dark))
    return sources, unmatched


def nfl_sources(team_ids: Iterable[str]) -> list[LogoSource]:
    return [
        LogoSource(Sport.NFL, team_id, ESPN_NFL.format(code=ESPN_CODES.get(team_id, team_id.lower())))
        for team_id in team_ids
    ]


def download_logos(
    sources: Iterable[LogoSource], logo_dir: Path, fetch: Callable[[str], bytes]
) -> LogoDownload:
    """Save each missing logo; keep any already saved. One failure never stops the rest."""
    outcome = LogoDownload()
    for source in sources:
        path = logo_dir / source.relative_path
        if path.exists():
            outcome.kept.append(source.relative_path)
            continue
        try:
            data = fetch(source.url)
            if not data.startswith(PNG_SIGNATURE):
                raise ValueError("not a PNG image")
            _write_bytes_atomic(path, data)
        except Exception as exc:  # a missing or broken logo only costs that one team
            outcome.failed.append(f"{source.relative_path}: {exc}")
            continue
        outcome.saved.append(source.relative_path)
    return outcome


def available_logos(logo_dir: Path) -> dict:
    """Team ids with a saved logo, by theme then board, in the shape the page reads."""
    listing: dict[str, dict[str, list[str]]] = {
        "light": {s.value: [] for s in (Sport.CFB, Sport.NFL)},
        "dark": {s.value: [] for s in (Sport.CFB, Sport.NFL)},
    }
    for sport in (Sport.CFB, Sport.NFL):
        folder = logo_dir / sport.value
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.png")):
            dark = path.stem.endswith("-dark")
            team_id = path.stem.removesuffix("-dark")
            listing["dark" if dark else "light"][sport.value].append(team_id)
    return listing


def fetch_logo(url: str) -> bytes:
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                                      delete=False)
    try:
        with tmp:
            tmp.write(data)
        Path(tmp.name).replace(path)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
```

CLI in `src/pickem/cli.py`. Add the imports `from pickem.ingest.cfbd_source import CfbdConfig, fetch_fbs_teams` (merging with any existing import from that module) and `from pickem.ingest.logos import LOGO_DIR, cfb_sources, download_logos, fetch_logo, nfl_sources`. Then:

```python
@app.command("fetch-logos")
def fetch_logos_cmd(
    season: int = typer.Option(None, help="Season whose FBS team list to use. Default: this year"),
    dashboard_dir: Path = typer.Option(
        None, help="Default: $PICKEM_DASHBOARD_DIR or ~/.local/share/pickem/dashboard"
    ),
) -> None:
    """Download team logos into the dashboard folder once; logos already saved are kept."""
    season = season or datetime.now(tz=UTC).year
    target = (dashboard_dir or config.dashboard_dir()) / LOGO_DIR
    with run_context("cli:fetch-logos", season=season, logo_dir=str(target)):
        resolver = TeamResolver.default()
        cfb, unmatched = cfb_sources(fetch_fbs_teams(CfbdConfig.from_env(), season), resolver)
        outcome = download_logos(
            [*cfb, *nfl_sources(resolver.team_ids(Sport.NFL))], target, fetch_logo
        )
        covered = {s.team_id for s in cfb}
        missing = [t for t in resolver.team_ids(Sport.CFB) if t not in covered]
        logger.bind(
            event="logos_fetched", saved=len(outcome.saved), kept=len(outcome.kept),
            failed=len(outcome.failed), unmatched=unmatched, cfb_without_logo=missing,
        ).info(f"logos: saved {len(outcome.saved)}, kept {len(outcome.kept)}, failed {len(outcome.failed)}")
    typer.echo(f"saved {len(outcome.saved)}, kept {len(outcome.kept)}, failed {len(outcome.failed)}")
    if missing:
        typer.echo("No logo (shown as a plain badge): " + ", ".join(missing))
    if unmatched:
        typer.echo("CFBD schools not in the alias table: " + ", ".join(unmatched))
    for line in outcome.failed:
        typer.secho(f"failed: {line}", fg="red", err=True)
    if outcome.failed:
        raise typer.Exit(code=1)
```

Reuse whatever the CLI already imports for `TeamResolver`, `Sport`, `logger`, `UTC` and `datetime`, and add any that are missing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_logos.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/ingest/logos.py src/pickem/resolve/resolver.py src/pickem/ingest/cfbd_source.py src/pickem/cli.py tests/test_logos.py
git commit -m "feat: download team logos for the dashboard"
```

---

### Task 4: One publisher for the whole page

**Files:**
- Create: `src/pickem/report/publish.py`
- Modify: `src/pickem/store/db.py` (add `latest_league_season`), `src/pickem/cli.py` (`_write_dashboard` delegates; move `_write_atomic` out; new `publish-dashboard`)
- Test: `tests/test_publish.py`, `tests/test_results_cli.py` (retarget three monkeypatches)

**Interfaces:**
- Consumes: `build_results_report`, `build_this_week` (Task 1), `render_dashboard`/`render_week_forwarder` (Task 2), `available_logos`/`LOGO_DIR` (Task 3).
- Produces:
  - `Store.latest_league_season() -> int | None`
  - In `pickem.report.publish`:
    - `TRIGGER_BOT = "bot"`, `TRIGGER_WEEK_START = "week-start"`, `TRIGGER_RESULTS = "results"`, `TRIGGER_MANUAL = "manual"`
    - `Published(path: Path | None, error: str | None = None)`
    - `publish_dashboard(store: Store, dashboard_dir: Path, *, trigger: str, season: int | None = None, entry_name: str = config.DEFAULT_ENTRY_NAME, now: datetime | None = None) -> Published`. It never raises.
    - `write_atomic(path: Path, text: str) -> None`, moved from `cli._write_atomic`.
  - CLI: `pickem publish-dashboard [--trigger NAME] [--db PATH] [--dashboard-dir DIR]`. It exits 3 when the page was not written.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_publish.py
"""The one routine every job uses to write the dashboard page."""

import json
from datetime import UTC, datetime, timedelta

from dashboard_helpers import data_block
from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import Game, LeagueLine, Sport
from pickem.report.publish import TRIGGER_BOT, publish_dashboard
from pickem.store.db import Store

NOW = datetime(2026, 10, 8, 14, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n"


def seed_board(store: Store) -> None:
    game = Game(game_id="cfb-2026-06-OU-at-MICH", sport=Sport.CFB, season=2026, week=6,
                kickoff_utc=NOW + timedelta(days=2), home_team_id="MICH", away_team_id="OU")
    store.upsert_games([game])
    store.upsert_league_lines([LeagueLine(game_id=game.game_id, season=2026, week=6,
                                          spread_home=-3.0, posted_at=NOW)])


def open_store(tmp_path) -> Store:
    store = Store(tmp_path / "pickem.duckdb")
    store.init_schema()
    return store


def test_a_board_with_no_imported_results_still_publishes(tmp_path):
    dash = tmp_path / "dash"
    (dash / "logos" / "cfb").mkdir(parents=True)
    (dash / "logos" / "cfb" / "OU.png").write_bytes(PNG)
    with open_store(tmp_path) as store:
        seed_board(store)
        published = publish_dashboard(store, dash, trigger=TRIGGER_BOT, now=NOW)
    assert published.path == dash / "index.html" and published.error is None
    data = data_block(published.path.read_text())
    assert data["season"] == 2026
    assert data["standings"] == []
    assert data["this_week"]["pool_week"] == 6
    assert data["logos"]["light"]["cfb"] == ["OU"]
    assert not list(dash.glob("week-*.html"))


def test_nothing_stored_writes_nothing(tmp_path, records):
    dash = tmp_path / "dash"
    with open_store(tmp_path) as store:
        published = publish_dashboard(store, dash, trigger=TRIGGER_BOT, now=NOW)
    assert published.path is None and published.error is None
    assert not dash.exists()
    assert [r["extra"]["trigger"] for r in records if r["extra"].get("event") == "dashboard_skipped"] == ["bot"]


def test_a_failure_is_logged_with_its_trigger_and_never_raised(tmp_path, records, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr("pickem.report.publish.render_dashboard", boom)
    dash = tmp_path / "dash"
    with open_store(tmp_path) as store:
        seed_board(store)
        published = publish_dashboard(store, dash, trigger=TRIGGER_BOT, now=NOW)
    assert published.path is None and published.error == "render broke"
    failed = [r for r in records if r["extra"].get("event") == "dashboard_write_failed"]
    assert len(failed) == 1
    assert failed[0]["extra"]["trigger"] == "bot"
    assert failed[0]["extra"]["error_detail"] == "render broke"


def test_a_successful_write_is_logged_with_its_trigger(tmp_path, records):
    with open_store(tmp_path) as store:
        seed_board(store)
        publish_dashboard(store, tmp_path / "dash", trigger=TRIGGER_BOT, now=NOW)
    [written] = [r for r in records if r["extra"].get("event") == "dashboard_written"]
    assert written["extra"]["trigger"] == "bot"
    assert written["extra"]["this_week"] == 6


def test_publish_dashboard_command(tmp_path):
    db, dash = tmp_path / "pickem.duckdb", tmp_path / "dash"
    with open_store(tmp_path) as store:
        seed_board(store)
    result = CliRunner().invoke(app, ["publish-dashboard", "--trigger", "week-start",
                                      "--db", str(db), "--dashboard-dir", str(dash)])
    assert result.exit_code == 0, result.output
    assert (dash / "index.html").exists()
    rows = [json.loads(line) for line in
            (tmp_path / "pickem-logs" / "pickem.jsonl").read_text().splitlines() if line.strip()]
    assert any(r["event"] == "dashboard_written" and r["trigger"] == "week-start" for r in rows)


def test_publish_dashboard_command_exits_3_when_not_written(tmp_path, monkeypatch):
    db = tmp_path / "pickem.duckdb"
    with open_store(tmp_path) as store:
        seed_board(store)
    def boom(*args, **kwargs):
        raise RuntimeError("render broke")

    monkeypatch.setattr("pickem.report.publish.render_dashboard", boom)
    result = CliRunner().invoke(app, ["publish-dashboard", "--db", str(db),
                                      "--dashboard-dir", str(tmp_path / "dash")])
    assert result.exit_code == 3
    assert "dashboard not written: render broke" in result.output
```

Check how `tests/test_results_cli.py::test_dashboard_write_is_logged` reads the JSON log rows (`row["event"]` at the top level) and match it in `test_publish_dashboard_command`. If the extra fields are nested differently, follow that file.

In `tests/test_results_cli.py`, retarget the monkeypatches because the code moved:
- `"pickem.cli.render_dashboard"` → `"pickem.report.publish.render_dashboard"`, in both tests that patch it.
- In `test_rebuilding_an_older_week_builds_the_page_through_the_latest`, patch the same `spy` onto **both** `"pickem.cli.build_results_report"` (week 2's Markdown) and `"pickem.report.publish.build_results_report"` (the page through week 3). `built == [2, 3]` stays as is.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_publish.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.report.publish'`.

- [ ] **Step 3: Implement**

`src/pickem/store/db.py`, a method on `Store`:

```python
    def latest_league_season(self) -> int | None:
        """The newest season with any CBS line stored, or None on an empty store."""
        return self._con.execute("SELECT max(season) FROM league_lines").fetchone()[0]
```

`src/pickem/report/publish.py`:

```python
"""Write the dashboard page from whatever is stored: the season's results, and this week's board.

Every job that touches the page comes through here (the bot after each
refresh, the week-start script, the Tuesday results import), so each one
writes the same complete page and whichever ran last is correct. A failure is
logged with the job that hit it and returned, never raised: no caller's
message or exit status should depend on the page.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from pickem import config
from pickem.ingest.logos import LOGO_DIR, available_logos
from pickem.report.dashboard import render_dashboard, render_week_forwarder
from pickem.report.results import build_results_report
from pickem.report.this_week import build_this_week
from pickem.store.db import Store

TRIGGER_BOT = "bot"
TRIGGER_WEEK_START = "week-start"
TRIGGER_RESULTS = "results"
TRIGGER_MANUAL = "manual"


@dataclass(frozen=True)
class Published:
    path: Path | None  # None when no page was written
    error: str | None = None  # why not, when writing failed


def publish_dashboard(
    store: Store,
    dashboard_dir: Path,
    *,
    trigger: str,
    season: int | None = None,
    entry_name: str = config.DEFAULT_ENTRY_NAME,
    now: datetime | None = None,
) -> Published:
    """Write index.html and a week-N.html forwarder per imported week."""
    now = now or datetime.now(tz=UTC)
    try:
        season = season if season is not None else store.latest_league_season()
        if season is None:
            logger.bind(event="dashboard_skipped", trigger=trigger).info(
                "dashboard not written: no CBS board is stored yet"
            )
            return Published(None)
        weeks = store.pool_weeks(season)
        report = (
            build_results_report(store, season=season, pool_week=weeks[-1], entry_name=entry_name)
            if weeks else None
        )
        this_week = build_this_week(store, season, now)
        page = render_dashboard(
            report, generated_at=now, season=season, this_week=this_week,
            logos=available_logos(dashboard_dir / LOGO_DIR),
        )
        dashboard_dir.mkdir(parents=True, exist_ok=True)
        path = dashboard_dir / "index.html"
        write_atomic(path, page)
        for week in weeks:
            write_atomic(dashboard_dir / f"week-{week}.html", render_week_forwarder(week))
    except Exception as exc:  # a render bug, a locked database or a full disk alike
        logger.bind(
            event="dashboard_write_failed",
            trigger=trigger,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        ).error(f"dashboard not written: {exc}")
        return Published(None, str(exc))
    logger.bind(
        event="dashboard_written",
        trigger=trigger,
        pool_week=weeks[-1] if weeks else None,
        this_week=None if this_week is None else this_week["pool_week"],
        path=str(path),
        bytes=len(page.encode("utf-8")),
        weeks=len(weeks),
    ).info(f"dashboard written to {path}")
    return Published(path)


def write_atomic(path: Path, text: str) -> None:
    """Write to a unique temp file beside the target, then rename, so a reader
    never sees half a page and concurrent writers never collide on one temp name."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
        suffix=".tmp", delete=False,
    )
    try:
        with tmp:
            tmp.write(text)
        Path(tmp.name).replace(path)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
```

In `src/pickem/cli.py`:
- Delete `_write_atomic`. Import `write_atomic` from `pickem.report.publish` wherever other CLI code uses `_write_atomic`. Grep for it first.
- Drop `render_dashboard` and `render_week_forwarder` from the CLI's imports if nothing else uses them.
- Replace the body of `_write_dashboard`:

```python
def _write_dashboard(store: Store, report: ResultsReport, dashboard_dir: Path) -> Path | None:
    """Write the whole page through the latest imported week; a failure is reported, never raised.

    The import and the Markdown are already saved, so the DM still goes out
    before the command exits 3.
    """
    published = publish_dashboard(
        store, dashboard_dir, trigger=TRIGGER_RESULTS,
        season=report.season, entry_name=report.entry_name,
    )
    if published.path is None:
        typer.secho(f"dashboard not written: {published.error}", fg="red", err=True)
    else:
        typer.echo(f"dashboard written to {published.path}")
    return published.path
```

- Add the command:

```python
@app.command("publish-dashboard")
def publish_dashboard_cmd(
    trigger: str = typer.Option(TRIGGER_MANUAL, help="Which job asked, for the log"),
    db: Path = typer.Option(config.DEFAULT_DB),
    dashboard_dir: Path = typer.Option(
        None, help="Default: $PICKEM_DASHBOARD_DIR or ~/.local/share/pickem/dashboard"
    ),
) -> None:
    """Rebuild the dashboard page from what is stored; exits 3 when it was not written."""
    with run_context("cli:publish-dashboard", db=str(db), trigger=trigger):
        with _store(db) as store:
            published = publish_dashboard(
                store, dashboard_dir or config.dashboard_dir(), trigger=trigger
            )
    if published.path is None:
        typer.secho(f"dashboard not written: {published.error or 'no CBS board is stored yet'}",
                    fg="red", err=True)
        raise typer.Exit(code=3)
    typer.echo(f"dashboard written to {published.path}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_publish.py tests/test_results_cli.py -q`
Expected: PASS. `test_dashboard_write_is_logged` still finds `pool_week == 2` and `weeks == 1`.

- [ ] **Step 5: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/report/publish.py src/pickem/store/db.py src/pickem/cli.py tests/test_publish.py tests/test_results_cli.py
git commit -m "feat: one publisher writes the whole dashboard page"
```

---

### Task 5: The "This week" tab: routing, list and logos

**Files:**
- Create: `src/pickem/report/dashboard_assets/tab_thisweek.js`
- Modify: `src/pickem/report/dashboard.py` (`SCRIPTS`: add `"tab_thisweek.js"` after `"panel.js"`), and these assets: `filters.js`, `ui_core.js`, `app.js`, `tab_week.js` (no changes beyond what's below), `app.css`
- Test: `tests/js/filters.test.mjs`, `tests/test_dashboard_page.py`

**Interfaces:**
- Consumes: the `this_week` and `logos` data keys (Task 2).
- Produces:
  - In `P.filters`: `TABS` (now starting with `"thisweek"`), `defaults(data)` (tab `"thisweek"` when `data.standings` is empty, otherwise `"week"`), `parseHash` (accepts a this-week game id), `toHash` (omits the default tab), `thisWeekGames(data, state)` (board and tier filters only).
  - In `P.ui`:
    - `logo(data, sport, team, large = false)`: a `span.logo` with an `img` (plus an `img.logo-dark` when a dark version exists). Without a logo, it's an empty `span.logo.logo-none`. When `large`, it's `span.logo.logo-lg.logo-badge` with the abbreviation inside.
    - `matchup(data, g)`: away logo, away, `@`, home logo, home.
    - `TIER_BADGES`: the Discord tier badges.
  - `P.tabs.thisweek(view)`.

- [ ] **Step 1: Write the failing JS unit tests**

Append to `tests/js/filters.test.mjs`:

```js
const thisWeek = {
  pool_week: 6,
  games: [
    { id: "tw-cfb", sport: "cfb", model: { side: "home", tier: "strong", edge: 3, at: "" } },
    { id: "tw-nfl", sport: "nfl", model: { side: "away", tier: "slight", edge: -0.5, at: "" } },
    { id: "tw-none", sport: "nfl", model: null },
  ],
};

test("this week is the first tab", () => {
  assert.equal(F.TABS[0], "thisweek");
});

test("with results, the bare page still opens the results tab, so old links keep working", () => {
  const withBoard = { ...data, this_week: thisWeek };
  assert.equal(F.parseHash("", withBoard).tab, "week");
  assert.equal(F.parseHash("#week=2", withBoard).tab, "week");
  assert.equal(F.toHash(F.defaults(withBoard), withBoard), "");
});

test("before any results, the page opens on this week", () => {
  const empty = { latest_week: null, standings: [], games: [], this_week: thisWeek };
  assert.equal(F.parseHash("", empty).tab, "thisweek");
  assert.equal(F.toHash(F.defaults(empty), empty), "");
  assert.equal(F.toHash({ ...F.defaults(empty), tab: "season" }, empty), "#tab=season");
});

test("a this-week game id survives the address", () => {
  const withBoard = { ...data, this_week: thisWeek };
  const state = plain(F.parseHash("#tab=thisweek&game=tw-nfl", withBoard));
  assert.equal(state.tab, "thisweek");
  assert.equal(state.game, "tw-nfl");
});

test("this week's games follow the board and tier filters, not the result filter", () => {
  const withBoard = { ...data, this_week: thisWeek };
  const ids = (patch) => F.thisWeekGames(withBoard, { ...F.defaults(withBoard), ...patch }).map((g) => g.id);
  assert.deepEqual(ids({}), ["tw-cfb", "tw-nfl", "tw-none"]);
  assert.deepEqual(ids({ sport: "nfl" }), ["tw-nfl", "tw-none"]);
  assert.deepEqual(ids({ tiers: ["slight", "none"] }), ["tw-nfl", "tw-none"]);
  assert.deepEqual(ids({ result: "loss" }), ["tw-cfb", "tw-nfl", "tw-none"]);
  assert.deepEqual(F.thisWeekGames(data, F.defaults(data)), []);
});
```

- [ ] **Step 2: Write the failing DOM tests**

Append to `tests/test_dashboard_page.py`. Import `LOGOS_FIXTURE`, `this_week_fixture`, `synthetic_season` and `TW_SLIGHT` from `dashboard_helpers` as needed.

```python
def this_week_page(tmp_path, report="fixture", **kwargs):
    kwargs.setdefault("this_week", this_week_fixture())
    kwargs.setdefault("logos", LOGOS_FIXTURE)
    if report is None:
        return page_fixture(tmp_path, None, season=2026, **kwargs)
    return page_fixture(tmp_path, None if report == "fixture" else report, **kwargs)


def test_this_week_lists_both_boards_in_kickoff_order(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), "tab=thisweek", tmp_path)
    assert dom.count('class="row-button tw-row"') == 5
    assert "CFB board" in dom and "NFL board" in dom
    assert "Pool week 6" in dom
    assert dom.count("🔒 Locked") == 1  # the final game shows its score instead
    assert "17–24" in dom and 'class="res res-win"' in dom
    assert "🔥 Strong" in dom and "🎯 Slight" in dom and "No model" in dom
    order = [dom.index(f'aria-label="{m}"') for m in
             ("AF @ ARMY", "PSU @ TEM", "OU @ MICH", "BUF @ MIA", "NE @ SEA")]
    assert order == sorted(order)


def test_this_week_draws_saved_logos_and_blanks_for_the_rest(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), "tab=thisweek", tmp_path)
    assert 'src="logos/cfb/OU.png"' in dom and 'src="logos/cfb/OU-dark.png"' in dom
    assert 'src="logos/nfl/BUF.png"' in dom
    assert "logos/nfl/BUF-dark.png" not in dom
    assert dom.count('class="logo logo-none"') == 7  # AF ARMY PSU TEM MIA NE SEA


def test_this_week_follows_the_board_and_tier_filters(tmp_path):
    page = this_week_page(tmp_path)
    assert rendered_dom(page, "tab=thisweek&sport=nfl", tmp_path).count('class="row-button tw-row"') == 2
    assert rendered_dom(page, "tab=thisweek&tier=strong", tmp_path).count('class="row-button tw-row"') == 2


def test_this_week_without_a_board_says_so(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path, this_week=None), "tab=thisweek", tmp_path)
    assert "No board is loaded yet." in dom


def test_before_any_results_the_page_opens_on_this_week(tmp_path):
    page = this_week_page(tmp_path, report=None)
    assert rendered_dom(page, "", tmp_path).count('class="row-button tw-row"') == 5
    assert "No results imported yet." in rendered_dom(page, "tab=season", tmp_path)


def test_every_page_says_when_it_was_updated(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "", tmp_path)
    assert "Updated Tue, Sep 29, 9:00 AM ET" in dom  # GENERATED, in Eastern time
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_js.py tests/test_dashboard_page.py -q`
Expected: FAIL. `F.TABS[0]` is `"week"`, the DOM has no `tw-row`, and the `tab_thisweek.js` file doesn't exist yet (raised once `SCRIPTS` is updated).

- [ ] **Step 4: Implement `filters.js`**

```js
  const TABS = ["thisweek", "week", "season", "model", "games"];

  function defaults(data) {
    return {
      week: data.latest_week, tab: data.standings.length ? "week" : "thisweek",
      sport: "all", tiers: [], result: "all", game: null,
    };
  }

  function thisWeekList(data) {
    return data.this_week ? data.this_week.games : [];
  }
```

In `parseHash`, change the game check to accept either list:

```js
    if (id && (data.games.some((g) => g.id === id) || thisWeekList(data).some((g) => g.id === id))) state.game = id;
```

In `toHash`, change the tab line to compare with the default:

```js
    if (state.tab !== defaults(data).tab) params.set("tab", state.tab);
```

Add the filter, and export `thisWeekGames` from `P.filters`:

```js
  // This week's board takes the board and tier filters; no game on it has our result yet.
  function thisWeekGames(data, state) {
    return thisWeekList(data).filter((g) =>
      (state.sport === "all" || g.sport === state.sport) &&
      (!state.tiers.length || state.tiers.includes(tierOf(g))));
  }
```

- [ ] **Step 5: Implement `ui_core.js`**

Add these before `P.ui = ...`, and export `logo`, `matchup` and `TIER_BADGES`:

```js
  // The same badges the Discord messages use.
  const TIER_BADGES = {
    strong: "🔥 Strong", lean: "✅ Lean", slight: "🎯 Slight", coinflip: "🪙 Coinflip", no_market: "⚠️ No market",
  };

  function savedLogo(data, kind, sport, team) {
    const list = data.logos && data.logos[kind] && data.logos[kind][sport];
    return Boolean(list && list.includes(team));
  }

  // A saved logo (with its dark version when there is one), or a plain stand-in.
  // Only files the page was told exist are named, so there is never a broken image.
  function logo(data, sport, team, large) {
    const size = large ? " logo-lg" : "";
    if (!savedLogo(data, "light", sport, team)) {
      return large
        ? h("span", { class: "logo logo-lg logo-badge", "aria-hidden": "true" }, team)
        : h("span", { class: "logo logo-none", "aria-hidden": "true" });
    }
    const src = (suffix) => `logos/${encodeURIComponent(sport)}/${encodeURIComponent(team)}${suffix}.png`;
    const dark = savedLogo(data, "dark", sport, team);
    return h("span", { class: "logo" + size, "aria-hidden": "true" },
      h("img", { class: dark ? "logo-light" : null, src: src(""), alt: "", loading: "lazy" }),
      dark ? h("img", { class: "logo-dark", src: src("-dark"), alt: "", loading: "lazy" }) : null);
  }

  function matchup(data, g) {
    return h("span", { class: "matchup" },
      logo(data, g.sport, g.away), h("span", null, g.away), h("span", { class: "at" }, "@"),
      logo(data, g.sport, g.home), h("span", null, g.home));
  }
```

- [ ] **Step 6: Implement `tab_thisweek.js`**

```js
// This week tab: every game on the current boards with the model's pick.
(function (P) {
  "use strict";
  const { h, card, LABELS, resultTag, fmt, matchup, TIER_BADGES } = P.ui;
  const F = P.filters;

  function pick(g) {
    if (!g.model) return h("span", { class: "muted" }, "No model");
    return [h("span", { class: "tier-badge" }, TIER_BADGES[g.model.tier]), " ", fmt.sideLine(g, g.model.side)];
  }

  function status(g) {
    if (g.final) return [h("span", { class: "muted" }, `${g.away_score}–${g.home_score}`), resultTag(g.result)];
    if (g.locked) return h("span", { class: "lock", title: "Kicked off: the pick can no longer change" }, "🔒 Locked");
    return null;
  }

  function row(view, g) {
    return h("li", null, h("button", {
      class: "row-button tw-row", type: "button", "aria-label": `${g.away} @ ${g.home}`,
      onclick: () => P.app.openGame(g.id),
    },
      h("span", { class: "grow" },
        matchup(view.data, g), h("br"),
        h("span", { class: "muted" }, `${fmt.kickoff(g.kickoff)} · CBS ${fmt.homeLine(g, g.line)}`)),
      h("span", { class: "tw-pick" }, pick(g)),
      h("span", { class: "tw-status" }, status(g))));
  }

  P.tabs.thisweek = function (view) {
    const board = view.data.this_week;
    if (!board) return [card("This week", h("p", { class: "empty" }, "No board is loaded yet."))];
    const games = F.thisWeekGames(view.data, view.state);
    const head = h("p", { class: "note wide" }, `Pool week ${board.pool_week}`);
    const sports = ["cfb", "nfl"].filter((sp) => games.some((g) => g.sport === sp));
    if (!sports.length) return [head, card("This week", h("p", { class: "empty" }, "No games match the filters."))];
    return [head, ...sports.map((sport) => card(`${LABELS.sport[sport]} board`,
      h("ul", { class: "list" }, games.filter((g) => g.sport === sport).map((g) => row(view, g)))))];
  };
})(globalThis.Pickem = globalThis.Pickem || {});
```

Add `"tab_thisweek.js"` to `SCRIPTS` in `dashboard.py`, after `"panel.js"`.

- [ ] **Step 7: Implement `app.js`**

- `TAB_LABELS`: `{ thisweek: "This week", week: "Results", season: "Season", model: "Model", games: "Games" }`. The tab key stays `week`, so old links keep working.
- In `topBar`:
  - Show the week `<label class="week-pick">` only when `state.tab !== "thisweek" && data.standings.length`.
  - Show the result `segmented` only when `state.tab !== "thisweek"`.
  - After the title row, add `h("p", { class: "updated" }, \`Updated ${P.ui.fmt.time(data.generated_at)}\`)`.
- In `app.render`, guard the results tabs:

```js
  function noResults() {
    return [P.ui.card(null, h("p", { class: "empty" },
      "No results imported yet. They appear after the first Tuesday results import."))];
  }
```

```js
    const needsResults = app.state.tab !== "thisweek" && !app.data.standings.length;
    const tab = needsResults ? noResults : (P.tabs[app.state.tab] || notBuilt);
```

- [ ] **Step 8: Add the styles to `app.css`**

```css
/* logos */
.logo{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;
  flex:none;vertical-align:middle}
.logo img{width:100%;height:100%;object-fit:contain}
.logo .logo-dark{display:none}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]) .logo .logo-light{display:none}
  :root:not([data-theme="light"]) .logo .logo-dark{display:inline}
}
:root[data-theme="dark"] .logo .logo-light{display:none}
:root[data-theme="dark"] .logo .logo-dark{display:inline}
.logo-none{border-radius:50%;background:var(--push-bg)}
.logo-lg{width:44px;height:44px}
.logo-badge{border-radius:50%;background:var(--push-bg);color:var(--muted);font-size:12px;font-weight:600}
.matchup{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap}
.matchup .at{color:var(--muted)}

/* this week */
.updated{margin:4px 0 0;color:var(--muted);font-size:13px}
.tw-pick{flex:none;text-align:right;white-space:nowrap}
.tw-status{flex:none;display:inline-flex;align-items:center;gap:6px;min-width:3.5em;justify-content:flex-end}
.tier-badge{font-weight:600}
.lock{font-size:12.5px;color:var(--muted);white-space:nowrap}
@media (max-width:520px){
  .tw-row{flex-wrap:wrap}
  .tw-row .grow{flex-basis:100%}
}
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dashboard_js.py tests/test_dashboard_page.py -q`
Expected: PASS, including every existing page test (the default tab with results is unchanged).

- [ ] **Step 10: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/report/dashboard.py src/pickem/report/dashboard_assets tests/js/filters.test.mjs tests/test_dashboard_page.py
git commit -m "feat: add the This week tab with logos"
```

---

### Task 6: The detail panel for this week's games, with pick-change markers

**Files:**
- Modify: `src/pickem/report/dashboard_assets/filters.js` (`pickChanges`), `panel.js`, `charts.js`, `tab_thisweek.js`, `app.css`
- Test: `tests/js/filters.test.mjs`, `tests/test_dashboard_page.py`

**Interfaces:**
- Consumes: `P.ui.logo`, `P.ui.TIER_BADGES` (Task 5).
- Produces:
  - `P.filters.pickChanges(history) -> [{at, from, to, kind: "side"|"tier"}]`
  - `P.charts.lineMove(g, marks = [])`, where `marks` is `[{at, label}]` and each mark is drawn as `line.mark-change`.
  - `P.panel.fact`, `P.panel.timeline` (exported).
  - `P.thisWeek.detail(g, data) -> Node[]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/filters.test.mjs`:

```js
test("pick changes are the history entries whose side or tier moved", () => {
  const h = (at, side, tier) => ({ at, side, tier, edge: 0 });
  const changes = F.pickChanges([h("1", "away", "lean"), h("2", "away", "lean"),
    h("3", "home", "lean"), h("4", "home", "strong")]);
  assert.deepEqual(plain(changes.map((c) => [c.at, c.kind])), [["3", "side"], ["4", "tier"]]);
  assert.deepEqual(F.pickChanges([]), []);
  assert.deepEqual(F.pickChanges([h("1", "home", "lean")]), []);
});
```

Append to `tests/test_dashboard_page.py` (import `TW_STRONG`, `TW_SLIGHT`, `TW_LOCKED`, `TW_NO_MODEL`):

```python
def test_a_this_week_game_opens_with_lines_pick_and_chart(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_STRONG}", tmp_path)
    assert 'class="panel"' in dom and "OU @ MICH" in dom
    assert "Lines now" in dom and "US books" in dom and "Pinnacle" in dom
    assert "MICH −6" in dom and "MICH −5.5" in dom
    assert "3.0 pts toward MICH" in dom
    assert "🔥 Strong" in dom and "league -3.0 vs market -6.0" in dom
    assert "How the pick changed this week" in dom and dom.count('class="changed"') == 2
    assert dom.count('class="mark-change"') == 2
    assert "Pick changed" in dom
    assert dom.count('class="logo logo-lg"') == 2


def test_a_slight_pick_names_its_key_number(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_SLIGHT}", tmp_path)
    assert "Crosses 3" in dom


def test_a_locked_pick_without_a_reason_says_so(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_LOCKED}", tmp_path)
    assert "No reason recorded for this pick." in dom
    assert "Locked" in dom


def test_a_game_with_no_model_and_no_quotes_still_opens(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_NO_MODEL}", tmp_path)
    assert "The model has no pick for this game." in dom
    assert "No line history" in dom
    assert "No quotes yet" in dom
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_js.py tests/test_dashboard_page.py -q`
Expected: FAIL. `F.pickChanges` is not a function, and the panel is missing for this-week games.

- [ ] **Step 3: Implement `filters.js` `pickChanges`** (and export it)

```js
  // Where the model's side or tier moved, for the timeline and the chart markers.
  function pickChanges(history) {
    const list = history || [];
    const out = [];
    for (let i = 1; i < list.length; i++) {
      const prev = list[i - 1], cur = list[i];
      if (prev.side !== cur.side || prev.tier !== cur.tier) {
        out.push({ at: cur.at, from: prev, to: cur, kind: prev.side !== cur.side ? "side" : "tier" });
      }
    }
    return out;
  }
```

- [ ] **Step 4: Implement `charts.js` markers**

Change `lineMove(g)` to `lineMove(g, marks)`. Set `marks = marks || []` first.

Make the time extent include the marks:
```js
    const times = g.lines.map((p) => Date.parse(p.at)).concat(marks.map((m) => Date.parse(m.at)));
```

Add a marker layer before the series in the `svg` children:
```js
      marks.map((m) => s("line", {
        class: "mark-change", x1: x(Date.parse(m.at)), x2: x(Date.parse(m.at)), y1: T, y2: H - B,
      }, s("title", null, `${fmt.time(m.at)}: ${m.label}`))),
```

After the CBS legend item:
```js
    if (marks.length) items.push(["change", "Pick changed (dotted)"]);
```

CSS in `app.css`:
```css
.mark-change{stroke:var(--muted);stroke-width:1.5;stroke-dasharray:2 4}
.key.s-change{border-top:2px dotted var(--muted);background:none}
```

Check how `.key.s-cbs` is styled in `app.css` and match its sizing rules for `.key.s-change`.

- [ ] **Step 5: Implement `panel.js` exports and delegation**

After the `P.panel = function ...` assignment, add:

```js
  P.panel.fact = fact;
  P.panel.timeline = timeline;
```

Change the start of `P.panel` to find this week's game when that tab is open:

```js
  P.panel = function (view) {
    const id = view.state.game;
    const current = id && view.data.this_week && view.data.this_week.games.find((x) => x.id === id);
    const past = id && view.data.games.find((x) => x.id === id);
    if (current && (view.state.tab === "thisweek" || !past)) {
      return shell(`${current.away} @ ${current.home}`, P.thisWeek.detail(current, view.data));
    }
    const g = past;
    if (g) return shell(`${g.away} @ ${g.home}`, gameDetail(g, view.data));
    if (P.app.list) return shell(P.app.list.title, gameList(P.app.list));
    return null;
  };
```

- [ ] **Step 6: Implement `P.thisWeek.detail` in `tab_thisweek.js`**

Inside the IIFE, after `P.tabs.thisweek`:

```js
  function gapText(g) {
    if (g.gap === null || g.gap === undefined) return "—";
    if (g.gap === 0) return "None";
    return `${Math.abs(g.gap).toFixed(1)} pts toward ${g.gap > 0 ? g.home : g.away}`;
  }

  function changeLabel(g, c) {
    const T = F.TIER_LABELS;
    return `${T[c.from.tier]} ${fmt.team(g, c.from.side)} → ${T[c.to.tier]} ${fmt.team(g, c.to.side)}`;
  }

  P.thisWeek = {
    detail(g, data) {
      const fact = P.panel.fact;
      const m = g.model;
      const marks = F.pickChanges(g.history).map((c) => ({ at: c.at, label: changeLabel(g, c) }));
      return [
        h("div", { class: "tw-head" },
          P.ui.logo(data, g.sport, g.away, true), h("span", { class: "at" }, "@"), P.ui.logo(data, g.sport, g.home, true)),
        h("p", { class: "muted" },
          `${LABELS.sport[g.sport]} · Week ${g.week} · ${fmt.kickoff(g.kickoff)}${g.locked ? " · Locked" : ""}`),
        h("h3", null, "Lines now"),
        h("dl", { class: "facts" },
          g.final ? fact("Final", `${g.away} ${g.away_score} – ${g.home} ${g.home_score}`) : null,
          fact("CBS line", fmt.homeLine(g, g.line)),
          fact("US books", g.market.us === null ? "No quotes yet" : fmt.homeLine(g, g.market.us)),
          fact("Pinnacle", g.market.pinnacle === null ? "No quote yet" : fmt.homeLine(g, g.market.pinnacle)),
          fact("Gap", gapText(g))),
        h("h3", null, "The pick and why"),
        m ? h("p", null, h("span", { class: "tier-badge" }, TIER_BADGES[m.tier]), " ", fmt.sideLine(g, m.side),
              g.final ? [" ", resultTag(g.result)] : null)
          : h("p", { class: "empty" }, "The model has no pick for this game."),
        m ? h("p", { class: "muted" }, m.rationale || "No reason recorded for this pick.") : null,
        g.key_number
          ? h("p", { class: "note" }, `Crosses ${g.key_number}, one of the margins football games most often end on.`)
          : null,
        h("h3", null, "How the pick changed this week"),
        P.panel.timeline(g),
        h("h3", null, "Spread over time"),
        P.charts.lineMove(g, marks),
      ];
    },
  };
```

The `fact` helper returns an array, and `h` flattens children while skipping `null`, so the conditional `Final` row works.

CSS:
```css
.tw-head{display:flex;align-items:center;gap:10px;margin:4px 0 2px}
.tw-head .at{color:var(--muted)}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dashboard_js.py tests/test_dashboard_page.py -q`
Expected: PASS. The existing `test_a_game_opens_in_the_detail_panel_with_its_history` still passes: results games get no marks because `lineMove(g)` defaults to none.

- [ ] **Step 8: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/report/dashboard_assets tests/js/filters.test.mjs tests/test_dashboard_page.py
git commit -m "feat: open this week's games with lines, reasons and pick-change markers"
```

---

### Task 7: Logos in the results tabs

**Files:**
- Modify: `src/pickem/report/dashboard_assets/panel.js` (drill-down rows, results game header), `tab_games.js` (matchup cell)
- Test: `tests/test_dashboard_page.py`

**Interfaces:**
- Consumes: `P.ui.logo`, `P.ui.matchup` (Task 5).

- [ ] **Step 1: Write the failing DOM tests**

```python
RESULT_LOGOS = {"light": {"cfb": ["PSU", "TEM"], "nfl": []}, "dark": {"cfb": [], "nfl": []}}


def test_the_games_table_shows_logos(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path, logos=RESULT_LOGOS), "tab=games", tmp_path)
    assert 'src="logos/cfb/PSU.png"' in dom and 'src="logos/cfb/TEM.png"' in dom


def test_a_results_game_header_shows_both_logos(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path, logos=RESULT_LOGOS), f"game={MODEL_GAME}", tmp_path)
    assert dom.count('class="logo logo-lg"') == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_dashboard_page.py -q -k logos`
Expected: FAIL. No `logos/cfb/PSU.png` appears in the DOM.

- [ ] **Step 3: Implement**

`tab_games.js`:
- `row(g)` becomes `row(view, g)`, with the matchup cell `h("td", null, P.ui.matchup(view.data, g))`.
- In `table`, use `games.map((g) => row(view, g))`.

`panel.js`:
- `gameButton(g, strategy)` becomes `gameButton(data, g, strategy)`. Its first text line becomes `` `W${g.week} · `, P.ui.matchup(data, g) ``.
- `gameList(list)` becomes `gameList(list, data)`, passing `data` through.
- In `P.panel`, call `gameList(P.app.list, view.data)`.
- At the top of `gameDetail`'s returned array, add:

```js
      h("div", { class: "tw-head" },
        P.ui.logo(data, g.sport, g.away, true), h("span", { class: "at" }, "@"), P.ui.logo(data, g.sport, g.home, true)),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dashboard_page.py -q`
Expected: PASS. The existing `games` row count, `PSU @ TEM` title and drill-down tests still pass, because the panel title stays text.

- [ ] **Step 5: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/report/dashboard_assets tests/test_dashboard_page.py
git commit -m "feat: show team logos in the results tabs"
```

---

### Task 8: The bot links the page and rebuilds it after every refresh

**Files:**
- Modify: `src/pickem/discord_bot.py`
- Test: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `publish_dashboard`, `TRIGGER_BOT` (Task 4).
- Produces:
  - `_add_page_link(embed) -> embed`
  - `_publish_dashboard(settings) -> None`, a module function patched in tests.
  - `PAGE_LINK_TEXT = "📊 Open this week's picks"`
  - `PickemBot._rebuild_dashboard()`, which never raises.

- [ ] **Step 1: Write the failing tests**

At the top of `tests/test_discord_bot.py`, add:

```python
from dashboard_helpers import data_block

from pickem.discord_bot import _add_page_link, _format_change_notification  # merge into the existing import block
from pickem.discord_bot import _publish_dashboard as real_publish_dashboard


@pytest.fixture(autouse=True)
def published(monkeypatch):
    """Record page rebuilds instead of writing one on every refresh under test."""
    calls = []
    monkeypatch.setattr("pickem.discord_bot._publish_dashboard", lambda settings: calls.append(settings.db))
    return calls
```

Tests:

```python
URL = "https://sandbox.tail750bff.ts.net/pickem"
LINK = "[📊 Open this week's picks](https://sandbox.tail750bff.ts.net/pickem/#tab=thisweek)"


def test_the_change_notification_ends_with_the_page_link(monkeypatch):
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", URL)
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    embed = _format_change_notification(MonitorScope(Sport.NFL, 2026, 1), _snapshot_for(_edge(game.game_id)), (game,))
    assert embed.fields[-1].value == LINK
    assert embed.title == "🏈 Recommendations Updated"


def test_status_ends_with_the_page_link(monkeypatch):
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", URL)
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    status = ScopeStatus(scope=MonitorScope(Sport.NFL, 2026, 1), state=AutomationState(),
                         snapshot=_snapshot_for(_edge(game.game_id)), games=(game,), market_timestamp=None)
    embed = _format_status((status,), FakeScheduler())
    assert embed.fields[-1].value == LINK
    assert embed.fields[-2].name == "Scheduling"


def test_without_a_dashboard_address_the_messages_are_unchanged():
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    embed = _format_change_notification(MonitorScope(Sport.NFL, 2026, 1), _snapshot_for(_edge(game.game_id)), (game,))
    assert all("Open this week's picks" not in f.value for f in embed.fields)


def test_a_full_embed_skips_the_link_rather_than_break_the_message(monkeypatch):
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", URL)
    embed = discord.Embed(title="t")
    for index in range(25):
        embed.add_field(name=str(index), value="x", inline=False)
    assert len(_add_page_link(embed).fields) == 25


@pytest.mark.asyncio
async def test_the_refresh_reply_with_new_picks_links_the_page(settings, monkeypatch):
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", URL)
    add_pick_scope(settings)
    snapshot = _snapshot_for(_edge("game-a"))
    interaction = FakeInteraction(user_id=settings.owner_id)
    bot = PickemBot(settings, FakeMonitor(RefreshResult(changed=True, snapshot=snapshot)), scheduler=FakeScheduler())
    await bot.refresh(interaction)
    assert interaction.followup.embeds[0].fields[-1].value == LINK


@pytest.mark.asyncio
async def test_an_unchanged_refresh_reply_has_no_link(settings, monkeypatch):
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", URL)
    add_pick_scope(settings)
    interaction = FakeInteraction(user_id=settings.owner_id)
    await PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler()).refresh(interaction)
    assert all("Open this week's picks" not in f.value for f in interaction.followup.embeds[0].fields)


@pytest.mark.asyncio
async def test_every_refresh_rebuilds_the_page(settings, published):
    add_pick_scope(settings)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    await bot._scheduled_refresh()
    await bot.refresh(FakeInteraction(user_id=settings.owner_id))
    assert published == [settings.db, settings.db]


@pytest.mark.asyncio
async def test_a_failed_rebuild_is_logged_and_the_reply_still_goes_out(settings, records, monkeypatch):
    def locked(_settings):
        raise RuntimeError("Could not set lock on file")

    monkeypatch.setattr("pickem.discord_bot._publish_dashboard", locked)
    add_pick_scope(settings)
    interaction = FakeInteraction(user_id=settings.owner_id)
    bot = PickemBot(settings, FakeMonitor(RefreshResult(changed=True, snapshot=_snapshot_for(_edge("game-a")))),
                    scheduler=FakeScheduler())
    await bot.refresh(interaction)
    assert interaction.followup.embeds[0].title == "🏈 Recommendations Updated"
    failed = [r for r in records if r["extra"].get("event") == "dashboard_write_failed"]
    assert [r["extra"]["trigger"] for r in failed] == ["bot"]


def test_the_bots_rebuild_writes_this_weeks_page(settings, tmp_path):
    add_pick_scope(settings)  # NFL week 1, so pool week 2
    real_publish_dashboard(settings)
    data = data_block((tmp_path / "dashboard" / "index.html").read_text())
    assert data["this_week"]["pool_week"] == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_discord_bot.py -q`
Expected: FAIL with `ImportError: cannot import name '_add_page_link'`.

- [ ] **Step 3: Implement**

In `src/pickem/discord_bot.py`, add `from pickem.report.publish import TRIGGER_BOT, publish_dashboard`. Then:

```python
PAGE_LINK_TEXT = "📊 Open this week's picks"
_DISCORD_EMBED_FIELD_LIMIT = 25


def _add_page_link(embed: discord.Embed) -> discord.Embed:
    """End the embed with a link to this week's page when the dashboard address is set.

    Skipped on an embed already at Discord's field limit: one more field would
    make Discord reject the whole message.
    """
    base = config.dashboard_url()
    if base is None or len(embed.fields) >= _DISCORD_EMBED_FIELD_LIMIT:
        return embed
    return embed.add_field(
        name="​", value=f"[{PAGE_LINK_TEXT}]({base}#tab=thisweek)", inline=False
    )


def _publish_dashboard(settings: DiscordSettings) -> None:
    """Rebuild the dashboard page from the bot's database. Runs in a worker thread."""
    with Store(settings.db) as store:
        store.init_schema()
        publish_dashboard(store, config.dashboard_dir(), trigger=TRIGGER_BOT)
```

Make these edits:
- `_format_change_notification`: `return _add_page_link(embed)`.
- `_format_status`: wrap both returns, i.e. `return _add_page_link(discord.Embed(...))` for the no-scopes branch, and `return _add_page_link(embed.add_field(name="Scheduling", ...))`.
- `_format_refresh_results`: just before its final `return embed`, add `if changed and not has_error: _add_page_link(embed)`.

In `PickemBot`, add the method:

```python
    async def _rebuild_dashboard(self) -> None:
        """Rebuild the page after a refresh, off the event loop. Never raises: a page is not worth a missed message."""
        try:
            await asyncio.to_thread(_publish_dashboard, self.settings)
        except Exception as error:  # opening the store can fail while another job holds it
            logger.bind(
                event="dashboard_write_failed",
                trigger=TRIGGER_BOT,
                error_type=type(error).__name__,
                error_detail=str(error),
            ).error(f"dashboard not written: {error}")
```

`asyncio.to_thread(_publish_dashboard, ...)` looks the name up at call time, so the test fixture's patch takes effect.

In `_refresh_scopes`, rebuild once all scopes are done, outside the refresh lock so a slow page never holds up the next refresh:

```python
        async with self._refresh_lock:
            scopes = self._resolve_scopes(season, week, sport=sport)
            results: list[tuple[MonitorScope, RefreshResult]] = []
            for scope in scopes:
                ...  # unchanged
        # Every refresh, changed or not: the spreads moved even when no pick did.
        await self._rebuild_dashboard()
        return tuple(results)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_discord_bot.py -q`
Expected: PASS, including all existing bot tests (the conftest unsets `PICKEM_DASHBOARD_URL`, so existing field-list assertions are unchanged).

- [ ] **Step 5: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add src/pickem/discord_bot.py tests/test_discord_bot.py
git commit -m "feat: link this week's page from the bot and rebuild it after every refresh"
```

---

### Task 9: The week-start job rebuilds the page

**Files:**
- Modify: `scripts/start-week.sh`
- Test: `tests/test_start_week_script.py`

- [ ] **Step 1: Write the failing tests**

Add a helper and tests:

```python
def _publish(db: Path) -> str:
    return f"run pickem publish-dashboard --trigger week-start --db {db}"


def test_the_page_is_rebuilt_after_the_polls(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"
    result, commands = _run(tmp_path, "3", "--season", "2026", "--file", str(page), "--db", str(db))
    assert result.returncode == 0, result.stderr
    assert commands[-1] == _publish(db)
    assert commands.index(_publish(db)) > commands.index(_poll("nfl", 2, db))


def test_no_rebuild_when_no_league_loaded(tmp_path):
    page, db = _page(tmp_path), tmp_path / "pickem.duckdb"
    _, commands = _run(tmp_path, "3", "--season", "2026", "--file", str(page), "--db", str(db),
                       fail_on="ingest-cbs")
    assert _publish(db) not in commands


def test_a_failed_rebuild_does_not_fail_the_week_start(tmp_path):
    weeks, db = tmp_path / "weeks", tmp_path / "pickem.duckdb"
    result, commands = _run(tmp_path, "--auto", "--season", "2026", "--weeks-dir", str(weeks),
                            "--db", str(db), fail_on="publish-dashboard")
    assert result.returncode == 0
    assert "Dashboard rebuild failed" in result.stderr
    [dm] = _dms(commands)
    assert "Pool week 4 board loaded" in dm
```

Then update every existing test that asserts the full `commands == [...]` list for a run where at least one league ingested: append `_publish(db)` after the last ingest or poll entry, before any `notify-owner` entry. Run the file first to see exactly which tests need it. Tests that slice (`commands[3:7]`) or look only at DMs need no change.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_start_week_script.py -q`
Expected: the new tests FAIL (no `publish-dashboard` call).

- [ ] **Step 3: Implement**

In `scripts/start-week.sh`, directly after the `for sport in ...; done` loop and before the `if ((${#failed[@]}))` block:

```bash
# Put the new board on the dashboard now rather than at the bot's next refresh.
# The page is a convenience: its failure never fails the week start.
if ((${#loaded[@]})); then
    uv run pickem publish-dashboard --trigger week-start --db "$database" ||
        echo "Dashboard rebuild failed; the bot rebuilds it on its next refresh." >&2
fi
```

Update the header comment's first paragraph to mention that the dashboard page is rebuilt after the polls.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_start_week_script.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`

```bash
git add scripts/start-week.sh tests/test_start_week_script.py
git commit -m "feat: rebuild the dashboard when a week starts"
```

---

### Task 10: Runbook, real logos, and the render check

**Files:**
- Modify: `docs/runbooks/dashboard.md`

- [ ] **Step 1: Update the runbook**

In `docs/runbooks/dashboard.md`:
- Opening paragraph: the page now also has a "This week" tab with the current week's boards. The bot rebuilds the page after every refresh, the week-start job rebuilds it after loading a board, and the Tuesday results job rebuilds it as before. The page shows when it was last updated at the top.
- Note that the page still makes no outside requests. Logos are files in `logos/` beside it.
- New section **Download logos (once)**:

  ```
  uv run pickem fetch-logos
  ```

  It saves college logos (with dark-mode versions) from the college football data service and NFL logos from ESPN into `$PICKEM_DASHBOARD_DIR/logos/`. Re-running fills in missing logos and keeps the rest. It lists college teams with no logo; those show a plain badge. Rebuild the page afterwards so it uses them.
- **Rebuild a page**: add `uv run pickem publish-dashboard`, which rebuilds from whatever is stored without touching the weekly report. Keep the `results-report` instructions for rebuilding a week's report.
- **Link the page from the Tuesday DM** → rename to **Link the page from Discord**. The same `PICKEM_DASHBOARD_URL` also adds a "📊 Open this week's picks" link to the end of the bot's status and "Recommendations Updated" messages. Restart the bot after changing `.env`.
- **When the page does not load / is stale**: a failed rebuild is logged as `dashboard_write_failed` with `trigger` = `bot`, `week-start`, `results` or `manual`. The "Updated" time on the page shows how old it is.

- [ ] **Step 2: Commit the docs**

```bash
git add docs/runbooks/dashboard.md
git commit -m "docs: runbook for the This week tab and logos"
```

- [ ] **Step 3: Render check (not a test)**

Run, with the scratchpad path standing in for `$S`:

```bash
S=<scratchpad>
PICKEM_LOG_DIR=$S/logs uv run pickem fetch-logos --dashboard-dir $S/dash
PICKEM_LOG_DIR=$S/logs uv run pickem publish-dashboard --dashboard-dir $S/dash
for theme in light dark; do
  google-chrome --headless=new --disable-gpu --window-size=390,1600 \
    --force-prefers-color-scheme=$theme --screenshot=$S/thisweek-$theme.png \
    "file://$S/dash/index.html#tab=thisweek"
done
google-chrome --headless=new --disable-gpu --window-size=390,1600 \
  --screenshot=$S/panel.png "file://$S/dash/index.html#tab=thisweek&game=<a pending game id from the data block>"
```

Look at each screenshot and check:
- No horizontal overflow at 390px wide.
- Logos are sharp, and dark-mode logos swap in.
- Tier badges, locked marks and scores line up in their rows.
- The panel's chart shows the dotted change markers, and the legend reads cleanly.

Fix the styling and re-check until all of that holds, committing each fix with `style: ...`. Report the remaining college teams the command listed as having no logo.

- [ ] **Step 4: Final full check**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass.

---

## After the plan (ship, not part of the tasks)

Shipping follows the `ship` skill and the project CLAUDE.md:
- Merge, then restart the bot.
- Run `uv run pickem fetch-logos` once against the real dashboard folder, then `uv run pickem publish-dashboard`.
- Confirm that `/status` in Discord ends with the link, and that the link opens the "This week" tab on the phone.
