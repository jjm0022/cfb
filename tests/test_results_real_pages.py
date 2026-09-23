"""Checks against the real saved standings pages.

The pages are not in the repo, so these skip anywhere they are not present.
``PAGES`` follows the configured results directory rather than naming a path,
so moving that location cannot silently turn these tests into skips.
"""

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from results_helpers import assert_well_formed

from pickem.backtest.stats import Result, grade_pick
from pickem.config import DEFAULT_ENTRY_NAME, DEFAULT_RESULTS_DIR
from pickem.ingest.cbs_results import parse_cbs_results_html
from pickem.models import Side
from pickem.report.results import build_results_report
from pickem.report.results_html import render_results_dashboard
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store

PAGES = DEFAULT_RESULTS_DIR
LIVE_DB = Path("data/pickem.duckdb")

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


def test_every_imported_real_week_renders_a_complete_page():
    if not LIVE_DB.exists():
        pytest.skip("live database is local-only")
    try:
        store = Store(LIVE_DB, read_only=True)
    except duckdb.Error as exc:
        pytest.skip(f"live database is busy: {exc}")
    with store:
        weeks = store.pool_weeks(2026)
        assert weeks, "no imported pool weeks in the live database"
        for week in weeks:
            report = build_results_report(
                store, season=2026, pool_week=week, entry_name=DEFAULT_ENTRY_NAME
            )
            page = render_results_dashboard(
                report, generated_at=datetime.now(tz=UTC), imported_weeks=weeks
            )
            assert_well_formed(page)
            for heading in ("This week", "Season trend", "Model by tier", "What the data says"):
                assert heading in page, (week, heading)
