"""Where a pool week stands, for the scheduled week-start and results jobs.

Both jobs decide from the database rather than from CBS's notion of the
current week: CBS may not have advanced that week by the time a job runs, and
"the week before current" would then name a week already imported.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from pickem.models import Sport
from pickem.store.db import Store

# How long after the last kickoff a pool week counts as over. Monday night's
# game kicks off around 00:15 UTC and is final well before 04:15 UTC, hours
# ahead of Tuesday's 09:00 America/New_York results run.
FINISHED_AFTER = timedelta(hours=4)


class PoolWeekStatus(StrEnum):
    NEW = "new"
    PARTIAL = "partial"
    STARTED = "started"
    FINISHED = "finished"


def pool_week_status(store: Store, season: int, pool_week: int) -> PoolWeekStatus:
    """Whether a pool week's boards are stored, and whether it is imported."""
    if pool_week in store.pool_weeks(season):
        return PoolWeekStatus.FINISHED
    leagues = [(Sport.CFB, pool_week)]
    if pool_week > 1:
        leagues.append((Sport.NFL, pool_week - 1))
    stored = [store.league_line_count(sport, season, week) > 0 for sport, week in leagues]
    if all(stored):
        return PoolWeekStatus.STARTED
    return PoolWeekStatus.PARTIAL if any(stored) else PoolWeekStatus.NEW


def pending_results_week(
    last_kickoffs: Mapping[int, datetime], imported: Iterable[int], now: datetime
) -> int | None:
    """The earliest stored pool week that is over but not yet imported."""
    done = set(imported)
    over = [
        week
        for week, last in last_kickoffs.items()
        if week not in done and last + FINISHED_AFTER <= now
    ]
    return min(over, default=None)
