import pytest

from pickem.ingest.cfbd_source import load_cfb_games, load_cfb_lines
from pickem.resolve.resolver import TeamResolver

GAMES = [
    {
        "id": 401,
        "season": 2025,
        "week": 3,
        "start_date": "2025-09-20T23:30:00.000Z",
        "home_team": "Alabama",
        "away_team": "Ole Miss",
        "home_points": 27,
        "away_points": 24,
    }
]

LINES = [
    {
        "season": 2025,
        "week": 3,
        "home_team": "Alabama",
        "away_team": "Ole Miss",
        "lines": [
            {"provider": "DraftKings", "spread": -7.5, "over_under": 52.5},
            {"provider": "Bovada", "spread": -7.0, "over_under": 52.0},
        ],
    }
]


@pytest.fixture
def resolver():
    return TeamResolver.default()


def test_builds_canonical_game_ids(resolver):
    games = load_cfb_games(2025, 3, resolver=resolver, fetcher=lambda s, w: GAMES)
    assert games[0].game_id == "cfb-2025-03-MISS-at-BAMA"


def test_resolves_source_specific_school_naming(resolver):
    games = load_cfb_games(2025, 3, resolver=resolver, fetcher=lambda s, w: GAMES)
    assert games[0].away_team_id == "MISS"


def test_preserves_cfbd_home_negative_convention(resolver):
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: LINES)
    # CFBD already uses home-negative; no flip.
    assert {line.spread_home for line in result.lines} == {-7.5, -7.0}


def test_each_provider_becomes_its_own_book_row(resolver):
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: LINES)
    assert {line.book for line in result.lines} == {"DraftKings", "Bovada"}


def test_providers_without_a_spread_are_surfaced_not_silently_dropped(resolver):
    payload = [{**LINES[0], "lines": [{"provider": "X", "spread": None, "over_under": 50.0}]}]
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: payload)
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "cfb-2025-03-MISS-at-BAMA" in result.skipped[0]
    assert "X" in result.skipped[0]


def test_usable_providers_survive_alongside_skipped_ones(resolver):
    payload = [
        {
            **LINES[0],
            "lines": [
                {"provider": "DraftKings", "spread": -7.5, "over_under": 52.5},
                {"provider": "X", "spread": None, "over_under": 50.0},
            ],
        }
    ]
    result = load_cfb_lines(2025, 3, resolver=resolver, fetcher=lambda s, w: payload)
    assert [line.book for line in result.lines] == ["DraftKings"]
    assert len(result.skipped) == 1
