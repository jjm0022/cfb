import polars as pl

from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.resolve.resolver import TeamResolver

FRAME = pl.DataFrame(
    {
        "season": [2025],
        "week": [3],
        "gameday": ["2025-09-21"],
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
    result = load_nfl_closing_lines(
        [2025], resolver=TeamResolver.default(), loader=lambda s: frame
    )
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
    result = load_nfl_closing_lines(
        [2025], resolver=TeamResolver.default(), loader=lambda s: frame
    )
    assert [line.game_id for line in result.lines] == ["nfl-2025-03-BUF-at-MIA"]
    assert len(result.skipped) == 1
    assert "LV-at-KC" in result.skipped[0]
