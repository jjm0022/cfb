"""Compare the owner's CBS picks with the model's, and word the result.

Pure apart from logging: the bot and the `check-picks` command supply the
parsed CBS entry, the model's edges and the games to check, and decide who
hears about it. A pick is entered by hand and the model can move after entry,
so this is the last look before each game locks.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from loguru import logger

from pickem.ingest.cbs import CbsParseError
from pickem.ingest.cbs_entry import EntryBoard
from pickem.ingest.cbs_fetch import (
    CbsFetchError,
    CbsProfileBusy,
    CbsSessionExpired,
    CbsWeekNotReady,
)
from pickem.models import Edge, Game, Side, Sport
from pickem.resolve.resolver import TeamResolver

EASTERN = ZoneInfo("America/New_York")


class Outcome(StrEnum):
    MATCH = "match"
    DIFFERENT_SIDE = "different_side"
    NO_PICK = "no_pick"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class GameCheck:
    game: Game
    outcome: Outcome
    model_side: Side
    cbs_side: Side | None
    # Home-perspective, favourite negative: the board's frozen line.
    league_spread: float


_TO_FIX = (Outcome.DIFFERENT_SIDE, Outcome.NO_PICK)


def compare_picks(
    board: EntryBoard, edges: Iterable[Edge], games: Iterable[Game]
) -> tuple[GameCheck, ...]:
    """One outcome per game in ``games`` that the model has a pick for."""
    by_game = {edge.game_id: edge for edge in edges}
    checks: list[GameCheck] = []
    for game in sorted(games, key=lambda g: (g.kickoff_utc, g.game_id)):
        edge = by_game.get(game.game_id)
        if edge is None:
            continue
        if game.game_id not in board.picks:
            outcome, cbs_side = Outcome.UNMATCHED, None
        else:
            cbs_side = board.picks[game.game_id]
            if cbs_side is None:
                outcome = Outcome.NO_PICK
            elif cbs_side is edge.side:
                outcome = Outcome.MATCH
            else:
                outcome = Outcome.DIFFERENT_SIDE
        checks.append(GameCheck(game, outcome, edge.side, cbs_side, edge.league_spread))
    return tuple(checks)


def _when(kickoff_utc: datetime) -> str:
    return kickoff_utc.astimezone(EASTERN).strftime("%a %-I:%M %p")


def _matchup(game: Game, resolver: TeamResolver) -> str:
    away = resolver.display_name(game.away_team_id, game.sport)
    home = resolver.display_name(game.home_team_id, game.sport)
    return f"{away} at {home}"


def _pick(check: GameCheck, side: Side, resolver: TeamResolver) -> str:
    game = check.game
    team_id = game.home_team_id if side is Side.HOME else game.away_team_id
    spread = check.league_spread if side is Side.HOME else -check.league_spread
    line = "PK" if spread == 0 else f"{spread:+g}"
    return f"{resolver.display_name(team_id, game.sport)} {line}"


def format_pick_check(
    checks: Sequence[GameCheck],
    *,
    kickoff_utc: datetime,
    stale: bool,
    pool_url: str,
    resolver: TeamResolver,
) -> tuple[str, str] | None:
    """The DM for one kickoff's check, or None when there is nothing to say."""
    fix = [check for check in checks if check.outcome in _TO_FIX]
    unmatched = [check for check in checks if check.outcome is Outcome.UNMATCHED]
    if not fix and not unmatched and not stale:
        return None

    count = len(checks)
    title = (
        f"⚠️ Pick check — {count} game{'' if count == 1 else 's'} "
        f"kick{'s' if count == 1 else ''} off at {_when(kickoff_utc)}"
    )
    lines: list[str] = []
    for check in fix:
        model = _pick(check, check.model_side, resolver)
        cbs = (
            "no pick entered on CBS"
            if check.cbs_side is None
            else f"CBS has **{_pick(check, check.cbs_side, resolver)}**"
        )
        lines.append(f"{_matchup(check.game, resolver)}: {cbs}, model says **{model}**")
    if unmatched:
        lines.append(
            "Couldn't find on CBS, so not checked: "
            + ", ".join(_matchup(check.game, resolver) for check in unmatched)
        )
    if stale:
        lines.append(
            "The model's pick could not be refreshed an hour before kickoff, so this "
            "compares against the last stored pick."
        )
        if not fix and not unmatched:
            lines.append(f"All {count} pick{' matches' if count == 1 else 's match'}.")
    if fix:
        lines.append(f"Fix on CBS before kickoff: {pool_url}")
    return title, "\n".join(lines)


