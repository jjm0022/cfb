import hashlib
from datetime import timedelta

import pytest
from results_helpers import IMPORTED_AT, KICK, parsed_fixture, seed
from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import RecommendationRecord, Side, Sport, Tier
from pickem.operations.results_import import import_results
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store

runner = CliRunner()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pickem.duckdb"
    with Store(path) as store:
        store.init_schema()
        seed(store)
        import_results(
            store, parsed_fixture(), season=2026, pool_week=2,
            resolver=TeamResolver.default(), imported_at=IMPORTED_AT,
        )
        store.append_recommendation_history([
            RecommendationRecord(
                game_id="cfb-2026-02-OU-at-MICH", sport=Sport.CFB, season=2026, week=2,
                side=Side.HOME, tier=Tier.COINFLIP, edge_points=0.0,
                generated_at=KICK - timedelta(hours=2), source="refresh",
            )
        ])
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(db, *extra):
    return runner.invoke(
        app, ["simulate-weekly-win", "--season", "2026", "--draws", "1000", "--db", str(db), *extra]
    )


def test_prints_each_week_and_the_season_and_writes_nothing(db):
    before = digest(db)
    result = invoke(db)
    assert result.exit_code == 0, result.output
    assert "Pool week 2: 4 entrants, 1,000 simulated weeks" in result.output
    for rule in ("current", "underdog", "minority*", "lopsided underdog*", "optimal mix*"):
        assert rule in result.output
    assert "no stored recommendation (treated as 50/50): 3" in result.output
    assert "Season, pool week 2" in result.output
    assert digest(db) == before


def test_same_seed_same_output(db):
    assert invoke(db, "--seed", "5").output == invoke(db, "--seed", "5").output


def test_explicit_week_range(db):
    result = invoke(db, "--pool-weeks", "2-2")
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--pool-weeks", "3"], "pool week 3 of 2026 is not imported"),
        (["--pool-weeks", "4-2"], "--pool-weeks"),
        (["--pool-weeks", "two"], "--pool-weeks"),
        (["--entry-name", "Nobody"], "no entrant named 'Nobody'"),
    ],
)
def test_bad_input_exits_1_with_a_message(db, extra, message):
    result = invoke(db, *extra)
    assert result.exit_code == 1
    assert message in result.output


def test_missing_database(tmp_path):
    result = invoke(tmp_path / "absent.duckdb")
    assert result.exit_code == 1
    assert "no database" in result.output


def test_season_with_nothing_imported(db):
    result = runner.invoke(
        app, ["simulate-weekly-win", "--season", "2025", "--draws", "1000", "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "no pool weeks of 2025 are imported" in result.output
