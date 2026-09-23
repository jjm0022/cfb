"""Synthetic CBS pool pages, shaped like the real Apollo payload.

Built in code rather than saved as fixtures so each test states the one fact it
depends on — how many weeks CBS lists, which one the page shows, which one is
current. The shape matches the logged-in pages captured on 2026-09-22.
"""

from __future__ import annotations

import json
from pathlib import Path

from pickem.ingest.cbs_fetch import FetchedPage

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

RESULTS_FIXTURE = Path("tests/fixtures/cbs_results_page.html")


def standings_page(weeks: int, shown: int, current: int, *, final: bool = True) -> str:
    """The real trimmed standings table plus a period blob for ``shown``."""
    table = RESULTS_FIXTURE.read_text(encoding="utf-8")
    if not final:
        table = table.replace(">FINAL</span>", ">Thu 8:15 PM</span>", 1)
    return table + pool_page(weeks=weeks, shown=shown, current=current)


class StubSession:
    """Serves canned pages by URL; an Exception value is raised instead.

    A list value is consumed one entry per fetch, to script a failure followed
    by a success.
    """

    def __init__(self, pages: dict[str, object]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.requested.append(url)
        page = self.pages[url]
        if isinstance(page, list):
            page = page.pop(0)
        if isinstance(page, Exception):
            raise page
        return page if isinstance(page, FetchedPage) else FetchedPage(url, page)
