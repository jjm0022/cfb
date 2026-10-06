# Weekly-Win Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only `pickem simulate-weekly-win` command that estimates, per imported pool week, how often each COINFLIP rule would have finished first in the pool.

**Architecture:** A pure module (`backtest/pool_sim.py`) holds the week model, the five rules, the seeded Monte Carlo simulation (bitmask scoring, common random numbers), the optimal-mix search and the text report. A loader (`operations/pool_sim_inputs.py`) builds the week model from the store. The CLI command opens the store read-only, loads, simulates, prints.

**Tech Stack:** Python 3.12, standard library only (`random`, `int.bit_count`, `bisect`, `statistics`), Typer, DuckDB via the existing `Store`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-weekly-win-simulator-design.md`

## Global Constraints

- Per-game probabilities: model side wins 0.637 on STRONG, 0.542 on LEAN; COINFLIP, NO_MARKET and "no recommendation" games are 0.5. Never fitted to 2026 outcomes.
- Lopsided threshold: underdog when ≥ 0.70 of other entrants who picked took the favorite. Fixed; do not tune.
- Ties for first share the win equally (k-way tie for first = 1/k win). Pushes are not modelled.
- A missing pick (blank) is always wrong.
- Default 20,000 draws; fixed default seed; same seed → identical output.
- Season projection: chance of at least one weekly win in 18 weeks = `1 − (1 − p)^18`.
- Rules marked as ceilings (minority, lopsided underdog, optimal mix) must be labelled as not playable live in the output.
- Read-only: the command never writes to the database.
- No new dependencies. Line length 100; ruff rules `E, F, I, UP, B`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH
  ```

## Review Focus

1. **Blank picks.** An entrant who left a game (or every game) blank must score it wrong, never crash or count it right. Pinned in Task 1.
2. **A game with no stored recommendation.** Treated as 50/50, keeps our submitted pick under every rule, and is counted in the output. Pinned in Tasks 1 and 4.
3. **A week with no COINFLIP games.** The optimal mix must equal the current rule and report no switches. Pinned in Task 2.
4. **Bad inputs at the command line.** Unknown entry name, a pool week that is not imported, a malformed `--pool-weeks`, or a missing database file must print a clear message and exit 1. Pinned in Task 5.
5. **The live database.** The command must open it read-only and leave the file byte-identical, since the Discord bot uses it at the same time. Pinned in Task 5.

---

## File Structure

- Create `src/pickem/backtest/pool_sim.py` — pure simulation: types, rules, draws, evaluation, search, season summary, text report.
- Create `src/pickem/operations/pool_sim_inputs.py` — builds a `SimWeek` from the store.
- Modify `src/pickem/cli.py` — the `simulate-weekly-win` command and a week-range parser.
- Create `tests/test_pool_sim.py`, `tests/test_pool_sim_inputs.py`, `tests/test_pool_sim_cli.py`.
- Modify `README.md` — one row in the "Historical / analysis" command table.
- Modify `docs/results.md` — the write-up (Task 6).

---

### Task 1: Week model, rules, draws and evaluation

**Files:**
- Create: `src/pickem/backtest/pool_sim.py`
- Test: `tests/test_pool_sim.py`

**Interfaces:**
- Consumes: `pickem.models.Side`, `pickem.models.Tier`, `pickem.edge.favorite.favorite_side(league_spread: float) -> Side` (HOME when spread ≤ 0).
- Produces:
  - `TIER_PROBABILITY: dict[Tier, float]`, `LOPSIDED_SHARE = 0.70`, `SEASON_WEEKS = 18`, `DEFAULT_DRAWS = 20_000`, `DEFAULT_SEED = 20261005`
  - `class Rule(StrEnum)`: `CURRENT="current"`, `UNDERDOG="underdog"`, `MINORITY="minority"`, `LOPSIDED="lopsided underdog"`, `OPTIMAL="optimal mix"`; `CEILINGS: frozenset[Rule]`
  - `opposite(side: Side) -> Side`
  - `@dataclass(frozen=True) SimGame(game_id: str, label: str, league_spread: float, tier: Tier | None, model_side: Side)` with properties `favorite: Side`, `is_coinflip: bool`, `p_home: float`
  - `@dataclass(frozen=True) SimWeek(pool_week: int, games: tuple[SimGame, ...], others: tuple[tuple[Side | None, ...], ...], unrecommended: int = 0)` with `entrants: int` property, `field_split(i: int) -> tuple[int, int]` (home, away) and `favorite_share(i: int) -> float | None`
  - `rule_sides(week: SimWeek, rule: Rule) -> tuple[Side, ...]` (raises `ValueError` for `Rule.OPTIMAL`)
  - `@dataclass(frozen=True) Draws(full: int, outcomes: tuple[int, ...], best: tuple[int, ...], best_count: tuple[int, ...], others_sorted: tuple[tuple[int, ...], ...])`
  - `draw(week: SimWeek, draws: int, seed: str) -> Draws`
  - `@dataclass(frozen=True) RuleResult(rule: Rule, sides: tuple[Side, ...], avg_points: float, win_chance: float, top3_chance: float, median_rank: float)`
  - `evaluate(rule: Rule, sides: Sequence[Side], draws: Draws) -> RuleResult`
  - `win_chance(sides: Sequence[Side], draws: Draws) -> float`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pool_sim.py`:

```python
import pytest

from pickem.backtest.pool_sim import (
    Rule,
    SimGame,
    SimWeek,
    draw,
    evaluate,
    rule_sides,
    win_chance,
)
from pickem.models import Side, Tier

H, A = Side.HOME, Side.AWAY


def game(i, tier=Tier.COINFLIP, spread=-3.5, model_side=H):
    return SimGame(
        game_id=f"g{i}", label=f"A{i} at H{i}", league_spread=spread,
        tier=tier, model_side=model_side,
    )


def week(games, others, pool_week=1, unrecommended=0):
    return SimWeek(
        pool_week=pool_week, games=tuple(games),
        others=tuple(tuple(row) for row in others), unrecommended=unrecommended,
    )


