# NFL Slight Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the NFL board, follow the market on any non-zero gap (new SLIGHT tier), keep CFB unchanged, track key-number crossings, and warn in the weekly report when the SLIGHT tier is clearly losing.

**Architecture:** `Thresholds` gains an opt-in `slight` flag that the tiering honours. Only live recommendations turn it on, and only for NFL, so the backtest and experiments replay history unchanged. A pure key-number helper feeds the pick rationale, the report's split rows and the dashboard. The weekly report adds a pre-declared early-stop warning.

**Tech Stack:** Python 3.12, pydantic, Typer, DuckDB, loguru, discord.py, vanilla JS dashboard tested under Node, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-nfl-slight-tier-design.md`

## Global Constraints

- NFL tiers: ≥ 2.0 STRONG; ≥ 1.0 LEAN; > 0 and < 1.0 **SLIGHT** (market's side); exactly 0 COINFLIP (CBS favorite); no market NO_MARKET (Elo).
- CFB tiers unchanged: < 1.0 is COINFLIP.
- `decide_edges` defaults do not change; backtest, COINFLIP experiments and the weekly-win simulator keep today's behaviour.
- The market is unchanged: US-book median (`consensus_spread`), Pinnacle excluded.
- Key number: 3 or 7, either sign, lying between the CBS line and the market consensus, inclusive. It never changes the side.
- SLIGHT reference rate: 0.521, labelled as the 0.5–1.0 band of the 2020–2025 NFL test on stand-in Tuesday lines.
- Early stop: when the NFL SLIGHT season record's 95% Wilson upper bound is below 0.50, the report findings and the Tuesday DM carry a warning naming the spec. No setting changes automatically.
- No new dependencies. Line length 100; ruff `E, F, I, UP, B`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH
  ```

## Review Focus

1. **Fractional gaps.** A two-book median can give a 0.25 gap. It must be SLIGHT on the market's side, never COINFLIP. Pinned in Task 1.
2. **NFL game with no market line.** Must stay NO_MARKET with the Elo pick, not SLIGHT. Pinned in Task 1.
3. **CFB with a small gap.** Live CFB must stay COINFLIP with the favorite. Pinned in Task 2.
4. **Stored history.** A recommendation stored with tier `slight` must round-trip through the database. Recommendations stored before the change keep their `coinflip` tier and are graded under it. Pinned in Tasks 2 and 4.
5. **Bot tier-change messages.** The bot looks up a badge for both the old and new tier. Every tier, SLIGHT included, must have one, or a coinflip→slight move would crash the message. Pinned in Task 3.

---

## File Structure

- Create `src/pickem/edge/key_numbers.py` — pure key-number helper.
- Modify `src/pickem/models.py` — `Tier.SLIGHT`.
- Modify `src/pickem/edge/divergence.py` — `Thresholds.slight`, `_tier`, the SLIGHT rationale note.
- Modify `src/pickem/operations/recommendations.py` — per-sport live thresholds.
- Modify `src/pickem/discord_bot.py` — SLIGHT badge.
- Modify `src/pickem/report/results.py`, `src/pickem/report/results_markdown.py`, `src/pickem/notify/discord_dm.py` — reference rate, key-number split, early-stop warning.
- Modify `src/pickem/report/dashboard.py`, `src/pickem/report/dashboard_assets/filters.js`, `src/pickem/report/dashboard_assets/tab_model.js`, `tests/js/parity.mjs`, `tests/js/filters.test.mjs`.
- Modify `docs/invariants.md`, `README.md`.

---

### Task 1: SLIGHT tier and key-number note in the decision code

**Files:**
- Create: `src/pickem/edge/key_numbers.py`
- Modify: `src/pickem/models.py` (`class Tier`)
- Modify: `src/pickem/edge/divergence.py` (`Thresholds`, `_tier`, `_compute_edge`)
- Test: `tests/test_key_numbers.py` (new), `tests/test_pipeline.py` (append), `tests/test_recommendations.py` (one expected dict)

**Interfaces:**
- Produces:
  - `Tier.SLIGHT = "slight"`, declared between `LEAN` and `COINFLIP`.
  - `Thresholds.slight: bool = False`
  - `pickem.edge.key_numbers.KEY_NUMBERS = (3, 7)` and `key_number_crossed(league_spread: float, market_spread: float) -> int | None`
  - SLIGHT edges carry the market's side, are never sent to a tiebreak, and their rationale ends with `"; crosses key number {k}"` when one is crossed.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_key_numbers.py`:

