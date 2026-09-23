import os
import socket

import pytest
from cbs_fetch_helpers import JOIN_PAGE, POOL, period_id, pool_page

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_fetch import (
    CbsSessionExpired,
    CbsWeekNotReady,
    current_pool_week,
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
