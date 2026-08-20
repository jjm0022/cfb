from datetime import UTC, datetime

import pytest

from pickem.backtest.archive import (
    ArchiveBackfill,
    ArchiveRunInterrupted,
    CreditLimitExceeded,
    NoHistoricalGames,
)
from pickem.ingest.odds import QuotaExhausted
from pickem.models import Game, MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


class StubHistoricalClient:
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def fetch_historical_spreads(self, sport_key, **kwargs):
        self.calls.append({"sport_key": sport_key, **kwargs})
        if self.fail_on_call == len(self.calls):
            raise QuotaExhausted("test quota exhausted")
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
            skipped=["off-slate game — not stored"],
        )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "archive.duckdb") as value:
        value.init_schema()
        yield value


@pytest.fixture
def games():
    values = [
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
    return values


def test_dry_run_reports_cost_without_constructing_a_client(store, games):
    def explode():
        raise AssertionError("dry run constructed the paid adapter")

    report = ArchiveBackfill(store, TeamResolver.default(), explode).run(
        games, max_credits=20_000, execute=False
    )

    assert report.total_snapshots == 3
    assert report.credits == 30
    assert report.executed_snapshots == 0


def test_credit_ceiling_fails_before_constructing_a_client(store, games):
    def explode():
        raise AssertionError("rejected plan constructed the paid adapter")

    with pytest.raises(CreditLimitExceeded):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            games, max_credits=1, execute=True
        )


def test_execution_assigns_both_proxy_roles_and_appends_results(store, games):
    client = StubHistoricalClient()
    progress = []
    store.upsert_games(games)

    report = ArchiveBackfill(store, TeamResolver.default(), lambda: client).run(
        games, max_credits=20_000, execute=True, on_progress=progress.append
    )

    assert report.executed_snapshots == 3
    assert {call["source"] for call in client.calls} == {
        "oddsapi:frozen",
        "oddsapi:submit",
    }
    assert len(progress) == 3
    assert any(item.skipped for item in progress)
    stored = store.load_week(Sport.NFL, 2024, 3)
    assert {line.source for line in stored.market_lines} == {
        "oddsapi:frozen",
        "oddsapi:submit",
    }


def test_no_stored_games_fails_before_constructing_a_client(store):
    def explode():
        raise AssertionError("empty run constructed the paid adapter")

    with pytest.raises(NoHistoricalGames):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            [], max_credits=20_000, execute=True
        )


def test_quota_failure_names_the_exact_partial_position(store, games):
    client = StubHistoricalClient(fail_on_call=2)

    with pytest.raises(ArchiveRunInterrupted) as caught:
        ArchiveBackfill(store, TeamResolver.default(), lambda: client).run(
            games, max_credits=20_000, execute=True
        )

    assert caught.value.index == 2
    assert caught.value.total == 3
