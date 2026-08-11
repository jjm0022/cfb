import pytest

from pickem.backtest.stats import Result, grade_pick, wilson_interval
from pickem.models import Side


def test_home_favorite_covering_is_a_home_win():
    # Home laying 3, wins by 7.
    assert grade_pick(Side.HOME, home_margin=7, spread_home=-3.0) is Result.WIN


def test_home_favorite_failing_to_cover_is_a_home_loss():
    assert grade_pick(Side.HOME, home_margin=1, spread_home=-3.0) is Result.LOSS


def test_away_dog_covering_is_an_away_win():
    assert grade_pick(Side.AWAY, home_margin=1, spread_home=-3.0) is Result.WIN


def test_exact_landing_on_the_number_is_a_push_for_both_sides():
    assert grade_pick(Side.HOME, home_margin=3, spread_home=-3.0) is Result.PUSH
    assert grade_pick(Side.AWAY, home_margin=3, spread_home=-3.0) is Result.PUSH


def test_home_underdog_covering_by_losing_close():
    # Home getting 7, loses by 3 -> home covers.
    assert grade_pick(Side.HOME, home_margin=-3, spread_home=7.0) is Result.WIN


def test_half_point_lines_never_push():
    assert grade_pick(Side.HOME, home_margin=3, spread_home=-3.5) is Result.LOSS
    assert grade_pick(Side.AWAY, home_margin=3, spread_home=-3.5) is Result.WIN


def test_wilson_interval_brackets_the_point_estimate():
    low, high = wilson_interval(54, 100)
    assert low < 0.54 < high


def test_wilson_interval_is_wide_on_small_samples():
    low, high = wilson_interval(54, 100)
    # 54/100 cannot be distinguished from a coin flip.
    assert low < 0.5


def test_wilson_interval_narrows_as_evidence_accumulates():
    small = wilson_interval(540, 1000)
    large = wilson_interval(5400, 10000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_interval_of_no_trials_is_the_full_range():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_rejects_impossible_input():
    with pytest.raises(ValueError):
        wilson_interval(10, 5)
