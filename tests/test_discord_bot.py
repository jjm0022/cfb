from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from pickem.automation.monitor import RefreshResult
from pickem.discord_bot import DiscordSettings, PickemBot, build_schedule, send_dm
from pickem.models import Sport


@dataclass
class FakeJob:
    func: object
    trigger: object
    kwargs: dict

    @property
    def trigger_fields(self):
        return {
            field.name: ",".join(str(expression) for expression in field.expressions)
            for field in self.trigger.fields
            if field.name in {"day_of_week", "hour", "minute"}
        }

    @property
    def timezone(self):
        return self.trigger.timezone


class FakeScheduler:
    def __init__(self):
        self.jobs: list[FakeJob] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append(FakeJob(func, trigger, kwargs))


class FakeMonitor:
    def __init__(self, result: RefreshResult | None = None, events: list[str] | None = None):
        self.result = result or RefreshResult(changed=False)
        self.calls = 0
        self.events = events

    async def refresh(self):
        if self.events is not None:
            self.events.append("monitor")
        self.calls += 1
        return self.result


class FakeFollowup:
    def __init__(self, events: list[str]):
        self.events = events
        self.messages: list[tuple[str, bool]] = []

    async def send(self, message: str, ephemeral: bool = False):
        self.events.append("followup")
        self.messages.append((message, ephemeral))


class FakeResponse:
    def __init__(self, events: list[str] | None = None):
        self.events = events if events is not None else []
        self.messages: list[tuple[str, bool]] = []

    async def defer(self):
        self.events.append("defer")

    async def send_message(self, message: str, ephemeral: bool = False):
        self.events.append("initial")
        self.messages.append((message, ephemeral))


class FakeInteraction:
    def __init__(self, user_id: int, events: list[str] | None = None):
        self.user = SimpleNamespace(id=user_id)
        self.events = events if events is not None else []
        self.response = FakeResponse(self.events)
        self.followup = FakeFollowup(self.events)
        self.guild = None


@pytest.fixture
def settings(monkeypatch, tmp_path: Path):
    values = {
        "DISCORD_BOT_TOKEN": "test-token",
        "DISCORD_OWNER_ID": "123",
        "PICKEM_SPORT": "nfl",
        "PICKEM_SEASON": "2026",
        "PICKEM_WEEK": "1",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return DiscordSettings.from_env(db=tmp_path / "pickem.duckdb")


def test_settings_use_required_environment_values(settings):
    assert settings.token == "test-token"
    assert settings.owner_id == 123
    assert settings.sport is Sport.NFL
    assert settings.season == 2026
    assert settings.week == 1


def test_project_packages_discord_console_entry_point():
    with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    assert project["project"]["scripts"]["pickem-discord-bot"] == "pickem.discord_bot:main"


def test_settings_missing_secret_uses_config_required_policy(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN"):
        DiscordSettings.from_env()


def test_build_schedule_adds_tuesday_reminder_and_weekday_refreshes(settings):
    scheduler = FakeScheduler()
    build_schedule(settings, FakeMonitor(), lambda _message: None, scheduler)

    assert scheduler.jobs[0].trigger_fields == {
        "day_of_week": "tue",
        "hour": "10",
        "minute": "0",
    }
    assert scheduler.jobs[1].trigger_fields == {
        "day_of_week": "wed-sun,mon",
        "hour": "10",
        "minute": "0",
    }
    assert scheduler.jobs[0].timezone.key == "America/New_York"


@pytest.mark.asyncio
async def test_scheduled_refresh_logs_a_result_error(settings, caplog):
    monitor = FakeMonitor(RefreshResult(changed=False, error=RuntimeError("quota exhausted")))
    scheduler = FakeScheduler()
    build_schedule(settings, monitor, lambda _message: None, scheduler)

    await scheduler.jobs[1].func()

    assert "quota exhausted" in caplog.text


def test_bot_uses_no_privileged_intents_and_registers_dm_commands(settings):
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())

    assert bot.intents.value == 0
    assert {command.name for command in bot.tree.get_commands()} == {"status", "refresh"}


@pytest.mark.asyncio
async def test_status_rejects_non_owner_before_reading_state(settings):
    monitor = FakeMonitor()
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=999)

    await bot.status(interaction)

    assert interaction.response.messages == [("This bot is private.", True)]
    assert monitor.calls == 0


@pytest.mark.asyncio
async def test_refresh_rejects_non_owner_before_refreshing(settings):
    monitor = FakeMonitor()
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=999)

    await bot.refresh(interaction)

    assert interaction.response.messages == [("This bot is private.", True)]
    assert monitor.calls == 0


@pytest.mark.asyncio
async def test_refresh_reports_unchanged_changed_and_failure(settings):
    for result, expected in (
        (RefreshResult(changed=False), "Recommendations unchanged."),
        (RefreshResult(changed=True), "Recommendations changed."),
        (RefreshResult(changed=False, error=RuntimeError("quota exhausted")), "Refresh failed"),
    ):
        events: list[str] = []
        monitor = FakeMonitor(result, events)
        bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
        interaction = FakeInteraction(user_id=settings.owner_id, events=events)

        await bot.refresh(interaction)

        assert expected in interaction.followup.messages[0][0]
        assert interaction.followup.messages[0][1] is False
        assert monitor.calls == 1
        assert events == ["defer", "monitor", "followup"]


@pytest.mark.asyncio
async def test_refresh_defers_before_monitor_and_uses_followup(settings):
    events: list[str] = []
    monitor = FakeMonitor(RefreshResult(changed=True), events)
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id, events=events)

    await bot.refresh(interaction)

    assert events == ["defer", "monitor", "followup"]
    assert interaction.response.messages == []
    assert interaction.followup.messages == [("Recommendations changed.", False)]


@pytest.mark.asyncio
async def test_status_reports_stored_market_and_monitor_state_without_refresh(settings):
    monitor = FakeMonitor()
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.status(interaction)

    message = interaction.response.messages[0][0]
    assert "Active scope: nfl 2026 week 1" in message
    assert "Current recommendations (from stored market data): none" in message
    assert "Last successful check (stored): never" in message
    assert monitor.calls == 0


@pytest.mark.asyncio
async def test_send_dm_fetches_only_configured_owner(settings):
    sent: list[str] = []

    class FakeUser:
        async def send(self, message):
            sent.append(message)

    class FakeBot:
        def __init__(self):
            self.fetched: list[int] = []

        async def fetch_user(self, user_id):
            self.fetched.append(user_id)
            return FakeUser()

    bot = FakeBot()
    await send_dm(bot, settings, "hello")

    assert bot.fetched == [settings.owner_id]
    assert sent == ["hello"]


def test_config_required_still_has_no_secret_logging(monkeypatch, caplog):
    values = {
        "DISCORD_BOT_TOKEN": "secret-value",
        "DISCORD_OWNER_ID": "123",
        "PICKEM_SPORT": "nfl",
        "PICKEM_SEASON": "2026",
        "PICKEM_WEEK": "1",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    DiscordSettings.from_env()
    assert "secret-value" not in caplog.text
