"""CBS prints its own team abbreviations on the Weekly Standings page.

Pinned from the saved 2026 pool week 1-2 pages. Several already resolve through
existing spellings; they are pinned too, so a later table edit cannot break them.
"""

import pytest

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

CBS_CFB = {
    "ARIZST": "ASU",
    "BOISE": "BOIS",
    "CINCY": "CIN",
    "COLOST": "CSU",
    "GATECH": "GT",
    "IOWAST": "ISU",
    "KSTATE": "KSU",
    "LVILLE": "LOU",
    "MISSST": "MSST",
    "MRSHL": "MRSH",
    "OHIOST": "OSU",
    "OKLA": "OU",
    "OKLAST": "OKST",
    "OREG": "ORE",
    "OREGST": "ORST",
    "TXAM": "TAM",
    "TXTECH": "TTU",
    "WASHST": "WSU",
    "WISC": "WIS",
    "AUBURN": "AUB",
    "BAYLOR": "BAY",
    "TEMPLE": "TEM",
    "TEXAS": "TEX",
    "TULANE": "TULN",
    "TULSA": "TLSA",
    "BAMA": "BAMA",
    "UK": "UK",
}


@pytest.fixture(scope="module")
def resolver():
    return TeamResolver.default()


@pytest.mark.parametrize(("abbrev", "team_id"), sorted(CBS_CFB.items()))
def test_cbs_cfb_abbreviation_resolves(resolver, abbrev, team_id):
    assert resolver.resolve(abbrev, Sport.CFB) == team_id


def test_cbs_nfl_jacksonville_abbreviation_resolves(resolver):
    assert resolver.resolve("JAC", Sport.NFL) == "JAX"
