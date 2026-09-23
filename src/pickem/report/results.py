"""Grade a pool week: us, the model, the field, and simple baselines.

Reads stored rows only — never CBS, never the Odds API. Every rate carries its
interval, because ~15 games per board cannot support a conclusion on its own.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pickem.backtest.stats import Result, grade_pick, wilson_interval
from pickem.edge.divergence import consensus_spread, suppress_decision_logging
from pickem.edge.favorite import favorite_side
from pickem.models import (
    LIVE_SOURCE,
    Game,
    MarketLine,
    PoolPick,
    PoolResult,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.store.db import Store

AGAINST_FIELD_SHARE = 0.40

# NFL 2020-2025 backtest (docs/results.md). Shown as NFL numbers even beside CFB.
BACKTEST_TIER_RATES = {Tier.STRONG: 0.637, Tier.LEAN: 0.542, Tier.COINFLIP: 0.495}


class ResultsReportError(RuntimeError):
    """The requested report cannot be built from what is stored."""


class Strategy(StrEnum):
    US = "us"
    MODEL = "model"
    FIRST_SHEET = "first sheet"
    CLOSE_DIVERGENCE = "close divergence"
    FAVORITES = "favorites"
    HOME = "home"
    FIELD = "field consensus"


BASELINES = (
    Strategy.MODEL,
    Strategy.FIRST_SHEET,
    Strategy.CLOSE_DIVERGENCE,
    Strategy.FAVORITES,
    Strategy.HOME,
    Strategy.FIELD,
)

# The strategies graded in the "this week" boards: Markdown, HTML and the
# Discord DM all show exactly this set, in this order.
WEEK_STRATEGIES = (Strategy.US, Strategy.MODEL, Strategy.FIELD, Strategy.CLOSE_DIVERGENCE)

# How each renderer marks a graded result; shared so Markdown, HTML and the
# Discord DM stay in lockstep.
RESULT_MARKS = {Result.WIN: "✓", Result.LOSS: "✗", Result.PUSH: "push"}

GLOSSARY_INTRO = (
    "Each line above grades one way of choosing a side. All of them are scored "
    "against the same CBS line, so their records compare directly. A strategy "
    "with no side on a game is not graded on it — that is why some carry a "
    "smaller n."
)

# Plain text: each renderer adds its own emphasis to the term.
STRATEGY_DEFINITIONS: dict[Strategy, str] = {
    Strategy.US: "the pick actually submitted on our CBS sheet. This is the only row "
    "that cost us anything; the rest are yardsticks.",
    Strategy.MODEL: "the last recommendation the model produced before kickoff. This is "
    "the advice we could still have acted on, so it is the fair measure of the model.",
    Strategy.FIRST_SHEET: "the model's earliest recommendation for that game, before any "
    "later revision. Compared against model, it shows whether reworking the sheet "
    "through the week actually helps.",
    Strategy.CLOSE_DIVERGENCE: "take whichever side the closing market rates higher than "
    "the CBS line did. CBS freezes its number early; when the market closes on a "
    "different one, this bets that the market's later number is the better one. It "
    "sits out any game where the close matches the board or no close was captured.",
    Strategy.FAVORITES: "always take the side the CBS line favors.",
    Strategy.HOME: "always take the home team.",
    Strategy.FIELD: "the side most other entrants in the pool picked. Beating "
    "it is what moves us up the standings; a tie is not graded.",
}


def close_divergence_side(league_spread: float, close_spread: float | None) -> Side | None:
    """The side the closing market favors against the frozen CBS line.

    A close more negative than the board makes home a bigger favorite than CBS
    did, so home is the side the market says is underpriced.
    """
    if close_spread is None or close_spread == league_spread:
        return None
    return Side.HOME if close_spread < league_spread else Side.AWAY


def field_consensus_side(home_picks: int, away_picks: int) -> Side | None:
    if home_picks == away_picks:
        return None
    return Side.HOME if home_picks > away_picks else Side.AWAY


def closing_line_value(
    side: Side | None, league_spread: float, close_spread: float | None
) -> float | None:
    """Points the close moved toward ``side``, measured from the CBS line.

    Taking home at -3.5 when the market closes at -5.0 is +1.5: the market came
    to us after CBS froze its number.
    """
    if side is None or close_spread is None:
        return None
    toward_home = league_spread - close_spread
    value = toward_home if side is Side.HOME else -toward_home
    return value + 0.0


def closing_spread(lines: Iterable[MarketLine], kickoff: datetime) -> float | None:
    """Live-market consensus from quotes captured before kickoff."""
    before = [ln for ln in lines if ln.source == LIVE_SOURCE and ln.captured_at < kickoff]
    with suppress_decision_logging():
        return consensus_spread(before)


def last_before(
    history: Iterable[RecommendationRecord], kickoff: datetime
) -> RecommendationRecord | None:
    """The recommendation the owner could still act on: latest strictly before kickoff."""
    eligible = [r for r in history if r.generated_at < kickoff]
    return max(eligible, key=lambda r: (r.generated_at, r.source), default=None)


def earliest(
    history: Iterable[RecommendationRecord], kickoff: datetime
) -> RecommendationRecord | None:
    eligible = [r for r in history if r.generated_at < kickoff]
    return min(eligible, key=lambda r: (r.generated_at, r.source), default=None)


@dataclass(frozen=True)
class GradedGame:
    game: Game
    pool_week: int
    league_spread: float
    close_spread: float | None
    field_home: int
    field_away: int
    model: RecommendationRecord | None
    picks: dict[Strategy, Side | None]

    def result(self, strategy: Strategy) -> Result | None:
        side = self.picks.get(strategy)
        if side is None:
            return None
        return grade_pick(side, self.game.home_score - self.game.away_score, self.league_spread)

    def team(self, side: Side | None) -> str:
        """The team id on ``side``, or an em dash when no side was picked."""
        if side is None:
            return "—"
        return self.game.home_team_id if side is Side.HOME else self.game.away_team_id

    @property
    def covered(self) -> str:
        """"push", or the id of the team that covered ``league_spread``."""
        home_result = self.result(Strategy.HOME)
        if home_result is Result.PUSH:
            return "push"
        return self.team(Side.HOME if home_result is Result.WIN else Side.AWAY)

    @property
    def clv(self) -> float | None:
        return closing_line_value(self.picks[Strategy.US], self.league_spread, self.close_spread)

    @property
    def our_field_share(self) -> float | None:
        side = self.picks[Strategy.US]
        total = self.field_home + self.field_away
        if side is None or total == 0:
            return None
        return (self.field_home if side is Side.HOME else self.field_away) / total

    @property
    def against_field(self) -> bool:
        share = self.our_field_share
        return share is not None and share <= AGAINST_FIELD_SHARE


def grade_game(
    *,
    game: Game,
    pool_week: int,
    league_spread: float,
    close_spread: float | None,
    our_side: Side | None,
    field_home: int,
    field_away: int,
    history: Sequence[RecommendationRecord],
) -> GradedGame:
    model = last_before(history, game.kickoff_utc)
    first = earliest(history, game.kickoff_utc)
    return GradedGame(
        game=game,
        pool_week=pool_week,
        league_spread=league_spread,
        close_spread=close_spread,
        field_home=field_home,
        field_away=field_away,
        model=model,
        picks={
            Strategy.US: our_side,
            Strategy.MODEL: model.side if model is not None else None,
            Strategy.FIRST_SHEET: first.side if first is not None else None,
            Strategy.CLOSE_DIVERGENCE: close_divergence_side(league_spread, close_spread),
            Strategy.FAVORITES: favorite_side(league_spread),
            Strategy.HOME: Side.HOME,
            Strategy.FIELD: field_consensus_side(field_home, field_away),
        },
    )


@dataclass(frozen=True)
class Record:
    wins: int = 0
    losses: int = 0
    pushes: int = 0

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def rate(self) -> float | None:
        return self.wins / self.decided if self.decided else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson_interval(self.wins, self.decided) if self.decided else None

    def __str__(self) -> str:
        head = f"{self.wins}–{self.losses} ({self.pushes})"
        if not self.decided:
            return f"{head} = n/a, n=0"
        low, high = self.interval
        return f"{head} = {self.rate:.1%} [{low:.0%}–{high:.0%}], n={self.decided}"


def record_for(
    games: Iterable[GradedGame],
    strategy: Strategy,
    *,
    sport: Sport | None = None,
    tier: Tier | None = None,
) -> Record:
    """W-L-P for one strategy; ``tier`` filters on the model's tier for the game."""
    counts = {Result.WIN: 0, Result.LOSS: 0, Result.PUSH: 0}
    for game in games:
        if sport is not None and game.game.sport is not sport:
            continue
        if tier is not None and (game.model is None or game.model.tier is not tier):
            continue
        result = game.result(strategy)
        if result is not None:
            counts[result] += 1
    return Record(counts[Result.WIN], counts[Result.LOSS], counts[Result.PUSH])


