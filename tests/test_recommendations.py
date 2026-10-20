from datetime import UTC, datetime, timedelta

import pytest

from pickem.ingest.sgo import SgoPull
from pickem.models import (
    LIVE_SOURCE,
    PINNACLE_SOURCE,
    SGO_SOURCE,
    Game,
    LeagueLine,
    MarketLine,
    MarketLinesResult,
    OddsPull,
    PollTrigger,
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


class FakeSgoClient:
    """Stands in for SportsGameOdds: records each request, returns canned lines."""

    calls: list[dict] = []
    lines: list[MarketLine] = []
    usage_reads = 0

    def __init__(self, api_key):
        assert api_key == "test-key"

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def fetch_spreads(self, **kwargs):
        FakeSgoClient.calls.append(kwargs)
        result = MarketLinesResult(lines=list(FakeSgoClient.lines), skipped=[])
        return SgoPull(result, len(kwargs["games"]))

    def account_usage(self):
        FakeSgoClient.usage_reads += 1
        return {"max-entities": 2500, "current-entities": 40}


@pytest.fixture
def sgo(monkeypatch):
    FakeSgoClient.calls, FakeSgoClient.lines, FakeSgoClient.usage_reads = [], [], 0
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "test-key")
    monkeypatch.setattr("pickem.operations.recommendations.SgoClient", FakeSgoClient)
    return FakeSgoClient


def test_refresh_appends_a_sportsgameodds_snapshot_before_generating(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    sgo.lines = [
        MarketLine(game_id="nfl:away:home", source=SGO_SOURCE, book="draftkings",
                   spread_home=-6.0, captured_at=now)
    ]

    snapshot = refresh_recommendations(db, Sport.NFL, 2026, 1, now)

    assert snapshot.edges[0].market_spread == -6.0
    assert snapshot.odds_notice is None
    with Store(db, read_only=True) as store:
        assert store.picks_for_week(2026, 1) == []
        assert len(store.load_week(Sport.NFL, 2026, 1).market_lines) == 1


def test_poll_looks_twelve_hours_back_by_default(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now)

    assert sgo.calls[0]["window"] == (now - timedelta(hours=12), now + timedelta(days=7))


def test_poll_can_exclude_games_already_underway(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now, window_start=now)

    assert sgo.calls[0]["window"] == (now, now + timedelta(days=7))


def test_refresh_passes_its_window_start_through_to_the_poll(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    refresh_recommendations(db, Sport.NFL, 2026, 1, now, window_start=now)

    assert sgo.calls[0]["window"][0] == now


def test_no_pinnacle_request_is_made(db, seeded_week, sgo):
    poll_odds_snapshot(db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC))

    assert len(sgo.calls) == 1


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
        Tier.SLIGHT.value: 0,
        Tier.COINFLIP.value: 0,
        Tier.NO_MARKET.value: 1,
    }
    assert len(snapshot.edges) == summary["extra"]["edges"]


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


def test_a_poll_asks_only_for_games_not_yet_started(db, sgo):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, datetime(2026, 9, 10, 18, tzinfo=UTC))

    assert [game.game_id for game in sgo.calls[0]["games"]] == ["nfl:late"]


def test_a_kickoff_poll_asks_only_for_its_own_kickoffs(db, sgo):
    """The kickoffs come from the planner as datetime.UTC; the games from DuckDB."""
    early = datetime(2026, 9, 13, 17, tzinfo=UTC)
    late = datetime(2026, 9, 14, 0, 20, tzinfo=UTC)
    _seed_two_games(db, early, late)

    poll_odds_snapshot(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 13, 12, tzinfo=UTC),
        kickoffs=(late,), trigger=PollTrigger.KICKOFF, offset_hours=12.0,
    )

    assert [game.game_id for game in sgo.calls[0]["games"]] == ["nfl:late"]


def test_a_poll_with_nothing_left_to_ask_for_makes_no_request(db, sgo):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    result = poll_odds_snapshot(db, Sport.NFL, 2026, 1, datetime(2026, 9, 14, tzinfo=UTC))

    assert sgo.calls == []
    assert result.lines == []
    with Store(db, read_only=True) as store:
        assert store.odds_games_used(datetime(2026, 1, 1, tzinfo=UTC)) == 0


