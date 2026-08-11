"""The Odds API client for live market spreads.

Quota exhaustion is a distinct exception so the CLI can fall back to the most
recent cached snapshot and stamp the report with its age, while a genuine bug
still fails loudly.

A bookmaker with no `spreads` market, or an outcome with no `point`, is never
silently dropped: `fetch_spreads` returns a `MarketLinesResult` and records a
one-liner in `skipped` naming the game and book, mirroring
`ingest.cbs.ParseResult`.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from pickem.models import MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

BASE_URL = "https://api.the-odds-api.com/v4"
NFL_KEY = "americanfootball_nfl"
CFB_KEY = "americanfootball_ncaaf"


class OddsApiError(RuntimeError):
    """The odds feed could not be read."""


class QuotaExhausted(OddsApiError):
    """The API key is out of credits or unauthorized."""


class OddsClient:
    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._api_key = api_key
        self._client = httpx.Client(base_url=BASE_URL, transport=transport, timeout=20.0)

    def fetch_spreads(
        self,
        sport_key: str,
        *,
        resolver: TeamResolver,
        sport: Sport,
        season: int,
        week: int,
        now: datetime,
    ) -> MarketLinesResult:
        response = self._client.get(
            f"/sports/{sport_key}/odds",
            params={
                "apiKey": self._api_key,
                "regions": "us",
                "markets": "spreads",
                "oddsFormat": "american",
            },
        )
        if response.status_code in (401, 429):
            raise QuotaExhausted(f"odds api returned {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise OddsApiError(f"odds api returned {response.status_code}: {response.text}")

        lines: list[MarketLine] = []
        skipped: list[str] = []
        for event in response.json():
            home_name = event["home_team"]
            home = resolver.resolve(home_name, sport)
            away = resolver.resolve(event["away_team"], sport)
            game_id = make_game_id(sport, season, week, away, home)

            for book in event.get("bookmakers", []):
                book_key = book.get("key")
                spreads = next(
                    (
                        market
                        for market in book.get("markets", [])
                        if market.get("key") == "spreads"
                    ),
                    None,
                )
                if spreads is None:
                    skipped.append(f"{game_id}: {book_key} — no spreads market")
                    continue
                outcome = next(
                    (
                        outcome
                        for outcome in spreads.get("outcomes", [])
                        if outcome.get("name") == home_name
                    ),
                    None,
                )
                if outcome is None or outcome.get("point") is None:
                    skipped.append(f"{game_id}: {book_key} — no spread")
                    continue
                lines.append(
                    MarketLine(
                        game_id=game_id,
                        source="oddsapi",
                        book=book_key,
                        # Already home-perspective; do not flip.
                        spread_home=float(outcome["point"]),
                        captured_at=now,
                    )
                )
        return MarketLinesResult(lines=lines, skipped=skipped)
