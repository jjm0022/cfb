from datetime import UTC, datetime

import pytest

from pickem.models import Game, LeagueLine, MarketLine, MarketLinesResult, Sport
from pickem.operations.recommendations import generate_recommendations, refresh_recommendations
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
