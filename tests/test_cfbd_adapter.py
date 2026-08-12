import pytest

from pickem.ingest.cfbd_source import load_cfb_games, load_cfb_lines
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

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


def test_unknown_team_propagates_out_of_the_cfb_loaders():
    def fetcher(_season, _week):
        return [
            {
                "home_team": "Nowhere State",
                "away_team": "Alabama",
                "start_date": "2025-09-06T23:30:00.000Z",
                "home_points": 21,
                "away_points": 24,
                "lines": [{"provider": "consensus", "spread": -7.0}],
            }
        ]

    with pytest.raises(UnknownTeamError):
        load_cfb_games(2025, 2, resolver=TeamResolver.default(), fetcher=fetcher)
    with pytest.raises(UnknownTeamError):
        load_cfb_lines(2025, 2, resolver=TeamResolver.default(), fetcher=fetcher)


_REAL_HTTPX_CLIENT = __import__("httpx").Client


def _config():
    from pydantic import SecretStr

    from pickem.ingest.cfbd_source import CfbdConfig

    return CfbdConfig(api_key=SecretStr("test-key"))


def _patched_client(monkeypatch, handler):
    """Route the adapter's httpx.Client at a mock transport.

    The real class is captured up front: patching twice in one test would
    otherwise wrap the previous patch and keep the first handler.
    """
    import httpx

    from pickem.ingest import cfbd_source

    real = _REAL_HTTPX_CLIENT

    def build(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(cfbd_source.httpx, "Client", build)


def test_the_games_fetcher_maps_camelcase_and_keeps_only_fbs_v_fbs(monkeypatch):
    import httpx

    from pickem.ingest.cfbd_source import default_games_fetcher

    payload = [
        {
            "homeTeam": "Georgia",
            "awayTeam": "Alabama",
            "startDate": "2025-09-06T23:30:00.000Z",
            "homePoints": 21,
            "awayPoints": 24,
            "homeClassification": "fbs",
            "awayClassification": "fbs",
        },
        {
            # classification=fbs still returns FBS-hosts-FCS games, and the
            # alias table is FBS-only, so this row must not reach the resolver.
            "homeTeam": "Alabama",
            "awayTeam": "Mercer",
            "startDate": "2025-09-13T23:30:00.000Z",
            "homePoints": 52,
            "awayPoints": 7,
            "homeClassification": "fbs",
            "awayClassification": "fcs",
        },
    ]
    _patched_client(monkeypatch, lambda _r: httpx.Response(200, json=payload))
    rows = default_games_fetcher(_config())(2025, 2)
    assert len(rows) == 1
    assert rows[0] == {
        "home_team": "Georgia",
        "away_team": "Alabama",
        "start_date": "2025-09-06T23:30:00.000Z",
        "home_points": 21,
        "away_points": 24,
    }


def test_the_lines_fetcher_maps_over_under_and_drops_fcs(monkeypatch):
    import httpx

    from pickem.ingest.cfbd_source import default_lines_fetcher

    payload = [
        {
            "homeTeam": "Georgia",
            "awayTeam": "Alabama",
            "startDate": "2025-09-06T23:30:00.000Z",
            "homeClassification": "fbs",
            "awayClassification": "fbs",
            "lines": [{"provider": "DraftKings", "spread": -7.0, "overUnder": 52.5}],
        },
        {
            "homeTeam": "Alabama",
            "awayTeam": "Mercer",
            "homeClassification": "fbs",
            "awayClassification": "fcs",
            "lines": [{"provider": "DraftKings", "spread": -45.0, "overUnder": 50.0}],
        },
    ]
    _patched_client(monkeypatch, lambda _r: httpx.Response(200, json=payload))
    rows = default_lines_fetcher(_config())(2025, 2)
    assert len(rows) == 1
    assert rows[0]["lines"] == [{"provider": "DraftKings", "spread": -7.0, "over_under": 52.5}]


def test_rate_limiting_is_retried_and_a_4xx_is_not(monkeypatch):
    import httpx

    from pickem.ingest.cfbd_source import CfbdApiError, _get

    calls = []

    def rate_limited(_request):
        calls.append(1)
        return httpx.Response(429, json={"error": "slow down"})

    _patched_client(monkeypatch, rate_limited)
    with pytest.raises(CfbdApiError):
        _get(_config(), "/games", {}, sleep=lambda _s: None)
    assert len(calls) == 5

    calls.clear()

    def forbidden(_request):
        calls.append(1)
        return httpx.Response(403, json={"error": "nope"})

    _patched_client(monkeypatch, forbidden)
    with pytest.raises(CfbdApiError):
        _get(_config(), "/games", {}, sleep=lambda _s: None)
    assert len(calls) == 1


def test_the_api_key_never_appears_in_a_repr():
    assert "test-key" not in repr(_config())
