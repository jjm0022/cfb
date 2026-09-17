from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from loguru import logger

from pickem.automation.monitor import (
    MonitorScope,
    RecommendationChange,
    RecommendationMonitor,
    RefreshResult,
)
from pickem.automation.poll_plan import PollInstant
from pickem.discord_bot import (
    DiscordSettings,
    LastChange,
    PickemBot,
    ScopeStatus,
    _format_recommendations,
    _format_refresh_results,
    _format_status,
    _last_recommendation_change,
    build_schedule,
    resolve_pickem_scopes,
    schedule_kickoff_polls,
    send_dm,
)
from pickem.models import (
    HISTORY_MONITOR,
    Edge,
    Game,
    LeagueLine,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import AutomationState, Store


@dataclass
class FakeJob:
    func: object
    trigger: object
    kwargs: dict

    @property
    def id(self):
        return self.kwargs.get("id")

    @property
    def name(self):
        return self.kwargs.get("name")

    @property
    def run_date(self):
        return getattr(self.trigger, "run_date", None)

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
        self.running = False
        self.removed: list[str] = []

    def add_job(self, func, trigger, **kwargs):
        job_id = kwargs.get("id")
        if job_id is not None and kwargs.get("replace_existing"):
            self.jobs = [job for job in self.jobs if job.id != job_id]
        self.jobs.append(FakeJob(func, trigger, kwargs))

    def get_jobs(self):
        return list(self.jobs)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs = [job for job in self.jobs if job.id != job_id]

    def start(self):
        self.running = True

    def job_ids(self, prefix=""):
        return [job.id for job in self.jobs if (job.id or "").startswith(prefix)]


class FakeMonitor:
    def __init__(self, result: RefreshResult | None = None, events: list[str] | None = None):
        self.result = result or RefreshResult(changed=False)
        self.calls = 0
        self.events = events

    async def refresh(self, **kwargs):
        if self.events is not None:
            self.events.append("monitor")
        self.calls += 1
        return self.result


class RaisingMonitor:
    async def refresh(self, **kwargs):
        raise RuntimeError("monitor exploded")


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


def _pick_lines(rendered: str) -> list[str]:
    """The pick lines only, so ordering assertions ignore any reasoning lines."""
    return [line for line in rendered.splitlines() if line.startswith("• ")]


def _nfl_game(away: str, home: str, kickoff: datetime) -> Game:
    return Game(
        game_id=f"nfl-2026-01-{away}-at-{home}",
        sport=Sport.NFL,
        season=2026,
        week=1,
        kickoff_utc=kickoff,
        home_team_id=home,
        away_team_id=away,
    )


def _snapshot_for(*edges: Edge) -> RecommendationSnapshot:
    return RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
        edges=edges,
    )


def _edge(game_id: str, *, rationale: str = "market moved", tier: Tier = Tier.STRONG) -> Edge:
    return Edge(
        game_id=game_id,
        side=Side.HOME,
        delta=3.0,
        tier=tier,
        league_spread=-3.0,
        market_spread=-6.0,
        rationale=rationale,
    )


def test_picks_are_rendered_in_kickoff_order_not_confidence_order():
    sunday = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    thursday = _nfl_game("DAL", "PHI", datetime(2026, 9, 10, 0, tzinfo=UTC))
    saturday = _nfl_game("GB", "CHI", datetime(2026, 9, 12, 20, tzinfo=UTC))
    snapshot = _snapshot_for(
        _edge(sunday.game_id, tier=Tier.STRONG),
        _edge(saturday.game_id, tier=Tier.LEAN),
        _edge(thursday.game_id, tier=Tier.COINFLIP),
    )

    rendered = _format_recommendations(snapshot, (sunday, thursday, saturday))

    assert [line.split(" — ")[1] for line in _pick_lines(rendered)] == [
        "~~Dallas Cowboys~~ at **Philadelphia Eagles**",
        "~~Green Bay Packers~~ at **Chicago Bears**",
        "~~Buffalo Bills~~ at **Miami Dolphins**",
    ]


def test_picks_without_a_stored_game_sort_last_in_confidence_order():
    kicked = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    snapshot = _snapshot_for(
        _edge("nfl-2026-01-UNKNOWN-A"),
        _edge(kicked.game_id),
        _edge("nfl-2026-01-UNKNOWN-B"),
    )

    lines = _pick_lines(_format_recommendations(snapshot, (kicked,)))

    assert "Miami Dolphins" in lines[0]
    assert "`nfl-2026-01-UNKNOWN-A`" in lines[1]
    assert "`nfl-2026-01-UNKNOWN-B`" in lines[2]


def test_picks_sharing_a_kickoff_keep_their_confidence_order():
    kickoff = datetime(2026, 9, 13, 17, tzinfo=UTC)
    first = _nfl_game("BUF", "MIA", kickoff)
    second = _nfl_game("DAL", "PHI", kickoff)
    snapshot = _snapshot_for(_edge(second.game_id), _edge(first.game_id))

    lines = _pick_lines(_format_recommendations(snapshot, (first, second)))

    assert "Philadelphia Eagles" in lines[0]
    assert "Miami Dolphins" in lines[1]


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

    rendered = _format_recommendations(snapshot, (game,), details=True)

    assert expected_matchup in rendered
    assert tier_badge in rendered
    assert "Why: league -3.0 vs market -6.0: 3.0 pts toward home" in rendered


def test_picks_omit_reasoning_unless_details_are_requested():
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    snapshot = _snapshot_for(_edge(game.game_id, rationale="the market moved three points"))

    compact = _format_recommendations(snapshot, (game,))
    verbose = _format_recommendations(snapshot, (game,), details=True)

    assert compact == "• 🔥 Strong — ~~Buffalo Bills~~ at **Miami Dolphins**"
    assert verbose == (
        "• 🔥 Strong — ~~Buffalo Bills~~ at **Miami Dolphins**\n"
        "> Why: the market moved three points"
    )


