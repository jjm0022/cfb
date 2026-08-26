"""Build audit-ready, pre-kickoff feature rows for CFB COINFLIP games."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from statistics import fmean

from pydantic import BaseModel

from pickem.edge.divergence import consensus_spread
from pickem.models import Game, MarketLine, Sport

_COINFLIP_BOUNDARY = 1.0


class CoinflipRow(BaseModel):
    game_id: str
    season: int
    week: int
    kickoff_utc: datetime
    frozen_spread: float
    median_delta: float
    mean_delta: float
    book_balance: float
    target_home_cover: int | None
    is_push: bool


class CoinflipDataset(BaseModel):
    rows: list[CoinflipRow]
    skipped: list[str]


def latest_by_book(lines: Sequence[MarketLine]) -> list[MarketLine]:
    """Choose a deterministic latest quote from every book.

    The market API records append-only snapshots. A frequently sampled book
    therefore gets one vote, never one vote per archived snapshot. Spread is
    a deterministic tie-breaker for otherwise indistinguishable timestamps.
    """
    latest: dict[str, MarketLine] = {}
    for line in sorted(lines, key=lambda line: (line.book, line.captured_at, line.spread_home)):
        latest[line.book] = line
    return [latest[book] for book in sorted(latest)]


def build_coinflip_rows(
    games: Sequence[Game],
    frozen: Sequence[MarketLine],
    submission: Sequence[MarketLine],
) -> CoinflipDataset:
    """Turn completed, eligible CFB games into leakage-safe model rows.

    The median feature deliberately uses the live strategy's
    :func:`consensus_spread`, keeping experiment membership aligned with the
    shipped COINFLIP definition. This function performs no I/O, so a caller can
    replay the identical archive independently of the database.
    """
    frozen_by_game = _group_by_game(frozen)
    submission_by_game = _group_by_game(submission)
    rows: list[CoinflipRow] = []
    skipped: list[str] = []

    for game in sorted(games, key=lambda game: (game.season, game.week, game.game_id)):
        frozen_lines = frozen_by_game.get(game.game_id, [])
        submission_lines = submission_by_game.get(game.game_id, [])

        if game.sport is not Sport.CFB:
            skipped.append(f"{game.game_id}: non-CFB game is outside the experiment population")
            continue
        if game.home_score is None or game.away_score is None:
            skipped.append(f"{game.game_id}: unplayed game (missing final score)")
            continue
        if not frozen_lines:
            skipped.append(f"{game.game_id}: missing frozen-line snapshot")
            continue
        if not submission_lines:
            skipped.append(f"{game.game_id}: missing submission-time snapshot")
            continue
        if _has_snapshot_at_or_after_kickoff([*frozen_lines, *submission_lines], game.kickoff_utc):
            skipped.append(f"{game.game_id}: snapshot at or after kickoff is not leakage-safe")
            continue
        if not _all_spreads_finite([*frozen_lines, *submission_lines]):
            skipped.append(f"{game.game_id}: non-finite market spread")
            continue

        frozen_latest = latest_by_book(frozen_lines)
        submission_latest = latest_by_book(submission_lines)
        frozen_spread = consensus_spread(frozen_latest)
        market_median = consensus_spread(submission_latest)
        if frozen_spread is None:
            skipped.append(f"{game.game_id}: missing frozen-line books")
            continue
        if market_median is None:
            skipped.append(f"{game.game_id}: missing submission-time books")
            continue

        market_spreads = [line.spread_home for line in submission_latest]
        median_delta = frozen_spread - market_median
        mean_delta = frozen_spread - fmean(market_spreads)
        book_balance = _book_balance(frozen_spread, market_spreads)
        features = (frozen_spread, median_delta, mean_delta, book_balance)
        if not all(math.isfinite(value) for value in features):
            skipped.append(f"{game.game_id}: non-finite feature")
            continue
        if abs(median_delta) >= _COINFLIP_BOUNDARY:
            skipped.append(
                f"{game.game_id}: not coinflip (median delta {median_delta:+.3f} is outside "
                f"the {_COINFLIP_BOUNDARY:.1f}-point boundary)"
            )
            continue

        adjusted_margin = game.home_score - game.away_score + frozen_spread
        is_push = adjusted_margin == 0
        rows.append(
            CoinflipRow(
                game_id=game.game_id,
                season=game.season,
                week=game.week,
                kickoff_utc=game.kickoff_utc,
                frozen_spread=frozen_spread,
                median_delta=median_delta,
                mean_delta=mean_delta,
                book_balance=book_balance,
                target_home_cover=None if is_push else int(adjusted_margin > 0),
                is_push=is_push,
            )
        )

    return CoinflipDataset(rows=rows, skipped=skipped)


def _group_by_game(lines: Sequence[MarketLine]) -> dict[str, list[MarketLine]]:
    grouped: dict[str, list[MarketLine]] = defaultdict(list)
    for line in lines:
        grouped[line.game_id].append(line)
    return grouped


def _has_snapshot_at_or_after_kickoff(lines: Sequence[MarketLine], kickoff: datetime) -> bool:
    return any(line.captured_at >= kickoff for line in lines)


def _all_spreads_finite(lines: Sequence[MarketLine]) -> bool:
    return all(math.isfinite(line.spread_home) for line in lines)


def _book_balance(frozen_spread: float, market_spreads: Sequence[float]) -> float:
    deltas = [frozen_spread - spread for spread in market_spreads]
    positive = sum(delta > 0 for delta in deltas)
    negative = sum(delta < 0 for delta in deltas)
    return (positive - negative) / len(deltas)
