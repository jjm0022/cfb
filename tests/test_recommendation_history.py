import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from pickem.models import (
    HISTORY_LOG_BACKFILL,
    HISTORY_MONITOR,
    Edge,
    Game,
    Side,
    Sport,
    Tier,
)
from pickem.operations.recommendation_history import (
    backfill_history,
    history_from_snapshot,
    iter_log_records,
    record_snapshot_history,
)
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import Store

KICK = datetime(2026, 9, 12, 16, tzinfo=UTC)
GID = "cfb-2026-02-PSU-at-TEM"


def edge(side: Side = Side.AWAY, tier: Tier = Tier.COINFLIP) -> Edge:
    return Edge(game_id=GID, side=side, delta=0.5, tier=tier, league_spread=24.5,
                market_spread=24.0, rationale="test")


def snapshot(at: datetime) -> RecommendationSnapshot:
    return RecommendationSnapshot(sport=Sport.CFB, season=2026, week=2, generated_at=at,
                                  edges=(edge(),))


def decision(ts: str, game_id: str = GID, side: str = "away", tier: str = "coinflip") -> str:
    return json.dumps({
        "ts": ts, "level": "INFO", "event": "edge_decided", "message": "PSU at TEM: pick PSU",
        "game_id": game_id, "side": side, "tier": tier, "delta": 0.5,
    })


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    s.upsert_games([
        Game(game_id=GID, sport=Sport.CFB, season=2026, week=2, kickoff_utc=KICK,
             home_team_id="TEM", away_team_id="PSU")
    ])
    yield s
    s.close()


def test_history_from_snapshot_keeps_side_tier_edge_and_time():
    rows = history_from_snapshot(snapshot(KICK - timedelta(hours=1)), HISTORY_MONITOR)
    fields = [(r.game_id, r.sport, r.week, r.side, r.tier, r.edge_points, r.source) for r in rows]
    assert fields == [
        (GID, Sport.CFB, 2, Side.AWAY, Tier.COINFLIP, 0.5, HISTORY_MONITOR)
    ]
    assert rows[0].generated_at == KICK - timedelta(hours=1)


def test_record_snapshot_history_is_idempotent(tmp_path):
    db = tmp_path / "pickem.duckdb"
    assert record_snapshot_history(db, snapshot(KICK - timedelta(hours=1)), HISTORY_MONITOR) == 1
    assert record_snapshot_history(db, snapshot(KICK - timedelta(hours=1)), HISTORY_MONITOR) == 0


def test_iter_log_records_reads_current_and_rotated_files(tmp_path):
    (tmp_path / "pickem.jsonl").write_text(decision("2026-09-11T10:00:00-04:00") + "\nnot json\n")
    with zipfile.ZipFile(tmp_path / "pickem.2026-09-10_00-00-00_1.jsonl.zip", "w") as archive:
        archive.writestr(
            "pickem.2026-09-10_00-00-00_1.jsonl", decision("2026-09-10T10:00:00-04:00")
        )
    (tmp_path / "pickem.log").write_text(decision("2026-09-09T10:00:00-04:00"))
    assert [r["ts"] for r in iter_log_records(tmp_path)] == [
        "2026-09-10T10:00:00-04:00",
        "2026-09-11T10:00:00-04:00",
    ]


def test_iter_log_records_skips_a_corrupt_rotated_archive(tmp_path):
    (tmp_path / "pickem.jsonl").write_text(decision("2026-09-11T10:00:00-04:00"))
    (tmp_path / "pickem.2026-09-10_00-00-00_000000.jsonl.zip").write_bytes(b"not a zip file")
    assert [r["ts"] for r in iter_log_records(tmp_path)] == ["2026-09-11T10:00:00-04:00"]


def test_backfill_reads_pick_batches_and_logged_decisions(store, tmp_path):
    store.record_picks([edge(Side.HOME, Tier.LEAN)], 2026, 2, KICK - timedelta(days=4))
    (tmp_path / "pickem.jsonl").write_text("\n".join([
        decision("2026-09-11T10:00:00-04:00"),
        decision("2026-09-11T11:00:00-04:00", game_id="cfb-2026-02-XXX-at-YYY"),
        json.dumps({"ts": "2026-09-11T10:00:00-04:00", "event": "refresh_started"}),
        decision("2026-09-11T12:00:00-04:00", game_id="nfl-2025-01-BUF-at-MIA"),
    ]))
    with zipfile.ZipFile(tmp_path / "pickem.2026-09-10_00-00-00_1.jsonl.zip", "w") as archive:
        archive.writestr("x.jsonl", decision("2026-09-10T10:00:00-04:00", side="home"))

    summary = backfill_history(store, season=2026, log_dir=tmp_path)

    assert (summary.from_picks, summary.from_logs) == (1, 2)
    assert (summary.log_decisions_seen, summary.log_decisions_unmatched) == (3, 1)
    logged = [r for r in store.recommendation_history([GID]) if r.source == HISTORY_LOG_BACKFILL]
    assert [(r.generated_at, r.side) for r in logged] == [
        (datetime(2026, 9, 10, 14, tzinfo=UTC), Side.HOME),
        (datetime(2026, 9, 11, 14, tzinfo=UTC), Side.AWAY),
    ]

    again = backfill_history(store, season=2026, log_dir=tmp_path)
    assert (again.from_picks, again.from_logs) == (0, 0)


def test_backfill_tolerates_a_missing_log_directory(store, tmp_path):
    summary = backfill_history(store, season=2026, log_dir=tmp_path / "absent")
    assert (summary.from_logs, summary.log_decisions_seen) == (0, 0)
