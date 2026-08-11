from datetime import UTC, datetime

import pytest

from pickem.models import Game, LeagueLine, MarketLine, Sport
from pickem.store.db import Store

KICK = datetime(2025, 9, 21, 17, 0, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    yield s
    s.close()


def game(**overrides) -> Game:
    base = dict(
        game_id=GID,
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=KICK,
        home_team_id="MIA",
        away_team_id="BUF",
    )
    return Game(**{**base, **overrides})


def market(spread: float, at: datetime, book: str = "pinnacle") -> MarketLine:
    return MarketLine(
        game_id=GID, source="oddsapi", book=book, spread_home=spread, total=41.5, captured_at=at
    )


def test_roundtrips_a_game(store):
    store.upsert_games([game()])
    got = store.games_for_week(Sport.NFL, 2025, 3)
    assert len(got) == 1
    assert got[0].home_team_id == "MIA"
    # kickoff_utc must survive the TIMESTAMPTZ round-trip exactly, tz-aware.
    assert got[0].kickoff_utc == KICK
    assert got[0].kickoff_utc.tzinfo is not None


def test_upserting_a_game_updates_scores_rather_than_duplicating(store):
    store.upsert_games([game()])
    store.upsert_games([game(home_score=24, away_score=17)])
    got = store.games_for_week(Sport.NFL, 2025, 3)
    assert len(got) == 1
    assert got[0].home_score == 24


def test_roundtrips_a_league_line(store):
    store.upsert_games([game()])  # league_lines_for_week joins games for the sport filter
    store.upsert_league_lines(
        [LeagueLine(game_id=GID, season=2025, week=3, spread_home=-3.0, posted_at=KICK)]
    )
    got = store.league_lines_for_week(Sport.NFL, 2025, 3)
    assert got[0].spread_home == -3.0
    # posted_at must survive the TIMESTAMPTZ round-trip exactly, tz-aware.
    assert got[0].posted_at == KICK
    assert got[0].posted_at.tzinfo is not None


def test_market_lines_are_append_only_and_preserve_movement(store):
    first_at = datetime(2025, 9, 16, tzinfo=UTC)
    second_at = datetime(2025, 9, 21, tzinfo=UTC)
    store.append_market_lines([market(-3.0, first_at)])
    store.append_market_lines([market(-6.0, second_at)])
    got = store.market_lines_for(GID)
    # Both snapshots survive. Overwriting would destroy the signal we exist to measure.
    assert sorted(line.spread_home for line in got) == [-6.0, -3.0]
    # captured_at must survive the TIMESTAMPTZ round-trip exactly, tz-aware —
    # backtest chronology and the `before` cutoff both depend on this.
    captured = {line.spread_home: line.captured_at for line in got}
    assert captured[-3.0] == first_at
    assert captured[-6.0] == second_at
    assert captured[-3.0].tzinfo is not None
    assert captured[-6.0].tzinfo is not None


def test_identical_snapshot_appended_twice_is_stored_once(store):
    at = datetime(2025, 9, 16, tzinfo=UTC)
    store.append_market_lines([market(-3.0, at)])
    store.append_market_lines([market(-3.0, at)])
    # Re-polling without a line change must not inflate the history.
    assert len(store.market_lines_for(GID)) == 1


def test_market_lines_can_be_cut_off_at_a_deadline(store):
    store.append_market_lines([market(-3.0, datetime(2025, 9, 16, tzinfo=UTC))])
    store.append_market_lines([market(-6.0, datetime(2025, 9, 22, tzinfo=UTC))])
    got = store.market_lines_for(GID, before=datetime(2025, 9, 21, tzinfo=UTC))
    # Backtests must not see lines captured after the game started.
    assert [line.spread_home for line in got] == [-3.0]


def test_week_queries_do_not_leak_across_weeks(store):
    store.upsert_games([game()])
    assert store.games_for_week(Sport.NFL, 2025, 4) == []
