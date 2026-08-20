from datetime import UTC, datetime

from pickem.backtest.runner import run_backtest, split_proxies
from pickem.models import (
    FROZEN_SOURCE,
    LIVE_SOURCE,
    SUBMISSION_SOURCE,
    Game,
    MarketLine,
    Sport,
    Tier,
)

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
    return MarketLine(game_id=gid, source="test", book=book, spread_home=spread, captured_at=at)


def test_a_correct_strong_pick_is_recorded_as_a_win():
    """Catches a runner that fails to grade a home-side cover as a win."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, home_score=27, away_score=17)],
        frozen=[line(gid, -3.0, "open", T_OPEN)],
        submission=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.wins == 1
    assert report.overall.losses == 0


def test_an_incorrect_pick_is_recorded_as_a_loss():
    """Catches a runner that treats a failed cover as a win."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, home_score=21, away_score=20)],
        frozen=[line(gid, -3.0, "open", T_OPEN)],
        submission=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.losses == 1


def test_pushes_are_excluded_from_the_hit_rate():
    """Catches a runner that counts pushes as decided picks."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, home_score=20, away_score=17)],
        frozen=[line(gid, -3.0, "open", T_OPEN)],
        submission=[line(gid, -6.0, "close", T_CLOSE)],
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
        frozen=[line(gid, -3.0, "open", T_OPEN)],
        submission=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert report.overall.wins + report.overall.losses + report.overall.pushes == 0


def test_games_without_a_frozen_snapshot_are_excluded():
    """Catches a runner that manufactures a frozen line when none exists."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, 27, 17)],
        frozen=[],
        submission=[line(gid, -6.0, "close", T_CLOSE)],
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
        frozen=[
            line(unplayed_gid, -3.0, "open", T_OPEN),
            line(missing_market_gid, -3.0, "open", T_OPEN),
        ],
        submission=[line(unplayed_gid, -6.0, "close", T_CLOSE)],
    )

    # A game with no closing market is NOT excluded: the report ships a pick on
    # it via the Elo tiebreak, so the backtest grades it under the NO_MARKET
    # tier. Only genuinely ungradeable games appear in `skipped`.
    assert [entry.split(":", maxsplit=1)[0] for entry in report.skipped] == [
        unplayed_gid,
        missing_opener_gid,
    ]
    reasons = [entry.lower() for entry in report.skipped]
    assert "unplayed" in reasons[0]
    assert "missing" in reasons[1] and "frozen" in reasons[1]
    assert [record.tier for record in report.by_tier] == [Tier.NO_MARKET]
    assert report.by_tier[0].wins + report.by_tier[0].losses == 1


