import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from traceback import format_tb

import pytest

from pickem.automation.monitor import (
    MonitorScope,
    RecommendationMonitor,
    recommendation_signature,
)
from pickem.models import Edge, Side, Sport, Tier
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


def _events(records, event: str):
    return [record for record in records if record["extra"].get("event") == event]


def _assert_one_refresh_failure(
    records,
    *,
    phase: str,
    error_type: str,
    error_detail: str,
    scope: MonitorScope,
):
    failures = _events(records, "refresh_failed")
    assert len(failures) == 1
    failure = failures[0]
    assert failure["level"].name == "ERROR"
    assert failure["extra"]["phase"] == phase
    assert failure["extra"]["error_type"] == error_type
    assert failure["extra"]["error_detail"] == error_detail
    assert failure["extra"]["sport"] == scope.sport.value
    assert failure["extra"]["season"] == scope.season
    assert failure["extra"]["week"] == scope.week
    if failure["exception"] is not None:
        exception_type, exception, traceback = failure["exception"]
        assert exception_type is type(exception)
        assert isinstance(exception, BaseException)
        assert traceback is not None
        assert format_tb(traceback)
    else:
        # A configured Loguru patcher folds and clears the tuple before the
        # records fixture receives it; the rendered traceback remains durable.
        assert "Traceback (most recent call last)" in failure["message"]


def test_repeat_failure_logs_info_suppression_and_one_traceback_failure(records):
    scope = MonitorScope(Sport.NFL, 2026, 1)
    state = AutomationState(error_fingerprint="RuntimeError: boom")

    async def raising_refresh(_scope):
        raise RuntimeError("boom")

    def fail_if_called(_message):
        raise AssertionError("duplicate notification should be suppressed")

    monitor = RecommendationMonitor(
        refresh_week=raising_refresh,
        load_state=lambda _scope: state,
        save_state=lambda _scope, _new_state: None,
        notify=fail_if_called,
        scope=scope,
    )

    result = asyncio.run(monitor.refresh())

    assert isinstance(result.error, RuntimeError)
    suppressed = _events(records, "notify_suppressed")
    assert len(suppressed) == 1
    assert suppressed[0]["level"].name == "INFO"
    assert suppressed[0]["extra"]["fingerprint"] == "RuntimeError: boom"
    assert suppressed[0]["extra"]["sport"] == "nfl"
    assert suppressed[0]["extra"]["season"] == 2026
    assert suppressed[0]["extra"]["week"] == 1
    _assert_one_refresh_failure(
        records,
        phase="refresh",
        error_type="RuntimeError",
        error_detail="boom",
        scope=scope,
    )


def test_successful_refresh_logs_change_notification_save_and_success(records):
    scope = MonitorScope(Sport.NFL, 2026, 1)
    snapshot = snapshot_with({"game-a": Side.AWAY})
    notifications: list[str] = []

    async def notify(message: str):
        notifications.append(message)

    saved: list[AutomationState] = []
    monitor = RecommendationMonitor(
        refresh_week=lambda _scope: snapshot,
        load_state=lambda _scope: AutomationState(signature="game-a:home"),
        save_state=lambda _scope, state: saved.append(state),
        notify=notify,
        scope=scope,
    )

    result = asyncio.run(monitor.refresh())

    assert result.changed is True
    assert result.error is None
    assert notifications == ["Recommendations changed: changed: game-a home → away"]
    assert len(saved) == 1

    started = _events(records, "refresh_started")
    assert len(started) == 1
    assert started[0]["level"].name == "INFO"
    assert started[0]["extra"]["sport"] == "nfl"
    assert started[0]["extra"]["season"] == 2026
    assert started[0]["extra"]["week"] == 1

    changed = _events(records, "recommendation_changed")
    assert len(changed) == 1
    assert changed[0]["level"].name == "INFO"
    assert changed[0]["message"] == "Recommendations changed: changed: game-a home → away"

    sent = _events(records, "notify_sent")
    assert len(sent) == 1
    assert sent[0]["level"].name == "INFO"
    assert sent[0]["extra"]["notification_kind"] == "recommendation_change"

    state_saved = _events(records, "state_saved")
    assert len(state_saved) == 1
    assert state_saved[0]["level"].name == "DEBUG"
    assert state_saved[0]["extra"]["signature"] == "game-a:away"
    assert state_saved[0]["extra"]["checked_at"] == GENERATED_AT

    succeeded = _events(records, "refresh_succeeded")
    assert len(succeeded) == 1
    assert succeeded[0]["level"].name == "INFO"
    assert succeeded[0]["extra"]["changed"] is True
    assert succeeded[0]["extra"]["edges"] == 1

    event_names = [record["extra"].get("event") for record in records]
    assert event_names.index("refresh_started") < event_names.index("recommendation_changed")
    assert event_names.index("recommendation_changed") < event_names.index("notify_sent")
    assert event_names.index("notify_sent") < event_names.index("state_saved")
    assert event_names.index("state_saved") < event_names.index("refresh_succeeded")


