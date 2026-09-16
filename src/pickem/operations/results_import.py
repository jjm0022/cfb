"""Link a parsed CBS Weekly Standings page to stored games, then store it.

Fails closed: every problem is collected and reported together, and nothing is
written unless the whole page links and cross-checks. A misread page would
silently corrupt every conclusion drawn from it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from pickem.backtest.stats import Result, grade_pick
from pickem.ingest.cbs_results import ParsedStandings, StandingsGame
from pickem.models import Game, PoolPick, PoolResult, Side, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver, UnknownTeamError
from pickem.store.db import Store


class ResultsImportError(RuntimeError):
    """The page could not be linked to stored games, or failed a cross-check."""


@dataclass(frozen=True)
class ImportSummary:
    season: int
    pool_week: int
    entrants: int
    picks: int
    blank_picks: int
    games_by_sport: dict[Sport, int]
    scores_filled: int


def league_week(sport: Sport, pool_week: int) -> int:
    """Translate a pool week into one board's league week.

    The pool pairs CFB week N with NFL week N-1, because the college season
    starts a week earlier. Pool week 1 is CFB only.
    """
    if pool_week < 1:
        raise ResultsImportError(f"pool week must be positive, got {pool_week}")
    if sport is Sport.CFB:
        return pool_week
    if pool_week == 1:
        raise ResultsImportError("pool week 1 has no NFL board")
    return pool_week - 1


def import_results(
    store: Store,
    parsed: ParsedStandings,
    *,
    season: int,
    pool_week: int,
    resolver: TeamResolver,
    imported_at: datetime,
) -> ImportSummary:
    """Validate the whole page against the store, then replace its pool week."""
    errors: list[str] = []
    linked = _link_games(store, parsed.games, season=season, pool_week=pool_week,
                         resolver=resolver, errors=errors)
    if not errors:
        _check_boards(store, parsed.games, season=season, pool_week=pool_week, errors=errors)
        _check_grades(parsed, errors)
    if errors:
        logger.bind(
            event="results_import_rejected", season=season, pool_week=pool_week,
            problems=len(errors), reasons=errors,
        ).error(f"results import rejected for pool week {pool_week}: {len(errors)} problem(s)")
        raise ResultsImportError(
            f"pool week {pool_week} was not imported ({len(errors)} problem(s)):\n"
            + "\n".join(f"  - {error}" for error in errors)
        )

    page_games = {game.cbs_event_id: game for game in parsed.games}
    unscored = [
        game.model_copy(update={
            "away_score": page_games[event_id].away_score,
            "home_score": page_games[event_id].home_score,
        })
        for event_id, game in linked.items()
        if game.home_score is None or game.away_score is None
    ]
    store.upsert_games(unscored)

    results = [
        PoolResult(
            season=season, pool_week=pool_week, entry_id=e.entry_id, name=e.name, rank=e.rank,
            points=e.points, ytd=e.ytd, tiebreak=e.tiebreak, imported_at=imported_at,
        )
        for e in parsed.entrants
    ]
    picks = [
        PoolPick(
            season=season, pool_week=pool_week, entry_id=p.entry_id,
            game_id=linked[p.cbs_event_id].game_id, cbs_event_id=p.cbs_event_id,
            side=_side(page_games[p.cbs_event_id], p.picked_abbrev), cbs_correct=p.cbs_correct,
        )
        for p in parsed.picks
    ]
    store.replace_pool_week(season, pool_week, results, picks)

    summary = ImportSummary(
        season=season,
        pool_week=pool_week,
        entrants=len(results),
        picks=len(picks),
        blank_picks=sum(1 for p in picks if p.side is None),
        games_by_sport=dict(Counter(game.sport for game in parsed.games)),
        scores_filled=len(unscored),
    )
    logger.bind(
        event="results_imported", season=season, pool_week=pool_week,
        entrants=summary.entrants, picks=summary.picks, blank_picks=summary.blank_picks,
        games={sport.value: count for sport, count in summary.games_by_sport.items()},
        scores_filled=summary.scores_filled,
    ).info(
        f"imported pool week {pool_week}: {summary.entrants} entrants, {summary.picks} picks, "
        f"{summary.scores_filled} missing score(s) filled from CBS"
    )
    return summary


def _side(game: StandingsGame, abbrev: str | None) -> Side | None:
    if abbrev is None:
        return None
    return Side.HOME if abbrev == game.home_abbrev else Side.AWAY


def _label(game: StandingsGame) -> str:
    return f"{game.away_abbrev} at {game.home_abbrev}"


def _link_games(
    store: Store,
    games: tuple[StandingsGame, ...],
    *,
    season: int,
    pool_week: int,
    resolver: TeamResolver,
    errors: list[str],
) -> dict[int, Game]:
    wanted: dict[int, str] = {}
    for game in games:
        try:
            week = league_week(game.sport, pool_week)
            away = resolver.resolve(game.away_abbrev, game.sport)
            home = resolver.resolve(game.home_abbrev, game.sport)
        except UnknownTeamError as exc:
            errors.append(
                f"{_label(game)}: {exc} — add the CBS abbreviation to resolve/aliases.yaml"
            )
            continue
        except ResultsImportError as exc:
            errors.append(f"{_label(game)}: {exc}")
            continue
        wanted[game.cbs_event_id] = make_game_id(game.sport, season, week, away, home)

    ids = list(wanted.values())
    stored = {game.game_id: game for game in store.games_by_ids(ids)}
    lines = {line.game_id: line for line in store.league_lines_by_ids(ids)}
    page = {game.cbs_event_id: game for game in games}

    linked: dict[int, Game] = {}
    for event_id, game_id in wanted.items():
        cbs = page[event_id]
        game = stored.get(game_id)
        if game is None:
            errors.append(
                f"{_label(cbs)}: no stored game {game_id} — run ingest-cbs for that board"
            )
            continue
        if (
            game.home_score is not None
            and game.away_score is not None
            and (game.away_score, game.home_score) != (cbs.away_score, cbs.home_score)
        ):
            errors.append(
                f"{_label(cbs)}: CBS final {cbs.away_score}-{cbs.home_score} but the store has "
                f"{game.away_score}-{game.home_score}"
            )
        line = lines.get(game_id)
        if line is None:
            errors.append(f"{_label(cbs)}: stored game {game_id} has no league line")
        elif line.spread_home != cbs.spread_home:
            errors.append(
                f"{_label(cbs)}: CBS line {cbs.spread_home:+.1f} but the stored league line is "
                f"{line.spread_home:+.1f}"
            )
        linked[event_id] = game
    return linked


def _check_boards(
    store: Store,
    games: tuple[StandingsGame, ...],
    *,
    season: int,
    pool_week: int,
    errors: list[str],
) -> None:
    on_page = Counter(game.sport for game in games)
    boards = {Sport.CFB} | ({Sport.NFL} if pool_week > 1 else set()) | set(on_page)
    for sport in sorted(boards):
        week = league_week(sport, pool_week)
        stored = store.league_line_count(sport, season, week)
        if stored != on_page[sport]:
            errors.append(
                f"{sport.value} week {week}: the page has {on_page[sport]} games but the store "
                f"has {stored} league lines"
            )


def _check_grades(parsed: ParsedStandings, errors: list[str]) -> None:
    games = {game.cbs_event_id: game for game in parsed.games}
    names = {entrant.entry_id: entrant.name for entrant in parsed.entrants}
    for pick in parsed.picks:
        if pick.picked_abbrev is None:
            continue
        game = games[pick.cbs_event_id]
        result = grade_pick(
            _side(game, pick.picked_abbrev), game.home_score - game.away_score, game.spread_home
        )
        # No push markup has been seen, so a push cannot be cross-checked; the
        # parser already rejects a pick whose icon is neither correct nor incorrect.
        if result is Result.PUSH:
            continue
        if (result is Result.WIN) != pick.cbs_correct:
            marked = "correct" if pick.cbs_correct else "incorrect"
            errors.append(
                f"{names[pick.entry_id]} on {_label(game)}: CBS marks {pick.picked_abbrev} "
                f"{marked} but the score and line say {result.value}"
            )
