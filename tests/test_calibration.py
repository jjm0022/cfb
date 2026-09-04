from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from pickem import cli
from pickem.backtest.calibration import calibrate
from pickem.models import Game, LeagueLine, MarketLine, Sport
from pickem.store.db import Store

POSTED = datetime(2025, 9, 16, 14, 0, tzinfo=UTC)
GID = "nfl-2025-03-BUF-at-MIA"


def league(spread: float, gid: str = GID, posted_at: datetime = POSTED) -> LeagueLine:
    return LeagueLine(game_id=gid, season=2025, week=3, spread_home=spread, posted_at=posted_at)


def line(
    spread: float,
    book: str = "book1",
    gid: str = GID,
    at: datetime | None = None,
    source: str = "oddsapi",
) -> MarketLine:
    return MarketLine(
        game_id=gid,
        source=source,
        book=book,
        spread_home=spread,
        captured_at=at or POSTED,
    )


def test_a_league_line_above_the_market_reports_a_positive_bias():
    """Catches a residual computed with the subtraction the wrong way round."""
    report = calibrate(league_lines=[league(-2.0)], market_lines=[line(-3.0)])

    assert report.compared == 1
    assert report.mean_residual == 1.0


def test_market_lines_captured_after_the_league_line_are_not_compared():
    """Catches a comparison that peeks at movement CBS could not have seen.

    The whole point is what the market said when CBS froze its number. A later
    snapshot is the thing the strategy trades against, not the thing being
    calibrated against.
    """
    later = datetime(2025, 9, 20, tzinfo=UTC)
    report = calibrate(
        league_lines=[league(-2.0)],
        market_lines=[line(-2.0, book="early"), line(-9.0, book="late", at=later)],
    )

    assert report.compared == 1
    assert report.mean_residual == 0.0


def test_a_game_with_no_usable_market_line_is_reported_not_dropped():
    """Catches a silent drop — the convention is that nothing vanishes quietly."""
    report = calibrate(
        league_lines=[league(-2.0), league(-7.0, gid="nfl-2025-03-NYJ-at-NE")],
        market_lines=[line(-3.0)],
    )

    assert report.compared == 1
    assert len(report.skipped) == 1
    assert "nfl-2025-03-NYJ-at-NE" in report.skipped[0]


def test_opposite_residuals_cancel_in_the_bias_but_not_in_the_dispersion():
    """Catches a report that conflates the two failure modes.

    Symmetric noise around zero mostly washes out of a divergence strategy; a
    systematic offset does not. A single averaged number would hide the
    difference, so bias and dispersion are reported separately.
    """
    other = "nfl-2025-03-NYJ-at-NE"
    report = calibrate(
        league_lines=[league(-1.0), league(-5.0, gid=other)],
        market_lines=[line(-3.0), line(-3.0, gid=other)],
    )

    assert report.mean_residual == 0.0
    assert report.mean_abs_residual == 2.0


def test_agreement_is_reported_in_the_same_buckets_as_the_archive_cross_check():
    """Catches buckets that are not cumulative, or that use < instead of <=.

    The archive-vs-nflverse check is quoted as 71% exact / 97% within 0.5 / 100%
    within 1.0. Using the same shape here makes the CBS number directly
    comparable to a proxy already known to be sound.
    """
    gids = [f"nfl-2025-03-A{i}-at-B{i}" for i in range(4)]
    report = calibrate(
        league_lines=[
            league(-3.0, gid=gids[0]),
            league(-3.5, gid=gids[1]),
            league(-4.0, gid=gids[2]),
            league(-6.0, gid=gids[3]),
        ],
        market_lines=[line(-3.0, gid=gid) for gid in gids],
    )

    assert report.compared == 4
    assert report.share_exact == 0.25
    assert report.share_within_half == 0.5
    assert report.share_within_one == 0.75


def test_nothing_to_compare_reports_none_rather_than_a_zero_bias():
    """Catches a report that presents an empty sample as perfect agreement.

    `league_lines` is empty until a real CBS paste is ingested. A 0.0 bias there
    would read as "CBS matches the market exactly" — the most misleading
    possible answer to the question this module exists to ask.
    """
    report = calibrate(league_lines=[], market_lines=[line(-3.0)])

    assert report.compared == 0
    assert report.mean_residual is None
    assert report.mean_abs_residual is None
    assert report.share_exact is None


def test_the_market_end_collapses_through_the_median_not_the_last_book():
    """Catches a last-wins dict, the bug that already bit run_backtest once.

    Books at -1.0/-3.0/-9.0 have a median of -3.0. Taking whichever book sorted
    last would report a residual of 6.0 against a league line of -3.0.
    """
    report = calibrate(
        league_lines=[league(-3.0)],
        market_lines=[
            line(-1.0, book="a"),
            line(-3.0, book="b"),
            line(-9.0, book="c"),
        ],
    )

    assert report.compared == 1
    assert report.mean_residual == 0.0


