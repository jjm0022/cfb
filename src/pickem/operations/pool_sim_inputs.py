"""Build a weekly-win simulation input from an imported pool week."""

from __future__ import annotations

from collections import defaultdict

from pickem.backtest.pool_sim import SimGame, SimWeek
from pickem.edge.favorite import favorite_side
from pickem.models import RecommendationRecord
from pickem.report.results import last_before
from pickem.store.db import Store


class SimInputError(RuntimeError):
    """The requested pool week cannot be simulated from what is stored."""


def load_sim_week(store: Store, *, season: int, pool_week: int, entry_name: str) -> SimWeek:
    results = store.pool_results(season, pool_week)
    if not results:
        raise SimInputError(
            f"pool week {pool_week} of {season} is not imported; run import-results first"
        )
    ours = next((r for r in results if r.name.strip() == entry_name), None)
    if ours is None:
        names = ", ".join(sorted(r.name for r in results))
        raise SimInputError(
            f"no entrant named {entry_name!r} in pool week {pool_week}; names found: {names}"
        )

    picks = store.pool_picks(season, pool_week)
    game_ids = sorted({pick.game_id for pick in picks})
    games = {game.game_id: game for game in store.games_by_ids(game_ids)}
    spreads = {ln.game_id: ln.spread_home for ln in store.league_lines_by_ids(game_ids)}
    history: dict[str, list[RecommendationRecord]] = defaultdict(list)
    for record in store.recommendation_history(game_ids):
        history[record.game_id].append(record)
    side_of = {(pick.entry_id, pick.game_id): pick.side for pick in picks}

    sim_games: list[SimGame] = []
    unrecommended = 0
    for game_id in game_ids:
        game = games[game_id]
        spread = spreads[game_id]
        model = last_before(history[game_id], game.kickoff_utc)
        if model is None:
            unrecommended += 1
            tier = None
            side = side_of.get((ours.entry_id, game_id)) or favorite_side(spread)
        else:
            tier, side = model.tier, model.side
        sim_games.append(
            SimGame(
                game_id=game_id,
                label=f"{game.away_team_id} at {game.home_team_id}",
                league_spread=spread,
                tier=tier,
                model_side=side,
            )
        )

    other_ids = sorted({r.entry_id for r in results} - {ours.entry_id})
    others = tuple(
        tuple(side_of.get((entry_id, game_id)) for game_id in game_ids) for entry_id in other_ids
    )
    return SimWeek(pool_week, tuple(sim_games), others, unrecommended)
