import hashlib
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import Game, LeagueLine, MarketLine, Sport
from pickem.operations.preflight import evaluate_preflight
from pickem.store.db import StoredDataset

NOW = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)
runner = CliRunner()


def _game(game_id: str = "nfl-2026-01-AWAY-at-HOME") -> Game:
    return Game(
        game_id=game_id,
        sport=Sport.NFL,
        season=2026,
        week=1,
        kickoff_utc=NOW + timedelta(hours=2),
        home_team_id="HOME",
        away_team_id="AWAY",
    )


def _dataset() -> StoredDataset:
    game = _game()
    league = LeagueLine(
        game_id=game.game_id,
        season=game.season,
        week=game.week,
        spread_home=-3.0,
        posted_at=NOW - timedelta(hours=1),
    )
    market = [
        MarketLine(
            game_id=game.game_id,
            source="oddsapi",
            book=book,
            spread_home=-3.0,
            captured_at=NOW - timedelta(minutes=15),
        )
        for book in ("book-a", "book-b", "book-c")
    ]
    return StoredDataset(games=[game], league_lines=[league], market_lines=market)


def test_a_complete_future_slate_with_fresh_live_books_is_ready():
    result = evaluate_preflight(
        _dataset(),
        [
            _game("nfl-2025-17-OTHER-at-PRIOR").model_copy(
                update={"home_score": 24, "away_score": 17}
            )
        ],
        sport=Sport.NFL,
        now=NOW,
        expected_games=1,
        max_age_minutes=60,
        min_books=3,
    )

    assert result.ready is True
    assert result.reasons == []
    assert result.games[0].ready is True


def _history() -> list[Game]:
    return [
        _game("nfl-2025-17-OTHER-at-PRIOR").model_copy(
            update={
                "season": 2025,
                "week": 17,
                "kickoff_utc": datetime(2025, 12, 28, 17, tzinfo=UTC),
                "home_score": 24,
                "away_score": 17,
            }
        )
    ]


def _evaluate(dataset: StoredDataset, **kwargs):
    options = {
        "max_age_minutes": 60,
        "min_books": 3,
        "expected_games": 1,
    }
    options.update(kwargs)
    return evaluate_preflight(
        dataset,
        _history(),
        sport=Sport.NFL,
        now=NOW,
        **options,
    )


def test_game_count_must_match_expected_games():
    result = _evaluate(_dataset(), expected_games=2)

    assert result.ready is False
    assert "expected 2 games, found 1" in result.reasons


def test_league_line_count_must_match_expected_games():
    dataset = _dataset().model_copy(update={"league_lines": []})

    result = _evaluate(dataset)

    assert result.ready is False
    assert "expected 1 league lines, found 0" in result.reasons
    assert "missing league line" in result.games[0].reasons


def test_only_live_market_source_counts_as_coverage():
    dataset = _dataset().model_copy(
        update={
            "market_lines": [
                line.model_copy(update={"source": "oddsapi:frozen"})
                for line in _dataset().market_lines
            ]
        }
    )

    result = _evaluate(dataset)

    assert result.ready is False
    assert "missing live market coverage" in result.games[0].reasons


def test_latest_live_snapshot_must_be_fresh_and_not_future_dated():
    stale = _dataset().model_copy(
        update={
            "market_lines": [
                line.model_copy(update={"captured_at": NOW - timedelta(minutes=61)})
                for line in _dataset().market_lines
            ]
        }
    )
    future = _dataset().model_copy(
        update={
            "market_lines": [
                line.model_copy(update={"captured_at": NOW + timedelta(minutes=1)})
                for line in _dataset().market_lines
            ]
        }
    )

    stale_result = _evaluate(stale)
    future_result = _evaluate(future)

    assert any("older than 60 minutes" in reason for reason in stale_result.reasons)
    assert any("future-dated" in reason for reason in future_result.reasons)


def test_latest_snapshot_must_have_enough_distinct_books():
    dataset = _dataset().model_copy(update={"market_lines": [_dataset().market_lines[0]]})

    result = _evaluate(dataset, min_books=3)

    assert result.ready is False
    assert result.games[0].distinct_books == 1
    assert "1 distinct books; 3 required" in result.games[0].reasons[0]


