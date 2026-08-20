"""The only module that talks to DuckDB.

`lines` is append-only: its primary key absorbs duplicate polls, and nothing
here issues UPDATE or DELETE against it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from importlib import resources
from pathlib import Path

import duckdb

from pickem.models import Edge, Game, LeagueLine, MarketLine, Sport


class Store:
    def __init__(self, path: Path | str) -> None:
        self._con = duckdb.connect(str(path))
        # Fixes the session timezone so TIMESTAMPTZ round-trips are
        # deterministic regardless of the host machine's local timezone.
        self._con.execute("SET TimeZone='UTC'")

    def init_schema(self) -> None:
        ddl = resources.files("pickem.store").joinpath("schema.sql").read_text()
        self._con.execute(ddl)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Closed on the failure path too: a leaked handle can leave a lock
        # behind on a file database and break the next command.
        self.close()

    def _executemany(self, sql: str, rows: Sequence[tuple]) -> None:
        """executemany, but "nothing to write" is not an error.

        DuckDB rejects an empty parameter list. A snapshot in which every row
        was skipped is an ordinary outcome, not a failure, and must not abort a
        backfill after the credits for it are already spent.
        """
        if not rows:
            return
        self._con.executemany(sql, rows)

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
        # Never blind-REPLACE: a source that does not carry scores (a CBS
        # paste) must not erase the finals `sync-results` already wrote, or the
        # Elo history silently shrinks and the tiebreak quietly degrades.
        self._executemany(
            """
            INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (game_id) DO UPDATE SET
                sport = excluded.sport,
                season = excluded.season,
                week = excluded.week,
                kickoff_utc = excluded.kickoff_utc,
                home_team_id = excluded.home_team_id,
                away_team_id = excluded.away_team_id,
                home_score = COALESCE(excluded.home_score, games.home_score),
                away_score = COALESCE(excluded.away_score, games.away_score)
            """,
            rows,
        )

    def insert_games_if_absent(self, games: Sequence[Game]) -> None:
        """Add games without touching rows that already exist.

        The CBS paste carries no kickoff time and no scores, so it must not
        overwrite a row an authoritative source already filled in.
        """
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
        self._executemany("INSERT OR IGNORE INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def upsert_league_lines(self, lines: Sequence[LeagueLine]) -> None:
        rows = [(x.game_id, x.season, x.week, x.spread_home, x.posted_at) for x in lines]
        self._executemany("INSERT OR REPLACE INTO league_lines VALUES (?, ?, ?, ?, ?)", rows)

    def append_market_lines(self, lines: Sequence[MarketLine]) -> None:
        rows = [(x.game_id, x.source, x.book, x.spread_home, x.total, x.captured_at) for x in lines]
        # INSERT OR IGNORE, never REPLACE: an existing snapshot is history.
        self._executemany("INSERT OR IGNORE INTO lines VALUES (?, ?, ?, ?, ?, ?)", rows)

    def market_lines_for(self, game_id: str, before: datetime | None = None) -> list[MarketLine]:
        sql = (
            "SELECT game_id, source, book, spread_home, total, captured_at "
            "FROM lines WHERE game_id = ?"
        )
        params: list[object] = [game_id]
        if before is not None:
            sql += " AND captured_at < ?"
            params.append(before)
        rows = self._con.execute(sql, params).fetchall()
        return [
            MarketLine(
                game_id=r[0], source=r[1], book=r[2], spread_home=r[3], total=r[4], captured_at=r[5]
            )
            for r in rows
        ]

    def league_lines_for_week(self, sport: Sport, season: int, week: int) -> list[LeagueLine]:
        rows = self._con.execute(
            """
            SELECT l.game_id, l.season, l.week, l.spread_home, l.posted_at
            FROM league_lines l JOIN games g USING (game_id)
            WHERE g.sport = ? AND l.season = ? AND l.week = ?
            """,
            [sport.value, season, week],
        ).fetchall()
        return [
            LeagueLine(game_id=r[0], season=r[1], week=r[2], spread_home=r[3], posted_at=r[4])
            for r in rows
        ]

    def games_for_week(self, sport: Sport, season: int, week: int) -> list[Game]:
        rows = self._con.execute(
            """
            SELECT game_id, sport, season, week, kickoff_utc,
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
                kickoff_utc=r[4],
                home_team_id=r[5],
                away_team_id=r[6],
                home_score=r[7],
                away_score=r[8],
            )
            for r in rows
        ]

    def games_before(self, sport: Sport, season: int, week: int) -> list[Game]:
        """Every game already played before this week, including prior seasons.

        The rating is only as good as its history: restricting it to the
        current season leaves every week-1 matchup at the initial rating.
        """
        rows = self._con.execute(
            """
            SELECT game_id, sport, season, week, kickoff_utc,
                   home_team_id, away_team_id, home_score, away_score
            FROM games
            WHERE sport = ? AND (season < ? OR (season = ? AND week < ?))
            """,
            [sport.value, season, season, week],
        ).fetchall()
        return [
            Game(
                game_id=r[0],
                sport=Sport(r[1]),
                season=r[2],
                week=r[3],
                kickoff_utc=r[4],
                home_team_id=r[5],
                away_team_id=r[6],
                home_score=r[7],
                away_score=r[8],
            )
            for r in rows
        ]

    def record_picks(
        self, edges: Sequence[Edge], season: int, week: int, generated_at: datetime
    ) -> None:
        """Persist a rendered sheet's picks so live results can be graded later.

        Keyed by `generated_at`, so re-running the report keeps every earlier
        sheet rather than overwriting the record of what was actually picked.
        """
        rows = [
            (season, week, e.game_id, e.side.value, e.delta, e.tier.value, generated_at)
            for e in edges
        ]
        self._executemany("INSERT OR IGNORE INTO picks VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def picks_for_week(self, season: int, week: int) -> list[tuple]:
        return self._con.execute(
            """
            SELECT season, week, game_id, side, edge_points, tier, generated_at
            FROM picks WHERE season = ? AND week = ? ORDER BY generated_at, game_id
            """,
            [season, week],
        ).fetchall()