def test_compact_picks_too_long_for_one_field_split_on_whole_picks():
    games = tuple(
        _nfl_game(f"AWAY{index:02d}", f"HOME{index:02d}", datetime(2026, 9, 13, 17, tzinfo=UTC))
        for index in range(40)
    )
    snapshot = _snapshot_for(*(_edge(game.game_id) for game in games))
    status = ScopeStatus(
        scope=MonitorScope(Sport.NFL, 2026, 1),
        state=AutomationState(),
        snapshot=snapshot,
        games=games,
        market_timestamp=None,
    )

    embed = _format_status((status,), FakeScheduler())

    pick_fields = [field for field in embed.fields if "Picks" in field.name]
    assert len(pick_fields) > 1
    assert all(len(field.value) <= 1024 for field in pick_fields)
    assert sum(len(_pick_lines(field.value)) for field in pick_fields) == 40
    assert all(
        line.startswith("• ") for field in pick_fields for line in field.value.splitlines()
    )


def test_status_renders_a_full_slate_as_one_unsplit_field():
    games = tuple(
        _nfl_game(f"AWAY{index:02d}", f"HOME{index:02d}", datetime(2026, 9, 13, 17, tzinfo=UTC))
        for index in range(16)
    )
    snapshot = _snapshot_for(
        *(_edge(game.game_id, rationale="market evidence " * 24) for game in games)
    )
    status = ScopeStatus(
        scope=MonitorScope(Sport.NFL, 2026, 1),
        state=AutomationState(),
        snapshot=snapshot,
        games=games,
        market_timestamp=None,
    )

    embed = _format_status((status,), FakeScheduler())

    pick_fields = [field for field in embed.fields if "Picks" in field.name]
    assert [field.name for field in pick_fields] == ["NFL • 2026 — Week 1 — Picks"]
    assert len(_pick_lines(pick_fields[0].value)) == 16


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
    monkeypatch.setattr(
        "pickem.discord_bot.generate_recommendations",
        lambda *_args, **_kwargs: snapshot,
    )
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

    embed = _format_status((status,), FakeScheduler(), details=True)
    rendered = "\n".join(field.value for field in embed.fields)

    assert len(embed.fields) > 2
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert "rationale 0:" in rendered
    assert "rationale 11:" in rendered


def test_refresh_splits_long_pick_lists_within_discord_field_limits():
    # /refresh --details on a real CFB slate sent one field per scope and
    # Discord rejected the whole message with "Must be 1024 or fewer in
    # length", losing the refresh's output entirely.
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
    scope = MonitorScope(Sport.NFL, 2026, 1)

    embed = _format_refresh_results(
        ((scope, RefreshResult(changed=True, snapshot=snapshot)),),
        games_by_scope={scope: games},
        details=True,
    )
    rendered = "\n".join(field.value for field in embed.fields)

    assert len(embed.fields) > 1
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


def test_bot_scope_resolution_logs_active_schedule_reason(settings, records):
    add_pick_scope(settings, Sport.CFB)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())

    scopes = bot._resolve_scopes()

    resolved = next(r for r in records if r["extra"].get("event") == "scope_resolved")
    assert resolved["extra"]["count"] == 1
    assert resolved["extra"]["scopes"] == ["cfb/2026/wk1"]
    assert resolved["extra"]["explicit"] is False
    assert resolved["extra"]["resolution_reason"] == "active_schedule"
    assert scopes == (MonitorScope(Sport.CFB, 2026, 1),)


