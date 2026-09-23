"""Key resolution: a real environment variable always beats the .env file."""

from pathlib import Path

import pytest

from pickem import config


def test_an_exported_variable_wins_over_the_dotenv_file(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "exported-value")
    assert config.odds_api_key() == "exported-value"


def test_a_missing_key_says_how_to_supply_it(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        config.cfbd_api_key()
    assert "CFBD_API_KEY" in str(exc.value)
    assert ".env" in str(exc.value)


def test_an_empty_value_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "")
    with pytest.raises(RuntimeError):
        config.odds_api_key()


def nas_at(monkeypatch, tmp_path, *, mounted: bool) -> Path:
    """Point the NAS constants at a temporary tree, present or absent."""
    mount = tmp_path / "mnt" / "nas"
    nas_dir = mount / "Betting" / "pickem"
    if mounted:
        nas_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "NAS_MOUNT", mount)
    monkeypatch.setattr(config, "NAS_DIR", nas_dir)
    return nas_dir


def test_a_path_off_the_nas_is_never_blamed_on_the_share(monkeypatch, tmp_path):
    nas_at(monkeypatch, tmp_path, mounted=False)
    assert config.nas_unavailable(Path("data/pickem.duckdb")) is None


def test_a_mounted_share_reports_no_problem(monkeypatch, tmp_path):
    nas_dir = nas_at(monkeypatch, tmp_path, mounted=True)
    assert config.nas_unavailable(nas_dir / "results" / "week3.html") is None


def test_an_unreachable_share_names_the_mount_and_how_to_check_it(monkeypatch, tmp_path):
    nas_dir = nas_at(monkeypatch, tmp_path, mounted=False)
    reason = config.nas_unavailable(nas_dir / "results" / "week3.html")

    assert reason is not None
    # The operator needs the mount point and a command, not just "not found".
    assert str(config.NAS_MOUNT) in reason
    assert "mount" in reason.lower()


def test_the_share_root_itself_is_checked(monkeypatch, tmp_path):
    nas_dir = nas_at(monkeypatch, tmp_path, mounted=False)
    assert config.nas_unavailable(nas_dir) is not None


def test_dashboard_dir_defaults_under_local_share(monkeypatch):
    monkeypatch.delenv("PICKEM_DASHBOARD_DIR", raising=False)
    assert config.dashboard_dir() == Path.home() / ".local/share/pickem/dashboard"


def test_dashboard_dir_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PICKEM_DASHBOARD_DIR", str(tmp_path / "dash"))
    assert config.dashboard_dir() == tmp_path / "dash"


def test_dashboard_url_is_none_when_unset_or_blank(monkeypatch):
    monkeypatch.delenv("PICKEM_DASHBOARD_URL", raising=False)
    assert config.dashboard_url() is None
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "  ")
    assert config.dashboard_url() is None


def test_dashboard_url_always_ends_in_a_slash(monkeypatch):
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem")
    assert config.dashboard_url() == "https://sandbox.tail750bff.ts.net/pickem/"
    monkeypatch.setenv("PICKEM_DASHBOARD_URL", "https://sandbox.tail750bff.ts.net/pickem/")
    assert config.dashboard_url() == "https://sandbox.tail750bff.ts.net/pickem/"
