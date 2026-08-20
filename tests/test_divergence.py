from datetime import UTC, datetime

from pickem.edge.divergence import consensus_spread, rank_edges
from pickem.models import Edge, MarketLine, Side, Tier

GID = "nfl-2025-03-BUF-at-MIA"
T0 = datetime(2025, 9, 16, tzinfo=UTC)
T1 = datetime(2025, 9, 21, tzinfo=UTC)


def market(spread: float, book: str = "pinnacle", at: datetime = T1) -> MarketLine:
    return MarketLine(game_id=GID, source="oddsapi", book=book, spread_home=spread, captured_at=at)


def test_consensus_is_the_median_across_books():
    assert consensus_spread([market(-6.0, "a"), market(-6.5, "b"), market(-7.0, "c")]) == -6.5


def test_consensus_median_resists_a_single_broken_book():
    assert consensus_spread([market(-6.0, "a"), market(-6.5, "b"), market(-60.0, "stale")]) == -6.5


def test_consensus_uses_only_the_latest_snapshot_per_book():
    assert consensus_spread([market(-3.0, "a", T0), market(-7.0, "a", T1)]) == -7.0


def test_consensus_of_nothing_is_none():
    assert consensus_spread([]) is None


def test_ranking_orders_by_absolute_divergence():
    edges = [
        Edge(
            game_id="small",
            side=Side.HOME,
            delta=0.5,
            tier=Tier.COINFLIP,
            league_spread=-3.0,
            market_spread=-3.5,
            rationale="x",
        ),
        Edge(
            game_id="large",
            side=Side.HOME,
            delta=6.0,
            tier=Tier.STRONG,
            league_spread=-3.0,
            market_spread=-9.0,
            rationale="x",
        ),
        Edge(
            game_id="medium",
            side=Side.AWAY,
            delta=-2.0,
            tier=Tier.STRONG,
            league_spread=-3.0,
            market_spread=-1.0,
            rationale="x",
        ),
    ]
    assert [abs(edge.delta) for edge in rank_edges(edges)] == [6.0, 2.0, 0.5]


def test_rank_edges_keeps_no_market_and_tied_edges_in_a_defined_order():
    def edge(game_id, delta, tier):
        return Edge(
            game_id=game_id,
            side=Side.HOME,
            delta=delta,
            tier=tier,
            league_spread=-3.0,
            market_spread=None if tier is Tier.NO_MARKET else -3.0 - delta,
            rationale="x",
        )

    ranked = rank_edges(
        [
            edge("a", 0.0, Tier.NO_MARKET),
            edge("b", 1.5, Tier.LEAN),
            edge("c", -1.5, Tier.LEAN),
            edge("d", 0.0, Tier.NO_MARKET),
            edge("e", 3.0, Tier.STRONG),
        ]
    )
    assert [edge.game_id for edge in ranked] == ["e", "b", "c", "a", "d"]
