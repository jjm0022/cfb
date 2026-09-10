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
from pickem.ingest.odds import CFB_KEY, NFL_KEY, OddsApiError, OddsClient, QuotaExhausted
from pickem.models import Edge, MarketLinesResult, Sport, Tier
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


def generate_recommendations(
    db: Path, sport: Sport, season: int, week: int, now: datetime
) -> RecommendationSnapshot:
    """Build ranked recommendations from the currently stored weekly data.

    This function deliberately does not call :meth:`Store.record_picks`.
    Persisting submitted picks remains a report concern so automation can
    inspect recommendation changes without creating grading records.
    """
    with _open_store(db) as store:
        dataset = store.load_week(sport, season, week)
        edges = decide_edges(
            dataset.league_lines,
            dataset.market_lines,
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
        with factory(config.odds_api_key()) as client:
            result = client.fetch_spreads(
                key,
                resolver=TeamResolver.default(),
                sport=sport,
                season=season,
                week=week,
                now=now,
                slate=slate,
                window=(
                    window_start if window_start is not None else now - timedelta(hours=12),
                    now + timedelta(days=days),
                ),
            )
        store.append_market_lines(result.lines)
    return result


def refresh_recommendations(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    window_start: datetime | None = None,
) -> RecommendationSnapshot:
    """Append a current market snapshot, then generate recommendations."""
    poll_odds_snapshot(db, sport, season, week, now, window_start=window_start)
    return generate_recommendations(db, sport, season, week, now)


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
