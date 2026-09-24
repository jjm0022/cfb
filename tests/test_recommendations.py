from datetime import UTC, datetime, timedelta

import pytest

from pickem.ingest.odds import OddsApiError
from pickem.models import (
    LIVE_SOURCE,
    PINNACLE_SOURCE,
    Game,
    LeagueLine,
    MarketLine,
    MarketLinesResult,
    Sport,
    Tier,
)
from pickem.operations.recommendations import (
    generate_recommendations,
    poll_odds_snapshot,
    refresh_recommendations,
)
from pickem.store.db import Store


@pytest.fixture
def db(tmp_path):
    return tmp_path / "pickem.duckdb"


@pytest.fixture
def seeded_week(db):
    kickoff = datetime(2026, 9, 6, 17, tzinfo=UTC)
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(
            [
                Game(
                    game_id="nfl:away:home",
                    sport=Sport.NFL,
                    season=2026,
                    week=1,
                    kickoff_utc=kickoff,
                    home_team_id="home",
                    away_team_id="away",
                )
            ]
        )
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id="nfl:away:home",
                    season=2026,
                    week=1,
                    spread_home=-3.0,
                    posted_at=kickoff,
                )
            ]
        )


def test_generate_recommendations_returns_ranked_edges_without_recording_picks(db, seeded_week):
    snapshot = generate_recommendations(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC)
    )

    assert [edge.game_id for edge in snapshot.edges] == ["nfl:away:home"]
    with Store(db, read_only=True) as store:
        assert store.picks_for_week(2026, 1) == []


def test_generation_logs_an_exact_summary(records, db, seeded_week):
    snapshot = generate_recommendations(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC)
    )

    summaries = [
        record for record in records if record["extra"].get("event") == "recommendations_generated"
    ]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["level"].name == "INFO"
    assert summary["extra"]["sport"] == "nfl"
    assert summary["extra"]["season"] == 2026
    assert summary["extra"]["week"] == 1
    assert summary["extra"]["edges"] == 1
    assert summary["extra"]["tiers"] == {
        Tier.STRONG.value: 0,
        Tier.LEAN.value: 0,
        Tier.COINFLIP.value: 0,
        Tier.NO_MARKET.value: 1,
    }
    assert len(snapshot.edges) == summary["extra"]["edges"]


def test_refresh_recommendations_appends_live_snapshot_before_generating(
    db, seeded_week, monkeypatch
):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    class FakeOddsClient:
        def __init__(self, api_key):
            assert api_key == "test-key"

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch_spreads(self, _key, **kwargs):
            assert kwargs["now"] == now
            if "bookmakers" in kwargs:
                return MarketLinesResult(lines=[], skipped=[])
            return MarketLinesResult(
                lines=[
                    MarketLine(
                        game_id="nfl:away:home",
                        source="oddsapi",
                        book="pinnacle",
                        spread_home=-6.0,
                        captured_at=now,
                    )
                ],
                skipped=[],
            )

    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr("pickem.operations.recommendations.OddsClient", FakeOddsClient)

    snapshot = refresh_recommendations(db, Sport.NFL, 2026, 1, now)

    assert [edge.game_id for edge in snapshot.edges] == ["nfl:away:home"]
    assert snapshot.edges[0].market_spread == -6.0
    with Store(db, read_only=True) as store:
        assert store.picks_for_week(2026, 1) == []
        assert len(store.load_week(Sport.NFL, 2026, 1).market_lines) == 1


class RecordingOddsClient:
    """Capture the guards a poll was issued with, storing nothing."""

    calls: list[dict] = []

    def __init__(self, api_key):
        assert api_key == "test-key"

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def fetch_spreads(self, _key, **kwargs):
        RecordingOddsClient.calls.append(kwargs)
        return MarketLinesResult(lines=[], skipped=[])


@pytest.fixture
def recording_client(monkeypatch):
    RecordingOddsClient.calls = []
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr(
        "pickem.operations.recommendations.OddsClient", RecordingOddsClient
    )
    return RecordingOddsClient


def test_poll_looks_twelve_hours_back_by_default(db, seeded_week, recording_client):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now)

    assert recording_client.calls[0]["window"] == (
        now - timedelta(hours=12),
        now + timedelta(days=7),
    )


def test_poll_can_exclude_games_already_underway(db, seeded_week, recording_client):
    """A poll fired an hour before a night game must not store in-play prices.

    The feed returns every event with posted odds, including ones already
    kicked off, whose spreads are live in-game numbers rather than the
    pre-kickoff market this system reasons about.
    """
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now, window_start=now)

    assert recording_client.calls[0]["window"] == (now, now + timedelta(days=7))


def test_refresh_passes_its_window_start_through_to_the_poll(
    db, seeded_week, recording_client
):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    refresh_recommendations(db, Sport.NFL, 2026, 1, now, window_start=now)

    assert recording_client.calls[0]["window"][0] == now


def _seed_two_games(db, early_kickoff, late_kickoff):
    """One game already kicked off, one still ahead, both with league lines."""
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(
            [
                Game(
                    game_id=f"nfl:{name}",
                    sport=Sport.NFL,
                    season=2026,
                    week=1,
                    kickoff_utc=kickoff,
                    home_team_id=f"{name}-home",
                    away_team_id=f"{name}-away",
                )
                for name, kickoff in (("early", early_kickoff), ("late", late_kickoff))
            ]
        )
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=f"nfl:{name}",
                    season=2026,
                    week=1,
                    spread_home=-3.0,
                    posted_at=early_kickoff - timedelta(days=2),
                )
                for name in ("early", "late")
            ]
        )