```python
import pytest

from pickem.edge.key_numbers import key_number_crossed


@pytest.mark.parametrize(
    ("league", "market", "expected"),
    [
        (-3.5, -3.0, 3),  # home favored: CBS 3.5, market 3
        (-2.5, -3.0, 3),
        (6.5, 7.0, 7),  # away favored by 7 in home-spread terms
        (-7.5, -6.75, 7),
        (-4.5, -5.0, None),
        (0.5, -0.5, None),
        (-3.0, -3.0, 3),  # inclusive at the number itself
    ],
)
def test_key_number_crossed(league, market, expected):
    assert key_number_crossed(league, market) == expected
```

Append to `tests/test_pipeline.py`:

```python
NFL_LIVE = Thresholds(slight=True)


def test_slight_follows_the_market_on_a_half_point_gap():
    [edge] = decide_edges([league(-4.5)], [market(-5.0)], [game()], HISTORY, NFL_LIVE)
    assert edge.tier is Tier.SLIGHT
    assert edge.side is Side.HOME
    assert "key number" not in edge.rationale


def test_slight_follows_a_fractional_gap():
    [edge] = decide_edges([league(-3.5)], [market(-3.25)], [game()], HISTORY, NFL_LIVE)
    assert edge.tier is Tier.SLIGHT
    assert edge.side is Side.AWAY


def test_slight_notes_a_key_number():
    [edge] = decide_edges([league(-3.5)], [market(-3.0)], [game()], HISTORY, NFL_LIVE)
    assert edge.tier is Tier.SLIGHT
    assert edge.side is Side.AWAY
    assert edge.rationale.endswith("; crosses key number 3")


def test_slight_never_runs_a_tiebreak(records):
    decide_edges([league(-3.5)], [market(-3.0)], [game()], HISTORY, NFL_LIVE)
    assert not [r for r in records if r["extra"].get("event") == "tiebreak_applied"]


def test_zero_gap_is_still_a_coinflip_for_the_favorite_with_slight_on():
    [edge] = decide_edges([league(-3.5)], [market(-3.5)], [game()], HISTORY, NFL_LIVE)
    assert edge.tier is Tier.COINFLIP
    assert edge.side is Side.HOME


@pytest.mark.parametrize(
    ("market_spread", "tier"), [(-1.5, Tier.LEAN), (-6.0, Tier.STRONG)]
)
def test_larger_gaps_keep_their_tiers_with_slight_on(market_spread, tier):
    [edge] = decide_edges([league(-3.0)], [market(market_spread)], [game()], HISTORY, NFL_LIVE)
    assert edge.tier is tier


def test_no_market_is_still_elo_with_slight_on():
    [edge] = decide_edges([league(-3.5)], [], [game()], HISTORY, NFL_LIVE)
    assert edge.tier is Tier.NO_MARKET
    assert "Elo" in edge.rationale


def test_default_thresholds_keep_small_gaps_as_coinflips():
    [edge] = decide_edges([league(-3.5)], [market(-3.0)], [game()], HISTORY)
    assert edge.tier is Tier.COINFLIP
    assert edge.side is Side.HOME
```

In `tests/test_recommendations.py`, `test_generation_logs_an_exact_summary`, change the expected `tiers` dict to include the new tier (every `Tier` member is a key):

```python
    assert summary["extra"]["tiers"] == {
        Tier.STRONG.value: 0,
        Tier.LEAN.value: 0,
        Tier.SLIGHT.value: 0,
        Tier.COINFLIP.value: 0,
        Tier.NO_MARKET.value: 1,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_key_numbers.py tests/test_pipeline.py tests/test_recommendations.py -q`
Expected: FAIL — `ModuleNotFoundError: pickem.edge.key_numbers`, then `Thresholds` has no `slight`, and `Tier` has no `SLIGHT`.

- [ ] **Step 3: Write the implementation**

Create `src/pickem/edge/key_numbers.py`:

```python
"""Football's common winning margins, and whether a small gap spans one.

NFL games end exactly 3 points apart about 15% of the time and exactly 7 apart
about 8% (2021-2025). CBS posts half points, so when the market sits on 3 or 7
the half point between them decides every game that lands there. That is worth
far more than a half point anywhere else.

Pure functions only. No network, no database, no filesystem.
"""

from __future__ import annotations

KEY_NUMBERS = (3, 7)


def key_number_crossed(league_spread: float, market_spread: float) -> int | None:
    """The key number lying between the two lines, inclusive; None if neither does.

    Both spreads are home-perspective, so either sign of 3 or 7 counts.
    """
    low, high = sorted((league_spread, market_spread))
    for key in KEY_NUMBERS:
        if low <= key <= high or low <= -key <= high:
            return key
    return None
```

In `src/pickem/models.py`, add the member between `LEAN` and `COINFLIP`:

```python
class Tier(StrEnum):
    STRONG = "strong"
    LEAN = "lean"
    SLIGHT = "slight"
    COINFLIP = "coinflip"
    NO_MARKET = "no_market"
```

In `src/pickem/edge/divergence.py`, extend `Thresholds`:

```python
class Thresholds(BaseModel):
    """Tier cutoffs in points of divergence.

    These defaults are initial guesses, to be replaced by backtested values.
    They are configuration, not logic.

    ``slight`` turns any non-zero gap under ``lean`` into SLIGHT, which follows
    the market instead of falling to the COINFLIP tiebreak. Off by default so
    history replays unchanged; live NFL turns it on
    (docs/superpowers/specs/2026-10-06-nfl-slight-tier-design.md).
    """

    strong: float = 2.0
    lean: float = 1.0
    slight: bool = False
```

Replace `_tier`:

```python
def _tier(delta: float, thresholds: Thresholds) -> Tier:
    magnitude = abs(delta)
    if magnitude >= thresholds.strong:
        return Tier.STRONG
    if magnitude >= thresholds.lean:
        return Tier.LEAN
    if thresholds.slight and magnitude > 0:
        return Tier.SLIGHT
    return Tier.COINFLIP
```

In `_compute_edge`, add the import `from pickem.edge.key_numbers import key_number_crossed` at the top of the module. Then replace the final `return Edge(...)` of the market branch so the rationale gains the note:

```python
    rationale = (
        f"league {league.spread_home:+.1f} vs market {consensus:+.1f}: "
        f"{abs(delta):.1f} pts toward {moved_toward}"
    )
    if tier is Tier.SLIGHT:
        key = key_number_crossed(league.spread_home, consensus)
        if key is not None:
            rationale += f"; crosses key number {key}"
    return Edge(
        game_id=league.game_id,
        side=side,
        delta=delta,
        tier=tier,
        league_spread=league.spread_home,
        market_spread=consensus,
        rationale=rationale,
    )
```

`edge/pipeline.py` needs no change: `_TIEBREAK_TIERS` is `{COINFLIP, NO_MARKET}`, so SLIGHT passes through with its market side.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_key_numbers.py tests/test_pipeline.py tests/test_divergence.py tests/test_recommendations.py -q && uv run ruff check .`
Expected: all PASS; ruff clean.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS. Defaults are unchanged, so any failure is a test that enumerates every `Tier` member exhaustively. Add `Tier.SLIGHT` to that test's expectation, and record each such test in the ledger. Do not change any test's expected tier for a game.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/edge/key_numbers.py src/pickem/models.py src/pickem/edge/divergence.py tests/
git commit -m "feat: add an opt-in slight tier that follows the market on small gaps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 2: Turn SLIGHT on for live NFL picks only

**Files:**
- Modify: `src/pickem/operations/recommendations.py` (constant + `generate_recommendations`)
- Test: `tests/test_recommendations.py` (append), `tests/test_recommendation_history.py` (append)

**Interfaces:**
- Consumes: `Thresholds(slight=True)`, `Tier.SLIGHT` (Task 1).
- Produces: `LIVE_THRESHOLDS: dict[Sport, Thresholds]` in `pickem.operations.recommendations`; `generate_recommendations` passes `LIVE_THRESHOLDS[sport]` to `decide_edges`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recommendations.py` (`LIVE_SOURCE` is already imported there):

```python
def _seed_small_gap(db, sport, game_id, *, league=-3.5, market=-3.0):
    kickoff = datetime(2026, 10, 11, 17, tzinfo=UTC)
    with Store(db) as store:
        store.init_schema()
        store.upsert_games([
            Game(game_id=game_id, sport=sport, season=2026, week=6, kickoff_utc=kickoff,
                 home_team_id="home", away_team_id="away")
        ])
        store.upsert_league_lines([
            LeagueLine(game_id=game_id, season=2026, week=6, spread_home=league,
                       posted_at=kickoff - timedelta(days=5))
        ])
        store.append_market_lines([
            MarketLine(game_id=game_id, source=LIVE_SOURCE, book="draftkings",
                       spread_home=market, captured_at=kickoff - timedelta(hours=2))
        ])


def test_live_nfl_follows_the_market_on_a_small_gap(db):
    _seed_small_gap(db, Sport.NFL, "nfl:small")
    [edge] = generate_recommendations(
        db, Sport.NFL, 2026, 6, datetime(2026, 10, 11, 15, tzinfo=UTC)
    ).edges
    assert edge.tier is Tier.SLIGHT
    assert edge.side.value == "away"
    assert "crosses key number 3" in edge.rationale


def test_live_cfb_keeps_small_gaps_as_coinflips(db):
    _seed_small_gap(db, Sport.CFB, "cfb:small")
    [edge] = generate_recommendations(
        db, Sport.CFB, 2026, 6, datetime(2026, 10, 11, 15, tzinfo=UTC)
    ).edges
    assert edge.tier is Tier.COINFLIP
    assert edge.side.value == "home"
```

