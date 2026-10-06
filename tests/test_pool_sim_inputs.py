from datetime import timedelta

import pytest
from results_helpers import JOTA_ID, KICK, imported_store

from pickem.models import RecommendationRecord, Side, Sport, Tier
from pickem.operations.pool_sim_inputs import SimInputError, load_sim_week

MICH = "cfb-2026-02-OU-at-MICH"
TEM = "cfb-2026-02-PSU-at-TEM"
SEA = "nfl-2026-01-NE-at-SEA"
DET = "nfl-2026-01-NO-at-DET"


def rec(game_id, side, tier, *, hours_before=2, sport=Sport.CFB, week=2):
    return RecommendationRecord(
        game_id=game_id, sport=sport, season=2026, week=week, side=side, tier=tier,
        edge_points=0.0, generated_at=KICK - timedelta(hours=hours_before), source="refresh",
    )


@pytest.fixture
def store():
    s = imported_store()
    s.append_recommendation_history([
        rec(MICH, Side.AWAY, Tier.LEAN, hours_before=30),
        rec(MICH, Side.HOME, Tier.COINFLIP, hours_before=2),  # the last one before kickoff wins
        rec(MICH, Side.AWAY, Tier.STRONG, hours_before=-1),  # after kickoff: ignored
        rec(TEM, Side.AWAY, Tier.STRONG),
        rec(SEA, Side.HOME, Tier.LEAN, sport=Sport.NFL, week=1),
        # DET has no recommendation.
    ])
    return s


def test_games_take_the_last_recommendation_before_kickoff(store):
    week = load_sim_week(store, season=2026, pool_week=2, entry_name="Jota")
    by_id = {g.game_id: g for g in week.games}
    assert [g.game_id for g in week.games] == sorted([MICH, TEM, SEA, DET])
    assert (by_id[MICH].tier, by_id[MICH].model_side) == (Tier.COINFLIP, Side.HOME)
    assert (by_id[TEM].tier, by_id[TEM].model_side) == (Tier.STRONG, Side.AWAY)
    assert by_id[MICH].league_spread == 5.5
    assert by_id[MICH].label == "OU at MICH"


def test_a_game_without_a_recommendation_uses_our_pick_and_is_counted(store):
    week = load_sim_week(store, season=2026, pool_week=2, entry_name="Jota")
    det = next(g for g in week.games if g.game_id == DET)
    assert det.tier is None
    assert det.model_side is Side.HOME  # Jota picked DET
    assert week.unrecommended == 1


def test_others_exclude_our_entry_and_keep_blanks(store):
    week = load_sim_week(store, season=2026, pool_week=2, entry_name="Jota")
    assert week.entrants == 4
    assert len(week.others) == 3
    assert all(len(row) == 4 for row in week.others)
    picks = store.pool_picks(2026, 2)
    blanks = sum(1 for p in picks if p.side is None and p.entry_id != JOTA_ID)
    assert sum(row.count(None) for row in week.others) == blanks


def test_unknown_entry_name_names_the_entrants(store):
    with pytest.raises(SimInputError, match="no entrant named 'Nobody'.*Jota"):
        load_sim_week(store, season=2026, pool_week=2, entry_name="Nobody")


def test_week_not_imported(store):
    with pytest.raises(SimInputError, match="pool week 9 of 2026 is not imported"):
        load_sim_week(store, season=2026, pool_week=9, entry_name="Jota")
