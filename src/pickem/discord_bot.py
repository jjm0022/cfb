"""Owner-only Discord integration for weekly pick recommendations."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from discord import app_commands
from discord.ext import commands
from loguru import logger

from pickem import config
from pickem.automation.monitor import MonitorScope, RecommendationMonitor, RefreshResult
from pickem.automation.poll_plan import PollInstant, plan_polls
from pickem.models import Game, Side, Sport, Tier
from pickem.obs.log import configure_logging, run_context
from pickem.operations.recommendations import generate_recommendations, refresh_recommendations
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import AutomationState, Store

EASTERN = ZoneInfo("America/New_York")
DEFAULT_CONFIG_PATH = Path("config/discord-bot.yaml")
REMINDER_MESSAGE = "Reminder: submit this week's picks."
PRIVATE_MESSAGE = "This bot is private."

# Hours before each kickoff that the market is polled, on top of the daily
# refresh. A fixed wall-clock time cannot be close to kickoff for a slate that
# runs twelve hours; these are anchored to the kickoffs themselves.
DEFAULT_POLL_OFFSETS_HOURS = (12.0, 6.0, 2.0, 1.0)
DEFAULT_POLL_PLAN_EVERY_HOURS = 6
DEFAULT_POLL_HORIZON_DAYS = 10
POLL_JOB_PREFIX = "poll:"


@dataclass(frozen=True)
class DiscordSettings:
    """Configuration needed to run the local Discord service."""

    token: str
    owner_id: int
    db: Path = config.DEFAULT_DB
    timezone: ZoneInfo = EASTERN
    reminder_day: str = "tue"
    reminder_hour: int = 10
    reminder_minute: int = 0
    refresh_days: str = "wed-sun,mon"
    refresh_hour: int = 10
    refresh_minute: int = 0
    poll_offsets_hours: tuple[float, ...] = DEFAULT_POLL_OFFSETS_HOURS
    poll_plan_every_hours: int = DEFAULT_POLL_PLAN_EVERY_HOURS
    poll_horizon_days: int = DEFAULT_POLL_HORIZON_DAYS

    @classmethod
    def from_env(
        cls, *, config_path: Path = DEFAULT_CONFIG_PATH, db: Path | None = None
    ) -> DiscordSettings:
        """Read secrets from the environment and operational values from YAML."""
        try:
            raw = yaml.safe_load(config_path.read_text())
            schedule = raw["schedule"]
            reminder_hour, reminder_minute = _parse_time(schedule["reminder"]["time"])
            refresh_hour, refresh_minute = _parse_time(schedule["refresh"]["time"])
            refresh_days = ",".join(schedule["refresh"]["days"])
            timezone = ZoneInfo(raw["timezone"])
            # Optional: a config file written before kickoff-anchored polling
            # existed must still start the bot.
            polls = schedule.get("kickoff_polls") or {}
            offsets = _parse_offsets(polls.get("offsets_hours", DEFAULT_POLL_OFFSETS_HOURS))
            plan_every = int(polls.get("plan_every_hours", DEFAULT_POLL_PLAN_EVERY_HOURS))
            horizon_days = int(polls.get("horizon_days", DEFAULT_POLL_HORIZON_DAYS))
            if not 1 <= plan_every <= 23:
                raise ValueError(f"plan_every_hours must be 1-23, got {plan_every}")
            if horizon_days < 1:
                raise ValueError(f"horizon_days must be positive, got {horizon_days}")
        except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as error:
            raise RuntimeError(
                f"invalid Discord bot configuration at {config_path}: {error}"
            ) from error
        return cls(
            token=config.discord_bot_token(),
            owner_id=config.discord_owner_id(),
            db=Path(db) if db is not None else Path(raw["database"]),
            timezone=timezone,
            reminder_day=schedule["reminder"]["day"],
            reminder_hour=reminder_hour,
            reminder_minute=reminder_minute,
            refresh_days=refresh_days,
            refresh_hour=refresh_hour,
            refresh_minute=refresh_minute,
            poll_offsets_hours=offsets,
            poll_plan_every_hours=plan_every,
            poll_horizon_days=horizon_days,
        )


def _parse_offsets(values: object) -> tuple[float, ...]:
    """Normalize configured kickoff offsets, rejecting ones that cannot fire.

    A zero or negative offset would name an instant at or after kickoff, which
    is a poll that cannot change a pick and can only capture in-play prices.
    """
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ValueError(f"offsets_hours must be a list, got {values!r}")
    offsets = tuple(float(value) for value in values)
    if any(offset <= 0 for offset in offsets):
        raise ValueError(f"offsets_hours must all be positive, got {offsets}")
    return offsets

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


def _scheduled_error_detail(settings: DiscordSettings, error: BaseException) -> str:
    """Return refresh error text with environment configuration redacted."""
    detail = str(error)
    for value in (settings.token, str(settings.owner_id), str(settings.db)):
        if value:
            detail = detail.replace(value, "[redacted]")
    return detail


def _scheduled_error_message(settings: DiscordSettings, error: BaseException) -> str:
    """Format a refresh failure without exposing environment configuration."""
    return f"{type(error).__name__}: {_scheduled_error_detail(settings, error)}"


def build_schedule(
    settings: DiscordSettings,
    refresh: Callable[[], Awaitable[Any] | Any] | RecommendationMonitor,
    send_dm: Callable[[str], Awaitable[None] | None],
    scheduler: Any,
    plan: Callable[[], Awaitable[Any] | Any] | None = None,
) -> None:
    """Register the reminder, the daily refresh, and the poll planner.

    The daily refresh stays: it is the digest, and the fallback for a week
    whose kickoffs are not yet stored. ``plan`` is the callback that turns
    those kickoffs into one-shot poll jobs; without it no planner is
    registered and the bot behaves exactly as it did before.
    """

    async def reminder_job() -> None:
        await _invoke(send_dm, REMINDER_MESSAGE)

    async def refresh_job() -> None:
        with run_context("sched:refresh", db=str(settings.db)):
            callback = getattr(refresh, "refresh", refresh)
            await _invoke(callback)

    async def plan_job() -> None:
        with run_context("sched:plan-polls", db=str(settings.db)):
            await _invoke(plan)

    scheduler.add_job(
        reminder_job,
        CronTrigger(
            day_of_week=settings.reminder_day,
            hour=settings.reminder_hour,
            minute=settings.reminder_minute,
            timezone=settings.timezone,
        ),
        id="pick-reminder",
        name="pick reminder",
        replace_existing=True,
    )
    logger.bind(
        event="job_scheduled",
        job="pick-reminder",
        days=settings.reminder_day,
        at=f"{settings.reminder_hour:02d}:{settings.reminder_minute:02d}",
        timezone=str(settings.timezone),
    ).info(
        f"reminder job scheduled: {settings.reminder_day} at "
        f"{settings.reminder_hour:02d}:{settings.reminder_minute:02d} {settings.timezone}"
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
        name="recommendation refresh",
        replace_existing=True,
    )
    logger.bind(
        event="job_scheduled",
        job="recommendation-refresh",
        days=settings.refresh_days,
        at=f"{settings.refresh_hour:02d}:{settings.refresh_minute:02d}",
        timezone=str(settings.timezone),
    ).info(
        f"refresh job scheduled: {settings.refresh_days} at "
        f"{settings.refresh_hour:02d}:{settings.refresh_minute:02d} {settings.timezone}"
    )
    if plan is None:
        return
    scheduler.add_job(
        plan_job,
        CronTrigger(
            hour=f"*/{settings.poll_plan_every_hours}",
            minute=settings.refresh_minute,
            timezone=settings.timezone,
        ),
        id="poll-planner",
        name="kickoff poll planner",
        replace_existing=True,
    )
    logger.bind(
        event="job_scheduled",
        job="poll-planner",
        days="*",
        at=f"every {settings.poll_plan_every_hours}h",
        timezone=str(settings.timezone),
    ).info(
        f"poll planner job scheduled: every {settings.poll_plan_every_hours}h at "
        f":{settings.refresh_minute:02d} {settings.timezone}"
    )


def poll_job_id(scope: MonitorScope, instant: PollInstant) -> str:
    """A stable id for one scope's poll at one instant.

    Stable so that re-planning the same slate replaces its jobs rather than
    duplicating them, and so a job whose instant left the plan can be
    identified and dropped.
    """
    return (
        f"{POLL_JOB_PREFIX}{scope.sport.value}:{scope.season}:{scope.week}"
        f":{instant.at.isoformat()}"
    )


def schedule_kickoff_polls(
    plans: Mapping[MonitorScope, Sequence[PollInstant]],
    run_poll: Callable[[MonitorScope, PollInstant], Awaitable[Any] | Any],
    scheduler: Any,
) -> tuple[str, ...]:
    """Make the scheduler's poll jobs match ``plans`` exactly, and say which.

    Re-planning is the whole point: a slate re-ingested with a moved kickoff
    must not leave the old instant's poll behind to spend a credit on a game
    that is no longer there. Every poll job is owned by this function, so any
    it did not just register is removed.
    """
    wanted: dict[str, tuple[MonitorScope, PollInstant]] = {
        poll_job_id(scope, instant): (scope, instant)
        for scope, instants in plans.items()
        for instant in instants
    }

    existing = {
        job_id
        for job in scheduler.get_jobs()
        if (job_id := getattr(job, "id", None) or "").startswith(POLL_JOB_PREFIX)
    }
    for job_id in existing - set(wanted):
        scheduler.remove_job(job_id)

    for job_id, (scope, instant) in wanted.items():
        scheduler.add_job(
            _poll_job(run_poll, scope, instant),
            DateTrigger(run_date=instant.at),
            id=job_id,
            name=_poll_job_name(scope, instant),
            replace_existing=True,
        )

    _log_plan(wanted, existing)
    return tuple(wanted)


def _poll_job_name(scope: MonitorScope, instant: PollInstant) -> str:
    """A job name that says what the poll is for.

    APScheduler quotes ``job.name`` in its own execution records. Left unset it
    is the callable's qualified name, so every poll on the board reports itself
    as the same anonymous closure.
    """
    return (
        f"poll {_scope_key(scope)} T-{instant.offset_hours:g}h "
        f"before {instant.kickoff_utc.isoformat()}"
    )


def _scope_key(scope: MonitorScope) -> str:
    return f"{scope.sport.value}/{scope.season}/wk{scope.week}"


def _log_plan(
    wanted: Mapping[str, tuple[MonitorScope, PollInstant]], existing: set[str]
) -> None:
    """Record what this planning run decided, not that it ran.

    A planner that found nothing to do and a planner that never ran are
    otherwise indistinguishable, so the empty plan is logged too.
    """
    per_scope: dict[str, int] = {}
    for scope, _ in wanted.values():
        per_scope[_scope_key(scope)] = per_scope.get(_scope_key(scope), 0) + 1
    next_poll = min((instant.at for _, instant in wanted.values()), default=None)
    added = len(set(wanted) - existing)
    fields = {
        "event": "polls_planned",
        "jobs": len(wanted),
        "added": added,
        "removed": len(existing - set(wanted)),
        "unchanged": len(existing & set(wanted)),
        "per_scope": per_scope,
        "next_poll": next_poll.isoformat() if next_poll else None,
        "offsets_hours": sorted(
            {instant.offset_hours for _, instant in wanted.values()}, reverse=True
        ),
    }
    if not wanted:
        logger.bind(**fields).info(
            "no kickoff polls to schedule: no active scope has a future kickoff"
        )
        return
    logger.bind(**fields).info(
        f"planned {len(wanted)} polls across {len(per_scope)} scope(s) "
        f"({', '.join(f'{key} {count}' for key, count in sorted(per_scope.items()))}); "
        f"next {next_poll.isoformat()}; "
        f"+{added} -{len(existing - set(wanted))}"
    )


def _poll_job(
    run_poll: Callable[[MonitorScope, PollInstant], Awaitable[Any] | Any],
    scope: MonitorScope,
    instant: PollInstant,
) -> Callable[[], Awaitable[Any]]:
    async def job() -> Any:
        return await _invoke(run_poll, scope, instant)

    return job


async def send_dm(
    bot: Any,
    settings: DiscordSettings,
    message: str | None = None,
    *,
    embed: discord.Embed | None = None,
) -> None:
    """Fetch the configured owner and deliver one direct message."""
    user = await bot.fetch_user(settings.owner_id)
    if embed is not None:
        await user.send(embed=embed)
    else:
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


def resolve_pickem_scopes(
    db: Path, now: datetime, *, season: int | None = None, week: int | None = None
) -> tuple[tuple[Sport, int, int], ...]:
    """Resolve the sports and weeks the bot should currently monitor.

    An explicit season and week returns every sport with stored pick lines for
    that slate.  Without overrides, each sport contributes its earliest
    incomplete picked week, so a week remains visible until its final score is
    present in the database.
    """
    if (season is None) != (week is None):
        raise ValueError("season and week must be provided together")
    _ensure_store_parent(db)
    with Store(db) as store:
        store.init_schema()
        if season is not None and week is not None:
            scopes = store.pickem_scopes_for_week(season, week)
        else:
            scopes = store.active_pickem_scopes(now)
    return tuple(scopes)


def _stored_week_details(
    settings: DiscordSettings, scope: MonitorScope
) -> tuple[tuple[Game, ...], datetime | None]:
    """Load a scope's teams and latest market timestamp for Discord output."""
    _ensure_store_parent(settings.db)
    with Store(settings.db) as store:
        store.init_schema()
        dataset = store.load_week(scope.sport, scope.season, scope.week)
    latest_market = max((line.captured_at for line in dataset.market_lines), default=None)
    return tuple(dataset.games), latest_market


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "never"
    return value.astimezone(EASTERN).strftime("%b %-d, %Y at %-I:%M %p %Z")