def test_no_market_games_are_graded_by_the_tiebreak_not_dropped():
    """The report picks these games with the rating, so the backtest must grade them."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, 27, 17)],
        frozen=[line(gid, -3.0, "open", T_OPEN)],
        submission=[],
    )
    assert report.skipped == []
    assert [record.tier for record in report.by_tier] == [Tier.NO_MARKET]


def test_the_tiebreak_never_sees_the_week_it_is_picking():
    """Catches a runner that leaks future results into its own picks."""
    from pickem.models import Game as G

    def played(gid, week, home_score, away_score, home, away):
        return G(
            game_id=gid,
            sport=Sport.NFL,
            season=2025,
            week=week,
            kickoff_utc=T_CLOSE,
            home_team_id=home,
            away_team_id=away,
            home_score=home_score,
            away_score=away_score,
        )

    # Week 1 alone: both teams sit at the initial rating, so the projected
    # margin is exactly home_field and cannot encode week 1's own result.
    first = played("nfl-2025-01-BUF-at-MIA", 1, 40, 0, "MIA", "BUF")
    report = run_backtest(
        games=[first],
        frozen=[line(first.game_id, 0.0, "open", T_OPEN)],
        submission=[],
    )
    # home_field (+2.0) > implied home margin (-0.0) -> HOME, and MIA won by 40.
    assert report.overall.wins == 1


def test_results_are_broken_out_by_tier():
    """Catches a runner that only aggregates overall results."""
    strong = "nfl-2025-03-BUF-at-MIA"
    lean = "nfl-2025-04-BUF-at-MIA"
    report = run_backtest(
        games=[game(strong, 27, 17), game(lean, 27, 17, week=4)],
        frozen=[
            line(strong, -3.0, "open", T_OPEN),
            line(lean, -3.0, "open", T_OPEN),
        ],
        submission=[
            line(strong, -6.0, "close", T_CLOSE),
            line(lean, -4.5, "close", T_CLOSE),
        ],
    )
    tiers = {record.tier for record in report.by_tier}
    assert Tier.STRONG in tiers
    assert Tier.LEAN in tiers


def test_report_states_its_proxy_assumptions():
    """Catches a report whose stated assumptions no longer match its proxies."""
    report = run_backtest(games=[], frozen=[], submission=[])
    joined = " ".join(report.assumptions).lower()
    # The proxy is an early-week snapshot, not a market opener. A stale string
    # here is a lie in the artifact the phase-exit decision is read from.
    assert "opening line" not in joined
    assert "early-week" in joined
    assert "frozen" in joined
    assert "same" in joined and "source" in joined


def test_backtest_is_deterministic():
    """Catches result ordering or construction that varies between replays."""
    gid = "nfl-2025-03-BUF-at-MIA"
    args = dict(
        games=[game(gid, 27, 17)],
        frozen=[line(gid, -3.0, "open", T_OPEN)],
        submission=[line(gid, -6.0, "close", T_CLOSE)],
    )
    assert run_backtest(**args) == run_backtest(**args)


# --- proxy classification and consensus --------------------------------------


def sourced(gid: str, source: str, book: str, spread: float, at: datetime) -> MarketLine:
    return MarketLine(game_id=gid, source=source, book=book, spread_home=spread, captured_at=at)


def test_split_classifies_by_source_across_books():
    """Both proxies carry real bookmaker keys, so book name cannot classify them."""
    lines = [
        sourced("g", FROZEN_SOURCE, "pinnacle", -3.0, T_OPEN),
        sourced("g", FROZEN_SOURCE, "draftkings", -3.5, T_OPEN),
        sourced("g", SUBMISSION_SOURCE, "pinnacle", -6.0, T_CLOSE),
    ]
    frozen, submission, unclassified = split_proxies(lines)
    assert len(frozen) == 2
    assert len(submission) == 1
    assert unclassified == []


def test_an_in_season_poll_is_neither_proxy():
    """Catches a classifier that lets live snapshots contaminate the backtest."""
    frozen, submission, unclassified = split_proxies(
        [sourced("g", LIVE_SOURCE, "pinnacle", -3.0, T_CLOSE)]
    )
    assert (frozen, submission) == ([], [])
    assert len(unclassified) == 1
    assert "not graded" in unclassified[0]


def test_nflverse_closers_are_no_longer_an_input():
    """Demoted to a cross-check: mixing sources puts book composition in the signal."""
    frozen, submission, unclassified = split_proxies(
        [sourced("g", "nflverse", "close", -3.0, T_CLOSE)]
    )
    assert (frozen, submission) == ([], [])
    assert len(unclassified) == 1


def test_unclassified_rows_name_themselves():
    """Catches a classifier that drops an unrecognised row without saying so."""
    _, _, unclassified = split_proxies([sourced("some-game", "mystery", "book", -1.0, T_CLOSE)])
    assert "some-game" in unclassified[0]
    assert "mystery" in unclassified[0]


def test_frozen_side_takes_a_consensus_not_an_arbitrary_book():
    """Catches a last-wins dict that grades against whichever book sorted last."""
    gid = "nfl-2025-03-BUF-at-MIA"
    frozen = [
        sourced(gid, FROZEN_SOURCE, "a", -1.0, T_OPEN),
        sourced(gid, FROZEN_SOURCE, "b", -3.0, T_OPEN),
        sourced(gid, FROZEN_SOURCE, "c", -9.0, T_OPEN),
    ]
    report = run_backtest(
        games=[game(gid, home_score=30, away_score=20)],
        frozen=frozen,
        submission=[sourced(gid, SUBMISSION_SOURCE, "a", -3.0, T_CLOSE)],
    )
    # Median -3.0 against a -3.0 submission line is a coinflip. Last-wins would
    # have taken -9.0 and manufactured a 6-point STRONG edge out of nothing.
    assert [record.tier for record in report.by_tier] == [Tier.COINFLIP]
    assert report.overall.wins + report.overall.losses == 1


def test_both_ends_collapse_per_book_before_comparing():
    """Catches a frequently-snapshotted book outvoting the rest on either end."""
    gid = "nfl-2025-03-BUF-at-MIA"
    frozen = [
        sourced(gid, FROZEN_SOURCE, "a", -3.0, T_OPEN),
        # Same book twice: the later snapshot wins, it does not get two votes.
        sourced(gid, FROZEN_SOURCE, "b", -20.0, T_OPEN),
        sourced(gid, FROZEN_SOURCE, "b", -3.0, T_CLOSE),
        sourced(gid, FROZEN_SOURCE, "c", -3.0, T_OPEN),
    ]
    report = run_backtest(
        games=[game(gid, home_score=30, away_score=20)],
        frozen=frozen,
        submission=[sourced(gid, SUBMISSION_SOURCE, "a", -3.0, T_CLOSE)],
    )
    assert [record.tier for record in report.by_tier] == [Tier.COINFLIP]


def test_the_frozen_line_is_posted_at_its_own_snapshot_time():
    """Catches a proxy that reports a posting time it did not come from."""
    gid = "nfl-2025-03-BUF-at-MIA"
    report = run_backtest(
        games=[game(gid, 27, 17)],
        frozen=[sourced(gid, FROZEN_SOURCE, "a", -3.0, T_OPEN)],
        submission=[sourced(gid, SUBMISSION_SOURCE, "a", -6.0, T_CLOSE)],
    )
    assert report.overall.wins == 1
