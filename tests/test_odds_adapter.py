import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from pickem.ingest.odds import (
    LIVE_SOURCE,
    NFL_KEY,
    OddsApiError,
    OddsClient,
    QuotaExhausted,
    _parse_events,
)
from pickem.models import FROZEN_SOURCE, SUBMISSION_SOURCE, Sport
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
WINDOW = (NOW - timedelta(hours=12), NOW + timedelta(days=7))


def client_returning(payload, status=200, headers=None):
    def handler(request):
        return httpx.Response(status, headers=headers, json=payload)

    return OddsClient("key", transport=httpx.MockTransport(handler), sleep=lambda _: None)


def client_with_remaining_header(value):
    def handler(request):
        assert request.url.path == "/v4/sports"
        headers = {} if value is None else {"x-requests-remaining": value}
        return httpx.Response(200, headers=headers, json=[])

    return OddsClient("key", transport=httpx.MockTransport(handler), sleep=lambda _: None)


def fetch(client, slate=SLATE, window=WINDOW):
    return client.fetch_spreads(
        NFL_KEY,
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        now=NOW,
        slate=slate,
        window=window,
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


def test_remaining_credits_reads_the_sports_response_header():
    assert client_with_remaining_header("10610").remaining_credits() == 10610


@pytest.mark.parametrize("header", [None, "not-a-number"])
def test_remaining_credits_rejects_unreadable_headers(header):
    with pytest.raises(OddsApiError, match="x-requests-remaining"):
        client_with_remaining_header(header).remaining_credits()


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
            # Inside the kickoff window, so only the slate can reject it.
            "id": "not-on-our-sheet",
            "commence_time": "2025-09-22T17:00:00Z",
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


def test_out_of_window_events_are_dropped_before_their_teams_are_resolved():
    # The NCAAF feed covers the whole country while aliases.yaml covers only the
    # schools we pick. Resolving before filtering aborts the entire poll on the
    # first unmapped school and stores nothing at all.
    payload = [
        PAYLOAD[0],
        {
            **PAYLOAD[0],
            "id": "far-future",
            "commence_time": "2025-11-30T17:00:00Z",
            "home_team": "Not A Real Team",
            "away_team": "Also Not Real",
        },
    ]
    result = fetch(client_returning(payload))
    assert {line.game_id for line in result.lines} == {"nfl-2025-03-BUF-at-MIA"}
    assert any("outside the nfl 2025 week 3 window" in row for row in result.skipped)


def test_a_repeat_matchup_in_another_week_is_caught_by_the_window():
    # Same teams, same home side, different week: make_game_id embeds the
    # REQUESTED week, so the constructed id lands inside the slate. Only the
    # kickoff window can tell these apart.
    payload = [
        {
            **PAYLOAD[0],
            "id": "rematch",
            "commence_time": "2026-01-11T17:00:00Z",
        }
    ]
    result = fetch(client_returning(payload))
    assert result.lines == []
    assert any("outside" in row for row in result.skipped)


def test_an_unreadable_commence_time_is_reported_not_assumed():
    payload = [{**PAYLOAD[0], "commence_time": "not a timestamp"}]
    result = fetch(client_returning(payload))
    assert result.lines == []
    assert any("unreadable commence_time" in row for row in result.skipped)


def test_the_window_is_sent_to_the_server_too():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=PAYLOAD)

    client = OddsClient("key", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    fetch(client)
    assert "commenceTimeFrom=2025-09-21T00%3A00%3A00Z" in seen["url"]
    assert "commenceTimeTo=2025-09-28T12%3A00%3A00Z" in seen["url"]


def test_a_team_we_do_not_track_is_reported_not_raised():
    # The NCAAF feed carries every FCS matchup with a posted line. Raising on
    # one would abort a poll over a game this system never picks. It cannot be
    # in the slate anyway, and the row is still surfaced in `skipped`.
    payload = [
        PAYLOAD[0],
        {
            **PAYLOAD[0],
            "id": "fcs",
            "commence_time": "2025-09-22T17:00:00Z",
            "home_team": "Towson Tigers",
            "away_team": "Morgan State Bears",
        },
    ]
    result = fetch(client_returning(payload))
    assert {line.game_id for line in result.lines} == {"nfl-2025-03-BUF-at-MIA"}
    assert any("not a team we track" in row for row in result.skipped)
    assert any("Towson" in row for row in result.skipped)


def test_events_outside_the_window_are_logged_with_the_guard_that_dropped_them(records):
    result = _parse_events(
        [
            {
                "home_team": "Auburn",
                "away_team": "Baylor",
                "commence_time": "2026-12-25T00:00:00Z",
                "bookmakers": [],
            }
        ],
        resolver=TeamResolver.default(),
        sport=Sport.CFB,
        season=2026,
        week=1,
        slate=set(),
        window=(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC)),
        captured_at=datetime(2026, 9, 4, tzinfo=UTC),
        source=LIVE_SOURCE,
    )

    assert len(result.skipped) == 1
    filtered = [r for r in records if r["extra"]["event"] == "odds_row_skipped"]
    assert len(filtered) == 1
    assert filtered[0]["level"].name == "WARNING"
    assert filtered[0]["extra"]["guard"] == "kickoff_window"


