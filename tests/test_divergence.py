from datetime import UTC, datetime

import pytest

from pickem.edge.divergence import Thresholds, _compute_edge, consensus_spread, rank_edges
from pickem.models import Edge, LeagueLine, MarketLine, Side, Tier

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


def test_edge_measurement_records_the_thresholds_in_effect(records):
    edge = _compute_edge(
        LeagueLine(game_id="g1", season=2025, week=3, spread_home=-3.5, posted_at=T1),
        [
            MarketLine(
                game_id="g1",
                source="oddsapi",
                book="dk",
                spread_home=-1.5,
                captured_at=T1,
            )
        ],
    )

    measured = next(r for r in records if r["extra"]["event"] == "edge_measured")
    assert measured["extra"]["delta"] == pytest.approx(-2.0)
    assert measured["extra"]["tier"] == edge.tier.value
    assert measured["extra"]["threshold_strong"] == 2.0
    assert measured["extra"]["threshold_lean"] == 1.0
    assert measured["extra"]["side"] == edge.side.value


def test_no_market_measurement_records_the_thresholds_in_effect(records):
    _compute_edge(
        LeagueLine(game_id="g1", season=2025, week=3, spread_home=-3.5, posted_at=T1),
        [],
        Thresholds(strong=4.0, lean=1.5),
    )

    measured = next(r for r in records if r["extra"]["event"] == "edge_measured")
    assert measured["extra"]["tier"] == Tier.NO_MARKET.value
    assert measured["extra"]["league_spread"] == -3.5
    assert measured["extra"]["market_spread"] is None
    assert measured["extra"]["delta"] == 0.0
    assert measured["extra"]["threshold_strong"] == 4.0
    assert measured["extra"]["threshold_lean"] == 1.5


def test_consensus_records_the_books_it_collapsed(records):
    lines = [
        market(-1.0, "dk", T0),
        market(-2.0, "dk", T1),
        market(-3.0, "fd", T1),
    ]

    assert consensus_spread(lines) == pytest.approx(-2.5)
    computed = next(r for r in records if r["extra"]["event"] == "consensus_computed")
    assert computed["extra"]["books_used"] == 2
    assert computed["extra"]["snapshots_collapsed"] == 1
    assert computed["extra"]["per_book"] == {"dk": -2.0, "fd": -3.0}


def test_rank_edges_records_the_result_order(records):
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
    ]

    assert [edge.game_id for edge in rank_edges(edges)] == ["large", "small"]
    ranked = next(r for r in records if r["extra"]["event"] == "edges_ranked")
    assert ranked["extra"]["count"] == 2
    assert ranked["extra"]["order"] == ["large", "small"]
