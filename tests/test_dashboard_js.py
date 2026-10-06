"""Run the dashboard's JavaScript unit tests under Node as part of pytest."""

import json

import pytest
from dashboard_helpers import GENERATED, run_node, synthetic_season

from pickem.models import Sport, Tier
from pickem.report.dashboard import build_dashboard_data
from pickem.report.results import Strategy, clv_summary, record_for

SPORTS = {"all": None, "cfb": Sport.CFB, "nfl": Sport.NFL}
TIERS = {"any": None, "strong": Tier.STRONG, "lean": Tier.LEAN, "slight": Tier.SLIGHT,
         "coinflip": Tier.COINFLIP, "no_market": Tier.NO_MARKET}


def test_dashboard_javascript_unit_tests():
    run_node("--test", "tests/js/*.test.mjs")


def test_page_math_matches_python(tmp_path):
    report = synthetic_season()
    games = report.season_games
    # Guard against a vacuous comparison: the fixture must exercise the awkward cases.
    assert any(g.result(Strategy.US) and g.result(Strategy.US).value == "push" for g in games)
    assert any(g.model is None for g in games)
    assert any(g.picks[Strategy.US] is None for g in games)

    path = tmp_path / "data.json"
    path.write_text(json.dumps(build_dashboard_data(report, generated_at=GENERATED)))
    out = json.loads(run_node("tests/js/parity.mjs", str(path)))

    for sport_key, sport in SPORTS.items():
        for tier_key, tier in TIERS.items():
            for strategy in Strategy:
                expected = record_for(games, strategy, sport=sport, tier=tier)
                got = out["records"][f"{strategy.value}|{sport_key}|{tier_key}"]
                label = (strategy.value, sport_key, tier_key)
                assert (got["wins"], got["losses"], got["pushes"]) == (
                    expected.wins, expected.losses, expected.pushes), label
                if expected.decided:
                    assert got["rate"] == pytest.approx(expected.rate, abs=1e-9), label
                    assert got["interval"] == pytest.approx(list(expected.interval), abs=1e-9)
                else:
                    assert got["rate"] is None and got["interval"] is None, label
        clv = clv_summary(games, sport=sport)
        got = out["clv"][sport_key]
        assert got["n"] == clv.n
        assert got["mean"] == pytest.approx(clv.mean, abs=1e-9)
        assert got["positive"] == pytest.approx(clv.positive_share, abs=1e-9)
        if clv.interval is None:
            assert got["interval"] is None
        else:
            assert got["interval"] == pytest.approx(list(clv.interval), abs=1e-9)
