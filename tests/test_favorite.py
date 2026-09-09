import pytest

from pickem.edge.favorite import favorite_side
from pickem.models import Side


@pytest.mark.parametrize(
    ("league_spread", "expected"),
    [
        (-40.0, Side.HOME),
        (-7.0, Side.HOME),
        (-0.5, Side.HOME),
        (0.5, Side.AWAY),
        (7.0, Side.AWAY),
        (40.0, Side.AWAY),
    ],
)
def test_favorite_side_follows_the_home_perspective_sign(league_spread, expected):
    assert favorite_side(league_spread) is expected


def test_a_pickem_board_resolves_home():
    """The evaluated 51.00% baseline sent an exact 0.0 board to the home side."""
    assert favorite_side(0.0) is Side.HOME


def test_the_size_of_the_number_never_changes_the_favorite():
    """The Elo rule this replaced flipped to the dog once the board outran its
    projected margin. The favorite rule has no such crossover — that difference
    is the whole behavioural change."""
    assert favorite_side(-3.0) is favorite_side(-40.0) is Side.HOME
    assert favorite_side(3.0) is favorite_side(40.0) is Side.AWAY
