"""Serialized recommendation refreshes and change notifications."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

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
    # The games behind `changed`, so a caller can report exactly what moved
    # instead of re-deriving it or falling back to the whole board.
    changed_game_ids: tuple[str, ...] = ()


class RecommendationChange(str):
    """A change notification carrying the exact snapshot and games that changed."""

    snapshot: RecommendationSnapshot
    game_ids: tuple[str, ...]

    def __new__(
        cls,
        message: str,
        snapshot: RecommendationSnapshot,
        game_ids: tuple[str, ...],
    ) -> RecommendationChange:
        notification = super().__new__(cls, message)
        notification.snapshot = snapshot
        notification.game_ids = game_ids
        return notification


_RefreshWeek = Callable[[Any], Awaitable[RecommendationSnapshot] | RecommendationSnapshot]
_LoadState = Callable[[Any], Awaitable[AutomationState] | AutomationState]
_SaveState = Callable[[Any, AutomationState], Awaitable[None] | None]
_Notify = Callable[[str], Awaitable[None] | None]
_RecordHistory = Callable[[Any, RecommendationSnapshot], Awaitable[None] | None]


async def _invoke[T](callback: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Invoke an adapter regardless of whether it is sync or async."""
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


_SIGNATURE_VERSION = "v2"
_SIGNATURE_PREFIX = f"{_SIGNATURE_VERSION}|"


def recommendation_signature(snapshot: RecommendationSnapshot) -> str:
    """Return the canonical game-ID-to-pick mapping for a snapshot.

    Side and tier both participate. A coinflip that firms into a lean is worth
    a DM even though the side never moved, and a game that acquires a side it
    did not have is the same event seen from the other end. ``delta``
    deliberately does not participate: an edge growing inside its own tier
    changes no pick, and notifying on it would make every poll noisy.

    The version tag exists so a signature written by an older format is
    recognizable as unreadable rather than mistaken for a different set of
    picks -- see :meth:`RecommendationMonitor._record_success`.
    """
    return _SIGNATURE_PREFIX + "|".join(
        f"{edge.game_id}:{edge.side.value}:{edge.tier.value}"
        for edge in sorted(snapshot.edges, key=lambda edge: edge.game_id)
    )


def _is_current_signature(signature: str | None) -> bool:
    return signature is not None and signature.startswith(_SIGNATURE_PREFIX)


def _mapping_from_signature(signature: str | None) -> dict[str, str]:
    if not _is_current_signature(signature):
        return {}
    mapping: dict[str, str] = {}
    for item in signature[len(_SIGNATURE_PREFIX) :].split("|"):
        if not item:
            continue
        game_id, side, tier = item.rsplit(":", 2)
        mapping[game_id] = f"{tier} {side}"
    return mapping


def _mapping_from_snapshot(snapshot: RecommendationSnapshot) -> dict[str, str]:
    return {
        edge.game_id: f"{edge.tier.value} {edge.side.value}" for edge in snapshot.edges
    }


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
    # Games that left the board are deliberately not reported: automation
    # decides only games that have not kicked off, so a disappearance means
    # "locked, no longer actionable" -- see `_picks_changed`.
    return "Recommendations changed: " + "; ".join(changes)


def _changed_game_ids(
    old_signature: str | None, snapshot: RecommendationSnapshot
) -> tuple[str, ...]:
    """Return games still on the board whose actionable pick changed.

    Only games present in the new snapshot are compared. Automation excludes
    games that have kicked off, so the board shrinks as a week burns down; a
    game dropping out is a pick becoming unactionable, not a pick changing, and
    treating it as news would DM the owner about a game already locked. A
    genuine flip and a newly added game both still register.
    """
    if not _is_current_signature(old_signature):
        return ()
    old = _mapping_from_signature(old_signature)
    new = _mapping_from_snapshot(snapshot)
    return tuple(sorted(game_id for game_id, pick in new.items() if old.get(game_id) != pick))


def _picks_changed(old_signature: str | None, snapshot: RecommendationSnapshot) -> bool:
    return bool(_changed_game_ids(old_signature, snapshot))


