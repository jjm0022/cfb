"""Logo sources, the one-time download, and the listing the page reads."""

from typer.testing import CliRunner

from pickem.cli import app
from pickem.ingest.logos import (
    LOGO_DIR,
    LogoSource,
    available_logos,
    cfb_sources,
    download_logos,
    nfl_sources,
)
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
CDN = "https://cdn.collegefootballdata.com"


def cfbd_team(school: str, number: int) -> dict:
    sizes = ("500", "96", "64")
    return {"school": school, "logos": [
        *(f"{CDN}/logos/{size}/{number}.png" for size in sizes),
        *(f"{CDN}/logos-dark/{size}/{number}.png" for size in sizes),
    ]}


def test_cfb_logos_match_through_the_alias_table():
    sources, unmatched = cfb_sources(
        [cfbd_team("Air Force", 2005), cfbd_team("Nowhere State", 1)], TeamResolver.default())
    assert [(s.team_id, s.url, s.dark) for s in sources] == [
        ("AF", f"{CDN}/logos/96/2005.png", False),
        ("AF", f"{CDN}/logos-dark/96/2005.png", True),
    ]
    assert unmatched == ["Nowhere State"]


def test_a_cfbd_team_with_no_logos_is_skipped():
    sources, unmatched = cfb_sources(
        [{"school": "Air Force", "logos": None}], TeamResolver.default()
    )
    assert sources == [] and unmatched == []


def test_nfl_logos_use_espn_codes_with_washingtons_exception():
    sources = nfl_sources(["BUF", "WAS"])
    assert [(s.team_id, s.url, s.dark) for s in sources] == [
        ("BUF", "https://a.espncdn.com/i/teamlogos/nfl/500/buf.png", False),
        ("BUF", "https://a.espncdn.com/i/teamlogos/nfl/500-dark/buf.png", True),
        ("WAS", "https://a.espncdn.com/i/teamlogos/nfl/500/wsh.png", False),
        ("WAS", "https://a.espncdn.com/i/teamlogos/nfl/500-dark/wsh.png", True),
    ]
    assert [s.relative_path for s in sources[:2]] == ["nfl/BUF.png", "nfl/BUF-dark.png"]


def test_every_nfl_team_has_a_light_and_a_dark_logo_source():
    team_ids = TeamResolver.default().team_ids(Sport.NFL)
    sources = nfl_sources(team_ids)
    assert len(sources) == 64
    assert {(s.team_id, s.dark) for s in sources} == {
        (team, dark) for team in team_ids for dark in (False, True)
    }


def test_download_saves_new_keeps_existing_and_reports_failures(tmp_path):
    (tmp_path / "nfl").mkdir()
    (tmp_path / "nfl" / "BUF.png").write_bytes(PNG)
    sources = [
        LogoSource(Sport.NFL, "BUF", "u/buf"),
        LogoSource(Sport.CFB, "AF", "u/af"),
        LogoSource(Sport.CFB, "AF", "u/af-dark", dark=True),
        LogoSource(Sport.NFL, "MIA", "u/broken"),
        LogoSource(Sport.NFL, "NE", "u/html"),
    ]
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url == "u/broken":
            raise OSError("timed out")
        return b"<html>not found</html>" if url == "u/html" else PNG

    outcome = download_logos(sources, tmp_path, fetch)
    assert outcome.saved == ["cfb/AF.png", "cfb/AF-dark.png"]
    assert outcome.kept == ["nfl/BUF.png"]
    assert outcome.failed == ["nfl/MIA.png: timed out", "nfl/NE.png: not a PNG image"]
    assert "u/buf" not in fetched
    assert (tmp_path / "cfb" / "AF-dark.png").read_bytes() == PNG
    assert not (tmp_path / "nfl" / "NE.png").exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_available_logos_lists_saved_files_by_theme_and_board(tmp_path):
    for name in ("cfb/AF.png", "cfb/AF-dark.png", "cfb/OU.png", "nfl/BUF.png", "nfl/notes.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(PNG)
    assert available_logos(tmp_path) == {
        "light": {"cfb": ["AF", "OU"], "nfl": ["BUF"]},
        "dark": {"cfb": ["AF"], "nfl": []},
    }


def test_no_logo_folder_lists_nothing(tmp_path):
    assert available_logos(tmp_path / "missing") == {
        "light": {"cfb": [], "nfl": []}, "dark": {"cfb": [], "nfl": []},
    }


def test_fetch_logos_command_fills_the_dashboard_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pickem.cli.fetch_fbs_teams", lambda config, season: [cfbd_team("Air Force", 2005)]
    )
    monkeypatch.setattr("pickem.cli.fetch_logo", lambda url: PNG)
    monkeypatch.setenv("CFBD_API_KEY", "test-key")
    result = CliRunner().invoke(
        app, ["fetch-logos", "--season", "2026", "--dashboard-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / LOGO_DIR / "cfb" / "AF.png").exists()
    assert (tmp_path / LOGO_DIR / "nfl" / "WAS.png").exists()
    assert (tmp_path / LOGO_DIR / "nfl" / "WAS-dark.png").exists()
    assert "saved 66" in result.output  # light + dark for AF and the 32 NFL teams


def test_fetch_logos_exits_1_when_any_download_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("pickem.cli.fetch_fbs_teams", lambda config, season: [])

    def fetch(url):
        raise OSError("offline")

    monkeypatch.setattr("pickem.cli.fetch_logo", fetch)
    monkeypatch.setenv("CFBD_API_KEY", "test-key")
    result = CliRunner().invoke(
        app, ["fetch-logos", "--season", "2026", "--dashboard-dir", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "nfl/BUF.png: offline" in result.output
