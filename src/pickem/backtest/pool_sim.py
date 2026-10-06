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