def failure_reason(error: BaseException) -> str:
    """One plain sentence saying why the check could not run."""
    if isinstance(error, CbsSessionExpired):
        return (
            "CBS login has expired — run scripts/cbs-login.sh "
            '(docs/runbooks/cbs-fetch.md, "Log in again")'
        )
    if isinstance(error, CbsProfileBusy):
        return "The CBS login window is open — close it so the check can use the profile"
    if isinstance(error, CbsWeekNotReady):
        return f"CBS isn't showing this pool week ({error})"
    if isinstance(error, CbsParseError | CbsFetchError):
        return f"couldn't read your picks from the CBS page ({error})"
    return f"CBS fetch failed ({type(error).__name__}: {error})"


def format_failure(
    reason: str, games: Sequence[Game], *, kickoff_utc: datetime, resolver: TeamResolver
) -> tuple[str, str]:
    title = f"⚠️ Pick check didn't run — {_when(kickoff_utc)} kickoff"
    unchecked = ", ".join(_matchup(game, resolver) for game in games)
    return title, f"{reason}\nNot checked: {unchecked or 'every game at this kickoff'}"


_MARKS = {
    Outcome.MATCH: "✅",
    Outcome.DIFFERENT_SIDE: "❌",
    Outcome.NO_PICK: "⚠️",
    Outcome.UNMATCHED: "❓",
}


def format_check_line(check: GameCheck, resolver: TeamResolver) -> str:
    model = _pick(check, check.model_side, resolver)
    if check.outcome is Outcome.MATCH:
        detail = f"CBS and model: {model}"
    elif check.outcome is Outcome.DIFFERENT_SIDE and check.cbs_side is not None:
        detail = f"CBS {_pick(check, check.cbs_side, resolver)} · model {model}"
    elif check.outcome is Outcome.NO_PICK:
        detail = f"no pick on CBS · model {model}"
    else:
        detail = f"not found on CBS · model {model}"
    return (
        f"{_MARKS[check.outcome]} {_when(check.game.kickoff_utc)}  "
        f"{_matchup(check.game, resolver)}: {detail}"
    )


def _scope(sport: Sport, season: int, week: int, kickoff_utc: datetime | None) -> dict:
    return {
        "sport": sport.value,
        "season": season,
        "week": week,
        "kickoff": kickoff_utc.isoformat() if kickoff_utc else None,
    }


def log_pick_check(
    *,
    sport: Sport,
    season: int,
    week: int,
    kickoff_utc: datetime | None,
    checks: Sequence[GameCheck],
    dm_sent: bool,
    stale: bool,
) -> None:
    counts = Counter(check.outcome for check in checks)
    needs_attention = any(counts[o] for o in (*_TO_FIX, Outcome.UNMATCHED))
    logger.bind(
        event="pick_check_completed",
        **_scope(sport, season, week, kickoff_utc),
        checked=len(checks),
        matched=counts[Outcome.MATCH],
        different_side=counts[Outcome.DIFFERENT_SIDE],
        no_pick=counts[Outcome.NO_PICK],
        unmatched=counts[Outcome.UNMATCHED],
        stale=stale,
        dm_sent=dm_sent,
    ).log(
        "WARNING" if needs_attention else "INFO",
        f"pick check {sport.value} wk{week}: {len(checks)} checked, "
        f"{counts[Outcome.MATCH]} match, {counts[Outcome.DIFFERENT_SIDE]} differ, "
        f"{counts[Outcome.NO_PICK]} unpicked, {counts[Outcome.UNMATCHED]} unmatched"
        f"{' (stale model pick)' if stale else ''}",
    )


def log_pick_check_failed(
    *,
    sport: Sport,
    season: int,
    week: int,
    kickoff_utc: datetime | None,
    reason: str,
    error: BaseException,
    unchecked: int,
    traceback: bool = False,
) -> None:
    logger.opt(exception=error if traceback else None).bind(
        event="pick_check_failed",
        **_scope(sport, season, week, kickoff_utc),
        reason=reason,
        error_type=type(error).__name__,
        unchecked=unchecked,
    ).error(f"pick check {sport.value} wk{week} did not run: {reason}")


__all__ = [
    "GameCheck",
    "Outcome",
    "compare_picks",
    "failure_reason",
    "format_check_line",
    "format_failure",
    "format_pick_check",
    "log_pick_check",
    "log_pick_check_failed",
]
