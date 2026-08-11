from datetime import UTC, datetime

from pickem.backtest.runner import run_backtest
from pickem.models import Game, MarketLine, Sport, Tier

T_OPEN = datetime(2025, 9, 16, tzinfo=UTC)
T_CLOSE = datetime(2025, 9, 21, tzinfo=UTC)


def game(gid: str, home_score: int, away_score: int, week: int = 3) -> Game:
    return Game(
        game_id=gid,
        sport=Sport.NFL,
        season=2025,
        week=week,
        kickoff_utc=T_CLOSE,
        home_team_id="MIA",
        away_team_id="BUF",
        home_score=home_score,
        away_score=away_score,
    )


def line(gid: str, spread: float, book: str, at: datetime) -> MarketLine:
    return MarketLine(
        game_id=gid, source="test", book=book, spread_home=spread, captured_at=at
    )


def test_a_correct_strong_pick_is_recorded_as_a_win():
    """Catches a runner that fails to grade a home-side cover as a win."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, home_score=27, away_score=17)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.wins == 1
    assert report.overall.losses == 0


def test_an_incorrect_pick_is_recorded_as_a_loss():
    """Catches a runner that treats a failed cover as a win."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, home_score=21, away_score=20)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.losses == 1


def test_pushes_are_excluded_from_the_hit_rate():
    """Catches a runner that counts pushes as decided picks."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, home_score=20, away_score=17)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.pushes == 1
    assert report.overall.wins + report.overall.losses == 0


def test_unplayed_games_are_excluded():
    """Catches a runner that grades games without final scores."""
    gid = "nfl-2025-03-BUF-at-MIA"
    unplayed = Game(
        game_id=gid,
        sport=Sport.NFL,
        season=2025,
        week=3,
        kickoff_utc=T_CLOSE,
        home_team_id="MIA",
        away_team_id="BUF",
    )
    report = run_backtest(
        games=[unplayed],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.wins + report.overall.losses + report.overall.pushes == 0


def test_games_without_an_opener_are_excluded():
    """Catches a runner that manufactures a frozen line when none exists."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, 27, 17)],
        openers=[],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.wins == 0


def test_excluded_games_report_each_reason_in_deterministic_order():
    """Catches a runner that silently drops excluded games or their reason."""
    unplayed_gid = "nfl-2025-01-BUF-at-MIA"
    missing_opener_gid = "nfl-2025-02-BUF-at-MIA"
    missing_market_gid = "nfl-2025-03-BUF-at-MIA"
    unplayed = Game(
        game_id=unplayed_gid,
        sport=Sport.NFL,
        season=2025,
        week=1,
        kickoff_utc=T_CLOSE,
        home_team_id="MIA",
        away_team_id="BUF",
    )
    report = run_backtest(
        games=[
            game(missing_market_gid, 27, 17, 3),
            game(missing_opener_gid, 27, 17, 2),
            unplayed,
        ],
        openers=[
            line(unplayed_gid, -3.0, "open", T_OPEN),
            line(missing_market_gid, -3.0, "open", T_OPEN),
        ],
        closers=[line(unplayed_gid, -6.0, "close", T_CLOSE)],
    )

    assert [entry.split(":", maxsplit=1)[0] for entry in report.skipped] == [
        unplayed_gid,
        missing_opener_gid,
        missing_market_gid,
    ]
    reasons = [entry.lower() for entry in report.skipped]
    assert "unplayed" in reasons[0]
    assert "missing" in reasons[1] and "open" in reasons[1]
    assert "missing" in reasons[2] and "clos" in reasons[2]


def test_results_are_broken_out_by_tier():
    """Catches a runner that only aggregates overall results."""
    strong = "nfl-2025-03-BUF-at-MIA"
    lean = "nfl-2025-04-BUF-at-MIA"
    report = run_backtest(
        games=[game(strong, 27, 17), game(lean, 27, 17, week=4)],
        openers=[
            line(strong, -3.0, "open", T_OPEN),
            line(lean, -3.0, "open", T_OPEN),
        ],
        closers=[
            line(strong, -6.0, "close", T_CLOSE),
            line(lean, -4.5, "close", T_CLOSE),
        ],
    )
    tiers = {record.tier for record in report.by_tier}
    assert Tier.STRONG in tiers
    assert Tier.LEAN in tiers


def test_report_states_its_proxy_assumption():
    """Catches a report that omits the opening-line frozen-line proxy."""
    report = run_backtest(games=[], openers=[], closers=[])
    joined = " ".join(report.assumptions).lower()
    assert "open" in joined and "frozen" in joined


def test_backtest_is_deterministic():
    """Catches result ordering or construction that varies between replays."""
    gid = "nfl-2025-03-BUF-at-MIA"
    args = dict(
        games=[game(gid, 27, 17)],
        openers=[line(gid, -3.0, "open", T_OPEN)],
        closers=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert run_backtest(**args) == run_backtest(**args)
