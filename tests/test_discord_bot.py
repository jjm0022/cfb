from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from pickem.automation.monitor import MonitorScope, RefreshResult
from pickem.discord_bot import (
    DiscordSettings,
    PickemBot,
    ScopeStatus,
    _format_recommendations,
    _format_status,
    build_schedule,
    resolve_pickem_scopes,
    send_dm,
)
from pickem.models import Edge, Game, LeagueLine, Side, Sport, Tier
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import AutomationState, Store


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
        self.messages: list[tuple[str | None, bool]] = []
        self.embeds: list[object] = []

    async def send(self, message: str | None = None, ephemeral: bool = False, *, embed=None):
        self.events.append("followup")
        self.messages.append((message, ephemeral))
        if embed is not None:
            self.embeds.append(embed)


class FakeResponse:
    def __init__(self, events: list[str] | None = None):
        self.events = events if events is not None else []
        self.messages: list[tuple[str | None, bool]] = []
        self.embeds: list[object] = []

    async def defer(self):
        self.events.append("defer")

    async def send_message(
        self, message: str | None = None, ephemeral: bool = False, *, embed=None
    ):
        self.events.append("initial")
        self.messages.append((message, ephemeral))
        if embed is not None:
            self.embeds.append(embed)


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
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config_path = tmp_path / "discord-bot.yaml"
    config_path.write_text(
        "database: pickem.duckdb\n"
        "timezone: America/New_York\n"
        "schedule:\n"
        "  reminder:\n"
        "    day: tue\n"
        "    time: '10:00'\n"
        "  refresh:\n"
        "    days: [wed, thu, fri, sat, sun, mon]\n"
        "    time: '10:00'\n"
    )
    return DiscordSettings.from_env(config_path=config_path, db=tmp_path / "pickem.duckdb")


def test_settings_use_secrets_and_runtime_configuration_without_an_active_week(settings):
    assert settings.token == "test-token"
    assert settings.owner_id == 123
    assert settings.db.name == "pickem.duckdb"
    assert not hasattr(settings, "scope")


def add_pick_scope(settings, sport: Sport = Sport.NFL) -> Game:
    game = Game(
        game_id=f"{sport.value}-2026-01-A-at-B",
        sport=sport,
        season=2026,
        week=1,
        kickoff_utc=datetime(2026, 8, 30, 17, tzinfo=UTC),
        home_team_id="B",
        away_team_id="A",
    )
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games([game])
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=-3.0,
                    posted_at=game.kickoff_utc,
                )
            ]
        )
    return game


@pytest.mark.parametrize(
    ("side", "tier", "tier_badge", "expected_matchup"),
    [
        (Side.HOME, Tier.STRONG, "🔥 Strong", "~~Buffalo Bills~~ at **Miami Dolphins**"),
        (Side.AWAY, Tier.LEAN, "✅ Lean", "**Buffalo Bills** at ~~Miami Dolphins~~"),
        (Side.HOME, Tier.COINFLIP, "🪙 Coinflip", "~~Buffalo Bills~~ at **Miami Dolphins**"),
        (Side.AWAY, Tier.NO_MARKET, "⚠️ No market", "**Buffalo Bills** at ~~Miami Dolphins~~"),
    ],
)
def test_status_pick_format_highlights_the_selected_team_and_explains_why(
    side, tier, tier_badge, expected_matchup
):
    game = Game(
        game_id="nfl-2026-01-BUF-at-MIA",
        sport=Sport.NFL,
        season=2026,
        week=1,
        kickoff_utc=datetime(2026, 9, 10, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
        edges=(
            Edge(
                game_id=game.game_id,
                side=side,
                delta=3.0,
                tier=tier,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale="league -3.0 vs market -6.0: 3.0 pts toward home",
            ),
        ),
    )

    rendered = _format_recommendations(snapshot, (game,))

    assert expected_matchup in rendered
    assert tier_badge in rendered
    assert "Why: league -3.0 vs market -6.0: 3.0 pts toward home" in rendered


@pytest.mark.asyncio
async def test_status_uses_stored_teams_to_render_readable_picks(settings, monkeypatch):
    game = Game(
        game_id="nfl-2026-01-BUF-at-MIA",
        sport=Sport.NFL,
        season=2026,
        week=1,
        kickoff_utc=datetime(2026, 9, 10, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games([game])
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=-3.0,
                    posted_at=game.kickoff_utc,
                )
            ]
        )
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
        edges=(
            Edge(
                game_id=game.game_id,
                side=Side.HOME,
                delta=3.0,
                tier=Tier.STRONG,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale="because the market moved",
            ),
        ),
    )
    monkeypatch.setattr("pickem.discord_bot.generate_recommendations", lambda *_args: snapshot)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.status(interaction)

    assert "🔥 Strong — ~~Buffalo Bills~~ at **Miami Dolphins**" in (
        interaction.response.embeds[0].fields[0].value
    )