@dataclass(frozen=True)
class ClvSummary:
    n: int
    mean: float | None
    positive_share: float | None
    interval: tuple[float, float] | None


def clv_summary(games: Iterable[GradedGame], *, sport: Sport | None = None) -> ClvSummary:
    """Mean CLV of our picks, with a normal-approximation 95% interval (n >= 2)."""
    values = [
        game.clv
        for game in games
        if (sport is None or game.game.sport is sport) and game.clv is not None
    ]
    if not values:
        return ClvSummary(0, None, None, None)
    mean = statistics.fmean(values)
    positive = sum(1 for value in values if value > 0) / len(values)
    if len(values) < 2:
        return ClvSummary(len(values), mean, positive, None)
    half = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return ClvSummary(len(values), mean, positive, (mean - half, mean + half))


@dataclass(frozen=True)
class BoardStanding:
    sport: Sport
    our_points: int
    median_points: float
    best_points: int


@dataclass(frozen=True)
class WeekStanding:
    pool_week: int
    entrants: int
    our_rank: int
    our_points: int
    median_points: float
    winner_points: int
    beat_share: float
    boards: tuple[BoardStanding, ...]

    @property
    def gap_to_winner(self) -> int:
        return self.winner_points - self.our_points


@dataclass(frozen=True)
class ResultsReport:
    season: int
    pool_week: int
    entry_name: str
    standings: tuple[WeekStanding, ...]
    week_games: tuple[GradedGame, ...]
    season_games: tuple[GradedGame, ...]

    @property
    def current(self) -> WeekStanding:
        return self.standings[-1]

    @property
    def unknown_model_games(self) -> int:
        return sum(1 for game in self.season_games if game.model is None)


