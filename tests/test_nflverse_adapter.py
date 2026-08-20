from datetime import UTC, datetime

import polars as pl
import pytest

from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

FRAME = pl.DataFrame(
    {
        "season": [2025],
        "week": [3],
        "gameday": ["2025-09-21"],
        "gametime": ["13:00"],
        "home_team": ["MIA"],
        "away_team": ["BUF"],
        "home_score": [24],
        "away_score": [17],
        "spread_line": [3.0],
        "total_line": [41.5],
    }
)


def fake_loader(seasons):
    return FRAME


def test_builds_canonical_game_ids():
    games = load_nfl_games([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert games[0].game_id == "nfl-2025-03-BUF-at-MIA"


def test_carries_final_scores():
    games = load_nfl_games([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert (games[0].home_score, games[0].away_score) == (24, 17)


def test_negates_nflverse_spread_to_home_perspective():
    # nflverse says home favored by 3 as +3.0; we store -3.0.
    result = load_nfl_closing_lines([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert result.lines[0].spread_home == -3.0


def test_closing_lines_are_tagged_as_such():
    result = load_nfl_closing_lines([2025], resolver=TeamResolver.default(), loader=fake_loader)
    assert result.lines[0].source == "nflverse"
    assert result.lines[0].book == "close"


def test_rows_without_a_spread_are_surfaced_not_silently_dropped():
    frame = FRAME.with_columns(pl.lit(None, dtype=pl.Float64).alias("spread_line"))
    result = load_nfl_closing_lines([2025], resolver=TeamResolver.default(), loader=lambda s: frame)
    # A missing line must never become 0.0 — that would read as a pick'em.
    assert result.lines == []
    assert len(result.skipped) == 1
    assert "nfl-2025-03-BUF-at-MIA" in result.skipped[0]


def test_usable_rows_survive_alongside_skipped_ones():
    second = FRAME.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("spread_line"),
        pl.lit("KC").alias("home_team"),
        pl.lit("LV").alias("away_team"),
    )
    frame = pl.concat([FRAME, second])
    result = load_nfl_closing_lines([2025], resolver=TeamResolver.default(), loader=lambda s: frame)
    assert [line.game_id for line in result.lines] == ["nfl-2025-03-BUF-at-MIA"]
    assert len(result.skipped) == 1
    assert "LV-at-KC" in result.skipped[0]


UNKNOWN_FRAME = FRAME.with_columns(pl.lit("ZZZ").alias("home_team"))


def unknown_loader(seasons):
    return UNKNOWN_FRAME


def test_unknown_team_propagates_out_of_the_game_loader():
    # "Degrade visibly": a spelling we do not know must stop the load, never
    # resolve to a guess. Regression-proofs the manual 1999-2025 sweep.
    with pytest.raises(UnknownTeamError):
        load_nfl_games([2025], resolver=TeamResolver.default(), loader=unknown_loader)


def test_unknown_team_propagates_out_of_the_closing_line_loader():
    with pytest.raises(UnknownTeamError):
        load_nfl_closing_lines([2025], resolver=TeamResolver.default(), loader=unknown_loader)


def _frame(**overrides):
    """FRAME with named columns replaced, for kickoff-timing cases."""
    frame = FRAME
    for name, value in overrides.items():
        dtype = pl.Utf8 if isinstance(value, str) or value is None else None
        frame = frame.with_columns(pl.lit(value, dtype=dtype).alias(name))
    return frame


def test_kickoff_uses_gametime_in_eastern():
    frame = _frame(gameday="2024-09-22", gametime="13:00")
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda s: frame)
    # 13:00 EDT is 17:00 UTC.
    assert games[0].kickoff_utc == datetime(2024, 9, 22, 17, 0, tzinfo=UTC)


def test_kickoff_handles_standard_time():
    frame = _frame(gameday="2025-01-05", gametime="13:00")
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda s: frame)
    # 13:00 EST is 18:00 UTC — the offset comes from the date, not a constant.
    assert games[0].kickoff_utc == datetime(2025, 1, 5, 18, 0, tzinfo=UTC)


def test_kickoff_crosses_into_the_next_utc_day():
    frame = _frame(gameday="2024-09-23", gametime="20:15")
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda s: frame)
    # A Monday night game is already Tuesday in UTC.
    assert games[0].kickoff_utc == datetime(2024, 9, 24, 0, 15, tzinfo=UTC)


def test_london_kickoff_is_eastern_not_venue_local():
    frame = _frame(gameday="2024-10-06", gametime="09:30")
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda s: frame)
    # nflverse states international kickoffs in ET: 09:30 ET, 14:30 in London.
    assert games[0].kickoff_utc == datetime(2024, 10, 6, 13, 30, tzinfo=UTC)


def test_missing_gametime_falls_back_to_midnight_utc():
    frame = _frame(gameday="2024-09-22", gametime=None)
    games = load_nfl_games([2024], resolver=TeamResolver.default(), loader=lambda s: frame)
    # Poor, but legible — and one malformed row cannot abort a season load.
    assert games[0].kickoff_utc == datetime(2024, 9, 22, 0, 0, tzinfo=UTC)


def test_closing_line_captured_at_stays_date_only():
    frame = _frame(gameday="2024-09-22", gametime="13:00")
    result = load_nfl_closing_lines([2024], resolver=TeamResolver.default(), loader=lambda s: frame)
    # captured_at is part of the lines primary key and these rows are already
    # stored. Correcting it would append a near-duplicate of every one of them
    # to an append-only table. See the plan's Task 1 warning.
    assert result.lines[0].captured_at == datetime(2024, 9, 22, 0, 0, tzinfo=UTC)