def test_status_splits_long_pick_lists_within_discord_field_limits():
    games = tuple(
        Game(
            game_id=f"nfl-2026-01-AWAY{index}-at-HOME{index}",
            sport=Sport.NFL,
            season=2026,
            week=1,
            kickoff_utc=datetime(2026, 9, 10, tzinfo=UTC),
            home_team_id=f"HOME{index}",
            away_team_id=f"AWAY{index}",
        )
        for index in range(12)
    )
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
        edges=tuple(
            Edge(
                game_id=game.game_id,
                side=Side.HOME,
                delta=3.0,
                tier=Tier.STRONG,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale=f"rationale {index}: " + "market evidence " * 24,
            )
            for index, game in enumerate(games)
        ),
    )
    status = ScopeStatus(
        scope=MonitorScope(Sport.NFL, 2026, 1),
        state=AutomationState(),
        snapshot=snapshot,
        games=games,
        market_timestamp=None,
    )

    embed = _format_status((status,), FakeScheduler())
    rendered = "\n".join(field.value for field in embed.fields)

    assert len(embed.fields) > 2
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert "rationale 0:" in rendered
    assert "rationale 11:" in rendered


def test_scope_resolution_discovers_only_the_active_sports_with_picks(settings):
    with Store(settings.db) as store:
        store.init_schema()
        game = Game(
            game_id="cfb-2026-01-A-at-B",
            sport=Sport.CFB,
            season=2026,
            week=1,
            kickoff_utc=datetime(2026, 8, 30, 17, tzinfo=UTC),
            home_team_id="B",
            away_team_id="A",
        )
        store.upsert_games([game])
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=-3.0,
                    posted_at=game.kickoff_utc,
                )
            ]
        )

    assert resolve_pickem_scopes(settings.db, datetime(2026, 8, 31, tzinfo=UTC)) == (
        (Sport.CFB, 2026, 1),
    )


@pytest.mark.asyncio
async def test_status_uses_the_discovered_cfb_scope(settings, monkeypatch):
    game = Game(
        game_id="cfb-2026-01-A-at-B",
        sport=Sport.CFB,
        season=2026,
        week=1,
        kickoff_utc=datetime(2026, 8, 30, 17, tzinfo=UTC),
        home_team_id="B",
        away_team_id="A",
    )
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games([game])
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=-3.0,
                    posted_at=game.kickoff_utc,
                )
            ]
        )
    snapshot = RecommendationSnapshot(
        sport=Sport.CFB,
        season=2026,
        week=1,
        generated_at=datetime(2026, 8, 31, tzinfo=UTC),
        edges=(),
    )
    monkeypatch.setattr("pickem.discord_bot.generate_recommendations", lambda *_args: snapshot)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.status(interaction)

    embed = interaction.response.embeds[0]
    assert embed.title == "🏈 Pick'em Status"
    assert embed.description == "Current active pick'em scopes."
    assert embed.fields[0].name == "CFB • 2026 — Week 1 — Picks"