def _failure_fingerprint(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _scope_fields(scope: Any) -> dict[str, Any]:
    """Return safe, queryable fields for a monitor scope when available."""
    fields: dict[str, Any] = {}
    for name in ("sport", "season", "week"):
        try:
            value = getattr(scope, name)
        except AttributeError:
            continue
        except Exception:
            # Logging must not turn an unusual adapter scope into a refresh error.
            continue
        if name == "sport":
            try:
                value = getattr(value, "value", value)
            except Exception:
                continue
        fields[name] = value
    return fields


def _scope_label(scope: Any) -> str:
    """Return ``"nfl/2026 wk1"`` for a scope, or ``""`` when it cannot say.

    ``scope`` is an opaque adapter value, so the label is best-effort: the text
    sink shows no bound fields, and two scopes refresh back to back, so a line
    that cannot name its scope is a line that cannot be attributed.
    """
    fields = _scope_fields(scope)
    if not {"sport", "season", "week"} <= fields.keys():
        return ""
    return f"{fields['sport']}/{fields['season']} wk{fields['week']}"


def _for_scope(scope: Any) -> str:
    label = _scope_label(scope)
    return f" for {label}" if label else ""


class RecommendationMonitor:
    """Run recommendation refreshes and notify only meaningful changes.

    Adapter callbacks receive ``scope``. ``load_state`` returns an
    :class:`AutomationState`; ``save_state`` receives the scope and complete
    replacement state. All callbacks may be synchronous or asynchronous.
    ``record_history``, when given, receives the scope and every successful
    snapshot.
    """

    def __init__(
        self,
        refresh_week: _RefreshWeek,
        load_state: _LoadState,
        save_state: _SaveState,
        notify: _Notify,
        scope: Any,
        *,
        record_history: _RecordHistory | None = None,
    ) -> None:
        self._refresh_week = refresh_week
        self._load_state = load_state
        self._save_state = save_state
        self._notify = notify
        self._scope = scope
        self._record_history = record_history
        self._lock = asyncio.Lock()
        self._load_error_fingerprint: str | None = None
        self._persistence_error_fingerprint: str | None = None

    async def refresh(self, **refresh_kwargs: Any) -> RefreshResult:
        """Refresh the active week while serializing all state transitions.

        Keyword arguments are forwarded to ``refresh_week`` untouched, so a
        caller can vary one poll without needing its own monitor and its own
        copy of the change-detection state.
        """
        async with self._lock:
            logger.bind(
                event="refresh_started",
                **_scope_fields(self._scope),
            ).info(f"refresh started{_for_scope(self._scope)}")
            try:
                loaded_state = await _invoke(self._load_state, self._scope)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self._record_load_failure(error)

            self._load_error_fingerprint = None
            state = loaded_state if loaded_state is not None else AutomationState()
            try:
                snapshot = await _invoke(self._refresh_week, self._scope, **refresh_kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self._record_failure(state, error)

            return await self._record_success(state, snapshot)

    async def _record_load_failure(self, error: Exception) -> RefreshResult:
        """Report a state-read failure without overwriting durable state."""
        fingerprint = _failure_fingerprint(error)
        try:
            if self._load_error_fingerprint != fingerprint:
                await _invoke(self._notify, f"Recommendation refresh failed: {error}")
                self._log_notification("load_failure", fingerprint=fingerprint)
                self._load_error_fingerprint = fingerprint
            else:
                self._log_notification_suppressed("load_failure", fingerprint)
        except asyncio.CancelledError:
            raise
        except Exception as notification_error:
            self._log_refresh_failure(notification_error, phase="load_notification")
            return RefreshResult(False, error=notification_error)
        self._log_refresh_failure(error, phase="load")
        return RefreshResult(False, error=error)

    async def _record_failure(self, state: AutomationState, error: Exception) -> RefreshResult:
        fingerprint = _failure_fingerprint(error)
        try:
            if state.error_fingerprint != fingerprint:
                await _invoke(self._notify, f"Recommendation refresh failed: {error}")
                self._log_notification("refresh_failure", fingerprint=fingerprint)
            else:
                self._log_notification_suppressed("refresh_failure", fingerprint)
        except asyncio.CancelledError:
            raise
        except Exception as notification_error:
            self._log_refresh_failure(notification_error, phase="failure_notification")
            return RefreshResult(False, error=notification_error)

        try:
            next_state = state.model_copy(update={"error_fingerprint": fingerprint})
            await _invoke(
                self._save_state,
                self._scope,
                next_state,
            )
            self._log_state_saved(next_state)
        except asyncio.CancelledError:
            raise
        except Exception as persistence_error:
            return await self._record_persistence_failure(persistence_error)
        self._log_refresh_failure(error, phase="refresh")
        return RefreshResult(False, error=error)

    async def _record_persistence_failure(self, error: Exception) -> RefreshResult:
        """Report a checkpoint failure when it cannot be persisted itself."""
        fingerprint = _failure_fingerprint(error)
        try:
            if self._persistence_error_fingerprint != fingerprint:
                await _invoke(self._notify, f"Recommendation refresh failed: {error}")
                self._log_notification("persistence_failure", fingerprint=fingerprint)
                self._persistence_error_fingerprint = fingerprint
            else:
                self._log_notification_suppressed("persistence_failure", fingerprint)
        except asyncio.CancelledError:
            raise
        except Exception as notification_error:
            self._log_refresh_failure(notification_error, phase="persistence_notification")
            return RefreshResult(False, error=notification_error)
        self._log_refresh_failure(error, phase="persistence")
        return RefreshResult(False, error=error)

    async def _record_success(
        self, state: AutomationState, snapshot: RecommendationSnapshot
    ) -> RefreshResult:
        await self._record_history_for(snapshot)
        signature = recommendation_signature(snapshot)
        # A stored signature in an older format is a format change, not news:
        # adopt it as the new baseline silently rather than DMing that every
        # game on the board "changed" the first time the bot runs after a
        # deploy.
        changed_game_ids = _changed_game_ids(state.signature, snapshot)
        changed = bool(changed_game_ids)
        next_state = state.model_copy(
            update={
                "signature": signature,
                "checked_at": snapshot.generated_at,
                "error_fingerprint": None,
            }
        )

        try:
            if changed:
                message = _change_message(state.signature, snapshot)
                logger.bind(
                    event="recommendation_changed",
                    **_scope_fields(self._scope),
                ).info(message)
                await _invoke(
                    self._notify,
                    RecommendationChange(
                        message,
                        snapshot,
                        changed_game_ids,
                    ),
                )
                self._log_notification("recommendation_change")
        except asyncio.CancelledError:
            raise
        except Exception as notification_error:
            self._log_refresh_failure(notification_error, phase="change_notification")
            return RefreshResult(
                changed,
                snapshot=snapshot,
                error=notification_error,
                changed_game_ids=changed_game_ids,
            )

        try:
            await _invoke(self._save_state, self._scope, next_state)
            self._persistence_error_fingerprint = None
            self._log_state_saved(next_state)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            persistence_result = await self._record_persistence_failure(error)
            return RefreshResult(
                changed,
                snapshot=snapshot,
                error=persistence_result.error,
                changed_game_ids=changed_game_ids,
            )

        count = len(snapshot.edges)
        logger.bind(
            event="refresh_succeeded",
            **_scope_fields(self._scope),
            changed=changed,
            edges=count,
        ).info(
            f"refresh succeeded{_for_scope(self._scope)}: "
            f"{count} edge{'' if count == 1 else 's'}, "
            f"{'recommendations changed' if changed else 'unchanged'}"
        )
        return RefreshResult(changed, snapshot=snapshot, changed_game_ids=changed_game_ids)

    async def _record_history_for(self, snapshot: RecommendationSnapshot) -> None:
        """Keep what the model said before anything else can fail.

        A failure is logged, never raised: history is evidence for grading
        later, and losing one refresh's rows must not stop a pick-change DM.
        """
        if self._record_history is None:
            return
        try:
            await _invoke(self._record_history, self._scope, snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.bind(
                event="history_record_failed",
                error_type=type(error).__name__,
                error_detail=str(error),
                **_scope_fields(self._scope),
            ).opt(exception=(type(error), error, error.__traceback__)).error(
                f"recommendation history not recorded{_for_scope(self._scope)}: {error}"
            )

    def _log_notification(self, kind: str, *, fingerprint: str | None = None) -> None:
        fields: dict[str, Any] = {
            "event": "notify_sent",
            "notification_kind": kind,
            **_scope_fields(self._scope),
        }
        if fingerprint is not None:
            fields["fingerprint"] = fingerprint
        logger.bind(**fields).info(
            f"notification sent{_for_scope(self._scope)}: {kind}"
        )

    def _log_notification_suppressed(self, kind: str, fingerprint: str) -> None:
        logger.bind(
            event="notify_suppressed",
            notification_kind=kind,
            fingerprint=fingerprint,
            **_scope_fields(self._scope),
        ).info(
            f"duplicate notification suppressed{_for_scope(self._scope)}: "
            f"{kind} ({fingerprint})"
        )

    def _log_state_saved(self, state: AutomationState) -> None:
        logger.bind(
            event="state_saved",
            signature=state.signature,
            checked_at=state.checked_at,
            error_fingerprint=state.error_fingerprint,
            **_scope_fields(self._scope),
        ).debug("automation state persisted")

    def _log_refresh_failure(self, error: Exception, *, phase: str) -> None:
        logger.bind(
            event="refresh_failed",
            phase=phase,
            error_type=type(error).__name__,
            error_detail=str(error),
            **_scope_fields(self._scope),
        ).opt(
            exception=(type(error), error, error.__traceback__),
        ).error(f"refresh failed: {error}")


__all__ = [
    "MonitorScope",
    "RecommendationChange",
    "RecommendationMonitor",
    "RefreshResult",
    "recommendation_signature",
]
