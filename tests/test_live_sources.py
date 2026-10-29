from datetime import UTC, datetime, timedelta

import pytest

from pickem import config
from pickem.backtest.runner import split_proxies
from pickem.models import CFBD_SOURCE, LIVE_SOURCE, LIVE_SOURCES, SGO_SOURCE, MarketLine
from pickem.obs.log import _secret_values
from pickem.report.results import US_LINE, closing_spread, line_history

KICKOFF = datetime(2026, 10, 23, 0, 15, tzinfo=UTC)


def _line(source: str, book: str, spread: float, hours_before: float) -> MarketLine:
    return MarketLine(
        game_id="g",
        source=source,
        book=book,
        spread_home=spread,
        captured_at=KICKOFF - timedelta(hours=hours_before),
    )


def test_every_live_provider_is_the_live_market():
    assert LIVE_SOURCES == {LIVE_SOURCE, SGO_SOURCE, CFBD_SOURCE}


def test_cfbd_backup_rows_are_never_graded_as_a_backtest_proxy():
    frozen, submission, unclassified = split_proxies([_line(CFBD_SOURCE, "draftkings", 6.5, 1)])

    assert (frozen, submission) == ([], [])
    assert "not graded" in unclassified[0]


def test_the_closing_line_reads_sportsgameodds_rows():
    lines = [
        _line(SGO_SOURCE, "draftkings", -3.0, 1),
        _line(SGO_SOURCE, "fanduel", -3.5, 1),
        _line(SGO_SOURCE, "bovada", -4.0, 1),
    ]

    assert closing_spread(lines, KICKOFF) == -3.5


def test_line_history_includes_sportsgameodds_polls():
    points = line_history([_line(SGO_SOURCE, "draftkings", -3.0, 2)], KICKOFF)

    assert [(point.source, point.spread_home) for point in points] == [(US_LINE, -3.0)]


def test_sportsgameodds_rows_are_never_graded_as_a_backtest_proxy():
    frozen, submission, unclassified = split_proxies([_line(SGO_SOURCE, "dk", -3.0, 1)])

    assert (frozen, submission) == ([], [])
    assert "not graded" in unclassified[0]


def test_the_sportsgameodds_key_is_required(monkeypatch):
    monkeypatch.delenv("SPORTSGAMEODDS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SPORTSGAMEODDS_API_KEY"):
        config.sgo_api_key()

    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "sgo-key-value")
    assert config.sgo_api_key() == "sgo-key-value"


def test_the_sportsgameodds_key_is_redacted_from_logs(monkeypatch):
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "sgo-secret-value-1")

    assert "sgo-secret-value-1" in _secret_values()
