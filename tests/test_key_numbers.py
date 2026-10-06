import pytest

from pickem.edge.key_numbers import key_number_crossed


@pytest.mark.parametrize(
    ("league", "market", "expected"),
    [
        (-3.5, -3.0, 3),  # home favored: CBS 3.5, market 3
        (-2.5, -3.0, 3),
        (6.5, 7.0, 7),  # away favored by 7 in home-spread terms
        (-7.5, -6.75, 7),
        (-4.5, -5.0, None),
        (0.5, -0.5, None),
        (-3.0, -3.0, 3),  # inclusive at the number itself
    ],
)
def test_key_number_crossed(league, market, expected):
    assert key_number_crossed(league, market) == expected