def _next_scheduled_event(scheduler: Any) -> str:
    get_jobs = getattr(scheduler, "get_jobs", None)
    if get_jobs is None:
        return "unknown"
    jobs = [job for job in get_jobs() if getattr(job, "next_run_time", None) is not None]
    if not jobs:
        return "unknown"
    job = min(jobs, key=lambda item: item.next_run_time)
    return f"{getattr(job, 'id', 'scheduled job')} at {_format_timestamp(job.next_run_time)}"


def _scope_description(scope: MonitorScope) -> str:
    return f"{scope.sport.value.upper()} • {scope.season} — Week {scope.week}"


_TIER_BADGES = {
    Tier.STRONG: "🔥 Strong",
    Tier.LEAN: "✅ Lean",
    Tier.COINFLIP: "🪙 Coinflip",
    Tier.NO_MARKET: "⚠️ No market",
}
_DISCORD_FIELD_VALUE_LIMIT = 1024
_PICK_BREAK = re.compile(r"(\n+)(?=• )")
_UNKNOWN_KICKOFF = datetime.max.replace(tzinfo=UTC)


def _kickoff_ordered(snapshot: Any, games_by_id: dict[str, Game]) -> list[Any]:
    """Order picks by kickoff, keeping confidence order as the tiebreaker.

    ``rank_edges`` orders the snapshot by confidence, which is what the CBS
    submission consumes.  Display order is a separate concern: the picks read
    more naturally in the order the games are played.  Edges whose game is not
    stored have no kickoff to sort on, so they keep their rank order at the end.
    """

    def kickoff(edge: Any) -> datetime:
        game = games_by_id.get(edge.game_id)
        return game.kickoff_utc if game is not None else _UNKNOWN_KICKOFF

    return sorted(snapshot.edges, key=kickoff)