def test_the_market_end_can_be_restricted_to_one_source():
    """Catches a comparison that mixes an archive proxy with a live poll.

    A historical calibration wants `oddsapi:frozen`; a live one wants the
    `oddsapi` polls. Averaging the two would put differing capture regimes
    inside the residual being measured.
    """
    report = calibrate(
        league_lines=[league(-3.0)],
        market_lines=[
            line(-3.0, book="a", source="oddsapi"),
            line(-9.0, book="b", source="oddsapi:frozen"),
        ],
        source="oddsapi",
    )

    assert report.compared == 1
    assert report.mean_residual == 0.0


def test_the_report_states_that_posted_at_is_our_ingest_time():
    """Catches a report that lets `posted_at` be read as CBS's own timestamp.

    `ingest-cbs` stamps the moment we pasted, not the moment CBS froze the
    number. A late paste widens the residual through our own operational delay,
    which would otherwise look like CBS disagreeing with the market.
    """
    report = calibrate(league_lines=[league(-3.0)], market_lines=[line(-3.0)])

    assert any("ingest" in a for a in report.assumptions)


def test_calibration_logs_summary_totals_and_suppresses_consensus_details(records):
    """A calibration sweep emits boundaries, not one consensus row per game."""
    result = calibrate(league_lines=[league(-2.0)], market_lines=[line(-3.0)])

    started = next(r for r in records if r["extra"]["event"] == "calibration_started")
    finished = next(r for r in records if r["extra"]["event"] == "calibration_finished")
    assert started["extra"]["seasons"] == [2025]
    assert started["extra"]["league_lines"] == 1
    assert started["extra"]["market_lines"] == 1
    assert finished["extra"]["compared"] == result.compared
    assert finished["extra"]["skipped"] == len(result.skipped)
    assert not any(
        record["extra"].get("event") == "consensus_computed" for record in records
    )


def test_the_cli_reports_the_bias_against_stored_lines(tmp_path):
    """Catches wiring that never joins league_lines to the stored market rows.

    Exercises the real Store and the real command, because the join between the
    two tables is exactly what a pure-function test cannot see.
    """
    db = tmp_path / "calib.duckdb"
    with Store(db) as store:
        store.init_schema()
        store.upsert_games(
            [
                Game(
                    game_id=GID,
                    sport=Sport.NFL,
                    season=2025,
                    week=3,
                    kickoff_utc=datetime(2025, 9, 21, tzinfo=UTC),
                    home_team_id="MIA",
                    away_team_id="BUF",
                )
            ]
        )
        store.upsert_league_lines([league(-2.0)])
        store.append_market_lines([line(-3.0, book="a"), line(-3.0, book="b")])

    result = CliRunner().invoke(
        cli.app,
        ["calibrate", "--season", "2025", "--from-week", "3", "--to-week", "3", "--db", str(db)],
    )

    assert result.exit_code == 0, result.output
    assert "calibrated 1 CBS line " in result.output
    assert "+1.00 pts" in result.output
    assert f"{GID}: CBS -2.0 vs market -3.0 (+1.0)" in result.output


def test_the_cli_says_so_plainly_when_no_paste_has_been_ingested(tmp_path):
    """Catches a run against an empty store that prints a bias of +0.00.

    `league_lines` is empty on every machine today, so this is the state a
    first-time run actually hits.
    """
    db = tmp_path / "empty.duckdb"
    result = CliRunner().invoke(
        cli.app,
        ["calibrate", "--season", "2025", "--db", str(db)],
    )

    assert result.exit_code == 0, result.output
    assert "no CBS line could be compared" in result.output
    assert "0.00" not in result.output


def test_a_poll_taken_just_after_the_paste_still_counts():
    """Catches a rule the real workflow cannot satisfy.

    `poll-odds` derives its slate from `league_lines`, so the market snapshot
    is always captured AFTER the paste it is being compared against — by
    construction, never before. A strict at-or-before rule makes every real
    week uncalibratable; the first live run missed by 11 seconds.
    """
    just_after = POSTED + timedelta(seconds=11)
    report = calibrate(
        league_lines=[league(-2.0)],
        market_lines=[line(-3.0, at=just_after)],
    )

    assert report.compared == 1
    assert report.mean_residual == 1.0


def test_a_poll_taken_long_after_the_paste_is_still_excluded():
    """Catches a tolerance so wide it readmits real line movement.

    The point of the cutoff is that later movement is what the strategy trades
    against, not what it is calibrated against. Widening it to admit the
    same-sitting poll must not widen it to admit the next day's number.
    """
    much_later = POSTED + timedelta(days=1)
    report = calibrate(
        league_lines=[league(-2.0)],
        market_lines=[line(-9.0, at=much_later)],
    )

    assert report.compared == 0
    assert len(report.skipped) == 1
