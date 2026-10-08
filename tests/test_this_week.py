"""This week's board: which week, which pick, and what the chart draws."""

from datetime import UTC, datetime, timedelta

import pytest

from pickem.models import (
    LIVE_SOURCE,
    PINNACLE_SOURCE,
    Game,
    LeagueLine,
    MarketLine,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
    make_game_id,
)
from pickem.operations.recommendations import generate_recommendations
from pickem.report.this_week import build_this_week
from pickem.store.db import Store

KICK = datetime(2026, 10, 10, 16, tzinfo=UTC)  # Saturday noon ET
NOW = KICK - timedelta(hours=3)


def game(sport, week, away, home, kickoff, **scores) -> Game:
    return Game(
        game_id=make_game_id(sport, 2026, week, away, home), sport=sport, season=2026,
        week=week, kickoff_utc=kickoff, home_team_id=home, away_team_id=away, **scores,
    )


PENDING = game(Sport.CFB, 6, "OU", "MICH", KICK)
LOCKED = game(Sport.CFB, 6, "PSU", "TEM", KICK - timedelta(hours=4))
FINAL = game(Sport.CFB, 6, "AF", "ARMY", KICK - timedelta(days=2), home_score=24, away_score=17)
NFL = game(Sport.NFL, 5, "BUF", "MIA", KICK + timedelta(days=1))
OLDER = game(Sport.CFB, 5, "OU", "TEM", KICK - timedelta(days=7), home_score=10, away_score=20)
LINES = [(PENDING, -3.0), (LOCKED, 7.0), (FINAL, -3.5), (NFL, -3.5), (OLDER, 1.5)]


def quote(g, book, spread, at, source=LIVE_SOURCE) -> MarketLine:
    return MarketLine(
        game_id=g.game_id, source=source, book=book, spread_home=spread, captured_at=at,
    )


def record(g, side, tier, edge, at) -> RecommendationRecord:
    return RecommendationRecord(
        game_id=g.game_id, sport=g.sport, season=2026, week=g.week, side=side, tier=tier,
        edge_points=edge, generated_at=at, source="monitor",
    )


