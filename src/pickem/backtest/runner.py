"""Replay historical weeks through the live strategy code.

The backtest calls the same final-decision interface the weekly report calls. If the
two ever diverge, the backtest stops being evidence about the thing being
shipped.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from loguru import logger
from pydantic import BaseModel

from pickem.backtest.stats import Result, grade_pick, wilson_interval
from pickem.edge.divergence import Thresholds, consensus_spread, suppress_decision_logging
from pickem.edge.elo import EloConfig
from pickem.edge.pipeline import decide_edges
from pickem.models import (
    FROZEN_SOURCE,
    SUBMISSION_SOURCE,
    Game,
    LeagueLine,
    MarketLine,
    Tier,
)

ASSUMPTIONS = [
    "The frozen league line is proxied by an early-week market snapshot taken "
    "the Tuesday before kickoff; the real CBS number was never recorded "
    "historically and is also set early in the week, but is not identical.",
    "The market at submission time is proxied by the last archived snapshot "
    "before each game's own kickoff.",
    "Both proxies come from the same feed and the same books, so the measured "
    "divergence is line movement rather than a difference between sources.",
    "Pushes are excluded from the hit rate rather than counted as half-wins.",
    "Coinflip and no-market games are resolved by the same Elo tiebreak the "
    "weekly report applies, using only games completed before the replayed "
    "week so no future result informs its own pick.",
]

# Classified by source, not by book: both proxies carry real bookmaker keys.
FROZEN_PROXY_SOURCE = FROZEN_SOURCE
SUBMISSION_PROXY_SOURCE = SUBMISSION_SOURCE


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
    """Sort stored lines into the frozen-line and submission-time proxies.

    Classification is explicit in both directions and keyed on `source`. A
    default bucket would let in-season `poll-odds` snapshots — which share the
    database with the backfill — be graded as a proxy and quietly contaminate
    the result. nflverse closers land in the same bucket on purpose: they are
    a cross-check on the archive, not an input, because sourcing the two ends
    differently would put book composition inside the measured divergence.
    """
    frozen: list[MarketLine] = []
    submission: list[MarketLine] = []
    skipped: list[str] = []
    for line in lines:
        if line.source == FROZEN_PROXY_SOURCE:
            frozen.append(line)
        elif line.source == SUBMISSION_PROXY_SOURCE:
            submission.append(line)
        else:
            skipped.append(
                f"{line.game_id}: {line.source}/{line.book} snapshot at "
                f"{line.captured_at.isoformat()} is neither the frozen-line nor "
                f"the submission-time proxy — not graded"
            )
    return frozen, submission, skipped


def run_backtest(
    games: Sequence[Game],
    frozen: Sequence[MarketLine],
    submission: Sequence[MarketLine],
    thresholds: Thresholds | None = None,
    elo_config: EloConfig | None = None,
) -> BacktestReport:
    """Replay a sweep while recording only its boundary and aggregate totals."""
    sports = sorted({game.sport.value for game in games})
    seasons = sorted({game.season for game in games})
    logger.bind(
        event="backtest_started",
        sport=sports[0] if len(sports) == 1 else None,
        sports=sports,
        seasons=seasons,
        games=len(games),
        frozen_lines=len(frozen),
        submission_lines=len(submission),
    ).info(
        f"backtest started: {'/'.join(sports) or 'no sport'} "
        f"{'/'.join(str(season) for season in seasons) or 'no season'}, "
        f"{len(games)} games, {len(frozen)} frozen lines, "
        f"{len(submission)} submission lines"
    )

    with suppress_decision_logging():
        result = _run_backtest(games, frozen, submission, thresholds, elo_config)

    overall = result.overall
    logger.bind(
        event="backtest_finished",
        graded=overall.wins + overall.losses + overall.pushes,
        wins=overall.wins,
        losses=overall.losses,
        pushes=overall.pushes,
        skipped=len(result.skipped),
    ).info(f"backtest graded {overall.wins + overall.losses + overall.pushes} games")
    return result


def _run_backtest(
    games: Sequence[Game],
    frozen: Sequence[MarketLine],
    submission: Sequence[MarketLine],
    thresholds: Thresholds | None = None,
    elo_config: EloConfig | None = None,
) -> BacktestReport:
    """Replay completed games using the two same-source snapshot proxies.

    Spreads remain home-perspective throughout: the early-week snapshot becomes
    the frozen league line, and the pre-kickoff snapshots form the consensus it
    is measured against.
    """
    games_by_id = {game.game_id: game for game in games}
    # Both ends are collapsed by the SAME function. A per-game dict here would
    # be last-wins, silently grading against whichever book sorted last while
    # the other end took a median — which manufactures and erases edges.
    frozen_by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in frozen:
        frozen_by_game[line.game_id].append(line)
    submission_by_game: dict[str, list[MarketLine]] = defaultdict(list)
    for line in submission:
        submission_by_game[line.game_id].append(line)

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
        league_by_game: dict[str, LeagueLine] = {}

        for game in week_games:
            if game.home_score is None or game.away_score is None:
                skipped.append(f"{game.game_id}: unplayed game (missing final score)")
                continue
            frozen_lines = frozen_by_game.get(game.game_id, [])
            frozen_spread = consensus_spread(frozen_lines)
            if frozen_spread is None:
                skipped.append(f"{game.game_id}: missing frozen-line snapshot for the proxy")
                continue

            league = LeagueLine(
                game_id=game.game_id,
                season=game.season,
                week=game.week,
                spread_home=frozen_spread,
                posted_at=max(line.captured_at for line in frozen_lines),
            )
            league_by_game[game.game_id] = league
        week_submission = [
            line for game in week_games for line in submission_by_game.get(game.game_id, [])
        ]
        decided = decide_edges(
            list(league_by_game.values()),
            week_submission,
            week_games,
            history,
            thresholds,
            elo_config,
        )
        for edge in decided:
            game = games_by_id[edge.game_id]
            result = grade_pick(
                edge.side,
                game.home_score - game.away_score,
                league_by_game[edge.game_id].spread_home,
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
