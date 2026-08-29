import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pickem.automation.monitor import (
    RecommendationMonitor,
    recommendation_signature,
)
from pickem.models import Edge, Side, Tier
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import AutomationState

SCOPE = object()
GENERATED_AT = datetime(2026, 9, 2, 14, tzinfo=UTC)


def edge(game_id: str, side: Side, *, delta: float = 3.0) -> Edge:
    return Edge(
        game_id=game_id,
        side=side,
        delta=delta,
        tier=Tier.LEAN,
        league_spread=-3.0,
        market_spread=-6.0,
        rationale="test",
    )


def snapshot_with(mapping: dict[str, Side], *, generated_at: datetime = GENERATED_AT):
    return RecommendationSnapshot(
        sport="nfl",
        season=2026,
        week=1,
        generated_at=generated_at,
        edges=tuple(edge(game_id, side) for game_id, side in mapping.items()),
    )


class FakeMonitor:
    def __init__(self, snapshots: list[RecommendationSnapshot]):
        self.snapshots = list(snapshots)
        self.state = AutomationState()
        self.saved_states: list[AutomationState] = []
        self.notifications: list[str] = []

    async def refresh_week(self, _scope):
        return self.snapshots.pop(0)

    def load_state(self, _scope):
        return self.state

    def save_state(self, _scope, state):
        self.state = state
        self.saved_states.append(state)

    async def notify(self, message: str):
        self.notifications.append(message)

    def monitor(self):
        return RecommendationMonitor(
            self.refresh_week,
            self.load_state,
            self.save_state,
            self.notify,
            SCOPE,
        )


def test_recommendation_signature_is_sorted_and_ignores_non_side_fields():
    first = snapshot_with({"game-b": Side.AWAY, "game-a": Side.HOME})
    second = replace(
        first,
        edges=(
            first.edges[0].model_copy(update={"delta": 99.0, "rationale": "different"}),
            first.edges[1].model_copy(update={"delta": -99.0, "rationale": "different"}),
        ),
    )

    assert recommendation_signature(first) == "game-a:home|game-b:away"
    assert recommendation_signature(second) == recommendation_signature(first)


def test_refresh_notifies_only_when_the_side_mapping_changes():
    fake = FakeMonitor(
        [
            snapshot_with({"game-a": Side.HOME}),
            snapshot_with({"game-a": Side.HOME}, generated_at=GENERATED_AT.replace(minute=1)),
            snapshot_with({"game-a": Side.AWAY}, generated_at=GENERATED_AT.replace(minute=2)),
        ]
    )
    monitor = fake.monitor()

    first, unchanged, changed = [asyncio.run(monitor.refresh()) for _ in range(3)]

    assert first.changed is False
    assert unchanged.changed is False
    assert changed.changed is True
    assert fake.notifications == ["Recommendations changed: changed: game-a home → away"]
    assert fake.state.signature == "game-a:away"
    assert fake.state.error_fingerprint is None


def test_refresh_describes_added_and_removed_games():
    fake = FakeMonitor(
        [
            snapshot_with({"game-a": Side.HOME, "game-b": Side.AWAY}),
            snapshot_with({"game-b": Side.HOME, "game-c": Side.AWAY}),
        ]
    )

    asyncio.run(fake.monitor().refresh())
    result = asyncio.run(fake.monitor().refresh())

    assert result.changed is True
    assert fake.notifications == [
        "Recommendations changed: added: game-c → away; changed: game-b away → home; "
        "removed: game-a (was home)"
    ]


def test_refresh_delivers_a_changed_notice_before_saving_the_new_signature():
    fake = FakeMonitor(
        [
            snapshot_with({"game-a": Side.HOME}),
            snapshot_with({"game-a": Side.AWAY}),
        ]
    )
    events: list[str] = []

    async def notify(message: str):
        events.append(f"notify:{message}")

    def save_state(_scope, state):
        events.append(f"save:{state.signature}")
        fake.save_state(_scope, state)

    monitor = RecommendationMonitor(
        fake.refresh_week,
        fake.load_state,
        save_state,
        notify,
        SCOPE,
    )

    asyncio.run(monitor.refresh())
    asyncio.run(monitor.refresh())

    assert events == [
        "save:game-a:home",
        "notify:Recommendations changed: changed: game-a home → away",
        "save:game-a:away",
    ]


def test_refresh_keeps_old_baseline_when_changed_notice_fails():
    fake = FakeMonitor(
        [
            snapshot_with({"game-a": Side.HOME}),
            snapshot_with({"game-a": Side.AWAY}),
            snapshot_with({"game-a": Side.AWAY}),
        ]
    )
    failures = 0

    async def notify(_message: str):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("DM unavailable")

    monitor = RecommendationMonitor(
        fake.refresh_week,
        fake.load_state,
        fake.save_state,
        notify,
        SCOPE,
    )

    asyncio.run(monitor.refresh())
    failed = asyncio.run(monitor.refresh())
    retried = asyncio.run(monitor.refresh())

    assert failed.changed is True
    assert failed.error is not None
    assert retried.changed is True
    assert fake.state.signature == "game-a:away"
    assert failures == 2


