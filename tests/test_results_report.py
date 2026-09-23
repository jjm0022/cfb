import math
from datetime import timedelta

import pytest
from results_helpers import KICK, imported_store

from pickem.backtest.stats import Result, wilson_interval
from pickem.models import (
    HISTORY_MONITOR,
    HISTORY_REPORT,
    Game,
    MarketLine,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.report.results import (
    Record,
    ResultsReportError,
    Strategy,
    build_results_report,
    close_divergence_side,
    closing_line_value,
    closing_spread,
    field_consensus_side,
    grade_game,
    last_before,
    record_for,
)

PSU_TEM = "cfb-2026-02-PSU-at-TEM"


def rec(at, side, tier=Tier.COINFLIP, source=HISTORY_MONITOR):
    return RecommendationRecord(game_id=PSU_TEM, sport=Sport.CFB, season=2026, week=2, side=side,
                                tier=tier, edge_points=0.5, generated_at=at, source=source)


def line(book, spread, at, source="oddsapi"):
    return MarketLine(game_id=PSU_TEM, source=source, book=book, spread_home=spread, captured_at=at)


def test_close_divergence_takes_the_side_the_market_moved_toward():
    assert close_divergence_side(-3.5, -5.0) is Side.HOME
    assert close_divergence_side(-3.5, -2.0) is Side.AWAY
    assert close_divergence_side(-3.5, -3.5) is None
    assert close_divergence_side(-3.5, None) is None


def test_closing_line_value_sign():
    assert closing_line_value(Side.HOME, -3.5, -5.0) == pytest.approx(1.5)
    assert closing_line_value(Side.AWAY, -3.5, -5.0) == pytest.approx(-1.5)
    assert closing_line_value(Side.HOME, -3.5, None) is None
    assert closing_line_value(None, -3.5, -5.0) is None


def test_closing_line_value_normalizes_negative_zero():
    value = closing_line_value(Side.AWAY, -3.5, -3.5)
    assert f"{value:+.1f}" == "+0.0"
    assert math.copysign(1, value) == 1


def test_field_consensus_skips_ties():
    assert field_consensus_side(3, 2) is Side.HOME
    assert field_consensus_side(1, 4) is Side.AWAY
    assert field_consensus_side(2, 2) is None


def test_model_pick_is_the_last_one_strictly_before_kickoff():
    history = [
        rec(KICK - timedelta(days=2), Side.HOME),
        rec(KICK - timedelta(hours=1), Side.AWAY),
        rec(KICK, Side.HOME, source=HISTORY_REPORT),
        rec(KICK + timedelta(hours=1), Side.HOME),
    ]
    assert last_before(history, KICK).side is Side.AWAY
    assert last_before([rec(KICK, Side.HOME)], KICK) is None


def test_closing_spread_uses_live_quotes_before_kickoff_only():
    lines = [
        line("a", 25.5, KICK - timedelta(hours=2)),
        line("b", 26.0, KICK - timedelta(hours=1)),
        line("a", 23.0, KICK + timedelta(hours=1)),
        line("c", 10.0, KICK - timedelta(hours=3), source="oddsapi:frozen"),
    ]
    assert closing_spread(lines, KICK) == pytest.approx(25.75)
    assert closing_spread([], KICK) is None


def test_team_names_a_side_and_dashes_none():
    game = Game(game_id=PSU_TEM, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
                home_team_id="TEM", away_team_id="PSU", home_score=9, away_score=27)
    graded = grade_game(game=game, pool_week=2, league_spread=24.5, close_spread=None,
                        our_side=None, field_home=0, field_away=0, history=[])
    assert graded.team(None) == "—"
    assert graded.team(Side.HOME) == "TEM"
    assert graded.team(Side.AWAY) == "PSU"


def test_covered_names_the_team_that_covered_or_push():
    def graded(home_score, away_score, league_spread):
        game = Game(game_id=PSU_TEM, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
                    home_team_id="TEM", away_team_id="PSU", home_score=home_score,
                    away_score=away_score)
        return grade_game(game=game, pool_week=2, league_spread=league_spread, close_spread=None,
                          our_side=None, field_home=0, field_away=0, history=[])

    assert graded(20, 10, 3.5).covered == "TEM"
    assert graded(10, 20, 3.5).covered == "PSU"
    assert graded(10, 13, 3.0).covered == "push"


def test_grade_game_grades_every_strategy():
    game = Game(game_id=PSU_TEM, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
                home_team_id="TEM", away_team_id="PSU", home_score=9, away_score=27)
    graded = grade_game(
        game=game, pool_week=2, league_spread=24.5, close_spread=25.75, our_side=Side.AWAY,
        field_home=1, field_away=3,
        history=[
            rec(KICK - timedelta(days=2), Side.HOME),
            rec(KICK - timedelta(hours=1), Side.AWAY),
        ],
    )
    assert graded.result(Strategy.US) is Result.LOSS
    assert graded.result(Strategy.MODEL) is Result.LOSS
    assert graded.result(Strategy.FIRST_SHEET) is Result.WIN
    assert graded.result(Strategy.CLOSE_DIVERGENCE) is Result.LOSS
    assert graded.result(Strategy.FAVORITES) is Result.LOSS
    assert graded.result(Strategy.HOME) is Result.WIN
    assert graded.result(Strategy.FIELD) is Result.LOSS
    assert graded.clv == pytest.approx(1.25)
    assert graded.our_field_share == pytest.approx(0.75)
    assert graded.against_field is False


def test_record_formats_rate_interval_and_n():
    record = Record(9, 6, 1)
    assert record.interval == wilson_interval(9, 15)
    text = str(record)
    assert text.startswith("9–6 (1) = 60.0% [")
    assert text.endswith("], n=15")
    assert str(Record(0, 0, 2)) == "0–0 (2) = n/a, n=0"


@pytest.fixture
def store():
    s = imported_store()
    s.append_recommendation_history([
        rec(KICK - timedelta(days=2), Side.HOME),
        rec(KICK - timedelta(hours=1), Side.AWAY),
        rec(KICK + timedelta(hours=1), Side.HOME, tier=Tier.LEAN),
    ])
    s.append_market_lines([
        line("a", 25.5, KICK - timedelta(hours=2)),
        line("b", 26.0, KICK - timedelta(hours=1)),
        line("a", 23.0, KICK + timedelta(hours=1)),
        line("c", 10.0, KICK - timedelta(hours=3), source="oddsapi:frozen"),
    ])
    yield s
    s.close()


def test_build_grades_us_the_model_the_field_and_baselines(store):
    report = build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    games = report.week_games
    assert len(games) == 4
    us = record_for(games, Strategy.US)
    assert (us.wins, us.losses) == (1, 3)
    assert record_for(games, Strategy.FAVORITES).losses == 4
    home = record_for(games, Strategy.HOME)
    assert (home.wins, home.losses) == (2, 2)
    assert record_for(games, Strategy.FIELD).decided == 1
    assert record_for(games, Strategy.MODEL).losses == 1
    assert record_for(games, Strategy.FIRST_SHEET).wins == 1
    assert report.unknown_model_games == 3

    psu = next(g for g in games if g.game.game_id == PSU_TEM)
    assert psu.model.tier is Tier.COINFLIP
    assert psu.close_spread == pytest.approx(25.75)
    assert psu.clv == pytest.approx(1.25)

    det = next(g for g in games if g.game.game_id == "nfl-2026-01-NO-at-DET")
    assert (det.field_home, det.field_away) == (2, 1)


def test_build_computes_standings(store):
    week = build_results_report(store, season=2026, pool_week=2, entry_name="Jota").current
    assert (week.entrants, week.our_rank, week.our_points) == (4, 17, 16)
    assert (week.median_points, week.winner_points, week.gap_to_winner) == (16, 20, 4)
    assert week.beat_share == pytest.approx(1 / 3)
    boards = {b.sport: (b.our_points, b.median_points, b.best_points) for b in week.boards}
    assert boards == {Sport.CFB: (1, 0.5, 2), Sport.NFL: (0, 0, 2)}


def test_build_rejects_unknown_entrant_and_week(store):
    with pytest.raises(ResultsReportError, match="no entrant named 'Nobody'.*Entrant A"):
        build_results_report(store, season=2026, pool_week=2, entry_name="Nobody")
    with pytest.raises(ResultsReportError, match="pool week 3 of 2026 is not imported"):
        build_results_report(store, season=2026, pool_week=3, entry_name="Jota")
