import re

from dashboard_helpers import (
    BARE_GAME,
    GENERATED,
    LOGOS_FIXTURE,
    MODEL_GAME,
    TW_LOCKED,
    TW_NO_MODEL,
    TW_SLIGHT,
    TW_STRONG,
    data_block,
    page_fixture,
    rendered_dom,
    synthetic_season,
    this_week_fixture,
)
from results_helpers import assert_well_formed

from pickem.report.dashboard import ASSETS, SCRIPTS, render_dashboard, render_week_forwarder


def test_page_is_one_well_formed_self_contained_document():
    page = render_dashboard(synthetic_season(), generated_at=GENERATED)
    assert page.startswith("<!doctype html>")
    assert_well_formed(page)
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert "@import" not in page and "url(" not in page


def test_every_script_and_the_stylesheet_are_inlined_in_order():
    page = render_dashboard(synthetic_season(), generated_at=GENERATED)
    positions = [page.index((ASSETS / name).read_text(encoding="utf-8")) for name in SCRIPTS]
    assert positions == sorted(positions)
    assert (ASSETS / "app.css").read_text(encoding="utf-8") in page


def test_no_asset_can_close_its_own_tag():
    for name in (*SCRIPTS, "app.css"):
        text = (ASSETS / name).read_text(encoding="utf-8").lower()
        assert "</script" not in text and "</style" not in text, name


def test_data_cannot_break_out_of_its_script_block():
    report = synthetic_season()
    evil = report.season_games[0].game.model_copy(update={"home_team_id": "</script><b>x&y"})
    report.season_games[0].__dict__["game"] = evil  # frozen dataclass: test-only override
    page = render_dashboard(report, generated_at=GENERATED)
    assert "</script><b>" not in page
    assert "\\u003c/script\\u003e\\u003cb\\u003ex\\u0026y" in page
    assert data_block(page)["games"][0]["home"] == "</script><b>x&y"


def test_forwarder_opens_the_page_at_its_week():
    page = render_week_forwarder(3)
    assert_well_formed(page)
    assert 'location.replace("index.html#week=3")' in page
    assert 'href="index.html#week=3"' in page


def test_the_page_boots_in_a_real_browser(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "", tmp_path)
    assert 'role="tablist"' in dom
    assert "Pick'em 2026" in dom


def test_a_game_opens_in_the_detail_panel_with_its_history(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), f"game={MODEL_GAME}", tmp_path)
    assert 'class="panel"' in dom
    assert "PSU @ TEM" in dom
    assert "The model through the week" in dom
    assert dom.count('class="changed"') == 2  # tier change, then side change
    assert "Line movement" in dom and "<polyline" in dom
    assert "Pinnacle" in dom


def test_a_game_the_model_never_covered_still_opens(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), f"game={BARE_GAME}", tmp_path)
    assert "The model never covered this game." in dom
    assert "No line history" in dom
    assert "Not captured" in dom


def test_week_tab_shows_the_headline_and_every_section(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "", tmp_path)
    assert dom.count('class="tile"') == 4
    assert "No analysis for this week yet." in dom
    assert "Confident picks that lost" in dom
    assert dom.count('class="board-table"') == 2  # both boards played in pool week 2


def test_week_tab_shows_only_boards_with_games(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "sport=nfl", tmp_path)
    assert dom.count('class="board-table"') == 1


