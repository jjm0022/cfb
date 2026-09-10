"""Turn a slate's kickoffs into the instants the market should be polled at.

Pure: no I/O, no clock of its own, no scheduler. The caller supplies the
kickoffs and ``now``; what comes back is the schedule it should register.

A fixed wall-clock refresh cannot be close to kickoff for a slate that runs
twelve hours, so polls are anchored to each kickoff instead. Cost scales with
the number of distinct *instants*, not games: one poll returns every game in
the sport, so games sharing a kickoff share their polls, and an instant two
kickoff slots both ask for is charged once.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_HORIZON = timedelta(days=10)


@dataclass(frozen=True)
class PollInstant:
    """One scheduled market poll, and the kickoff that earned it."""

    at: datetime
    offset_hours: float
    kickoff_utc: datetime


def plan_polls(
    kickoffs: Iterable[datetime],
    *,
    offsets_hours: Sequence[float],
    now: datetime,
    horizon: timedelta = DEFAULT_HORIZON,
) -> tuple[PollInstant, ...]:
    """Return the future poll instants for ``kickoffs``, ordered by time.

    An instant already in the past is dropped rather than fired late: a bot
    restarted mid-week must not replay the polls it missed, because every one
    of them costs a credit and none of them can change a pick that is already
    locked. Kickoffs past ``horizon`` are ignored so an early-ingested slate
    does not register jobs a week and a half out.
    """
    deadline = now + horizon
    # Most urgent first, so the dedupe below keeps the closest-to-kickoff
    # reason for an instant that two kickoff slots both ask for.
    candidates = sorted(
        (
            PollInstant(
                at=kickoff - timedelta(hours=offset),
                offset_hours=offset,
                kickoff_utc=kickoff,
            )
            for kickoff in set(kickoffs)
            if kickoff <= deadline
            for offset in offsets_hours
        ),
        key=lambda instant: (instant.offset_hours, instant.kickoff_utc),
    )

    chosen: dict[datetime, PollInstant] = {}
    for instant in candidates:
        if instant.at > now:
            chosen.setdefault(instant.at, instant)
    return tuple(sorted(chosen.values(), key=lambda instant: instant.at))


__all__ = ["DEFAULT_HORIZON", "PollInstant", "plan_polls"]
