"""SportsGameOdds client for live market spreads.

The free plan bills one object per event returned, 2,500 per rolling 30 days
for the whole account, and props pulls on the same account. Every request
therefore names the teams it wants: a time-range-only query is billed for every
college game in that slot, most of which we never pick.

Each event is converted to the shape The Odds API feed uses and parsed by the
same `_parse_events`, so the kickoff window, slate, unknown-team and
neutral-site guards are the code the rest of the system was built on.
"""

from __future__ import annotations

import re
import unicodedata

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

LEAGUES = {Sport.NFL: "NFL", Sport.CFB: "NCAAF"}
# Where SportsGameOdds' id is not the team's display name run through the rule
# in `sgo_team_id`. Add a row when a pull lists "no SportsGameOdds lines" for a
# game the books are pricing.
TEAM_ID_OVERRIDES: dict[tuple[Sport, str], str] = {
    (Sport.CFB, "MIAFL"): "MIAMI_NCAAF",
}


def sgo_team_id(team_id: str, sport: Sport, resolver: TeamResolver) -> str:
    """SportsGameOdds' id for one of our teams, e.g. ``KANSAS_CITY_CHIEFS_NFL``.

    The team's display name (its first spelling in aliases.yaml), folded to
    ASCII with apostrophes dropped, every other run of non-alphanumerics made
    one underscore, uppercased, plus the league. Checked against every college
    id in props' saved pulls (tests/fixtures/sgo_fbs_team_ids.json).
    """
    override = TEAM_ID_OVERRIDES.get((sport, team_id))
    if override is not None:
        return override
    name = resolver.display_name(team_id, sport)
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9]+", "_", folded.replace("'", "")).strip("_").upper()
    return f"{stem}_{LEAGUES[sport]}"