Before writing, check the store's method name for appending market lines (`grep -n "def .*market" src/pickem/store/db.py`). If it is not `append_market_lines`, use the real name and record a ledger ruling.

Append to `tests/test_recommendation_history.py` a round-trip for the new tier. Copy that file's existing fixture and record-building pattern, set `tier=Tier.SLIGHT`, append it with `store.append_recommendation_history([...])`, read it back with `store.recommendation_history([game_id])`, and assert `tier is Tier.SLIGHT`:

```python
def test_slight_tier_round_trips(tmp_path):
    from datetime import UTC, datetime

    from pickem.models import RecommendationRecord, Side, Sport, Tier
    from pickem.store.db import Store

    record = RecommendationRecord(
        game_id="nfl:rt", sport=Sport.NFL, season=2026, week=6, side=Side.AWAY,
        tier=Tier.SLIGHT, edge_points=-0.5,
        generated_at=datetime(2026, 10, 11, 15, tzinfo=UTC), source="refresh",
    )
    with Store(tmp_path / "rt.duckdb") as store:
        store.init_schema()
        store.append_recommendation_history([record])
        [back] = store.recommendation_history(["nfl:rt"])
    assert back.tier is Tier.SLIGHT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_recommendations.py tests/test_recommendation_history.py -q`
Expected: `test_live_nfl_follows_the_market_on_a_small_gap` FAILS (tier is COINFLIP). The CFB test and the round-trip pass already. The round-trip passing is a finding to note, not a problem.

- [ ] **Step 3: Write the implementation**

In `src/pickem/operations/recommendations.py`, add `from pickem.edge.divergence import Thresholds` (beside the existing `rank_edges` import) and, below the imports:

```python
# Tier settings for live picks, per sport. NFL follows the market on any gap
# (the SLIGHT tier; docs/superpowers/specs/2026-10-06-nfl-slight-tier-design.md).
# To revert NFL, set it back to Thresholds().
LIVE_THRESHOLDS: dict[Sport, Thresholds] = {
    Sport.NFL: Thresholds(slight=True),
    Sport.CFB: Thresholds(),
}
```

In `generate_recommendations`, pass it to `decide_edges`:

```python
        edges = decide_edges(
            _still_pending(dataset, pending_as_of),
            # Pinnacle is recorded for a later comparison, not used: the
            # consensus stays the US books' median until a backtest says so.
            [line for line in dataset.market_lines if line.source != PINNACLE_SOURCE],
            dataset.games,
            store.games_before(sport, season, week),
            LIVE_THRESHOLDS[sport],
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_recommendations.py tests/test_recommendation_history.py -q && uv run ruff check .`
Expected: all PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS, except tests that drive live NFL recommendations through a small gap and expect COINFLIP. Typical places are `test_end_to_end.py`, `test_report.py`, `test_monitor.py` and `test_discord_bot.py`. For each one:
- Confirm the game is NFL with a gap above 0 and under 1.
- Update the expectation to SLIGHT and the market's side.
- Record the test name and the old and new expectation in the ledger as a ruling.

Never change a CFB expectation, and never change a test whose gap is 0 or 1 or more. If a failure fits none of these, stop and investigate with superpowers:systematic-debugging.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/operations/recommendations.py tests/
git commit -m "feat: follow the market on any gap for live NFL picks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 3: Bot badge for SLIGHT

**Files:**
- Modify: `src/pickem/discord_bot.py` (`_TIER_BADGES`)
- Test: `tests/test_discord_bot.py` (append)

**Interfaces:**
- Consumes: `Tier.SLIGHT` (Task 1).
- Produces: `_TIER_BADGES[Tier.SLIGHT] == "🎯 Slight"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_discord_bot.py`:

```python
def test_every_tier_has_a_badge():
    from pickem.discord_bot import _TIER_BADGES
    from pickem.models import Tier

    assert set(_TIER_BADGES) == set(Tier)
    assert _TIER_BADGES[Tier.SLIGHT] == "🎯 Slight"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_discord_bot.py::test_every_tier_has_a_badge -q`
Expected: FAIL — `Tier.SLIGHT` missing from the badges.

- [ ] **Step 3: Implement**