def test_bot_scope_resolution_logs_explicit_override_reason(settings, records):
    add_pick_scope(settings, Sport.CFB)
    add_pick_scope(settings, Sport.NFL)
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())

    scopes = bot._resolve_scopes(2026, 1)

    resolved = next(r for r in records if r["extra"].get("event") == "scope_resolved")
    assert resolved["extra"]["count"] == 2
    assert resolved["extra"]["scopes"] == ["cfb/2026/wk1", "nfl/2026/wk1"]
    assert resolved["extra"]["explicit"] is True
    assert resolved["extra"]["resolution_reason"] == "explicit_override"
    assert scopes == (
        MonitorScope(Sport.CFB, 2026, 1),
        MonitorScope(Sport.NFL, 2026, 1),
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
    monkeypatch.setattr(
        "pickem.discord_bot.generate_recommendations",
        lambda *_args, **_kwargs: snapshot,
    )
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
async def test_scheduled_refresh_does_not_repeat_a_returned_result_error(settings, records):
    error = RuntimeError("quota exhausted")

    async def refresh():
        logger.bind(
            event="refresh_failed",
            phase="refresh",
            error_type=type(error).__name__,
            error_detail=str(error),
        ).error(f"refresh failed: {error}")
        return RefreshResult(changed=False, error=error)

    scheduler = FakeScheduler()
    build_schedule(settings, refresh, lambda _message: None, scheduler)

    await scheduler.jobs[1].func()

    failures = [record for record in records if record["extra"].get("event") == "refresh_failed"]
    assert len(failures) == 1
    assert failures[0]["extra"]["error_type"] == "RuntimeError"
    assert failures[0]["extra"]["error_detail"] == "quota exhausted"
    assert failures[0]["message"] == "refresh failed: quota exhausted"


@pytest.mark.asyncio
async def test_scheduled_refresh_does_not_repeat_tuple_result_errors(settings, records):
    scope = MonitorScope(Sport.CFB, 2026, 2)
    error = ValueError("missing slate")

    async def refresh():
        logger.bind(
            event="refresh_failed",
            phase="refresh",
            scope=f"{scope.sport.value}/{scope.season}/wk{scope.week}",
            error_type=type(error).__name__,
            error_detail=str(error),
        ).error(f"refresh failed: {error}")
        return (
            (
                scope,
                RefreshResult(changed=False, error=error),
            ),
        )

    scheduler = FakeScheduler()
    build_schedule(settings, refresh, lambda _message: None, scheduler)

    await scheduler.jobs[1].func()

    failures = [record for record in records if record["extra"].get("event") == "refresh_failed"]
    assert len(failures) == 1
    assert failures[0]["extra"]["scope"] == "cfb/2026/wk2"
    assert failures[0]["extra"]["error_type"] == "ValueError"
    assert failures[0]["extra"]["error_detail"] == "missing slate"
    assert failures[0]["message"] == "refresh failed: missing slate"


def test_schedule_emits_one_observable_event_per_registered_job(settings, records):
    scheduler = FakeScheduler()
    build_schedule(settings, FakeMonitor(), lambda _message: None, scheduler)

    scheduled = [r for r in records if r["extra"].get("event") == "job_scheduled"]
    assert len(scheduled) == 2
    assert [r["extra"]["job"] for r in scheduled] == [
        "pick-reminder",
        "recommendation-refresh",
    ]
    assert scheduled[0]["extra"]["days"] == "tue"
    assert scheduled[0]["extra"]["at"] == "10:00"
    assert scheduled[0]["extra"]["timezone"] == "America/New_York"
    assert scheduled[1]["extra"]["days"] == "wed,thu,fri,sat,sun,mon"
    assert scheduled[1]["extra"]["at"] == "10:00"
    assert scheduled[1]["extra"]["timezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_scheduled_refresh_runs_inside_a_context_with_database_fact(settings, records):
    scheduler = FakeScheduler()
    build_schedule(settings, FakeMonitor(), lambda _message: None, scheduler)

    await scheduler.jobs[1].func()

    started = next(r for r in records if r["extra"].get("event") == "run_started")
    assert started["extra"]["entry"] == "sched:refresh"
    assert started["extra"]["db"] == str(settings.db)
    finished = [r for r in records if r["extra"].get("event") == "run_finished"]
    assert len(finished) == 1
    assert not any(r["extra"].get("event") == "job_fired" for r in records)


@pytest.mark.asyncio
async def test_monitor_exception_is_logged_once_with_scope_and_traceback(settings, records):
    add_pick_scope(settings, Sport.CFB)
    scope = MonitorScope(Sport.CFB, 2026, 1)
    bot = PickemBot(settings, RaisingMonitor(), scheduler=FakeScheduler())

    results = await bot._refresh_scopes()

    assert results[0][0] == scope
    assert isinstance(results[0][1].error, RuntimeError)
    failures = [r for r in records if r["extra"].get("event") == "refresh_failed"]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["level"].name == "ERROR"
    assert failure["extra"]["phase"] == "refresh"
    assert failure["extra"]["scope"] == "cfb/2026/wk1"
    assert failure["extra"]["error_type"] == "RuntimeError"
    assert failure["extra"]["error_detail"] == "monitor exploded"
    if failure["exception"] is not None:
        exception_type, exception, traceback = failure["exception"]
        assert exception_type is RuntimeError
        assert isinstance(exception, RuntimeError)
        assert traceback is not None
    else:
        # The configured redaction patcher folds and clears exception tuples;
        # the durable record must still retain the traceback text.
        assert "Traceback (most recent call last)" in failure["message"]
        assert "in _refresh_scopes" in failure["message"]


@pytest.mark.asyncio
async def test_monitor_change_notification_lists_only_changed_picks(settings):
    game = Game(
        game_id="nfl-2026-01-BUF-at-MIA",
        sport=Sport.NFL,
        season=2026,
        week=1,
        kickoff_utc=datetime(2026, 9, 10, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
    )
    unchanged_game = Game(
        game_id="nfl-2026-01-DAL-at-PHI",
        sport=Sport.NFL,
        season=2026,
        week=1,
        kickoff_utc=datetime(2026, 9, 10, tzinfo=UTC),
        home_team_id="PHI",
        away_team_id="DAL",
    )
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games([game, unchanged_game])
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
            Edge(
                game_id=unchanged_game.game_id,
                side=Side.HOME,
                delta=2.0,
                tier=Tier.LEAN,
                league_spread=-2.0,
                market_spread=-4.0,
                rationale="league -2.0 vs market -4.0: 2.0 pts toward home",
            ),
        ),
    )
    bot = PickemBot(settings, scheduler=FakeScheduler())
    bot._load_state = lambda _scope: AutomationState(
        signature=(
            f"v2|{game.game_id}:away:strong|{unchanged_game.game_id}:home:lean"
        )
    )
    bot._save_state = lambda _scope, _state: None
    sent: list[tuple[str | None, object | None]] = []

    async def capture_dm(message: str | None = None, *, embed=None):
        sent.append((message, embed))

    bot._send_owner_dm = capture_dm
    scope = MonitorScope(Sport.NFL, 2026, 1)
    monitor = RecommendationMonitor(
        refresh_week=lambda _scope: snapshot,
        load_state=bot._load_state,
        save_state=bot._save_state,
        notify=lambda message: bot._send_monitor_notification(scope, message),
        scope=scope,
    )

    result = await monitor.refresh()

    assert result.changed is True
    assert sent[0][0] is None
    embed = sent[0][1]
    assert embed.title == "🏈 Recommendations Updated"
    assert embed.fields[0].name == "NFL • 2026 — Week 1 — Picks"
    assert embed.fields[0].value == "• 🔥 Strong — ~~Buffalo Bills~~ at **Miami Dolphins**"
    await bot.close()


def test_bot_uses_no_privileged_intents_and_registers_dm_commands(settings):
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())

    assert bot.intents.value == 0
    assert {command.name for command in bot.tree.get_commands()} == {"status", "refresh"}


def test_both_commands_expose_an_optional_details_toggle(settings):
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())

    for command in bot.tree.get_commands():
        details = next(param for param in command.parameters if param.name == "details")
        assert details.required is False
        assert details.type is discord.AppCommandOptionType.boolean