def test_kickoff_must_be_valid_and_in_the_future():
    invalid = _dataset().model_copy(
        update={"games": [_game().model_copy(update={"kickoff_utc": datetime(2026, 9, 5, 17)})]}
    )
    nonfuture = _dataset().model_copy(
        update={"games": [_game().model_copy(update={"kickoff_utc": NOW})]}
    )

    invalid_result = _evaluate(invalid)
    nonfuture_result = _evaluate(nonfuture)

    assert "invalid kickoff" in invalid_result.games[0].reasons
    assert "kickoff is not in the future" in nonfuture_result.games[0].reasons


def test_elo_requires_prior_completed_history_for_the_same_sport():
    result = evaluate_preflight(
        _dataset(),
        [_game("nfl-2025-17-OTHER-at-PRIOR")],
        sport=Sport.NFL,
        now=NOW,
        expected_games=1,
    )

    assert result.ready is False
    assert "no prior completed nfl history for Elo" in result.reasons


def test_read_only_store_cannot_write(tmp_path):
    from pickem.store.db import Store

    db = tmp_path / "preflight.duckdb"
    with Store(db) as store:
        store.init_schema()
    before = hashlib.sha256(db.read_bytes()).digest()

    with Store(db, read_only=True) as store:
        assert store.load_week(Sport.NFL, 2026, 1).games == []
        try:
            store.init_schema()
        except Exception as exc:
            assert "read-only" in str(exc).lower() or "read only" in str(exc).lower()
        else:
            raise AssertionError("read-only store unexpectedly initialized schema")

    assert hashlib.sha256(db.read_bytes()).digest() == before


def test_preflight_cli_refuses_a_missing_database(tmp_path):
    db = tmp_path / "missing.duckdb"

    result = runner.invoke(
        app,
        [
            "preflight",
            "--sport",
            "nfl",
            "--season",
            "2026",
            "--week",
            "1",
            "--expected-games",
            "1",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code != 0
    assert "no database" in result.output.lower()
    assert not db.exists()


def test_preflight_cli_reads_without_writing_the_database(tmp_path):
    from pickem.store.db import Store

    db = tmp_path / "ready.duckdb"
    current = datetime.now(tz=UTC)
    target = _game().model_copy(update={"kickoff_utc": current + timedelta(hours=2)})
    league = LeagueLine(
        game_id=target.game_id,
        season=target.season,
        week=target.week,
        spread_home=-3.0,
        posted_at=current - timedelta(hours=1),
    )
    market = [
        MarketLine(
            game_id=target.game_id,
            source="oddsapi",
            book=book,
            spread_home=-3.0,
            captured_at=current - timedelta(minutes=15),
        )
        for book in ("book-a", "book-b", "book-c")
    ]
    with Store(db) as store:
        store.init_schema()
        store.upsert_games([target, *_history()])
        store.upsert_league_lines([league])
        store.append_market_lines(market)
    before = hashlib.sha256(db.read_bytes()).digest()

    result = runner.invoke(
        app,
        [
            "preflight",
            "--sport",
            "nfl",
            "--season",
            "2026",
            "--week",
            "1",
            "--expected-games",
            "1",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Overall: READY" in result.output
    assert target.game_id in result.output
    assert hashlib.sha256(db.read_bytes()).digest() == before
    with Store(db, read_only=True) as store:
        assert store.picks_for_week(2026, 1) == []


def test_preflight_cli_returns_nonzero_when_a_hard_gate_fails(tmp_path):
    from pickem.store.db import Store

    db = tmp_path / "incomplete.duckdb"
    with Store(db) as store:
        store.init_schema()
        store.upsert_games([_game(), *_history()])

    result = runner.invoke(
        app,
        [
            "preflight",
            "--sport",
            "nfl",
            "--season",
            "2026",
            "--week",
            "1",
            "--expected-games",
            "1",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code == 1
    assert "Overall: NOT READY" in result.output
    assert "missing league line" in result.output