def test_rules_differ_only_on_coinflips():
    # g0: coinflip, home favored, every other entrant on the favorite.
    # g1: strong, model on the away side.
    w = week([game(0), game(1, tier=Tier.STRONG, model_side=A)], [[H, H]] * 3)
    assert rule_sides(w, Rule.CURRENT) == (H, A)
    assert rule_sides(w, Rule.UNDERDOG) == (A, A)
    assert rule_sides(w, Rule.MINORITY) == (A, A)
    assert rule_sides(w, Rule.LOPSIDED) == (A, A)


def test_minority_takes_the_favorite_on_an_exact_split():
    w = week([game(0)], [[H], [A]])
    assert rule_sides(w, Rule.MINORITY) == (H,)


def test_minority_takes_the_favorite_when_the_pool_prefers_the_underdog():
    w = week([game(0)], [[A], [A], [H]])
    assert rule_sides(w, Rule.MINORITY) == (H,)


def test_lopsided_threshold_is_seventy_percent_of_entrants_who_picked():
    seven_of_ten = [[H]] * 7 + [[A]] * 3 + [[None]] * 5
    six_of_ten = [[H]] * 6 + [[A]] * 4
    assert rule_sides(week([game(0)], seven_of_ten), Rule.LOPSIDED) == (A,)
    assert rule_sides(week([game(0)], six_of_ten), Rule.LOPSIDED) == (H,)


def test_favorite_follows_the_cbs_spread():
    # +6.5 home: the away team is favored.
    w = week([game(0, spread=6.5)], [[A]])
    assert rule_sides(w, Rule.CURRENT) == (A,)
    assert rule_sides(w, Rule.UNDERDOG) == (H,)


def test_optimal_is_not_a_fixed_rule():
    with pytest.raises(ValueError):
        rule_sides(week([game(0)], [[H]]), Rule.OPTIMAL)


def test_game_without_a_recommendation_is_even_and_keeps_our_side():
    g = game(0, tier=None, model_side=A)
    assert g.p_home == 0.5
    w = week([g], [[H]])
    assert all(rule_sides(w, rule) == (A,) for rule in Rule if rule is not Rule.OPTIMAL)


def test_blank_picks_are_always_wrong():
    w = week([game(0), game(1)], [[None, None], [H, None]])
    d = draw(w, 2000, "blank")
    for outcome, best in zip(d.outcomes, d.best, strict=True):
        expected = 1 if outcome & 1 else 0  # only entrant 2's home pick on g0 can score
        assert best == expected
    assert all(scores[0] == 0 for scores in d.others_sorted)


def test_a_tie_for_first_is_half_a_win():
    # One other entrant always picks exactly what we pick: always tied.
    w = week([game(0)], [[H]])
    d = draw(w, 2000, "tie")
    assert win_chance((H,), d) == pytest.approx(0.5)


def test_identical_sides_give_identical_results():
    w = week([game(0), game(1, tier=Tier.LEAN)], [[H, A], [A, A], [H, H]])
    d = draw(w, 2000, "same")
    a = evaluate(Rule.CURRENT, (H, A), d)
    b = evaluate(Rule.MINORITY, (H, A), d)
    assert (a.avg_points, a.win_chance, a.top3_chance, a.median_rank) == (
        b.avg_points, b.win_chance, b.top3_chance, b.median_rank,
    )


def test_going_against_a_unanimous_pool_raises_win_chance_at_the_same_average():
    # Four coinflips, five other entrants all on the (home) favorite everywhere.
    w = week([game(i) for i in range(4)], [[H] * 4] * 5)
    d = draw(w, 20_000, "contrarian")
    current = evaluate(Rule.CURRENT, rule_sides(w, Rule.CURRENT), d)
    underdog = evaluate(Rule.UNDERDOG, rule_sides(w, Rule.UNDERDOG), d)
    assert current.win_chance == pytest.approx(1 / 6)  # always a six-way tie
    assert underdog.win_chance == pytest.approx(0.375, abs=0.015)  # 5/16 + 6/16 * 1/6
    assert underdog.avg_points == pytest.approx(current.avg_points, abs=0.05)


@pytest.mark.parametrize(
    ("tier", "model_side", "rate"), [(Tier.STRONG, H, 0.637), (Tier.LEAN, A, 0.542)]
)
def test_model_side_wins_at_its_tier_rate(tier, model_side, rate):
    w = week([game(0, tier=tier, model_side=model_side)], [[H]])
    d = draw(w, 20_000, "rates")
    home_rate = sum(o & 1 for o in d.outcomes) / len(d.outcomes)
    model_rate = home_rate if model_side is H else 1 - home_rate
    assert model_rate == pytest.approx(rate, abs=0.015)


def test_rank_counts_entrants_strictly_ahead():
    # We pick away, the other two pick home: rank 1 when away covers, 3 when home does.
    w = week([game(0)], [[H], [H]])
    d = draw(w, 2000, "rank")
    result = evaluate(Rule.UNDERDOG, (A,), d)
    away_rate = 1 - sum(o & 1 for o in d.outcomes) / len(d.outcomes)
    assert result.top3_chance == 1.0
    assert result.win_chance == pytest.approx(away_rate)
    assert result.median_rank in (1, 2, 3)


def test_same_seed_same_draws():
    w = week([game(i) for i in range(3)], [[H, A, H], [A, A, H]])
    assert draw(w, 500, "s") == draw(w, 500, "s")
    assert draw(w, 500, "s").outcomes != draw(w, 500, "t").outcomes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool_sim.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pickem.backtest.pool_sim'`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/backtest/pool_sim.py`:

```python
"""Weekly-win simulation: how often would each COINFLIP rule finish first?

The pool pays the week's winner, so a COINFLIP side is worth choosing for how
it separates us from the other entrants, not only for whether it covers. This
replays an imported pool week many times: each draw decides every game once,
shared by all entrants; the other entrants keep their real picks; our entry
takes the model's side on STRONG, LEAN and NO_MARKET games and lets the rule
under test choose each COINFLIP side. Every rule is scored on the same draws.

Probabilities are fixed in advance (see the spec,
`docs/superpowers/specs/2026-10-05-weekly-win-simulator-design.md`) and never
fitted to 2026 outcomes.

Pure functions only. No network, no database, no filesystem.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from pickem.edge.favorite import favorite_side
from pickem.models import Side, Tier

# NFL 2020-2025 backtest (docs/results.md). Anything else is an even game.
TIER_PROBABILITY: dict[Tier, float] = {Tier.STRONG: 0.637, Tier.LEAN: 0.542}
LOPSIDED_SHARE = 0.70
SEASON_WEEKS = 18
DEFAULT_DRAWS = 20_000
DEFAULT_SEED = 20261005


class Rule(StrEnum):
    CURRENT = "current"
    UNDERDOG = "underdog"
    MINORITY = "minority"
    LOPSIDED = "lopsided underdog"
    OPTIMAL = "optimal mix"


# These read pool picks that stay hidden until each kickoff: upper bounds only.
CEILINGS = frozenset({Rule.MINORITY, Rule.LOPSIDED, Rule.OPTIMAL})


def opposite(side: Side) -> Side:
    return Side.AWAY if side is Side.HOME else Side.HOME


@dataclass(frozen=True)
class SimGame:
    game_id: str
    label: str
    league_spread: float
    tier: Tier | None  # None: no recommendation was stored before kickoff
    model_side: Side  # the model's side, or our submitted pick when tier is None

    @property
    def favorite(self) -> Side:
        return favorite_side(self.league_spread)

    @property
    def is_coinflip(self) -> bool:
        return self.tier is Tier.COINFLIP

    @property
    def p_home(self) -> float:
        """Chance the home side covers the CBS line."""
        p = TIER_PROBABILITY.get(self.tier, 0.5) if self.tier else 0.5
        return p if self.model_side is Side.HOME else 1 - p


@dataclass(frozen=True)
class SimWeek:
    pool_week: int
    games: tuple[SimGame, ...]
    # One row per other entrant, aligned with `games`; None is a blank pick.
    others: tuple[tuple[Side | None, ...], ...]
    unrecommended: int = 0

    @property
    def entrants(self) -> int:
        return len(self.others) + 1

    def field_split(self, i: int) -> tuple[int, int]:
        """(home, away) counts of the other entrants' picks on game ``i``."""
        column = [row[i] for row in self.others]
        return column.count(Side.HOME), column.count(Side.AWAY)

    def favorite_share(self, i: int) -> float | None:
        home, away = self.field_split(i)
        if home + away == 0:
            return None
        return (home if self.games[i].favorite is Side.HOME else away) / (home + away)


def rule_sides(week: SimWeek, rule: Rule) -> tuple[Side, ...]:
    if rule is Rule.OPTIMAL:
        raise ValueError("the optimal mix comes from search_optimal, not a fixed rule")
    return tuple(
        _coinflip_side(week, i, rule) if game.is_coinflip else game.model_side
        for i, game in enumerate(week.games)
    )


def _coinflip_side(week: SimWeek, i: int, rule: Rule) -> Side:
    favorite = week.games[i].favorite
    underdog = opposite(favorite)
    if rule is Rule.CURRENT:
        return favorite
    if rule is Rule.UNDERDOG:
        return underdog
    home, away = week.field_split(i)
    on_favorite, on_underdog = (home, away) if favorite is Side.HOME else (away, home)
    if rule is Rule.MINORITY:
        return underdog if on_underdog < on_favorite else favorite
    share = week.favorite_share(i)
    return underdog if share is not None and share >= LOPSIDED_SHARE else favorite


def _bits(sides: Sequence[Side | None]) -> tuple[int, int]:
    """(home picks, all picks) as bitmasks over the week's games."""
    home = picked = 0
    for i, side in enumerate(sides):
        if side is None:
            continue
        picked |= 1 << i
        if side is Side.HOME:
            home |= 1 << i
    return home, picked


def _score(outcome: int, home: int, picked: int, full: int) -> int:
    # A pick is right when its home bit equals the outcome bit; blanks never score.
    return ((outcome ^ home ^ full) & picked).bit_count()


@dataclass(frozen=True)
class Draws:
    """Simulated weeks: every game's winner, and how the other entrants scored."""

    full: int
    outcomes: tuple[int, ...]  # bit i set: the home side covered game i
    best: tuple[int, ...]  # best other score in each draw
    best_count: tuple[int, ...]  # how many other entrants hold it
    others_sorted: tuple[tuple[int, ...], ...]  # every other score, ascending


def draw(week: SimWeek, draws: int, seed: str) -> Draws:
    rng = random.Random(seed)
    full = (1 << len(week.games)) - 1
    p_home = [game.p_home for game in week.games]
    masks = [_bits(row) for row in week.others]
    outcomes: list[int] = []
    best: list[int] = []
    best_count: list[int] = []
    others_sorted: list[tuple[int, ...]] = []
    for _ in range(draws):
        outcome = 0
        for i, p in enumerate(p_home):
            if rng.random() < p:
                outcome |= 1 << i
        scores = tuple(sorted(_score(outcome, home, picked, full) for home, picked in masks))
        top = scores[-1] if scores else -1
        outcomes.append(outcome)
        best.append(top)
        best_count.append(scores.count(top))
        others_sorted.append(scores)
    return Draws(full, tuple(outcomes), tuple(best), tuple(best_count), tuple(others_sorted))


def _win_share(score: int, best: int, count: int) -> float:
    if score > best:
        return 1.0
    if score == best:
        return 1.0 / (count + 1)
    return 0.0


@dataclass(frozen=True)
class RuleResult:
    rule: Rule
    sides: tuple[Side, ...]
    avg_points: float
    win_chance: float
    top3_chance: float
    median_rank: float


def evaluate(rule: Rule, sides: Sequence[Side], draws: Draws) -> RuleResult:
    home, picked = _bits(sides)
    points = wins = top3 = 0.0
    ranks: list[int] = []
    for outcome, best, count, others in zip(
        draws.outcomes, draws.best, draws.best_count, draws.others_sorted, strict=True
    ):
        score = _score(outcome, home, picked, draws.full)
        rank = 1 + len(others) - bisect_right(others, score)
        points += score
        wins += _win_share(score, best, count)
        top3 += rank <= 3
        ranks.append(rank)
    n = len(draws.outcomes)
    return RuleResult(rule, tuple(sides), points / n, wins / n, top3 / n, median(ranks))


def win_chance(sides: Sequence[Side], draws: Draws) -> float:
    home, picked = _bits(sides)
    total = sum(
        _win_share(_score(outcome, home, picked, draws.full), best, count)
        for outcome, best, count in zip(draws.outcomes, draws.best, draws.best_count, strict=True)
    )
    return total / len(draws.outcomes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pool_sim.py -q && uv run ruff check src/pickem/backtest/pool_sim.py tests/test_pool_sim.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/pool_sim.py tests/test_pool_sim.py
git commit -m "feat: simulate a pool week under fixed coin-flip rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 2: Optimal-mix search and the per-week simulation

**Files:**
- Modify: `src/pickem/backtest/pool_sim.py` (append)
- Test: `tests/test_pool_sim.py` (append)

**Interfaces:**
- Consumes (Task 1): `SimWeek`, `SimGame`, `Rule`, `rule_sides`, `draw`, `evaluate`, `win_chance`, `opposite`, `RuleResult`, `DEFAULT_DRAWS`, `DEFAULT_SEED`.
- Produces:
  - `search_optimal(week: SimWeek, draws: Draws) -> tuple[Side, ...]`
  - `@dataclass(frozen=True) SwitchedGame(label: str, league_spread: float, favorite_share: float | None)`
  - `@dataclass(frozen=True) WeekResult(pool_week: int, entrants: int, draws: int, results: tuple[RuleResult, ...], switched: tuple[SwitchedGame, ...], unrecommended: int)` with `result(rule: Rule) -> RuleResult`
  - `simulate_week(week: SimWeek, *, draws: int = DEFAULT_DRAWS, seed: int = DEFAULT_SEED) -> WeekResult`. Evaluation draws use seed string `f"{seed}:{week.pool_week}:evaluate"`, search draws `f"{seed}:{week.pool_week}:search"`. `results` are in `Rule` declaration order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pool_sim.py` (and add `from itertools import product` plus `search_optimal`, `simulate_week` to the imports at the top):

