import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from pickem.backtest.archive import ArchiveRunInterrupted
from pickem.cli import app

runner = CliRunner()


def _seed_coinflip_experiment(db):
    """Five balanced CFB seasons with the two archived proxy roles."""
    from pickem.models import Game, MarketLine, Sport
    from pickem.store.db import Store

    games = []
    lines = []
    for season in range(2021, 2026):
        for week, target, index in ((1, 0, 0), (2, 1, 1), (9, 0, 2), (10, 1, 3)):
            kickoff = datetime(season, 9, min(week, 28), 17, tzinfo=UTC)
            game_id = f"cfb-{season}-{week:02d}-A{index}-at-H{index}"
            games.append(
                Game(
                    game_id=game_id,
                    sport=Sport.CFB,
                    season=season,
                    week=week,
                    kickoff_utc=kickoff,
                    home_team_id=f"H{index}",
                    away_team_id=f"A{index}",
                    home_score=27 if target else 20,
                    away_score=20 if target else 24,
                )
            )
            lines.append(
                MarketLine(
                    game_id=game_id,
                    source="oddsapi:frozen",
                    book="a",
                    spread_home=-3.0,
                    captured_at=kickoff - timedelta(days=3),
                )
            )
            lines.extend(
                MarketLine(
                    game_id=game_id,
                    source="oddsapi:submit",
                    book=book,
                    spread_home=spread,
                    captured_at=kickoff - timedelta(minutes=10),
                )
                for book, spread in (("a", -3.8), ("b", -3.0), ("c", -2.2))
            )
    excluded_kickoff = datetime(2025, 11, 15, 17, tzinfo=UTC)
    excluded_game_id = "cfb-2025-11-AX-at-HX"
    games.append(
        Game(
            game_id=excluded_game_id,
            sport=Sport.CFB,
            season=2025,
            week=11,
            kickoff_utc=excluded_kickoff,
            home_team_id="HX",
            away_team_id="AX",
        )
    )
    lines.append(
        MarketLine(
            game_id=excluded_game_id,
            source="oddsapi",
            book="coverage",
            spread_home=-3.0,
            captured_at=excluded_kickoff - timedelta(minutes=10),
        )
    )
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(games)
        store.append_market_lines(lines)