@pytest.mark.parametrize(
    ("scenario", "phase", "error_type", "error_detail"),
    [
        ("load", "load", "OSError", "state unavailable"),
        ("refresh", "refresh", "RuntimeError", "refresh unavailable"),
        (
            "failure_notification",
            "failure_notification",
            "ConnectionError",
            "DM unavailable",
        ),
        ("persistence", "persistence", "OSError", "checkpoint unavailable"),
        (
            "persistence_notification",
            "persistence_notification",
            "ConnectionError",
            "persistence DM unavailable",
        ),
        (
            "change_notification",
            "change_notification",
            "ConnectionError",
            "change DM unavailable",
        ),
    ],
)
def test_each_returned_error_phase_logs_once_with_its_traceback(
    records, scenario, phase, error_type, error_detail
):
    scope = MonitorScope(Sport.NFL, 2026, 1)
    snapshot = snapshot_with({"game-a": Side.AWAY})
    state = AutomationState(signature="game-a:home")
    notify_calls = 0

    async def refresh_week(_scope):
        failing_scenarios = {
            "refresh",
            "failure_notification",
            "persistence",
            "persistence_notification",
        }
        if scenario in failing_scenarios:
            raise RuntimeError("refresh unavailable")
        return snapshot

    def load_state(_scope):
        if scenario == "load":
            raise OSError("state unavailable")
        return state

    def save_state(_scope, _new_state):
        if scenario in {"persistence", "persistence_notification"}:
            raise OSError("checkpoint unavailable")

    async def notify(_message: str):
        nonlocal notify_calls
        notify_calls += 1
        if scenario == "failure_notification":
            raise ConnectionError("DM unavailable")
        if scenario == "persistence_notification" and notify_calls == 2:
            raise ConnectionError("persistence DM unavailable")
        if scenario == "change_notification":
            raise ConnectionError("change DM unavailable")

    monitor = RecommendationMonitor(
        refresh_week=refresh_week,
        load_state=load_state,
        save_state=save_state,
        notify=notify,
        scope=scope,
    )

    result = asyncio.run(monitor.refresh())

    assert result.error is not None
    if scenario == "failure_notification":
        assert isinstance(result.error, ConnectionError)
    elif scenario == "persistence_notification":
        assert isinstance(result.error, ConnectionError)
    elif scenario == "change_notification":
        assert isinstance(result.error, ConnectionError)
    elif scenario == "persistence":
        assert isinstance(result.error, OSError)
    elif scenario == "load":
        assert isinstance(result.error, OSError)
    else:
        assert isinstance(result.error, RuntimeError)
    _assert_one_refresh_failure(
        records,
        phase=phase,
        error_type=error_type,
        error_detail=error_detail,
        scope=scope,
    )


def test_successful_state_save_clears_persistence_deduplication(records):
    scope = MonitorScope(Sport.NFL, 2026, 1)
    snapshot = snapshot_with({"game-a": Side.HOME})
    saved: list[AutomationState] = []
    monitor = RecommendationMonitor(
        refresh_week=lambda _scope: snapshot,
        load_state=lambda _scope: AutomationState(),
        save_state=lambda _scope, state: saved.append(state),
        notify=lambda _message: None,
        scope=scope,
    )

    result = asyncio.run(monitor.refresh())

    assert result.error is None
    assert len(_events(records, "state_saved")) == 1
    assert saved[0].signature == "game-a:home"