@pytest.mark.asyncio
async def test_status_accepts_an_explicit_season_and_week_for_every_sport(settings, monkeypatch):
    add_pick_scope(settings, Sport.CFB)
    add_pick_scope(settings, Sport.NFL)
    generated_for: list[Sport] = []

    def generate(_db, sport, season, week, now):
        generated_for.append(sport)
        return RecommendationSnapshot(sport, season, week, now, ())

    monkeypatch.setattr("pickem.discord_bot.generate_recommendations", generate)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.status(interaction, season=2026, week=1)

    embed = interaction.response.embeds[0]
    assert [field.name for field in embed.fields if field.name.endswith("— Picks")] == [
        "CFB • 2026 — Week 1 — Picks",
        "NFL • 2026 — Week 1 — Picks",
    ]
    assert generated_for == [Sport.CFB, Sport.NFL]


@pytest.mark.asyncio
async def test_refresh_processes_every_discovered_sport(settings):
    add_pick_scope(settings, Sport.CFB)
    add_pick_scope(settings, Sport.NFL)
    monitor = FakeMonitor()
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.refresh(interaction)

    assert monitor.calls == 2
    assert [field.name for field in interaction.followup.embeds[0].fields] == [
        "CFB • 2026 — Week 1",
        "NFL • 2026 — Week 1",
    ]


def test_project_packages_discord_console_entry_point():
    with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    assert project["project"]["scripts"]["pickem-discord-bot"] == "pickem.discord_bot:main"


def test_systemd_service_uses_project_directory_and_restart_policy():
    repository_root = Path(__file__).parents[1]
    service = (repository_root / "deploy/pickem-discord-bot.service").read_text()

    assert "Type=simple" in service
    assert "WorkingDirectory=/home/jmiller/cfb" in service
    assert "EnvironmentFile=/home/jmiller/cfb/.env" in service
    assert "ExecStart=/usr/bin/env uv run pickem-discord-bot" in service
    assert "Restart=on-failure" in service
    assert "RestartSec=5" in service
    assert "Environment=PATH=/home/jmiller/.local/bin:/usr/local/bin:/usr/bin" in service


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
        "day_of_week": "wed,thu,fri,sat,sun,mon",
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


@pytest.mark.asyncio
async def test_monitor_change_notification_uses_status_pick_format(settings, monkeypatch):
    game = Game(
        game_id="nfl-2026-01-BUF-at-MIA",
        sport=Sport.NFL,
        season=2026,
        week=1,
        kickoff_utc=datetime(2026, 9, 10, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games([game])
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 9, 2, tzinfo=UTC),
        edges=(
            Edge(
                game_id=game.game_id,
                side=Side.HOME,
                delta=3.0,
                tier=Tier.STRONG,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale="league -3.0 vs market -6.0: 3.0 pts toward home",
            ),
        ),
    )
    monkeypatch.setattr("pickem.discord_bot.refresh_recommendations", lambda *_args: snapshot)
    monkeypatch.setattr("pickem.discord_bot.generate_recommendations", lambda *_args: snapshot)
    bot = PickemBot(settings, scheduler=FakeScheduler())
    bot._load_state = lambda _scope: AutomationState(signature=f"{game.game_id}:away")
    bot._save_state = lambda _scope, _state: None
    sent: list[tuple[str | None, object | None]] = []

    async def capture_dm(message: str | None = None, *, embed=None):
        sent.append((message, embed))

    bot._send_owner_dm = capture_dm

    result = await bot._monitor_for(MonitorScope(Sport.NFL, 2026, 1)).refresh()

    assert result.changed is True
    assert sent[0][0] is None
    embed = sent[0][1]
    assert embed.title == "🏈 Recommendations Updated"
    assert embed.fields[0].name == "NFL • 2026 — Week 1 — Picks"
    assert embed.fields[0].value == (
        "• 🔥 Strong — ~~Buffalo Bills~~ at **Miami Dolphins**\n"
        "> Why: league -3.0 vs market -6.0: 3.0 pts toward home"
    )


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
async def test_refresh_lists_updated_picks_in_changed_embed(settings):
    add_pick_scope(settings)
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 8, 29, 14, tzinfo=UTC),
        edges=(
            Edge(
                game_id="game-a",
                side=Side.AWAY,
                delta=3.0,
                tier=Tier.LEAN,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale="test",
            ),
        ),
    )
    interaction = FakeInteraction(user_id=settings.owner_id)
    bot = PickemBot(
        settings,
        FakeMonitor(RefreshResult(changed=True, snapshot=snapshot)),
        scheduler=FakeScheduler(),
    )

    await bot.refresh(interaction)

    assert interaction.followup.messages == [(None, False)]
    embed = interaction.followup.embeds[0]
    assert embed.title == "🏈 Recommendations Updated"
    assert embed.fields[0].name == "NFL • 2026 — Week 1"
    assert embed.fields[0].value == "• `game-a` — **away**"


