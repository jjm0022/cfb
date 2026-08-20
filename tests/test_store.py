from datetime import UTC, datetime

import duckdb
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
    got = store.load_week(Sport.NFL, 2025, 3).games
    assert len(got) == 1
    assert got[0].home_team_id == "MIA"
    # kickoff_utc must survive the TIMESTAMPTZ round-trip exactly, tz-aware.
    assert got[0].kickoff_utc == KICK
    assert got[0].kickoff_utc.tzinfo is not None


def test_upserting_a_game_updates_scores_rather_than_duplicating(store):
    store.upsert_games([game()])
    store.upsert_games([game(home_score=24, away_score=17)])
    got = store.load_week(Sport.NFL, 2025, 3).games
    assert len(got) == 1
    assert got[0].home_score == 24


def test_roundtrips_a_league_line(store):
    store.upsert_games([game()])
    store.upsert_league_lines(
        [LeagueLine(game_id=GID, season=2025, week=3, spread_home=-3.0, posted_at=KICK)]
    )
    got = store.load_week(Sport.NFL, 2025, 3).league_lines
    assert got[0].spread_home == -3.0
    # posted_at must survive the TIMESTAMPTZ round-trip exactly, tz-aware.
    assert got[0].posted_at == KICK
    assert got[0].posted_at.tzinfo is not None


def test_market_lines_are_append_only_and_preserve_movement(store):
    first_at = datetime(2025, 9, 16, tzinfo=UTC)
    second_at = datetime(2025, 9, 21, tzinfo=UTC)
    store.upsert_games([game()])
    store.append_market_lines([market(-3.0, first_at)])
    store.append_market_lines([market(-6.0, second_at)])
    got = store.load_week(Sport.NFL, 2025, 3).market_lines
    # Both snapshots survive. Overwriting would destroy the signal we exist to measure.
    assert sorted(line.spread_home for line in got) == [-6.0, -3.0]
    # captured_at must survive the TIMESTAMPTZ round-trip exactly, tz-aware —
    # backtest chronology depends on this.
    captured = {line.spread_home: line.captured_at for line in got}
    assert captured[-3.0] == first_at
    assert captured[-6.0] == second_at
    assert captured[-3.0].tzinfo is not None
    assert captured[-6.0].tzinfo is not None


def test_identical_snapshot_appended_twice_is_stored_once(store):
    at = datetime(2025, 9, 16, tzinfo=UTC)
    store.upsert_games([game()])
    store.append_market_lines([market(-3.0, at)])
    store.append_market_lines([market(-3.0, at)])
    # Re-polling without a line change must not inflate the history.
    assert len(store.load_week(Sport.NFL, 2025, 3).market_lines) == 1


def test_load_week_does_not_leak_across_weeks(store):
    store.upsert_games([game()])
    assert store.load_week(Sport.NFL, 2025, 4).games == []


def test_reingesting_a_week_does_not_erase_synced_scores(tmp_path):
    # A CBS paste carries no scores. Re-ingesting a week already synced must
    # leave the finals alone, or build_ratings silently loses its history.
    store = Store(tmp_path / "s.duckdb")
    store.init_schema()
    played = Game(
        game_id="nfl-2025-03-BUF-at-MIA",
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=datetime(2025, 9, 21, 17, 0, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
        home_score=17,
        away_score=24,
    )
    store.upsert_games([played])

    scoreless = played.model_copy(
        update={
            "home_score": None,
            "away_score": None,
            "kickoff_utc": datetime(2026, 1, 1, tzinfo=UTC),
        }
    )
    store.upsert_games([scoreless])

    kept = store.load_week(Sport.NFL, 2025, 3).games[0]
    assert (kept.home_score, kept.away_score) == (17, 24)
    store.close()


def test_insert_games_if_absent_leaves_existing_rows_untouched(tmp_path):
    store = Store(tmp_path / "s.duckdb")
    store.init_schema()
    real = Game(
        game_id="nfl-2025-03-BUF-at-MIA",
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=datetime(2025, 9, 21, 17, 0, tzinfo=UTC),
        home_team_id="MIA",
        away_team_id="BUF",
        home_score=17,
        away_score=24,
    )
    store.upsert_games([real])
    placeholder = real.model_copy(
        update={
            "home_score": None,
            "away_score": None,
            "kickoff_utc": datetime(2026, 1, 1, tzinfo=UTC),
        }
    )
    store.insert_games_if_absent([placeholder])

    kept = store.load_week(Sport.NFL, 2025, 3).games[0]
    assert (kept.home_score, kept.away_score) == (17, 24)
    assert kept.kickoff_utc.year == 2025
    store.close()


def test_store_closes_its_handle_when_the_body_raises(tmp_path):
    store = Store(tmp_path / "s.duckdb")
    with pytest.raises(RuntimeError), store:
        store.init_schema()
        raise RuntimeError("boom")
    with pytest.raises(duckdb.Error):
        store.load_week(Sport.NFL, 2025, 3)


def test_every_writer_accepts_an_empty_sequence(tmp_path):
    """A snapshot where every row was skipped is normal, not an error.

    DuckDB's executemany rejects an empty parameter list, so an unguarded
    writer turns "nothing to store" into a crash — mid-backfill, after credits
    are already spent.
    """
    from datetime import UTC, datetime

    now = datetime(2025, 9, 21, tzinfo=UTC)
    with Store(tmp_path / "empty.duckdb") as store:
        store.init_schema()
        store.upsert_games([])
        store.insert_games_if_absent([])
        store.upsert_league_lines([])
        store.append_market_lines([])
        store.record_picks(season=2025, week=3, edges=[], generated_at=now)
        assert store.load_week(Sport.NFL, 2025, 3).games == []


def test_load_week_returns_games_league_lines_and_market_history_together(store):
    store.upsert_games([game()])
    store.upsert_league_lines(
        [LeagueLine(game_id=GID, season=2025, week=3, spread_home=-3.0, posted_at=KICK)]
    )
    store.append_market_lines([market(-3.0, KICK), market(-6.0, KICK.replace(day=22))])

    dataset = store.load_week(Sport.NFL, 2025, 3)

    assert [item.game_id for item in dataset.games] == [GID]
    assert [item.game_id for item in dataset.league_lines] == [GID]
    assert sorted(item.spread_home for item in dataset.market_lines) == [-6.0, -3.0]


def test_load_weeks_excludes_neighboring_weeks(store):
    week_three = game()
    week_four = game().model_copy(update={"game_id": "nfl-2025-04-BUF-at-MIA", "week": 4})
    store.upsert_games([week_three, week_four])

    dataset = store.load_weeks(Sport.NFL, 2025, 4, 4)

    assert [item.game_id for item in dataset.games] == [week_four.game_id]


def test_load_seasons_excludes_other_sports_and_seasons(store):
    nfl = game()
    cfb = game().model_copy(
        update={
            "game_id": "cfb-2025-03-BUF-at-MIA",
            "sport": Sport.CFB,
        }
    )
    store.upsert_games([nfl, cfb])

    dataset = store.load_seasons(Sport.NFL, 2025, 2025)

    assert [item.game_id for item in dataset.games] == [nfl.game_id]