```python
_TIER_BADGES = {
    Tier.STRONG: "🔥 Strong",
    Tier.LEAN: "✅ Lean",
    Tier.SLIGHT: "🎯 Slight",
    Tier.COINFLIP: "🪙 Coinflip",
    Tier.NO_MARKET: "⚠️ No market",
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_discord_bot.py -q && uv run ruff check .`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/discord_bot.py tests/test_discord_bot.py
git commit -m "feat: show the slight tier in bot messages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 4: Report — reference rate, key-number split, early-stop warning

**Files:**
- Modify: `src/pickem/report/results.py`
- Modify: `src/pickem/report/results_markdown.py` (`_season`)
- Modify: `src/pickem/notify/discord_dm.py` (`build_results_embed`)
- Test: `tests/test_results_slight.py` (new)

**Interfaces:**
- Consumes: `Tier.SLIGHT`, `key_number_crossed` (Task 1).
- Produces (in `pickem.report.results`):
  - `BACKTEST_TIER_RATES` gains `Tier.SLIGHT: 0.521` (insertion order: STRONG, LEAN, SLIGHT, COINFLIP).
  - `SLIGHT_REFERENCE_NOTE: str`, `SLIGHT_SPEC: str`
  - `GradedGame.key_number -> int | None` (property)
  - `record_for(..., key_number: bool | None = None)`
  - `slight_stop_warning(games: Iterable[GradedGame]) -> str | None`
  - `findings()` puts the warning first in `claims` when present.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_results_slight.py`:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from dashboard_helpers import synthetic_season

from pickem.models import Game, RecommendationRecord, Side, Sport, Tier
from pickem.notify.discord_dm import build_results_embed
from pickem.report.results import (
    Strategy,
    findings,
    grade_game,
    record_for,
    slight_stop_warning,
)
from pickem.report.results_markdown import _season

KICK = datetime(2026, 10, 4, 17, tzinfo=UTC)


def graded(gid="g", *, sport=Sport.NFL, line=-3.5, tier=Tier.SLIGHT, delta=-0.5,
           side=Side.AWAY, home=20, away=17):
    game = Game(game_id=gid, sport=sport, season=2026, week=6, kickoff_utc=KICK,
                home_team_id="H", away_team_id="A", home_score=home, away_score=away)
    rec = RecommendationRecord(game_id=gid, sport=sport, season=2026, week=6, side=side,
                               tier=tier, edge_points=delta,
                               generated_at=KICK - timedelta(hours=1), source="refresh")
    return grade_game(game=game, pool_week=6, league_spread=line, close_spread=None,
                      our_side=side, field_home=0, field_away=0, history=[rec])


def test_key_number_comes_from_the_models_market():
    assert graded(line=-3.5, delta=-0.5).key_number == 3  # market -3.0
    assert graded(line=-4.5, delta=0.5, side=Side.HOME).key_number is None  # market -5.0
    assert graded(tier=Tier.LEAN, delta=-1.5).key_number is None


def test_record_for_splits_slight_by_key_number():
    win_key = graded("a")  # away covers -3.5 on a 3-point home win
    loss_other = graded("b", line=-4.5, delta=0.5, side=Side.HOME, home=20, away=17)
    games = [win_key, loss_other]
    crossed = record_for(games, Strategy.MODEL, tier=Tier.SLIGHT, key_number=True)
    other = record_for(games, Strategy.MODEL, tier=Tier.SLIGHT, key_number=False)
    assert (crossed.wins, crossed.losses) == (1, 0)
    assert (other.wins, other.losses) == (0, 1)


def losing_slight(n, sport=Sport.NFL):
    # Model on the home side at -4.5, home wins by 3: a loss every time.
    return [graded(f"l{i}", sport=sport, line=-4.5, delta=0.5, side=Side.HOME)
            for i in range(n)]


def test_warning_when_slight_is_clearly_below_half():
    text = slight_stop_warning(losing_slight(12))
    assert text is not None
    assert "2026-10-06-nfl-slight-tier-design.md" in text
    assert "revert" in text


def test_no_warning_when_slight_is_uncertain():
    games = losing_slight(5) + [graded(f"w{i}") for i in range(5)]
    assert slight_stop_warning(games) is None
    assert slight_stop_warning([]) is None


def test_cfb_games_never_trigger_the_warning():
    assert slight_stop_warning(losing_slight(12, sport=Sport.CFB)) is None


def test_findings_lead_with_the_warning():
    assert findings(losing_slight(12)).claims[0] == slight_stop_warning(losing_slight(12))


def test_markdown_splits_slight_rows_and_labels_the_reference():
    games = [graded("a"), graded("b", line=-4.5, delta=0.5, side=Side.HOME)]
    text = "\n".join(_season(SimpleNamespace(season_games=tuple(games), unknown_model_games=0),
                             [Sport.NFL]))
    assert "| NFL | slight, crosses 3 or 7 |" in text
    assert "| NFL | slight, other |" in text
    assert "stand-in" in text


def test_markdown_omits_slight_rows_for_a_board_without_slight_picks():
    games = [graded("c", sport=Sport.CFB, tier=Tier.COINFLIP, delta=0.0, side=Side.HOME)]
    text = "\n".join(_season(SimpleNamespace(season_games=tuple(games), unknown_model_games=0),
                             [Sport.CFB]))
    assert "slight" not in text


def test_dm_carries_the_warning(monkeypatch):
    monkeypatch.setattr("pickem.notify.discord_dm.slight_stop_warning", lambda games: "WARN")
    embed = build_results_embed(synthetic_season(), Path("report.md"))
    assert any(field.value == "WARN" for field in embed.fields)


def test_dm_has_no_warning_field_normally(monkeypatch):
    monkeypatch.setattr("pickem.notify.discord_dm.slight_stop_warning", lambda games: None)
    embed = build_results_embed(synthetic_season(), Path("report.md"))
    assert not any("early stop" in field.name.lower() for field in embed.fields)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_results_slight.py -q`
