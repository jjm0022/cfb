from dataclasses import replace

import pytest
from results_helpers import IMPORTED_AT, JOTA_ID, SEEDED, parsed_fixture, seed

from pickem.models import Side, Sport
from pickem.operations.results_import import (
    ResultsImportError,
    import_results,
    league_week,
)
from pickem.resolve.resolver import TeamResolver
from pickem.store.db import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    s.init_schema()
    yield s
    s.close()


def run_import(store, parsed=None):
    return import_results(
        store, parsed or parsed_fixture(), season=2026, pool_week=2,
        resolver=TeamResolver.default(), imported_at=IMPORTED_AT,
    )


def test_pool_week_translates_to_each_boards_league_week():
    assert league_week(Sport.CFB, 2) == 2
    assert league_week(Sport.NFL, 2) == 1
    with pytest.raises(ResultsImportError, match="no NFL board"):
        league_week(Sport.NFL, 1)


def test_imports_every_entrant_and_pick(store):
    seed(store)
    summary = run_import(store)
    assert (summary.entrants, summary.picks, summary.blank_picks) == (4, 16, 3)
    assert summary.games_by_sport == {Sport.CFB: 2, Sport.NFL: 2}
    assert summary.scores_filled == 0

    jota = next(r for r in store.pool_results(2026, 2) if r.entry_id == JOTA_ID)
    assert (jota.name, jota.rank, jota.points, jota.tiebreak) == ("Jota", 17, 16, 41)

    picks = {(p.entry_id, p.game_id): p for p in store.pool_picks(2026, 2)}
    assert picks[(JOTA_ID, "nfl-2026-01-NE-at-SEA")].side is Side.HOME
    assert picks[(JOTA_ID, "nfl-2026-01-NE-at-SEA")].cbs_correct is False
    assert picks[(JOTA_ID, "cfb-2026-02-PSU-at-TEM")].side is Side.AWAY
    assert picks[("fixtureentryb", "nfl-2026-01-NE-at-SEA")].side is None


def test_fills_missing_scores_from_cbs(store):
    seed(store, scores=False)
    summary = run_import(store)
    assert summary.scores_filled == 4
    game = store.games_by_ids(["cfb-2026-02-OU-at-MICH"])[0]
    assert (game.away_score, game.home_score) == (10, 17)


def test_unresolved_team_aborts_without_writing(store):
    seed(store)
    parsed = parsed_fixture()
    parsed = replace(
        parsed, games=(replace(parsed.games[0], away_abbrev="ZZZZ"),) + parsed.games[1:]
    )
    with pytest.raises(ResultsImportError, match="ZZZZ"):
        run_import(store, parsed)
    assert store.pool_weeks(2026) == []


def test_missing_stored_game_aborts(store):
    seed(store, [row for row in SEEDED if row[3] != "TEM"])
    with pytest.raises(ResultsImportError, match="no stored game cfb-2026-02-PSU-at-TEM"):
        run_import(store)
    assert store.pool_weeks(2026) == []


def test_extra_stored_league_line_aborts(store):
    seed(store, [*SEEDED, (Sport.CFB, 2, "ARK", "UTAH", -11.5, 10, 43)])
    with pytest.raises(ResultsImportError, match="the page has 2 games but the store has 3"):
        run_import(store)


def test_missing_board_aborts(store):
    seed(store)
    parsed = parsed_fixture()
    cfb_only = replace(
        parsed,
        games=tuple(g for g in parsed.games if g.sport is Sport.CFB),
        picks=tuple(p for p in parsed.picks if p.cbs_event_id in {50027615, 50027628}),
    )
    with pytest.raises(ResultsImportError, match="nfl week 1: the page has 0 games"):
        run_import(store, cfb_only)


def test_score_disagreement_aborts(store):
    seed(store, [row if row[3] != "MICH" else (*row[:6], 20) for row in SEEDED])
    with pytest.raises(ResultsImportError, match="CBS final 10-17 but the store has 10-20"):
        run_import(store)


def test_line_disagreement_aborts(store):
    seed(store, [row if row[3] != "TEM" else (*row[:4], 23.5, *row[5:]) for row in SEEDED])
    with pytest.raises(
        ResultsImportError, match=r"CBS line \+24.5 but the stored league line is \+23.5"
    ):
        run_import(store)


def test_grade_that_disagrees_with_the_score_aborts(store):
    seed(store)
    parsed = parsed_fixture()
    picks = tuple(
        replace(p, cbs_correct=False) if (p.entry_id, p.cbs_event_id) == (JOTA_ID, 50027615) else p
        for p in parsed.picks
    )
    with pytest.raises(ResultsImportError, match="Jota on OKLA at MICH"):
        run_import(store, replace(parsed, picks=picks))
    assert store.pool_weeks(2026) == []


def test_reimport_replaces_the_week(store):
    seed(store)
    run_import(store)
    parsed = parsed_fixture()
    fewer = replace(
        parsed,
        entrants=tuple(e for e in parsed.entrants if e.entry_id != "fixtureentryc"),
        picks=tuple(p for p in parsed.picks if p.entry_id != "fixtureentryc"),
    )
    run_import(store, fewer)
    assert len(store.pool_results(2026, 2)) == 3
    assert len(store.pool_picks(2026, 2)) == 12
