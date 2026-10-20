"""SportsGameOdds client for live market spreads.

The free plan bills one object per event returned, 2,500 per rolling 30 days
for the whole account, and props pulls on the same account. Every request
therefore names the teams it wants: a time-range-only query is billed for every
college game in that slot, most of which we never pick.

Each event is converted to the shape The Odds API feed uses and parsed by the
same `_parse_events`, so the kickoff window, slate, unknown-team and
neutral-site guards are the code the rest of the system was built on.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from loguru import logger

from pickem.ingest.odds import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    KeyRejected,
    OddsApiError,
    QuotaExhausted,
    _log_poll,
    _parse_events,
    _skip,
)
from pickem.models import SGO_SOURCE, Game, MarketLinesResult, Sport
from pickem.resolve.resolver import TeamResolver

BASE_URL = "https://api.sportsgameodds.com/v2"
HOME_SPREAD_ODD = "points-home-game-sp-home"
# SportsGameOdds' start time and the board's can differ, by minutes or by a TV
# slot. Padding is free: the team filter bills only our games, and a team plays
# once a week, so a wider range cannot return a second event for it.
RANGE_PADDING = timedelta(hours=6)
PAGE_LIMIT = 100

LEAGUES = {Sport.NFL: "NFL", Sport.CFB: "NCAAF"}
# Where SportsGameOdds' id is not the team's display name run through the rule
# in `sgo_team_id`. Add a row when a pull lists "no SportsGameOdds lines" for a
# game the books are pricing.
TEAM_ID_OVERRIDES: dict[tuple[Sport, str], str] = {
    (Sport.CFB, "MIAFL"): "MIAMI_NCAAF",
}


def sgo_team_id(team_id: str, sport: Sport, resolver: TeamResolver) -> str:
    """SportsGameOdds' id for one of our teams, e.g. ``KANSAS_CITY_CHIEFS_NFL``.

    The team's display name (its first spelling in aliases.yaml), folded to
    ASCII with apostrophes dropped, every other run of non-alphanumerics made
    one underscore, uppercased, plus the league. Checked against every college
    id in props' saved pulls (tests/fixtures/sgo_fbs_team_ids.json).
    """
    override = TEAM_ID_OVERRIDES.get((sport, team_id))
    if override is not None:
        return override
    name = resolver.display_name(team_id, sport)
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9]+", "_", folded.replace("'", "")).strip("_").upper()
    return f"{stem}_{LEAGUES[sport]}"


@dataclass(frozen=True)
class SgoPull:
    """What one pull parsed, and how many events SportsGameOdds billed for it."""

    result: MarketLinesResult
    events_returned: int


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _checked(response: httpx.Response) -> httpx.Response:
    status = response.status_code
    if status in (401, 403):
        raise KeyRejected(
            f"SportsGameOdds rejected the API key (HTTP {status}); "
            "check SPORTSGAMEODDS_API_KEY and the account"
        )
    if status == 429:
        raise QuotaExhausted(
            "SportsGameOdds refused the request (HTTP 429): rate or monthly limit reached"
        )
    if status >= 400:
        raise OddsApiError(f"SportsGameOdds returned HTTP {status}: {response.text[:200]}")
    return response


def _payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise OddsApiError("SportsGameOdds sent invalid JSON") from exc
    if not isinstance(payload, dict):
        raise OddsApiError("SportsGameOdds sent an unexpected response shape")
    return payload


def _point(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _as_feed_event(event: dict, skipped: list[str]) -> dict | None:
    """One SportsGameOdds event in The Odds API's shape, minus closed books."""
    try:
        home = event["teams"]["home"]["names"]["long"]
        away = event["teams"]["away"]["names"]["long"]
        starts = event["status"]["startsAt"]
    except (KeyError, TypeError):
        _skip(
            skipped,
            guard="sgo_event_shape",
            event_id=event.get("eventID"),
            message=(
                f"SportsGameOdds event {event.get('eventID')!r}: "
                "no teams or start time — not stored"
            ),
        )
        return None
    odds = event.get("odds")
    odd = odds.get(HOME_SPREAD_ODD) if isinstance(odds, dict) else None
    quotes = odd.get("byBookmaker") if isinstance(odd, dict) else None
    bookmakers = []
    for book, quote in (quotes if isinstance(quotes, dict) else {}).items():
        if not isinstance(quote, dict):
            continue
        if not quote.get("available"):
            _skip(
                skipped,
                guard="book_unavailable",
                book=book,
                message=f"{away} at {home}: {book} — not open for betting",
            )
            continue
        point = _point(quote.get("spread"))
        outcomes: list[dict] = [{"name": home, "point": point}]
        if point is not None:
            # Spreads are symmetric. The away side is what is read when the
            # board lists this neutral-site game the other way round.
            outcomes.append({"name": away, "point": 0.0 - point})
        bookmakers.append({"key": book, "markets": [{"key": "spreads", "outcomes": outcomes}]})
    return {"home_team": home, "away_team": away, "commence_time": starts, "bookmakers": bookmakers}


class SgoClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # The key travels in a header, never the URL, so a request line or an
        # exception message cannot carry it into a log.
        self._client = httpx.Client(
            base_url=BASE_URL,
            transport=transport,
            timeout=30.0,
            headers={"x-api-key": api_key},
        )
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SgoClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        """GET with bounded retry on transport errors and 5xx; a 4xx is final."""
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            logger.bind(
                event="odds_request",
                provider="sportsgameodds",
                path=path,
                attempt=attempt + 1,
                max_attempts=MAX_ATTEMPTS,
            ).debug(f"GET {path}")
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code < 500:
                    return _checked(response)
                last_error = OddsApiError(f"SportsGameOdds returned {response.status_code}")
            if attempt < MAX_ATTEMPTS - 1:
                backoff_seconds = BACKOFF_SECONDS * (2**attempt)
                logger.bind(
                    event="odds_retry",
                    provider="sportsgameodds",
                    path=path,
                    attempt=attempt + 1,
                    error_type=type(last_error).__name__,
                    backoff_seconds=backoff_seconds,
                ).warning(f"{path} failed; retrying")
                self._sleep(backoff_seconds)
        raise OddsApiError(
            f"SportsGameOdds unreachable after {MAX_ATTEMPTS} attempts: "
            f"{type(last_error).__name__}"
        )

    def fetch_spreads(
        self,
        *,
        resolver: TeamResolver,
        sport: Sport,
        season: int,
        week: int,
        now: datetime,
        slate: Collection[str],
        window: tuple[datetime, datetime],
        games: Sequence[Game],
    ) -> SgoPull:
        """Every open book's current spread for ``games``, through the shared guards.

        Only ``games`` are asked for, and so billed. A requested game that
        comes back without a line is listed in ``skipped`` and shows as
        NO_MARKET, like a game any feed has no line for.
        """
        if not games:
            return SgoPull(MarketLinesResult(lines=[], skipped=[]), 0)
        team_ids = {
            game.game_id: (
                sgo_team_id(game.away_team_id, sport, resolver),
                sgo_team_id(game.home_team_id, sport, resolver),
            )
            for game in games
        }
        kickoffs = [game.kickoff_utc for game in games]
        params = {
            "leagueID": LEAGUES[sport],
            "teamID": ",".join(sorted({tid for pair in team_ids.values() for tid in pair})),
            "oddID": HOME_SPREAD_ODD,
            "startsAfter": _iso(min(kickoffs) - RANGE_PADDING),
            "startsBefore": _iso(max(kickoffs) + RANGE_PADDING),
            "limit": str(PAGE_LIMIT),
        }
        events: list[dict] = []
        cursor: str | None = None
        while True:
            page = {**params, "cursor": cursor} if cursor else params
            payload = _payload(self._get("/events", page))
            data = payload.get("data")
            if not isinstance(data, list):
                raise OddsApiError("SportsGameOdds /events sent no list of events")
            events.extend(event for event in data if isinstance(event, dict))
            cursor = payload.get("nextCursor")
            if not cursor:
                break

        skipped: list[str] = []
        feed = [
            converted
            for event in events
            if (converted := _as_feed_event(event, skipped)) is not None
        ]
        parsed = _parse_events(
            feed,
            resolver=resolver,
            sport=sport,
            season=season,
            week=week,
            slate=slate,
            window=window,
            captured_at=now,
            source=SGO_SOURCE,
        )
        priced = {line.game_id for line in parsed.lines}
        for game in games:
            if game.game_id not in priced:
                away_id, home_id = team_ids[game.game_id]
                _skip(
                    skipped,
                    guard="no_sgo_lines",
                    game_id=game.game_id,
                    message=(
                        f"{game.game_id}: no SportsGameOdds lines "
                        f"(team ids {away_id}, {home_id}) — shows as NO_MARKET"
                    ),
                )
        result = MarketLinesResult(lines=parsed.lines, skipped=[*parsed.skipped, *skipped])
        _log_poll(result, sport=sport, season=season, week=week, source=SGO_SOURCE)
        return SgoPull(result, len(events))

    def account_usage(self) -> dict | None:
        """The account's monthly block, props included, or None if unreadable."""
        try:
            usage = _payload(self._get("/account/usage", {}))["data"]["rateLimits"]["per-month"]
        except (OddsApiError, KeyError, TypeError):
            return None
        return usage if isinstance(usage, dict) else None
