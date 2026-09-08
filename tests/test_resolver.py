import pytest

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError


@pytest.fixture
def resolver():
    return TeamResolver.default()


def test_resolves_canonical_name(resolver):
    assert resolver.resolve("Miami Dolphins", Sport.NFL) == "MIA"


def test_displays_the_primary_friendly_team_name(resolver):
    assert resolver.display_name("MIA", Sport.NFL) == "Miami Dolphins"
    assert resolver.display_name("MISSING", Sport.NFL) == "MISSING"


def test_resolves_across_source_naming_conventions(resolver):
    # Different feeds spell the same franchise differently.
    for name in ["LA Rams", "Los Angeles Rams", "LAR", "Rams"]:
        assert resolver.resolve(name, Sport.NFL) == "LAR"


def test_resolves_cbs_bare_las_vegas_name(resolver):
    assert resolver.resolve("Las Vegas", Sport.NFL) == "LV"


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


def test_resolves_new_orleans_saints(resolver):
    # Regression guard: bare `NO` is a YAML 1.1 boolean literal (parses as
    # False), so the "NO" key in aliases.yaml must stay quoted as a string.
    # If that quoting is ever reverted, TeamResolver.default() raises
    # AttributeError('bool' object has no attribute 'strip') during fixture
    # setup for every test in this file, not a targeted assertion here.
    for name in ["New Orleans Saints", "New Orleans", "NO", "NOR"]:
        assert resolver.resolve(name, Sport.NFL) == "NO"


def test_duplicate_alias_raises_at_load_time(tmp_path):
    # An alias claimed by two different team ids must fail loudly at build
    # time rather than silently resolving to whichever team happened to be
    # processed last.
    conflicting_yaml = tmp_path / "conflicting_aliases.yaml"
    conflicting_yaml.write_text('nfl:\n  MIA: ["Dolphins"]\n  BUF: ["Dolphins"]\n')
    with pytest.raises(ValueError):
        TeamResolver.from_yaml(conflicting_yaml)


def test_washington_resolves_under_every_name_the_franchise_has_used():
    """The odds archive spans a rename, so era-varying names are era-varying data.

    Washington was the Football Team for 2020-2021, which is inside the
    backfill range. A missing spelling here does not raise on the odds feed —
    it silently drops that team's games from the backtest.
    """
    resolver = TeamResolver.default()
    for name in [
        "Washington Commanders",
        "Washington Football Team",
        "Washington Redskins",
        "Washington",
        "WAS",
    ]:
        assert resolver.resolve(name, Sport.NFL) == "WAS"