@pytest.mark.asyncio
async def test_setup_hook_logs_bot_ready_after_scheduler_start(settings, records, monkeypatch):
    scheduler = FakeScheduler()
    bot = PickemBot(settings, FakeMonitor(), scheduler=scheduler)

    async def sync():
        return ()

    monkeypatch.setattr(bot.tree, "sync", sync)
    await bot.setup_hook()

    assert scheduler.running is True
    ready = [r for r in records if r["extra"].get("event") == "bot_ready"]
    assert len(ready) == 1
    assert ready[0]["extra"]["owner_id"] == settings.owner_id


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
async def test_rejected_command_logs_warning_without_opening_a_run(settings, records):
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=999)

    await bot.refresh(interaction)

    rejected = [r for r in records if r["extra"].get("event") == "command_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["level"].name == "WARNING"
    assert rejected[0]["extra"]["user_id"] == 999
    assert not any(r["extra"].get("event") == "run_started" for r in records)


@pytest.mark.asyncio
async def test_refresh_command_logs_invocation_and_run_facts(settings, records):
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.refresh(interaction, season=2026, week=1)

    started = next(r for r in records if r["extra"].get("event") == "run_started")
    invoked = next(r for r in records if r["extra"].get("event") == "command_invoked")
    finished = next(r for r in records if r["extra"].get("event") == "run_finished")
    assert started["extra"]["entry"] == "discord:/refresh"
    assert started["extra"]["season"] == 2026
    assert started["extra"]["week"] == 1
    assert started["extra"]["db"] == str(settings.db)
    assert invoked["extra"]["command"] == "refresh"
    assert (
        invoked["extra"]["run_id"]
        == started["extra"]["run_id"]
        == finished["extra"]["run_id"]
    )
    assert not any(r["extra"].get("event") == "command_completed" for r in records)


@pytest.mark.asyncio
async def test_refresh_command_logs_unexpected_failure_once_with_traceback(
    settings, records, monkeypatch
):
    error = RuntimeError("refresh orchestration exploded")
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    async def raise_refresh(_season, _week):
        raise error

    monkeypatch.setattr(bot, "_refresh_scopes", raise_refresh)

    await bot.refresh(interaction, season=2026, week=1)

    failures = [r for r in records if r["extra"].get("event") == "refresh_failed"]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["level"].name == "ERROR"
    assert failure["extra"]["phase"] == "command"
    assert failure["extra"]["command"] == "refresh"
    assert failure["extra"]["entry"] == "discord:/refresh"
    assert failure["extra"]["season"] == 2026
    assert failure["extra"]["week"] == 1
    assert failure["extra"]["db"] == str(settings.db)
    assert failure["extra"]["error_type"] == "RuntimeError"
    assert failure["extra"]["error_detail"] == "refresh orchestration exploded"
    if failure["exception"] is not None:
        exception_type, exception, traceback = failure["exception"]
        assert exception_type is RuntimeError
        assert exception is error
        assert traceback is error.__traceback__
    else:
        # The configured redaction patcher folds and clears exception tuples;
        # the durable record must still retain the traceback text.
        assert "Traceback (most recent call last)" in failure["message"]
        assert "in raise_refresh" in failure["message"]
    assert interaction.events == ["defer", "followup"]


@pytest.mark.asyncio
async def test_status_command_logs_invocation_and_run_facts(settings, records):
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    await bot.status(interaction)

    started = next(r for r in records if r["extra"].get("event") == "run_started")
    invoked = next(r for r in records if r["extra"].get("event") == "command_invoked")
    finished = next(r for r in records if r["extra"].get("event") == "run_finished")
    assert started["extra"]["entry"] == "discord:/status"
    assert started["extra"]["season"] is None
    assert started["extra"]["week"] is None
    assert started["extra"]["db"] == str(settings.db)
    assert invoked["extra"]["command"] == "status"
    assert (
        invoked["extra"]["run_id"]
        == started["extra"]["run_id"]
        == finished["extra"]["run_id"]
    )
    assert not any(r["extra"].get("event") == "command_completed" for r in records)


@pytest.mark.asyncio
async def test_status_failure_is_owned_by_run_context_and_still_replies(
    settings, records, monkeypatch
):
    """Catches /status swallowing a failure and recording the command as successful."""
    error = RuntimeError("status read exploded")
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)

    def raise_status(*_args):
        raise error

    monkeypatch.setattr(bot, "_resolve_scopes", raise_status)

    await bot.status(interaction, season=2026, week=1)

    failures = [r for r in records if r["extra"].get("event") == "run_failed"]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["level"].name == "ERROR"
    assert failure["extra"]["entry"] == "discord:/status"
    assert failure["extra"]["season"] == 2026
    assert failure["extra"]["week"] == 1
    assert failure["extra"]["db"] == str(settings.db)
    if failure["exception"] is not None:
        exception_type, exception, traceback = failure["exception"]
        assert exception_type is RuntimeError
        assert exception is error
        frame_names = []
        while traceback is not None:
            frame_names.append(traceback.tb_frame.f_code.co_name)
            traceback = traceback.tb_next
        assert "raise_status" in frame_names
    else:
        assert "Traceback (most recent call last)" in failure["message"]
        assert "in raise_status" in failure["message"]
    assert len([r for r in records if r["level"].name == "ERROR"]) == 1
    assert not any(r["extra"].get("event") == "run_finished" for r in records)
    assert interaction.response.messages == [("Status unavailable: status read exploded", False)]