Expected: FAIL — `ImportError: cannot import name 'slight_stop_warning'`.

- [ ] **Step 3: Implement in `src/pickem/report/results.py`**

Add the import `from pickem.edge.key_numbers import key_number_crossed`, and add `Sport` and `Tier` to the `pickem.models` import if they are missing. Replace `BACKTEST_TIER_RATES` and add the two constants:

```python
# NFL 2020-2025 backtest (docs/results.md). Shown as NFL numbers even beside CFB.
# SLIGHT is the 0.5-1.0 band of the threshold test, on stand-in lines.
BACKTEST_TIER_RATES = {
    Tier.STRONG: 0.637,
    Tier.LEAN: 0.542,
    Tier.SLIGHT: 0.521,
    Tier.COINFLIP: 0.495,
}
SLIGHT_REFERENCE_NOTE = (
    "slight's reference is the 0.5–1.0 point band of the 2020–2025 NFL test, measured "
    "on stand-in Tuesday sportsbook lines rather than real CBS lines"
)
SLIGHT_SPEC = "docs/superpowers/specs/2026-10-06-nfl-slight-tier-design.md"
```

Add to `GradedGame`:

```python
    @property
    def key_number(self) -> int | None:
        """3 or 7 when the model's SLIGHT pick spanned that margin, else None."""
        if self.model is None or self.model.tier is not Tier.SLIGHT:
            return None
        market = self.league_spread - self.model.edge_points
        return key_number_crossed(self.league_spread, market)
```

Extend `record_for`:

```python
def record_for(
    games: Iterable[GradedGame],
    strategy: Strategy,
    *,
    sport: Sport | None = None,
    tier: Tier | None = None,
    key_number: bool | None = None,
) -> Record:
    """W-L-P for one strategy; ``tier`` filters on the model's tier for the game.

    ``key_number`` keeps only SLIGHT games that did (True) or did not (False)
    span 3 or 7.
    """
    counts = {Result.WIN: 0, Result.LOSS: 0, Result.PUSH: 0}
    for game in games:
        if sport is not None and game.game.sport is not sport:
            continue
        if tier is not None and (game.model is None or game.model.tier is not tier):
            continue
        if key_number is not None and (game.key_number is not None) is not key_number:
            continue
        result = game.result(strategy)
        if result is not None:
            counts[result] += 1
    return Record(counts[Result.WIN], counts[Result.LOSS], counts[Result.PUSH])
```

Add the warning, just before `findings`:

```python
def slight_stop_warning(games: Iterable[GradedGame]) -> str | None:
    """The early-stop rule declared with the SLIGHT tier, or None while it holds.

    Fires only when the whole 95% interval of the NFL SLIGHT record is below 50%.
    """
    record = record_for(games, Strategy.MODEL, sport=Sport.NFL, tier=Tier.SLIGHT)
    if not record.decided or record.interval[1] >= 0.5:
        return None
    return (
        f"NFL slight tier {record} is clearly below 50%: the early-stop rule in "
        f"{SLIGHT_SPEC} says to revert NFL to the old tiers"
    )
```

In `findings`, right after `not_yet: list[str] = []`:

```python
    warning = slight_stop_warning(games)
    if warning is not None:
        claims.append(warning)
```

- [ ] **Step 4: Implement in `src/pickem/report/results_markdown.py` (`_season`)**

Add `SLIGHT_REFERENCE_NOTE` to the `pickem.report.results` import, and `Tier` to the `pickem.models` import. Replace the tier loop and the line after the table:

```python
    shown_slight = False
    for sport in sports:
        for tier, expected in BACKTEST_TIER_RATES.items():
            if tier is Tier.SLIGHT:
                if not record_for(games, Strategy.MODEL, sport=sport, tier=tier).decided:
                    continue
                shown_slight = True
                for label, crossed in (("slight, crosses 3 or 7", True), ("slight, other", False)):
                    record = record_for(
                        games, Strategy.MODEL, sport=sport, tier=tier, key_number=crossed
                    )
                    lines.append(
                        f"| {sport.value.upper()} | {label} | {record} | {expected:.1%}* |"
                    )
                continue
            record = record_for(games, Strategy.MODEL, sport=sport, tier=tier)
            lines.append(f"| {sport.value.upper()} | {tier.value} | {record} | {expected:.1%} |")
    if shown_slight:
        lines += ["", f"\\* {SLIGHT_REFERENCE_NOTE[0].upper()}{SLIGHT_REFERENCE_NOTE[1:]}."]
```

Keep the existing lines that follow, starting with `"",` and the "Games with no recommendation stored…" line.

- [ ] **Step 5: Implement in `src/pickem/notify/discord_dm.py`**

Add `slight_stop_warning` to the `pickem.report.results` import. Right after the "Season to date — model by tier" field:

```python
    warning = slight_stop_warning(report.season_games)
    if warning is not None:
        embed.add_field(name="⚠️ Slight tier early stop", value=_cap(warning), inline=False)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_results_slight.py tests/test_results_report.py tests/test_results_markdown.py tests/test_discord_dm.py -q && uv run ruff check .`
Expected: all PASS. If an existing markdown or DM test pins the exact tier list, the new SLIGHT entry changes it in one of two ways. The DM tiers field gains a "slight" line. The markdown shows slight rows only when slight games exist. Update such tests to the new list and record each in the ledger.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/report/results.py src/pickem/report/results_markdown.py src/pickem/notify/discord_dm.py tests/
git commit -m "feat: grade the slight tier by key number and warn when it is losing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 5: Dashboard — SLIGHT in filters and the Model tab

**Files:**
- Modify: `src/pickem/report/dashboard.py` (`_game`)
- Modify: `src/pickem/report/dashboard_assets/filters.js`, `src/pickem/report/dashboard_assets/tab_model.js`
- Modify: `tests/js/parity.mjs`, `tests/js/filters.test.mjs`, `tests/test_dashboard_js.py`, `tests/test_dashboard_data.py`

**Interfaces:**
- Consumes: `GradedGame.key_number`, `BACKTEST_TIER_RATES[Tier.SLIGHT]` (Task 4).
- Produces: each game in the dashboard data carries `"key_number": int | null`; the tier filter accepts `slight`; the Model tab shows two SLIGHT rows per board that has SLIGHT games.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_data.py`:

```python
def test_games_carry_their_key_number():
    from dashboard_helpers import GENERATED, synthetic_season

    from pickem.report.dashboard import build_dashboard_data

    report = synthetic_season()
    data = build_dashboard_data(report, generated_at=GENERATED)
    assert all("key_number" in g for g in data["games"])
    assert data["backtest"]["slight"] == 0.521