```python
def three_coinflip_week():
    return week(
        [game(0), game(1, spread=2.5), game(2), game(3, tier=Tier.STRONG)],
        [[H, A, H, H], [H, A, A, H], [A, A, H, H], [H, H, H, A], [H, A, H, H]],
    )


def test_search_never_ends_below_the_current_rule():
    w = three_coinflip_week()
    d = draw(w, 4000, "search")
    assert win_chance(search_optimal(w, d), d) >= win_chance(rule_sides(w, Rule.CURRENT), d)


def test_search_ends_where_no_single_switch_helps():
    w = three_coinflip_week()
    d = draw(w, 4000, "search")
    found = search_optimal(w, d)
    base = win_chance(found, d)
    for i, g in enumerate(w.games):
        if g.is_coinflip:
            flipped = list(found)
            flipped[i] = A if flipped[i] is H else H
            assert win_chance(flipped, d) <= base + 1e-12


def test_search_finds_the_brute_force_best_on_three_coinflips():
    w = three_coinflip_week()
    d = draw(w, 4000, "search")
    best = max(
        win_chance((a, b, c, H), d) for a, b, c in product((H, A), repeat=3)
    )
    assert win_chance(search_optimal(w, d), d) == pytest.approx(best)


def test_search_leaves_non_coinflip_games_alone():
    w = three_coinflip_week()
    assert search_optimal(w, draw(w, 2000, "x"))[3] is H


def test_week_without_coinflips_keeps_the_current_rule():
    w = week([game(0, tier=Tier.LEAN), game(1, tier=Tier.STRONG, model_side=A)], [[H, H]] * 3)
    result = simulate_week(w, draws=2000, seed=1)
    assert result.result(Rule.OPTIMAL).sides == result.result(Rule.CURRENT).sides
    assert result.switched == ()


def test_simulate_week_reports_every_rule_in_order():
    w = three_coinflip_week()
    result = simulate_week(w, draws=2000, seed=1)
    assert [r.rule for r in result.results] == list(Rule)
    assert result.entrants == 6 and result.draws == 2000 and result.pool_week == 1


def test_switched_lists_coinflips_the_optimal_mix_took_against_the_favorite():
    w = three_coinflip_week()
    result = simulate_week(w, draws=2000, seed=1)
    optimal = result.result(Rule.OPTIMAL).sides
    expected = [
        g.label for g, side in zip(w.games, optimal, strict=True)
        if g.is_coinflip and side is not g.favorite
    ]
    assert [s.label for s in result.switched] == expected


def test_simulate_week_is_repeatable():
    w = three_coinflip_week()
    assert simulate_week(w, draws=1000, seed=7) == simulate_week(w, draws=1000, seed=7)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool_sim.py -q`
Expected: FAIL — `ImportError: cannot import name 'search_optimal'`

- [ ] **Step 3: Write the implementation**

Append to `src/pickem/backtest/pool_sim.py`:

```python
def search_optimal(week: SimWeek, draws: Draws) -> tuple[Side, ...]:
    """Coordinate ascent on win chance over the COINFLIP sides.

    Starts from the current rule and keeps any single switch that raises the
    win chance on ``draws``; stops when none does. Callers must measure the
    result on different draws, or it partly reflects its own luck.
    """
    sides = list(rule_sides(week, Rule.CURRENT))
    best = win_chance(sides, draws)
    improved = True
    while improved:
        improved = False
        for i, game in enumerate(week.games):
            if not game.is_coinflip:
                continue
            sides[i] = opposite(sides[i])
            trial = win_chance(sides, draws)
            if trial > best + 1e-12:
                best = trial
                improved = True
            else:
                sides[i] = opposite(sides[i])
    return tuple(sides)


@dataclass(frozen=True)
class SwitchedGame:
    label: str
    league_spread: float
    favorite_share: float | None


@dataclass(frozen=True)
class WeekResult:
    pool_week: int
    entrants: int
    draws: int
    results: tuple[RuleResult, ...]
    switched: tuple[SwitchedGame, ...]
    unrecommended: int

    def result(self, rule: Rule) -> RuleResult:
        return next(r for r in self.results if r.rule is rule)


def simulate_week(
    week: SimWeek, *, draws: int = DEFAULT_DRAWS, seed: int = DEFAULT_SEED
) -> WeekResult:
    evaluation = draw(week, draws, f"{seed}:{week.pool_week}:evaluate")
    search = draw(week, draws, f"{seed}:{week.pool_week}:search")
    optimal = search_optimal(week, search)
    results = tuple(
        evaluate(rule, optimal if rule is Rule.OPTIMAL else rule_sides(week, rule), evaluation)
        for rule in Rule
    )
    switched = tuple(
        SwitchedGame(game.label, game.league_spread, week.favorite_share(i))
        for i, (game, side) in enumerate(zip(week.games, optimal, strict=True))
        if game.is_coinflip and side is not game.favorite
    )
    return WeekResult(
        week.pool_week, week.entrants, draws, results, switched, week.unrecommended
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pool_sim.py -q && uv run ruff check src/pickem/backtest/pool_sim.py tests/test_pool_sim.py`
Expected: all PASS, ruff clean.

If `test_search_finds_the_brute_force_best_on_three_coinflips` fails, the search stopped at a local optimum. Do not change the test or the week. Stop and report it, because it changes what the spec promises.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/pool_sim.py tests/test_pool_sim.py
git commit -m "feat: search for the coin-flip mix most likely to win a week

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 3: Season summary and text report

**Files:**
- Modify: `src/pickem/backtest/pool_sim.py` (append)
- Test: `tests/test_pool_sim.py` (append)

**Interfaces:**
- Consumes (Tasks 1–2): `WeekResult`, `RuleResult`, `Rule`, `CEILINGS`, `SEASON_WEEKS`.
- Produces:
  - `at_least_one(weekly: float, weeks: int = SEASON_WEEKS) -> float`
  - `@dataclass(frozen=True) SeasonRow(label: str, weekly_win: float, season_win: float)`
  - `season_summary(weeks: Sequence[WeekResult]) -> tuple[SeasonRow, ...]` — one row per `Rule` (label = rule value, `*` appended for ceilings), then a final `"no-edge entrant"` row at the mean of `1 / entrants`.
  - `format_report(weeks: Sequence[WeekResult]) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pool_sim.py` (add `at_least_one`, `format_report`, `season_summary` to the imports):

```python
def test_at_least_one_win_in_eighteen_weeks():
    assert at_least_one(0.02) == pytest.approx(1 - 0.98**18)
    assert at_least_one(0.0) == 0.0


def test_season_summary_averages_weekly_win_chance():
    w1 = simulate_week(three_coinflip_week(), draws=1000, seed=1)
    w2 = simulate_week(week([game(0)], [[H]] * 3, pool_week=2), draws=1000, seed=1)
    rows = {row.label: row for row in season_summary([w1, w2])}
    expected = (w1.result(Rule.CURRENT).win_chance + w2.result(Rule.CURRENT).win_chance) / 2
    assert rows["current"].weekly_win == pytest.approx(expected)
    assert rows["current"].season_win == pytest.approx(at_least_one(expected))
    assert rows["minority*"].weekly_win == pytest.approx(
        (w1.result(Rule.MINORITY).win_chance + w2.result(Rule.MINORITY).win_chance) / 2
    )
    assert rows["no-edge entrant"].weekly_win == pytest.approx((1 / 6 + 1 / 4) / 2)


def test_report_labels_ceilings_and_counts_unrecommended_games():
    w = week(
        [game(0), game(1, tier=None, model_side=A)], [[H, H]] * 4, pool_week=3, unrecommended=1
    )
    text = format_report([simulate_week(w, draws=1000, seed=1)])
    assert "Pool week 3: 5 entrants, 1,000 simulated weeks" in text
    for rule in Rule:
        assert str(rule) in text
    assert "minority*" in text and "current*" not in text
    assert "* ceiling" in text
    assert "no stored recommendation (treated as 50/50): 1" in text
    assert "no-edge entrant" in text
    assert "Season, pool week 3" in text


def test_report_names_the_switched_games():
    # Every other entrant on the favorite everywhere: the search takes underdogs.
    w = week([game(i) for i in range(4)], [[H] * 4] * 5)
    text = format_report([simulate_week(w, draws=2000, seed=1)])
    assert "optimal mix took the underdog on:" in text
    assert "A0 at H0 (-3.5, pool 100% on the favorite)" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool_sim.py -q`
Expected: FAIL — `ImportError: cannot import name 'at_least_one'`

- [ ] **Step 3: Write the implementation**

Append to `src/pickem/backtest/pool_sim.py`:

```python
def at_least_one(weekly: float, weeks: int = SEASON_WEEKS) -> float:
    """Chance of at least one weekly win over ``weeks`` independent weeks."""
    return 1 - (1 - weekly) ** weeks


@dataclass(frozen=True)
class SeasonRow:
    label: str
    weekly_win: float
    season_win: float


def _label(rule: Rule) -> str:
    return f"{rule}*" if rule in CEILINGS else str(rule)


def season_summary(weeks: Sequence[WeekResult]) -> tuple[SeasonRow, ...]:
    rows = []
    for rule in Rule:
        weekly = sum(w.result(rule).win_chance for w in weeks) / len(weeks)
        rows.append(SeasonRow(_label(rule), weekly, at_least_one(weekly)))
    no_edge = sum(1 / w.entrants for w in weeks) / len(weeks)
    rows.append(SeasonRow("no-edge entrant", no_edge, at_least_one(no_edge)))
    return tuple(rows)


def _share(share: float | None) -> str:
    return "no pool picks" if share is None else f"pool {share:.0%} on the favorite"


def _span(weeks: Sequence[WeekResult]) -> str:
    first, last = weeks[0].pool_week, weeks[-1].pool_week
    return f"pool week {first}" if first == last else f"pool weeks {first}-{last}"


def format_report(weeks: Sequence[WeekResult]) -> str:
    lines: list[str] = []
    for w in weeks:
        lines.append(f"Pool week {w.pool_week}: {w.entrants} entrants, {w.draws:,} simulated weeks")
        lines.append(f"  {'rule':<20}{'avg pts':>8}{'win':>8}{'top 3':>8}{'median rank':>13}")
        for r in w.results:
            lines.append(
                f"  {_label(r.rule):<20}{r.avg_points:>8.2f}{r.win_chance:>8.1%}"
                f"{r.top3_chance:>8.1%}{r.median_rank:>13g}"
            )
        if w.switched:
            games = "; ".join(
                f"{g.label} ({g.league_spread:+.1f}, {_share(g.favorite_share)})"
                for g in w.switched
            )
            lines.append(f"  optimal mix took the underdog on: {games}")
        else:
            lines.append("  optimal mix kept every favorite")
        if w.unrecommended:
            lines.append(
                f"  games with no stored recommendation (treated as 50/50): {w.unrecommended}"
            )
        lines.append("")
    lines.append(f"Season, {_span(weeks)}")
    lines.append(f"  {'rule':<20}{'avg weekly win':>16}{f'>=1 win in {SEASON_WEEKS} weeks':>22}")
    for row in season_summary(weeks):
        lines.append(f"  {row.label:<20}{row.weekly_win:>16.1%}{row.season_win:>22.1%}")
    lines.append("")
    lines.append("* ceiling: uses pool picks hidden until kickoff; cannot be played live.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pool_sim.py -q && uv run ruff check src/pickem/backtest/pool_sim.py tests/test_pool_sim.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/backtest/pool_sim.py tests/test_pool_sim.py
git commit -m "feat: summarize simulated weekly-win chances over a season

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 4: Load a pool week from the store

**Files:**
- Create: `src/pickem/operations/pool_sim_inputs.py`
- Test: `tests/test_pool_sim_inputs.py`

**Interfaces:**
- Consumes: `SimGame`, `SimWeek` (Task 1); `Store.pool_results(season, pool_week) -> list[PoolResult]`, `Store.pool_picks(season, pool_week) -> list[PoolPick]`, `Store.games_by_ids(ids) -> list[Game]`, `Store.league_lines_by_ids(ids) -> list[LeagueLine]`, `Store.recommendation_history(ids) -> list[RecommendationRecord]`; `pickem.report.results.last_before(history, kickoff) -> RecommendationRecord | None` (latest strictly before kickoff); `favorite_side`.
- Produces:
  - `class SimInputError(RuntimeError)`
  - `load_sim_week(store: Store, *, season: int, pool_week: int, entry_name: str) -> SimWeek`. Games sorted by `game_id`; `others` sorted by `entry_id`, excluding our entry; label `"{away_team_id} at {home_team_id}"`.

Test fixture facts (`tests/results_helpers.py`, `imported_store()`): pool week 2 of 2026, 4 entrants (ours is `Jota`, id `JOTA_ID`), 4 games, all kicking off at `KICK`. Game ids: `cfb-2026-02-OU-at-MICH` (+5.5), `cfb-2026-02-PSU-at-TEM` (+24.5), `nfl-2026-01-NE-at-SEA` (-3.5), `nfl-2026-01-NO-at-DET` (-7.5). Jota picked MICH, PSU, SEA, DET (home, away, home, home). Three picks in the page are blank.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pool_sim_inputs.py`:

```python
from datetime import timedelta

import pytest
from results_helpers import JOTA_ID, KICK, imported_store

from pickem.models import RecommendationRecord, Side, Sport, Tier
from pickem.operations.pool_sim_inputs import SimInputError, load_sim_week

MICH = "cfb-2026-02-OU-at-MICH"
TEM = "cfb-2026-02-PSU-at-TEM"
SEA = "nfl-2026-01-NE-at-SEA"
DET = "nfl-2026-01-NO-at-DET"


def rec(game_id, side, tier, *, hours_before=2, sport=Sport.CFB, week=2):
    return RecommendationRecord(
        game_id=game_id, sport=sport, season=2026, week=week, side=side, tier=tier,
        edge_points=0.0, generated_at=KICK - timedelta(hours=hours_before), source="refresh",
    )


@pytest.fixture
def store():
    s = imported_store()
    s.append_recommendation_history([
        rec(MICH, Side.AWAY, Tier.LEAN, hours_before=30),
        rec(MICH, Side.HOME, Tier.COINFLIP, hours_before=2),  # the last one before kickoff wins
        rec(MICH, Side.AWAY, Tier.STRONG, hours_before=-1),  # after kickoff: ignored
        rec(TEM, Side.AWAY, Tier.STRONG),
        rec(SEA, Side.HOME, Tier.LEAN, sport=Sport.NFL, week=1),
        # DET has no recommendation.
    ])
    return s


def test_games_take_the_last_recommendation_before_kickoff(store):
    week = load_sim_week(store, season=2026, pool_week=2, entry_name="Jota")
    by_id = {g.game_id: g for g in week.games}
    assert [g.game_id for g in week.games] == sorted([MICH, TEM, SEA, DET])
    assert (by_id[MICH].tier, by_id[MICH].model_side) == (Tier.COINFLIP, Side.HOME)
    assert (by_id[TEM].tier, by_id[TEM].model_side) == (Tier.STRONG, Side.AWAY)
    assert by_id[MICH].league_spread == 5.5
    assert by_id[MICH].label == "OU at MICH"


def test_a_game_without_a_recommendation_uses_our_pick_and_is_counted(store):
    week = load_sim_week(store, season=2026, pool_week=2, entry_name="Jota")
    det = next(g for g in week.games if g.game_id == DET)
    assert det.tier is None
    assert det.model_side is Side.HOME  # Jota picked DET
    assert week.unrecommended == 1


def test_others_exclude_our_entry_and_keep_blanks(store):
    week = load_sim_week(store, season=2026, pool_week=2, entry_name="Jota")
    assert week.entrants == 4
    assert len(week.others) == 3
    assert all(len(row) == 4 for row in week.others)
    picks = store.pool_picks(2026, 2)
    blanks = sum(1 for p in picks if p.side is None and p.entry_id != JOTA_ID)
    assert sum(row.count(None) for row in week.others) == blanks


def test_unknown_entry_name_names_the_entrants(store):
    with pytest.raises(SimInputError, match="no entrant named 'Nobody'.*Jota"):
        load_sim_week(store, season=2026, pool_week=2, entry_name="Nobody")


def test_week_not_imported(store):
    with pytest.raises(SimInputError, match="pool week 9 of 2026 is not imported"):
        load_sim_week(store, season=2026, pool_week=9, entry_name="Jota")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool_sim_inputs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pickem.operations.pool_sim_inputs'`

