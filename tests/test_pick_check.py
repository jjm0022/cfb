from datetime import UTC, datetime

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_entry import EntryBoard
from pickem.ingest.cbs_fetch import CbsProfileBusy, CbsSessionExpired, CbsWeekNotReady
from pickem.models import Edge, Game, Side, Sport, Tier
from pickem.operations.pick_check import (
    GameCheck,
    Outcome,
    compare_picks,
    failure_reason,
    format_check_line,
    format_failure,
    format_pick_check,
    log_pick_check,
    log_pick_check_failed,
)
from pickem.resolve.resolver import TeamResolver

KICKOFF = datetime(2026, 9, 25, 0, 15, tzinfo=UTC)  # Thu 8:15 PM Eastern
POOL = "https://cbs.test/pool"
RESOLVER = TeamResolver.default()


def game(away: str, home: str, kickoff: datetime = KICKOFF) -> Game:
    return Game(
        game_id=f"nfl-2026-03-{away}-at-{home}",
        sport=Sport.NFL,
        season=2026,
        week=3,
        kickoff_utc=kickoff,
        home_team_id=home,
        away_team_id=away,
    )


def edge(g: Game, side: Side, spread: float = -6.5) -> Edge:
    return Edge(
        game_id=g.game_id,
        side=side,
        delta=1.0,
        tier=Tier.LEAN,
        league_spread=spread,
        market_spread=spread - 1,
        rationale="test",
    )


ATL_GB = game("ATL", "GB")
DAL_PHI = game("DAL", "PHI")


def test_each_outcome_is_classified():
    board = EntryBoard(picks={ATL_GB.game_id: Side.HOME, DAL_PHI.game_id: None}, unresolved=())
    other = game("BUF", "MIA")
    checks = compare_picks(
        board,
        [edge(ATL_GB, Side.HOME), edge(DAL_PHI, Side.AWAY), edge(other, Side.HOME)],
        [ATL_GB, DAL_PHI, other],
    )

    assert {c.game.game_id: c.outcome for c in checks} == {
        ATL_GB.game_id: Outcome.MATCH,
        DAL_PHI.game_id: Outcome.NO_PICK,
        other.game_id: Outcome.UNMATCHED,
    }


def test_a_different_side_is_flagged_with_both_sides():
    board = EntryBoard(picks={ATL_GB.game_id: Side.AWAY}, unresolved=())

    (check,) = compare_picks(board, [edge(ATL_GB, Side.HOME)], [ATL_GB])

    assert (check.outcome, check.cbs_side, check.model_side) == (
        Outcome.DIFFERENT_SIDE,
        Side.AWAY,
        Side.HOME,
    )


def test_a_game_missing_from_the_cbs_page_is_unmatched():
    board = EntryBoard(picks={}, unresolved=("Nowhere at Green Bay",))

    (check,) = compare_picks(board, [edge(ATL_GB, Side.HOME)], [ATL_GB])

    assert check.outcome is Outcome.UNMATCHED


def test_only_the_games_passed_in_are_checked():
    board = EntryBoard(picks={ATL_GB.game_id: Side.HOME, DAL_PHI.game_id: None}, unresolved=())

    checks = compare_picks(board, [edge(ATL_GB, Side.HOME), edge(DAL_PHI, Side.HOME)], [ATL_GB])

    assert [c.game.game_id for c in checks] == [ATL_GB.game_id]


def test_a_game_the_model_has_no_pick_for_is_skipped():
    board = EntryBoard(picks={ATL_GB.game_id: None}, unresolved=())

    assert compare_picks(board, [], [ATL_GB]) == ()


def _check(outcome: Outcome, cbs: Side | None, model: Side = Side.HOME) -> GameCheck:
    return GameCheck(ATL_GB, outcome, model, cbs, -6.5)


def test_all_matching_and_fresh_says_nothing():
    checks = (_check(Outcome.MATCH, Side.HOME),)

    assert (
        format_pick_check(
            checks, kickoff_utc=KICKOFF, stale=False, pool_url=POOL, resolver=RESOLVER
        )
        is None
    )


def test_a_mismatch_names_both_picks_with_their_spreads_and_the_pool_link():
    title, body = format_pick_check(
        (_check(Outcome.DIFFERENT_SIDE, Side.AWAY),),
        kickoff_utc=KICKOFF,
        stale=False,
        pool_url=POOL,
        resolver=RESOLVER,
    )

    assert title == "⚠️ Pick check — 1 game kicks off at Thu 8:15 PM"
    atl = RESOLVER.display_name("ATL", Sport.NFL)
    gb = RESOLVER.display_name("GB", Sport.NFL)
    assert f"{atl} at {gb}: CBS has **{atl} +6.5**, model says **{gb} -6.5**" in body
    assert body.endswith(f"Fix on CBS before kickoff: {POOL}")


