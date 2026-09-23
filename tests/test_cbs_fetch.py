import asyncio
import os
import socket

import pytest
from cbs_fetch_helpers import (
    JOIN_PAGE,
    POOL,
    StubSession,
    period_id,
    pool_page,
    standings_page,
)

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_fetch import (
    CbsProfileBusy,
    CbsSessionExpired,
    CbsWeekNotReady,
    ChromeSession,
    FetchedPage,
    current_pool_week,
    fetch_board,
    fetch_current_week,
    fetch_standings,
    pool_periods,
    profile_in_use,
    require_logged_in,
    require_week,
    shown_pool_week,
)


def test_pool_periods_maps_each_pool_week_to_its_cbs_id():
    assert pool_periods(pool_page(weeks=3, shown=3, current=3)) == {
        1: period_id(1), 2: period_id(2), 3: period_id(3),
    }


def test_pool_periods_fails_closed_without_a_period_list():
    with pytest.raises(CbsParseError, match="period list"):
        pool_periods(JOIN_PAGE)


def test_pool_periods_rejects_two_ids_for_one_week():
    with pytest.raises(CbsParseError, match="two ids for pool week 1"):
        pool_periods(pool_page(weeks=2, shown=2, current=2, conflicting=True))


def test_current_pool_week_is_the_period_cbs_marks_current():
    assert current_pool_week(pool_page(weeks=4, shown=3, current=4)) == 4


def test_shown_pool_week_is_the_period_the_page_displays():
    assert shown_pool_week(pool_page(weeks=4, shown=3, current=4)) == 3


def test_a_join_redirect_means_the_session_expired():
    with pytest.raises(CbsSessionExpired, match="cbs-login.sh"):
        require_logged_in(f"{POOL}/join?device=desktop", pool_page(weeks=1, shown=1, current=1))


def test_a_page_without_pool_data_means_the_session_expired():
    with pytest.raises(CbsSessionExpired):
        require_logged_in(POOL, JOIN_PAGE)


def test_a_logged_in_page_passes():
    require_logged_in(POOL, pool_page(weeks=1, shown=1, current=1))


def test_require_week_accepts_the_week_on_screen():
    require_week(pool_page(weeks=4, shown=4, current=4), 4)


def test_require_week_rejects_a_week_cbs_has_not_posted():
    with pytest.raises(CbsWeekNotReady, match="has not posted pool week 5"):
        require_week(pool_page(weeks=4, shown=4, current=4), 5)


def test_require_week_rejects_a_page_showing_another_week():
    with pytest.raises(CbsWeekNotReady, match="showing pool week 4, not 3"):
        require_week(pool_page(weeks=4, shown=4, current=4), 3)


def test_profile_without_a_lock_is_free(tmp_path):
    assert profile_in_use(tmp_path) is False


def test_profile_locked_by_a_live_local_chrome_is_in_use(tmp_path):
    os.symlink(f"{socket.gethostname()}-{os.getpid()}", tmp_path / "SingletonLock")
    assert profile_in_use(tmp_path) is True


def test_a_stale_lock_from_a_dead_process_is_free(tmp_path):
    os.symlink(f"{socket.gethostname()}-999999999", tmp_path / "SingletonLock")
    assert profile_in_use(tmp_path) is False


def test_a_lock_held_from_another_host_counts_as_in_use(tmp_path):
    os.symlink("some-other-host-123", tmp_path / "SingletonLock")
    assert profile_in_use(tmp_path) is True


STANDINGS = f"{POOL}/standings/weekly"


def _standings_url(week: int) -> str:
    return f"{STANDINGS}?poolPeriodId=period-{week}%3D"


def test_board_is_saved_when_cbs_shows_the_requested_week(tmp_path):
    session = StubSession({POOL: pool_page(weeks=4, shown=4, current=4)})
    out = tmp_path / "week4.html"

    saved = asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=out))

    assert out.read_text() == pool_page(weeks=4, shown=4, current=4)
    assert (saved.path, saved.pool_week, saved.period_id) == (out, 4, period_id(4))
    assert saved.size == out.stat().st_size


def test_board_for_a_week_cbs_is_not_showing_writes_nothing(tmp_path):
    session = StubSession({POOL: pool_page(weeks=3, shown=3, current=3)})
    out = tmp_path / "week4.html"

    with pytest.raises(CbsWeekNotReady):
        asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=out))

    assert list(tmp_path.iterdir()) == []


