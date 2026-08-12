"""The Odds API client for live market spreads.

Quota exhaustion is a distinct exception so the CLI can fall back to the most
recent cached snapshot and stamp the report with its age, while a genuine bug
still fails loudly.

A bookmaker with no `spreads` market, or an outcome with no `point`, is never
silently dropped: `fetch_spreads` returns a `MarketLinesResult` and records a
one-liner in `skipped` naming the game and book, mirroring
`ingest.cbs.ParseResult`.

The endpoint returns every event with posted odds, which spans more than one
week. `fetch_spreads` therefore requires the caller to name the week's slate of
game ids and reports any event outside it in `skipped`. Without that, an event
from a later week would be stamped with the requested week's number and land
permanently in the append-only `lines` table under an id no report will ever
join on.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from datetime import datetime

import httpx

from pickem.models import MarketLine, MarketLinesResult, Sport, make_game_id
from pickem.resolve.resolver import TeamResolver

BASE_URL = "https://api.the-odds-api.com/v4"
NFL_KEY = "americanfootball_nfl"
CFB_KEY = "americanfootball_ncaaf"

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.5


class OddsApiError(RuntimeError):
    """The odds feed could not be read."""


class QuotaExhausted(OddsApiError):
    """The API key is out of credits or unauthorized."""


class OddsClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = httpx.Client(base_url=BASE_URL, transport=transport, timeout=20.0)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OddsClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        """GET with bounded retry on transport errors and 5xx.

        A flaky network is retried; a 4xx is the server telling us something
        true about the request, so it is never retried. After the last attempt
        the failure is raised, not swallowed.
        """
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code < 500:
                    return response
                last_error = OddsApiError(
                    f"odds api returned {response.status_code}: {response.text}"
                )
            if attempt < MAX_ATTEMPTS - 1:
                self._sleep(BACKOFF_SECONDS * (2**attempt))
        raise OddsApiError(f"odds api unreachable after {MAX_ATTEMPTS} attempts: {last_error}")

    def fetch_spreads(
        self,
        sport_key: str,
        *,
        resolver: TeamResolver,
        sport: Sport,
        season: int,
        week: int,
        now: datetime,
        slate: Collection[str],
    ) -> MarketLinesResult:
        """Fetch current spreads for the games named in `slate`.

        `slate` is the set of canonical game ids for the target week — normally
        the ids already ingested from the CBS sheet. Events outside it belong to
        another week and are reported, never stamped with this week's number.
        """
        response = self._get(
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

        wanted = set(slate)
        lines: list[MarketLine] = []
        skipped: list[str] = []
        for event in response.json():
            home_name = event["home_team"]
            home = resolver.resolve(home_name, sport)
            away = resolver.resolve(event["away_team"], sport)
            game_id = make_game_id(sport, season, week, away, home)

            if game_id not in wanted:
                skipped.append(
                    f"{game_id}: not in the {sport.value} {season} week {week} slate "
                    f"(commence_time {event.get('commence_time')}) — not stored"
                )
                continue

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
