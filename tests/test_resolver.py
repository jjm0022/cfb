import pytest

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError


@pytest.fixture
def resolver():
    return TeamResolver.default()


def test_resolves_canonical_name(resolver):
    assert resolver.resolve("Miami Dolphins", Sport.NFL) == "MIA"


def test_resolves_across_source_naming_conventions(resolver):
    # Different feeds spell the same franchise differently.
    for name in ["LA Rams", "Los Angeles Rams", "LAR", "Rams"]:
        assert resolver.resolve(name, Sport.NFL) == "LAR"


def test_resolution_is_case_and_whitespace_insensitive(resolver):
    assert resolver.resolve("  miami DOLPHINS ", Sport.NFL) == "MIA"


def test_resolves_cfb_naming_mismatch(resolver):
    # CBS says "Ole Miss", CFBD says "Mississippi".
    assert resolver.resolve("Ole Miss", Sport.CFB) == "MISS"
    assert resolver.resolve("Mississippi", Sport.CFB) == "MISS"


def test_same_string_can_mean_different_teams_in_different_sports(resolver):
    # There is an NFL and a college team that share a nickname; sport disambiguates.
    assert resolver.resolve("Miami Dolphins", Sport.NFL) == "MIA"
    assert resolver.resolve("Miami (FL)", Sport.CFB) == "MIAFL"


def test_unknown_team_raises_rather_than_guessing(resolver):
    with pytest.raises(UnknownTeamError):
        resolver.resolve("Fictional State Aardvarks", Sport.CFB)


def test_unknown_team_error_suggests_close_matches(resolver):
    with pytest.raises(UnknownTeamError) as exc:
        resolver.resolve("Ole Mis", Sport.CFB)
    # The suggestion helps the user extend aliases.yaml; it never auto-resolves.
    assert "Ole Miss" in str(exc.value) or "MISS" in str(exc.value)


def test_never_falls_back_to_fuzzy_resolution(resolver):
    # A near-miss must still raise. Fuzzy matching informs the human, never the data.
    with pytest.raises(UnknownTeamError):
        resolver.resolve("Miami Dolphin", Sport.NFL)
