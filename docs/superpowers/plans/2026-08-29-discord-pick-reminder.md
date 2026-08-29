# Discord Pick Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a local, owner-only Discord bot that reminds the owner every Tuesday and monitors weekly recommendation-side changes every Wednesday through Monday.

**Architecture:** Extract non-recording recommendation generation from CLI wiring, then persist monitoring state separately from submitted pick history. A monitor service owns serialized refreshes and notification policy; a thin Discord Gateway adapter owns slash commands, owner authorization, DMs, and Eastern-time scheduling.

**Tech Stack:** Python 3.12, discord.py, APScheduler, DuckDB, Pydantic, Typer, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-29-discord-pick-reminder-design.md`

## Global Constraints

- Read `DISCORD_BOT_TOKEN`, `DISCORD_OWNER_ID`, `PICKEM_SPORT`, `PICKEM_SEASON`, and `PICKEM_WEEK` from the environment; never log tokens.
- Use `America/New_York`; Tuesday reminder is at 10:00 and monitoring occurs at 10:00 Wednesday–Monday.
- Notify only when the canonical game-ID-to-side mapping changes, not when only odds move.
- Monitoring must never invoke the CLI `report` command or write `picks`.
- Restrict commands and notifications to the configured owner ID.
- Keep tests offline, with fake Discord and Odds API collaborators.

## File Structure

- `src/pickem/operations/recommendations.py`: non-recording weekly recommendation generation and fresh-odds refresh composition.
- `src/pickem/store/schema.sql` and `src/pickem/store/db.py`: automation state, isolated from submitted picks.
- `src/pickem/automation/monitor.py`: serialized change detection and failure-notification policy.
- `src/pickem/discord_bot.py`: settings, Discord gateway, commands, scheduler, entrypoint.
- `tests/test_recommendations.py`, `tests/test_monitor.py`, `tests/test_discord_bot.py`: new focused offline tests.
- `deploy/pickem-discord-bot.service` and `docs/runbooks/discord-pick-reminder.md`: local operation.

### Task 1: Extract recommendation generation

**Files:**
- Create: `src/pickem/operations/recommendations.py`
- Modify: `src/pickem/cli.py:125-281`
- Test: `tests/test_recommendations.py`

**Interfaces:**
- Produces `RecommendationSnapshot(sport, season, week, generated_at, edges)`.
- Produces `generate_recommendations(db: Path, sport: Sport, season: int, week: int, now: datetime) -> RecommendationSnapshot`.
- Produces `refresh_recommendations(db: Path, sport: Sport, season: int, week: int, now: datetime) -> RecommendationSnapshot`, which appends the current Odds API snapshot before generating recommendations.
- Consumes `Store.load_week`, `Store.games_before`, `decide_edges`, and `rank_edges`.

- [ ] **Step 1: Write the failing test**

~~~python
def test_generate_recommendations_returns_ranked_edges_without_recording_picks(db, seeded_week):
    snapshot = generate_recommendations(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC)
    )

    assert [edge.game_id for edge in snapshot.edges] == ["nfl:away:home"]
    with Store(db, read_only=True) as store:
        assert store.picks_for_week(2026, 1) == []
~~~

- [ ] **Step 2: Verify it fails**

Run: `uv run pytest tests/test_recommendations.py::test_generate_recommendations_returns_ranked_edges_without_recording_picks -v`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the minimal service**

~~~python
@dataclass(frozen=True)
class RecommendationSnapshot:
    sport: Sport
    season: int
    week: int
    generated_at: datetime
    edges: tuple[Edge, ...]

def generate_recommendations(db: Path, sport: Sport, season: int, week: int,
                             now: datetime) -> RecommendationSnapshot:
    with Store(db) as store:
        dataset = store.load_week(sport, season, week)
        edges = decide_edges(
            dataset.league_lines, dataset.market_lines, dataset.games,
            store.games_before(sport, season, week),
        )
    return RecommendationSnapshot(sport, season, week, now, tuple(rank_edges(edges)))
~~~

Move only sheet formatting and `store.record_picks(...)` into `cli.report`; it calls this service so existing report behavior remains unchanged.
Extract the current `poll-odds` implementation into the refresh service as a polling helper, preserving its slate filtering, kickoff window, exception types, and append-only line write. Make `cli.poll_odds` call that helper and retain its existing output; `refresh_recommendations` calls the same helper then `generate_recommendations`.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_recommendations.py tests/test_report.py tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

`git add src/pickem/operations/recommendations.py src/pickem/cli.py tests/test_recommendations.py && git commit -m "feat: extract non-recording recommendations"`

### Task 2: Store monitoring state

**Files:**
- Modify: `src/pickem/store/schema.sql`, `src/pickem/store/db.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces `AutomationState(signature: str | None, checked_at: datetime | None, error_fingerprint: str | None)`.
- Produces `Store.automation_state(sport, season, week) -> AutomationState` and `Store.save_automation_state(sport, season, week, signature, checked_at, error_fingerprint) -> None`.

