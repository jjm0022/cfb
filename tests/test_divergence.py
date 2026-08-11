from datetime import UTC, datetime

from pickem.edge.divergence import Thresholds, compute_edge, consensus_spread, rank_edges
from pickem.models import LeagueLine, MarketLine, Side, Tier

GID = "nfl-2025-03-BUF-at-MIA"
T0 = datetime(2025, 9, 16, tzinfo=UTC)
T1 = datetime(2025, 9, 21, tzinfo=UTC)


def league(spread: float) -> LeagueLine:
    return LeagueLine(game_id=GID, season=2025, week=3, spread_home=spread, posted_at=T0)


def market(spread: float, book: str = "pinnacle", at: datetime = T1) -> MarketLine:
    return MarketLine(game_id=GID, source="oddsapi", book=book, spread_home=spread, captured_at=at)


def test_consensus_is_the_median_across_books():
    lines = [market(-6.0, "a"), market(-6.5, "b"), market(-7.0, "c")]
    assert consensus_spread(lines) == -6.5


def test_consensus_median_resists_a_single_broken_book():
    lines = [market(-6.0, "a"), market(-6.5, "b"), market(-60.0, "stale")]
    # A mean would be dragged to -24.2; the median holds.
    assert consensus_spread(lines) == -6.5


def test_consensus_uses_only_the_latest_snapshot_per_book():
    lines = [market(-3.0, "a", at=T0), market(-7.0, "a", at=T1)]
    assert consensus_spread(lines) == -7.0


def test_consensus_of_nothing_is_none():
    assert consensus_spread([]) is None


def test_market_moving_toward_home_makes_home_the_value_side():
    # We hold home at -3 while the market has repriced to -6: 3 points of value on home.
    edge = compute_edge(league(-3.0), [market(-6.0)])
    assert edge.side == Side.HOME
    assert edge.delta == 3.0


def test_market_moving_toward_away_makes_away_the_value_side():
    # We hold home at -6 while the market says -3: the away dog is the value.
    edge = compute_edge(league(-6.0), [market(-3.0)])
    assert edge.side == Side.AWAY
    assert edge.delta == -3.0


def test_sign_convention_holds_when_the_line_crosses_zero():
    edge = compute_edge(league(2.0), [market(-1.0)])
    assert edge.side == Side.HOME
    assert edge.delta == 3.0


def test_large_divergence_is_strong():
    assert compute_edge(league(-3.0), [market(-6.0)]).tier is Tier.STRONG


def test_moderate_divergence_is_a_lean():
    assert compute_edge(league(-3.0), [market(-4.5)]).tier is Tier.LEAN


def test_small_divergence_is_a_coinflip():
    assert compute_edge(league(-3.0), [market(-3.5)]).tier is Tier.COINFLIP


def test_tier_boundaries_are_inclusive_at_the_threshold():
    assert compute_edge(league(-3.0), [market(-5.0)]).tier is Tier.STRONG   # exactly 2.0
    assert compute_edge(league(-3.0), [market(-4.0)]).tier is Tier.LEAN     # exactly 1.0


def test_thresholds_are_configuration_not_logic():
    strict = Thresholds(strong=5.0, lean=3.0)
    assert compute_edge(league(-3.0), [market(-6.0)], strict).tier is Tier.LEAN


def test_missing_market_is_reported_not_skipped():
    edge = compute_edge(league(-3.0), [])
    assert edge.tier is Tier.NO_MARKET
    assert edge.market_spread is None
    assert edge.delta == 0.0


def test_edge_carries_both_source_numbers_for_audit():
    edge = compute_edge(league(-3.0), [market(-6.0)])
    assert edge.league_spread == -3.0
    assert edge.market_spread == -6.0
    assert edge.rationale


def test_ranking_orders_by_absolute_divergence():
    edges = [
        compute_edge(league(-3.0), [market(-3.5)]),
        compute_edge(league(-3.0), [market(-9.0)]),
        compute_edge(league(-3.0), [market(-1.0)]),
    ]
    assert [abs(e.delta) for e in rank_edges(edges)] == [6.0, 2.0, 0.5]
