from __future__ import annotations

from pydantic import BaseModel, model_validator

from pickem.models import Sport, make_game_id
from pickem.resolve.resolver import TeamResolver


class CanonicalMatchup(BaseModel):
    sport: Sport
    season: int
    week: int
    away_team_id: str
    home_team_id: str

    @model_validator(mode="after")
    def _teams_must_differ(self) -> CanonicalMatchup:
        if self.away_team_id == self.home_team_id:
            raise ValueError(f"a team cannot play itself: {self.away_team_id}")
        return self

    @property
    def game_id(self) -> str:
        return make_game_id(
            self.sport,
            self.season,
            self.week,
            self.away_team_id,
            self.home_team_id,
        )


def resolve_matchup(
    *,
    resolver: TeamResolver,
    sport: Sport,
    season: int,
    week: int,
    away_name: str,
    home_name: str,
) -> CanonicalMatchup:
    return CanonicalMatchup(
        sport=sport,
        season=season,
        week=week,
        away_team_id=resolver.resolve(away_name, sport),
        home_team_id=resolver.resolve(home_name, sport),
    )
