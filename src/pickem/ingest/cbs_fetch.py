"""Fetch the CBS pool pages the week's workflow reads: the board and the
Weekly Standings.

Pages are saved exactly where, and in the form, the owner used to save them by
hand, so `ingest-cbs --html` and `import-results` read them unchanged. Chrome
is confined to `ChromeSession`; everything else here is plain functions over
page text.

Which week a page shows is read from CBS's own period list in the Apollo
payload (`commonPool.poolPeriods`), never computed. A period id is base32 of
`PoolPeriod:<n>`, but `<n>` is a CBS database id, not a week.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_html import _payloads

LOGIN_HINT = (
    "run scripts/cbs-login.sh on the desktop, sign in to CBS in the window it "
    "opens, then close that window"
)


class CbsFetchError(RuntimeError):
    """A CBS page could not be fetched as a usable, saved page."""


class CbsSessionExpired(CbsFetchError):
    """CBS served the page to a logged-out visitor."""


class CbsWeekNotReady(CbsFetchError):
    """CBS does not (yet) show the requested pool week."""


class CbsProfileBusy(CbsFetchError):
    """Another Chrome holds the dedicated profile."""


def _pools(html: str) -> list[dict[str, Any]]:
    """Every object carrying a `poolPeriods` list, wherever CBS nests it."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("poolPeriods"), list):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for blob in _payloads(html):
        walk(blob)
    return found


def _periods(html: str) -> list[dict[str, Any]]:
    return [period for pool in _pools(html) for period in pool["poolPeriods"]]


def pool_periods(html: str) -> dict[int, str]:
    """Pool week → CBS `poolPeriodId`, from the page's own period list."""
    periods: dict[int, str] = {}
    for period in _periods(html):
        week, period_id = period.get("order"), period.get("id")
        if not isinstance(week, int) or not isinstance(period_id, str) or not period_id:
            raise CbsParseError(f"malformed CBS pool period: {period!r}")
        if periods.setdefault(week, period_id) != period_id:
            raise CbsParseError(f"CBS lists two ids for pool week {week}")
    if not periods:
        raise CbsParseError("no CBS pool period list found on the page")
    return periods


def current_pool_week(html: str) -> int:
    """The pool week CBS marks current."""
    pool_periods(html)  # validates the list before trusting any flag in it
    current = {
        period["order"]
        for period in _periods(html)
        if period.get("isCurrent") is True
    }
    if len(current) != 1:
        raise CbsParseError(f"expected one current CBS pool week, found {sorted(current)}")
    return current.pop()


def shown_pool_week(html: str) -> int:
    """The pool week the page is displaying.

    Only meaningful on raw server HTML fetched for a specific period: a page
    saved by hand after switching weeks in the browser still names the week
    the server first rendered.
    """
    by_id = {period_id: week for week, period_id in pool_periods(html).items()}
    shown = {
        pool["poolPeriod"].get("id")
        for pool in _pools(html)
        if isinstance(pool.get("poolPeriod"), dict)
    }
    if len(shown) != 1:
        raise CbsParseError(f"expected one displayed CBS pool period, found {sorted(shown)}")
    period_id = shown.pop()
    if period_id not in by_id:
        raise CbsParseError(
            f"the displayed CBS period {period_id!r} is not in the period list"
        )
    return by_id[period_id]


def require_logged_in(final_url: str, html: str) -> None:
    """Refuse a page CBS served to a logged-out visitor."""
    if urlsplit(final_url).path.rstrip("/").endswith("/join"):
        raise CbsSessionExpired(
            f"CBS redirected to its join page: the login has expired; {LOGIN_HINT}"
        )
    if not _pools(html):
        raise CbsSessionExpired(
            f"CBS returned the page without the pool's data, so the login has "
            f"most likely expired; {LOGIN_HINT}"
        )


def require_week(html: str, pool_week: int) -> None:
    """Refuse a page that does not show ``pool_week``."""
    periods = pool_periods(html)
    if pool_week not in periods:
        raise CbsWeekNotReady(
            f"CBS has not posted pool week {pool_week} yet (it lists weeks {sorted(periods)})"
        )
    shown = shown_pool_week(html)
    if shown != pool_week:
        raise CbsWeekNotReady(f"CBS is showing pool week {shown}, not {pool_week}")


def profile_in_use(profile: Path) -> bool:
    """Whether another Chrome holds the profile.

    Chrome marks a profile with a `SingletonLock` symlink to `<host>-<pid>`. A
    lock from a process that is gone is stale and Chrome takes it over; a lock
    from another host cannot be checked, so it counts as held.
    """
    lock = profile / "SingletonLock"
    if not lock.is_symlink():
        return False
    host, _, pid = os.readlink(lock).rpartition("-")
    if host != socket.gethostname() or not pid.isdigit():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