@pytest.mark.asyncio
async def test_status_delivery_failure_propagates_without_retrying(settings, records, monkeypatch):
    """Catches response delivery being retried as though status computation failed."""
    error = RuntimeError("status delivery exploded")
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    interaction = FakeInteraction(user_id=settings.owner_id)
    attempts = 0

    async def fail_delivery(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr(interaction.response, "send_message", fail_delivery)

    with pytest.raises(RuntimeError, match="status delivery exploded") as raised:
        await bot.status(interaction)

    assert raised.value is error
    assert attempts == 1
    assert len([r for r in records if r["extra"].get("event") == "run_finished"]) == 1
    assert not any(r["extra"].get("event") == "run_failed" for r in records)


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


def _store_slate(settings, games: tuple[Game, ...]) -> None:
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games(list(games))
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=-3.0,
                    posted_at=game.kickoff_utc,
                )
                for game in games
            ]
        )


@pytest.mark.asyncio
async def test_refresh_names_teams_in_kickoff_order(settings):
    sunday = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    thursday = _nfl_game("DAL", "PHI", datetime(2026, 9, 10, 0, tzinfo=UTC))
    _store_slate(settings, (sunday, thursday))
    snapshot = _snapshot_for(_edge(sunday.game_id), _edge(thursday.game_id))
    interaction = FakeInteraction(user_id=settings.owner_id)
    bot = PickemBot(
        settings,
        FakeMonitor(RefreshResult(changed=True, snapshot=snapshot)),
        scheduler=FakeScheduler(),
    )

    await bot.refresh(interaction)

    assert interaction.followup.embeds[0].fields[0].value == (
        "• 🔥 Strong — ~~Dallas Cowboys~~ at **Philadelphia Eagles**\n"
        "• 🔥 Strong — ~~Buffalo Bills~~ at **Miami Dolphins**"
    )


@pytest.mark.asyncio
async def test_refresh_details_option_restores_reasoning(settings):
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    _store_slate(settings, (game,))
    snapshot = _snapshot_for(_edge(game.game_id, rationale="the market moved three points"))
    interaction = FakeInteraction(user_id=settings.owner_id)
    bot = PickemBot(
        settings,
        FakeMonitor(RefreshResult(changed=True, snapshot=snapshot)),
        scheduler=FakeScheduler(),
    )

    await bot.refresh(interaction, details=True)

    assert "> Why: the market moved three points" in (
        interaction.followup.embeds[0].fields[0].value
    )


@pytest.mark.asyncio
async def test_status_details_option_restores_reasoning(settings, monkeypatch):
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    _store_slate(settings, (game,))
    snapshot = _snapshot_for(_edge(game.game_id, rationale="the market moved three points"))
    monkeypatch.setattr(
        "pickem.discord_bot.generate_recommendations",
        lambda *_args, **_kwargs: snapshot,
    )
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())
    compact = FakeInteraction(user_id=settings.owner_id)
    verbose = FakeInteraction(user_id=settings.owner_id)

    await bot.status(compact)
    await bot.status(verbose, details=True)

    assert "Why:" not in compact.response.embeds[0].fields[0].value
    assert "> Why: the market moved three points" in verbose.response.embeds[0].fields[0].value


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
    monkeypatch.setattr(
        "pickem.discord_bot.generate_recommendations",
        lambda *_args, **_kwargs: snapshot,
    )
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


# --- kickoff-anchored polling -------------------------------------------------

CFB_SCOPE = MonitorScope(Sport.CFB, 2026, 2)
NFL_SCOPE = MonitorScope(Sport.NFL, 2026, 1)
PLAN_NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


def _instant(day: int, hour: int, *, offset: float = 1.0, kickoff_hour: int = 17):
    return PollInstant(
        at=datetime(2026, 9, day, hour, tzinfo=UTC),
        offset_hours=offset,
        kickoff_utc=datetime(2026, 9, day, kickoff_hour, tzinfo=UTC),
    )


def test_settings_read_kickoff_poll_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
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
        "  kickoff_polls:\n"
        "    offsets_hours: [6, 2, 0.5]\n"
        "    plan_every_hours: 4\n"
        "    horizon_days: 3\n"
    )

    settings = DiscordSettings.from_env(config_path=config_path, db=tmp_path / "p.duckdb")

    assert settings.poll_offsets_hours == (6.0, 2.0, 0.5)
    assert settings.poll_plan_every_hours == 4
    assert settings.poll_horizon_days == 3


def test_settings_default_kickoff_polls_when_the_block_is_absent(settings):
    """A config file written before this feature must still start the bot."""
    assert settings.poll_offsets_hours == (12.0, 6.0, 2.0, 1.0)
    assert settings.poll_plan_every_hours == 6
    assert settings.poll_horizon_days == 10


def test_build_schedule_adds_a_poll_planner_alongside_the_daily_jobs(settings):
    scheduler = FakeScheduler()

    build_schedule(settings, FakeMonitor(), lambda _message: None, scheduler, plan=lambda: None)

    assert scheduler.job_ids() == ["pick-reminder", "recommendation-refresh", "poll-planner"]
    assert scheduler.jobs[2].trigger_fields["hour"] == "*/6"


def test_planner_registers_one_job_per_planned_instant():
    scheduler = FakeScheduler()

    ids = schedule_kickoff_polls(
        {NFL_SCOPE: (_instant(13, 5), _instant(13, 16))},
        lambda _scope, _instant: None,
        scheduler,
    )

    assert ids == (
        "poll:nfl:2026:1:2026-09-13T05:00:00+00:00",
        "poll:nfl:2026:1:2026-09-13T16:00:00+00:00",
    )
    assert scheduler.job_ids("poll:") == list(ids)
    assert scheduler.jobs[0].run_date == datetime(2026, 9, 13, 5, tzinfo=UTC)


def test_planner_keeps_two_scopes_apart():
    scheduler = FakeScheduler()

    ids = schedule_kickoff_polls(
        {NFL_SCOPE: (_instant(13, 5),), CFB_SCOPE: (_instant(13, 5),)},
        lambda _scope, _instant: None,
        scheduler,
    )

    assert set(ids) == {
        "poll:nfl:2026:1:2026-09-13T05:00:00+00:00",
        "poll:cfb:2026:2:2026-09-13T05:00:00+00:00",
    }