QUOTES = [
    quote(PENDING, "dk", -6.0, NOW - timedelta(hours=1)),
    quote(PENDING, "fd", -6.0, NOW - timedelta(hours=1)),
    quote(PENDING, "pinnacle", -5.5, NOW - timedelta(hours=1), PINNACLE_SOURCE),
    quote(LOCKED, "dk", 5.5, LOCKED.kickoff_utc - timedelta(hours=2)),
    quote(LOCKED, "dk", -10.0, LOCKED.kickoff_utc + timedelta(hours=1)),  # after kickoff: ignored
    quote(FINAL, "dk", -7.0, FINAL.kickoff_utc - timedelta(hours=3)),
    quote(NFL, "dk", -3.0, NOW - timedelta(hours=1)),
]
HISTORY = [
    record(PENDING, Side.AWAY, Tier.LEAN, -1.5, NOW - timedelta(hours=30)),
    record(PENDING, Side.HOME, Tier.STRONG, 3.0, NOW - timedelta(hours=1)),
    record(LOCKED, Side.HOME, Tier.LEAN, 1.5, LOCKED.kickoff_utc - timedelta(hours=2)),
    record(FINAL, Side.HOME, Tier.STRONG, 3.5, FINAL.kickoff_utc - timedelta(hours=1)),
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pickem.duckdb"
    with Store(path) as store:
        store.init_schema()
        store.upsert_games([g for g, _ in LINES])
        store.upsert_league_lines([
            LeagueLine(game_id=g.game_id, season=2026, week=g.week, spread_home=spread,
                       posted_at=KICK - timedelta(days=4))
            for g, spread in LINES
        ])
        store.append_market_lines(QUOTES)
        store.append_recommendation_history(HISTORY)
    return path


def board(db, now=NOW) -> dict:
    with Store(db) as store:
        return build_this_week(store, 2026, now)


def by_id(board_data) -> dict:
    return {g["id"]: g for g in board_data["games"]}


def test_the_latest_pool_week_with_both_boards_in_kickoff_order(db):
    data = board(db)
    assert data["pool_week"] == 6
    assert [g["id"] for g in data["games"]] == [
        FINAL.game_id, LOCKED.game_id, PENDING.game_id, NFL.game_id,
    ]


def test_a_pending_pick_is_what_the_bot_would_pick_now(db):
    snapshots = [
        generate_recommendations(db, sport, 2026, week, NOW, pending_as_of=NOW)
        for sport, week in ((Sport.CFB, 6), (Sport.NFL, 5))
    ]
    expected = {e.game_id: e for s in snapshots for e in s.edges}
    games = by_id(board(db))
    for g in (PENDING, NFL):
        model, edge = games[g.game_id]["model"], expected[g.game_id]
        assert (model["side"], model["tier"], model["edge"], model["rationale"]) == (
            edge.side.value, edge.tier.value, edge.delta, edge.rationale)
    assert games[PENDING.game_id]["model"]["tier"] == "strong"
    assert games[PENDING.game_id]["locked"] is False


def test_a_locked_game_shows_the_pick_recorded_before_kickoff(db):
    locked = by_id(board(db))[LOCKED.game_id]
    assert locked["locked"] is True
    assert (locked["model"]["side"], locked["model"]["tier"], locked["model"]["edge"]) == (
        "home", "lean", 1.5)
    assert locked["model"]["rationale"] == "league +7.0 vs market +5.5: 1.5 pts toward home"


def test_a_locked_pick_that_disagrees_with_the_recompute_has_no_rationale(db):
    with Store(db) as store:
        store.append_recommendation_history([
            record(LOCKED, Side.AWAY, Tier.LEAN, -1.5, LOCKED.kickoff_utc - timedelta(minutes=5)),
        ])
    locked = by_id(board(db))[LOCKED.game_id]
    assert locked["model"]["side"] == "away"
    assert locked["model"]["rationale"] is None


def test_a_locked_game_the_model_never_covered_has_no_pick(db):
    later = KICK + timedelta(days=2)  # every game, the NFL one included, has kicked off
    nfl = by_id(board(db, now=later))[NFL.game_id]
    assert nfl["locked"] is True and nfl["model"] is None


def test_a_final_game_is_graded_against_the_cbs_line(db):
    final = by_id(board(db))[FINAL.game_id]
    assert final["final"] is True
    assert final["result"] == "win"  # home by 7 covers -3.5
    assert by_id(board(db))[PENDING.game_id]["result"] is None


def test_lines_stop_at_kickoff_and_give_the_market_now(db):
    games = by_id(board(db))
    locked = games[LOCKED.game_id]
    assert [p["spread"] for p in locked["lines"]] == [5.5]
    pending = games[PENDING.game_id]
    assert {p["source"] for p in pending["lines"]} == {"us", "pinnacle"}
    assert pending["market"] == {"us": -6.0, "pinnacle": -5.5}
    assert pending["gap"] == 3.0
    assert [h["tier"] for h in pending["history"]] == ["lean", "strong"]


def test_a_slight_pick_names_the_key_number_it_crosses(db):
    nfl = by_id(board(db))[NFL.game_id]
    assert nfl["model"]["tier"] == "slight"
    assert nfl["key_number"] == 3
    assert by_id(board(db))[PENDING.game_id]["key_number"] is None


def test_pool_week_one_has_only_the_cfb_board(tmp_path):
    path = tmp_path / "w1.duckdb"
    first = game(Sport.CFB, 1, "OU", "MICH", KICK)
    with Store(path) as store:
        store.init_schema()
        store.upsert_games([first])
        store.upsert_league_lines([LeagueLine(game_id=first.game_id, season=2026, week=1,
                                              spread_home=-3.0, posted_at=KICK)])
        data = build_this_week(store, 2026, NOW)
    assert data["pool_week"] == 1
    assert [g["id"] for g in data["games"]] == [first.game_id]
    assert data["games"][0]["model"]["tier"] == "no_market"


def test_a_cbs_line_without_its_game_is_left_off(db):
    with Store(db) as store:
        store.upsert_league_lines([LeagueLine(game_id="cfb-2026-06-X-at-Y", season=2026, week=6,
                                              spread_home=1.0, posted_at=KICK)])
        data = build_this_week(store, 2026, NOW)
    assert "cfb-2026-06-X-at-Y" not in by_id(data)
    assert len(data["games"]) == 4


def test_nothing_stored_is_none(tmp_path):
    with Store(tmp_path / "empty.duckdb") as store:
        store.init_schema()
        assert build_this_week(store, 2026, NOW) is None


def test_building_the_board_logs_no_pick_decisions(db, records):
    board(db)
    assert not [r for r in records if r["extra"].get("event") in ("edge_decided", "edge_measured")]
