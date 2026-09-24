"""Read the owner's own entered picks from a CBS board page.

The board page embeds the viewer's entry beside the events: the entry marked
`isMine` lists `entryPicks`, each naming a game by `cbsSlotId` (the event's
`cbsEventId`) and the team picked by `cbsItemId` (that team's `cbsTeamId`).
An entry with nothing picked yet has `entryPicks: []`, so a page saved on
Tuesday, before picking, reads as every game unpicked.

Games are keyed by the same `game_id` the board ingest stores, derived the same
way, so a pick lines up with the model's recommendation for that game.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_html import _SPORT_TYPES, _events, _payloads, _team_name
from pickem.models import Side
from pickem.operations.results_import import ResultsImportError, league_week
from pickem.resolve.matchup import resolve_matchup
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

_LEAGUES = {code: sport for sport, code in _SPORT_TYPES.items()}


@dataclass(frozen=True)
class EntryBoard:
    """The owner's entry as CBS holds it right now."""

    # Every game on the page whose teams resolved; None means nothing is picked.
    picks: dict[str, Side | None]
    # "Away at Home" for page events that could not be tied to a stored game.
    unresolved: tuple[str, ...]


def _walk(node: Any, found: dict[Any, dict]) -> None:
    if isinstance(node, dict):
        if node.get("isMine") is True and "entryPicks" in node:
            # The page repeats the same entry in more than one blob.
            found.setdefault(node.get("id"), node)
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found)


def _my_entry(blobs: list[Any]) -> dict:
    found: dict[Any, dict] = {}
    for blob in blobs:
        _walk(blob, found)
    if not found:
        raise CbsParseError(
            "the CBS page carries no entry marked as yours — the login may belong to "
            "another account, or CBS changed the page"
        )
    if len(found) > 1:
        raise CbsParseError(
            f"the CBS page marks {len(found)} entries as yours ({sorted(map(str, found))}); "
            "expected one"
        )
    return next(iter(found.values()))


def _side(event: dict, item: Any) -> Side:
    if item == (event.get("homeTeam") or {}).get("cbsTeamId"):
        return Side.HOME
    if item == (event.get("awayTeam") or {}).get("cbsTeamId"):
        return Side.AWAY
    raise CbsParseError(
        f"your pick on CBS event {event.get('cbsEventId')} names team {item}, "
        "which is neither side of that game"
    )


def parse_entry_picks(
    html: str, *, resolver: TeamResolver, season: int, pool_week: int
) -> EntryBoard:
    """Return the owner's pick, or its absence, for every game on the page."""
    blobs = _payloads(html)
    found: list[dict] = []
    for blob in blobs:
        _events(blob, found)
    events: dict[int, dict] = {}
    for event in found:
        cbs_id = event.get("cbsEventId")
        if isinstance(cbs_id, int) and not isinstance(cbs_id, bool):
            events.setdefault(cbs_id, event)
    if not events:
        raise CbsParseError("no CBS event payload found on the board page")
    mine = _my_entry(blobs)

    game_ids: dict[int, str] = {}
    unresolved: list[str] = []
    for cbs_id, event in events.items():
        away = _team_name(event.get("awayTeam"))
        home = _team_name(event.get("homeTeam"))
        sport = _LEAGUES.get(event.get("sportType"))
        if away is None or home is None or sport is None:
            unresolved.append(f"{away} at {home}")
            continue
        try:
            matchup = resolve_matchup(
                resolver=resolver,
                sport=sport,
                season=season,
                week=league_week(sport, pool_week),
                away_name=away,
                home_name=home,
            )
        except (UnknownTeamError, ResultsImportError, ValueError):
            unresolved.append(f"{away} at {home}")
            continue
        game_ids[cbs_id] = matchup.game_id

    picks: dict[str, Side | None] = {game_id: None for game_id in game_ids.values()}
    for pick in mine.get("entryPicks") or []:
        slot = pick.get("cbsSlotId")
        event = events.get(slot)
        if event is None:
            raise CbsParseError(
                f"your pick on CBS event {slot} names a game that is not on the page"
            )
        side = _side(event, pick.get("cbsItemId"))
        if slot in game_ids:
            picks[game_ids[slot]] = side

    logger.bind(
        event="cbs_entry_parsed",
        games=len(picks),
        picked=sum(1 for side in picks.values() if side is not None),
        unresolved=len(unresolved),
    ).debug(f"read {len(picks)} games from the owner's CBS entry")
    return EntryBoard(picks=picks, unresolved=tuple(unresolved))


__all__ = ["EntryBoard", "parse_entry_picks"]