def build_results_report(
    store: Store, *, season: int, pool_week: int, entry_name: str
) -> ResultsReport:
    """Grade every imported pool week of ``season`` up to and including ``pool_week``."""
    weeks = [week for week in store.pool_weeks(season) if week <= pool_week]
    if pool_week not in weeks:
        raise ResultsReportError(
            f"pool week {pool_week} of {season} is not imported; run import-results first"
        )
    standings: list[WeekStanding] = []
    season_games: list[GradedGame] = []
    for week in weeks:
        standing, graded = _grade_week(store, season, week, entry_name)
        standings.append(standing)
        season_games.extend(graded)
    return ResultsReport(
        season=season,
        pool_week=pool_week,
        entry_name=entry_name,
        standings=tuple(standings),
        week_games=tuple(game for game in season_games if game.pool_week == pool_week),
        season_games=tuple(season_games),
    )


def _grade_week(
    store: Store, season: int, week: int, entry_name: str
) -> tuple[WeekStanding, list[GradedGame]]:
    results = store.pool_results(season, week)
    ours = next((r for r in results if r.name.strip() == entry_name), None)
    if ours is None:
        names = ", ".join(sorted(r.name for r in results))
        raise ResultsReportError(
            f"no entrant named {entry_name!r} in pool week {week}; names found: {names}"
        )

    picks = store.pool_picks(season, week)
    game_ids = sorted({pick.game_id for pick in picks})
    games = {game.game_id: game for game in store.games_by_ids(game_ids)}
    spreads = {ln.game_id: ln.spread_home for ln in store.league_lines_by_ids(game_ids)}
    lines: dict[str, list[MarketLine]] = defaultdict(list)
    for market_line in store.market_lines_by_ids(game_ids):
        lines[market_line.game_id].append(market_line)
    history: dict[str, list[RecommendationRecord]] = defaultdict(list)
    for record in store.recommendation_history(game_ids):
        history[record.game_id].append(record)
    by_game: dict[str, list[PoolPick]] = defaultdict(list)
    for pick in picks:
        by_game[pick.game_id].append(pick)

    graded: list[GradedGame] = []
    for game_id in game_ids:
        game = games[game_id]
        our_side = next((p.side for p in by_game[game_id] if p.entry_id == ours.entry_id), None)
        field = [
            p.side for p in by_game[game_id] if p.entry_id != ours.entry_id and p.side is not None
        ]
        graded.append(
            grade_game(
                game=game,
                pool_week=week,
                league_spread=spreads[game_id],
                close_spread=closing_spread(lines[game_id], game.kickoff_utc),
                our_side=our_side,
                field_home=field.count(Side.HOME),
                field_away=field.count(Side.AWAY),
                history=history[game_id],
            )
        )
    graded.sort(key=lambda g: (g.game.kickoff_utc, g.game.game_id))
    return _standing(week, results, ours, picks, games), graded


