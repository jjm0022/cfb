"""Shared records passed between modules.

Every spread in this system is home-perspective: home favored by 3 is -3.0.
Adapters normalize to this convention at ingest; nothing downstream re-checks it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, model_validator


class Sport(StrEnum):
    NFL = "nfl"
    CFB = "cfb"


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"


class Tier(StrEnum):
    STRONG = "strong"
    LEAN = "lean"
    COINFLIP = "coinflip"
    NO_MARKET = "no_market"


def make_game_id(sport: Sport, season: int, week: int, away_team_id: str, home_team_id: str) -> str:
    """Deterministic game identity, independent of any source's own IDs.

    Two sources describing the same matchup produce the same string, which is
    what lets CBS, CFBD, nflverse and the odds feed be joined without a
    per-source ID crosswalk.
    """
    return f"{sport.value}-{season}-{week:02d}-{away_team_id}-at-{home_team_id}"


class Game(BaseModel):
    game_id: str
    sport: Sport
    season: int
    week: int
    kickoff_utc: datetime
    home_team_id: str
    away_team_id: str
    home_score: int | None = None
    away_score: int | None = None

    @model_validator(mode="after")
    def _teams_must_differ(self) -> Game:
        if self.home_team_id == self.away_team_id:
            raise ValueError(f"a team cannot play itself: {self.home_team_id}")
        return self


class LeagueLine(BaseModel):
    """The frozen CBS spread. One per game per week."""

    game_id: str
    season: int
    week: int
    spread_home: float
    posted_at: datetime


# `source` says which role a stored market row plays; `book` keeps the real
# bookmaker key in every case, so per-book detail survives and consensus_spread
# still collapses correctly. The backtest classifies its two proxies on these,
# which is what keeps an in-season poll from ever being graded as one.
LIVE_SOURCE = "oddsapi"
FROZEN_SOURCE = "oddsapi:frozen"
SUBMISSION_SOURCE = "oddsapi:submit"


class MarketLine(BaseModel):
    """One book's spread at one moment. Append-only; never updated in place."""

    game_id: str
    source: str
    book: str
    spread_home: float
    total: float | None = None
    captured_at: datetime


class MarketLinesResult(BaseModel):
    """Return shape for market-line loaders: what parsed, and what did not.

    Mirrors `ingest.cbs.ParseResult` — a row with no usable spread is counted
    in `skipped`, never silently dropped.
    """

    lines: list[MarketLine]
    skipped: list[str]


class Edge(BaseModel):
    """The output of the strategy for a single game.

    Carries the two numbers it was derived from so any pick can be audited
    without opening the database.
    """

    game_id: str
    side: Side
    delta: float
    tier: Tier
    league_spread: float
    market_spread: float | None
    rationale: str
