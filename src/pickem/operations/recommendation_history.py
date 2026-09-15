"""What the model recommended for each game, and when.

This is not the `picks` table. `picks` records a rendered `report` batch, and
`report` stays the only command that writes it; what was actually submitted now
comes from the CBS standings page. History exists so a result can be graded
against the last recommendation before kickoff, which is what the owner acted on.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from pickem.models import (
    HISTORY_LOG_BACKFILL,
    Game,
    RecommendationRecord,
    Side,
    Sport,
    Tier,
)
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import Store

_EDGE_EVENT = "edge_decided"


@dataclass(frozen=True)
class BackfillSummary:
    from_picks: int
    from_logs: int
    log_decisions_seen: int
    log_decisions_unmatched: int


def history_from_snapshot(
    snapshot: RecommendationSnapshot, source: str
) -> list[RecommendationRecord]:
    sport = Sport(snapshot.sport)
    return [
        RecommendationRecord(
            game_id=edge.game_id,
            sport=sport,
            season=snapshot.season,
            week=snapshot.week,
            side=edge.side,
            tier=edge.tier,
            edge_points=edge.delta,
            generated_at=snapshot.generated_at,
            source=source,
        )
        for edge in snapshot.edges
    ]


def record_snapshot_history(db: Path, snapshot: RecommendationSnapshot, source: str) -> int:
    """Append one snapshot's recommendations to the store at ``db``."""
    db.parent.mkdir(parents=True, exist_ok=True)
    with Store(db) as store:
        store.init_schema()
        added = store.append_recommendation_history(history_from_snapshot(snapshot, source))
    logger.bind(
        event="recommendation_history_recorded", source=source,
        sport=Sport(snapshot.sport).value, season=snapshot.season, week=snapshot.week,
        rows=added,
    ).debug(f"recorded {added} recommendation history row(s) from {source}")
    return added


def iter_log_records(log_dir: Path) -> Iterator[dict]:
    """Yield JSON records from current and rotated JSONL logs, oldest file first.

    Rotated files are zipped by the logger. Lines that are not JSON objects are
    skipped: a record cut off mid-write must not stop a backfill.
    """
    for path in sorted(log_dir.glob("pickem*.jsonl*")):
        if path.name.endswith(".jsonl.zip"):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    with archive.open(member) as handle:
                        yield from _json_objects(
                            line.decode("utf-8", errors="replace") for line in handle
                        )
        elif path.suffix == ".jsonl":
            with path.open(encoding="utf-8", errors="replace") as handle:
                yield from _json_objects(handle)


def _json_objects(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield record


def _history_from_log(record: dict, games: dict[str, Game]) -> RecommendationRecord | None:
    game = games.get(str(record.get("game_id")))
    if game is None:
        return None
    try:
        return RecommendationRecord(
            game_id=game.game_id,
            sport=game.sport,
            season=game.season,
            week=game.week,
            side=Side(record["side"]),
            tier=Tier(record["tier"]),
            edge_points=float(record["delta"]),
            generated_at=datetime.fromisoformat(record["ts"]),
            source=HISTORY_LOG_BACKFILL,
        )
    except (KeyError, TypeError, ValueError):
        return None


def backfill_history(store: Store, *, season: int, log_dir: Path) -> BackfillSummary:
    """Rebuild history from recorded `report` batches and logged decisions."""
    from_picks = store.append_recommendation_history(store.pick_batches(season))

    if not log_dir.is_dir():
        logger.bind(event="log_directory_missing", log_dir=str(log_dir)).warning(
            f"no log directory at {log_dir}; only report batches were backfilled"
        )

    games = {game.game_id: game for game in store.games_for_season(season)}
    season_tag = f"-{season}-"
    seen = 0
    records: list[RecommendationRecord] = []
    if log_dir.is_dir():
        for record in iter_log_records(log_dir):
            if record.get("event") != _EDGE_EVENT or season_tag not in str(record.get("game_id")):
                continue
            seen += 1
            row = _history_from_log(record, games)
            if row is not None:
                records.append(row)
    from_logs = store.append_recommendation_history(records)

    summary = BackfillSummary(from_picks, from_logs, seen, seen - len(records))
    logger.bind(
        event="recommendation_history_backfilled", season=season, log_dir=str(log_dir),
        from_picks=from_picks, from_logs=from_logs, log_decisions_seen=seen,
        log_decisions_unmatched=summary.log_decisions_unmatched,
    ).info(
        f"backfilled {from_picks} report row(s) and {from_logs} logged decision(s); "
        f"{summary.log_decisions_unmatched} of {seen} logged decisions matched no stored game"
    )
    return summary