def _format_recommendations(
    snapshot: Any | None, games: tuple[Game, ...] = (), *, details: bool = False
) -> str:
    """Render one scope's picks in kickoff order, one line each.

    Reasoning is opt-in.  A compact slate fits a single Discord field, which
    keeps the message whole; ``details`` adds the rationale line back and
    accepts that a long slate will be split across continuation fields.
    """
    if snapshot is None:
        return "Updated recommendations were not returned."
    games_by_id = {game.game_id: game for game in games}
    resolver = TeamResolver.default()
    formatted: list[str] = []
    for edge in _kickoff_ordered(snapshot, games_by_id):
        game = games_by_id.get(edge.game_id)
        if game is None:
            formatted.append(f"• `{edge.game_id}` — **{edge.side.value}**")
            continue
        away = resolver.display_name(game.away_team_id, game.sport)
        home = resolver.display_name(game.home_team_id, game.sport)
        matchup = (
            f"**{away}** at ~~{home}~~"
            if edge.side is Side.AWAY
            else f"~~{away}~~ at **{home}**"
        )
        pick = f"• {_TIER_BADGES[edge.tier]} — {matchup}"
        formatted.append(f"{pick}\n> Why: {edge.rationale}" if details else pick)
    separator = "\n\n" if details else "\n"
    return separator.join(formatted) or "No recommendations stored yet."