- [ ] **Step 1: Write the failing test**

~~~python
def test_automation_state_upsert_is_isolated_from_picks(tmp_path):
    checked_at = datetime(2026, 9, 2, tzinfo=UTC)
    with Store(tmp_path / "pickem.duckdb") as store:
        store.init_schema()
        store.save_automation_state(Sport.NFL, 2026, 1, "game-a:home", checked_at, None)
        state = store.automation_state(Sport.NFL, 2026, 1)

    assert state.signature == "game-a:home"
    assert state.checked_at == checked_at
    assert state.error_fingerprint is None
~~~

- [ ] **Step 2: Verify it fails**

Run: `uv run pytest tests/test_store.py::test_automation_state_upsert_is_isolated_from_picks -v`

Expected: FAIL because the table and methods do not exist.

- [ ] **Step 3: Add the table and methods**

~~~sql
CREATE TABLE IF NOT EXISTS automation_state (
    sport VARCHAR NOT NULL, season INTEGER NOT NULL, week INTEGER NOT NULL,
    recommendation_signature VARCHAR, checked_at TIMESTAMPTZ,
    error_fingerprint VARCHAR, PRIMARY KEY (sport, season, week)
);
~~~

Use `INSERT ... ON CONFLICT ... DO UPDATE` to set all state columns. Do not read or write `picks` in this task.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_store.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

`git add src/pickem/store/schema.sql src/pickem/store/db.py tests/test_store.py && git commit -m "feat: persist recommendation monitor state"`

### Task 3: Implement refresh and notification policy

**Files:**
- Create: `src/pickem/automation/__init__.py`, `src/pickem/automation/monitor.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- `RecommendationMonitor(refresh_week, load_state, save_state, notify, scope)`.
- `await RecommendationMonitor.refresh() -> RefreshResult`, containing `changed`, `snapshot`, and `error`.
- `recommendation_signature(snapshot) -> str`.

- [ ] **Step 1: Write failing policy tests**

~~~python
async def test_refresh_notifies_only_when_the_side_mapping_changes(fake_monitor):
    first = await fake_monitor.refresh()
    unchanged = await fake_monitor.refresh()
    fake_monitor.refresh_week.return_value = snapshot_with({"game-a": Side.AWAY})
    changed = await fake_monitor.refresh()

    assert first.changed is False
    assert unchanged.changed is False
    assert changed.changed is True
    assert fake_monitor.notifications == ["Recommendations changed: game-a → away"]

async def test_refresh_deduplicates_same_failure_until_success(fake_monitor):
    fake_monitor.refresh_week.side_effect = RuntimeError("quota exhausted")
    await fake_monitor.refresh()
    await fake_monitor.refresh()

    assert fake_monitor.notifications == ["Recommendation refresh failed: quota exhausted"]
~~~

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_monitor.py -v`

Expected: FAIL because the monitor module does not exist.

- [ ] **Step 3: Implement exactly the policy**

~~~python
def recommendation_signature(snapshot: RecommendationSnapshot) -> str:
    return "|".join(
        f"{edge.game_id}:{edge.side.value}"
        for edge in sorted(snapshot.edges, key=lambda edge: edge.game_id)
    )

class RecommendationMonitor:
    async def refresh(self) -> RefreshResult:
        async with self._lock:
            try:
                snapshot = await self._refresh_week(self._scope)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return await self._record_failure(type(exc).__name__, str(exc))
            return await self._record_success(snapshot)
~~~

On the first success, persist the signature with `changed=False` and no notification. On a later different signature, persist it and notify only changed game IDs with their old/new sides. Every success clears `error_fingerprint`. Failure fingerprints are the exception type plus message; notify only when that fingerprint changes.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_monitor.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

`git add src/pickem/automation tests/test_monitor.py && git commit -m "feat: monitor recommendation changes"`

### Task 4: Add the Discord bot and schedules

**Files:**
- Create: `src/pickem/discord_bot.py`
- Modify: `src/pickem/config.py`, `pyproject.toml`, `uv.lock`
- Test: `tests/test_discord_bot.py`

