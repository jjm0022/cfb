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
from pickem.edge.elo import EloConfig
from pickem.edge.pipeline import apply_tiebreaks
from pickem.models import Game, LeagueLine, MarketLine, Tier

ASSUMPTIONS = [
    "The frozen league line is proxied by the market OPENING line; the real CBS "
    "number was never recorded historically and tracks the opener closely but is "
    "not identical.",
    "The market at submission time is proxied by the CLOSING line.",
    "Pushes are excluded from the hit rate rather than counted as half-wins.",
    "Coinflip and no-market games are resolved by the same Elo tiebreak the "
    "weekly report applies, using only games completed before the replayed "
    "week so no future result informs its own pick.",
]

OPENER_BOOK = "open"
CLOSER_SOURCE = "nflverse"
CLOSER_BOOK = "close"


# Reported most-confident first, which is the order the tiers are read in.
_TIER_ORDER = [Tier.STRONG, Tier.LEAN, Tier.COINFLIP, Tier.NO_MARKET]


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


def split_proxies(
    lines: Sequence[MarketLine],
) -> tuple[list[MarketLine], list[MarketLine], list[str]]:
    """Sort stored lines into opening and closing proxies.

    Classification is explicit in both directions. A default bucket would let
    in-season `poll-odds` snapshots — which share the database with the
    backfill — be graded as closing lines and quietly contaminate the result.
    """
    openers: list[MarketLine] = []
    closers: list[MarketLine] = []
    skipped: list[str] = []
    for line in lines:
        if line.book == OPENER_BOOK:
            openers.append(line)
        elif line.source == CLOSER_SOURCE and line.book == CLOSER_BOOK:
            closers.append(line)
        else:
            skipped.append(
                f"{line.game_id}: {line.source}/{line.book} snapshot at "
                f"{line.captured_at.isoformat()} is neither an opening nor a "
                f"closing proxy — not graded"
            )
    return openers, closers, skipped


def run_backtest(
    games: Sequence[Game],
    openers: Sequence[MarketLine],
    closers: Sequence[MarketLine],
    thresholds: Thresholds | None = None,
    elo_config: EloConfig | None = None,
) -> BacktestReport:
    """Replay completed games using opening and closing line proxies.

    Spreads remain home-perspective throughout: an opening market line becomes
    the frozen league line, while closing snapshots form the live consensus.
    """
    games_by_id = {game.game_id: game for game in games}
    openers_by_game: dict[str, MarketLine] = {line.game_id: line for line in openers}
    closers_by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in closers:
        closers_by_game[line.game_id].append(line)

    all_results: list[Result] = []
    by_tier: dict[Tier, list[Result]] = defaultdict(list)
    skipped: list[str] = []

    ordered = sorted(games, key=lambda game: (game.season, game.week, game.game_id))
    by_week: dict[tuple[int, int], list[Game]] = defaultdict(list)
    for game in ordered:
        by_week[(game.season, game.week)].append(game)

    # Grows as weeks are replayed, so each week's tiebreak sees only games that
    # had already finished when the pick would have been made.
    history: list[Game] = []

    for key in sorted(by_week):
        week_games = by_week[key]
        frozen_by_game: dict[str, LeagueLine] = {}
        edges = []

        for game in week_games:
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
            frozen_by_game[game.game_id] = frozen
            edges.append(compute_edge(frozen, closers_by_game.get(game.game_id, []), thresholds))

        # The report resolves coinflip and no-market games with the rating, so
        # the backtest must too — otherwise it is not measuring what ships.
        for edge in apply_tiebreaks(edges, week_games, history, elo_config):
            game = games_by_id[edge.game_id]
            result = grade_pick(
                edge.side,
                game.home_score - game.away_score,
                frozen_by_game[edge.game_id].spread_home,
            )
            all_results.append(result)
            by_tier[edge.tier].append(result)

        history.extend(week_games)

    return BacktestReport(
        overall=_record(None, all_results),
        by_tier=[_record(tier, by_tier[tier]) for tier in _TIER_ORDER if tier in by_tier],
        assumptions=list(ASSUMPTIONS),
        skipped=skipped,
    )
