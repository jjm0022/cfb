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
from pydantic import BaseModel

from pickem.models import Edge, Game, LeagueLine, MarketLine, Sport


class StoredDataset(BaseModel):
    games: list[Game]
    league_lines: list[LeagueLine]
    market_lines: list[MarketLine]


class AutomationState(BaseModel):
    signature: str | None = None
    checked_at: datetime | None = None
    error_fingerprint: str | None = None


def _game_from_row(row: tuple) -> Game:
    return Game(
        game_id=row[0],
        sport=Sport(row[1]),
        season=row[2],
        week=row[3],
        kickoff_utc=row[4],
        home_team_id=row[5],
        away_team_id=row[6],
        home_score=row[7],
        away_score=row[8],
    )


def _league_line_from_row(row: tuple) -> LeagueLine:
    return LeagueLine(
        game_id=row[0], season=row[1], week=row[2], spread_home=row[3], posted_at=row[4]
    )


def _market_line_from_row(row: tuple) -> MarketLine:
    return MarketLine(
        game_id=row[0],
        source=row[1],
        book=row[2],
        spread_home=row[3],
        total=row[4],
        captured_at=row[5],
    )


class Store:
    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self._con = duckdb.connect(str(path), read_only=read_only)
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

    def completed_archive_request_ids(self, request_ids: Sequence[str]) -> set[str]:
        if not request_ids:
            return set()
        rows = self._con.execute(
            "SELECT request_id FROM archive_requests WHERE request_id IN "
            + "("
            + ",".join("?" for _ in request_ids)
            + ")",
            list(request_ids),
        ).fetchall()
        return {row[0] for row in rows}

    def _insert_archive_request(
        self,
        request_id: str,
        sport: Sport,
        season: int,
        week: int,
        kind: str,
        requested_at: datetime,
        returned_at: datetime,
        line_count: int,
    ) -> bool:
        row = self._con.execute(
            """
            INSERT OR IGNORE INTO archive_requests
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING request_id
            """,
            [
                request_id,
                sport.value,
                season,
                week,
                kind,
                requested_at,
                returned_at,
                line_count,
                datetime.now(tz=UTC),
            ],
        ).fetchone()
        return row is not None

    def commit_archive_request(
        self,
        request_id: str,
        sport: Sport,
        season: int,
        week: int,
        kind: str,
        requested_at: datetime,
        returned_at: datetime,
        lines: Sequence[MarketLine],
    ) -> None:
        """Atomically store a completed archive response and its line history."""
        self._con.execute("BEGIN TRANSACTION")
        try:
            claimed = self._insert_archive_request(
                request_id,
                sport,
                season,
                week,
                kind,
                requested_at,
                returned_at,
                len(lines),
            )
            if claimed:
                self.append_market_lines(lines)
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")

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
        return [_game_from_row(row) for row in rows]

    def _load_dataset(self, where: str, params: list[object]) -> StoredDataset:
        games = self._con.execute(
            "SELECT game_id, sport, season, week, kickoff_utc, "
            "home_team_id, away_team_id, home_score, away_score FROM games g WHERE " + where,
            params,
        ).fetchall()
        league_lines = self._con.execute(
            "SELECT l.game_id, l.season, l.week, l.spread_home, l.posted_at "
            "FROM league_lines l JOIN games g USING (game_id) WHERE " + where,
            params,
        ).fetchall()
        market_lines = self._con.execute(
            "SELECT l.game_id, l.source, l.book, l.spread_home, l.total, l.captured_at "
            "FROM lines l JOIN games g USING (game_id) WHERE " + where,
            params,
        ).fetchall()
        return StoredDataset(
            games=[_game_from_row(row) for row in games],
            league_lines=[_league_line_from_row(row) for row in league_lines],
            market_lines=[_market_line_from_row(row) for row in market_lines],
        )

    def load_week(self, sport: Sport, season: int, week: int) -> StoredDataset:
        return self._load_dataset(
            "g.sport = ? AND g.season = ? AND g.week = ?",
            [sport.value, season, week],
        )

    def load_weeks(
        self, sport: Sport, season: int, start_week: int, end_week: int
    ) -> StoredDataset:
        return self._load_dataset(
            "g.sport = ? AND g.season = ? AND g.week BETWEEN ? AND ?",
            [sport.value, season, start_week, end_week],
        )

    def active_pickem_scopes(self, now: datetime) -> list[tuple[Sport, int, int]]:
        """Return the current incomplete pick week for every sport.

        A week's picks remain active until every picked game has a final
        score.  Once it is complete, the next incomplete picked week for that
        sport becomes active, even when its games have not kicked off yet.
        ``now`` is accepted so the resolver's time-based policy can stay at
        this database boundary as it evolves.
        """
        del now
        rows = self._con.execute(
            """
            WITH weekly AS (
                SELECT
                    g.sport,
                    g.season,
                    g.week,
                    COUNT(*) AS game_count,
                    COUNT(g.home_score) + COUNT(g.away_score) AS score_count
                FROM games g
                JOIN league_lines l USING (game_id)
                GROUP BY g.sport, g.season, g.week
            ), incomplete AS (
                SELECT
                    sport,
                    season,
                    week,
                    ROW_NUMBER() OVER (
                        PARTITION BY sport ORDER BY season, week
                    ) AS scope_rank
                FROM weekly
                WHERE score_count < game_count * 2
            )
            SELECT sport, season, week
            FROM incomplete
            WHERE scope_rank = 1
            ORDER BY sport
            """
        ).fetchall()
        return [(Sport(sport), season, week) for sport, season, week in rows]

    def pickem_scopes_for_week(self, season: int, week: int) -> list[tuple[Sport, int, int]]:
        """Return every sport that has stored league picks for an exact week."""
        rows = self._con.execute(
            """
            SELECT DISTINCT g.sport, g.season, g.week
            FROM games g
            JOIN league_lines l USING (game_id)
            WHERE g.season = ? AND g.week = ?
            ORDER BY g.sport
            """,
            [season, week],
        ).fetchall()
        return [
            (Sport(sport), scope_season, scope_week)
            for sport, scope_season, scope_week in rows
        ]

    def load_seasons(self, sport: Sport, start_season: int, end_season: int) -> StoredDataset:
        return self._load_dataset(
            "g.sport = ? AND g.season BETWEEN ? AND ?",
            [sport.value, start_season, end_season],
        )

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

    def automation_state(self, sport: Sport, season: int, week: int) -> AutomationState:
        row = self._con.execute(
            """
            SELECT recommendation_signature, checked_at, error_fingerprint
            FROM automation_state
            WHERE sport = ? AND season = ? AND week = ?
            """,
            [sport.value, season, week],
        ).fetchone()
        if row is None:
            return AutomationState()
        return AutomationState(signature=row[0], checked_at=row[1], error_fingerprint=row[2])

    def save_automation_state(
        self,
        sport: Sport,
        season: int,
        week: int,
        signature: str | None,
        checked_at: datetime | None,
        error_fingerprint: str | None,
    ) -> None:
        self._con.execute(
            """
            INSERT INTO automation_state
                (sport, season, week, recommendation_signature, checked_at, error_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (sport, season, week) DO UPDATE SET
                recommendation_signature = excluded.recommendation_signature,
                checked_at = excluded.checked_at,
                error_fingerprint = excluded.error_fingerprint
            """,
            [sport.value, season, week, signature, checked_at, error_fingerprint],
        )