- [ ] **Step 3: Write the implementation**

Create `src/pickem/operations/pool_sim_inputs.py`:

```python
"""Build a weekly-win simulation input from an imported pool week."""

from __future__ import annotations

from collections import defaultdict

from pickem.backtest.pool_sim import SimGame, SimWeek
from pickem.edge.favorite import favorite_side
from pickem.models import RecommendationRecord
from pickem.report.results import last_before
from pickem.store.db import Store


class SimInputError(RuntimeError):
    """The requested pool week cannot be simulated from what is stored."""


def load_sim_week(store: Store, *, season: int, pool_week: int, entry_name: str) -> SimWeek:
    results = store.pool_results(season, pool_week)
    if not results:
        raise SimInputError(
            f"pool week {pool_week} of {season} is not imported; run import-results first"
        )
    ours = next((r for r in results if r.name.strip() == entry_name), None)
    if ours is None:
        names = ", ".join(sorted(r.name for r in results))
        raise SimInputError(
            f"no entrant named {entry_name!r} in pool week {pool_week}; names found: {names}"
        )

    picks = store.pool_picks(season, pool_week)
    game_ids = sorted({pick.game_id for pick in picks})
    games = {game.game_id: game for game in store.games_by_ids(game_ids)}
    spreads = {ln.game_id: ln.spread_home for ln in store.league_lines_by_ids(game_ids)}
    history: dict[str, list[RecommendationRecord]] = defaultdict(list)
    for record in store.recommendation_history(game_ids):
        history[record.game_id].append(record)
    side_of = {(pick.entry_id, pick.game_id): pick.side for pick in picks}

    sim_games: list[SimGame] = []
    unrecommended = 0
    for game_id in game_ids:
        game = games[game_id]
        spread = spreads[game_id]
        model = last_before(history[game_id], game.kickoff_utc)
        if model is None:
            unrecommended += 1
            tier = None
            side = side_of.get((ours.entry_id, game_id)) or favorite_side(spread)
        else:
            tier, side = model.tier, model.side
        sim_games.append(
            SimGame(
                game_id=game_id,
                label=f"{game.away_team_id} at {game.home_team_id}",
                league_spread=spread,
                tier=tier,
                model_side=side,
            )
        )

    other_ids = sorted({r.entry_id for r in results} - {ours.entry_id})
    others = tuple(
        tuple(side_of.get((entry_id, game_id)) for game_id in game_ids) for entry_id in other_ids
    )
    return SimWeek(pool_week, tuple(sim_games), others, unrecommended)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pool_sim_inputs.py -q && uv run ruff check src/pickem/operations/pool_sim_inputs.py tests/test_pool_sim_inputs.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/operations/pool_sim_inputs.py tests/test_pool_sim_inputs.py
git commit -m "feat: load an imported pool week for the weekly-win simulation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 5: The `simulate-weekly-win` command

**Files:**
- Modify: `src/pickem/cli.py` (new command after `results-report`, plus imports and a `_parse_pool_weeks` helper)
- Modify: `README.md` (one row in the "Historical / analysis" table)
- Test: `tests/test_pool_sim_cli.py`

**Interfaces:**
- Consumes: `load_sim_week`, `SimInputError` (Task 4); `simulate_week`, `format_report`, `DEFAULT_DRAWS`, `DEFAULT_SEED` (Tasks 1–3); `Store(path, read_only=True)` as a context manager; `store.pool_weeks(season) -> list[int]`; `run_context`, `config.DEFAULT_ENTRY_NAME`, `config.DEFAULT_DB` (already imported in `cli.py`).
- Produces: `uv run pickem simulate-weekly-win --season INT [--pool-weeks "N" | "A-B"] [--draws INT] [--seed INT] [--entry-name STR] [--db PATH]`; exit 1 with a red message on any input error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pool_sim_cli.py`:

```python
import hashlib
from datetime import timedelta

import pytest
from results_helpers import IMPORTED_AT, KICK, parsed_fixture, seed
from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import RecommendationRecord, Side, Sport, Tier
from pickem.operations.results_import import import_results
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store

runner = CliRunner()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pickem.duckdb"
    with Store(path) as store:
        store.init_schema()
        seed(store)
        import_results(
            store, parsed_fixture(), season=2026, pool_week=2,
            resolver=TeamResolver.default(), imported_at=IMPORTED_AT,
        )
        store.append_recommendation_history([
            RecommendationRecord(
                game_id="cfb-2026-02-OU-at-MICH", sport=Sport.CFB, season=2026, week=2,
                side=Side.HOME, tier=Tier.COINFLIP, edge_points=0.0,
                generated_at=KICK - timedelta(hours=2), source="refresh",
            )
        ])
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(db, *extra):
    return runner.invoke(
        app, ["simulate-weekly-win", "--season", "2026", "--draws", "1000", "--db", str(db), *extra]
    )


def test_prints_each_week_and_the_season_and_writes_nothing(db):
    before = digest(db)
    result = invoke(db)
    assert result.exit_code == 0, result.output
    assert "Pool week 2: 4 entrants, 1,000 simulated weeks" in result.output
    for rule in ("current", "underdog", "minority*", "lopsided underdog*", "optimal mix*"):
        assert rule in result.output
    assert "no stored recommendation (treated as 50/50): 3" in result.output
    assert "Season, pool week 2" in result.output
    assert digest(db) == before


def test_same_seed_same_output(db):
    assert invoke(db, "--seed", "5").output == invoke(db, "--seed", "5").output


def test_explicit_week_range(db):
    result = invoke(db, "--pool-weeks", "2-2")
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--pool-weeks", "3"], "pool week 3 of 2026 is not imported"),
        (["--pool-weeks", "4-2"], "--pool-weeks"),
        (["--pool-weeks", "two"], "--pool-weeks"),
        (["--entry-name", "Nobody"], "no entrant named 'Nobody'"),
    ],
)
def test_bad_input_exits_1_with_a_message(db, extra, message):
    result = invoke(db, *extra)
    assert result.exit_code == 1
    assert message in result.output


def test_missing_database(tmp_path):
    result = invoke(tmp_path / "absent.duckdb")
    assert result.exit_code == 1
    assert "no database" in result.output


def test_season_with_nothing_imported(db):
    result = runner.invoke(
        app, ["simulate-weekly-win", "--season", "2025", "--draws", "1000", "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "no pool weeks of 2025 are imported" in result.output
```