def test_evaluate_coinflip_writes_deterministic_auditable_artifacts(tmp_path):
    """Catches an unpersisted, unordered, or timestamped CFB experiment run."""
    db = tmp_path / "coinflip.duckdb"
    _seed_coinflip_experiment(db)
    first_predictions = tmp_path / "first.jsonl"
    first_report = tmp_path / "first.md"
    second_predictions = tmp_path / "second.jsonl"
    second_report = tmp_path / "second.md"
    command = [
        "evaluate-coinflip",
        "--sport",
        "cfb",
        "--from",
        "2021",
        "--to",
        "2025",
        "--db",
        str(db),
    ]

    first = runner.invoke(
        app,
        [
            *command,
            "--predictions",
            str(first_predictions),
            "--report",
            str(first_report),
        ],
    )
    second = runner.invoke(
        app,
        [
            *command,
            "--predictions",
            str(second_predictions),
            "--report",
            str(second_report),
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    predictions = [json.loads(line) for line in first_predictions.read_text().splitlines()]
    assert [(row["season"], row["week"], row["game_id"]) for row in predictions] == sorted(
        (row["season"], row["week"], row["game_id"]) for row in predictions
    )
    assert first_predictions.read_bytes() == second_predictions.read_bytes()
    assert first_report.read_bytes() == second_report.read_bytes()
    markdown = first_report.read_text()
    for text in [
        "Training seasons",
        "Test season",
        "Chosen C",
        "Fitted fold/model state",
        '"means":[0.0,0.0,0.0]',
        '"scales":[1.0,1.0,1.0]',
        '"coefficients":',
        '"intercept":',
        "Paired accuracy delta",
        "Brier score",
        "Log loss",
        "Calibration",
        "Bootstrap 95% interval",
        "accuracy delta >= 1.0 percentage point",
        "at least 3 positive seasons",
        "Brier score < 0.25",
        "leakage-safe replay",
        "deterministic execution",
        "Coverage and exclusions",
        "Eligible feature rows: 20",
        "Outer-fold predictions: 16",
        "unplayed game (missing final score)",
        "neither the frozen-line nor the submission-time proxy",
        "Candidate 1: NULL",
    ]:
        assert text in markdown
    assert first_predictions.read_bytes().endswith(b"\n")
    assert first_report.read_bytes().endswith(b"\n")


def test_evaluate_coinflip_refuses_nfl(tmp_path):
    """Catches the approved CFB-only experiment silently accepting NFL data."""
    result = runner.invoke(
        app,
        [
            "evaluate-coinflip",
            "--sport",
            "nfl",
            "--from",
            "2021",
            "--to",
            "2025",
            "--predictions",
            str(tmp_path / "predictions.jsonl"),
            "--report",
            str(tmp_path / "report.md"),
        ],
    )

    assert result.exit_code != 0
    assert "CFB-only" in result.output


def test_evaluate_coinflip_refuses_an_unapproved_season_range(tmp_path):
    """Catches silently changing the precommitted one-shot experiment window."""
    result = runner.invoke(
        app,
        [
            "evaluate-coinflip",
            "--sport",
            "cfb",
            "--from",
            "2020",
            "--to",
            "2025",
            "--predictions",
            str(tmp_path / "predictions.jsonl"),
            "--report",
            str(tmp_path / "report.md"),
            "--db",
            str(tmp_path / "missing.duckdb"),
        ],
    )

    assert result.exit_code != 0
    assert "only permits" in result.output


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in [
        "ingest-cbs",
        "poll-odds",
        "report",
        "sync-results",
        "backfill",
        "backfill-history",
        "backtest",
    ]:
        assert command in result.stdout


def test_ingest_cbs_reports_a_parse_summary(tmp_path):
    paste = tmp_path / "week3.txt"
    paste.write_text("Buffalo Bills at Miami Dolphins -3.0\n")
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest-cbs",
            "--file",
            str(paste),
            "--sport",
            "nfl",
            "--season",
            "2025",
            "--week",
            "3",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0
    assert "1" in result.stdout


def test_ingest_cbs_exits_nonzero_on_an_unknown_team(tmp_path):
    paste = tmp_path / "bad.txt"
    paste.write_text("Fictional State Aardvarks at Miami Dolphins -3.0\n")
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest-cbs",
            "--file",
            str(paste),
            "--sport",
            "nfl",
            "--season",
            "2025",
            "--week",
            "3",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code != 0


def test_ingest_cbs_rejects_a_partially_parsed_block_without_creating_a_database(tmp_path):
    paste = tmp_path / "mixed.txt"
    paste.write_text(
        "Buffalo Bills at Miami Dolphins -3.0\nKansas City Chiefs -6.5 at New York Jets 45.5\n"
    )
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest-cbs",
            "--file",
            str(paste),
            "--sport",
            "nfl",
            "--season",
            "2025",
            "--week",
            "3",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code != 0
    assert "skipped" in result.stdout
    assert "Kansas City Chiefs -6.5 at New York Jets 45.5" in result.stdout
    assert not db.exists()


def _seed(db):
    """A single completed 2025 week-3 game with a frozen line and one closer."""
    from datetime import UTC, datetime

    from pickem.models import Game, LeagueLine, MarketLine, Sport
    from pickem.store.db import Store

    gid = "nfl-2025-03-BUF-at-MIA"
    kick = datetime(2025, 9, 21, 17, 0, tzinfo=UTC)
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(
            [
                Game(
                    game_id=gid,
                    sport=Sport.NFL,
                    season=2025,
                    week=3,
                    kickoff_utc=kick,
                    home_team_id="MIA",
                    away_team_id="BUF",
                    home_score=24,
                    away_score=17,
                )
            ]
        )
        store.upsert_league_lines(
            [LeagueLine(game_id=gid, season=2025, week=3, spread_home=-3.0, posted_at=kick)]
        )
        store.append_market_lines(
            [
                MarketLine(
                    game_id=gid,
                    source="oddsapi",
                    book="pinnacle",
                    spread_home=-6.0,
                    captured_at=kick,
                )
            ]
        )
    return gid


def test_report_shows_provenance_and_records_its_picks(tmp_path):
    from pickem.store.db import Store

    db = tmp_path / "test.duckdb"
    _seed(db)
    result = runner.invoke(
        app,
        ["report", "--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)],
    )
    assert result.exit_code == 0, result.output
    assert "CBS frozen league lines" in result.stdout

    with Store(db) as store:
        picks = store.picks_for_week(2025, 3)
    assert len(picks) == 1
    assert picks[0][2] == "nfl-2025-03-BUF-at-MIA"
    assert picks[0][5] == "strong"


def test_backtest_explains_why_nothing_was_graded(tmp_path):
    # Before `backfill-history` there are no frozen-line snapshots at all, so
    # every game is excluded. A bare 0-0-0 with no reason is the silent
    # degradation the project forbids.
    db = tmp_path / "test.duckdb"
    _seed(db)
    result = runner.invoke(app, ["backtest", "--from", "2025", "--to", "2025", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "0-0-0" in result.stdout
    assert "games not graded" in result.stdout
    assert "missing frozen-line snapshot" in result.stdout
    # The stored in-season oddsapi snapshot is neither proxy and must be named,
    # not silently graded as a submission-time line.
    assert "neither the frozen-line nor the submission-time proxy" in result.stdout


def test_poll_odds_refuses_to_run_before_the_week_is_ingested(tmp_path):
    db = tmp_path / "test.duckdb"
    result = runner.invoke(
        app,
        ["poll-odds", "--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)],
    )
    assert result.exit_code == 1
    assert "run ingest-cbs first" in result.output


def _seed_two_slots(db):
    """Two completed 2024 week-3 games at two kickoff slots.

    That shape plans to exactly three snapshots — one frozen anchor plus one
    per slot — so the credit arithmetic can be asserted without a season-sized
    fixture.
    """
    from datetime import UTC, datetime

    from pickem.models import Game, Sport, make_game_id
    from pickem.store.db import Store

    games = [
        Game(
            game_id=make_game_id(Sport.NFL, 2024, 3, "BUF", "MIA"),
            sport=Sport.NFL,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 22, 17, 0, tzinfo=UTC),
            home_team_id="MIA",
            away_team_id="BUF",
            home_score=30,
            away_score=20,
        ),
        Game(
            game_id=make_game_id(Sport.NFL, 2024, 3, "DAL", "NYG"),
            sport=Sport.NFL,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 24, 0, 15, tzinfo=UTC),
            home_team_id="NYG",
            away_team_id="DAL",
            home_score=17,
            away_score=21,
        ),
    ]
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(games)


def _seed_cfb_compact_slots(db):
    """Two CFB games 75 minutes apart, which a 90-minute planner batches."""
    from datetime import UTC, datetime

    from pickem.models import Game, Sport, make_game_id
    from pickem.store.db import Store

    games = [
        Game(
            game_id=make_game_id(Sport.CFB, 2024, 3, "BAMA", "UGA"),
            sport=Sport.CFB,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 22, 17, 0, tzinfo=UTC),
            home_team_id="UGA",
            away_team_id="BAMA",
            home_score=24,
            away_score=17,
        ),
        Game(
            game_id=make_game_id(Sport.CFB, 2024, 3, "LSU", "TENN"),
            sport=Sport.CFB,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 22, 18, 15, tzinfo=UTC),
            home_team_id="TENN",
            away_team_id="LSU",
            home_score=21,
            away_score=20,
        ),
    ]
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(games)


def test_backfill_history_dry_run_prints_plan_without_spending(tmp_path, monkeypatch):
    # Catches eagerly reading the paid-adapter key for a dry run.
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)

    def explode():
        raise AssertionError("dry run read the paid-adapter key")

    monkeypatch.setattr("pickem.config.odds_api_key", explode)
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "nfl",
            "--from",
            "2024",
            "--to",
            "2024",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "3 planned" in result.output
    assert "30 credits" in result.output
    assert "dry run" in result.output.lower()


def test_backfill_history_cfb_dry_run_batches_with_a_90_minute_maximum(tmp_path, monkeypatch):
    # Catches defaulting CFB to the NFL's 15-minute batching window, which
    # purchases an unnecessary third snapshot for these two kickoff slots.
    db = tmp_path / "cfb.duckdb"
    _seed_cfb_compact_slots(db)
    monkeypatch.setattr(
        "pickem.config.odds_api_key",
        lambda: (_ for _ in ()).throw(AssertionError("dry run read the paid-adapter key")),
    )
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "cfb",
            "--from",
            "2024",
            "--to",
            "2024",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2 planned" in result.output
    assert "20 credits" in result.output


def test_backfill_history_rejects_a_non_90_minute_cfb_paid_run(tmp_path):
    db = tmp_path / "cfb.duckdb"
    _seed_cfb_compact_slots(db)
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "cfb",
            "--from",
            "2024",
            "--to",
            "2024",
            "--max-snapshot-age-minutes",
            "15",
            "--execute",
            "--expected-credits",
            "20",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 1
    assert "90" in result.output


def test_backfill_history_paid_run_requires_an_expected_credit_total(tmp_path, monkeypatch):
    # Catches reaching the client factory before the operator confirms the
    # exact planned spend.
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    monkeypatch.setattr(
        "pickem.config.odds_api_key",
        lambda: (_ for _ in ()).throw(AssertionError("paid run constructed a client")),
    )
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "nfl",
            "--from",
            "2024",
            "--to",
            "2024",
            "--execute",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 1
    assert "expected-credits" in result.output


def test_backfill_history_rejects_mismatched_credits_without_constructing_a_client(
    tmp_path, monkeypatch
):
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    monkeypatch.setattr(
        "pickem.config.odds_api_key",
        lambda: (_ for _ in ()).throw(AssertionError("mismatch constructed a client")),
    )
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "nfl",
            "--from",
            "2024",
            "--to",
            "2024",
            "--execute",
            "--expected-credits",
            "999",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 1
    assert "expected 999 pending credits" in result.output


def test_backfill_history_forwards_the_probe_cap_and_prints_execution_balances(
    tmp_path, monkeypatch
):
    # Catches dropping --max-new-requests at the CLI boundary or hiding the
    # already-completed/pending ledger state after the paid probe.
    from datetime import UTC, datetime

    from pickem.models import MarketLinesResult

    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)

    class Client:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            return None

        def remaining_credits(self):
            return 2_000

        def fetch_historical_spreads(self, _sport_key, **kwargs):
            self.calls.append(kwargs)
            return MarketLinesResult(
                lines=[],
                skipped=[],
                snapshot_at=datetime(2024, 9, 22, 16, 45, tzinfo=UTC),
            )

    client = Client()
    monkeypatch.setattr("pickem.cli.OddsClient", lambda _key: client)
    monkeypatch.setattr("pickem.config.odds_api_key", lambda: "test-key")
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "nfl",
            "--from",
            "2024",
            "--to",
            "2024",
            "--execute",
            "--expected-credits",
            "30",
            "--max-new-requests",
            "1",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(client.calls) == 1
    assert "3 planned" in result.output
    assert "0 complete" in result.output
    assert "3 pending = 30 credits" in result.output
    assert "balance: 2000 credits" in result.output


def test_backfill_history_translates_credit_limit_to_exit_one(tmp_path):
    # Catches leaking ArchiveBackfill's credit-limit exception as a traceback.
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "nfl",
            "--from",
            "2024",
            "--to",
            "2024",
            "--db",
            str(db),
            "--execute",
            "--expected-credits",
            "30",
            "--max-credits",
            "1",
        ],
    )
    assert result.exit_code == 1
    assert "credit ceiling" in result.output.lower()


def test_backfill_history_translates_interruption_to_exit_one(tmp_path, monkeypatch):
    # Catches leaking an interrupted archive run instead of presenting its position.
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    monkeypatch.setattr(
        "pickem.cli.ArchiveBackfill.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ArchiveRunInterrupted(2, 3, "test quota exhausted")
        ),
    )
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--sport",
            "nfl",
            "--from",
            "2024",
            "--to",
            "2024",
            "--db",
            str(db),
            "--execute",
            "--expected-credits",
            "30",
        ],
    )
    assert result.exit_code == 1
    assert "stopped at snapshot 2/3" in result.output
