import re

from pickem.report.charts import (
    EMPTY,
    RateRow,
    Series,
    rate_rows,
    rate_x,
    trend_chart,
    trend_x,
    trend_y,
)


def test_rate_scale_maps_zero_half_and_one():
    assert (rate_x(0.0), rate_x(0.5), rate_x(1.0)) == (8.0, 208.0, 408.0)


def test_dot_mark_whiskers_and_expected_tick_sit_on_the_scale():
    html = rate_rows(
        [RateRow("CFB strong", "3–1 (0)", 0.75, (0.25, 0.75), expected=0.637)],
        mark="dot", caption="c",
    )
    assert 'class="mark" cx="308.0"' in html
    assert 'class="whisker" x1="108.0"' in html and 'x2="308.0"' in html
    assert 'class="expected" x1="262.8"' in html


def test_bar_mark_runs_from_zero_to_the_rate():
    html = rate_rows([RateRow("us", "d", 0.5, (0.4, 0.6))], mark="bar", caption="c")
    assert '<rect class="mark" x="8.0"' in html and 'width="200.0"' in html


def test_a_row_without_a_rate_draws_no_mark_but_keeps_the_tick():
    html = rate_rows(
        [RateRow("NFL lean", "0–0 (0) = —, n=0", None, None, expected=0.542)],
        mark="bar", caption="c",
    )
    assert 'class="mark"' not in html
    assert 'class="expected" x1="224.8"' in html
    assert "n=0" in html


def test_rate_labels_are_escaped_html_not_svg_text():
    html = rate_rows([RateRow("<b>", "a&b", 0.5, None)], mark="dot", caption="<c>")
    assert "&lt;b&gt;" in html and "a&amp;b" in html and "&lt;c&gt;" in html
    assert "<b>" not in html


def test_no_rows_and_no_weeks_say_no_games_yet():
    assert rate_rows([], mark="dot", caption="c") == EMPTY
    assert trend_chart([], [], y_max=1, fmt=str, caption="c") == EMPTY


def test_trend_scale():
    assert trend_x(0, 1) == 212.0
    assert (trend_x(0, 3), trend_x(2, 3)) == (36.0, 388.0)
    assert (trend_y(0, 10), trend_y(10, 10)) == (150.0, 12.0)


def test_one_week_draws_points_without_a_line():
    html = trend_chart([2], [Series("us", "s-us", (16,))], y_max=20, fmt=str, caption="c")
    assert "<polyline" not in html
    assert 'cx="212.0" cy="39.6"' in html  # 150 - 16/20 * 138


def test_several_weeks_draw_a_line_through_every_point():
    html = trend_chart(
        [1, 2, 3], [Series("us", "s-us", (8, 16, 12))], y_max=16, fmt=str, caption="c"
    )
    assert 'points="36.0,81.0 212.0,12.0 388.0,46.5"' in html
    assert html.count('<circle class="dot s-us"') == 3


def test_an_all_zero_trend_does_not_divide_by_zero():
    html = trend_chart([1, 2], [Series("us", "s-us", (0, 0))], y_max=0, fmt=str, caption="c")
    assert html.count('cy="150.0"') == 2


def test_trend_has_a_legend_and_week_labels():
    html = trend_chart(
        [1, 2], [Series("us", "s-us", (1, 2)), Series("winner", "s-winner", (3, 4))],
        y_max=4, fmt=lambda v: f"{v:g}", caption="Points",
    )
    assert re.search(r'class="key s-winner".*winner', html)
    assert ">Wk 1<" in html and ">Wk 2<" in html
