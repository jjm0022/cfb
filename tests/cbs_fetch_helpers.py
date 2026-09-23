"""Synthetic CBS pool pages, shaped like the real Apollo payload.

Built in code rather than saved as fixtures so each test states the one fact it
depends on — how many weeks CBS lists, which one the page shows, which one is
current. The shape matches the logged-in pages captured on 2026-09-22.
"""

from __future__ import annotations

import json

POOL = "https://cbs.test/football/pickem/pools/POOL"

_SCRIPT = '<script>(window[Symbol.for("ApolloSSRDataTransport")] ??= []).push({})</script>'


def period_id(week: int) -> str:
    return f"period-{week}="


def _blob(data: dict) -> str:
    return _SCRIPT.replace("{}", json.dumps({"rehydrate": {"_R_test_": {"data": data}}}))


def pool_page(weeks: int, shown: int, current: int, *, conflicting: bool = False) -> str:
    """A logged-in pool page listing weeks 1..``weeks``, showing ``shown``."""
    periods = [
        {
            "__typename": "PoolPeriod",
            "id": period_id(week),
            "description": f"Week {week}",
            "isCurrent": week == current,
            "order": week,
        }
        for week in range(1, weeks + 1)
    ]
    user = _blob({"currentUser": {"__typename": "User", "id": "user="}})
    pool = _blob({
        "commonPool": {
            "__typename": "FootballPickemManagerPool",
            "id": "POOL",
            "poolPeriod": {"__typename": "PoolPeriod", "id": period_id(shown)},
            "poolPeriods": periods,
        }
    })
    extra = ""
    if conflicting:
        clash = [dict(periods[0], id="other-id=")]
        extra = _blob({"commonPool": {"poolPeriods": clash}})
    return f"<html><body>{user}{pool}{extra}</body></html>"


# What the pool URL serves after CBS redirects a logged-out visitor to /join.
JOIN_PAGE = "<html><body>" + _blob({"currentUser": None}) + "</body></html>"
