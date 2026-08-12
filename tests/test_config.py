"""Key resolution: a real environment variable always beats the .env file."""

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
