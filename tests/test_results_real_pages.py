"""Checks against the real saved standings pages.

`data/` is gitignored, so these skip anywhere the pages are not present.
"""

from pathlib import Path

import pytest

from pickem.backtest.stats import Result, grade_pick
from pickem.ingest.cbs_results import parse_cbs_results_html
from pickem.models import Side
from pickem.resolve.resolver import TeamResolver

PAGES = Path("data/cbs/results")

pytestmark = pytest.mark.skipif(
    not (PAGES / "week2.html").exists(), reason="real CBS pages are local-only"
)


@pytest.mark.parametrize(
    ("week", "games", "jota_points", "jota_rank"), [(1, 15, 8, 21), (2, 31, 16, 17)]
)
def test_real_standings_page(week, games, jota_points, jota_rank):
    parsed = parse_cbs_results_html((PAGES / f"week{week}.html").read_text(encoding="utf-8"))

    assert len(parsed.games) == games
    assert len(parsed.entrants) == 53
    jota = next(e for e in parsed.entrants if e.name == "Jota")
    assert (jota.points, jota.rank) == (jota_points, jota_rank)

    by_game = {game.cbs_event_id: game for game in parsed.games}
    for pick in parsed.picks:
        if pick.picked_abbrev is None:
            continue
        game = by_game[pick.cbs_event_id]
        side = Side.HOME if pick.picked_abbrev == game.home_abbrev else Side.AWAY
        result = grade_pick(side, game.home_score - game.away_score, game.spread_home)
        assert (result is Result.WIN) == pick.cbs_correct, (game, pick)

    # CBS's points column is exactly its own count of green marks.
    for entrant in parsed.entrants:
        greens = sum(1 for p in parsed.picks if p.entry_id == entrant.entry_id and p.cbs_correct)
        assert entrant.points == greens, entrant.name

    resolver = TeamResolver.default()
    for game in parsed.games:
        resolver.resolve(game.away_abbrev, game.sport)
        resolver.resolve(game.home_abbrev, game.sport)