def test_replanning_the_same_slate_leaves_the_same_jobs():
    scheduler = FakeScheduler()
    plan = {NFL_SCOPE: (_instant(13, 5), _instant(13, 16))}

    first = schedule_kickoff_polls(plan, lambda _s, _i: None, scheduler)
    second = schedule_kickoff_polls(plan, lambda _s, _i: None, scheduler)

    assert first == second
    assert scheduler.job_ids("poll:") == list(second)
    assert scheduler.removed == []


def test_planner_drops_jobs_for_instants_that_left_the_plan():
    """A re-ingested slate that moves a kickoff must not leave its old poll behind."""
    scheduler = FakeScheduler()
    schedule_kickoff_polls(
        {NFL_SCOPE: (_instant(13, 5), _instant(13, 16))}, lambda _s, _i: None, scheduler
    )

    schedule_kickoff_polls({NFL_SCOPE: (_instant(13, 16),)}, lambda _s, _i: None, scheduler)

    assert scheduler.job_ids("poll:") == ["poll:nfl:2026:1:2026-09-13T16:00:00+00:00"]
    assert scheduler.removed == ["poll:nfl:2026:1:2026-09-13T05:00:00+00:00"]


def test_planner_leaves_the_daily_jobs_alone(settings):
    scheduler = FakeScheduler()
    build_schedule(settings, FakeMonitor(), lambda _message: None, scheduler, plan=lambda: None)

    schedule_kickoff_polls({NFL_SCOPE: (_instant(13, 5),)}, lambda _s, _i: None, scheduler)
    schedule_kickoff_polls({}, lambda _s, _i: None, scheduler)

    assert scheduler.job_ids() == ["pick-reminder", "recommendation-refresh", "poll-planner"]


@pytest.mark.asyncio
async def test_a_planned_job_polls_its_own_instant():
    scheduler = FakeScheduler()
    fired: list[tuple[MonitorScope, PollInstant]] = []
    instant = _instant(13, 16)

    schedule_kickoff_polls(
        {NFL_SCOPE: (instant,)},
        lambda scope, poll: fired.append((scope, poll)),
        scheduler,
    )
    await scheduler.jobs[0].func()

    assert fired == [(NFL_SCOPE, instant)]


@pytest.mark.asyncio
async def test_a_kickoff_poll_refreshes_only_its_own_sport(settings):
    """CFB week 2 and NFL week 1 can share a season+week; a poll must not pay for both."""
    add_pick_scope(settings, Sport.CFB)
    add_pick_scope(settings, Sport.NFL)
    monitor = FakeMonitor()
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())

    results = await bot._refresh_scopes(2026, 1, sport=Sport.NFL)

    assert [scope.sport for scope, _ in results] == [Sport.NFL]


@pytest.mark.asyncio
async def test_a_kickoff_poll_excludes_games_already_underway(settings):
    add_pick_scope(settings, Sport.NFL)
    seen: list[object] = []

    class RecordingMonitor:
        async def refresh(self, **kwargs):
            seen.append(kwargs.get("window_start"))
            return RefreshResult(changed=False)

    bot = PickemBot(settings, RecordingMonitor(), scheduler=FakeScheduler())
    instant = _instant(13, 16)

    await bot._run_kickoff_poll(NFL_SCOPE, instant)

    assert seen == [instant.at]


@pytest.mark.asyncio
async def test_the_daily_refresh_keeps_its_lookback_window(settings):
    add_pick_scope(settings, Sport.NFL)
    seen: list[dict] = []

    class RecordingMonitor:
        async def refresh(self, **kwargs):
            seen.append(kwargs)
            return RefreshResult(changed=False)

    bot = PickemBot(settings, RecordingMonitor(), scheduler=FakeScheduler())

    await bot._scheduled_refresh()

    assert [kwargs.get("window_start") for kwargs in seen] == [None]


@pytest.mark.asyncio
async def test_every_automated_refresh_decides_only_games_still_ahead(settings):
    """Both scheduled paths pass a cutoff; a locked pick cannot be acted on."""
    add_pick_scope(settings, Sport.NFL)
    seen: list[dict] = []

    class RecordingMonitor:
        async def refresh(self, **kwargs):
            seen.append(kwargs)
            return RefreshResult(changed=False)

    bot = PickemBot(settings, RecordingMonitor(), scheduler=FakeScheduler())

    await bot._scheduled_refresh()
    await bot._run_kickoff_poll(NFL_SCOPE, _instant(13, 16))

    assert len(seen) == 2
    for kwargs in seen:
        assert isinstance(kwargs["pending_as_of"], datetime)


def _seed_slate(settings, sport: Sport, kickoffs: list[datetime]) -> None:
    """Store a picked week whose games kick off at the given instants."""
    games = [
        Game(
            game_id=f"{sport.value}-2026-01-A{index}-at-B{index}",
            sport=sport,
            season=2026,
            week=1,
            kickoff_utc=kickoff,
            home_team_id=f"B{index}",
            away_team_id=f"A{index}",
        )
        for index, kickoff in enumerate(kickoffs)
    ]
    with Store(settings.db) as store:
        store.init_schema()
        store.upsert_games(games)
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=game.game_id,
                    season=game.season,
                    week=game.week,
                    spread_home=-3.0,
                    posted_at=game.kickoff_utc,
                )
                for game in games
            ]
        )


