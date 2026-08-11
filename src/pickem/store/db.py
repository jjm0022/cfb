"""The only module that talks to DuckDB.

`lines` is append-only: its primary key absorbs duplicate polls, and nothing
here issues UPDATE or DELETE against it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import duckdb

from pickem.models import Game, LeagueLine, MarketLine, Sport


class Store:
    def __init__(self, path: Path | str) -> None:
        self._con = duckdb.connect(str(path))
        # This build has no pytz, which duckdb needs to materialize TIMESTAMPTZ
        # values as tz-aware Python datetimes on fetch. Pin the session to UTC
        # and read timestamp columns back via `AT TIME ZONE 'UTC'`, which
        # yields a naive-UTC datetime through the pytz-free fast path; callers
        # below reattach tzinfo=UTC. Writes are unaffected — binding a
        # tz-aware datetime as a query parameter never touches pytz.
        self._con.execute("SET TimeZone='UTC'")

    def init_schema(self) -> None:
        ddl = resources.files("pickem.store").joinpath("schema.sql").read_text()
        self._con.execute(ddl)

    def close(self) -> None:
        self._con.close()

    def upsert_games(self, games: Sequence[Game]) -> None:
        rows = [
            (
                g.game_id,
                g.sport.value,
                g.season,
                g.week,
                g.kickoff_utc,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score,
            )
            for g in games
        ]
        self._con.executemany(
            "INSERT OR REPLACE INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )

    def upsert_league_lines(self, lines: Sequence[LeagueLine]) -> None:
        rows = [(x.game_id, x.season, x.week, x.spread_home, x.posted_at) for x in lines]
        self._con.executemany("INSERT OR REPLACE INTO league_lines VALUES (?, ?, ?, ?, ?)", rows)

    def append_market_lines(self, lines: Sequence[MarketLine]) -> None:
        rows = [
            (x.game_id, x.source, x.book, x.spread_home, x.total, x.captured_at) for x in lines
        ]
        # INSERT OR IGNORE, never REPLACE: an existing snapshot is history.
        self._con.executemany("INSERT OR IGNORE INTO lines VALUES (?, ?, ?, ?, ?, ?)", rows)

    def market_lines_for(self, game_id: str, before: datetime | None = None) -> list[MarketLine]:
        sql = (
            "SELECT game_id, source, book, spread_home, total, captured_at AT TIME ZONE 'UTC' "
            "FROM lines WHERE game_id = ?"
        )
        params: list = [game_id]
        if before is not None:
            sql += " AND captured_at < ?"
            params.append(before)
        rows = self._con.execute(sql, params).fetchall()
        return [
            MarketLine(
                game_id=r[0],
                source=r[1],
                book=r[2],
                spread_home=r[3],
                total=r[4],
                captured_at=r[5].replace(tzinfo=UTC),
            )
            for r in rows
        ]

    def league_lines_for_week(self, sport: Sport, season: int, week: int) -> list[LeagueLine]:
        rows = self._con.execute(
            """
            SELECT l.game_id, l.season, l.week, l.spread_home, l.posted_at AT TIME ZONE 'UTC'
            FROM league_lines l JOIN games g USING (game_id)
            WHERE g.sport = ? AND l.season = ? AND l.week = ?
            """,
            [sport.value, season, week],
        ).fetchall()
        return [
            LeagueLine(
                game_id=r[0],
                season=r[1],
                week=r[2],
                spread_home=r[3],
                posted_at=r[4].replace(tzinfo=UTC),
            )
            for r in rows
        ]

    def games_for_week(self, sport: Sport, season: int, week: int) -> list[Game]:
        rows = self._con.execute(
            """
            SELECT game_id, sport, season, week, kickoff_utc AT TIME ZONE 'UTC',
                   home_team_id, away_team_id, home_score, away_score
            FROM games WHERE sport = ? AND season = ? AND week = ?
            """,
            [sport.value, season, week],
        ).fetchall()
        return [
            Game(
                game_id=r[0],
                sport=Sport(r[1]),
                season=r[2],
                week=r[3],
                kickoff_utc=r[4].replace(tzinfo=UTC),
                home_team_id=r[5],
                away_team_id=r[6],
                home_score=r[7],
                away_score=r[8],
            )
            for r in rows
        ]
