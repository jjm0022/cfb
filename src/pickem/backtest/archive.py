from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel

from pickem.backtest.snapshots import (
    SnapshotKind,
    SnapshotRequest,
    estimate_credits,
    plan_snapshots,
)
from pickem.ingest.odds import NFL_KEY, OddsClient, QuotaExhausted
from pickem.models import FROZEN_SOURCE, SUBMISSION_SOURCE, Game
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


class ArchiveProgress(BaseModel):
    index: int
    total: int
    request: SnapshotRequest
    line_count: int
    skipped: list[str]


class ArchiveRunReport(BaseModel):
    total_snapshots: int
    frozen_snapshots: int
    submission_snapshots: int
    weeks: int
    credits: int
    executed_snapshots: int


class NoHistoricalGames(ValueError):
    pass


class CreditLimitExceeded(ValueError):
    def __init__(self, credits: int, maximum: int) -> None:
        self.credits = credits
        self.maximum = maximum
        super().__init__(f"plan costs {credits} credits, above the {maximum}-credit ceiling")


class ArchiveRunInterrupted(RuntimeError):
    def __init__(self, index: int, total: int, reason: str) -> None:
        self.index = index
        self.total = total
        super().__init__(f"stopped at snapshot {index}/{total}: {reason}")


ProgressCallback = Callable[[ArchiveProgress], None]
ClientFactory = Callable[[], OddsClient]


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
    ) -> ArchiveRunReport:
        if not games:
            raise NoHistoricalGames("no historical NFL games are stored")
        plan = plan_snapshots(games)
        credits = estimate_credits(plan)
        frozen = sum(request.kind is SnapshotKind.FROZEN for request in plan)
        report = ArchiveRunReport(
            total_snapshots=len(plan),
            frozen_snapshots=frozen,
            submission_snapshots=len(plan) - frozen,
            weeks=len({(request.season, request.week) for request in plan}),
            credits=credits,
            executed_snapshots=0,
        )
        if credits > max_credits:
            raise CreditLimitExceeded(credits, max_credits)
        if not execute:
            return report

        completed = 0
        with self._client_factory() as client:
            for index, request in enumerate(plan, start=1):
                source = FROZEN_SOURCE if request.kind is SnapshotKind.FROZEN else SUBMISSION_SOURCE
                try:
                    result = client.fetch_historical_spreads(
                        NFL_KEY,
                        resolver=self._resolver,
                        sport=games[0].sport,
                        season=request.season,
                        week=request.week,
                        at=request.at,
                        slate=request.slate,
                        window=request.window,
                        source=source,
                    )
                except QuotaExhausted as exc:
                    raise ArchiveRunInterrupted(index, len(plan), str(exc)) from exc
                self._store.append_market_lines(result.lines)
                completed = index
                if on_progress is not None:
                    on_progress(
                        ArchiveProgress(
                            index=index,
                            total=len(plan),
                            request=request,
                            line_count=len(result.lines),
                            skipped=result.skipped,
                        )
                    )
        return report.model_copy(update={"executed_snapshots": completed})
