"""CFB games, results and betting lines from CollegeFootballData.

CFBD already expresses spreads home-negative, matching this project's
convention, so no sign flip happens here. The tests pin that.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel

from pickem.models import Game, MarketLine, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

Fetcher = Callable[[int, int], list[dict]]


class CfbdConfig(BaseModel):
    api_key: str

    @classmethod
    def from_env(cls) -> CfbdConfig:
        key = os.environ.get("CFBD_API_KEY")
        if not key:
            raise RuntimeError("CFBD_API_KEY is not set")
        return cls(api_key=key)


def _client(config: CfbdConfig):
    import cfbd

    configuration = cfbd.Configuration(access_token=config.api_key)
    return cfbd.ApiClient(configuration)


def default_games_fetcher(config: CfbdConfig) -> Fetcher:
    import cfbd

    def fetch(season: int, week: int) -> list[dict]:
        api = cfbd.GamesApi(_client(config))
        return [g.to_dict() for g in api.get_games(year=season, week=week)]

    return fetch


def default_lines_fetcher(config: CfbdConfig) -> Fetcher:
    import cfbd

    def fetch(season: int, week: int) -> list[dict]:
        api = cfbd.BettingApi(_client(config))
        return [g.to_dict() for g in api.get_lines(year=season, week=week)]

    return fetch


def _kickoff(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def load_cfb_games(
    season: int, week: int, *, resolver: TeamResolver, fetcher: Fetcher
) -> list[Game]:
    games: list[Game] = []
    for row in fetcher(season, week):
        home = resolver.resolve(row["home_team"], Sport.CFB)
        away = resolver.resolve(row["away_team"], Sport.CFB)
        games.append(
            Game(
                game_id=make_game_id(Sport.CFB, season, week, away, home),
                sport=Sport.CFB,
                season=season,
                week=week,
                kickoff_utc=_kickoff(row["start_date"]),
                home_team_id=home,
                away_team_id=away,
                home_score=row.get("home_points"),
                away_score=row.get("away_points"),
            )
        )
    return games


def load_cfb_lines(
    season: int, week: int, *, resolver: TeamResolver, fetcher: Fetcher
) -> list[MarketLine]:
    lines: list[MarketLine] = []
    for row in fetcher(season, week):
        home = resolver.resolve(row["home_team"], Sport.CFB)
        away = resolver.resolve(row["away_team"], Sport.CFB)
        game_id = make_game_id(Sport.CFB, season, week, away, home)
        for provider in row.get("lines") or []:
            if provider.get("spread") is None:
                continue
            lines.append(
                MarketLine(
                    game_id=game_id,
                    source="cfbd",
                    book=provider["provider"],
                    spread_home=float(provider["spread"]),
                    total=provider.get("over_under"),
                    captured_at=datetime.now(tz=UTC),
                )
            )
    return lines
