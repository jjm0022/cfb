import re
from datetime import UTC, datetime, timedelta
from html import escape as html_escape

import pytest
from results_helpers import KICK, assert_well_formed, imported_store

from pickem.models import HISTORY_MONITOR, Game, RecommendationRecord, Side, Sport, Tier
from pickem.report.results import (
    BASELINES,
    STRATEGY_DEFINITIONS,
    BoardStanding,
    ResultsReport,
    Strategy,
    WeekStanding,
    build_results_report,
    findings,
    grade_game,
)
from pickem.report.results_html import render_results_dashboard

GENERATED = datetime(2026, 9, 15, 13, tzinfo=UTC)


@pytest.fixture
def report():
    store = imported_store()
    store.append_recommendation_history([
        RecommendationRecord(
            game_id="cfb-2026-02-PSU-at-TEM", sport=Sport.CFB, season=2026, week=2,
            side=Side.HOME, tier=Tier.COINFLIP, edge_points=0.5,
            generated_at=KICK - timedelta(hours=1), source=HISTORY_MONITOR,
        )
    ])
    try:
        yield build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    finally:
        store.close()


def render(report, weeks=(2,)):
    return render_results_dashboard(report, generated_at=GENERATED, imported_weeks=weeks)


def synthetic_game(index, *, home="H", away="A", our_side=Side.HOME, field=(0, 0),
                   model_side=None):
    game = Game(
        game_id=f"cfb-2026-02-a{index}-at-h{index}", sport=Sport.CFB, season=2026, week=2,
        kickoff_utc=KICK, home_team_id=f"{home}{index}", away_team_id=f"{away}{index}",
        home_score=20, away_score=10,
    )
    history = [] if model_side is None else [
        RecommendationRecord(
            game_id=game.game_id, sport=Sport.CFB, season=2026, week=2, side=model_side,
            tier=Tier.STRONG, edge_points=2.5, generated_at=KICK - timedelta(hours=1),
            source=HISTORY_MONITOR,
        )
    ]
    return grade_game(game=game, pool_week=2, league_spread=3.5, close_spread=None,
                      our_side=our_side, field_home=field[0], field_away=field[1],
                      history=history)


def synthetic_report(games, *, our_points=1, winner_points=2):
    standing = WeekStanding(
        pool_week=2, entrants=3, our_rank=1, our_points=our_points, median_points=1,
        winner_points=winner_points, beat_share=1.0,
        boards=(BoardStanding(Sport.CFB, our_points, 1, winner_points),),
    )
    return ResultsReport(season=2026, pool_week=2, entry_name="Jota", standings=(standing,),
                         week_games=tuple(games), season_games=tuple(games))


def row(text, needle):
    return next(r for r in re.findall(r"<tr>.*?</tr>", text, re.S) if needle in r)


def test_page_is_a_complete_well_formed_document(report):
    text = render(report)
    assert text.startswith("<!doctype html>")
    assert "<title>Pool week 2 · 2026</title>" in text
    assert '<meta name="viewport"' in text
    assert_well_formed(text)


def test_page_is_self_contained(report):
    text = render(report, weeks=(1, 2, 3))
    assert "<script" not in text and "<link" not in text and " src=" not in text
    assert set(re.findall(r'href="([^"]*)"', text)) == {"week-1.html", "week-3.html"}


def test_headline_tiles_carry_this_weeks_standing(report):
    text = render(report)
    headline = text.split('id="this-week"', 1)[0]
    assert '<span class="value">16</span><span class="label">points of 4 games' in headline
    assert '<span class="value">17 of 4</span>' in headline
    assert "CFB</strong> 1 pts · median 0.5 · best 2" in headline


def test_a_tie_for_first_reads_top_score():
    text = render(synthetic_report([synthetic_game(0)], our_points=2, winner_points=2))
    assert '<span class="value">top score</span>' in text


def test_each_board_has_its_records_and_folded_game_table(report):
    text = render(report)
    for board in ("CFB board", "NFL board"):
        assert f"<h3>{board}</h3>" in text
    assert text.count("<details class=\"games\">") == 2
    psu = row(text, "PSU at TEM")
    assert "PSU ✗" in psu
    assert "TEM (coinflip)" in psu
    assert "chip-model" in psu


