"""Shared fixtures for the dashboard: a synthetic season, and runners for Node and Chrome."""

import random
from datetime import UTC, datetime, timedelta

from pickem.models import HISTORY_MONITOR, Game, RecommendationRecord, Side, Sport, Tier
from pickem.report.results import BoardStanding, ResultsReport, WeekStanding, grade_game

GENERATED = datetime(2026, 9, 29, 13, tzinfo=UTC)
KICK = datetime(2026, 9, 5, 16, tzinfo=UTC)


def synthetic_season(seed: int = 7, per_week: int = 30) -> ResultsReport:
    """Two pool weeks on both boards.

    Includes pushes, blank picks, every tier, and games the model skipped.
    """
    rng = random.Random(seed)
    games = []
    for week in (1, 2):
        for i in range(per_week):
            sport = Sport.CFB if i % 2 else Sport.NFL
            line = rng.choice([-7.0, -3.5, -3.0, 1.5, 3.0, 6.5])
            margin = -int(line) if i % 7 == 0 and line == int(line) else rng.randint(-14, 14)
            game = Game(
                game_id=f"{sport.value}-2026-{week:02d}-a{i}-at-h{i}", sport=sport, season=2026,
                week=week, kickoff_utc=KICK + timedelta(days=7 * (week - 1), hours=i),
                home_team_id=f"H{week}{i}", away_team_id=f"A{week}{i}",
                home_score=20 + margin, away_score=20,
            )
            tier = rng.choice([None, Tier.STRONG, Tier.LEAN, Tier.COINFLIP, Tier.NO_MARKET])
            history = [] if tier is None else [
                RecommendationRecord(
                    game_id=game.game_id, sport=sport, season=2026, week=week,
                    side=rng.choice([Side.HOME, Side.AWAY]), tier=tier,
                    edge_points=round(rng.uniform(-3, 3), 1),
                    generated_at=game.kickoff_utc - timedelta(hours=2), source=HISTORY_MONITOR,
                )
            ]
            games.append(grade_game(
                game=game, pool_week=week, league_spread=line,
                close_spread=rng.choice([None, line - 1, line, line + 0.5]),
                our_side=rng.choice([None, Side.HOME, Side.AWAY, Side.HOME]),
                field_home=rng.randint(0, 20), field_away=rng.randint(0, 20), history=history,
            ))
    standings = tuple(
        WeekStanding(
            pool_week=week, entrants=20, our_rank=5, our_points=15, median_points=14.0,
            winner_points=20, beat_share=0.75,
            boards=(BoardStanding(Sport.CFB, 8, 7.0, 11), BoardStanding(Sport.NFL, 7, 7.0, 10)),
        )
        for week in (1, 2)
    )
    return ResultsReport(
        season=2026, pool_week=2, entry_name="Jota", standings=standings,
        week_games=tuple(g for g in games if g.pool_week == 2), season_games=tuple(games),
    )
