"""Owner-only Discord integration for weekly pick recommendations."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import app_commands
from discord.ext import commands

from pickem import config
from pickem.automation.monitor import MonitorScope, RecommendationMonitor, RefreshResult
from pickem.models import Sport
from pickem.operations.recommendations import generate_recommendations, refresh_recommendations
from pickem.store.db import AutomationState, Store

EASTERN = ZoneInfo("America/New_York")
DEFAULT_CONFIG_PATH = Path("config/discord-bot.yaml")
REMINDER_MESSAGE = "Reminder: submit this week's picks."
PRIVATE_MESSAGE = "This bot is private."
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscordSettings:
    """Configuration needed to run the local Discord service."""

    token: str
    owner_id: int
    sport: Sport
    season: int
    week: int
    db: Path = config.DEFAULT_DB
    timezone: ZoneInfo = EASTERN
    reminder_day: str = "tue"
    reminder_hour: int = 10
    reminder_minute: int = 0
    refresh_days: str = "wed-sun,mon"
    refresh_hour: int = 10
    refresh_minute: int = 0

    @classmethod
    def from_env(
        cls, *, config_path: Path = DEFAULT_CONFIG_PATH, db: Path | None = None
    ) -> DiscordSettings:
        """Read secrets from the environment and operational values from YAML."""
        try:
            raw = yaml.safe_load(config_path.read_text())
            active_week = raw["active_week"]
            schedule = raw["schedule"]
            reminder_hour, reminder_minute = _parse_time(schedule["reminder"]["time"])
            refresh_hour, refresh_minute = _parse_time(schedule["refresh"]["time"])
            refresh_days = ",".join(schedule["refresh"]["days"])
            timezone = ZoneInfo(raw["timezone"])
        except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as error:
            raise RuntimeError(
                f"invalid Discord bot configuration at {config_path}: {error}"
            ) from error
        return cls(
            token=config.discord_bot_token(),
            owner_id=config.discord_owner_id(),
            sport=Sport(active_week["sport"]),
            season=int(active_week["season"]),
            week=int(active_week["week"]),
            db=Path(db) if db is not None else Path(raw["database"]),
            timezone=timezone,
            reminder_day=schedule["reminder"]["day"],
            reminder_hour=reminder_hour,
            reminder_minute=reminder_minute,
            refresh_days=refresh_days,
            refresh_hour=refresh_hour,
            refresh_minute=refresh_minute,
        )

    @property
    def scope(self) -> MonitorScope:
        return MonitorScope(self.sport, self.season, self.week)


def _parse_time(value: str) -> tuple[int, int]:
    hour, minute = (int(part) for part in value.split(":", maxsplit=1))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid time {value!r}")
    return hour, minute


async def _invoke(callback: Callable[..., Any], *args: Any) -> Any:
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _scheduled_error_message(settings: DiscordSettings, error: BaseException) -> str:
    """Format a refresh failure without exposing environment configuration."""
    detail = str(error)
    for value in (settings.token, str(settings.owner_id), str(settings.db)):
        if value:
            detail = detail.replace(value, "[redacted]")
    return f"{type(error).__name__}: {detail}"


def build_schedule(
    settings: DiscordSettings,
    monitor: RecommendationMonitor,
    send_dm: Callable[[str], Awaitable[None] | None],
    scheduler: Any,
) -> None:
    """Register reminder and recommendation refresh jobs in Eastern time."""

    async def reminder_job() -> None:
        await _invoke(send_dm, REMINDER_MESSAGE)

    async def refresh_job() -> None:
        result = await _invoke(monitor.refresh)
        if isinstance(result, RefreshResult) and result.error is not None:
            logger.error(
                "scheduled recommendation refresh failed: %s",
                _scheduled_error_message(settings, result.error),
            )

    scheduler.add_job(
        reminder_job,
        CronTrigger(
            day_of_week=settings.reminder_day,
            hour=settings.reminder_hour,
            minute=settings.reminder_minute,
            timezone=settings.timezone,
        ),
        id="pick-reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_job,
        CronTrigger(
            day_of_week=settings.refresh_days,
            hour=settings.refresh_hour,
            minute=settings.refresh_minute,
            timezone=settings.timezone,
        ),
        id="recommendation-refresh",
        replace_existing=True,
    )


async def send_dm(bot: Any, settings: DiscordSettings, message: str) -> None:
    """Fetch the configured owner and deliver one direct message."""
    user = await bot.fetch_user(settings.owner_id)
    await user.send(message)


def _ensure_store_parent(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)


def _load_state(settings: DiscordSettings, scope: MonitorScope) -> AutomationState:
    _ensure_store_parent(settings.db)
    with Store(settings.db) as store:
        store.init_schema()
        return store.automation_state(scope.sport, scope.season, scope.week)


def _save_state(settings: DiscordSettings, scope: MonitorScope, state: AutomationState) -> None:
    _ensure_store_parent(settings.db)
    with Store(settings.db) as store:
        store.init_schema()
        store.save_automation_state(
            scope.sport,
            scope.season,
            scope.week,
            state.signature,
            state.checked_at,
            state.error_fingerprint,
        )


def _latest_market_timestamp(settings: DiscordSettings, scope: MonitorScope) -> datetime | None:
    _ensure_store_parent(settings.db)
    with Store(settings.db) as store:
        store.init_schema()
        dataset = store.load_week(scope.sport, scope.season, scope.week)
    return max((line.captured_at for line in dataset.market_lines), default=None)


def _format_timestamp(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "never"


def _next_scheduled_event(scheduler: Any) -> str:
    get_jobs = getattr(scheduler, "get_jobs", None)
    if get_jobs is None:
        return "unknown"
    jobs = [job for job in get_jobs() if getattr(job, "next_run_time", None) is not None]
    if not jobs:
        return "unknown"
    job = min(jobs, key=lambda item: item.next_run_time)
    return f"{getattr(job, 'id', 'scheduled job')} at {_format_timestamp(job.next_run_time)}"


def _format_status(
    settings: DiscordSettings,
    state: AutomationState,
    snapshot: Any,
    market_timestamp: datetime | None,
    scheduler: Any,
) -> str:
    recommendations = ", ".join(
        f"{edge.game_id}: {edge.side.value}" for edge in snapshot.edges
    ) or "none"
    return "\n".join(
        [
            f"Active scope: {settings.sport.value} {settings.season} week {settings.week}",
            f"Next scheduled event: {_next_scheduled_event(scheduler)}",
            f"Last successful check (stored): {_format_timestamp(state.checked_at)}",
            f"Stored market timestamp: {_format_timestamp(market_timestamp)}",
            f"Current recommendations (from stored market data): {recommendations}",
            f"Stored signature: {state.signature or 'none'}",
        ]
    )


class PickemBot(commands.Bot):
    """A Gateway bot with owner-only DM commands and no privileged intents."""

    def __init__(
        self,
        settings: DiscordSettings,
        monitor: RecommendationMonitor | Any | None = None,
        scheduler: Any | None = None,
    ) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.none(),
        )
        self.settings = settings
        self.scheduler = (
            scheduler if scheduler is not None else AsyncIOScheduler(timezone=settings.timezone)
        )
        self.scope = settings.scope
        self._load_state = lambda scope: _load_state(settings, scope)
        self._save_state = lambda scope, state: _save_state(settings, scope, state)
        self._send_owner_dm = lambda message: send_dm(self, settings, message)
        self.monitor = monitor if monitor is not None else RecommendationMonitor(
            refresh_week=lambda scope: asyncio.to_thread(
                refresh_recommendations,
                settings.db,
                scope.sport,
                scope.season,
                scope.week,
                datetime.now(UTC),
            ),
            load_state=self._load_state,
            save_state=self._save_state,
            notify=self._send_owner_dm,
            scope=self.scope,
        )
        command_context = app_commands.AppCommandContext(
            guild=False, dm_channel=True, private_channel=False
        )
        user_install = app_commands.AppInstallationType(guild=False, user=True)
        self.tree.add_command(
            app_commands.Command(
                name="status",
                description="Show this week's recommendation status",
                callback=self.status,
                allowed_contexts=command_context,
                allowed_installs=user_install,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="refresh",
                description="Refresh this week's recommendations",
                callback=self.refresh,
                allowed_contexts=command_context,
                allowed_installs=user_install,
            )
        )

    async def setup_hook(self) -> None:
        await self.tree.sync()
        build_schedule(self.settings, self.monitor, self._send_owner_dm, self.scheduler)
        start = getattr(self.scheduler, "start", None)
        if start is not None and not getattr(self.scheduler, "running", False):
            start()

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        user = getattr(interaction, "user", None)
        return (
            user is not None
            and getattr(user, "id", None) == self.settings.owner_id
            and getattr(interaction, "guild", None) is None
        )

    async def _reject_private(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(interaction):
            return False
        await interaction.response.send_message(PRIVATE_MESSAGE, ephemeral=True)
        return True

    async def status(self, interaction: discord.Interaction) -> None:
        """Show current recommendations computed from stored market data."""
        if await self._reject_private(interaction):
            return
        try:
            # These reads are local DuckDB operations. Keep them on the
            # interaction's loop so the connection is opened and closed on
            # one thread; live refreshes are the intentionally offloaded
            # operation in the monitor composition below.
            state = self._load_state(self.scope)
            snapshot = generate_recommendations(
                self.settings.db,
                self.scope.sport,
                self.scope.season,
                self.scope.week,
                datetime.now(UTC),
            )
            market_timestamp = _latest_market_timestamp(self.settings, self.scope)
            message = _format_status(
                self.settings, state, snapshot, market_timestamp, self.scheduler
            )
        except Exception as error:
            message = f"Status unavailable: {error}"
        await interaction.response.send_message(message)

    async def refresh(self, interaction: discord.Interaction) -> None:
        """Run one serialized recommendation refresh and report its outcome."""
        if await self._reject_private(interaction):
            return
        await interaction.response.defer()
        try:
            result: RefreshResult = await self.monitor.refresh()
        except Exception as error:
            message = f"Refresh failed: {error}"
        else:
            if result.error is not None:
                message = f"Refresh failed: {result.error}"
            elif result.changed:
                message = "Recommendations changed."
            else:
                message = "Recommendations unchanged."
        await interaction.followup.send(message)


def create_bot(
    settings: DiscordSettings,
    monitor: RecommendationMonitor | Any | None = None,
    scheduler: Any | None = None,
) -> PickemBot:
    """Build a configured bot for the console entry point."""
    return PickemBot(settings, monitor, scheduler)


# Keep both names discoverable for callers that refer to the integration as a
# Discord bot rather than by its Pickem-specific class name.
DiscordBot = PickemBot
build_bot = create_bot


def main() -> None:
    settings = DiscordSettings.from_env()
    create_bot(settings).run(settings.token)


__all__ = [
    "DiscordSettings",
    "DiscordBot",
    "EASTERN",
    "PickemBot",
    "REMINDER_MESSAGE",
    "build_schedule",
    "build_bot",
    "create_bot",
    "main",
    "send_dm",
]
