"""Synthetic CBS board pages that carry the owner's entry.

Field names match the logged-in board fetched on 2026-09-24: each event has a
`cbsEventId` and teams with a `cbsTeamId`; the owner's entry is the one marked
`isMine`, and each of its `entryPicks` names the game (`cbsSlotId`) and the
team picked (`cbsItemId`).
"""

from __future__ import annotations

from cbs_fetch_helpers import _blob, pool_page

ATL, GB = 405, 414
WAKE, LOU = 668, 669
# 2026-09-25 00:15 UTC, Thursday 8:15 PM Eastern.
KICKOFF_MS = 1790295300000


def event(
    cbs_id: int, *, sport: str, away: str, home: str, away_id: int, home_id: int
) -> dict:
    def team(name: str, team_id: int) -> dict:
        return {"__typename": "Team", "cbsTeamId": team_id, "mediumName": name}

    return {
        "__typename": "Event",
        "id": f"event-{cbs_id}=",
        "cbsEventId": cbs_id,
        "sportType": sport,
        "startsAt": KICKOFF_MS,
        "homeTeamSpread": -6.5,
        "awayTeam": team(away, away_id),
        "homeTeam": team(home, home_id),
    }


ATL_GB = event(50029231, sport="NFL", away="Atlanta", home="Green Bay", away_id=ATL, home_id=GB)
WAKE_LOU = event(
    50027863, sport="NCAAF", away="Wake Forest", home="Louisville", away_id=WAKE, home_id=LOU
)


def entry(picks: dict[int, int], *, entry_id: str = "ENTRY", mine: bool = True) -> dict:
    """An entry whose picks map CBS event id -> CBS team id picked."""
    return {
        "__typename": "FootballPickemEntry",
        "id": entry_id,
        "isMine": mine,
        "entryPicks": [
            {"__typename": "EntryPick", "cbsSlotId": slot, "cbsItemId": item}
            for slot, item in picks.items()
        ],
    }


def entry_page(
    *entries: dict, events: tuple[dict, ...] = (ATL_GB, WAKE_LOU), pool_week: int = 4
) -> str:
    board = _blob({"events": list(events)})
    owners = "".join(_blob({"commonEntry": one}) for one in entries)
    page = pool_page(weeks=pool_week, shown=pool_week, current=pool_week)
    return page.replace("</body>", board + owners + "</body>")
