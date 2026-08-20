"""CFB games, results and betting lines from CollegeFootballData.

Talks to the CFBD REST API over httpx rather than the `cfbd` SDK. Every
published `cfbd` 5.x release pins `pydantic<2` and this project is built on
pydantic 2, so the two cannot coexist; the 4.x line predates the current API
and silently deserializes its camelCase fields to None, which made every team
name on `/games` come back null. The REST surface is small and stable, and the
`fetcher` seam means only the two default fetchers depend on it.

CFBD already expresses spreads home-negative, matching this project's
convention, so no sign flip happens here. The tests pin that.

A provider row with no spread is never silently dropped: `load_cfb_lines`
returns a `MarketLinesResult` and records a one-liner in `skipped` naming the
game and book, mirroring `ingest.cbs.ParseResult`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, SecretStr

from pickem import config
from pickem.models import Game, MarketLine, MarketLinesResult, Sport
from pickem.resolve.matchup import resolve_matchup
from pickem.resolve.resolver import TeamResolver

BASE_URL = "https://api.collegefootballdata.com"
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 1.0
_RETRYABLE = {429}

Fetcher = Callable[[int, int], list[dict]]


class CfbdApiError(RuntimeError):
    """The CFBD feed could not be read."""


class CfbdConfig(BaseModel):
    # SecretStr so a traceback repr cannot print the key.
    api_key: SecretStr

    @classmethod
    def from_env(cls) -> CfbdConfig:
        return cls(api_key=SecretStr(config.cfbd_api_key()))


def _get(
    config: CfbdConfig,
    path: str,
    params: dict,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """GET with bounded retry on rate limiting, 5xx and transport errors.

    CFBD rate-limits by request rate, separately from the monthly quota, and
    says so in the 429 body: wait and retry. A backfill sweeping many weeks
    will hit it, so retrying is the difference between a usable loader and one
    that dies partway through.
    """
    headers = {"Authorization": f"Bearer {config.api_key.get_secret_value()}"}
    last_error: str | None = None
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=30.0) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.get(path, params=params)
            except httpx.TransportError as exc:
                last_error = str(exc)
            else:
                if response.status_code < 400:
                    return response.json()
                if response.status_code not in _RETRYABLE and response.status_code < 500:
                    raise CfbdApiError(
                        f"cfbd returned {response.status_code} for {path}: {response.text}"
                    )
                last_error = f"{response.status_code}: {response.text}"
            if attempt < MAX_ATTEMPTS - 1:
                sleep(BACKOFF_SECONDS * (2**attempt))
    raise CfbdApiError(f"cfbd unreachable after {MAX_ATTEMPTS} attempts for {path}: {last_error}")


def default_games_fetcher(config: CfbdConfig) -> Fetcher:
    """Games for one week, restricted to FBS.

    `aliases.yaml` covers FBS, so both sides must be FBS. `classification=fbs`
    alone is not enough — it returns any game with at least one FBS side, so an
    FBS team hosting an FCS opponent still arrives and raises on a school this
    system will never pick.

    An FCS school that genuinely appears on the CBS sheet still fails loud at
    `ingest-cbs`, which is the right place to notice it and hand-add the alias.
    """

    def fetch(season: int, week: int) -> list[dict]:
        rows = _get(config, "/games", {"year": season, "week": week, "classification": "fbs"})
        return [
            {
                "home_team": row.get("homeTeam"),
                "away_team": row.get("awayTeam"),
                "start_date": row.get("startDate"),
                "home_points": row.get("homePoints"),
                "away_points": row.get("awayPoints"),
            }
            for row in rows
            if row.get("homeClassification") == "fbs" and row.get("awayClassification") == "fbs"
        ]

    return fetch


def default_lines_fetcher(config: CfbdConfig) -> Fetcher:
    def fetch(season: int, week: int) -> list[dict]:
        rows = _get(config, "/lines", {"year": season, "week": week})
        return [
            {
                "home_team": row.get("homeTeam"),
                "away_team": row.get("awayTeam"),
                "start_date": row.get("startDate"),
                "lines": [
                    {
                        "provider": book.get("provider"),
                        "spread": book.get("spread"),
                        "over_under": book.get("overUnder"),
                    }
                    for book in (row.get("lines") or [])
                ],
            }
            for row in rows
            # The betting endpoint has no classification filter, so FCS-only
            # matchups arrive here and would raise on an unmapped school.
            if row.get("homeClassification") == "fbs" and row.get("awayClassification") == "fbs"
        ]

    return fetch


def _kickoff(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def load_cfb_games(
    season: int, week: int, *, resolver: TeamResolver, fetcher: Fetcher
) -> list[Game]:
    games: list[Game] = []
    for row in fetcher(season, week):
        matchup = resolve_matchup(
            resolver=resolver,
            sport=Sport.CFB,
            season=season,
            week=week,
            away_name=row["away_team"],
            home_name=row["home_team"],
        )
        games.append(
            Game(
                game_id=matchup.game_id,
                sport=matchup.sport,
                season=matchup.season,
                week=matchup.week,
                kickoff_utc=_kickoff(row["start_date"]),
                home_team_id=matchup.home_team_id,
                away_team_id=matchup.away_team_id,
                home_score=row.get("home_points"),
                away_score=row.get("away_points"),
            )
        )
    return games


def load_cfb_lines(
    season: int,
    week: int,
    *,
    resolver: TeamResolver,
    fetcher: Fetcher,
    captured_at: datetime | None = None,
) -> MarketLinesResult:
    """Load per-book CFB spreads.

    `captured_at` defaults to now, which is right for a live poll. A historical
    load must pass the moment the line actually applied, or every row in the
    batch shares today's timestamp and `consensus_spread`'s latest-per-book
    ordering becomes arbitrary.
    """
    stamp = captured_at or datetime.now(tz=UTC)
    lines: list[MarketLine] = []
    skipped: list[str] = []
    for row in fetcher(season, week):
        matchup = resolve_matchup(
            resolver=resolver,
            sport=Sport.CFB,
            season=season,
            week=week,
            away_name=row["away_team"],
            home_name=row["home_team"],
        )
        for provider in row.get("lines") or []:
            if provider.get("spread") is None:
                skipped.append(f"{matchup.game_id}: {provider.get('provider')} — no spread")
                continue
            lines.append(
                MarketLine(
                    game_id=matchup.game_id,
                    source="cfbd",
                    book=provider["provider"],
                    spread_home=float(provider["spread"]),
                    total=provider.get("over_under"),
                    captured_at=stamp,
                )
            )
    return MarketLinesResult(lines=lines, skipped=skipped)
