from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta

from pydantic import BaseModel

from pickem.backtest.snapshots import (
    SnapshotKind,
    SnapshotRequest,
    estimate_credits,
    plan_snapshots,
    snapshot_request_id,
)
from pickem.ingest.odds import CFB_KEY, NFL_KEY, OddsClient, QuotaExhausted
from pickem.models import FROZEN_SOURCE, SUBMISSION_SOURCE, Game, Sport
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


class ArchiveProgress(BaseModel):
    index: int
    total: int
    request: SnapshotRequest
    line_count: int
    skipped: list[str]


class ArchiveRunReport(BaseModel):
    # Retained for the existing CLI. The more explicit fields below distinguish
    # the whole plan from the subset that still needs to be bought.
    total_snapshots: int
    frozen_snapshots: int
    submission_snapshots: int
    weeks: int
    credits: int
    planned_snapshots: int
    completed_snapshots: int
    pending_snapshots: int
    pending_credits: int
    executed_snapshots: int
    remaining_credits: int | None


class NoHistoricalGames(ValueError):
    pass


class CreditLimitExceeded(ValueError):
    def __init__(self, credits: int, maximum: int) -> None:
        self.credits = credits
        self.maximum = maximum
        super().__init__(f"plan costs {credits} credits, above the {maximum}-credit ceiling")


class MixedSports(ValueError):
    def __init__(self, sports: set[Sport]) -> None:
        self.sports = sports
        names = ", ".join(sorted(sport.value for sport in sports))
        super().__init__(f"archive requests must contain one sport, got {names}")


class InsufficientCredits(ValueError):
    def __init__(self, remaining: int, pending_credits: int, reserve: int) -> None:
        self.remaining = remaining
        self.pending_credits = pending_credits
        self.reserve = reserve
        super().__init__(
            f"{remaining} credits remaining cannot cover {pending_credits} pending credits "
            f"plus the {reserve}-credit reserve"
        )


class PlannedCostChanged(ValueError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected {expected} pending credits, but plan now costs {actual}")


class ArchiveRunInterrupted(RuntimeError):
    def __init__(self, index: int, total: int, reason: str) -> None:
        self.index = index
        self.total = total
        super().__init__(f"stopped at snapshot {index}/{total}: {reason}")


ProgressCallback = Callable[[ArchiveProgress], None]
ClientFactory = Callable[[], OddsClient]


def sport_key(sport: Sport) -> str:
    """Map the application's sport enum to the Odds API archive key."""
    return {Sport.NFL: NFL_KEY, Sport.CFB: CFB_KEY}[sport]


class ArchiveBackfill:
    def __init__(
        self,
        store: Store,
        resolver: TeamResolver,
        client_factory: ClientFactory,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._client_factory = client_factory

    def run(
        self,
        games: Sequence[Game],
        *,
        max_credits: int,
        execute: bool,
        on_progress: ProgressCallback | None = None,
        max_submission_age: timedelta = timedelta(minutes=15),
        expected_credits: int | None = None,
        min_credit_reserve: int = 100,
        max_new_requests: int | None = None,
    ) -> ArchiveRunReport:
        if max_new_requests is not None and max_new_requests < 0:
            raise ValueError("max_new_requests must be non-negative")
        if not games:
            raise NoHistoricalGames("no historical games are stored")
        sports = {game.sport for game in games}
        if len(sports) != 1:
            raise MixedSports(sports)
        sport = games[0].sport
        plan = plan_snapshots(games, max_submission_age=max_submission_age)
        request_ids = {snapshot_request_id(sport, request): request for request in plan}
        completed_ids = self._store.completed_archive_request_ids(list(request_ids))
        pending = [
            (request_id, request)
            for request_id, request in request_ids.items()
            if request_id not in completed_ids
        ]
        credits = estimate_credits(plan)
        pending_credits = estimate_credits([request for _, request in pending])
        frozen = sum(request.kind is SnapshotKind.FROZEN for request in plan)
        report = ArchiveRunReport(
            total_snapshots=len(plan),
            frozen_snapshots=frozen,
            submission_snapshots=len(plan) - frozen,
            weeks=len({(request.season, request.week) for request in plan}),
            credits=credits,
            planned_snapshots=len(plan),
            completed_snapshots=len(completed_ids),
            pending_snapshots=len(pending),
            pending_credits=pending_credits,
            executed_snapshots=0,
            remaining_credits=None,
        )
        if pending_credits > max_credits:
            raise CreditLimitExceeded(pending_credits, max_credits)
        if expected_credits is not None and pending_credits != expected_credits:
            raise PlannedCostChanged(expected_credits, pending_credits)
        if not execute:
            return report
        if not pending:
            return report

        completed = 0
        with self._client_factory() as client:
            remaining_credits = client.remaining_credits()
            if remaining_credits < pending_credits + min_credit_reserve:
                raise InsufficientCredits(remaining_credits, pending_credits, min_credit_reserve)
            to_execute = pending if max_new_requests is None else pending[:max_new_requests]
            for index, (request_id, request) in enumerate(to_execute, start=1):
                source = FROZEN_SOURCE if request.kind is SnapshotKind.FROZEN else SUBMISSION_SOURCE
                try:
                    result = client.fetch_historical_spreads(
                        sport_key(sport),
                        resolver=self._resolver,
                        sport=sport,
                        season=request.season,
                        week=request.week,
                        at=request.at,
                        slate=request.slate,
                        window=request.window,
                        source=source,
                    )
                except QuotaExhausted as exc:
                    raise ArchiveRunInterrupted(index, len(to_execute), str(exc)) from exc
                returned_at = result.snapshot_at
                if returned_at is None:
                    raise ValueError("archive response is missing its snapshot timestamp")
                self._store.commit_archive_request(
                    request_id=request_id,
                    sport=sport,
                    season=request.season,
                    week=request.week,
                    kind=request.kind.value,
                    requested_at=request.at,
                    returned_at=returned_at,
                    lines=result.lines,
                )
                completed = index
                if on_progress is not None:
                    on_progress(
                        ArchiveProgress(
                            index=index,
                            total=len(to_execute),
                            request=request,
                            line_count=len(result.lines),
                            skipped=result.skipped,
                        )
                    )
        return report.model_copy(
            update={"executed_snapshots": completed, "remaining_credits": remaining_credits}
        )
