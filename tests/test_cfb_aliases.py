"""Guards on the CFB alias table.

Generated 2026-08-11 from the CFBD FBS team registry (2021-2025) and verified
by sweeping 2022-2025 weeks 1-15 through the shipped loaders: 3,173 games and
10,197 lines resolved with zero UnknownTeamError.
"""

import pathlib

import pytest
import yaml

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

ALIASES = pathlib.Path("src/pickem/resolve/aliases.yaml")

# YAML 1.1 reads these bare tokens as booleans/null, which crashes the resolver.
TRAP = {"NO", "ON", "OFF", "YES", "Y", "N", "TRUE", "FALSE", "NULL", "~"}


@pytest.fixture(scope="module")
def resolver():
    return TeamResolver.default()


def test_the_table_covers_the_whole_fbs():
    raw = yaml.safe_load(ALIASES.read_text())
    assert len(raw["cfb"]) >= 130, "an FBS-wide table should carry ~136 schools"
    assert len(raw["nfl"]) == 32


def test_no_id_or_alias_can_be_read_as_a_yaml_boolean():
    raw = yaml.safe_load(ALIASES.read_text())
    for sport, teams in raw.items():
        for team_id, aliases in teams.items():
            assert isinstance(team_id, str), f"{sport}: {team_id!r} did not parse as a string"
            for alias in aliases:
                assert isinstance(alias, str), f"{sport}/{team_id}: {alias!r} is not a string"
                assert alias.upper() not in TRAP or alias == alias  # quoted, so it parsed


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # The 14 hand-entered ids must survive the generated table unchanged.
        ("Alabama", "BAMA"),
        ("Ole Miss", "MISS"),
        ("Mississippi State", "MSST"),
        ("Ohio State", "OSU"),
        ("Notre Dame", "ND"),
        ("LSU", "LSU"),
        ("USC", "USC"),
        # Schools the sweep added.
        ("Rutgers", "RUTG"),
        ("Boise State", "BOIS"),
        ("Texas A&M", "TAM"),
        # Diacritics and punctuation as CFBD spells them.
        ("San José State", "SJSU"),
        ("Hawai'i", "HAW"),
    ],
)
def test_known_schools_resolve(resolver, name, expected):
    assert resolver.resolve(name, Sport.CFB) == expected


def test_the_two_miamis_stay_distinct(resolver):
    assert resolver.resolve("Miami (OH)", Sport.CFB) == "MIAOH"
    assert resolver.resolve("Miami (FL)", Sport.CFB) == "MIAFL"
    # CFBD calls the Hurricanes plain "Miami", so bare "Miami" maps there.
    assert resolver.resolve("Miami", Sport.CFB) == "MIAFL"


def test_school_plus_mascot_resolves(resolver):
    # The naming style live odds feeds use.
    assert resolver.resolve("Alabama Crimson Tide", Sport.CFB) == "BAMA"
    assert resolver.resolve("Georgia Bulldogs", Sport.CFB) == "UGA"


def test_an_fcs_school_still_fails_loud(resolver):
    # The table is FBS-only by design. An FCS opponent on the CBS sheet must
    # stop the ingest rather than resolve to something that looks close.
    with pytest.raises(UnknownTeamError):
        resolver.resolve("Villanova", Sport.CFB)


@pytest.mark.parametrize(
    ("cbs_spelling", "expected"),
    [("Boise St.", "BOIS"), ("Colorado St.", "CSU"), ("Washington St.", "WSU")],
)
def test_the_cbs_st_abbreviation_resolves(resolver, cbs_spelling, expected):
    """Catches the spelling CBS actually ships.

    The saved week-1 2026 page writes `Boise St.` where the table was generated
    from CFBD's `Boise State`. An unknown name aborts the whole ingest, so a
    missing spelling costs a week rather than a row.
    """
    assert resolver.resolve(cbs_spelling, Sport.CFB) == expected