def _field_value_chunks(value: str) -> tuple[str, ...]:
    """Split formatted picks without exceeding Discord's field-value limit.

    Picks are the split boundary, so a chunk never ends mid-pick.  Compact and
    detailed output separate picks differently, so the separator each break
    consumed is captured and restored rather than assumed.
    """
    parts = _PICK_BREAK.split(value)
    entries = parts[0::2]
    separators = [""] + parts[1::2]
    chunks: list[str] = []
    current = ""
    for entry, separator in zip(entries, separators, strict=True):
        if len(entry) > _DISCORD_FIELD_VALUE_LIMIT:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                entry[index : index + _DISCORD_FIELD_VALUE_LIMIT]
                for index in range(0, len(entry), _DISCORD_FIELD_VALUE_LIMIT)
            )
            continue
        candidate = entry if not current else f"{current}{separator}{entry}"
        if len(candidate) > _DISCORD_FIELD_VALUE_LIMIT:
            chunks.append(current)
            current = entry
        else:
            current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks) or ("No recommendations stored yet.",)


@dataclass(frozen=True)
class ScopeStatus:
    """The stored and computed status for one currently selected scope."""

    scope: MonitorScope
    state: AutomationState
    snapshot: Any
    games: tuple[Game, ...]
    market_timestamp: datetime | None