def test_unmapped_team_is_logged_once_not_twice(records):
    result = _parse_events(
        [
            {
                "home_team": "Nowhere State",
                "away_team": "Baylor",
                "commence_time": "2026-09-04T00:00:00Z",
                "bookmakers": [],
            }
        ],
        resolver=TeamResolver.default(),
        sport=Sport.CFB,
        season=2026,
        week=1,
        slate=set(),
        window=(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 8, tzinfo=UTC)),
        captured_at=datetime(2026, 9, 4, tzinfo=UTC),
        source=LIVE_SOURCE,
    )

    assert len(result.skipped) == 1
    emitted = [r for r in records if r["extra"]["event"] == "odds_row_skipped"]
    assert len(emitted) == 1
    assert emitted[0]["extra"]["guard"] == "unknown_team"


def test_request_records_include_safe_endpoint_and_window_facts(records):
    fetch(client_returning(PAYLOAD))

    request_records = [r for r in records if r["extra"].get("event") == "odds_request"]
    assert len(request_records) == 1
    extra = request_records[0]["extra"]
    assert extra["path"] == "/sports/americanfootball_nfl/odds"
    assert extra["sport_key"] == NFL_KEY
    assert extra["window_from"] == "2025-09-21T00:00:00Z"
    assert extra["window_to"] == "2025-09-28T12:00:00Z"
    assert extra["attempt"] == 1
    assert extra["max_attempts"] == 3
    assert "params" not in extra
    assert "apiKey" not in repr(extra)
    assert "apiKey" not in request_records[0]["message"]


