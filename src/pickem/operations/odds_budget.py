"""Pick'em's share of the SportsGameOdds plan.

The free plan bills one object per game returned, 2,500 per rolling 30 days for
the whole account, and props pulls on the same account. Pick'em counts its own
pulls in `odds_pulls` (the account's counter includes props) and keeps to its
share. The 2-hour and 1-hour kickoff polls always run, because they set the
picks; at worst pick'em overshoots by part of their ~250 games a month.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger

from pickem.models import OddsPull, PollTrigger, Sport
from pickem.store.db import Store

GAMES_PER_30_DAYS = 800
PROTECTED_OFFSETS_HOURS = (2.0, 1.0)
WINDOW = timedelta(days=30)


class OddsBudgetSkipped(Exception):
    """A pull was not made, to keep pick'em inside its share.

    ``notify`` is true when no other skip was recorded in the 30 days before,
    so the owner hears about it once rather than at every skipped poll.
    """

    def __init__(self, message: str, *, notify: bool) -> None:
        super().__init__(message)
        self.notify = notify


def is_protected(trigger: PollTrigger, offset_hours: float | None) -> bool:
    return trigger is PollTrigger.KICKOFF and offset_hours in PROTECTED_OFFSETS_HOURS


def check_budget(
    store: Store,
    *,
    now: datetime,
    sport: Sport,
    season: int,
    week: int,
    trigger: PollTrigger,
    offset_hours: float | None,
    games_requested: int,
) -> int:
    """Return the games used in the last 30 days, or record a skip and raise."""
    used = store.odds_games_used(now - WINDOW)
    if used + games_requested <= GAMES_PER_30_DAYS or is_protected(trigger, offset_hours):
        return used
    notify = store.odds_skips_since(now - WINDOW) == 0
    store.record_odds_pull(
        OddsPull(
            pulled_at=now,
            sport=sport,
            season=season,
            week=week,
            trigger=trigger,
            offset_hours=offset_hours,
            games_requested=games_requested,
            games_returned=0,
            skipped=True,
        )
    )
    message = (
        f"Skipped a {trigger.value} odds pull for {sport.value} week {week}: pick'em has "
        f"used {used} of its {GAMES_PER_30_DAYS} SportsGameOdds games in the last 30 days "
        f"and this pull needed {games_requested}. The 2-hour and 1-hour pulls before "
        "each kickoff still run."
    )
    logger.bind(
        event="odds_budget_skipped",
        sport=sport.value,
        season=season,
        week=week,
        trigger=trigger.value,
        offset_hours=offset_hours,
        games_requested=games_requested,
        used_30d=used,
        share=GAMES_PER_30_DAYS,
        notify=notify,
    ).warning(message)
    raise OddsBudgetSkipped(message, notify=notify)
