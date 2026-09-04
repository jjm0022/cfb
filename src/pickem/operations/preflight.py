"""Pure readiness evaluation for the live weekly pick workflow."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from loguru import logger
from pydantic import BaseModel

from pickem.models import LIVE_SOURCE, Game, MarketLine, Sport
from pickem.store.db import StoredDataset


class PreflightGame(BaseModel):
    """Readiness and supporting measurements for one slate game."""

    game_id: str
    ready: bool
    reasons: list[str]
    kickoff_utc: datetime | None = None
    latest_snapshot_at: datetime | None = None
    distinct_books: int = 0


class PreflightResult(BaseModel):
    """The complete, side-effect-free result of a weekly readiness check."""

    ready: bool
    reasons: list[str]
    games: list[PreflightGame]
    expected_games: int
    game_count: int
    league_line_count: int


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def evaluate_preflight(
    dataset: StoredDataset,
    history: Sequence[Game],
    *,
    sport: Sport,
    now: datetime,
    expected_games: int,
    max_age_minutes: int = 60,
    min_books: int = 3,
) -> PreflightResult:
    """Evaluate all hard gates needed before generating a live report.

    This function only inspects its inputs. The CLI supplies an existing
    ``StoredDataset`` and prior-game history loaded through a read-only Store.
    """
    if expected_games <= 0:
        raise ValueError("expected_games must be positive")
    if max_age_minutes < 0:
        raise ValueError("max_age_minutes must be non-negative")
    if min_books < 1:
        raise ValueError("min_books must be positive")
    checked_now = _as_utc(now)
    if checked_now is None:
        raise ValueError("now must be timezone-aware")

    target_sport = Sport(sport)
    reasons: list[str] = []
    if len(dataset.games) != expected_games:
        reasons.append(f"expected {expected_games} games, found {len(dataset.games)}")
    if len(dataset.league_lines) != expected_games:
        reasons.append(f"expected {expected_games} league lines, found {len(dataset.league_lines)}")

    completed_history = any(
        game.sport == target_sport and game.home_score is not None and game.away_score is not None
        for game in history
    )
    if not completed_history:
        reasons.append(f"no prior completed {target_sport.value} history for Elo")

    league_game_ids = {line.game_id for line in dataset.league_lines}
    live_by_game: dict[str, list[MarketLine]] = {}
    for line in dataset.market_lines:
        if line.source == LIVE_SOURCE:
            live_by_game.setdefault(line.game_id, []).append(line)

    game_results: list[PreflightGame] = []
    for game in dataset.games:
        game_reasons: list[str] = []
        kickoff = _as_utc(game.kickoff_utc)
        if kickoff is None:
            game_reasons.append("invalid kickoff")
        elif kickoff <= checked_now:
            game_reasons.append("kickoff is not in the future")
        if game.game_id not in league_game_ids:
            game_reasons.append("missing league line")

        live_lines = live_by_game.get(game.game_id, [])
        valid_lines = [line for line in live_lines if _as_utc(line.captured_at) is not None]
        invalid_timestamps = len(valid_lines) != len(live_lines)
        if not live_lines:
            game_reasons.append("missing live market coverage")
            latest = None
            books = 0
        elif not valid_lines:
            game_reasons.append("invalid live market snapshot timestamp")
            latest = None
            books = 0
        else:
            latest = max(_as_utc(line.captured_at) for line in valid_lines)
            latest_lines = [line for line in valid_lines if _as_utc(line.captured_at) == latest]
            books = len({line.book for line in latest_lines})
            if invalid_timestamps:
                game_reasons.append("invalid live market snapshot timestamp")
            if latest > checked_now:
                game_reasons.append("latest live snapshot is future-dated")
            elif (checked_now - latest).total_seconds() > max_age_minutes * 60:
                game_reasons.append(f"latest live snapshot is older than {max_age_minutes} minutes")
            if books < min_books:
                game_reasons.append(
                    f"latest live snapshot has {books} distinct books; {min_books} required"
                )

        game_results.append(
            PreflightGame(
                game_id=game.game_id,
                ready=not game_reasons,
                reasons=game_reasons,
                kickoff_utc=kickoff,
                latest_snapshot_at=latest,
                distinct_books=books,
            )
        )

    for game_result in game_results:
        reasons.extend(f"{game_result.game_id}: {reason}" for reason in game_result.reasons)

    for game_result in game_results:
        if not game_result.ready:
            logger.bind(
                event="preflight_game_not_ready",
                game_id=game_result.game_id,
                reasons=game_result.reasons,
                distinct_books=game_result.distinct_books,
                latest_snapshot_at=game_result.latest_snapshot_at,
            ).warning(f"{game_result.game_id} not ready: {'; '.join(game_result.reasons)}")

    logger.bind(
        event="preflight_evaluated",
        sport=target_sport.value,
        ready=not reasons,
        expected_games=expected_games,
        game_count=len(dataset.games),
        league_line_count=len(dataset.league_lines),
        not_ready=sum(1 for game_result in game_results if not game_result.ready),
    ).info("preflight ready" if not reasons else "preflight not ready")

    return PreflightResult(
        ready=not reasons,
        reasons=reasons,
        games=game_results,
        expected_games=expected_games,
        game_count=len(dataset.games),
        league_line_count=len(dataset.league_lines),
    )


def _format_time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%MZ") if value else "—"


def render_preflight(result: PreflightResult) -> str:
    """Render a concise table and the overall decision for terminal output."""
    rows = [
        "Game                                   Status      Kickoff UTC       Latest UTC  Books",
        "-------------------------------------- ----------- ----------------- ------------ -----",
    ]
    for game in result.games:
        rows.append(
            f"{game.game_id:<38} {('READY' if game.ready else 'NOT READY'):<11} "
            f"{_format_time(game.kickoff_utc):<17} {_format_time(game.latest_snapshot_at):<16} "
            f"{game.distinct_books:>5}"
        )
    rows.append("")
    rows.append(f"Overall: {'READY' if result.ready else 'NOT READY'}")
    if result.reasons:
        rows.append("Reasons:")
        rows.extend(f"- {reason}" for reason in result.reasons)
    return "\n".join(rows)
