from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from cbs_entry_helpers import ATL, ATL_GB, GB, entry, entry_page
from cbs_fetch_helpers import JOIN_PAGE, POOL, StubSession
from typer.testing import CliRunner

from pickem import config
from pickem.cli import app
from pickem.ingest.cbs_fetch import FetchedPage
from pickem.models import Edge, Game, LeagueLine, Side, Sport, Tier
from pickem.operations.recommendations import RecommendationSnapshot
from pickem.store.db import Store

runner = CliRunner()
FUTURE = datetime(2099, 9, 25, 0, 15, tzinfo=UTC)
GAME = Game(
    game_id="nfl-2026-03-ATL-at-GB",
    sport=Sport.NFL,
    season=2026,
    week=3,
    kickoff_utc=FUTURE,
    home_team_id="GB",
    away_team_id="ATL",
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pickem.duckdb"
    with Store(path) as store:
        store.init_schema()
        store.upsert_games([GAME])
        store.upsert_league_lines(
            [
                LeagueLine(
                    game_id=GAME.game_id, season=2026, week=3, spread_home=-6.5, posted_at=FUTURE
                )
            ]
        )
    return path


@pytest.fixture
def cbs(monkeypatch):
    monkeypatch.setattr(config, "CBS_POOL_URL", POOL)
    pages: dict = {}

    @asynccontextmanager
    async def fake_open(*, profile, chrome):
        yield StubSession(pages)

    monkeypatch.setattr("pickem.cli.open_cbs_session", fake_open)
    return pages


@pytest.fixture
def model(monkeypatch):
    """The model picks GB (home) on the one NFL game; CFB has no stored week."""

    def generate(db, sport, season, week, now, **_):
        edges = (
            (
                Edge(
                    game_id=GAME.game_id,
                    side=Side.HOME,
                    delta=1.0,
                    tier=Tier.LEAN,
                    league_spread=-6.5,
                    market_spread=-7.5,
                    rationale="t",
                ),
            )
            if sport is Sport.NFL
            else ()
        )
        return RecommendationSnapshot(sport, season, week, now, edges)

    monkeypatch.setattr("pickem.cli.generate_recommendations", generate)


def invoke(db):
    return runner.invoke(
        app, ["check-picks", "--season", "2026", "--pool-week", "4", "--db", str(db)]
    )


def test_a_differing_pick_is_marked_and_summarised(db, cbs, model):
    cbs[POOL] = entry_page(entry({ATL_GB["cbsEventId"]: ATL}), events=(ATL_GB,))

    result = invoke(db)

    assert result.exit_code == 0, result.output
    assert "❌" in result.output
    assert "1 checked: 0 match, 1 differ, 0 unpicked, 0 unmatched" in result.output


def test_a_matching_pick_is_marked(db, cbs, model):
    cbs[POOL] = entry_page(entry({ATL_GB["cbsEventId"]: GB}), events=(ATL_GB,))

    result = invoke(db)

    assert result.exit_code == 0, result.output
    assert "✅" in result.output


def test_an_expired_login_exits_1_with_the_hint(db, cbs, model):
    cbs[POOL] = FetchedPage(f"{POOL}/join", JOIN_PAGE)

    result = invoke(db)

    assert result.exit_code == 1
    assert "cbs-login.sh" in result.output


def test_an_unreadable_entry_exits_1(db, cbs, model):
    cbs[POOL] = entry_page(entry({}, mine=False), events=(ATL_GB,))

    result = invoke(db)

    assert result.exit_code == 1
    assert "no entry marked as yours" in result.output