```

Append to `tests/js/filters.test.mjs`, following that file's existing `test(...)`/`assert` and `data` setup:

```js
test("the slight tier is a valid filter", () => {
  const state = plain(F.parseHash("#tier=slight,lean", data));
  assert.deepEqual(state.tiers, ["lean", "slight"]);
  assert.equal(F.TIER_LABELS.slight, "Slight");
});
```

In `tests/js/parity.mjs`, change the tier list to:

```js
  for (const tier of ["any", "strong", "lean", "slight", "coinflip", "no_market"]) {
```

In `tests/test_dashboard_js.py`, add `"slight": Tier.SLIGHT,` to the `TIERS` dict, after `"lean"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dashboard_data.py tests/test_dashboard_js.py -q`
Expected: FAIL — no `key_number` in game data, and the JS filter rejects `slight`.

- [ ] **Step 3: Implement**

`src/pickem/report/dashboard.py`, in `_game`, after `"clv": graded.clv,`:

```python
        "key_number": graded.key_number,
```

`src/pickem/report/dashboard_assets/filters.js`:

```js
  const TIERS = ["strong", "lean", "slight", "coinflip", "no_market", "none"];
```

```js
  const TIER_LABELS = {
    strong: "Strong", lean: "Lean", slight: "Slight", coinflip: "Coinflip", no_market: "No market",
    none: "No model",
  };
```

`src/pickem/report/dashboard_assets/tab_model.js`: set `const TIERS = ["strong", "lean", "slight", "coinflip"];` and replace `tierRows`:

```js
  function tierRows(view) {
    const sports = ["cfb", "nfl"].filter((sp) => view.games.some((g) => g.sport === sp));
    return sports.flatMap((sport) => TIERS.flatMap((tier) => {
      const games = view.games.filter((g) => g.sport === sport && F.tierOf(g) === tier);
      const expected = view.data.backtest[tier];
      const source = tier === "slight" ? "stand-in-line test"
        : sport === "cfb" ? "NFL backtest" : "backtest";
      const row = (label, subset) => ({
        label: `${LABELS.sport[sport]} ${label}`,
        note: `${source} ${fmt.pct(expected, 1)}`,
        record: P.stats.record(subset, "model"), expected,
        games: F.gradedFor(subset, "model"), strategy: "model",
      });
      if (tier !== "slight") return [row(F.TIER_LABELS[tier].toLowerCase(), games)];
      if (!games.length) return [];
      return [
        row("slight · crosses 3 or 7", games.filter((g) => g.key_number != null)),
        row("slight · other", games.filter((g) => g.key_number == null)),
      ];
    }));
  }
```

The parse order in `filters.js` already follows `TIERS`, which is why the expected filter result is `["lean", "slight"]`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_dashboard_data.py tests/test_dashboard_js.py tests/test_dashboard_page.py -q && uv run ruff check .`
Expected: all PASS.

- [ ] **Step 5: Render check (no new test, per the project's convention for visual changes)**

Build the page from a copy of the live database and look at the Model tab. The NFL rows should read strong, lean, coinflip. There should be no slight rows yet, since no game has a SLIGHT recommendation:

```bash
SP=/tmp/claude-1000/-home-jmiller-cfb/e5199453-4984-4aa3-88a3-429ec39d1c69/scratchpad
cp /home/jmiller/cfb/data/pickem.duckdb $SP/render.duckdb
PICKEM_LOG_DIR=$SP/logs PICKEM_DASHBOARD_DIR=$SP/dash uv run pickem results-report \
  --season 2026 --db $SP/render.duckdb --out-dir $SP/reports --no-notify
```

Expected: exits 0 and writes `$SP/dash/index.html`. Open it, or check it with the browser tools, and confirm the Model tab renders without console errors.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/report/dashboard.py src/pickem/report/dashboard_assets/ tests/
git commit -m "feat: show the slight tier on the dashboard, split by key number

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 6: Record the decision

**Files:**
- Modify: `docs/invariants.md` (the "tier thresholds stay at 2.0/1.0" bullet)
- Modify: `README.md` (the tier table under "Concepts you need before running anything")

- [ ] **Step 1: Amend `docs/invariants.md`**

Directly after the bullet that starts `- **The tier thresholds stay at 2.0/1.0** (2026-08-19).`, insert:

```markdown
- **Amended for NFL (2026-10-06): the SLIGHT tier.** Live NFL picks follow the
  market on any non-zero gap under 1.0 (tier `slight`); a zero gap is still
  COINFLIP with the CBS favorite. CFB and every historical replay keep 2.0/1.0
  (`Thresholds.slight` is off by default; `LIVE_THRESHOLDS` in
  `operations/recommendations.py` turns it on for NFL only). Reason: the sweep
  above used Tuesday sportsbook lines as stand-ins for CBS lines, so it cannot
  show whether CBS's own lines are worse than the books'. The 2026 real-CBS
  record of following the closing market was NFL 32–15 overall and 15–8 on
  gaps under 1 (CFB 28–31, unchanged). The rule was chosen after seeing that
  record, so an early stop is declared: the weekly report warns when the NFL
  SLIGHT record's 95% interval lies wholly below 50%. Revert by setting NFL
  back to `Thresholds()`. Spec:
  `docs/superpowers/specs/2026-10-06-nfl-slight-tier-design.md`.
```

- [ ] **Step 2: Update `README.md`'s tier table**

Insert a row between `lean` and `coinflip`, and qualify the coinflip row:

```markdown
| `slight` | NFL only: any gap above 0 and under 1.0 point. Follows the market, like lean. Notes when the gap crosses 3 or 7 |
| `coinflip` | Less than 1.0 point on the CFB board, exactly 0 on the NFL board — no real edge. Resolved by taking the frozen-board favorite (home when the frozen spread is ≤ 0) |
```

(The second line replaces the existing `coinflip` row.)

- [ ] **Step 3: Full checks and commit**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all PASS.

```bash
git add docs/invariants.md README.md
git commit -m "docs: record the NFL slight tier decision

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```
