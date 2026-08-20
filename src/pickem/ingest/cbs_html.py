"""Parser for a saved CBS pick'em page.

The page is a React app, but it server-renders its GraphQL result into an
Apollo rehydration blob, so the spreads are available as structured data rather
than scraped text. This reads that blob.

Keyed on JSON field names, never on CSS classes: the page's classes are hashed
at build time (`mui-1m0pb6d`) and change on every CBS deploy, while
`homeTeamSpread` is the shape their own client consumes.

Returns the same `ParseResult` as the text parser, so nothing downstream needs
to know which path produced a week.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pickem.ingest.cbs import CbsParseError, ParsedCbsGame, ParseResult, _parsed_game
from pickem.models import Sport
from pickem.resolve.matchup import resolve_matchup
from pickem.resolve.resolver import TeamResolver

_MARKER = "ApolloSSRDataTransport"
_PUSH = ".push("
# CBS emits the JavaScript literal `undefined`, which is not valid JSON.
_UNDEFINED = "undefined"


def _scan_object(text: str, start: int) -> tuple[str, int]:
    """Extract the JSON object beginning at ``start``, ending at its own brace.

    A regex cannot do this: the payload nests arbitrarily and contains braces
    inside string literals. Bare `undefined` is rewritten to null, but only
    outside strings — a blind replace would corrupt any team name containing
    the word.
    """
    out: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    index = start

    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
        elif text.startswith(_UNDEFINED, index):
            out.append("null")
            index += len(_UNDEFINED)
            continue
        else:
            out.append(char)
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return "".join(out), index + 1
        index += 1

    raise CbsParseError("an Apollo payload was opened but never closed")


def _payloads(text: str) -> list[Any]:
    blobs: list[Any] = []
    position = 0
    while (found := text.find(_MARKER, position)) != -1:
        push = text.find(_PUSH, found)
        opening = text.find("{", push)
        if push == -1 or opening == -1:
            break
        body, end = _scan_object(text, opening)
        blobs.append(json.loads(body))
        position = end
    return blobs


def _events(node: Any, found: list[dict]) -> None:
    """Collect every event-shaped object, wherever it sits in the response.

    Walking for shape rather than following a fixed path, because the query
    envelope around the events is CBS's business and changes more often than
    the event itself.
    """
    if isinstance(node, dict):
        if "homeTeamSpread" in node and "homeTeam" in node and "awayTeam" in node:
            found.append(node)
        for value in node.values():
            _events(value, found)
    elif isinstance(node, list):
        for value in node:
            _events(value, found)


def _team_name(team: Any) -> str | None:
    if not isinstance(team, dict):
        return None
    for key in ("mediumName", "shortName", "abbrev"):
        name = team.get(key)
        if isinstance(name, str) and name.strip():
            return name
    return None


def parse_cbs_html(
    text: str,
    *,
    resolver: TeamResolver,
    sport: Sport,
    season: int,
    week: int,
    posted_at: datetime,
) -> ParseResult:
    """Read a saved CBS pick'em page into frozen league lines."""
    events: list[dict] = []
    for blob in _payloads(text):
        _events(blob, events)

    if not events:
        raise CbsParseError(
            "no CBS event payload found — the page may have been saved before it "
            "finished loading, or CBS may have stopped server-rendering it"
        )

    games: list[ParsedCbsGame] = []
    skipped: list[str] = []
    seen: set[str] = set()

    for event in events:
        # The same events are emitted in more than one blob. Deduplicated on
        # CBS's own id so a repeated payload cannot inflate the reported count.
        event_id = event.get("id")
        if isinstance(event_id, str):
            if event_id in seen:
                continue
            seen.add(event_id)

        away_name = _team_name(event.get("awayTeam"))
        home_name = _team_name(event.get("homeTeam"))
        if away_name is None or home_name is None:
            skipped.append(f"{event_id}: event carries no usable team names")
            continue

        spread = event.get("homeTeamSpread")
        if not isinstance(spread, int | float) or isinstance(spread, bool):
            # Never fabricated. A game CBS has not priced is reported, and the
            # caller decides whether a week missing a line can be submitted.
            skipped.append(f"{away_name} at {home_name}: no spread posted")
            continue

        # `homeTeamSpread` is already home-perspective favourite-negative,
        # which is this codebase's convention. No flip. Pinned by a test.
        matchup = resolve_matchup(
            resolver=resolver,
            sport=sport,
            season=season,
            week=week,
            away_name=away_name,
            home_name=home_name,
        )
        kickoff_utc = None
        starts_at = event.get("startsAt")
        if isinstance(starts_at, int | float) and not isinstance(starts_at, bool):
            kickoff_utc = datetime.fromtimestamp(starts_at / 1000, tz=UTC)

        games.append(
            _parsed_game(
                matchup=matchup,
                spread_home=float(spread),
                posted_at=posted_at,
                kickoff_utc=kickoff_utc,
            )
        )

    if not games:
        raise CbsParseError("the CBS payload was found but carried no priced games")

    return ParseResult(games=games, skipped=skipped)
