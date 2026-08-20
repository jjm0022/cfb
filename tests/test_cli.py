from typer.testing import CliRunner

from pickem.cli import app
from pickem.models import Sport

runner = CliRunner()


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


class _StubClient:
    """An OddsClient that answers without a network or a credit."""

    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def fetch_historical_spreads(self, *args, **kwargs):
        from pickem.models import MarketLine, MarketLinesResult

        _StubClient.calls.append(kwargs)
        return MarketLinesResult(
            lines=[
                MarketLine(
                    game_id=game_id,
                    source=kwargs["source"],
                    book="pinnacle",
                    spread_home=-3.0,
                    captured_at=kwargs["at"],
                )
                for game_id in sorted(kwargs["slate"])
            ],
            skipped=["some-other-game: not in the slate — not stored"],
        )


def test_backfill_history_dry_run_spends_nothing(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)

    def explode(*args, **kwargs):
        raise AssertionError("a dry run must never construct a client")

    monkeypatch.setattr("pickem.cli.OddsClient", explode)
    result = runner.invoke(
        app, ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db)]
    )
    assert result.exit_code == 0, result.output
    assert "dry run" in result.stdout.lower()
    # One frozen anchor plus two kickoff slots, at 10 credits each.
    assert "3 snapshots" in result.stdout
    assert "30 credits" in result.stdout


def test_backfill_history_refuses_to_exceed_the_credit_ceiling(tmp_path):
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    result = runner.invoke(
        app,
        [
            "backfill-history",
            "--from",
            "2024",
            "--to",
            "2024",
            "--db",
            str(db),
            "--execute",
            "--max-credits",
            "1",
        ],
    )
    assert result.exit_code == 1
    assert "exceeds" in result.output.lower()


def test_backfill_history_without_stored_games_says_what_to_run_first(tmp_path):
    from pickem.store.db import Store

    db = tmp_path / "empty.duckdb"
    with Store(db) as store:
        store.init_schema()
    result = runner.invoke(
        app, ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "backfill" in result.output.lower()


def test_backfill_history_writes_both_proxies_and_reports_skips(tmp_path, monkeypatch):
    _StubClient.calls = []
    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    monkeypatch.setattr("pickem.cli.OddsClient", _StubClient)
    monkeypatch.setattr("pickem.config.odds_api_key", lambda: "test-key")
    result = runner.invoke(
        app,
        ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db), "--execute"],
    )
    assert result.exit_code == 0, result.output
    # Nothing a source could not give us is dropped in silence.
    assert "not in the slate" in result.stdout

    sources = {call["source"] for call in _StubClient.calls}
    assert sources == {"oddsapi:frozen", "oddsapi:submit"}

    from pickem.store.db import Store

    with Store(db) as store:
        dataset = store.load_week(Sport.NFL, 2024, 3)
        stored = [line for line in dataset.market_lines if line.game_id == "nfl-2024-03-BUF-at-MIA"]
    assert {line.source for line in stored} == {"oddsapi:frozen", "oddsapi:submit"}


def test_backfill_history_stops_on_quota_exhaustion(tmp_path, monkeypatch):
    from pickem.ingest.odds import QuotaExhausted

    class _Broke(_StubClient):
        def fetch_historical_spreads(self, *args, **kwargs):
            raise QuotaExhausted("odds api returned 401")

    db = tmp_path / "t.duckdb"
    _seed_two_slots(db)
    monkeypatch.setattr("pickem.cli.OddsClient", _Broke)
    monkeypatch.setattr("pickem.config.odds_api_key", lambda: "test-key")
    result = runner.invoke(
        app,
        ["backfill-history", "--from", "2024", "--to", "2024", "--db", str(db), "--execute"],
    )
    # Stopping loudly beats half a backfill nobody knows is half.
    assert result.exit_code == 1
    assert "stopped at snapshot 1/3" in result.output
