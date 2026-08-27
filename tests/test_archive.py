from datetime import UTC, datetime, timedelta

import pytest

from pickem.backtest.archive import (
    ArchiveBackfill,
    ArchiveRunInterrupted,
    CreditLimitExceeded,
    InsufficientCredits,
    MixedSports,
    NoHistoricalGames,
    PlannedCostChanged,
)
from pickem.ingest.odds import CFB_KEY, QuotaExhausted
from pickem.models import Game, MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


class StubHistoricalClient:
    def __init__(self, fail_on_call: int | None = None, remaining: int = 20_000) -> None:
        self.fail_on_call = fail_on_call
        self.remaining = remaining
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def remaining_credits(self):
        return self.remaining

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
            snapshot_at=kwargs["at"],
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


@pytest.fixture
def cfb_games():
    return [
        Game(
            game_id=make_game_id(Sport.CFB, 2024, 3, "CLEM", "UGA"),
            sport=Sport.CFB,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 22, 17, 0, tzinfo=UTC),
            home_team_id="UGA",
            away_team_id="CLEM",
            home_score=24,
            away_score=17,
        ),
        Game(
            game_id=make_game_id(Sport.CFB, 2024, 3, "BAMA", "LSU"),
            sport=Sport.CFB,
            season=2024,
            week=3,
            kickoff_utc=datetime(2024, 9, 22, 18, 15, tzinfo=UTC),
            home_team_id="LSU",
            away_team_id="BAMA",
            home_score=21,
            away_score=20,
        ),
    ]


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

    with pytest.raises(NoHistoricalGames, match="no historical games are stored"):
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


def test_cfb_execution_uses_the_ncaaf_sport_key(store, cfb_games):
    client = StubHistoricalClient(remaining=20_000)
    ArchiveBackfill(store, TeamResolver.default(), lambda: client).run(
        cfb_games,
        max_credits=20_000,
        execute=True,
        max_submission_age=timedelta(minutes=90),
    )
    assert {call["sport_key"] for call in client.calls} == {CFB_KEY}


def test_resume_buys_only_pending_requests(store, games):
    first = StubHistoricalClient(remaining=20_000)
    ArchiveBackfill(store, TeamResolver.default(), lambda: first).run(
        games, max_credits=20_000, execute=True
    )

    def explode():
        raise AssertionError("completed requests were repurchased")

    report = ArchiveBackfill(store, TeamResolver.default(), explode).run(
        games, max_credits=20_000, execute=True
    )
    assert report.pending_snapshots == 0
    assert report.pending_credits == 0
    assert report.completed_snapshots == 3


def test_balance_must_cover_pending_cost_plus_reserve(store, games):
    client = StubHistoricalClient(remaining=129)
    with pytest.raises(InsufficientCredits):
        ArchiveBackfill(store, TeamResolver.default(), lambda: client).run(
            games, max_credits=20_000, execute=True, min_credit_reserve=100
        )


def test_expected_credit_mismatch_fails_before_client_creation(store, games):
    def explode():
        raise AssertionError("changed cost constructed the paid adapter")

    with pytest.raises(PlannedCostChanged):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            games, max_credits=20_000, execute=True, expected_credits=999
        )


def test_max_new_requests_executes_probe_then_resume_finishes(store, games):
    first = StubHistoricalClient(remaining=20_000)
    first_report = ArchiveBackfill(store, TeamResolver.default(), lambda: first).run(
        games, max_credits=20_000, execute=True, max_new_requests=1
    )
    assert first_report.executed_snapshots == 1
    assert first_report.pending_snapshots == 3

    second = StubHistoricalClient(remaining=20_000)
    report = ArchiveBackfill(store, TeamResolver.default(), lambda: second).run(
        games, max_credits=20_000, execute=True
    )
    assert len(second.calls) == 2
    assert report.executed_snapshots == 2
    assert report.completed_snapshots == 1
    assert report.pending_snapshots == 2


def test_negative_max_new_requests_fails_before_client_construction(store, games):
    def explode():
        raise AssertionError("negative cap constructed the paid adapter")

    with pytest.raises(ValueError, match="max_new_requests must be non-negative"):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            games, max_credits=20_000, execute=True, max_new_requests=-1
        )


def test_zero_max_new_requests_executes_no_archive_requests(store, games):
    client = StubHistoricalClient(remaining=20_000)
    report = ArchiveBackfill(store, TeamResolver.default(), lambda: client).run(
        games, max_credits=20_000, execute=True, max_new_requests=0
    )

    assert report.executed_snapshots == 0
    assert client.calls == []


def test_mixed_sports_are_rejected_before_constructing_a_client(store, games, cfb_games):
    def explode():
        raise AssertionError("mixed sports constructed the paid adapter")

    with pytest.raises(MixedSports):
        ArchiveBackfill(store, TeamResolver.default(), explode).run(
            [games[0], cfb_games[0]], max_credits=20_000, execute=True
        )
