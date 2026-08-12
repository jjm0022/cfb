from datetime import UTC, datetime

import httpx
import pytest

from pickem.ingest.odds import NFL_KEY, OddsApiError, OddsClient, QuotaExhausted
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

NOW = datetime(2025, 9, 21, 12, 0, tzinfo=UTC)

PAYLOAD = [
    {
        "id": "abc",
        "commence_time": "2025-09-21T17:00:00Z",
        "home_team": "Miami Dolphins",
        "away_team": "Buffalo Bills",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Miami Dolphins", "point": -6.0},
                            {"name": "Buffalo Bills", "point": 6.0},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Miami Dolphins", "point": -6.5},
                            {"name": "Buffalo Bills", "point": 6.5},
                        ],
                    }
                ],
            },
        ],
    }
]


SLATE = {"nfl-2025-03-BUF-at-MIA"}


def client_returning(payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)

    return OddsClient("key", transport=httpx.MockTransport(handler), sleep=lambda _: None)


def fetch(client, slate=SLATE):
    return client.fetch_spreads(
        NFL_KEY,
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        now=NOW,
        slate=slate,
    )


def test_one_market_line_per_bookmaker():
    assert len(fetch(client_returning(PAYLOAD)).lines) == 2


def test_takes_the_home_teams_point_without_flipping():
    lines = {line.book: line.spread_home for line in fetch(client_returning(PAYLOAD)).lines}
    assert lines == {"pinnacle": -6.0, "draftkings": -6.5}


def test_builds_canonical_game_ids():
    assert fetch(client_returning(PAYLOAD)).lines[0].game_id == "nfl-2025-03-BUF-at-MIA"


def test_snapshot_is_stamped_with_capture_time():
    assert fetch(client_returning(PAYLOAD)).lines[0].captured_at == NOW


def test_quota_exhaustion_raises_a_distinct_error():
    # The caller must be able to fall back to cached snapshots on quota, but not on a bug.
    with pytest.raises(QuotaExhausted):
        fetch(client_returning({"message": "out of credits"}, status=401))


def test_bookmaker_without_a_spreads_market_is_surfaced_not_silently_dropped():
    # Human ruling, 2026-08-11 (Task 9a): a missing spreads market or missing
    # point is counted and named in `skipped`, never silently dropped.
    payload = [
        {
            **PAYLOAD[0],
            "bookmakers": [{"key": "x", "markets": [{"key": "totals", "outcomes": []}]}],
        }
    ]
    result = fetch(client_returning(payload))
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "nfl-2025-03-BUF-at-MIA" in result.skipped[0]
    assert "x" in result.skipped[0]


def test_outcome_missing_a_point_is_surfaced_not_silently_dropped():
    payload = [
        {
            **PAYLOAD[0],
            "bookmakers": [
                {
                    "key": "y",
                    "markets": [
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Miami Dolphins", "point": None},
                                {"name": "Buffalo Bills", "point": None},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    result = fetch(client_returning(payload))
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "nfl-2025-03-BUF-at-MIA" in result.skipped[0]
    assert "y" in result.skipped[0]


def test_events_outside_the_requested_week_are_never_stamped_with_it():
    # The endpoint returns every event with posted odds, which spans weeks. An
    # out-of-slate event must not be written under this week's game id: the
    # lines table is append-only, so a wrong id can never be cleaned up, and the
    # real week's report would silently come back NO_MARKET.
    payload = [
        PAYLOAD[0],
        {
            **PAYLOAD[0],
            "id": "next-week",
            "commence_time": "2025-09-28T17:00:00Z",
            "home_team": "New York Jets",
            "away_team": "Dallas Cowboys",
        },
    ]
    result = fetch(client_returning(payload))
    assert {line.game_id for line in result.lines} == {"nfl-2025-03-BUF-at-MIA"}
    assert any("not in the nfl 2025 week 3 slate" in row for row in result.skipped)
    assert any("DAL-at-NYJ" in row for row in result.skipped)


def test_an_empty_slate_stores_nothing():
    result = fetch(client_returning(PAYLOAD), slate=set())
    assert result.lines == []
    assert len(result.skipped) == 1


def test_transport_errors_are_retried_then_raised_loudly():
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ConnectError("boom")

    client = OddsClient("key", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(OddsApiError):
        fetch(client)
    assert len(attempts) == 3


def test_server_errors_are_retried_but_client_errors_are_not():
    server_attempts = []

    def server_handler(request):
        server_attempts.append(request)
        return httpx.Response(503, json={})

    client = OddsClient("key", transport=httpx.MockTransport(server_handler), sleep=lambda _: None)
    with pytest.raises(OddsApiError):
        fetch(client)
    assert len(server_attempts) == 3

    client_attempts = []

    def client_handler(request):
        client_attempts.append(request)
        return httpx.Response(401, json={})

    client = OddsClient("key", transport=httpx.MockTransport(client_handler), sleep=lambda _: None)
    with pytest.raises(QuotaExhausted):
        fetch(client)
    assert len(client_attempts) == 1


def test_client_closes_as_a_context_manager():
    with client_returning(PAYLOAD) as client:
        assert fetch(client).lines
    assert client._client.is_closed
