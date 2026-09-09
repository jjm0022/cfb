from datetime import UTC, datetime

import pytest

from pickem.edge.pipeline import decide_edges
from pickem.models import Edge, Game, LeagueLine, MarketLine, Side, Sport, Tier
from pickem.report.sheet import render_sheet

NOW = datetime(2025, 9, 21, 12, 0, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"
PROVENANCE = "CBS frozen league lines vs latest stored market consensus"

GAME = Game(
    game_id=GID,
    sport=Sport.NFL,
    season=2025,
    week=3,
    kickoff_utc=NOW,
    home_team_id="MIA",
    away_team_id="BUF",
)


def edge(tier: Tier = Tier.STRONG, delta: float = 3.0, side: Side = Side.HOME) -> Edge:
    return Edge(
        game_id=GID,
        side=side,
        delta=delta,
        tier=tier,
        league_spread=-3.0,
        market_spread=-6.0,
        rationale="league -3.0 vs market -6.0: 3.0 pts toward home",
    )


def test_names_the_picked_team_not_just_a_side():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "MIA" in sheet


def test_shows_both_numbers_so_a_pick_can_be_audited():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "-3.0" in sheet and "-6.0" in sheet


def test_shows_the_deciding_rationale_for_each_pick():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "league -3.0 vs market -6.0: 3.0 pts toward home" in sheet


@pytest.mark.parametrize(
    ("market_lines", "expected_tier", "expected_rationale"),
    [
        (
            [
                MarketLine(
                    game_id=GID,
                    source="oddsapi",
                    book="pinnacle",
                    spread_home=-3.5,
                    captured_at=NOW,
                )
            ],
            Tier.COINFLIP,
            "frozen-board favorite",
        ),
        ([], Tier.NO_MARKET, "Elo rating projects"),
    ],
)
def test_renders_the_deciding_tiebreak_rationale_for_tiebreak_tiers(
    market_lines, expected_tier, expected_rationale
):
    """Each tiebreak tier reaches the sheet naming the rule that actually
    decided it — COINFLIP the frozen-board favorite, NO_MARKET the Elo rating."""
    league = LeagueLine(game_id=GID, season=2025, week=3, spread_home=-3.0, posted_at=NOW)
    [decided] = decide_edges([league], market_lines, [GAME], [])
    sheet = render_sheet([decided], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert decided.tier is expected_tier
    assert expected_rationale in sheet


def test_orders_by_divergence_strongest_first():
    strong = edge(Tier.STRONG, delta=6.0)
    weak = edge(Tier.COINFLIP, delta=0.5)
    sheet = render_sheet([weak, strong], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert sheet.index("6.0") < sheet.index("0.5")


def test_stamps_snapshot_age_when_data_is_stale():
    sheet = render_sheet(
        [edge()], [GAME], generated_at=NOW, provenance=PROVENANCE, snapshot_age_minutes=180.0
    )
    assert "180" in sheet


def test_flags_games_with_no_market_line():
    sheet = render_sheet(
        [edge(Tier.NO_MARKET, delta=0.0)], [GAME], generated_at=NOW, provenance=PROVENANCE
    )
    assert "no_market" in sheet.lower() or "no market" in sheet.lower()


def test_displays_provenance_in_every_sheet():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert PROVENANCE in sheet


def test_stamps_unknown_snapshot_age_when_unavailable():
    sheet = render_sheet([edge()], [GAME], generated_at=NOW, provenance=PROVENANCE)
    assert "market snapshot age: **unknown" in sheet.lower()
