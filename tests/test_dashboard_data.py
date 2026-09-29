import json
from datetime import timedelta

from dashboard_helpers import GENERATED, KICK, synthetic_season

from pickem.report.dashboard import build_dashboard_data
from pickem.report.results import (
    BACKTEST_TIER_RATES,
    STRATEGY_DEFINITIONS,
    US_LINE,
    LinePoint,
    Strategy,
    findings,
)


def test_data_is_plain_json():
    data = build_dashboard_data(synthetic_season(), generated_at=GENERATED)
    assert json.loads(json.dumps(data)) == data
    assert data["generated_at"] == "2026-09-29T13:00:00Z"
    assert data["latest_week"] == 2 and data["season"] == 2026 and data["entry"] == "Jota"
    assert data["analysis"] is None


def test_every_game_carries_every_strategys_side_and_graded_result():
    report = synthetic_season()
    data = build_dashboard_data(report, generated_at=GENERATED)
    assert [g["id"] for g in data["games"]] == [g.game.game_id for g in report.season_games]
    for graded, game in zip(report.season_games, data["games"], strict=True):
        for strategy in Strategy:
            side = graded.picks.get(strategy)
            result = graded.result(strategy)
            assert game["picks"][strategy.value] == (side.value if side else None)
            assert game["results"][strategy.value] == (result.value if result else None)
        assert game["week"] == graded.pool_week
        assert game["line"] == graded.league_spread and game["close"] == graded.close_spread
        assert game["clv"] == graded.clv and game["against_field"] == graded.against_field
        assert (game["model"] is None) == (graded.model is None)
        if graded.model is not None:
            assert game["model"]["tier"] == graded.model.tier.value
            assert game["model"]["side"] == graded.model.side.value
        assert len(game["history"]) == len(graded.history)


def test_line_history_is_serialised_in_order():
    report = synthetic_season()
    first = report.season_games[0]
    points = (LinePoint(KICK - timedelta(hours=5), US_LINE, -3.0),
              LinePoint(KICK - timedelta(hours=1), US_LINE, -3.5))
    report.season_games[0].__dict__["line_history"] = points  # frozen dataclass: test-only override
    data = build_dashboard_data(report, generated_at=GENERATED)
    assert data["games"][0]["id"] == first.game.game_id
    assert data["games"][0]["lines"] == [
        {"at": "2026-09-05T11:00:00Z", "source": "us", "spread": -3.0},
        {"at": "2026-09-05T15:00:00Z", "source": "us", "spread": -3.5},
    ]


def test_standings_and_reference_tables():
    report = synthetic_season()
    data = build_dashboard_data(report, generated_at=GENERATED)
    assert data["standings"][1] == {
        "week": 2, "entrants": 20, "rank": 5, "points": 15, "median": 14.0, "winner": 20,
        "beat_share": 0.75,
        "boards": [{"sport": "cfb", "points": 8, "median": 7.0, "best": 11},
                   {"sport": "nfl", "points": 7, "median": 7.0, "best": 10}],
    }
    assert data["strategies"] == [s.value for s in Strategy]
    assert data["definitions"] == {s.value: t for s, t in STRATEGY_DEFINITIONS.items()}
    assert data["backtest"] == {t.value: r for t, r in BACKTEST_TIER_RATES.items()}


def test_findings_are_computed_through_each_week():
    report = synthetic_season()
    data = build_dashboard_data(report, generated_at=GENERATED)
    week1 = findings([g for g in report.season_games if g.pool_week <= 1])
    assert data["findings"]["1"] == {"claims": list(week1.claims), "not_yet": list(week1.not_yet)}
    season = findings(report.season_games)
    assert data["findings"]["2"] == {"claims": list(season.claims), "not_yet": list(season.not_yet)}
