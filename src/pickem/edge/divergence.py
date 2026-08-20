"""The strategy: exploit the gap between a frozen league line and the live market.

Pure functions only. No network, no database, no filesystem — which is what
makes the whole strategy testable offline and replayable in the backtest.

Sign convention (everything is home-perspective):

    delta = league_spread - market_spread

    delta > 0  ->  we hold the home side at a better price than the market's
    delta < 0  ->  we hold the away side at a better price
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from pydantic import BaseModel

from pickem.models import Edge, LeagueLine, MarketLine, Side, Tier


class Thresholds(BaseModel):
    """Tier cutoffs in points of divergence.

    These defaults are initial guesses, to be replaced by backtested values.
    They are configuration, not logic.
    """

    strong: float = 2.0
    lean: float = 1.0


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
    return median(line.spread_home for line in latest.values())


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
    return Edge(
        game_id=league.game_id,
        side=side,
        delta=delta,
        tier=_tier(delta, thresholds),
        league_spread=league.spread_home,
        market_spread=consensus,
        rationale=(
            f"league {league.spread_home:+.1f} vs market {consensus:+.1f}: "
            f"{abs(delta):.1f} pts toward {moved_toward}"
        ),
    )


def rank_edges(edges: Sequence[Edge]) -> list[Edge]:
    """Most divergent first — the order the pick sheet is read in."""
    return sorted(edges, key=lambda e: abs(e.delta), reverse=True)
