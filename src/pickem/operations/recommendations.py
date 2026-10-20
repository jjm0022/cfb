"""Generate weekly recommendations without submitting picks.

The CLI owns presentation and pick history.  This module owns the reusable
weekly computation, including the live-odds refresh used by both the CLI and
the automation monitor.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from pickem import config
from pickem.edge.divergence import Thresholds, rank_edges
from pickem.edge.pipeline import decide_edges
from pickem.ingest.odds import KeyRejected, OddsApiError, QuotaExhausted
from pickem.ingest.sgo import SgoClient
from pickem.models import (
    PINNACLE_SOURCE,
    Edge,
    Game,
    MarketLinesResult,
    OddsPull,
    PollTrigger,
    Sport,
    Tier,
)
from pickem.operations.odds_budget import GAMES_PER_30_DAYS, OddsBudgetSkipped, check_budget
from pickem.resolve.resolver import TeamResolver, UnknownTeamError
from pickem.store.db import Store

# Tier settings for live picks, per sport. NFL follows the market on any gap
# (the SLIGHT tier; docs/superpowers/specs/2026-10-06-nfl-slight-tier-design.md).
# To revert NFL, set it back to Thresholds().
LIVE_THRESHOLDS: dict[Sport, Thresholds] = {
    Sport.NFL: Thresholds(slight=True),
    Sport.CFB: Thresholds(),
}


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
    # Set when this refresh skipped its odds pull to stay inside pick'em's
    # SportsGameOdds share and no other skip was recorded in the 30 days before.
    odds_notice: str | None = None
    # Set on every skipped pull, so a reply can say no new odds were pulled.
    odds_skipped: str | None = None


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
            # Pinnacle rows (recorded 2026-09-25 to 2026-10-19) never enter the consensus.
            [line for line in dataset.market_lines if line.source != PINNACLE_SOURCE],
            dataset.games,
            store.games_before(sport, season, week),
            LIVE_THRESHOLDS[sport],
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


def _games_to_request(
    dataset,
    now: datetime,
    window_end: datetime,
    kickoffs: Collection[datetime] | None,
) -> list[Game]:
    """The slate games a pull should pay for.

    SportsGameOdds bills per game, so: only games not yet started (a started
    game would return in-play odds), inside the window, and for a kickoff
    poll only the games whose kickoff asked for it.
    """
    slate = {line.game_id for line in dataset.league_lines}
    wanted = None if kickoffs is None else set(kickoffs)
    return sorted(
        (
            game
            for game in dataset.games
            if game.game_id in slate
            and now < game.kickoff_utc <= window_end
            and (wanted is None or game.kickoff_utc in wanted)
        ),
        key=lambda game: (game.kickoff_utc, game.game_id),
    )


def poll_odds_snapshot(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    days: int = 7,
    window_start: datetime | None = None,
    kickoffs: Collection[datetime] | None = None,
    trigger: PollTrigger = PollTrigger.CLI,
    offset_hours: float | None = None,
    client_factory: Callable[..., SgoClient] | None = None,
) -> MarketLinesResult:
    """Fetch and append the live market snapshot for one ingested week.

    The slate and kickoff-window guards are kept here with the append-only
    write. ``window_start`` overrides the lower edge of the kickoff window;
    the default reaches twelve hours back.

    Only games not yet started are requested -- for a kickoff poll, only the
    games at ``kickoffs`` -- because SportsGameOdds bills per game. A pull
    that would take pick'em past its share raises ``OddsBudgetSkipped``
    instead of requesting anything.
    """
    if not db.exists():
        raise RecommendationDatabaseMissing(f"no database at {db}; run ingest-cbs first")

    with _open_store(db) as store:
        dataset = store.load_week(sport, season, week)
        slate = [line.game_id for line in dataset.league_lines]
        if not slate:
            raise RecommendationSlateMissing(
                f"no {sport.value} {season} week {week} games in the store; run ingest-cbs first"
            )
        window = (
            window_start if window_start is not None else now - timedelta(hours=12),
            now + timedelta(days=days),
        )
        games = _games_to_request(dataset, now, window[1], kickoffs)
        if not games:
            logger.bind(
                event="odds_poll_empty",
                sport=sport.value,
                season=season,
                week=week,
                trigger=trigger.value,
            ).info("no games left to poll; nothing requested")
            return MarketLinesResult(lines=[], skipped=[])
        used = check_budget(
            store,
            now=now,
            sport=sport,
            season=season,
            week=week,
            trigger=trigger,
            offset_hours=offset_hours,
            games_requested=len(games),
        )
        factory = client_factory or SgoClient
        with factory(config.sgo_api_key()) as client:
            pull = client.fetch_spreads(
                resolver=TeamResolver.default(),
                sport=sport,
                season=season,
                week=week,
                now=now,
                slate=slate,
                window=window,
                games=games,
            )
            store.append_market_lines(pull.result.lines)
            store.record_odds_pull(
                OddsPull(
                    pulled_at=now,
                    sport=sport,
                    season=season,
                    week=week,
                    trigger=trigger,
                    offset_hours=offset_hours,
                    games_requested=len(games),
                    games_returned=pull.events_returned,
                )
            )
            total = used + pull.events_returned
            logger.bind(
                event="odds_usage",
                trigger=trigger.value,
                games_requested=len(games),
                games_returned=pull.events_returned,
                used_30d=total,
                share=GAMES_PER_30_DAYS,
            ).info(
                f"pick'em has used {total} of its {GAMES_PER_30_DAYS} "
                "SportsGameOdds games in 30 days"
            )
            if trigger is PollTrigger.WEEK_START:
                usage = client.account_usage()
                logger.bind(event="odds_account_usage", usage=usage).info(
                    f"SportsGameOdds account usage, props included: {usage}"
                )
    return pull.result


def refresh_recommendations(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    window_start: datetime | None = None,
    pending_as_of: datetime | None = None,
    kickoffs: Collection[datetime] | None = None,
    trigger: PollTrigger = PollTrigger.MANUAL,
    offset_hours: float | None = None,
    pull_odds: bool = True,
) -> RecommendationSnapshot:
    """Append a current market snapshot (unless ``pull_odds`` is off), then decide.

    A pull the budget skips is not a failure: the picks are re-decided from
    the lines already stored, and the snapshot carries the notice when the
    owner should hear about it.
    """
    notice: str | None = None
    skipped_message: str | None = None
    if pull_odds:
        try:
            poll_odds_snapshot(
                db,
                sport,
                season,
                week,
                now,
                window_start=window_start,
                kickoffs=kickoffs,
                trigger=trigger,
                offset_hours=offset_hours,
            )
        except OddsBudgetSkipped as skipped:
            skipped_message = str(skipped)
            notice = skipped_message if skipped.notify else None
    snapshot = generate_recommendations(
        db, sport, season, week, now, pending_as_of=pending_as_of
    )
    return replace(snapshot, odds_notice=notice, odds_skipped=skipped_message)


__all__ = [
    "RecommendationDatabaseMissing",
    "RecommendationSlateMissing",
    "RecommendationSnapshot",
    "generate_recommendations",
    "poll_odds_snapshot",
    "refresh_recommendations",
    "KeyRejected",
    "OddsApiError",
    "OddsBudgetSkipped",
    "QuotaExhausted",
    "UnknownTeamError",
]
