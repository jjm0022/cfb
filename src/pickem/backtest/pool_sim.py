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
