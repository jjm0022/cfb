"""Serialized recommendation refreshes and change notifications."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pickem.models import Sport
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import AutomationState


@dataclass(frozen=True)
class MonitorScope:
    """The active weekly slice monitored by a :class:`RecommendationMonitor`."""

    sport: Sport
    season: int
    week: int


@dataclass(frozen=True)
class RefreshResult:
    """Outcome of one serialized recommendation refresh."""

    changed: bool
    snapshot: RecommendationSnapshot | None = None
    error: BaseException | None = None


_RefreshWeek = Callable[[Any], Awaitable[RecommendationSnapshot] | RecommendationSnapshot]
_LoadState = Callable[[Any], Awaitable[AutomationState] | AutomationState]
_SaveState = Callable[[Any, AutomationState], Awaitable[None] | None]
_Notify = Callable[[str], Awaitable[None] | None]


async def _invoke[T](callback: Callable[..., T], *args: Any) -> T:
    """Invoke an adapter regardless of whether it is sync or async."""
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def recommendation_signature(snapshot: RecommendationSnapshot) -> str:
    """Return the canonical game-ID-to-side mapping for a snapshot."""
    return "|".join(
        f"{edge.game_id}:{edge.side.value}"
        for edge in sorted(snapshot.edges, key=lambda edge: edge.game_id)
    )


def _mapping_from_signature(signature: str | None) -> dict[str, str]:
    if not signature:
        return {}
    mapping: dict[str, str] = {}
    for item in signature.split("|"):
        game_id, side = item.rsplit(":", 1)
        mapping[game_id] = side
    return mapping


def _mapping_from_snapshot(snapshot: RecommendationSnapshot) -> dict[str, str]:
    return {edge.game_id: edge.side.value for edge in snapshot.edges}


def _change_message(old_signature: str | None, snapshot: RecommendationSnapshot) -> str:
    old = _mapping_from_signature(old_signature)
    new = _mapping_from_snapshot(snapshot)

    changes = [
        f"added: {game_id} → {new[game_id]}"
        for game_id in sorted(new.keys() - old.keys())
    ]
    changes.extend(
        f"changed: {game_id} {old[game_id]} → {new[game_id]}"
        for game_id in sorted(old.keys() & new.keys())
        if old[game_id] != new[game_id]
    )
    changes.extend(
        f"removed: {game_id} (was {old[game_id]})"
        for game_id in sorted(old.keys() - new.keys())
    )
    return "Recommendations changed: " + "; ".join(changes)


def _failure_fingerprint(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


class RecommendationMonitor:
    """Run recommendation refreshes and notify only meaningful changes.

    Adapter callbacks receive ``scope``. ``load_state`` returns an
    :class:`AutomationState`; ``save_state`` receives the scope and complete
    replacement state. All callbacks may be synchronous or asynchronous.
    """

    def __init__(
        self,
        refresh_week: _RefreshWeek,
        load_state: _LoadState,
        save_state: _SaveState,
        notify: _Notify,
        scope: Any,
    ) -> None:
        self._refresh_week = refresh_week
        self._load_state = load_state
        self._save_state = save_state
        self._notify = notify
        self._scope = scope
        self._lock = asyncio.Lock()

    async def refresh(self) -> RefreshResult:
        """Refresh the active week while serializing all state transitions."""
        async with self._lock:
            state = AutomationState()
            try:
                loaded_state = await _invoke(self._load_state, self._scope)
                if loaded_state is not None:
                    state = loaded_state
                snapshot = await _invoke(self._refresh_week, self._scope)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self._record_failure(state, error)

            return await self._record_success(state, snapshot)

    async def _record_failure(self, state: AutomationState, error: Exception) -> RefreshResult:
        fingerprint = _failure_fingerprint(error)
        try:
            if state.error_fingerprint != fingerprint:
                await _invoke(self._notify, f"Recommendation refresh failed: {error}")
            await _invoke(
                self._save_state,
                self._scope,
                state.model_copy(update={"error_fingerprint": fingerprint}),
            )
        except asyncio.CancelledError:
            raise
        except Exception as persistence_error:
            return RefreshResult(False, error=persistence_error)
        return RefreshResult(False, error=error)

    async def _record_success(
        self, state: AutomationState, snapshot: RecommendationSnapshot
    ) -> RefreshResult:
        signature = recommendation_signature(snapshot)
        changed = state.signature is not None and state.signature != signature
        next_state = state.model_copy(
            update={
                "signature": signature,
                "checked_at": snapshot.generated_at,
                "error_fingerprint": None,
            }
        )

        try:
            if changed:
                await _invoke(self._notify, _change_message(state.signature, snapshot))
            await _invoke(self._save_state, self._scope, next_state)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return RefreshResult(changed, snapshot=snapshot, error=error)

        return RefreshResult(changed, snapshot=snapshot)


__all__ = [
    "MonitorScope",
    "RecommendationMonitor",
    "RefreshResult",
    "recommendation_signature",
]