def test_a_missing_pick_says_so():
    _, body = format_pick_check(
        (_check(Outcome.NO_PICK, None),),
        kickoff_utc=KICKOFF,
        stale=False,
        pool_url=POOL,
        resolver=RESOLVER,
    )

    assert "no pick entered on CBS" in body


def test_unmatched_games_are_listed_as_not_checked():
    _, body = format_pick_check(
        (_check(Outcome.UNMATCHED, None),),
        kickoff_utc=KICKOFF,
        stale=False,
        pool_url=POOL,
        resolver=RESOLVER,
    )

    assert "Couldn't find on CBS, so not checked:" in body
    assert "Fix on CBS" not in body


def test_a_stale_model_pick_is_reported_even_when_everything_matches():
    _, body = format_pick_check(
        (_check(Outcome.MATCH, Side.HOME),),
        kickoff_utc=KICKOFF,
        stale=True,
        pool_url=POOL,
        resolver=RESOLVER,
    )

    assert "could not be refreshed" in body
    assert "All 1 pick matches" in body


def test_a_pick_em_line_reads_pk():
    check = GameCheck(ATL_GB, Outcome.DIFFERENT_SIDE, Side.HOME, Side.AWAY, 0.0)

    _, body = format_pick_check(
        (check,), kickoff_utc=KICKOFF, stale=False, pool_url=POOL, resolver=RESOLVER
    )

    assert " PK**" in body


def test_failure_reason_for_an_expired_login_points_at_the_fix():
    assert "cbs-login.sh" in failure_reason(CbsSessionExpired("gone"))


def test_failure_reason_for_an_open_login_window():
    assert "login window is open" in failure_reason(CbsProfileBusy("busy"))


def test_failure_reason_for_a_week_cbs_is_not_showing():
    reason = failure_reason(CbsWeekNotReady("CBS is showing pool week 5, not 4"))

    assert "showing pool week 5" in reason


def test_failure_reason_for_an_unreadable_page():
    assert "couldn't read your picks" in failure_reason(CbsParseError("no entry"))


def test_failure_reason_for_anything_else_names_the_error_type():
    assert "TimeoutError" in failure_reason(TimeoutError("slow"))


def test_failure_message_lists_the_unchecked_games():
    title, body = format_failure(
        "CBS login has expired", [ATL_GB], kickoff_utc=KICKOFF, resolver=RESOLVER
    )

    assert title == "⚠️ Pick check didn't run — Thu 8:15 PM kickoff"
    assert body.startswith("CBS login has expired")
    assert f"Not checked: {RESOLVER.display_name('ATL', Sport.NFL)} at" in body


def test_cli_line_marks_each_outcome():
    assert format_check_line(_check(Outcome.MATCH, Side.HOME), RESOLVER).startswith("✅")
    assert format_check_line(_check(Outcome.DIFFERENT_SIDE, Side.AWAY), RESOLVER).startswith("❌")
    assert "no pick on CBS" in format_check_line(_check(Outcome.NO_PICK, None), RESOLVER)
    assert "not found on CBS" in format_check_line(_check(Outcome.UNMATCHED, None), RESOLVER)


def test_completed_log_counts_each_outcome(records):
    log_pick_check(
        sport=Sport.NFL,
        season=2026,
        week=3,
        kickoff_utc=KICKOFF,
        checks=(_check(Outcome.MATCH, Side.HOME), _check(Outcome.NO_PICK, None)),
        dm_sent=True,
        stale=False,
    )

    (row,) = [r for r in records if r["extra"].get("event") == "pick_check_completed"]
    expected = {
        "checked": 2,
        "matched": 1,
        "no_pick": 1,
        "different_side": 0,
        "unmatched": 0,
        "dm_sent": True,
        "stale": False,
    }
    for key, value in expected.items():
        assert row["extra"][key] == value, key
    assert row["level"].name == "WARNING"


def test_failed_log_names_the_reason(records):
    log_pick_check_failed(
        sport=Sport.NFL,
        season=2026,
        week=3,
        kickoff_utc=KICKOFF,
        reason="CBS login has expired",
        error=CbsSessionExpired("gone"),
        unchecked=3,
    )

    (row,) = [r for r in records if r["extra"].get("event") == "pick_check_failed"]
    assert row["extra"]["reason"] == "CBS login has expired"
    assert row["extra"]["unchecked"] == 3
    assert row["extra"]["error_type"] == "CbsSessionExpired"


def test_failure_message_without_known_games_says_every_game():
    _, body = format_failure("it crashed", [], kickoff_utc=KICKOFF, resolver=RESOLVER)

    assert body.endswith("Not checked: every game at this kickoff")