def test_refresh_preserves_baseline_when_state_loading_fails():
    checked_at = GENERATED_AT.replace(minute=30)
    persisted = {
        "state": AutomationState(
            signature="game-a:home",
            checked_at=checked_at,
            error_fingerprint=None,
        )
    }
    load_attempts = 0
    notifications: list[str] = []

    def load_state(_scope):
        nonlocal load_attempts
        load_attempts += 1
        if load_attempts == 1:
            raise OSError("state unavailable")
        return persisted["state"]

    def save_state(_scope, state):
        persisted["state"] = state

    snapshots = iter(
        [
            snapshot_with({"game-a": Side.AWAY}),
        ]
    )

    async def refresh_week(_scope):
        return next(snapshots)

    async def notify(message: str):
        notifications.append(message)

    monitor = RecommendationMonitor(
        refresh_week,
        load_state,
        save_state,
        notify,
        SCOPE,
    )

    failed = asyncio.run(monitor.refresh())
    assert failed.error is not None
    assert persisted["state"].signature == "game-a:home"

    changed = asyncio.run(monitor.refresh())

    assert changed.changed is True
    assert notifications[-1] == "Recommendations changed: changed: game-a home → away"
    assert persisted["state"].signature == "game-a:away"


def test_refresh_notifies_once_when_initial_state_save_fails():
    fake = FakeMonitor([snapshot_with({"game-a": Side.HOME})])
    notifications: list[str] = []
    save_attempts = 0

    def failing_save(_scope, _state):
        nonlocal save_attempts
        save_attempts += 1
        raise OSError("checkpoint unavailable")

    async def notify(message: str):
        notifications.append(message)

    monitor = RecommendationMonitor(
        fake.refresh_week,
        fake.load_state,
        failing_save,
        notify,
        SCOPE,
    )

    first = asyncio.run(monitor.refresh())
    # Reuse the same snapshot to exercise the same failed checkpoint again.
    fake.snapshots.append(snapshot_with({"game-a": Side.HOME}))
    second = asyncio.run(monitor.refresh())

    assert first.error is not None
    assert second.error is not None
    assert save_attempts == 2
    assert notifications == ["Recommendation refresh failed: checkpoint unavailable"]


def test_refresh_notifies_once_when_unchanged_state_save_fails():
    fake = FakeMonitor(
        [
            snapshot_with({"game-a": Side.HOME}),
            snapshot_with({"game-a": Side.HOME}, generated_at=GENERATED_AT.replace(minute=1)),
            snapshot_with({"game-a": Side.HOME}, generated_at=GENERATED_AT.replace(minute=2)),
        ]
    )
    notifications: list[str] = []
    save_attempts = 0

    def save_state(scope, state):
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts > 1:
            raise OSError("checkpoint unavailable")
        fake.save_state(scope, state)

    async def notify(message: str):
        notifications.append(message)

    monitor = RecommendationMonitor(
        fake.refresh_week,
        fake.load_state,
        save_state,
        notify,
        SCOPE,
    )

    first = asyncio.run(monitor.refresh())
    second = asyncio.run(monitor.refresh())
    third = asyncio.run(monitor.refresh())

    assert first.error is None
    assert second.error is not None
    assert third.error is not None
    assert notifications == ["Recommendation refresh failed: checkpoint unavailable"]


def test_refresh_deduplicates_changed_checkpoint_failure_but_retries_change_notice():
    fake = FakeMonitor(
        [
            snapshot_with({"game-a": Side.HOME}),
            snapshot_with({"game-a": Side.AWAY}),
            snapshot_with({"game-a": Side.AWAY}),
        ]
    )
    notifications: list[str] = []

    def save_state(scope, state):
        if state.signature == "game-a:away":
            raise OSError("checkpoint unavailable")
        fake.save_state(scope, state)

    async def notify(message: str):
        notifications.append(message)

    monitor = RecommendationMonitor(
        fake.refresh_week,
        fake.load_state,
        save_state,
        notify,
        SCOPE,
    )

    asyncio.run(monitor.refresh())
    first_change = asyncio.run(monitor.refresh())
    second_change = asyncio.run(monitor.refresh())

    assert first_change.changed is True
    assert second_change.changed is True
    assert notifications == [
        "Recommendations changed: changed: game-a home → away",
        "Recommendation refresh failed: checkpoint unavailable",
        "Recommendations changed: changed: game-a home → away",
    ]


def test_refresh_deduplicates_same_failure_until_success():
    fake = FakeMonitor([snapshot_with({"game-a": Side.HOME})])
    calls = 0

    async def failing_refresh(_scope):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("quota exhausted")
        return fake.snapshots.pop(0)

    fake.refresh_week = failing_refresh
    monitor = fake.monitor()

    first = asyncio.run(monitor.refresh())
    second = asyncio.run(monitor.refresh())
    third = asyncio.run(monitor.refresh())

    assert calls == 3
    assert first.error is not None
    assert second.error is not None
    assert third.error is None
    assert fake.notifications == ["Recommendation refresh failed: quota exhausted"]
    assert fake.state.error_fingerprint is None


def test_refresh_notifies_again_when_failure_fingerprint_changes():
    fake = FakeMonitor([])
    messages = iter(["quota exhausted", "network down"])

    async def failing_refresh(_scope):
        raise RuntimeError(next(messages))

    fake.refresh_week = failing_refresh
    monitor = fake.monitor()

    asyncio.run(monitor.refresh())
    asyncio.run(monitor.refresh())

    assert fake.notifications == [
        "Recommendation refresh failed: quota exhausted",
        "Recommendation refresh failed: network down",
    ]


def test_refresh_serializes_overlapping_calls():
    snapshots = [snapshot_with({"game-a": Side.HOME}) for _ in range(2)]
    active = 0
    maximum = 0

    async def refresh_week(_scope):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return snapshots.pop(0)

    fake = FakeMonitor([])
    fake.refresh_week = refresh_week
    monitor = fake.monitor()

    async def run_both():
        return await asyncio.gather(monitor.refresh(), monitor.refresh())

    asyncio.run(run_both())

    assert maximum == 1


def test_refresh_rethrows_cancellation():
    fake = FakeMonitor([])

    async def cancelled(_scope):
        raise asyncio.CancelledError

    fake.refresh_week = cancelled

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fake.monitor().refresh())