**Interfaces:**
- `DiscordSettings.from_env() -> DiscordSettings`.
- `build_schedule(settings, monitor, send_dm, scheduler) -> None`.
- Console entry point: `pickem-discord-bot = "pickem.discord_bot:main"`.

- [ ] **Step 1: Write failing configuration, schedule, and authorization tests**

~~~python
def test_build_schedule_adds_tuesday_reminder_and_wednesday_to_monday_refreshes(settings):
    scheduler = FakeScheduler()
    build_schedule(settings, FakeMonitor(), AsyncMock(), scheduler)

    assert scheduler.jobs[0].trigger_fields == {"day_of_week": "tue", "hour": 10, "minute": 0}
    assert scheduler.jobs[1].trigger_fields == {"day_of_week": "wed-mon", "hour": 10, "minute": 0}
    assert scheduler.jobs[0].timezone.key == "America/New_York"

async def test_status_rejects_non_owner(bot):
    interaction = FakeInteraction(user_id=999)
    await bot.status(interaction)

    assert interaction.response.messages == [("This bot is private.", True)]
~~~

- [ ] **Step 2: Verify they fail**

Run: `uv run pytest tests/test_discord_bot.py -v`

Expected: FAIL because the bot module and dependencies do not exist.

- [ ] **Step 3: Implement the local Gateway bot**

Add `discord.py>=2.6,<3` and `APScheduler>=3.11,<4` to runtime dependencies and `pytest-asyncio` to dev dependencies, then refresh `uv.lock`. Create a no-privileged-intents `commands.Bot` and application-command tree. At startup, synchronize global commands configured for the bot-DM context.

`DiscordSettings.from_env` uses the same required-value policy as `config._required`, converts owner/season/week to `int`, and parses `Sport`. Both handlers reject a non-owner before refreshing or reading status. `/status` reports active scope, next run, last successful check, and signature. `/refresh` awaits the monitor and replies with unchanged/changed/failure result.

Use APScheduler `CronTrigger` objects with `ZoneInfo("America/New_York")`: Tuesday at 10:00 invokes `send_dm("Reminder: submit this week's picks.")`; Wednesday–Monday at 10:00 invokes monitor refresh. `send_dm` fetches only `DISCORD_OWNER_ID` then calls `user.send`.
Compose the monitor with `refresh_week=lambda scope: asyncio.to_thread(refresh_recommendations, settings.db, scope.sport, scope.season, scope.week, datetime.now(UTC))`; never shell out to the Typer CLI.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_discord_bot.py tests/test_config.py tests/test_cli.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

`git add src/pickem/discord_bot.py src/pickem/config.py pyproject.toml uv.lock tests/test_discord_bot.py && git commit -m "feat: add local Discord pick bot"`

### Task 5: Add the service and runbook

**Files:**
- Create: `deploy/pickem-discord-bot.service`, `docs/runbooks/discord-pick-reminder.md`
- Modify: `tests/test_discord_bot.py`

- [ ] **Step 1: Write the failing service-file test**

~~~python
def test_systemd_service_uses_project_directory_and_restart_policy():
    service = Path("deploy/pickem-discord-bot.service").read_text()

    assert "WorkingDirectory=/home/jmiller/cfb" in service
    assert "ExecStart=/usr/bin/env uv run pickem-discord-bot" in service
    assert "Restart=on-failure" in service
~~~

- [ ] **Step 2: Verify it fails**

Run: `uv run pytest tests/test_discord_bot.py::test_systemd_service_uses_project_directory_and_restart_policy -v`

Expected: FAIL because the unit does not exist.

- [ ] **Step 3: Add production setup artifacts**

Create a `Type=simple` unit with `WorkingDirectory=/home/jmiller/cfb`, `EnvironmentFile=/home/jmiller/cfb/.env`, `ExecStart=/usr/bin/env uv run pickem-discord-bot`, `Restart=on-failure`, and `RestartSec=5`.

The runbook must give exact Discord Developer Portal steps: create app/bot; enable user install and bot-DM command context; install to owner account; place the token, owner ID, and active-week values in `.env`; start interactively; copy service to `~/.config/systemd/user/`; run `systemctl --user daemon-reload`, `enable --now pickem-discord-bot`, `status`, and `journalctl`. Include `loginctl enable-linger $USER` and token-rotation guidance.

- [ ] **Step 4: Verify everything**

Run: `uv run ruff check src tests && uv run pytest -v`

Expected: PASS without live Discord or Odds API calls.

- [ ] **Step 5: Commit**

`git add deploy/pickem-discord-bot.service docs/runbooks/discord-pick-reminder.md tests/test_discord_bot.py && git commit -m "docs: add Discord bot service runbook"`
