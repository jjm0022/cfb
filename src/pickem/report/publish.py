"""Write the dashboard page from whatever is stored: the season's results, and this week's board.

Every job that touches the page comes through here (the bot after each
refresh, the week-start script, the Tuesday results import), so each one
writes the same complete page and whichever ran last is correct. A failure is
logged with the job that hit it and returned, never raised: no caller's
message or exit status should depend on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from pickem import config
from pickem.atomic import write_atomic
from pickem.ingest.logos import LOGO_DIR, available_logos
from pickem.report.dashboard import render_dashboard, render_week_forwarder
from pickem.report.results import build_results_report
from pickem.report.this_week import build_this_week
from pickem.store.db import Store

TRIGGER_BOT = "bot"
TRIGGER_WEEK_START = "week-start"
TRIGGER_RESULTS = "results"
TRIGGER_MANUAL = "manual"


@dataclass(frozen=True)
class Published:
    path: Path | None  # None when no page was written
    error: str | None = None  # why not, when writing failed


def log_write_failure(trigger: str, error: BaseException, detail: str | None = None) -> None:
    """Log a page that was not written, with the job that tried and the traceback.

    There is no DM for this, so the traceback is the only clue to a failure
    that repeats. ``detail`` replaces the error's text in the message, for a
    caller that must redact it.
    """
    detail = str(error) if detail is None else detail
    logger.bind(
        event="dashboard_write_failed",
        trigger=trigger,
        error_type=type(error).__name__,
        error_detail=detail,
    ).opt(depth=1, exception=(type(error), error, error.__traceback__)).error(
        f"dashboard not written: {detail}"
    )


def publish_dashboard(
    store: Store,
    dashboard_dir: Path,
    *,
    trigger: str,
    season: int | None = None,
    entry_name: str = config.DEFAULT_ENTRY_NAME,
    now: datetime | None = None,
) -> Published:
    """Write index.html and a week-N.html forwarder per imported week."""
    now = now or datetime.now(tz=UTC)
    try:
        season = season if season is not None else store.latest_league_season()
        if season is None:
            logger.bind(event="dashboard_skipped", trigger=trigger).info(
                "dashboard not written: no CBS board is stored yet"
            )
            return Published(None)
        weeks = store.pool_weeks(season)
        report = (
            build_results_report(store, season=season, pool_week=weeks[-1], entry_name=entry_name)
            if weeks else None
        )
        this_week = build_this_week(store, season, now)
        page = render_dashboard(
            report, generated_at=now, season=season, this_week=this_week,
            logos=available_logos(dashboard_dir / LOGO_DIR),
        )
        path = dashboard_dir / "index.html"
        write_atomic(path, page)
        for week in weeks:
            write_atomic(dashboard_dir / f"week-{week}.html", render_week_forwarder(week))
    except Exception as exc:  # a render bug, a locked database or a full disk alike
        log_write_failure(trigger, exc)
        return Published(None, str(exc))
    logger.bind(
        event="dashboard_written",
        trigger=trigger,
        pool_week=weeks[-1] if weeks else None,
        this_week=None if this_week is None else this_week["pool_week"],
        path=str(path),
        bytes=len(page.encode("utf-8")),
        weeks=len(weeks),
    ).info(f"dashboard written to {path}")
    return Published(path)