def test_every_pull_is_recorded_for_the_budget(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now, trigger=PollTrigger.MIDWEEK)

    with Store(db, read_only=True) as store:
        assert store.odds_games_used(now - timedelta(days=30)) == 1


def _spend(db, games: int, at: datetime) -> None:
    with Store(db) as store:
        store.record_odds_pull(
            OddsPull(pulled_at=at, sport=Sport.CFB, season=2026, week=1,
                     trigger=PollTrigger.MIDWEEK, games_requested=games, games_returned=games)
        )


def test_a_pull_over_the_share_is_skipped_and_the_refresh_still_decides(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    _spend(db, 800, now - timedelta(days=1))

    snapshot = refresh_recommendations(db, Sport.NFL, 2026, 1, now, trigger=PollTrigger.MIDWEEK)

    assert sgo.calls == []
    assert [edge.game_id for edge in snapshot.edges] == ["nfl:away:home"]
    assert "800 of its 800" in snapshot.odds_notice


def test_the_owner_is_not_told_twice_in_thirty_days(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    _spend(db, 800, now - timedelta(days=1))
    refresh_recommendations(db, Sport.NFL, 2026, 1, now, trigger=PollTrigger.MIDWEEK)

    later = refresh_recommendations(
        db, Sport.NFL, 2026, 1, now + timedelta(hours=1), trigger=PollTrigger.MIDWEEK
    )

    assert later.odds_notice is None


def test_a_refresh_without_odds_makes_no_request(db, seeded_week, sgo):
    snapshot = refresh_recommendations(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC), pull_odds=False
    )

    assert sgo.calls == []
    assert len(snapshot.edges) == 1


def test_the_week_start_pull_reads_the_account_usage(db, seeded_week, sgo, records):
    poll_odds_snapshot(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC), trigger=PollTrigger.WEEK_START
    )

    assert sgo.usage_reads == 1
    assert any(r["extra"].get("event") == "odds_account_usage" for r in records)


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


def _seed_small_gap(db, sport, game_id, *, league=-3.5, market=-3.0):
    kickoff = datetime(2026, 10, 11, 17, tzinfo=UTC)
    with Store(db) as store:
        store.init_schema()
        store.upsert_games([
            Game(game_id=game_id, sport=sport, season=2026, week=6, kickoff_utc=kickoff,
                 home_team_id="home", away_team_id="away")
        ])
        store.upsert_league_lines([
            LeagueLine(game_id=game_id, season=2026, week=6, spread_home=league,
                       posted_at=kickoff - timedelta(days=5))
        ])
        store.append_market_lines([
            MarketLine(game_id=game_id, source=LIVE_SOURCE, book="draftkings",
                       spread_home=market, captured_at=kickoff - timedelta(hours=2))
        ])


def test_live_nfl_follows_the_market_on_a_small_gap(db):
    _seed_small_gap(db, Sport.NFL, "nfl:small")
    [edge] = generate_recommendations(
        db, Sport.NFL, 2026, 6, datetime(2026, 10, 11, 15, tzinfo=UTC)
    ).edges
    assert edge.tier is Tier.SLIGHT
    assert edge.side.value == "away"
    assert "crosses key number 3" in edge.rationale


def test_live_cfb_keeps_small_gaps_as_coinflips(db):
    _seed_small_gap(db, Sport.CFB, "cfb:small")
    [edge] = generate_recommendations(
        db, Sport.CFB, 2026, 6, datetime(2026, 10, 11, 15, tzinfo=UTC)
    ).edges
    assert edge.tier is Tier.COINFLIP
    assert edge.side.value == "home"


def test_every_skip_is_marked_even_when_the_owner_is_not_told(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    _spend(db, 800, now - timedelta(days=1))
    refresh_recommendations(db, Sport.NFL, 2026, 1, now, trigger=PollTrigger.MANUAL)

    later = refresh_recommendations(
        db, Sport.NFL, 2026, 1, now + timedelta(hours=1), trigger=PollTrigger.MANUAL
    )

    assert later.odds_notice is None
    assert "800 of its 800" in later.odds_skipped


def test_a_pull_that_runs_is_not_marked_skipped(db, seeded_week, sgo):
    snapshot = refresh_recommendations(db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC))

    assert snapshot.odds_skipped is None
