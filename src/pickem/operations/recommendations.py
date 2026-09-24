"""Generate weekly recommendations without submitting picks.

The CLI owns presentation and pick history.  This module owns the reusable
weekly computation, including the live-odds refresh used by both the CLI and
the automation monitor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from pickem import config
from pickem.edge.divergence import rank_edges
from pickem.edge.pipeline import decide_edges
from pickem.ingest.odds import (
    CFB_KEY,
    NFL_KEY,
    OddsApiError,
    OddsClient,
    QuotaExhausted,
    _safe_error_detail,
)
from pickem.models import PINNACLE_SOURCE, Edge, MarketLinesResult, Sport, Tier
from pickem.resolve.resolver import TeamResolver, UnknownTeamError
from pickem.store.db import Store


class RecommendationDatabaseMissing(FileNotFoundError):
    """The requested weekly refresh has no database to read."""


class RecommendationSlateMissing(RuntimeError):
    """The requested week has no ingested league lines."""


@dataclass(frozen=True)
class RecommendationSnapshot:
    sport: Sport
    season: int
    week: int
    generated_at: datetime
    edges: tuple[Edge, ...]


def _open_store(db: Path) -> Store:
    """Open a writable store and ensure a fresh/default path has its schema."""
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db)
    store.init_schema()
    return store


def _still_pending(dataset, cutoff: datetime | None) -> list:
    """Return the league lines whose game has not kicked off by ``cutoff``.

    Only a game with a *known* kickoff at or before the cutoff is dropped. A
    league line with no matching game record keeps its existing path, so a
    missing game still surfaces as a loud ``MissingGameError`` downstream
    rather than being quietly swept out by this filter.
    """
    if cutoff is None:
        return list(dataset.league_lines)

    locked = {game.game_id for game in dataset.games if game.kickoff_utc <= cutoff}
    if not locked:
        return list(dataset.league_lines)

    pending = [line for line in dataset.league_lines if line.game_id not in locked]
    # One aggregate line, not one warning per game. These are not anomalous
    # drops -- every game locks eventually, and a per-game warning on every
    # poll would rebuild exactly the noise this logging pass removed.
    excluded = sorted(locked & {line.game_id for line in dataset.league_lines})
    logger.bind(
        event="locked_games_excluded",
        locked=len(excluded),
        game_ids=excluded,
        cutoff=cutoff,
    ).info(f"excluded {len(excluded)} game(s) past kickoff: {', '.join(excluded)}")
    return pending


def generate_recommendations(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    pending_as_of: datetime | None = None,
) -> RecommendationSnapshot:
    """Build ranked recommendations from the currently stored weekly data.

    This function deliberately does not call :meth:`Store.record_picks`.
    Persisting submitted picks remains a report concern so automation can
    inspect recommendation changes without creating grading records.

    ``pending_as_of`` restricts the board to games that have not kicked off by
    that moment. Automation passes it because a pick that is already locked
    cannot be acted on, so re-deciding it every poll is noise at best and a
    misleading notification at worst. It defaults to ``None`` -- the pick sheet
    renders the whole week, played games included.
    """
    with _open_store(db) as store:
        dataset = store.load_week(sport, season, week)
        edges = decide_edges(
            _still_pending(dataset, pending_as_of),
            # Pinnacle is recorded for a later comparison, not used: the
            # consensus stays the US books' median until a backtest says so.
            [line for line in dataset.market_lines if line.source != PINNACLE_SOURCE],
            dataset.games,
            store.games_before(sport, season, week),
        )
    ranked = tuple(rank_edges(edges))
    logger.bind(
        event="recommendations_generated",
        sport=sport.value,
        season=season,
        week=week,
        edges=len(ranked),
        tiers={tier.value: sum(1 for edge in ranked if edge.tier is tier) for tier in Tier},
    ).info(f"generated {len(ranked)} recommendations")
    return RecommendationSnapshot(sport, season, week, now, ranked)


def poll_odds_snapshot(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    days: int = 7,
    window_start: datetime | None = None,
    client_factory: Callable[..., OddsClient] | None = None,
) -> MarketLinesResult:
    """Fetch and append the live market snapshot for one ingested week.

    The slate and kickoff-window guards are kept here with the append-only
    write.  ``client_factory`` is an adapter seam for the CLI's existing HTTP
    transport test seam; production callers leave it unset.

    ``window_start`` overrides the lower edge of the kickoff window.  The
    default reaches twelve hours back so a poll running slightly late still
    covers the game it was meant to.  A poll anchored to a *later* kickoff
    passes ``now`` instead: the feed returns every event with posted odds, and
    an hour before a night game the afternoon games are in progress, quoting
    live in-game spreads.  Those are not the pre-kickoff market this system
    reasons about, and `lines` is append-only, so a stored one cannot be
    withdrawn.
    """
    if not db.exists():
        raise RecommendationDatabaseMissing(f"no database at {db}; run ingest-cbs first")

    key = NFL_KEY if sport is Sport.NFL else CFB_KEY
    with _open_store(db) as store:
        dataset = store.load_week(sport, season, week)
        slate = [line.game_id for line in dataset.league_lines]
        if not slate:
            raise RecommendationSlateMissing(
                f"no {sport.value} {season} week {week} games in the store; run ingest-cbs first"
            )

        factory = client_factory or OddsClient
        request = {
            "resolver": TeamResolver.default(),
            "sport": sport,
            "season": season,
            "week": week,
            "now": now,
            "slate": slate,
            "window": (
                window_start if window_start is not None else now - timedelta(hours=12),
                now + timedelta(days=days),
            ),
        }
        with factory(config.odds_api_key()) as client:
            result = client.fetch_spreads(key, **request)
            store.append_market_lines(result.lines)
            _poll_pinnacle(client, store, key, request)
    return result


def _poll_pinnacle(client: OddsClient, store: Store, key: str, request: dict) -> None:
    """Record Pinnacle alone, for a later sharp-book comparison.

    One extra credit per poll. Never allowed to fail the poll it rides on:
    the US books are what the picks use.
    """
    try:
        pinnacle = client.fetch_spreads(
            key, **request, bookmakers="pinnacle", source=PINNACLE_SOURCE
        )
        store.append_market_lines(pinnacle.lines)
    except Exception as error:
        # The final "unreachable" error embeds the last transport error's text.
        detail = _safe_error_detail(error, config.odds_api_key())
        logger.bind(
            event="pinnacle_poll_failed",
            sport=request["sport"].value,
            week=request["week"],
            error_type=type(error).__name__,
            error_detail=detail,
        ).warning(f"Pinnacle poll failed; US books were stored: {detail}")


def refresh_recommendations(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    window_start: datetime | None = None,
    pending_as_of: datetime | None = None,
) -> RecommendationSnapshot:
    """Append a current market snapshot, then generate recommendations."""
    poll_odds_snapshot(db, sport, season, week, now, window_start=window_start)
    return generate_recommendations(
        db, sport, season, week, now, pending_as_of=pending_as_of
    )


__all__ = [
    "RecommendationDatabaseMissing",
    "RecommendationSlateMissing",
    "RecommendationSnapshot",
    "generate_recommendations",
    "poll_odds_snapshot",
    "refresh_recommendations",
    "OddsApiError",
    "QuotaExhausted",
    "UnknownTeamError",
]
