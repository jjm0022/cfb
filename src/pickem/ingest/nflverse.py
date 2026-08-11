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

import polars as pl

from pickem.models import Game, MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

Loader = Callable[[Sequence[int]], pl.DataFrame]


def _default_loader(seasons: Sequence[int]) -> pl.DataFrame:
    import nflreadpy

    return nflreadpy.load_schedules(seasons=list(seasons))


def _kickoff(gameday: str) -> datetime:
    return datetime.fromisoformat(str(gameday)).replace(tzinfo=UTC)


def load_nfl_games(
    seasons: Sequence[int], *, resolver: TeamResolver, loader: Loader | None = None
) -> list[Game]:
    frame = (loader or _default_loader)(seasons)
    games: list[Game] = []
    for row in frame.iter_rows(named=True):
        home = resolver.resolve(row["home_team"], Sport.NFL)
        away = resolver.resolve(row["away_team"], Sport.NFL)
        games.append(
            Game(
                game_id=make_game_id(Sport.NFL, row["season"], row["week"], away, home),
                sport=Sport.NFL,
                season=row["season"],
                week=row["week"],
                kickoff_utc=_kickoff(row["gameday"]),
                home_team_id=home,
                away_team_id=away,
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
        home = resolver.resolve(row["home_team"], Sport.NFL)
        away = resolver.resolve(row["away_team"], Sport.NFL)
        game_id = make_game_id(Sport.NFL, row["season"], row["week"], away, home)
        if row["spread_line"] is None:
            # never default a missing line to 0.0; that reads as a pick'em
            skipped.append(f"{game_id}: close — no spread")
            continue
        lines.append(
            MarketLine(
                game_id=game_id,
                source="nflverse",
                book="close",
                spread_home=-float(row["spread_line"]),
                total=row["total_line"],
                captured_at=_kickoff(row["gameday"]),
            )
        )
    return MarketLinesResult(lines=lines, skipped=skipped)
