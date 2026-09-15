from datetime import UTC, datetime, timedelta

import pytest
from results_helpers import KICK, imported_store

from pickem.models import HISTORY_MONITOR, Game, RecommendationRecord, Side, Sport, Tier
from pickem.report.results import build_results_report, findings, grade_game
from pickem.report.results_markdown import render_results_report


def synthetic(index: int, *, home_covers: bool, our_side: Side) -> object:
    game = Game(
        game_id=f"cfb-2026-02-A{index}-at-H{index}", sport=Sport.CFB, season=2026, week=2,
        kickoff_utc=KICK, home_team_id=f"H{index}", away_team_id=f"A{index}",
        home_score=20 if home_covers else 10, away_score=10 if home_covers else 20,
    )
    # A +3.5 home underdog: the favorites baseline always takes the away team.
    return grade_game(game=game, pool_week=2, league_spread=3.5, close_spread=None,
                      our_side=our_side, field_home=0, field_away=0, history=[])


def test_findings_claim_only_when_intervals_separate():
    games = [synthetic(i, home_covers=True, our_side=Side.HOME) for i in range(30)]
    result = findings(games)
    assert any("vs favorites" in claim and "us ahead" in claim for claim in result.claims)
    assert not any("vs home" in claim for claim in result.claims)
    assert any("vs home" in line and "not distinguishable yet" in line for line in result.not_yet)


def test_findings_make_no_claim_on_a_small_even_sample():
    games = [synthetic(i, home_covers=i % 2 == 0, our_side=Side.HOME) for i in range(4)]
    assert findings(games).claims == ()


@pytest.fixture
def store():
    s = imported_store()
    s.append_recommendation_history([
        RecommendationRecord(
            game_id="cfb-2026-02-PSU-at-TEM", sport=Sport.CFB, season=2026, week=2,
            side=Side.HOME, tier=Tier.COINFLIP, edge_points=0.5,
            generated_at=KICK - timedelta(hours=1), source=HISTORY_MONITOR,
        )
    ])
    yield s
    s.close()


def test_rendered_report_has_every_section_and_the_game_rows(store):
    report = build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    text = render_results_report(report, generated_at=datetime(2026, 9, 15, 12, tzinfo=UTC))

    for heading in (
        "# Pool week 2 results — 2026",
        "## Headline",
        "## This week",
        "### CFB board",
        "### NFL board",
        "## Season to date",
        "### Model by tier",
        "### Us against baselines",
        "### Closing-line value (our picks)",
        "### Against the field",
        "## What the data says",
    ):
        assert heading in text
    assert "**Jota: 16 pts, rank 17 of 4**" in text
    assert "| CFB | 1 | 0.5 | 2 |" in text
    psu_row = next(row for row in text.splitlines() if "PSU at TEM" in row)
    assert "PSU ✗" in psu_row
    assert "TEM (coinflip)" in psu_row
    assert "differs from model" in psu_row
    assert "Games with no recommendation stored before kickoff" in text
