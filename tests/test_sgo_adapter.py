import json
from pathlib import Path

from pickem.ingest.sgo import TEAM_ID_OVERRIDES, sgo_team_id
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

FIXTURES = Path(__file__).parent / "fixtures"
RESOLVER = TeamResolver.default()


def test_every_college_team_id_props_has_seen_is_reproduced():
    seen = json.loads((FIXTURES / "sgo_fbs_team_ids.json").read_text())

    built = {team: sgo_team_id(team, Sport.CFB, RESOLVER) for team in seen}

    assert built == seen


def test_apostrophes_are_dropped_not_turned_into_underscores():
    assert sgo_team_id("HAW", Sport.CFB, RESOLVER) == "HAWAII_NCAAF"


def test_miami_fl_uses_its_override():
    assert TEAM_ID_OVERRIDES[(Sport.CFB, "MIAFL")] == "MIAMI_NCAAF"
    assert sgo_team_id("MIAFL", Sport.CFB, RESOLVER) == "MIAMI_NCAAF"


def test_nfl_ids_are_the_full_team_name():
    assert sgo_team_id("KC", Sport.NFL, RESOLVER) == "KANSAS_CITY_CHIEFS_NFL"
    assert sgo_team_id("SF", Sport.NFL, RESOLVER) == "SAN_FRANCISCO_49ERS_NFL"