@pytest.mark.asyncio
async def test_refresh_shows_unchanged_embed(settings):
    add_pick_scope(settings)
    interaction = FakeInteraction(user_id=settings.owner_id)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())

    await bot.refresh(interaction)

    assert interaction.followup.messages == [(None, False)]
    assert interaction.followup.embeds[0].title == "✅ Recommendations Unchanged"


@pytest.mark.asyncio
async def test_refresh_shows_failure_embed(settings):
    add_pick_scope(settings)
    interaction = FakeInteraction(user_id=settings.owner_id)
    bot = PickemBot(
        settings,
        FakeMonitor(RefreshResult(changed=False, error=RuntimeError("quota exhausted"))),
        scheduler=FakeScheduler(),
    )

    await bot.refresh(interaction)

    assert interaction.followup.messages == [(None, False)]
    embed = interaction.followup.embeds[0]
    assert embed.title == "⚠️ Refresh Completed with Errors"
    assert "quota exhausted" in embed.fields[0].value


@pytest.mark.asyncio
async def test_refresh_defers_before_monitor_and_uses_followup(settings):
    add_pick_scope(settings)
    events: list[str] = []
    monitor = FakeMonitor(RefreshResult(changed=True), events)
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id, events=events)

    await bot.refresh(interaction)

    assert events == ["defer", "monitor", "followup"]
    assert interaction.response.messages == []
    assert interaction.followup.messages == [(None, False)]
    assert interaction.followup.embeds[0].title == "🏈 Recommendations Updated"


@pytest.mark.asyncio
async def test_status_shows_an_embed_when_no_active_picks_are_stored(settings):
    monitor = FakeMonitor()
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.status(interaction)

    assert interaction.response.messages == [(None, False)]
    assert interaction.response.embeds
    embed = interaction.response.embeds[0]
    assert embed.title == "🏈 Pick'em Status"
    assert embed.description == "No active pick'em scopes with stored picks."
    assert monitor.calls == 0


@pytest.mark.asyncio
async def test_status_lists_each_stored_recommendation_in_its_embed(settings, monkeypatch):
    add_pick_scope(settings)
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 8, 29, 14, tzinfo=UTC),
        edges=(
            Edge(
                game_id="game-a",
                side=Side.AWAY,
                delta=3.0,
                tier=Tier.LEAN,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale="test",
            ),
        ),
    )
    monkeypatch.setattr("pickem.discord_bot.generate_recommendations", lambda *_args: snapshot)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.status(interaction)

    embed = interaction.response.embeds[0]
    assert embed.fields[0].value.startswith("• `game-a` — **away**")


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


@pytest.mark.asyncio
async def test_send_dm_delivers_an_embed_without_plain_text(settings):
    sent: list[tuple[str | None, object | None]] = []

    class FakeUser:
        async def send(self, message=None, *, embed=None):
            sent.append((message, embed))

    class FakeBot:
        async def fetch_user(self, _user_id):
            return FakeUser()

    embed = object()

    await send_dm(FakeBot(), settings, embed=embed)

    assert sent == [(None, embed)]


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
