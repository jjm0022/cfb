from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from pickem.models import (
    HISTORY_MONITOR,
    HISTORY_REPORT,
    Edge,
    Game,
    LeagueLine,
    MarketLine,
    PoolPick,
    PoolResult,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.store.db import Store

AT = datetime(2026, 9, 15, 12, tzinfo=UTC)
KICK = datetime(2026, 9, 12, 16, tzinfo=UTC)
GID = "cfb-2026-02-PSU-at-TEM"


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    yield s
    s.close()


def result(entry_id: str, rank: int, points: int, week: int = 2) -> PoolResult:
    return PoolResult(
        season=2026, pool_week=week, entry_id=entry_id, name=entry_id.title(), rank=rank,
        points=points, ytd=points, tiebreak=None, imported_at=AT,
    )


def pick(entry_id: str, side: Side | None, week: int = 2, game_id: str = GID) -> PoolPick:
    return PoolPick(
        season=2026, pool_week=week, entry_id=entry_id, game_id=game_id, cbs_event_id=1,
        side=side, cbs_correct=None if side is None else side is Side.HOME,
    )


def history(at: datetime, source: str = HISTORY_MONITOR, side: Side = Side.HOME):
    return RecommendationRecord(
        game_id=GID, sport=Sport.CFB, season=2026, week=2, side=side, tier=Tier.LEAN,
        edge_points=1.0, generated_at=at, source=source,
    )


def seed_game(store: Store) -> None:
    store.upsert_games([
        Game(game_id=GID, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
             home_team_id="TEM", away_team_id="PSU")
    ])
    store.upsert_league_lines([
        LeagueLine(game_id=GID, season=2026, week=2, spread_home=24.5, posted_at=KICK)
    ])


def test_pool_week_round_trips(store):
    store.replace_pool_week(
        2026, 2, [result("b", 2, 10), result("a", 1, 12)], [pick("a", Side.HOME), pick("b", None)]
    )
    assert store.pool_weeks(2026) == [2]
    assert [r.entry_id for r in store.pool_results(2026, 2)] == ["a", "b"]
    picks = store.pool_picks(2026, 2)
    assert [(p.entry_id, p.side, p.cbs_correct) for p in picks] == [
        ("a", Side.HOME, True),
        ("b", None, None),
    ]


def test_replacing_a_pool_week_leaves_other_weeks_alone(store):
    store.replace_pool_week(2026, 1, [result("a", 1, 9, week=1)], [pick("a", Side.AWAY, week=1)])
    store.replace_pool_week(2026, 2, [result("a", 1, 12)], [pick("a", Side.HOME)])
    store.replace_pool_week(2026, 2, [result("z", 1, 5)], [])
    assert store.pool_weeks(2026) == [1, 2]
    assert [r.entry_id for r in store.pool_results(2026, 2)] == ["z"]
    assert store.pool_picks(2026, 2) == []
    assert len(store.pool_picks(2026, 1)) == 1


def test_failed_replace_keeps_the_previous_import(store):
    store.replace_pool_week(2026, 2, [result("a", 1, 12)], [pick("a", Side.HOME)])
    with pytest.raises(duckdb.ConstraintException):
        store.replace_pool_week(2026, 2, [result("b", 1, 3)], [pick("b", Side.HOME)] * 2)
    assert [r.entry_id for r in store.pool_results(2026, 2)] == ["a"]


def test_history_is_append_only_and_ignores_duplicates(store):
    rows = [history(KICK - timedelta(hours=2)), history(KICK - timedelta(hours=1))]
    assert store.append_recommendation_history(rows) == 2
    assert store.append_recommendation_history(rows) == 0
    report_row = [history(KICK - timedelta(hours=1), HISTORY_REPORT)]
    assert store.append_recommendation_history(report_row) == 1
    got = store.recommendation_history([GID])
    assert [(r.generated_at, r.source) for r in got] == [
        (KICK - timedelta(hours=2), HISTORY_MONITOR),
        (KICK - timedelta(hours=1), HISTORY_MONITOR),
        (KICK - timedelta(hours=1), HISTORY_REPORT),
    ]
    assert store.recommendation_history([]) == []


def test_by_id_loaders_and_counts(store):
    seed_game(store)
    store.append_market_lines([
        MarketLine(game_id=GID, source="oddsapi", book="fd", spread_home=25.0, captured_at=KICK)
    ])
    assert [g.game_id for g in store.games_by_ids([GID, "missing"])] == [GID]
    assert [line.spread_home for line in store.league_lines_by_ids([GID])] == [24.5]
    assert [line.book for line in store.market_lines_by_ids([GID])] == ["fd"]
    assert store.league_line_count(Sport.CFB, 2026, 2) == 1
    assert store.league_line_count(Sport.NFL, 2026, 1) == 0
    assert [g.game_id for g in store.games_for_season(2026)] == [GID]
    assert store.games_by_ids([]) == []


def test_pick_batches_become_report_history(store):
    seed_game(store)
    edge = Edge(game_id=GID, side=Side.AWAY, delta=0.5, tier=Tier.COINFLIP,
                league_spread=24.5, market_spread=24.0, rationale="test")
    store.record_picks([edge], 2026, 2, KICK - timedelta(days=4))
    assert store.pick_batches(2026) == [
        RecommendationRecord(
            game_id=GID, sport=Sport.CFB, season=2026, week=2, side=Side.AWAY,
            tier=Tier.COINFLIP, edge_points=0.5, generated_at=KICK - timedelta(days=4),
            source=HISTORY_REPORT,
        )
    ]