def _standing(
    week: int,
    results: Sequence[PoolResult],
    ours: PoolResult,
    picks: Sequence[PoolPick],
    games: dict[str, Game],
) -> WeekStanding:
    points = [r.points for r in results]
    boards: list[BoardStanding] = []
    for sport in sorted({game.sport for game in games.values()}):
        per_entrant = {r.entry_id: 0 for r in results}
        for pick in picks:
            if pick.cbs_correct and games[pick.game_id].sport is sport:
                per_entrant[pick.entry_id] += 1
        values = list(per_entrant.values())
        boards.append(
            BoardStanding(sport, per_entrant[ours.entry_id], statistics.median(values), max(values))
        )
    beaten = sum(1 for value in points if value < ours.points)
    return WeekStanding(
        pool_week=week,
        entrants=len(results),
        our_rank=ours.rank,
        our_points=ours.points,
        median_points=statistics.median(points),
        winner_points=max(points),
        beat_share=beaten / max(len(results) - 1, 1),
        boards=tuple(boards),
    )


@dataclass(frozen=True)
class Findings:
    claims: tuple[str, ...]
    not_yet: tuple[str, ...]


def _separate(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[1] < b[0] or b[1] < a[0]


def findings(games: Sequence[GradedGame]) -> Findings:
    """Statements the numbers support, and comparisons they cannot settle yet.

    A difference is claimed only when the two 95% intervals do not overlap.
    That test is deliberately conservative: comparisons on the same games are
    paired, and it errs toward saying nothing.
    """
    claims: list[str] = []
    not_yet: list[str] = []
    sports = sorted({game.game.sport for game in games})
    scopes: list[tuple[Sport | None, str]] = [(None, "all boards")]
    scopes += [(sport, sport.value.upper()) for sport in sports]

    for sport, label in scopes:
        ours = record_for(games, Strategy.US, sport=sport)
        for baseline in BASELINES:
            other = record_for(games, baseline, sport=sport)
            if not ours.decided or not other.decided:
                continue
            text = (
                f"{label}: us {ours.rate:.1%} (n={ours.decided}) vs {baseline.value} "
                f"{other.rate:.1%} (n={other.decided})"
            )
            if _separate(ours.interval, other.interval):
                claims.append(f"{text} — us {'ahead' if ours.rate > other.rate else 'behind'}")
            else:
                not_yet.append(f"{text} — not distinguishable yet")

        for tier, expected in BACKTEST_TIER_RATES.items():
            model = record_for(games, Strategy.MODEL, sport=sport, tier=tier)
            if not model.decided:
                continue
            low, high = model.interval
            text = (
                f"{label}: model {tier.value} {model.rate:.1%} (n={model.decided}) vs NFL "
                f"backtest {expected:.1%}"
            )
            if expected < low:
                claims.append(f"{text} — above expectation")
            elif expected > high:
                claims.append(f"{text} — below expectation")
            else:
                not_yet.append(f"{text} — not distinguishable yet")

        clv = clv_summary(games, sport=sport)
        if not clv.n:
            continue
        text = f"{label}: mean CLV {clv.mean:+.2f} pts (n={clv.n})"
        if clv.interval is not None and (clv.interval[0] > 0 or clv.interval[1] < 0):
            direction = "toward" if clv.mean > 0 else "against"
            claims.append(f"{text} — the market moved {direction} our picks")
        else:
            not_yet.append(f"{text} — not distinguishable from zero yet")
    return Findings(tuple(claims), tuple(not_yet))
