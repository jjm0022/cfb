"""Parser for the text block copied off the CBS pick sheet.

A partially-ingested week is worse than a failed one, because it looks like
success. Anything not parsed is reported in `skipped`; anything parsed but
unresolvable raises.
"""

from __future__ import annotations

import re
from datetime import datetime

from loguru import logger
from pydantic import BaseModel

from pickem.models import Game, LeagueLine, Sport
from pickem.resolve.matchup import CanonicalMatchup, resolve_matchup
from pickem.resolve.resolver import TeamResolver

# "<away> [spread] at <home> [spread]" — the number may sit on either team.
_NUM = r"(?:[+-]?\d+(?:\.\d+)?|PK|EVEN)"
_GAME_RE = re.compile(
    rf"^\s*(?P<away>.+?)\s*(?P<away_num>{_NUM})?\s+at\s+(?P<home>.+?)\s*(?P<home_num>{_NUM})?\s*$",
    re.IGNORECASE,
)


class CbsParseError(ValueError):
    """The block contained no parsable games at all."""


class ParsedCbsGame(BaseModel):
    game: Game
    league_line: LeagueLine


class ParseResult(BaseModel):
    games: list[ParsedCbsGame]
    skipped: list[str]


def _skip_row(skipped: list[str], row: str, *, guard: str, source: str) -> None:
    """Keep the returned skip list and its warning records in lockstep."""
    skipped.append(row)
    logger.bind(
        event="ingest_row_skipped",
        source=source,
        guard=guard,
        reason=row,
    ).warning(f"CBS row skipped ({guard}): {row}")


def _log_parse_summary(
    *,
    source: str,
    sport: Sport,
    season: int,
    week: int,
    games: int,
    skipped: int,
) -> None:
    logger.bind(
        event="ingest_parsed",
        source=source,
        sport=sport.value,
        season=season,
        week=week,
        games=games,
        skipped=skipped,
    ).info(f"parsed {games} games")


def _parsed_game(
    *,
    matchup: CanonicalMatchup,
    spread_home: float,
    posted_at: datetime,
    kickoff_utc: datetime | None = None,
) -> ParsedCbsGame:
    return ParsedCbsGame(
        game=Game(
            game_id=matchup.game_id,
            sport=matchup.sport,
            season=matchup.season,
            week=matchup.week,
            kickoff_utc=kickoff_utc or posted_at,
            home_team_id=matchup.home_team_id,
            away_team_id=matchup.away_team_id,
        ),
        league_line=LeagueLine(
            game_id=matchup.game_id,
            season=matchup.season,
            week=matchup.week,
            spread_home=spread_home,
            posted_at=posted_at,
        ),
    )


def _to_spread(token: str | None) -> float | None:
    if token is None:
        return None
    if token.upper() in {"PK", "EVEN"}:
        return 0.0
    return float(token)


def parse_cbs_block(
    text: str,
    *,
    resolver: TeamResolver,
    sport: Sport,
    season: int,
    week: int,
    posted_at: datetime,
) -> ParseResult:
    games: list[ParsedCbsGame] = []
    skipped: list[str] = []

    for raw in text.splitlines():
        if not raw.strip():
            continue
        match = _GAME_RE.match(raw)
        if match is None:
            _skip_row(skipped, raw, guard="line_format", source="cbs_text")
            continue

        away_num = _to_spread(match.group("away_num"))
        home_num = _to_spread(match.group("home_num"))
        if away_num is None and home_num is None:
            _skip_row(skipped, raw, guard="missing_spread", source="cbs_text")
            continue

        if away_num is not None and home_num is not None:
            _skip_row(skipped, raw, guard="ambiguous_spread", source="cbs_text")
            continue

        # Exactly one side carries the number. If the away team does, flip its
        # sign to express the same line from the home team's perspective.
        spread_home = home_num if home_num is not None else -away_num

        matchup = resolve_matchup(
            resolver=resolver,
            sport=sport,
            season=season,
            week=week,
            away_name=match.group("away"),
            home_name=match.group("home"),
        )

        games.append(
            _parsed_game(
                matchup=matchup,
                spread_home=spread_home,
                posted_at=posted_at,
            )
        )

    if not games:
        raise CbsParseError("no games parsed from the pasted block")

    _log_parse_summary(
        source="cbs_text",
        sport=sport,
        season=season,
        week=week,
        games=len(games),
        skipped=len(skipped),
    )
    return ParseResult(games=games, skipped=skipped)