def _add_recommendation_fields(
    embed: discord.Embed,
    scope: MonitorScope,
    snapshot: Any | None,
    games: tuple[Game, ...],
    *,
    details: bool = False,
) -> None:
    scope_name = _scope_description(scope)
    for index, picks in enumerate(
        _field_value_chunks(_format_recommendations(snapshot, games, details=details)), start=1
    ):
        suffix = " Picks" if index == 1 else f" Picks (cont. {index})"
        embed.add_field(name=f"{scope_name} —{suffix}", value=picks, inline=False)


def _format_change_notification(
    scope: MonitorScope, snapshot: Any | None, games: tuple[Game, ...]
) -> discord.Embed:
    embed = discord.Embed(title="🏈 Recommendations Updated", color=discord.Color.green())
    _add_recommendation_fields(embed, scope, snapshot, games)
    return embed


def _format_status(
    statuses: tuple[ScopeStatus, ...], scheduler: Any, *, details: bool = False
) -> discord.Embed:
    if not statuses:
        return discord.Embed(
            title="🏈 Pick'em Status",
            description="No active pick'em scopes with stored picks.",
            color=discord.Color.blurple(),
        )
    embed = discord.Embed(
        title="🏈 Pick'em Status",
        description="Current active pick'em scopes.",
        color=discord.Color.blurple(),
    )
    for status in statuses:
        scope_name = _scope_description(status.scope)
        _add_recommendation_fields(
            embed, status.scope, status.snapshot, status.games, details=details
        )
        monitoring = "\n".join(
            [
                f"Last successful check: {_format_timestamp(status.state.checked_at)}",
                f"Stored market data: {_format_timestamp(status.market_timestamp)}",
            ]
        )
        embed.add_field(name=f"{scope_name} — Monitoring", value=monitoring, inline=False)
    return embed.add_field(
        name="Scheduling",
        value=f"Next scheduled event: {_next_scheduled_event(scheduler)}",
        inline=False,
    )


