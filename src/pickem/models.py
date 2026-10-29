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
    SLIGHT = "slight"
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
# Pinnacle alone, polled beside the US books and recorded for a later
# sharp-book comparison. Kept out of the consensus until that comparison
# says otherwise.
PINNACLE_SOURCE = "oddsapi:pinnacle"
# Live spreads from SportsGameOdds, since The Odds API key was deactivated on
# 2026-10-20. Every reader of "the live market" accepts both labels, so weeks
# polled before the switch still render.
SGO_SOURCE = "sportsgameodds"
# CollegeFootballData's book lines, used only for college games SportsGameOdds
# has no lines for (it listed Utah at Cincinnati as empty duplicates in 2026).
CFBD_SOURCE = "cfbd"
LIVE_SOURCES = frozenset({LIVE_SOURCE, SGO_SOURCE, CFBD_SOURCE})


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
    # The historical Odds API returns the instant its envelope was captured.
    # Live and CFBD loaders have no equivalent envelope, so they leave this
    # unset; archive commits must reject that absence rather than inventing it.
    snapshot_at: datetime | None = None


class PollTrigger(StrEnum):
    """Why a live odds pull ran. The budget and the logs read it."""

    WEEK_START = "week_start"
    MIDWEEK = "midweek"
    KICKOFF = "kickoff"
    MANUAL = "manual"
    CLI = "cli"


class OddsPull(BaseModel):
    """One live odds pull, or one the budget skipped. Append-only."""

    pulled_at: datetime
    sport: Sport
    season: int
    week: int
    trigger: PollTrigger
    offset_hours: float | None = None
    games_requested: int
    games_returned: int
    skipped: bool = False


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


# Where a recommendation-history row came from.
HISTORY_MONITOR = "monitor"
HISTORY_REPORT = "report"
HISTORY_LOG_BACKFILL = "log_backfill"


class PoolResult(BaseModel):
    """One entrant's line on a CBS Weekly Standings page."""

    season: int
    pool_week: int
    entry_id: str
    name: str
    rank: int
    points: int
    ytd: int
    tiebreak: int | None
    imported_at: datetime


class PoolPick(BaseModel):
    """One entrant's pick on one game. `side` is None when it was left blank."""

    season: int
    pool_week: int
    entry_id: str
    game_id: str
    cbs_event_id: int
    side: Side | None
    cbs_correct: bool | None


class RecommendationRecord(BaseModel):
    """What the model said about a game, and when. Not a submitted pick."""

    game_id: str
    sport: Sport
    season: int
    week: int
    side: Side
    tier: Tier
    edge_points: float
    generated_at: datetime
    source: str
