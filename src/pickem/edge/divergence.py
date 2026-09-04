"""The strategy: exploit the gap between a frozen league line and the live market.

Pure functions only. No network, no database, no filesystem — which is what
makes the whole strategy testable offline and replayable in the backtest.

Sign convention (everything is home-perspective):

    delta = league_spread - market_spread

    delta > 0  ->  we hold the home side at a better price than the market's
    delta < 0  ->  we hold the away side at a better price
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from statistics import median

from loguru import logger
from pydantic import BaseModel

from pickem.models import Edge, LeagueLine, MarketLine, Side, Tier


class Thresholds(BaseModel):
    """Tier cutoffs in points of divergence.

    These defaults are initial guesses, to be replaced by backtested values.
    They are configuration, not logic.
    """

    strong: float = 2.0
    lean: float = 1.0


@dataclass(frozen=True)
class _DecisionEvent:
    level: str
    message: str
    fields: dict[str, object]


_DECISION_LOGGING_ENABLED: ContextVar[bool] = ContextVar(
    "pickem_decision_logging_enabled", default=True
)
_DECISION_LOG_BUFFER: ContextVar[list[_DecisionEvent] | None] = ContextVar(
    "pickem_decision_log_buffer", default=None
)


def _decision_logging_enabled() -> bool:
    """Return whether decision-layer records are enabled in this context."""
    return _DECISION_LOGGING_ENABLED.get()


def _write_decision_event(event: _DecisionEvent) -> None:
    logger.bind(**event.fields).log(event.level, event.message)


def _emit_decision_event(level: str, message: str, **fields: object) -> None:
    """Emit immediately or retain a decision record for a successful pipeline."""
    if not _decision_logging_enabled():
        return
    event = _DecisionEvent(level, message, fields)
    buffer = _DECISION_LOG_BUFFER.get()
    if buffer is None:
        _write_decision_event(event)
    else:
        buffer.append(event)


@contextmanager
def _buffer_decision_logging() -> Iterator[None]:
    """Commit decision records only when the enclosed operation succeeds."""
    parent = _DECISION_LOG_BUFFER.get()
    token = _DECISION_LOG_BUFFER.set([])
    try:
        yield
    except BaseException:
        _DECISION_LOG_BUFFER.reset(token)
        raise
    else:
        buffered = _DECISION_LOG_BUFFER.get()
        _DECISION_LOG_BUFFER.reset(token)
        if parent is not None:
            parent.extend(buffered or [])
        elif _decision_logging_enabled():
            for event in buffered or []:
                _write_decision_event(event)


@contextmanager
def suppress_decision_logging() -> Iterator[None]:
    """Temporarily suppress decision-layer records in the current context.

    Backtesting and calibration can call the decision engine repeatedly without
    producing a record for every replay. Context-local state keeps concurrent
    runs isolated, and resetting the token restores the prior setting for
    nested contexts and exceptions.
    """
    token = _DECISION_LOGGING_ENABLED.set(False)
    try:
        yield
    finally:
        _DECISION_LOGGING_ENABLED.reset(token)


def consensus_spread(lines: Sequence[MarketLine]) -> float | None:
    """Median spread across books, using each book's most recent snapshot.

    Median rather than mean so one stale or erroneous book cannot drag the
    consensus. Collapsing per book first stops a frequently-polled book from
    outvoting the rest.
    """
    if not lines:
        return None
    latest: dict[str, MarketLine] = {}
    for line in lines:
        current = latest.get(line.book)
        if current is None or line.captured_at > current.captured_at:
            latest[line.book] = line
    result = median(line.spread_home for line in latest.values())
    _emit_decision_event(
        "DEBUG",
        f"consensus {result:+.1f} across {len(latest)} books",
        event="consensus_computed",
        game_id=next(iter(latest.values())).game_id,
        consensus=result,
        books_used=len(latest),
        snapshots_collapsed=len(lines) - len(latest),
        per_book={book: line.spread_home for book, line in latest.items()},
    )
    return result


def _tier(delta: float, thresholds: Thresholds) -> Tier:
    magnitude = abs(delta)
    if magnitude >= thresholds.strong:
        return Tier.STRONG
    if magnitude >= thresholds.lean:
        return Tier.LEAN
    return Tier.COINFLIP


# Internal measurement stage. COINFLIP and NO_MARKET carry a temporary side
# until pipeline.decide_edges resolves them. Callers must use decide_edges.
def _compute_edge(
    league: LeagueLine,
    market: Sequence[MarketLine],
    thresholds: Thresholds | None = None,
) -> Edge:
    thresholds = thresholds or Thresholds()
    consensus = consensus_spread(market)

    if consensus is None:
        # Never skipped. A game with no market is surfaced as NO_MARKET so the
        # caller can fall through to the tiebreak rating with its eyes open.
        _emit_decision_event(
            "DEBUG",
            "no market line available",
            event="edge_measured",
            game_id=league.game_id,
            tier=Tier.NO_MARKET.value,
            league_spread=league.spread_home,
            market_spread=None,
            delta=0.0,
            threshold_strong=thresholds.strong,
            threshold_lean=thresholds.lean,
        )
        return Edge(
            game_id=league.game_id,
            side=Side.HOME,
            delta=0.0,
            tier=Tier.NO_MARKET,
            league_spread=league.spread_home,
            market_spread=None,
            rationale="no market line available; falls through to tiebreak rating",
        )

    delta = league.spread_home - consensus
    side = Side.HOME if delta > 0 else Side.AWAY
    moved_toward = "home" if delta > 0 else "away"
    tier = _tier(delta, thresholds)
    _emit_decision_event(
        "DEBUG",
        f"{abs(delta):.1f} pts toward {moved_toward}",
        event="edge_measured",
        game_id=league.game_id,
        tier=tier.value,
        side=side.value,
        league_spread=league.spread_home,
        market_spread=consensus,
        delta=delta,
        threshold_strong=thresholds.strong,
        threshold_lean=thresholds.lean,
    )
    return Edge(
        game_id=league.game_id,
        side=side,
        delta=delta,
        tier=tier,
        league_spread=league.spread_home,
        market_spread=consensus,
        rationale=(
            f"league {league.spread_home:+.1f} vs market {consensus:+.1f}: "
            f"{abs(delta):.1f} pts toward {moved_toward}"
        ),
    )


def rank_edges(edges: Sequence[Edge]) -> list[Edge]:
    """Most divergent first — the order the pick sheet is read in."""
    ranked = sorted(edges, key=lambda e: abs(e.delta), reverse=True)
    _emit_decision_event(
        "INFO",
        f"ranked {len(ranked)} edges",
        event="edges_ranked",
        count=len(ranked),
        order=[edge.game_id for edge in ranked],
    )
    return ranked