def _format_refresh_results(
    results: tuple[tuple[MonitorScope, RefreshResult], ...],
    error: BaseException | None = None,
    *,
    games_by_scope: dict[MonitorScope, tuple[Game, ...]] | None = None,
    details: bool = False,
) -> discord.Embed:
    if error is not None:
        return discord.Embed(
            title="⚠️ Refresh Failed",
            description=str(error),
            color=discord.Color.red(),
        )
    if not results:
        return discord.Embed(
            title="🏈 Pick'em Refresh",
            description="No active pick'em scopes with stored picks.",
            color=discord.Color.blurple(),
        )
    has_error = any(result.error is not None for _, result in results)
    changed = any(result.changed for _, result in results)
    title = "⚠️ Refresh Completed with Errors" if has_error else (
        "🏈 Recommendations Updated" if changed else "✅ Recommendations Unchanged"
    )
    color = discord.Color.red() if has_error else (
        discord.Color.green() if changed else discord.Color.blurple()
    )
    embed = discord.Embed(title=title, color=color)
    for scope, result in results:
        value = (
            f"Refresh failed: {result.error}"
            if result.error is not None
            else _format_recommendations(
                result.snapshot,
                (games_by_scope or {}).get(scope, ()),
                details=details,
            )
            if result.changed
            else "The latest odds refresh completed with no recommendation changes."
        )
        embed.add_field(name=_scope_description(scope), value=value, inline=False)
    return embed


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
        self._load_state = lambda scope: _load_state(settings, scope)
        self._save_state = lambda scope, state: _save_state(settings, scope, state)
        self._send_owner_dm = lambda message=None, *, embed=None: send_dm(
            self, settings, message, embed=embed
        )
        self._injected_monitor = monitor
        self._monitors: dict[MonitorScope, RecommendationMonitor] = {}
        self._refresh_lock = asyncio.Lock()
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
        build_schedule(
            self.settings,
            self._scheduled_refresh,
            self._send_owner_dm,
            self.scheduler,
            self._plan_kickoff_polls,
        )
        # Plan once at startup as well as on the cron: a restart between
        # planning runs must not leave the rest of the week unpolled.
        self._plan_kickoff_polls()
        start = getattr(self.scheduler, "start", None)
        if start is not None and not getattr(self.scheduler, "running", False):
            start()
        logger.bind(event="bot_ready", owner_id=self.settings.owner_id).info(
            f"bot ready; owner {self.settings.owner_id}"
        )

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
        logger.bind(
            event="command_rejected",
            user_id=getattr(getattr(interaction, "user", None), "id", None),
        ).warning(
            "non-owner command rejected: user "
            f"{getattr(getattr(interaction, 'user', None), 'id', None)}"
        )
        return True

    def _resolve_scopes(
        self,
        season: int | None = None,
        week: int | None = None,
        *,
        sport: Sport | None = None,
    ) -> tuple[MonitorScope, ...]:
        scopes = tuple(
            MonitorScope(scope_sport, scope_season, scope_week)
            for scope_sport, scope_season, scope_week in resolve_pickem_scopes(
                self.settings.db, datetime.now(UTC), season=season, week=week
            )
            # CFB week 2 and NFL week 1 are the same pool week but not the same
            # board, and an explicit season+week returns every sport holding
            # lines for it. A poll anchored to one sport's kickoff must not
            # spend a credit on the other's.
            if sport is None or scope_sport is sport
        )
        logger.bind(
            event="scope_resolved",
            count=len(scopes),
            scopes=[f"{s.sport.value}/{s.season}/wk{s.week}" for s in scopes],
            explicit=season is not None,
            resolution_reason=(
                "explicit_override" if season is not None else "active_schedule"
            ),
        ).info(f"resolved {len(scopes)} active scopes")
        return scopes

    def _monitor_for(self, scope: MonitorScope) -> RecommendationMonitor | Any:
        if self._injected_monitor is not None:
            return self._injected_monitor
        monitor = self._monitors.get(scope)
        if monitor is None:
            monitor = RecommendationMonitor(
                refresh_week=lambda current_scope, **kwargs: asyncio.to_thread(
                    refresh_recommendations,
                    self.settings.db,
                    current_scope.sport,
                    current_scope.season,
                    current_scope.week,
                    datetime.now(UTC),
                    **kwargs,
                ),
                load_state=self._load_state,
                save_state=self._save_state,
                notify=lambda message: self._send_monitor_notification(scope, message),
                scope=scope,
            )
            self._monitors[scope] = monitor
        return monitor

    async def _send_monitor_notification(self, scope: MonitorScope, message: str) -> None:
        if not message.startswith("Recommendations changed:"):
            await self._send_owner_dm(message)
            return
        games, _ = _stored_week_details(self.settings, scope)
        now = datetime.now(UTC)
        snapshot = generate_recommendations(
            self.settings.db,
            scope.sport,
            scope.season,
            scope.week,
            now,
            # Matches the refresh that produced this notification; without it
            # the embed would re-list games the refresh deliberately excluded.
            pending_as_of=now,
        )
        await self._send_owner_dm(
            embed=_format_change_notification(scope, snapshot, games)
        )

    async def _refresh_scopes(
        self,
        season: int | None = None,
        week: int | None = None,
        *,
        sport: Sport | None = None,
        window_start: datetime | None = None,
    ) -> tuple[tuple[MonitorScope, RefreshResult], ...]:
        # Automation only ever acts on games that have not kicked off: a locked
        # pick cannot be changed, so re-deciding it is noise and notifying on
        # it is misleading. The pick sheet still renders the whole week.
        refresh_kwargs: dict[str, Any] = {"pending_as_of": datetime.now(UTC)}
        # Passed through only when set, so an adapter whose refresh() takes no
        # keywords keeps working.
        if window_start is not None:
            refresh_kwargs["window_start"] = window_start
        async with self._refresh_lock:
            scopes = self._resolve_scopes(season, week, sport=sport)
            results: list[tuple[MonitorScope, RefreshResult]] = []
            for scope in scopes:
                try:
                    result = await self._monitor_for(scope).refresh(**refresh_kwargs)
                except Exception as error:
                    logger.bind(
                        event="refresh_failed",
                        phase="refresh",
                        scope=f"{scope.sport.value}/{scope.season}/wk{scope.week}",
                        error_type=type(error).__name__,
                        error_detail=_scheduled_error_detail(self.settings, error),
                    ).opt(
                        exception=(type(error), error, error.__traceback__),
                    ).error(_scheduled_error_message(self.settings, error))
                    result = RefreshResult(changed=False, error=error)
                results.append((scope, result))
            return tuple(results)

    async def _scheduled_refresh(self) -> tuple[tuple[MonitorScope, RefreshResult], ...]:
        return await self._refresh_scopes()

    def _plan_kickoff_polls(self, now: datetime | None = None) -> tuple[str, ...]:
        """Re-derive the poll schedule from the kickoffs currently stored.

        Run periodically rather than once, because the slate arrives during
        the week: CBS posts the two boards on different days, so a week
        ingested after the last planning run would otherwise go unpolled.
        Each scope is planned separately -- a poll only ever covers one sport
        -- and a scope with nothing left to poll contributes no jobs.
        """
        moment = now if now is not None else datetime.now(UTC)
        plans: dict[MonitorScope, tuple[PollInstant, ...]] = {}
        for scope in self._resolve_scopes():
            games, _ = _stored_week_details(self.settings, scope)
            instants = plan_polls(
                [game.kickoff_utc for game in games],
                offsets_hours=self.settings.poll_offsets_hours,
                now=moment,
                horizon=timedelta(days=self.settings.poll_horizon_days),
            )
            if instants:
                plans[scope] = instants
        return schedule_kickoff_polls(plans, self._run_kickoff_poll, self.scheduler)

    async def _run_kickoff_poll(
        self, scope: MonitorScope, instant: PollInstant
    ) -> tuple[tuple[MonitorScope, RefreshResult], ...]:
        with run_context("sched:kickoff-poll", db=str(self.settings.db)):
            logger.bind(
                event="kickoff_poll_fired",
                sport=scope.sport.value,
                season=scope.season,
                week=scope.week,
                offset_hours=instant.offset_hours,
                kickoff=instant.kickoff_utc.isoformat(),
            ).info(
                f"polling {scope.sport.value} {instant.offset_hours:g}h before "
                f"{instant.kickoff_utc.isoformat()}"
            )
            return await self._refresh_scopes(
                scope.season,
                scope.week,
                sport=scope.sport,
                window_start=instant.at,
            )

    async def status(
        self,
        interaction: discord.Interaction,
        season: int | None = None,
        week: int | None = None,
        details: bool = False,
    ) -> None:
        """Show current recommendations computed from stored market data."""
        if await self._reject_private(interaction):
            return
        try:
            with run_context(
                "discord:/status",
                season=season,
                week=week,
                db=str(self.settings.db),
            ):
                logger.bind(event="command_invoked", command="status").info(
                    "/status invoked"
                )
                # These reads are local DuckDB operations. Keep them on the
                # interaction's loop so the connection is opened and closed on
                # one thread; live refreshes are the intentionally offloaded
                # operation in the monitor composition below.
                statuses: list[ScopeStatus] = []
                for scope in self._resolve_scopes(season, week):
                    games, market_timestamp = _stored_week_details(self.settings, scope)
                    statuses.append(
                        ScopeStatus(
                            scope=scope,
                            state=self._load_state(scope),
                            snapshot=generate_recommendations(
                                self.settings.db,
                                scope.sport,
                                scope.season,
                                scope.week,
                                datetime.now(UTC),
                            ),
                            games=games,
                            market_timestamp=market_timestamp,
                        )
                    )
                embed = _format_status(tuple(statuses), self.scheduler, details=details)
        except Exception as error:
            await interaction.response.send_message(f"Status unavailable: {error}")
            return
        await interaction.response.send_message(embed=embed)

    async def refresh(
        self,
        interaction: discord.Interaction,
        season: int | None = None,
        week: int | None = None,
        details: bool = False,
    ) -> None:
        """Run one serialized recommendation refresh and report its outcome."""
        if await self._reject_private(interaction):
            return
        with run_context(
            "discord:/refresh",
            season=season,
            week=week,
            db=str(self.settings.db),
        ):
            logger.bind(event="command_invoked", command="refresh").info(
                "/refresh invoked"
            )
            await interaction.response.defer()
            try:
                results = await self._refresh_scopes(season, week)
            except Exception as error:
                logger.bind(
                    event="refresh_failed",
                    phase="command",
                    command="refresh",
                    error_type=type(error).__name__,
                    error_detail=_scheduled_error_detail(self.settings, error),
                ).opt(
                    exception=(type(error), error, error.__traceback__),
                ).error(_scheduled_error_message(self.settings, error))
                embed = _format_refresh_results((), error=error)
            else:
                embed = _format_refresh_results(
                    results,
                    games_by_scope={
                        scope: _stored_week_details(self.settings, scope)[0]
                        for scope, result in results
                        if result.changed
                    },
                    details=details,
                )
            await interaction.followup.send(embed=embed)


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
    configure_logging("pickem", console="off")
    settings = DiscordSettings.from_env()
    logger.bind(event="bot_starting", db=str(settings.db)).info(
        f"starting discord bot on {settings.db}"
    )
    create_bot(settings).run(settings.token)


__all__ = [
    "DEFAULT_POLL_OFFSETS_HOURS",
    "DiscordSettings",
    "DiscordBot",
    "EASTERN",
    "POLL_JOB_PREFIX",
    "PickemBot",
    "REMINDER_MESSAGE",
    "build_schedule",
    "build_bot",
    "create_bot",
    "main",
    "poll_job_id",
    "resolve_pickem_scopes",
    "schedule_kickoff_polls",
    "send_dm",
]
