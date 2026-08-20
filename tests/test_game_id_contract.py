"""Every source must produce the SAME id for the same matchup.

`make_game_id` is the only join key between CBS, nflverse, CFBD and the odds
feed. If two adapters disagree by a single character the join silently returns
nothing and the week comes back NO_MARKET with no error anywhere.
"""

from datetime import UTC, datetime, timedelta

import httpx
import polars as pl

from pickem.ingest.cbs import parse_cbs_block
from pickem.ingest.cfbd_source import load_cfb_lines
from pickem.ingest.nflverse import load_nfl_closing_lines, load_nfl_games
from pickem.ingest.odds import NFL_KEY, OddsClient
from pickem.models import Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

NOW = datetime(2025, 9, 21, 12, 0, tzinfo=UTC)
EXPECTED = "nfl-2025-03-BUF-at-MIA"


def resolver():
    return TeamResolver.default()


def test_cbs_nflverse_and_the_odds_feed_agree_on_one_id():
    cbs = parse_cbs_block(
        "Buffalo Bills at Miami Dolphins -3.0\n",
        resolver=resolver(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        posted_at=NOW,
    )

    frame = pl.DataFrame(
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
    games = load_nfl_games([2025], resolver=resolver(), loader=lambda _s: frame)
    closers = load_nfl_closing_lines([2025], resolver=resolver(), loader=lambda _s: frame)

    payload = [
        {
            "id": "abc",
            "commence_time": "2025-09-21T17:00:00Z",
            "home_team": "Miami Dolphins",
            "away_team": "Buffalo Bills",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "markets": [
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Miami Dolphins", "point": -6.0},
                                {"name": "Buffalo Bills", "point": 6.0},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    client = OddsClient(
        "key",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)),
        sleep=lambda _s: None,
    )
    odds = client.fetch_spreads(
        NFL_KEY,
        resolver=resolver(),
        sport=Sport.NFL,
        season=2025,
        week=3,
        now=NOW,
        slate={EXPECTED},
        window=(NOW - timedelta(hours=12), NOW + timedelta(days=7)),
    )

    produced = {
        cbs.games[0].league_line.game_id,
        games[0].game_id,
        closers.lines[0].game_id,
        odds.lines[0].game_id,
        make_game_id(Sport.NFL, 2025, 3, "BUF", "MIA"),
    }
    assert produced == {EXPECTED}


def test_the_cfb_adapter_uses_the_same_id_construction():
    result = load_cfb_lines(
        2025,
        2,
        resolver=resolver(),
        fetcher=lambda _s, _w: [
            {
                "home_team": "Georgia",
                "away_team": "Alabama",
                "lines": [{"provider": "consensus", "spread": -7.0}],
            }
        ],
        captured_at=NOW,
    )
    assert result.lines[0].game_id == make_game_id(Sport.CFB, 2025, 2, "BAMA", "UGA")
