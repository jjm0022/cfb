from datetime import UTC, datetime

import pytest

from pickem.edge import pipeline as pipeline_module
from pickem.edge.divergence import Thresholds, rank_edges, suppress_decision_logging
from pickem.edge.elo import EloConfig
from pickem.edge.pipeline import MissingGameError, decide_edges, predict_tiebreaker_total
from pickem.models import Game, LeagueLine, MarketLine, Side, Sport, Tier

NOW = datetime(2025, 9, 21, tzinfo=UTC)
POSTED = datetime(2025, 9, 16, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


def game(gid: str = GID, week: int = 3, home_score=None, away_score=None) -> Game:
    return Game(
        game_id=gid,
        sport=Sport.NFL,
        season=2025,
        week=week,
        kickoff_utc=NOW,
        home_team_id="MIA",
        away_team_id="BUF",
        home_score=home_score,
        away_score=away_score,
    )


def league(spread: float = -3.0) -> LeagueLine:
    return LeagueLine(game_id=GID, season=2025, week=3, spread_home=spread, posted_at=POSTED)


def market(spread: float) -> MarketLine:
    return MarketLine(
        game_id=GID, source="oddsapi", book="pinnacle", spread_home=spread, captured_at=NOW
    )


HISTORY = [
    game(gid=f"nfl-2025-{week:02d}-BUF-at-MIA", week=week, home_score=30, away_score=10)
    for week in range(1, 4)
]


def test_strong_divergence_returns_its_final_side_without_rating_override():
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.STRONG


def test_lean_divergence_returns_its_final_side_without_rating_override():
    [edge] = decide_edges([league(-3.0)], [market(-1.5)], [game()], HISTORY)
    assert edge.side is Side.AWAY
    assert edge.tier is Tier.LEAN
    assert "rating" not in edge.rationale.lower()


@pytest.mark.parametrize(
    ("market_spread", "expected_side", "expected_tier"),
    [(-6.0, Side.HOME, Tier.STRONG), (-1.5, Side.AWAY, Tier.LEAN)],
)
def test_settled_divergence_does_not_require_a_matching_game(
    market_spread, expected_side, expected_tier
):
    [edge] = decide_edges([league(-3.0)], [market(market_spread)], [], HISTORY)
    assert edge.side is expected_side
    assert edge.tier is expected_tier


def test_coinflip_returns_the_board_favorite_not_a_placeholder():
    [edge] = decide_edges([league(-3.0)], [market(-3.5)], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.COINFLIP
    assert "frozen-board favorite" in edge.rationale


def test_coinflip_on_an_away_favored_board_returns_away():
    [edge] = decide_edges([league(3.0)], [market(3.5)], [game()], HISTORY)
    assert edge.side is Side.AWAY
    assert edge.tier is Tier.COINFLIP
    assert "frozen-board favorite" in edge.rationale


def test_coinflip_takes_the_favorite_however_big_the_number():
    """The Elo tiebreak this replaced returned AWAY here, flipping to the dog
    once the board outran its projected margin."""
    [edge] = decide_edges([league(-40.0)], [market(-40.0)], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.COINFLIP


def test_coinflip_never_consults_the_rating(records):
    [edge] = decide_edges([league(-3.0)], [market(-3.5)], [game()], HISTORY)

    assert "elo" not in edge.rationale.lower()
    tiebreak = next(r for r in records if r["extra"]["event"] == "tiebreak_applied")
    assert tiebreak["extra"]["method"] == "frozen_line_favorite"
    assert tiebreak["extra"]["league_spread"] == -3.0
    assert tiebreak["extra"]["side"] == edge.side.value
    assert "projected_margin" not in tiebreak["extra"]


def test_no_market_returns_the_rating_side_not_a_placeholder():
    [edge] = decide_edges([league(-3.0)], [], [game()], HISTORY)
    assert edge.side is Side.HOME
    assert edge.tier is Tier.NO_MARKET
    assert edge.market_spread is None
    assert "Elo rating projects" in edge.rationale


def test_a_required_tiebreak_without_a_game_fails_loudly():
    with pytest.raises(MissingGameError):
        decide_edges([league(-3.0)], [], [], HISTORY)


def test_a_missing_tiebreak_game_does_not_emit_decision_logs(records):
    with pytest.raises(MissingGameError):
        decide_edges([league(-3.0)], [], [], HISTORY)

    decision_events = {
        "consensus_computed",
        "edge_measured",
        "tiebreak_applied",
        "edge_decided",
        "edges_ranked",
    }
    assert not any(r["extra"].get("event") in decision_events for r in records)


def test_decide_edges_measures_each_edge_once(monkeypatch, records):
    original_compute_edge = pipeline_module._compute_edge
    calls = 0

    def counted_compute_edge(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_compute_edge(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "_compute_edge", counted_compute_edge)

    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [], HISTORY)

    assert calls == 1
    measured = [r for r in records if r["extra"].get("event") == "edge_measured"]
    assert len(measured) == 1
    assert measured[0]["extra"]["game_id"] == edge.game_id


@pytest.mark.parametrize(
    ("league_spread", "market_spread", "expected_side", "expected_delta"),
    [(-3.0, -6.0, Side.HOME, 3.0), (-6.0, -3.0, Side.AWAY, -3.0), (2.0, -1.0, Side.HOME, 3.0)],
)
def test_final_pick_obeys_the_home_perspective_sign_convention(
    league_spread, market_spread, expected_side, expected_delta
):
    [edge] = decide_edges([league(league_spread)], [market(market_spread)], [game()], HISTORY)
    assert edge.side is expected_side
    assert edge.delta == expected_delta


@pytest.mark.parametrize(
    ("market_spread", "expected_tier"),
    [(-5.0, Tier.STRONG), (-4.0, Tier.LEAN), (-3.5, Tier.COINFLIP)],
)
def test_final_pick_uses_inclusive_tier_thresholds(market_spread, expected_tier):
    [edge] = decide_edges([league(-3.0)], [market(market_spread)], [game()], HISTORY)
    assert edge.tier is expected_tier


def test_threshold_configuration_changes_tiers_without_changing_the_interface():
    strict = Thresholds(strong=5.0, lean=3.0)
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY, thresholds=strict)
    assert edge.tier is Tier.LEAN


def test_final_pick_carries_both_source_numbers_for_audit():
    [edge] = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY)
    assert edge.league_spread == -3.0
    assert edge.market_spread == -6.0
    assert edge.rationale


def test_tiebreaker_total_uses_the_market():
    lines = [
        market(-3.0).model_copy(update={"book": "a", "total": 44.5}),
        market(-3.0).model_copy(update={"book": "b", "total": 45.5}),
    ]
    assert predict_tiebreaker_total(lines) == 45.0


def test_tiebreaker_total_is_none_without_a_market():
    assert predict_tiebreaker_total([]) is None


def test_tiebreaker_total_ignores_books_without_a_total():
    lines = [
        market(-3.0).model_copy(update={"book": "a"}),
        market(-3.0).model_copy(update={"book": "b", "total": 45.0}),
    ]
    assert predict_tiebreaker_total(lines) == 45.0


def test_every_decided_edge_is_logged_with_its_rationale(records):
    edges = decide_edges([league(-3.0)], [market(-6.0)], [game()], HISTORY)

    decided = [r for r in records if r["extra"]["event"] == "edge_decided"]
    assert len(decided) == len(edges)
    by_game = {r["extra"]["game_id"]: r for r in decided}
    for edge in edges:
        assert by_game[edge.game_id]["message"] == edge.rationale
        assert by_game[edge.game_id]["extra"]["side"] == edge.side.value
        assert by_game[edge.game_id]["extra"]["tier"] == edge.tier.value


def test_tiebreak_log_contains_the_actual_default_ratings(records):
    [edge] = decide_edges([league(-3.0)], [], [game()], [])

    tiebreak = next(r for r in records if r["extra"]["event"] == "tiebreak_applied")
    assert tiebreak["extra"]["game_id"] == edge.game_id
    assert tiebreak["extra"]["method"] == "elo"
    assert tiebreak["extra"]["tier"] == Tier.NO_MARKET.value
    assert tiebreak["extra"]["home_team_id"] == "MIA"
    assert tiebreak["extra"]["away_team_id"] == "BUF"
    assert tiebreak["extra"]["home_rating"] == 1500.0
    assert tiebreak["extra"]["away_rating"] == 1500.0
    assert tiebreak["extra"]["projected_margin"] == pytest.approx(2.0)
    assert tiebreak["extra"]["league_spread"] == -3.0
    assert tiebreak["extra"]["side"] == edge.side.value


def test_tiebreak_log_contains_ratings_updated_from_history(records):
    history = [game(gid="history", week=1, home_score=13, away_score=10)]
    config = EloConfig(k=2.0, home_field=0.0, points_per_elo=0.04, initial=1500.0)

    [edge] = decide_edges([league(-3.0)], [], [game()], history, elo_config=config)

    tiebreak = next(r for r in records if r["extra"]["event"] == "tiebreak_applied")
    assert tiebreak["extra"]["home_rating"] == pytest.approx(1502.0)
    assert tiebreak["extra"]["away_rating"] == pytest.approx(1498.0)
    assert tiebreak["extra"]["projected_margin"] == pytest.approx(0.16)
    assert tiebreak["extra"]["league_spread"] == -3.0
    assert tiebreak["extra"]["side"] == edge.side.value


def test_decision_logging_suppression_preserves_results_and_resets(records):
    expected = decide_edges([league(-3.0)], [], [game()], [])
    records.clear()

    with suppress_decision_logging():
        actual = decide_edges([league(-3.0)], [], [game()], [])
        rank_edges(actual)

    assert actual == expected
    decision_events = {
        "consensus_computed",
        "edge_measured",
        "tiebreak_applied",
        "edge_decided",
        "edges_ranked",
    }
    assert not any(r["extra"].get("event") in decision_events for r in records)

    decide_edges([league(-3.0)], [], [game()], [])
    assert any(r["extra"].get("event") == "edge_measured" for r in records)


def test_decision_logging_suppression_nests_and_restores_after_exception(records):
    decision_events = {
        "consensus_computed",
        "edge_measured",
        "tiebreak_applied",
        "edge_decided",
        "edges_ranked",
    }

    with pytest.raises(RuntimeError, match="probe"):
        with suppress_decision_logging():
            decide_edges([league(-3.0)], [], [game()], [])
            with suppress_decision_logging():
                rank_edges([])
            assert not any(r["extra"].get("event") in decision_events for r in records)
            raise RuntimeError("probe")

    decide_edges([league(-3.0)], [], [game()], [])
    assert any(r["extra"].get("event") == "edge_decided" for r in records)