def test_pending_as_of_drops_games_that_already_kicked_off(db):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    snapshot = generate_recommendations(
        db, Sport.NFL, 2026, 1, late, pending_as_of=datetime(2026, 9, 10, 18, tzinfo=UTC)
    )

    assert [edge.game_id for edge in snapshot.edges] == ["nfl:late"]


def test_pending_as_of_keeps_a_game_that_has_not_kicked_off_yet(db):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    snapshot = generate_recommendations(
        db, Sport.NFL, 2026, 1, early, pending_as_of=datetime(2026, 9, 9, tzinfo=UTC)
    )

    assert sorted(edge.game_id for edge in snapshot.edges) == ["nfl:early", "nfl:late"]


def test_without_pending_as_of_every_game_in_the_week_is_still_decided(db):
    """`pickem report` renders the whole week, played games included."""
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    snapshot = generate_recommendations(db, Sport.NFL, 2026, 1, late)

    assert sorted(edge.game_id for edge in snapshot.edges) == ["nfl:early", "nfl:late"]


def test_excluded_games_are_logged_once_as_an_aggregate(records, db):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    generate_recommendations(
        db, Sport.NFL, 2026, 1, late, pending_as_of=datetime(2026, 9, 10, 18, tzinfo=UTC)
    )

    [excluded] = [r for r in records if r["extra"]["event"] == "locked_games_excluded"]
    assert excluded["extra"]["locked"] == 1
    assert excluded["extra"]["game_ids"] == ["nfl:early"]
    assert "nfl:early" in excluded["message"]


def test_nothing_is_logged_when_no_game_has_kicked_off(records, db):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    generate_recommendations(
        db, Sport.NFL, 2026, 1, early, pending_as_of=datetime(2026, 9, 9, tzinfo=UTC)
    )

    assert not any(r["extra"]["event"] == "locked_games_excluded" for r in records)


def _line(source: str, book: str, spread: float, at: datetime) -> MarketLine:
    return MarketLine(
        game_id="nfl:away:home", source=source, book=book, spread_home=spread, captured_at=at
    )


class TwoRequestClient:
    """The main poll returns one US book; the Pinnacle request returns Pinnacle or fails."""

    calls: list[dict] = []
    pinnacle_error: Exception | None = None

    def __init__(self, api_key):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def fetch_spreads(self, _key, **kwargs):
        TwoRequestClient.calls.append(kwargs)
        now = kwargs["now"]
        if kwargs.get("bookmakers") == "pinnacle":
            if TwoRequestClient.pinnacle_error is not None:
                raise TwoRequestClient.pinnacle_error
            return MarketLinesResult(
                lines=[_line(kwargs["source"], "pinnacle", -9.0, now)], skipped=[]
            )
        return MarketLinesResult(lines=[_line(LIVE_SOURCE, "draftkings", -6.0, now)], skipped=[])


@pytest.fixture
def two_request_client(monkeypatch):
    TwoRequestClient.calls = []
    TwoRequestClient.pinnacle_error = None
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    monkeypatch.setattr("pickem.operations.recommendations.OddsClient", TwoRequestClient)
    return TwoRequestClient


def test_each_poll_also_records_pinnacle_under_its_own_source(
    db, seeded_week, two_request_client
):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now, window_start=now)

    main, pinnacle = two_request_client.calls
    assert "bookmakers" not in main
    assert pinnacle["bookmakers"] == "pinnacle"
    assert pinnacle["source"] == PINNACLE_SOURCE
    assert pinnacle["window"] == main["window"]
    with Store(db, read_only=True) as store:
        lines = store.load_week(Sport.NFL, 2026, 1).market_lines
    stored = {(line.source, line.book) for line in lines}
    assert stored == {(LIVE_SOURCE, "draftkings"), (PINNACLE_SOURCE, "pinnacle")}


def test_a_failed_pinnacle_request_keeps_the_main_poll(
    db, seeded_week, two_request_client, records
):
    two_request_client.pinnacle_error = OddsApiError("odds api returned 500")
    now = datetime(2026, 9, 2, tzinfo=UTC)

    result = poll_odds_snapshot(db, Sport.NFL, 2026, 1, now)

    assert [line.book for line in result.lines] == ["draftkings"]
    with Store(db, read_only=True) as store:
        assert len(store.load_week(Sport.NFL, 2026, 1).market_lines) == 1
    assert any(r["extra"].get("event") == "pinnacle_poll_failed" for r in records)


def test_a_failed_pinnacle_request_never_logs_the_api_key(
    db, seeded_week, two_request_client, records
):
    two_request_client.pinnacle_error = OddsApiError(
        "odds api unreachable: GET /v4/sports/x/odds?apiKey=test-key&bookmakers=pinnacle"
    )

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC))

    (row,) = [r for r in records if r["extra"].get("event") == "pinnacle_poll_failed"]
    assert "test-key" not in row["extra"]["error_detail"]
    assert "test-key" not in row["message"]


def test_pinnacle_lines_never_move_the_models_market_spread(db, seeded_week):
    at = datetime(2026, 9, 2, tzinfo=UTC)
    with Store(db) as store:
        store.append_market_lines(
            [
                _line(LIVE_SOURCE, "draftkings", -6.0, at),
                _line(PINNACLE_SOURCE, "pinnacle", -9.0, at),
            ]
        )

    snapshot = generate_recommendations(db, Sport.NFL, 2026, 1, at)

    assert snapshot.edges[0].market_spread == -6.0
