"""NFL games, results and closing lines from nflverse via nflreadpy.

nflverse expresses `spread_line` as a positive number when the home team is
favored. This project stores home-perspective spreads, where a home favorite is
negative. The negation below is the only place that conversion happens.

A row with no spread is never silently dropped: `load_nfl_closing_lines`
returns a `MarketLinesResult` and records a one-liner in `skipped` naming the
game, mirroring `ingest.cbs.ParseResult`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import polars as pl

from pickem.models import Game, MarketLine, MarketLinesResult, Sport
from pickem.resolve.matchup import resolve_matchup
from pickem.resolve.resolver import TeamResolver

Loader = Callable[[Sequence[int]], pl.DataFrame]


def _default_loader(seasons: Sequence[int]) -> pl.DataFrame:
    import nflreadpy

    return nflreadpy.load_schedules(seasons=list(seasons))


# nflverse states every kickoff in US Eastern, including neutral-site
# internationals: a London game reads 09:30, not its 14:30 local time.
EASTERN = ZoneInfo("America/New_York")


def _kickoff(gameday: str, gametime: str | None = None) -> datetime:
    """The true kickoff instant.

    The date lives in `gameday` and the local clock time in `gametime`. The
    offset must be derived from the date rather than fixed, so a January
    playoff game gets EST and a September game gets EDT.

    A missing `gametime` degrades to midnight UTC rather than raising: a poor
    timestamp, but a legible one, and one malformed row cannot abort a whole
    season's load.
    """
    day = datetime.fromisoformat(str(gameday))
    if not gametime:
        return day.replace(tzinfo=UTC)
    hour, _, minute = str(gametime).partition(":")
    return day.replace(hour=int(hour), minute=int(minute), tzinfo=EASTERN).astimezone(UTC)


def load_nfl_games(
    seasons: Sequence[int], *, resolver: TeamResolver, loader: Loader | None = None
) -> list[Game]:
    frame = (loader or _default_loader)(seasons)
    games: list[Game] = []
    for row in frame.iter_rows(named=True):
        matchup = resolve_matchup(
            resolver=resolver,
            sport=Sport.NFL,
            season=row["season"],
            week=row["week"],
            away_name=row["away_team"],
            home_name=row["home_team"],
        )
        games.append(
            Game(
                game_id=matchup.game_id,
                sport=matchup.sport,
                season=matchup.season,
                week=matchup.week,
                kickoff_utc=_kickoff(row["gameday"], row.get("gametime")),
                home_team_id=matchup.home_team_id,
                away_team_id=matchup.away_team_id,
                home_score=row["home_score"],
                away_score=row["away_score"],
            )
        )
    return games


def load_nfl_closing_lines(
    seasons: Sequence[int], *, resolver: TeamResolver, loader: Loader | None = None
) -> MarketLinesResult:
    frame = (loader or _default_loader)(seasons)
    lines: list[MarketLine] = []
    skipped: list[str] = []
    for row in frame.iter_rows(named=True):
        # Resolve teams first (unknown teams must still raise, never become a
        # skipped line) so a missing spread can be named by game_id below.
        matchup = resolve_matchup(
            resolver=resolver,
            sport=Sport.NFL,
            season=row["season"],
            week=row["week"],
            away_name=row["away_team"],
            home_name=row["home_team"],
        )
        if row["spread_line"] is None:
            # never default a missing line to 0.0; that reads as a pick'em
            skipped.append(f"{matchup.game_id}: close — no spread")
            continue
        lines.append(
            MarketLine(
                game_id=matchup.game_id,
                source="nflverse",
                book="close",
                spread_home=-float(row["spread_line"]),
                total=row["total_line"],
                # Deliberately date-only, though _kickoff can now do better.
                # captured_at is part of the `lines` primary key and these rows
                # are already stored; refining it would append a near-duplicate
                # of every one of them to an append-only table. These closers
                # are a cross-check for the archive backfill, not an input, so
                # the coarse timestamp costs nothing.
                captured_at=_kickoff(row["gameday"]),
            )
        )
    return MarketLinesResult(lines=lines, skipped=skipped)
