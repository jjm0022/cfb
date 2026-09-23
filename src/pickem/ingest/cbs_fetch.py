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

import asyncio
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from loguru import logger

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_html import _payloads
from pickem.ingest.cbs_results import parse_cbs_results_html

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


PAGE_TIMEOUT_SECONDS = 60
# Raw server HTML, as a hand-saved page holds it, rather than the hydrated DOM.
_RAW_HTML = "fetch(location.href, {credentials: 'include'}).then(r => r.text())"


@dataclass(frozen=True)
class FetchedPage:
    final_url: str
    html: str


class PageSession(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


@dataclass(frozen=True)
class SavedPage:
    path: Path
    pool_week: int
    period_id: str
    size: int


async def _fetch_logged_in(session: PageSession, url: str) -> FetchedPage:
    """Fetch once more on a Chrome or network error; never on a CBS answer."""
    for attempt in (1, 2):
        try:
            page = await session.fetch(url)
            break
        except CbsFetchError:
            raise
        except Exception as exc:
            if attempt == 2:
                raise
            logger.bind(
                event="cbs_fetch_retry", url=url, error_type=type(exc).__name__
            ).warning(f"CBS fetch of {url} failed ({exc}); retrying once")
    require_logged_in(page.final_url, page.html)
    return page


def _refuse_overwrite(out: Path, force: bool) -> None:
    if out.exists() and not force:
        raise FileExistsError(f"{out} already exists; pass --force to replace it")


def _save(out: Path, html: str, pool_week: int, period_id: str) -> SavedPage:
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    temp.write_text(html, encoding="utf-8")
    os.replace(temp, out)
    saved = SavedPage(out, pool_week, period_id, out.stat().st_size)
    logger.bind(
        event="cbs_fetch_saved",
        path=str(out),
        bytes=saved.size,
        pool_week=pool_week,
        period_id=period_id,
    ).info(f"saved CBS pool week {pool_week} to {out}")
    return saved


async def fetch_board(
    session: PageSession, *, pool_url: str, pool_week: int, out: Path, force: bool = False
) -> SavedPage:
    """Save the board for ``pool_week``; CBS must be showing that week."""
    _refuse_overwrite(out, force)
    logger.bind(event="cbs_fetch_started", page="board", pool_week=pool_week).info(
        f"fetching the CBS board for pool week {pool_week}"
    )
    page = await _fetch_logged_in(session, pool_url)
    require_week(page.html, pool_week)
    return _save(out, page.html, pool_week, pool_periods(page.html)[pool_week])


async def fetch_standings(
    session: PageSession, *, pool_url: str, pool_week: int, out: Path, force: bool = False
) -> SavedPage:
    """Save the Weekly Standings for ``pool_week`` once every game is final.

    The standings parser runs before the write so a week with a late game is
    not saved, and the next scheduled run fetches it again.
    """
    _refuse_overwrite(out, force)
    logger.bind(event="cbs_fetch_started", page="standings", pool_week=pool_week).info(
        f"fetching the CBS standings for pool week {pool_week}"
    )
    index = await _fetch_logged_in(session, f"{pool_url}/standings/weekly")
    periods = pool_periods(index.html)
    if pool_week not in periods:
        raise CbsWeekNotReady(
            f"CBS has not posted pool week {pool_week} yet (it lists weeks {sorted(periods)})"
        )
    url = f"{pool_url}/standings/weekly?poolPeriodId={quote(periods[pool_week], safe='')}"
    page = await _fetch_logged_in(session, url)
    require_week(page.html, pool_week)
    parse_cbs_results_html(page.html)
    return _save(out, page.html, pool_week, periods[pool_week])


async def fetch_current_week(session: PageSession, *, pool_url: str) -> int:
    """The pool week CBS currently marks current."""
    page = await _fetch_logged_in(session, pool_url)
    return current_pool_week(page.html)


class ChromeSession:
    """Headless Chrome on the dedicated, logged-in CBS profile.

    The only code that touches Chrome. `--password-store=basic` keeps the
    profile's cookies readable without an unlocked GNOME keyring, so the
    scheduled job works while nobody is logged in to the desktop.
    """

    def __init__(
        self, *, profile: Path, chrome: Path, timeout: float = PAGE_TIMEOUT_SECONDS
    ) -> None:
        self._profile = profile
        self._chrome = chrome
        self._timeout = timeout
        self._browser: Any = None

    async def __aenter__(self) -> ChromeSession:
        if profile_in_use(self._profile):
            raise CbsProfileBusy(
                f"the CBS Chrome profile {self._profile} is open in another Chrome; "
                f"close that window and retry"
            )
        if not self._profile.is_dir():
            raise CbsSessionExpired(
                f"there is no CBS Chrome profile at {self._profile}; {LOGIN_HINT}"
            )
        import zendriver  # here, so commands that never fetch never load Chrome tooling

        self._browser = await zendriver.start(
            headless=True,
            user_data_dir=str(self._profile),
            browser_executable_path=str(self._chrome),
            browser_args=["--password-store=basic"],
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._browser is not None:
            browser, self._browser = self._browser, None
            await browser.stop()

    async def fetch(self, url: str) -> FetchedPage:
        return await asyncio.wait_for(self._fetch(url), self._timeout)

    async def _fetch(self, url: str) -> FetchedPage:
        tab = await self._browser.get(url)
        await tab.wait_for_ready_state("complete", timeout=int(self._timeout))
        final_url = await tab.evaluate("location.href")
        html = await tab.evaluate(_RAW_HTML, await_promise=True)
        if not isinstance(final_url, str) or not isinstance(html, str):
            raise RuntimeError(f"Chrome returned no page text for {url}")
        return FetchedPage(final_url, html)


def open_session(*, profile: Path, chrome: Path) -> ChromeSession:
    """The session the CLI uses; tests replace this name with a stub."""
    return ChromeSession(profile=profile, chrome=chrome)