def test_logged_out_board_writes_nothing(tmp_path):
    session = StubSession({POOL: FetchedPage(f"{POOL}/join?device=desktop", JOIN_PAGE)})
    out = tmp_path / "week4.html"

    with pytest.raises(CbsSessionExpired):
        asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=out))

    assert list(tmp_path.iterdir()) == []


def test_existing_page_is_not_replaced_without_force(tmp_path):
    out = tmp_path / "week4.html"
    out.write_text("saved by hand")
    session = StubSession({POOL: pool_page(weeks=4, shown=4, current=4)})

    with pytest.raises(FileExistsError, match="--force"):
        asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=out))

    assert out.read_text() == "saved by hand"
    assert session.requested == []


def test_force_replaces_an_existing_page(tmp_path):
    out = tmp_path / "week4.html"
    out.write_text("saved by hand")
    session = StubSession({POOL: pool_page(weeks=4, shown=4, current=4)})

    asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=out, force=True))

    assert out.read_text() == pool_page(weeks=4, shown=4, current=4)


def test_a_transient_error_is_retried_once(tmp_path):
    session = StubSession({POOL: [TimeoutError("slow"), pool_page(weeks=4, shown=4, current=4)]})

    asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=tmp_path / "w.html"))

    assert session.requested == [POOL, POOL]


def test_a_second_transient_error_is_raised(tmp_path):
    session = StubSession({POOL: [TimeoutError("slow"), TimeoutError("still slow")]})

    with pytest.raises(TimeoutError, match="still slow"):
        asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=tmp_path / "w.html"))

    assert list(tmp_path.iterdir()) == []


def test_an_expired_session_is_not_retried(tmp_path):
    session = StubSession({POOL: FetchedPage(f"{POOL}/join", JOIN_PAGE)})

    with pytest.raises(CbsSessionExpired):
        asyncio.run(fetch_board(session, pool_url=POOL, pool_week=4, out=tmp_path / "w.html"))

    assert session.requested == [POOL]


def test_standings_are_fetched_for_the_requested_period(tmp_path):
    session = StubSession({
        STANDINGS: standings_page(weeks=4, shown=4, current=4),
        _standings_url(3): standings_page(weeks=4, shown=3, current=4),
    })
    out = tmp_path / "week3.html"

    saved = asyncio.run(fetch_standings(session, pool_url=POOL, pool_week=3, out=out))

    assert session.requested == [STANDINGS, _standings_url(3)]
    assert out.read_text() == standings_page(weeks=4, shown=3, current=4)
    assert saved.period_id == period_id(3)


def test_standings_with_an_unfinished_game_writes_nothing(tmp_path):
    session = StubSession({
        STANDINGS: standings_page(weeks=4, shown=4, current=4),
        _standings_url(3): standings_page(weeks=4, shown=3, current=4, final=False),
    })

    with pytest.raises(CbsParseError, match="not final"):
        asyncio.run(fetch_standings(session, pool_url=POOL, pool_week=3, out=tmp_path / "w.html"))

    assert list(tmp_path.iterdir()) == []


def test_standings_for_an_unposted_week_are_not_requested(tmp_path):
    session = StubSession({STANDINGS: standings_page(weeks=4, shown=4, current=4)})

    with pytest.raises(CbsWeekNotReady, match="has not posted pool week 5"):
        asyncio.run(fetch_standings(session, pool_url=POOL, pool_week=5, out=tmp_path / "w.html"))

    assert session.requested == [STANDINGS]


def test_current_week_is_read_from_the_pool_page():
    session = StubSession({POOL: pool_page(weeks=5, shown=5, current=5)})

    assert asyncio.run(fetch_current_week(session, pool_url=POOL)) == 5


def test_chrome_session_refuses_a_profile_in_use(tmp_path):
    os.symlink(f"{socket.gethostname()}-{os.getpid()}", tmp_path / "SingletonLock")
    session = ChromeSession(profile=tmp_path, chrome=tmp_path / "chrome")

    async def enter() -> None:
        async with session:
            pass

    with pytest.raises(CbsProfileBusy, match="close that window"):
        asyncio.run(enter())


def test_chrome_session_without_a_profile_asks_for_a_login(tmp_path):
    session = ChromeSession(profile=tmp_path / "missing", chrome=tmp_path / "chrome")

    async def enter() -> None:
        async with session:
            pass

    with pytest.raises(CbsSessionExpired, match="cbs-login.sh"):
        asyncio.run(enter())
