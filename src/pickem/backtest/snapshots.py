"""Turn a stored schedule into the exact archive requests to make.

Pure: no network, no database. That is what lets the credit budget be read off
a plan before a single credit is spent, and what makes the timing rules below
testable without buying a snapshot to check them.

The two proxies are timed differently on purpose:

* the FROZEN anchor imitates CBS, which freezes its number early in the week,
  so it is one snapshot per week at a fixed early-week instant. A true "opening
  line" would be whenever each book first posted — a different and less
  relevant moment.
* the SUBMISSION anchor imitates pressing submit, so it is a bounded-age,
  pre-kickoff batch for games whose kickoffs are close together. Kickoffs are
  staggered, so this is neither one request per week nor necessarily one per
  game: a single snapshot carries every game with posted odds.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from pickem.models import Game, Sport

# The Odds API archive begins here. An earlier request returns nothing.
ARCHIVE_START = datetime(2020, 6, 6, 10, 5, tzinfo=UTC)

# 10 per region per market, and we ask for one of each. Ten times a live call.
CREDITS_PER_REQUEST = 10

# Tuesday. Fixed in UTC rather than tracked against US Eastern so a re-run is
# byte-identical: ~09:00 ET in summer, ~10:00 ET in winter. Precision does not
# matter here; determinism does, because captured_at is part of the `lines`
# primary key and the table is append-only.
_ANCHOR_WEEKDAY = 1
_ANCHOR_HOUR = 14

# Far enough before kickoff to be a real snapshot, close enough to be the
# submission-time market. The archive answers with the closest snapshot at or
# earlier than the requested instant, so this can only ever resolve backwards.
#
# Fifteen minutes, not five, because the two sources disagree about when a game
# starts: measured against a real 2024-09-22 snapshot, the Odds API's
# commence_time ran from 5 minutes BEFORE to 2 minutes after nflverse's
# kickoff. A 5-minute lead would put the request on top of the real kickoff for
# the earliest of those, and the returned snapshot could then carry in-play
# odds into a proxy that is supposed to predate the game. The extra margin
# costs almost nothing — a line barely moves in the last ten minutes — and it
# buys back three times the worst disagreement observed.
_SUBMISSION_LEAD = timedelta(minutes=15)

# Padding on the kickoff window that guards every request. Wide enough to hold
# a whole week's slate, narrow enough to exclude the neighbouring weeks.
_WINDOW_LEAD = timedelta(hours=1)
_WINDOW_TRAIL = timedelta(hours=6)


class SnapshotKind(StrEnum):
    FROZEN = "frozen"
    SUBMISSION = "submission"


class SnapshotRequest(BaseModel):
    """One archive call, with both guards already computed."""

    kind: SnapshotKind
    at: datetime
    window: tuple[datetime, datetime]
    slate: frozenset[str]
    season: int
    week: int


def _frozen_anchor(first_kickoff: datetime) -> datetime:
    """The most recent anchor weekday, strictly before the first kickoff."""
    candidate = first_kickoff.replace(hour=_ANCHOR_HOUR, minute=0, second=0, microsecond=0)
    while candidate.weekday() != _ANCHOR_WEEKDAY or candidate >= first_kickoff:
        candidate -= timedelta(days=1)
    return candidate


def _submission_batches(
    games: Sequence[Game], max_submission_age: timedelta
) -> list[tuple[datetime, list[Game]]]:
    if max_submission_age < _SUBMISSION_LEAD:
        raise ValueError("max submission age must be at least the 15 minutes safety lead")
    remaining = sorted(games, key=lambda game: (game.kickoff_utc, game.game_id))
    batches: list[tuple[datetime, list[Game]]] = []
    while remaining:
        first = remaining[0].kickoff_utc
        request_at = first - _SUBMISSION_LEAD
        latest = request_at + max_submission_age
        batch = [game for game in remaining if game.kickoff_utc <= latest]
        batches.append((request_at, batch))
        selected = {game.game_id for game in batch}
        remaining = [game for game in remaining if game.game_id not in selected]
    return batches


def snapshot_request_id(sport: Sport, request: SnapshotRequest) -> str:
    payload = {
        "sport": sport.value,
        "season": request.season,
        "week": request.week,
        "kind": request.kind.value,
        "at": request.at.astimezone(UTC).isoformat(),
        "slate": sorted(request.slate),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def plan_snapshots(
    games: Sequence[Game], max_submission_age: timedelta = timedelta(minutes=15)
) -> list[SnapshotRequest]:
    """Every archive request needed to backfill both proxies for `games`.

    Unplayed games are dropped: the backtest cannot grade them, so paying to
    snapshot them is waste. A week is planned around the games that remain.
    """
    playable = [g for g in games if g.home_score is not None and g.away_score is not None]
    if not playable:
        return []

    earliest = min(g.kickoff_utc for g in playable)
    if earliest < ARCHIVE_START:
        raise ValueError(
            f"kickoff {earliest.isoformat()} precedes the archive start "
            f"{ARCHIVE_START.isoformat()}; no snapshot exists to fetch"
        )

    by_week: dict[tuple[int, int], list[Game]] = defaultdict(list)
    for game in playable:
        by_week[(game.season, game.week)].append(game)

    requests: list[SnapshotRequest] = []
    for (season, week), week_games in sorted(by_week.items()):
        kickoffs = [g.kickoff_utc for g in week_games]
        # One window per week, shared by every request in it: the guard decides
        # which week an event belongs to, not which slot.
        window = (min(kickoffs) - _WINDOW_LEAD, max(kickoffs) + _WINDOW_TRAIL)

        requests.append(
            SnapshotRequest(
                kind=SnapshotKind.FROZEN,
                at=_frozen_anchor(min(kickoffs)),
                window=window,
                slate=frozenset(g.game_id for g in week_games),
                season=season,
                week=week,
            )
        )

        for request_at, batch in _submission_batches(week_games, max_submission_age):
            requests.append(
                SnapshotRequest(
                    kind=SnapshotKind.SUBMISSION,
                    at=request_at,
                    window=window,
                    # Narrowed to this bounded-age batch, so a game can never be graded
                    # against a snapshot taken after it kicked off.
                    slate=frozenset(g.game_id for g in batch),
                    season=season,
                    week=week,
                )
            )
    return requests


def estimate_credits(requests: Sequence[SnapshotRequest]) -> int:
    """One region, one market — so a flat 10 credits per request."""
    return CREDITS_PER_REQUEST * len(requests)