`result.output` includes stderr in this project's Typer version; `tests/test_results_cli.py` already asserts on red error messages that way.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool_sim_cli.py -q`
Expected: FAIL — exit code 2, "No such command 'simulate-weekly-win'".

- [ ] **Step 3: Write the implementation**

In `src/pickem/cli.py`, add to the imports (keep ruff's import order):

```python
from pickem.backtest.pool_sim import DEFAULT_DRAWS, DEFAULT_SEED, format_report, simulate_week
from pickem.operations.pool_sim_inputs import SimInputError, load_sim_week
```

Add after the `results-report` command:

```python
def _parse_pool_weeks(text: str | None) -> list[int] | None:
    """``"4"`` or ``"1-4"``; None means every imported week."""
    if text is None:
        return None
    first, _, last = text.partition("-")
    try:
        start, end = int(first), int(last or first)
    except ValueError as exc:
        raise ValueError(f"--pool-weeks must be N or A-B, got {text!r}") from exc
    if start < 1 or end < start:
        raise ValueError(f"--pool-weeks must be N or A-B with 1 <= A <= B, got {text!r}")
    return list(range(start, end + 1))


@app.command("simulate-weekly-win")
def simulate_weekly_win_cmd(
    season: int = typer.Option(...),
    pool_weeks: str = typer.Option(
        None, help="Pool weeks, e.g. 4 or 1-4. Default: every imported pool week"
    ),
    draws: int = typer.Option(DEFAULT_DRAWS, min=1000, help="Simulated weeks per pool week"),
    seed: int = typer.Option(DEFAULT_SEED),
    entry_name: str = typer.Option(config.DEFAULT_ENTRY_NAME),
    db: Path = typer.Option(config.DEFAULT_DB),
) -> None:
    """Estimate how often each COINFLIP rule would have won each pool week. Read-only."""
    with run_context("cli:simulate-weekly-win", season=season, db=str(db)):
        try:
            requested = _parse_pool_weeks(pool_weeks)
        except ValueError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(code=1) from exc
        if not db.exists():
            typer.secho(f"no database at {db}", fg="red", err=True)
            raise typer.Exit(code=1)
        with Store(db, read_only=True) as store:
            weeks = requested if requested is not None else store.pool_weeks(season)
            if not weeks:
                typer.secho(
                    f"no pool weeks of {season} are imported; run import-results first",
                    fg="red",
                    err=True,
                )
                raise typer.Exit(code=1)
            try:
                inputs = [
                    load_sim_week(store, season=season, pool_week=week, entry_name=entry_name)
                    for week in weeks
                ]
            except SimInputError as exc:
                typer.secho(str(exc), fg="red", err=True)
                raise typer.Exit(code=1) from exc
        results = [simulate_week(week, draws=draws, seed=seed) for week in inputs]
        typer.echo(format_report(results))
```

In `README.md`, add this row at the end of the "Historical / analysis" table (after `evaluate-coinflip-residual`):

```markdown
| `simulate-weekly-win` | Estimate how often each COINFLIP rule would have finished first in each imported pool week. Read-only |
```

The README says `uv run pickem --help` "should print 12 commands", which is already stale (there are 21). Leave that line alone; it is out of scope here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pool_sim_cli.py -q && uv run ruff check .`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pickem/cli.py tests/test_pool_sim_cli.py README.md
git commit -m "feat: add the simulate-weekly-win command

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 6: Run it on the real season and record the result

**Files:**
- Modify: `docs/results.md` (new section at the end)

- [ ] **Step 1: Run on the live database (read-only)**

```bash
PICKEM_LOG_DIR=/tmp/claude-1000/-home-jmiller-cfb/e5199453-4984-4aa3-88a3-429ec39d1c69/scratchpad/logs \
  uv run pickem simulate-weekly-win --season 2026 | tee /tmp/claude-1000/-home-jmiller-cfb/e5199453-4984-4aa3-88a3-429ec39d1c69/scratchpad/sim.txt
```

Expected: one table per imported pool week (1–4, or 1–5 if week 5 has been imported by then), the optimal-mix switches, and the season table. It should finish in under a minute. If it takes several minutes, report the timing instead of optimizing.

- [ ] **Step 2: Check the output makes sense**

- The `current` rule's `avg pts` should be near the week's real score, within a couple of points.
- `no stored recommendation` should be 0 for every week (the weekly reports say 0 games lacked a recommendation).
- Every rule's win chance should sit roughly between 0% and 15%. A value near 50% or above means something is wrong, so stop and report it.

- [ ] **Step 3: Write the result into `docs/results.md`**

Append a section `## Weekly-win simulation (2026-10-05)` containing:
- the exact command and seed used;
- the season table copied verbatim from the output;
- one short paragraph per question:
  1. Does a **playable** rule (underdog) beat `current` on weekly win chance, and by how much?
  2. How much of the **ceiling** (minority, lopsided, optimal mix) does the playable rule capture?
  3. What the optimal-mix switches have in common (spread size, pool share), from the per-week lines.
- The assumptions that limit it, copied from the spec's "Assumptions" section in one line each.
- A **Decision** line, left as `pending owner review`. The owner decides whether to change the live rule; this task does not change it.

- [ ] **Step 4: Commit**

```bash
git add docs/results.md
git commit -m "docs: record the weekly-win simulation result

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```
