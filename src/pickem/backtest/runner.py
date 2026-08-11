"""Replay historical weeks through the live strategy code.

The backtest calls the same ``compute_edge`` the weekly report calls. If the
two ever diverge, the backtest stops being evidence about the thing being
shipped.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from pydantic import BaseModel

from pickem.backtest.stats import Result, grade_pick, wilson_interval
from pickem.edge.divergence import Thresholds, compute_edge
from pickem.models import Game, LeagueLine, MarketLine, Tier

ASSUMPTIONS = [
    "The frozen league line is proxied by the market OPENING line; the real CBS "
    "number was never recorded historically and tracks the opener closely but is "
    "not identical.",
    "The market at submission time is proxied by the CLOSING line.",
    "Pushes are excluded from the hit rate rather than counted as half-wins.",
]


class TierRecord(BaseModel):
    tier: Tier | None
    wins: int
    losses: int
    pushes: int
    hit_rate: float
    ci_low: float
    ci_high: float


class BacktestReport(BaseModel):
    overall: TierRecord
    by_tier: list[TierRecord]
    assumptions: list[str]
    skipped: list[str]


def _record(tier: Tier | None, results: Sequence[Result]) -> TierRecord:
    wins = sum(1 for result in results if result is Result.WIN)
    losses = sum(1 for result in results if result is Result.LOSS)
    pushes = sum(1 for result in results if result is Result.PUSH)
    decided = wins + losses
    low, high = wilson_interval(wins, decided)
    return TierRecord(
        tier=tier,
        wins=wins,
        losses=losses,
        pushes=pushes,
        hit_rate=(wins / decided) if decided else 0.0,
        ci_low=low,
        ci_high=high,
    )


def run_backtest(
    games: Sequence[Game],
    openers: Sequence[MarketLine],
    closers: Sequence[MarketLine],
    thresholds: Thresholds | None = None,
) -> BacktestReport:
    """Replay completed games using opening and closing line proxies.

    Spreads remain home-perspective throughout: an opening market line becomes
    the frozen league line, while closing snapshots form the live consensus.
    """
    openers_by_game: dict[str, MarketLine] = {line.game_id: line for line in openers}
    closers_by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in closers:
        closers_by_game[line.game_id].append(line)

    all_results: list[Result] = []
    by_tier: dict[Tier, list[Result]] = defaultdict(list)
    skipped: list[str] = []

    for game in sorted(games, key=lambda game: (game.season, game.week, game.game_id)):
        if game.home_score is None or game.away_score is None:
            skipped.append(f"{game.game_id}: unplayed game (missing final score)")
            continue
        opener = openers_by_game.get(game.game_id)
        if opener is None:
            skipped.append(f"{game.game_id}: missing opening line for frozen proxy")
            continue

        frozen = LeagueLine(
            game_id=game.game_id,
            season=game.season,
            week=game.week,
            spread_home=opener.spread_home,
            posted_at=opener.captured_at,
        )
        edge = compute_edge(frozen, closers_by_game.get(game.game_id, []), thresholds)
        if edge.tier is Tier.NO_MARKET:
            skipped.append(f"{game.game_id}: missing closing market line")
            continue

        result = grade_pick(
            edge.side, game.home_score - game.away_score, frozen.spread_home
        )
        all_results.append(result)
        by_tier[edge.tier].append(result)

    return BacktestReport(
        overall=_record(None, all_results),
        by_tier=[
            _record(tier, by_tier[tier])
            for tier in sorted(by_tier, key=lambda tier: tier.value)
        ],
        assumptions=list(ASSUMPTIONS),
        skipped=skipped,
    )
