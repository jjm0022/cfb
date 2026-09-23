"""Checks against the real board pages saved by hand on the NAS.

Only board pages: a standings page saved by hand after switching weeks in the
browser still names the current week in its payload, so the week check is
only valid on pages this project fetches itself.
"""

import pytest

from pickem.config import DEFAULT_WEEKS_DIR
from pickem.ingest.cbs_fetch import (
    current_pool_week,
    pool_periods,
    require_logged_in,
    require_week,
)

PAGES = DEFAULT_WEEKS_DIR

pytestmark = pytest.mark.skipif(
    not (PAGES / "week4.html").exists(), reason="real CBS pages are local-only"
)


@pytest.mark.parametrize("week", [3, 4])
def test_real_board_shows_its_own_week(week):
    html = (PAGES / f"week{week}.html").read_text(encoding="utf-8")

    require_logged_in("https://picks.cbssports.com/football/pickem/pools/x", html)
    require_week(html, week)


def test_real_week4_board_lists_weeks_one_to_four_and_marks_four_current():
    html = (PAGES / "week4.html").read_text(encoding="utf-8")

    assert set(pool_periods(html)) >= {1, 2, 3, 4}
    assert current_pool_week(html) == 4