def test_planning_turns_a_stored_slate_into_one_job_per_instant(settings):
    afternoon = datetime(2026, 9, 13, 17, tzinfo=UTC)
    night = datetime(2026, 9, 14, 0, 20, tzinfo=UTC)
    _seed_slate(settings, Sport.NFL, [afternoon, night])
    scheduler = FakeScheduler()
    bot = PickemBot(settings, FakeMonitor(), scheduler=scheduler)

    ids = bot._plan_kickoff_polls(now=PLAN_NOW)

    # Four offsets against two distinct kickoffs, none of them colliding.
    assert len(ids) == 8
    assert scheduler.job_ids("poll:") == list(ids)
    assert {job.run_date for job in scheduler.jobs} == {
        afternoon - timedelta(hours=h) for h in (12, 6, 2, 1)
    } | {night - timedelta(hours=h) for h in (12, 6, 2, 1)}


def test_planning_charges_one_set_of_polls_for_games_sharing_a_kickoff(settings):
    kickoff = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_slate(settings, Sport.NFL, [kickoff, kickoff, kickoff])
    bot = PickemBot(settings, FakeMonitor(), scheduler=FakeScheduler())

    assert len(bot._plan_kickoff_polls(now=PLAN_NOW)) == 4


def test_planning_skips_a_week_whose_kickoffs_have_all_passed(settings):
    _seed_slate(settings, Sport.NFL, [datetime(2026, 9, 6, 17, tzinfo=UTC)])
    scheduler = FakeScheduler()
    bot = PickemBot(settings, FakeMonitor(), scheduler=scheduler)

    assert bot._plan_kickoff_polls(now=PLAN_NOW) == ()
    assert scheduler.job_ids("poll:") == []


