"""The Odds API client for live market spreads.

Quota exhaustion is a distinct exception so the CLI can fall back to the most
recent cached snapshot and stamp the report with its age, while a genuine bug
still fails loudly.

A bookmaker with no `spreads` market, or an outcome with no `point`, is never
silently dropped: `fetch_spreads` returns a `MarketLinesResult` and records a
one-liner in `skipped` naming the game and book, mirroring
`ingest.cbs.ParseResult`.

`fetch_spreads` reads the live feed and `fetch_historical_spreads` reads the
archive, which wraps its events in a snapshot envelope. Everything beneath that
— both guards, resolution, per-book extraction, skip reporting — is shared in
`_parse_events`. If the two paths ever parsed differently, the backtest would
stop being evidence about the code that ships.

The endpoint returns every event with posted odds, which spans more than one
week. Both methods therefore take two independent guards, and need both:

* a kickoff `window`, applied to each event's `commence_time` BEFORE the team
  names are resolved. Resolving first would abort the whole poll on the first
  unmapped school the NCAAF feed happens to return, and the window is also the
  only thing that can catch a repeat matchup played at the same site in a
  different week — its constructed id embeds the requested week, so an id-set
  check is blind to it.
* the week's `slate` of canonical game ids, applied after resolution.

Without them an out-of-week event would be stamped with the requested week's
number and land permanently in the append-only `lines` table under an id no
report will ever join on.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from datetime import UTC, datetime

import httpx

from pickem.models import (
    FROZEN_SOURCE,
    LIVE_SOURCE,
    SUBMISSION_SOURCE,
    MarketLine,
    MarketLinesResult,
    Sport,
)
from pickem.resolve.matchup import resolve_matchup
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

BASE_URL = "https://api.the-odds-api.com/v4"
NFL_KEY = "americanfootball_nfl"
CFB_KEY = "americanfootball_ncaaf"

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.5

__all__ = [
    "FROZEN_SOURCE",
    "LIVE_SOURCE",
    "SUBMISSION_SOURCE",
    "OddsApiError",
    "OddsClient",
    "QuotaExhausted",
]


def _api_time(value: datetime) -> str:
    """The Odds API wants second-precision ISO8601 with a literal Z."""
    return value.astimezone(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _commence_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_events(
    events: list[dict],
    *,
    resolver: TeamResolver,
    sport: Sport,
    season: int,
    week: int,
    slate: Collection[str],
    window: tuple[datetime, datetime],
    captured_at: datetime,
    source: str,
    snapshot_at: datetime | None = None,
) -> MarketLinesResult:
    """Both guards, team resolution and per-book extraction, in one place.

    Shared by the live and historical paths deliberately. If the two ever
    parsed differently, the backtest would stop being evidence about the code
    that ships.
    """
    window_start, window_end = window
    wanted = set(slate)
    lines: list[MarketLine] = []
    skipped: list[str] = []
    for event in events:
        home_name = event["home_team"]
        away_name = event["away_team"]

        # Before resolution, deliberately. The NCAAF feed covers the whole
        # country while aliases.yaml covers the schools we actually pick, so
        # resolving first would abort the poll on a school we never wanted.
        kickoff = _commence_time(event.get("commence_time"))
        if kickoff is None:
            skipped.append(
                f"{away_name} at {home_name}: unreadable commence_time "
                f"{event.get('commence_time')!r} — not stored"
            )
            continue
        if not window_start <= kickoff <= window_end:
            skipped.append(
                f"{away_name} at {home_name}: kickoff {kickoff.isoformat()} is outside "
                f"the {sport.value} {season} week {week} window — not stored"
            )
            continue

        # A name we cannot map is REPORTED here, not raised. This feed is a
        # firehose: its NCAAF coverage includes every FCS matchup with a
        # posted line, none of which this system picks, so an unknown name
        # is expected rather than exceptional. It cannot be in the slate
        # either — slate ids are built from names that already resolved.
        # The CBS paste keeps raising, because there every line IS a game we
        # must pick. Either way nothing is dropped in silence: poll-odds
        # prints these, and a game left without a market shows as NO_MARKET.
        try:
            matchup = resolve_matchup(
                resolver=resolver,
                sport=sport,
                season=season,
                week=week,
                away_name=away_name,
                home_name=home_name,
            )
        except UnknownTeamError as exc:
            skipped.append(f"{away_name} at {home_name}: not a team we track ({exc}) — not stored")
            continue
        game_id = matchup.game_id

        if game_id not in wanted:
            skipped.append(
                f"{game_id}: not in the {sport.value} {season} week {week} slate "
                f"(kickoff {kickoff.isoformat()}) — not stored"
            )
            continue

        for book in event.get("bookmakers", []):
            book_key = book.get("key")
            spreads = next(
                (market for market in book.get("markets", []) if market.get("key") == "spreads"),
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
                    source=source,
                    book=book_key,
                    # Already home-perspective; do not flip.
                    spread_home=float(outcome["point"]),
                    captured_at=captured_at,
                )
            )
    return MarketLinesResult(lines=lines, skipped=skipped, snapshot_at=snapshot_at)


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

    def remaining_credits(self) -> int:
        """Return the quota currently available to this API key."""
        response = self._get("/sports", params={"apiKey": self._api_key})
        if response.status_code in (401, 429):
            raise QuotaExhausted(f"odds api returned {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise OddsApiError(f"odds api returned {response.status_code}: {response.text}")
        raw = response.headers.get("x-requests-remaining")
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise OddsApiError(f"unreadable x-requests-remaining header {raw!r}") from exc

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
        window: tuple[datetime, datetime],
    ) -> MarketLinesResult:
        """Fetch current spreads for the games named in `slate`.

        `slate` is the set of canonical game ids for the target week — normally
        the ids already ingested from the CBS sheet. `window` is the kickoff
        range that week occupies. An event failing either guard is reported and
        never stamped with this week's number.
        """
        window_start, window_end = window
        response = self._get(
            f"/sports/{sport_key}/odds",
            params={
                "apiKey": self._api_key,
                "regions": "us",
                "markets": "spreads",
                "oddsFormat": "american",
                # Narrows the payload server-side; the client-side window check
                # below is still authoritative.
                "commenceTimeFrom": _api_time(window_start),
                "commenceTimeTo": _api_time(window_end),
            },
        )
        if response.status_code in (401, 429):
            raise QuotaExhausted(f"odds api returned {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise OddsApiError(f"odds api returned {response.status_code}: {response.text}")

        return _parse_events(
            response.json(),
            resolver=resolver,
            sport=sport,
            season=season,
            week=week,
            slate=slate,
            window=window,
            captured_at=now,
            source=LIVE_SOURCE,
        )

    def fetch_historical_spreads(
        self,
        sport_key: str,
        *,
        resolver: TeamResolver,
        sport: Sport,
        season: int,
        week: int,
        at: datetime,
        slate: Collection[str],
        window: tuple[datetime, datetime],
        source: str,
    ) -> MarketLinesResult:
        """One archived snapshot, taken at or earlier than `at`.

        Costs 10 credits per call — ten times a live request — so callers plan
        their requests before making them rather than discovering the bill.

        Both guards still apply: the archive is the same nationwide firehose as
        the live feed, and a backfill has no weekly sheet being read afterwards
        to make a wrong game id noticeable.
        """
        response = self._get(
            f"/historical/sports/{sport_key}/odds",
            params={
                "apiKey": self._api_key,
                "regions": "us",
                "markets": "spreads",
                "oddsFormat": "american",
                "date": _api_time(at),
            },
        )
        if response.status_code in (401, 429):
            raise QuotaExhausted(f"odds api returned {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise OddsApiError(f"odds api returned {response.status_code}: {response.text}")

        envelope = response.json()
        # The snapshot's own timestamp, never `at`: the archive answers with the
        # closest snapshot at or earlier, so stamping the requested instant
        # would misdate the row and make a re-run append near-duplicates to an
        # append-only table. An unreadable one raises rather than guessing.
        captured_at = _commence_time(envelope.get("timestamp"))
        if captured_at is None:
            raise OddsApiError(
                f"unreadable snapshot timestamp {envelope.get('timestamp')!r} — nothing stored"
            )

        return _parse_events(
            envelope.get("data") or [],
            resolver=resolver,
            sport=sport,
            season=season,
            week=week,
            slate=slate,
            window=window,
            captured_at=captured_at,
            source=source,
            snapshot_at=captured_at,
        )