def test_only_boards_with_games_this_week_appear():
    text = render(synthetic_report([synthetic_game(0)]))
    assert "<h3>CFB board</h3>" in text and "NFL board" not in text


def test_flags_mark_exactly_the_games_that_qualify():
    games = [
        synthetic_game(0, field=(1, 9)),                    # 10% on our side: against
        synthetic_game(1, field=(5, 5)),                    # 50%: not against
        synthetic_game(2, model_side=Side.AWAY),            # model disagrees
        synthetic_game(3, model_side=Side.HOME),            # model agrees
    ]
    text = render(synthetic_report(games))
    assert "chip-field" in row(text, "A0 at H0") and "chip-model" not in row(text, "A0 at H0")
    assert "chip-field" not in row(text, "A1 at H1")
    assert "chip-model" in row(text, "A2 at H2")
    assert "chip-model" not in row(text, "A3 at H3")


def test_kickoffs_show_in_eastern_time(report):
    # KICK is 16:00 UTC on Saturday 2026-09-12, noon EDT.
    assert "Sat 12:00 PM" in row(render(report), "PSU at TEM")


def test_a_board_with_no_decided_model_games_prints_a_dash():
    text = render(synthetic_report([synthetic_game(0)]))
    records = text.split("<h3>CFB board</h3>", 1)[1].split("</table>", 1)[0]
    assert "model</th><td>0–0 (0) = —, n=0</td>" in records


def test_text_from_the_data_is_escaped():
    text = render(synthetic_report([synthetic_game(0, home="<H&", away="A\"")]))
    assert "&lt;H&amp;0" in text
    assert "<H&" not in text


def test_every_season_section_is_present(report):
    text = render(report)
    for heading in (
        "<h2>Season trend</h2>",
        "<h2>Model by tier</h2>",
        "<h2>Us against baselines</h2>",
        "<h2>Closing-line value and the field</h2>",
        "<h2>What the data says</h2>",
        "<summary>What the terms mean</summary>",
    ):
        assert heading in text
    assert_well_formed(text)


def test_tier_rows_carry_the_nfl_backtest_ticks(report):
    tiers = render(report).split("<h2>Model by tier</h2>", 1)[1].split("</section>", 1)[0]
    for x in ('x1="262.8"', 'x1="224.8"', 'x1="206.0"'):  # 63.7%, 54.2%, 49.5%
        assert f'class="expected" {x}' in tiers
    assert "CFB coinflip" in tiers and "NFL strong" in tiers
    assert "NFL backtest 63.7%" in tiers
    assert "Games with no recommendation stored before kickoff" in tiers


def test_baselines_chart_has_a_row_for_every_strategy(report):
    section = render(report).split("<h2>Us against baselines</h2>", 1)[1].split("</section>")[0]
    for strategy in (Strategy.US, *BASELINES):
        assert f'<div class="rate-label">{html_escape(strategy.value)}<small>' in section


def test_trend_uses_every_imported_week(report):
    trend = render(report).split("<h2>Season trend</h2>", 1)[1].split("</section>", 1)[0]
    assert trend.count("<figure") == 2
    assert ">Wk 2<" in trend
    assert "<polyline" not in trend  # a single week draws points only


def test_findings_are_the_shared_findings_text(report):
    text = render(report)
    result = findings(report.season_games)
    for line in (*result.claims, *result.not_yet):
        assert html_escape(line) in text


def test_glossary_defines_every_strategy(report):
    glossary = render(report).split("<summary>What the terms mean</summary>", 1)[1]
    for strategy, definition in STRATEGY_DEFINITIONS.items():
        term = f"<dt>{html_escape(strategy.value)}</dt><dd>{html_escape(definition)}</dd>"
        assert term in glossary


def test_week_links_skip_the_page_being_shown(report):
    text = render(report, weeks=(1, 2, 3))
    nav = text.split('<nav class="weeks"', 1)[1].split("</nav>", 1)[0]
    assert '<a href="week-1.html">Week 1</a>' in nav
    assert '<span aria-current="page">Week 2</span>' in nav
    assert '<a href="week-3.html">Week 3</a>' in nav


def test_clv_without_closes_prints_dashes():
    text = render(synthetic_report([synthetic_game(0)]))
    section = text.split("<h2>Closing-line value and the field</h2>", 1)[1].split("</section>")[0]
    assert '<span class="value">—</span><span class="label">mean CLV</span>' in section