def test_retry_records_include_status_error_and_backoff_without_credentials(records):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(
            503,
            text="upstream unavailable at https://api.example.test/odds?apiKey=secret-api-key",
        )

    client = OddsClient(
        "secret-api-key", transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    with pytest.raises(OddsApiError):
        fetch(client)

    retries = [r for r in records if r["extra"].get("event") == "odds_retry"]
    assert len(attempts) == 3
    assert len(retries) == 2
    assert [r["extra"]["status"] for r in retries] == [503, 503]
    assert [r["extra"]["backoff_seconds"] for r in retries] == [0.5, 1.0]
    assert all(r["extra"]["error_type"] == "OddsApiError" for r in retries)
    assert all("upstream unavailable" in r["extra"]["error_detail"] for r in retries)
    assert all(r["extra"]["sport_key"] == NFL_KEY for r in retries)
    assert all(r["extra"]["window_from"] == "2025-09-21T00:00:00Z" for r in retries)
    assert all(r["extra"]["window_to"] == "2025-09-28T12:00:00Z" for r in retries)
    assert all("secret-api-key" not in repr(r) for r in retries)
    assert all("apiKey" not in repr(r) for r in retries)


def test_transport_retry_records_have_null_status(records):
    def handler(request):
        raise httpx.ConnectError("network unavailable")

    client = OddsClient(
        "secret-api-key", transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    with pytest.raises(OddsApiError):
        fetch(client)

    retries = [r for r in records if r["extra"].get("event") == "odds_retry"]
    assert [r["extra"]["status"] for r in retries] == [None, None]
    assert all(r["extra"]["error_type"] == "ConnectError" for r in retries)
    assert all(r["extra"]["error_detail"] == "network unavailable" for r in retries)


def test_live_poll_logs_quota_and_counts(records):
    result = fetch(
        client_returning(
            PAYLOAD,
            headers={"x-requests-remaining": "100", "x-requests-used": "12"},
        )
    )

    quota = [r for r in records if r["extra"].get("event") == "odds_quota"]
    polled = [r for r in records if r["extra"].get("event") == "odds_polled"]
    assert len(quota) == 1
    assert quota[0]["level"].name == "INFO"
    assert quota[0]["extra"]["remaining"] == 100
    assert quota[0]["extra"]["used"] == 12
    assert len(polled) == 1
    assert polled[0]["extra"]["lines"] == len(result.lines) == 2
    assert polled[0]["extra"]["books"] == 2
    assert polled[0]["extra"]["games"] == 1
    assert polled[0]["extra"]["skipped"] == 0


def test_historical_poll_logs_the_same_quota_and_count_contract(records):
    result = historical(
        client_returning(
            ENVELOPE,
            headers={"x-requests-remaining": "90", "x-requests-used": "20"},
        )
    )

    quota = [r for r in records if r["extra"].get("event") == "odds_quota"]
    polled = [r for r in records if r["extra"].get("event") == "odds_polled"]
    assert len(quota) == 1
    assert quota[0]["extra"]["remaining"] == 90
    assert quota[0]["extra"]["used"] == 20
    assert len(polled) == 1
    assert polled[0]["extra"]["lines"] == len(result.lines) == 2
    assert polled[0]["extra"]["books"] == 2
    assert polled[0]["extra"]["games"] == 1
    assert polled[0]["extra"]["skipped"] == 0


# --- historical archive ------------------------------------------------------

SNAPSHOT_AT = datetime(2025, 9, 21, 16, 55, tzinfo=UTC)
SNAPSHOT_TAKEN = datetime(2025, 9, 21, 16, 50, tzinfo=UTC)

ENVELOPE = {
    "timestamp": "2025-09-21T16:50:00Z",
    "previous_timestamp": "2025-09-21T16:40:00Z",
    "next_timestamp": "2025-09-21T17:00:00Z",
    "data": PAYLOAD,
}


def historical(client, slate=SLATE, window=WINDOW, source=SUBMISSION_SOURCE):
    return client.fetch_historical_spreads(
        NFL_KEY,
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        at=SNAPSHOT_AT,
        slate=slate,
        window=window,
        source=source,
    )


def test_historical_unwraps_the_snapshot_envelope():
    result = historical(client_returning(ENVELOPE))
    assert len(result.lines) == 2
    assert {line.book for line in result.lines} == {"pinnacle", "draftkings"}


def test_historical_result_carries_the_archive_timestamp():
    # Catches returning the parsed lines without the envelope timestamp the
    # archive ledger needs even when an archive response has no usable lines.
    result = historical(client_returning(ENVELOPE))
    assert result.snapshot_at == SNAPSHOT_TAKEN


def test_captured_at_is_the_snapshot_timestamp_not_the_requested_one():
    result = historical(client_returning(ENVELOPE))
    # The archive answers with the closest snapshot at or earlier. Storing the
    # requested instant would misdate the row by up to ten minutes and make a
    # re-run append near-duplicates to an append-only table.
    assert {line.captured_at for line in result.lines} == {SNAPSHOT_TAKEN}


def test_source_labels_which_proxy_a_row_is():
    frozen = historical(client_returning(ENVELOPE), source=FROZEN_SOURCE)
    assert {line.source for line in frozen.lines} == {FROZEN_SOURCE}
    submission = historical(client_returning(ENVELOPE), source=SUBMISSION_SOURCE)
    assert {line.source for line in submission.lines} == {SUBMISSION_SOURCE}


def test_historical_does_not_flip_the_home_point():
    result = historical(client_returning(ENVELOPE))
    assert sorted(line.spread_home for line in result.lines) == [-6.5, -6.0]


def test_historical_applies_the_slate_guard():
    result = historical(client_returning(ENVELOPE), slate={"nfl-2025-03-XXX-at-YYY"})
    assert result.lines == []
    assert any("slate" in row for row in result.skipped)


def test_historical_applies_the_window_guard():
    far = (SNAPSHOT_AT + timedelta(days=30), SNAPSHOT_AT + timedelta(days=37))
    result = historical(client_returning(ENVELOPE), window=far)
    assert result.lines == []
    assert any("outside" in row for row in result.skipped)


def test_an_empty_snapshot_is_not_an_error():
    result = historical(client_returning({"timestamp": "2025-09-21T16:50:00Z", "data": []}))
    assert result.lines == []
    assert result.skipped == []


def test_an_empty_snapshot_carries_its_archive_timestamp():
    # Catches a zero-line archive response that completes the ledger using the
    # requested instant instead of the timestamp the archive actually returned.
    result = historical(client_returning({"timestamp": "2025-09-21T16:50:00Z", "data": []}))
    assert result.lines == []
    assert result.snapshot_at == SNAPSHOT_TAKEN


def test_an_unreadable_snapshot_timestamp_raises_rather_than_guessing():
    # Guessing would write a misdated row into an append-only table.
    with pytest.raises(OddsApiError, match="timestamp"):
        historical(client_returning({"timestamp": "not-a-time", "data": PAYLOAD}))


def test_a_missing_data_key_is_treated_as_an_empty_snapshot():
    result = historical(client_returning({"timestamp": "2025-09-21T16:50:00Z"}))
    assert result.lines == []


def test_historical_quota_exhaustion_is_distinct():
    with pytest.raises(QuotaExhausted):
        historical(client_returning({"message": "out of credits"}, status=401))


def test_historical_requests_the_archive_path_with_a_date():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=ENVELOPE)

    client = OddsClient("key", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    historical(client)
    assert "/v4/historical/sports/americanfootball_nfl/odds" in seen["url"]
    assert "date=2025-09-21T16%3A55%3A00Z" in seen["url"]


def test_live_and_historical_agree_on_game_ids_and_spreads():
    # The two paths share one parser on purpose. If they ever disagreed, the
    # backtest would stop being evidence about the code that ships.
    live = fetch(client_returning(PAYLOAD))
    archived = historical(client_returning(ENVELOPE))
    assert {line.game_id for line in live.lines} == {line.game_id for line in archived.lines}
    assert sorted(line.spread_home for line in live.lines) == sorted(
        line.spread_home for line in archived.lines
    )


# --- the real archive response -----------------------------------------------

REAL_SNAPSHOT = json.loads(
    (Path(__file__).parent / "fixtures" / "odds_historical_nfl.json").read_text()
)


def test_a_real_archive_response_parses_end_to_end():
    """Guards the envelope contract against a genuinely captured response.

    Bought for 10 credits on 2026-08-19: the 2024-09-22T16:55Z NFL snapshot,
    31 events across two weeks and ten books. Hand-built fixtures encode the
    author's assumptions; this one does not.
    """
    kickoff = datetime(2024, 9, 22, 17, 2, tzinfo=UTC)
    slate = {"nfl-2024-03-CHI-at-IND", "nfl-2024-03-NYG-at-CLE"}
    result = client_returning(REAL_SNAPSHOT).fetch_historical_spreads(
        NFL_KEY,
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2024,
        week=3,
        at=datetime(2024, 9, 22, 16, 55, tzinfo=UTC),
        slate=slate,
        window=(kickoff - timedelta(hours=1), kickoff + timedelta(hours=6)),
        source=SUBMISSION_SOURCE,
    )
    assert {line.game_id for line in result.lines} == slate
    assert {line.source for line in result.lines} == {SUBMISSION_SOURCE}
    # Ten books quote these games; a parser that kept one would still "work".
    assert len({line.book for line in result.lines}) > 5
    # The envelope's own timestamp, five minutes before the requested instant.
    assert {line.captured_at for line in result.lines} == {
        datetime(2024, 9, 22, 16, 50, 38, tzinfo=UTC)
    }
    # Every other event in the payload is named, never silently dropped.
    assert result.skipped


def test_the_real_response_has_out_of_window_events_that_are_rejected():
    """The archive is the same nationwide firehose as the live feed."""
    kickoff = datetime(2024, 9, 22, 17, 2, tzinfo=UTC)
    result = client_returning(REAL_SNAPSHOT).fetch_historical_spreads(
        NFL_KEY,
        resolver=TeamResolver.default(),
        sport=Sport.NFL,
        season=2024,
        week=3,
        at=datetime(2024, 9, 22, 16, 55, tzinfo=UTC),
        slate={"nfl-2024-03-CHI-at-IND"},
        window=(kickoff - timedelta(hours=1), kickoff + timedelta(hours=6)),
        source=SUBMISSION_SOURCE,
    )
    # Week 4 games carry posted odds already and must never be stamped week 3.
    assert any("outside" in row for row in result.skipped)
    assert {line.game_id for line in result.lines} == {"nfl-2024-03-CHI-at-IND"}


def test_quota_numbers_are_in_the_message_and_typed_as_integers(records):
    """The text sink renders only the message; "odds api quota" alone said
    nothing, and header strings do not compare or sort in the JSON sink."""
    fetch(
        client_returning(
            PAYLOAD,
            headers={"x-requests-remaining": "17918", "x-requests-used": "2082"},
        )
    )

    [quota] = [r for r in records if r["extra"].get("event") == "odds_quota"]
    assert quota["extra"]["remaining"] == 17918
    assert quota["extra"]["used"] == 2082
    assert quota["message"] == "odds api quota: 17918 remaining, 2082 used"


def test_missing_quota_headers_do_not_break_the_line(records):
    fetch(client_returning(PAYLOAD))

    [quota] = [r for r in records if r["extra"].get("event") == "odds_quota"]
    assert quota["extra"]["remaining"] is None
    assert quota["message"] == "odds api quota: unknown remaining, unknown used"
