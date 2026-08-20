"""One walk through the real weekly workflow, end to end.

Every module here is the shipped one and the database is a real DuckDB file.
Only the HTTP transport is faked, so this exercises the seams that per-module
tests with injected fakes structurally cannot see: whether the game ids CBS
writes are the ones the odds feed joins on, whether a stored snapshot actually
reaches the final decision path, and whether the rendered sheet names real matchups.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from pickem import cli
from pickem.ingest.odds import OddsClient
from pickem.models import Sport
from pickem.store.db import Store

runner = CliRunner()

PASTE = "Buffalo Bills at Miami Dolphins -3.0\nDallas Cowboys at New York Jets +2.5\n"


def _event(home, away, kickoff, home_point):
    return {
        "id": f"{away}-at-{home}",
        "commence_time": kickoff.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": book,
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": home, "point": home_point + offset},
                            {"name": away, "point": -(home_point + offset)},
                        ],
                    }
                ],
            }
            for book, offset in (("pinnacle", 0.0), ("draftkings", 0.5))
        ],
    }


@pytest.fixture
def payload():
    soon = datetime.now(tz=UTC) + timedelta(days=2)
    later = datetime.now(tz=UTC) + timedelta(days=40)
    return [
        # On our sheet: the market has moved three points toward Miami.
        _event("Miami Dolphins", "Buffalo Bills", soon, -6.0),
        # On our sheet: barely moved, so this one lands as a coinflip.
        _event("New York Jets", "Dallas Cowboys", soon, 2.5),
        # A later week the feed also returns. It must never be stored: its id
        # would be stamped with THIS week's number and poison the append-only
        # lines table forever.
        _event("Green Bay Packers", "Chicago Bears", later, -1.5),
    ]


@pytest.fixture
def fake_odds(monkeypatch, payload):
    monkeypatch.setenv("ODDS_API_KEY", "test-key")

    def build(api_key, **kwargs):
        transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))
        return OddsClient(api_key, transport=transport, sleep=lambda _s: None)

    monkeypatch.setattr(cli, "OddsClient", build)


def _run(*args):
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, f"{args} failed:\n{result.output}\n{result.exception}"
    return result


def test_paste_to_pick_sheet(tmp_path, fake_odds):
    db = tmp_path / "e2e.duckdb"
    paste = tmp_path / "week3.txt"
    paste.write_text(PASTE)
    week = ["--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)]

    ingest = _run("ingest-cbs", "--file", str(paste), *week)
    assert "ingested 2 games" in ingest.stdout

    poll = _run("poll-odds", *week)
    assert "appended 4 market lines" in poll.stdout
    # The out-of-week event is reported rather than silently stored.
    assert "outside" in poll.stdout

    out = tmp_path / "sheet.md"
    report = _run("report", *week, "--out", str(out))
    sheet = out.read_text()
    assert sheet == report.stdout.rstrip("\n") or sheet in report.stdout

    # The sheet names real matchups by canonical id, and names a side.
    assert "BUF at MIA" in sheet
    assert "DAL at NYJ" in sheet
    assert "**MIA**" in sheet
    # Both games had a market, so nothing falls through to NO_MARKET.
    assert "no_market" not in sheet.lower()
    # Provenance and age are always present.
    assert "CBS frozen league lines" in sheet
    assert "unknown/unavailable" not in sheet

    # The strongest divergence is ranked first and picks the side the frozen
    # line is generous on: league -3.0 vs market -6.25 is +3.25 toward home.
    assert sheet.index("BUF at MIA") < sheet.index("DAL at NYJ")

    # The near-flat game fell to the tiebreak rather than being dropped, and the
    # clear divergence was NOT overridden by it.
    assert "coinflip" in sheet
    assert "strong" in sheet

    with Store(db) as store:
        dataset = store.load_week(Sport.NFL, 2025, 3)
        stored = {line.game_id for line in dataset.market_lines}
        assert stored == {"nfl-2025-03-BUF-at-MIA", "nfl-2025-03-DAL-at-NYJ"}
        assert not any("CHI" in gid or "GB" in gid for gid in stored)

        picks = store.picks_for_week(2025, 3)
        assert {row[2] for row in picks} == stored
        assert {row[3] for row in picks} <= {"home", "away"}


def test_a_second_poll_appends_history_rather_than_overwriting(tmp_path, fake_odds):
    db = tmp_path / "e2e.duckdb"
    paste = tmp_path / "week3.txt"
    paste.write_text(PASTE)
    week = ["--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)]

    _run("ingest-cbs", "--file", str(paste), *week)
    _run("poll-odds", *week)
    _run("poll-odds", *week)

    # Line movement is the signal: a re-poll at a new instant is history, not an
    # update. Identical (game, source, book, captured_at) tuples still collapse.
    with Store(db) as store:
        dataset = store.load_week(Sport.NFL, 2025, 3)
        lines = [line for line in dataset.market_lines if line.game_id == "nfl-2025-03-BUF-at-MIA"]
    assert len(lines) >= 2
    assert len({line.captured_at for line in lines}) >= 1


def test_reingesting_the_paste_does_not_destroy_synced_scores(tmp_path, fake_odds):
    db = tmp_path / "e2e.duckdb"
    paste = tmp_path / "week3.txt"
    paste.write_text(PASTE)
    week = ["--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)]

    _run("ingest-cbs", "--file", str(paste), *week)

    from pickem.models import Game

    with Store(db) as store:
        game = store.load_week(Sport.NFL, 2025, 3).games[0]
        store.upsert_games([game.model_copy(update={"home_score": 24, "away_score": 17})])

    _run("ingest-cbs", "--file", str(paste), *week)

    with Store(db) as store:
        after = {g.game_id: g for g in store.load_week(Sport.NFL, 2025, 3).games}
    assert (after[game.game_id].home_score, after[game.game_id].away_score) == (24, 17)
    assert isinstance(after[game.game_id], Game)


def test_poll_odds_refuses_before_the_week_exists(tmp_path, fake_odds):
    db = tmp_path / "e2e.duckdb"
    result = runner.invoke(
        cli.app,
        ["poll-odds", "--sport", "nfl", "--season", "2025", "--week", "3", "--db", str(db)],
    )
    assert result.exit_code == 1
    assert "run ingest-cbs first" in result.output
    assert not db.exists()