def test_week_tab_survives_filters_that_leave_nothing(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tier=lean&result=win", tmp_path)
    assert dom.count('class="tile"') == 4  # standings are not filtered
    assert "No games match the filters." in dom


def test_season_tab_draws_both_trends(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=season", tmp_path)
    assert dom.count('<figure class="trend"') == 2
    assert "<polyline" not in dom  # one imported week: points, no lines


def test_season_trends_draw_lines_across_weeks(tmp_path):
    page = page_fixture(tmp_path, synthetic_season())
    dom = rendered_dom(page, "tab=season", tmp_path)
    assert dom.count("<polyline") == 4  # us, median, winner; and share beaten


def test_model_tab_has_tiers_baselines_clv_findings_and_glossary(tmp_path):
    page = page_fixture(tmp_path, synthetic_season())
    dom = rendered_dom(page, "tab=model", tmp_path)
    assert dom.count('class="rate-row"') == 6 + 7  # 2 boards × 3 tiers, us + 6 baselines
    assert "NFL backtest" in dom
    assert "Closing-line value" in dom
    assert "What the data says" in dom
    assert "What the terms mean" in dom


def test_model_tier_rows_follow_the_board_filter(tmp_path):
    page = page_fixture(tmp_path, synthetic_season())
    dom = rendered_dom(page, "tab=model&sport=cfb", tmp_path)
    assert dom.count('class="rate-row"') == 3 + 7


def test_games_tab_lists_every_game(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=games", tmp_path)
    assert '<table class="games"' in dom
    assert dom.count('<tr class="game-row"') == 4
    assert 'aria-sort="ascending"' in dom  # kickoff, by default


def test_games_tab_with_no_matches_says_so(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=games&tier=lean", tmp_path)
    assert "No games match" in dom


def test_season_tab_prints_a_weeks_values_as_text(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=season", tmp_path)
    assert dom.count('class="readout"') == 2
    assert re.search(r"Week 2: Us \d+ · Field median [\d.]+ · Winner \d+", dom)
    assert re.search(r"Week 2: Beaten \d+%", dom)
    assert "Open week 2" in dom


def test_model_tab_with_no_games_says_so(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "tab=model&tier=lean", tmp_path)
    assert 'class="rate-row"' not in dom
    assert dom.count("No games yet") == 2


def test_the_model_timeline_shows_the_date(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), f"game={MODEL_GAME}", tmp_path)
    assert "Sep 11, 6:00 AM ET" in dom  # 30 hours before a Saturday noon kickoff


def this_week_page(tmp_path, report="fixture", **kwargs):
    kwargs.setdefault("this_week", this_week_fixture())
    kwargs.setdefault("logos", LOGOS_FIXTURE)
    if report is None:
        return page_fixture(tmp_path, None, season=2026, **kwargs)
    return page_fixture(tmp_path, None if report == "fixture" else report, **kwargs)


def test_this_week_lists_both_boards_in_kickoff_order(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), "tab=thisweek", tmp_path)
    assert dom.count('class="row-button tw-row"') == 5
    assert "CFB board" in dom and "NFL board" in dom
    assert "Pool week 6" in dom
    assert dom.count("🔒 Locked") == 1  # the final game shows its score instead
    assert "17–24" in dom and 'class="res res-win"' in dom
    assert "🔥 Strong" in dom and "🎯 Slight" in dom and "No model" in dom
    order = [dom.index(f'aria-label="{m}"') for m in
             ("AF @ ARMY", "PSU @ TEM", "OU @ MICH", "BUF @ MIA", "NE @ SEA")]
    assert order == sorted(order)


def test_this_week_draws_saved_logos_and_blanks_for_the_rest(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), "tab=thisweek", tmp_path)
    assert 'src="logos/cfb/OU.png"' in dom and 'src="logos/cfb/OU-dark.png"' in dom
    assert 'src="logos/nfl/BUF.png"' in dom
    assert "logos/nfl/BUF-dark.png" not in dom
    assert dom.count('class="logo logo-none"') == 7  # AF ARMY PSU TEM MIA NE SEA


def test_this_week_follows_the_board_and_tier_filters(tmp_path):
    page = this_week_page(tmp_path)
    row = 'class="row-button tw-row"'
    assert rendered_dom(page, "tab=thisweek&sport=nfl", tmp_path).count(row) == 2
    assert rendered_dom(page, "tab=thisweek&tier=strong", tmp_path).count(row) == 2


def test_this_week_without_a_board_says_so(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path, this_week=None), "tab=thisweek", tmp_path)
    assert "No board is loaded yet." in dom


def test_before_any_results_the_page_opens_on_this_week(tmp_path):
    page = this_week_page(tmp_path, report=None)
    assert rendered_dom(page, "", tmp_path).count('class="row-button tw-row"') == 5
    assert "No results imported yet." in rendered_dom(page, "tab=season", tmp_path)


def test_every_page_says_when_it_was_updated(tmp_path):
    dom = rendered_dom(page_fixture(tmp_path), "", tmp_path)
    assert "Updated Tue, Sep 29, 9:00 AM ET" in dom  # GENERATED, in Eastern time


def test_a_this_week_game_opens_with_lines_pick_and_chart(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_STRONG}", tmp_path)
    assert 'class="panel"' in dom and "OU @ MICH" in dom
    assert "Lines now" in dom and "US books" in dom and "Pinnacle" in dom
    assert "MICH −6" in dom and "MICH −5.5" in dom
    assert "3.0 pts toward MICH" in dom
    assert "🔥 Strong" in dom and "league -3.0 vs market -6.0" in dom
    assert "How the pick changed this week" in dom and dom.count('class="changed"') == 2
    assert dom.count('class="mark-change"') == 2
    assert "Pick changed" in dom
    assert dom.count('class="logo logo-lg"') == 2


def test_a_slight_pick_names_its_key_number(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_SLIGHT}", tmp_path)
    assert "Crosses 3" in dom


def test_a_locked_pick_without_a_reason_says_so(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_LOCKED}", tmp_path)
    assert "No reason recorded for this pick." in dom
    assert "Locked" in dom


def test_a_game_with_no_model_and_no_quotes_still_opens(tmp_path):
    dom = rendered_dom(this_week_page(tmp_path), f"tab=thisweek&game={TW_NO_MODEL}", tmp_path)
    assert "The model has no pick for this game." in dom
    assert "No line history" in dom
    assert "No quotes yet" in dom