def test_planning_honours_the_configured_offsets(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    config_path = tmp_path / "discord-bot.yaml"
    config_path.write_text(
        "database: pickem.duckdb\n"
        "timezone: America/New_York\n"
        "schedule:\n"
        "  reminder: {day: tue, time: '10:00'}\n"
        "  refresh: {days: [wed], time: '10:00'}\n"
        "  kickoff_polls: {offsets_hours: [2]}\n"
    )
    settings = DiscordSettings.from_env(
        config_path=config_path, db=tmp_path / "pickem.duckdb"
    )
    kickoff = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_slate(settings, Sport.NFL, [kickoff])
    scheduler = FakeScheduler()
    bot = PickemBot(settings, FakeMonitor(), scheduler=scheduler)

    bot._plan_kickoff_polls(now=PLAN_NOW)

    assert [job.run_date for job in scheduler.jobs] == [kickoff - timedelta(hours=2)]


@pytest.mark.asyncio
async def test_a_planned_job_polls_the_scope_that_earned_it(settings):
    _seed_slate(settings, Sport.CFB, [datetime(2026, 9, 13, 17, tzinfo=UTC)])
    seen: list[tuple[Sport, object]] = []

    class RecordingMonitor:
        async def refresh(self, **kwargs):
            seen.append(kwargs.get("window_start"))
            return RefreshResult(changed=False)

    scheduler = FakeScheduler()
    bot = PickemBot(settings, RecordingMonitor(), scheduler=scheduler)
    bot._plan_kickoff_polls(now=PLAN_NOW)

    await scheduler.jobs[0].func()

    assert seen == [scheduler.jobs[0].run_date]


def test_every_job_is_named_for_its_work_not_its_callable(settings):
    """APScheduler quotes job.name in its own records.

    Unnamed closures all render as `_poll_job.<locals>.job`, so a file with a
    slate's worth of polls in it says the same uninformative thing dozens of
    times.
    """
    scheduler = FakeScheduler()
    build_schedule(settings, FakeMonitor(), lambda _message: None, scheduler, plan=lambda: None)
    schedule_kickoff_polls(
        {NFL_SCOPE: (_instant(13, 16),)}, lambda _s, _i: None, scheduler
    )

    assert [job.name for job in scheduler.jobs] == [
        "pick reminder",
        "recommendation refresh",
        "kickoff poll planner",
        "poll nfl/2026/wk1 T-1h before 2026-09-13T17:00:00+00:00",
    ]


def test_planning_records_what_changed_and_when_the_next_poll_fires(records):
    scheduler = FakeScheduler()
    schedule_kickoff_polls(
        {NFL_SCOPE: (_instant(13, 5, offset=12.0), _instant(13, 16, offset=1.0))},
        lambda _s, _i: None,
        scheduler,
    )
    schedule_kickoff_polls(
        {
            NFL_SCOPE: (
                _instant(13, 16, offset=1.0),
                _instant(14, 8, offset=6.0, kickoff_hour=14),
            )
        },
        lambda _s, _i: None,
        scheduler,
    )

    planned = [r for r in records if r["extra"].get("event") == "polls_planned"][-1]
    assert planned["extra"]["jobs"] == 2
    assert planned["extra"]["added"] == 1
    assert planned["extra"]["removed"] == 1
    assert planned["extra"]["unchanged"] == 1
    assert planned["extra"]["next_poll"] == "2026-09-13T16:00:00+00:00"
    assert planned["extra"]["per_scope"] == {"nfl/2026/wk1": 2}
    assert planned["extra"]["offsets_hours"] == [6.0, 1.0]


def test_planning_says_so_when_there_is_nothing_left_to_poll(records):
    """A week correctly finished and a planner that never ran must not look alike."""
    scheduler = FakeScheduler()

    schedule_kickoff_polls({}, lambda _s, _i: None, scheduler)

    planned = [r for r in records if r["extra"].get("event") == "polls_planned"][-1]
    assert planned["extra"]["jobs"] == 0
    assert planned["extra"]["next_poll"] is None
    assert "no kickoff polls" in planned["message"]


@pytest.mark.asyncio
async def test_monitor_refresh_records_recommendation_history(settings, monkeypatch):
    scope = MonitorScope(Sport.NFL, 2026, 1)
    snapshot = RecommendationSnapshot(
        sport=Sport.NFL,
        season=2026,
        week=1,
        generated_at=datetime(2026, 9, 2, tzinfo=UTC),
        edges=(
            Edge(
                game_id="nfl-2026-01-BUF-at-MIA",
                side=Side.HOME,
                delta=3.0,
                tier=Tier.STRONG,
                league_spread=-3.0,
                market_spread=-6.0,
                rationale="league -3.0 vs market -6.0: 3.0 pts toward home",
            ),
        ),
    )
    monkeypatch.setattr(
        "pickem.discord_bot.refresh_recommendations", lambda *args, **kwargs: snapshot
    )
    bot = PickemBot(settings, scheduler=FakeScheduler())
    bot._load_state = lambda _scope: AutomationState()
    bot._save_state = lambda _scope, _state: None

    result = await bot._monitor_for(scope).refresh()

    assert result.error is None
    with Store(settings.db) as store:
        store.init_schema()
        history = store.recommendation_history(["nfl-2026-01-BUF-at-MIA"])
    assert [(r.side, r.tier, r.source) for r in history] == [(Side.HOME, Tier.STRONG, "monitor")]


def test_refresh_embed_shows_only_the_picks_that_changed():
    # The DM already filters to the changed games; the command's own embed
    # printed the whole board, so one refresh read as two different answers.
    changed = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    steady = _nfl_game("DAL", "NYG", datetime(2026, 9, 13, 17, tzinfo=UTC))
    snapshot = _snapshot_for(
        _edge(changed.game_id, rationale="market moved two points"),
        _edge(steady.game_id, rationale="unchanged since Tuesday"),
    )
    scope = MonitorScope(Sport.NFL, 2026, 1)

    embed = _format_refresh_results(
        (
            (
                scope,
                RefreshResult(
                    changed=True,
                    snapshot=snapshot,
                    changed_game_ids=(changed.game_id,),
                ),
            ),
        ),
        games_by_scope={scope: (changed, steady)},
    )
    rendered = "\n".join(field.value for field in embed.fields)

    assert "Miami Dolphins" in rendered
    assert "Giants" not in rendered


def test_refresh_embed_names_the_last_change_when_nothing_changed():
    scope = MonitorScope(Sport.NFL, 2026, 1)

    embed = _format_refresh_results(
        ((scope, RefreshResult(changed=False)),),
        last_change_by_scope={
            scope: LastChange(
                at=datetime(2026, 9, 16, 21, 18, tzinfo=UTC),
                summary="LSU at Ole Miss ✅ Lean → 🔥 Strong",
            )
        },
    )

    assert "LSU at Ole Miss ✅ Lean → 🔥 Strong" in embed.fields[0].value
    assert "5:18 PM" in embed.fields[0].value


def test_refresh_embed_falls_back_when_no_change_was_ever_recorded():
    scope = MonitorScope(Sport.NFL, 2026, 1)

    embed = _format_refresh_results(((scope, RefreshResult(changed=False)),))

    assert embed.fields[0].value == (
        "The latest odds refresh completed with no recommendation changes."
    )


@pytest.mark.asyncio
async def test_a_command_refresh_reports_in_its_reply_without_also_dming(settings):
    # The command's reply already carries the change. A DM as well meant two
    # messages for one refresh.
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    _store_slate(settings, (game,))
    snapshot = _snapshot_for(_edge(game.game_id))
    sent: list[object] = []
    bot = PickemBot(
        settings,
        FakeMonitor(RefreshResult(changed=True, snapshot=snapshot)),
        scheduler=FakeScheduler(),
    )

    async def capture_dm(message=None, *, embed=None):
        sent.append(embed if embed is not None else message)

    bot._send_owner_dm = capture_dm
    await bot._send_monitor_notification(
        MonitorScope(Sport.NFL, 2026, 1),
        RecommendationChange("Recommendations changed: x", snapshot, (game.game_id,)),
    )
    assert len(sent) == 1, "a scheduled refresh still DMs"

    async with bot._command_refresh():
        await bot._send_monitor_notification(
            MonitorScope(Sport.NFL, 2026, 1),
            RecommendationChange("Recommendations changed: x", snapshot, (game.game_id,)),
        )

    assert len(sent) == 1, "a command refresh reports in its reply instead"
    await bot.close()


def test_last_recommendation_change_reads_the_latest_flip_from_history(settings):
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    steady = _nfl_game("DAL", "NYG", datetime(2026, 9, 13, 17, tzinfo=UTC))
    _store_slate(settings, (game, steady))
    first = datetime(2026, 9, 11, 14, tzinfo=UTC)

    def record(game_id, side, tier, at):
        return RecommendationRecord(
            game_id=game_id,
            sport=Sport.NFL,
            season=2026,
            week=1,
            side=side,
            tier=tier,
            edge_points=1.0,
            generated_at=at,
            source=HISTORY_MONITOR,
        )

    with Store(settings.db) as store:
        store.init_schema()
        store.append_recommendation_history(
            [
                # A game's first record is a baseline, never a change.
                record(steady.game_id, Side.HOME, Tier.LEAN, first),
                record(game.game_id, Side.HOME, Tier.COINFLIP, first),
                record(game.game_id, Side.HOME, Tier.COINFLIP, first + timedelta(hours=1)),
                record(game.game_id, Side.HOME, Tier.STRONG, first + timedelta(hours=2)),
            ]
        )

    last = _last_recommendation_change(settings, MonitorScope(Sport.NFL, 2026, 1))

    assert last is not None
    assert last.at == first + timedelta(hours=2)
    assert last.summary == (
        "Buffalo Bills at Miami Dolphins went 🪙 Coinflip home → 🔥 Strong Miami Dolphins"
    )


def test_last_recommendation_change_is_none_before_any_flip(settings):
    game = _nfl_game("BUF", "MIA", datetime(2026, 9, 13, 17, tzinfo=UTC))
    _store_slate(settings, (game,))

    assert _last_recommendation_change(settings, MonitorScope(Sport.NFL, 2026, 1)) is None
