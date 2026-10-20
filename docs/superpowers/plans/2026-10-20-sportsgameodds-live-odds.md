# Live Odds from SportsGameOdds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pick'em gets its live market spreads from SportsGameOdds (SGO) instead of the deactivated Odds API key, within an 800-games-per-30-days share of the SGO free plan.

**Architecture:** A new SGO adapter (`ingest/sgo.py`) asks only for our games, by team id, and converts each SGO event into the Odds API feed shape so the existing `_parse_events` guards run unchanged. The poll path (`operations/recommendations.py`) requests only unstarted games (only a kickoff poll's own kickoffs for a kickoff poll), checks a per-project budget (`operations/odds_budget.py`, backed by a new append-only `odds_pulls` table) and stops polling Pinnacle. The bot passes each kickoff poll's kickoffs and pulls odds in its daily refresh only on Thursdays.

**Tech Stack:** Python 3.12, httpx, DuckDB, pydantic 2, loguru, typer, discord.py + APScheduler, pytest, ruff. Run everything with `uv run`.

**Spec:** `docs/superpowers/specs/2026-10-20-sportsgameodds-live-odds-design.md`

**Worktree:** `/home/jmiller/cfb/.worktrees/sgo-live-odds`, branch `feature/sgo-live-odds`. Run every command from there.

## Global Constraints

- Every spread is home-perspective: home favored by 3 is `-3.0`. SGO's `points-home-game-sp-home` spread is already home-perspective; never flip it.
- `lines` and `odds_pulls` are append-only: insert only, never update or delete.
- The SGO key travels only in the `x-api-key` header, never in a URL, log record or exception message. Env var: `SPORTSGAMEODDS_API_KEY`.
- Stored source label for SGO rows: `sportsgameodds`.
- Pick'em's share: `GAMES_PER_30_DAYS = 800`; protected kickoff offsets: `(2.0, 1.0)`.
- Daily refresh pulls odds only on `odds_days: [thu]`.
- No change to the consensus (median of each book's latest spread), the tiers, or the backtest.
- Checks: `uv run pytest -q` and `uv run ruff check .` (line length 100).
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH
  ```

## Review Focus

1. Two kickoff slots that share one poll instant (e.g. 12h before a 1 PM game and 1h before a 2 AM game) must fetch both slots' games — Task 6 (planner) and Task 7 (bot passes them through).
2. Kickoffs read back from DuckDB carry pytz UTC, the planner's carry `datetime.UTC`; the kickoff filter must still match them — Task 5, `test_a_kickoff_poll_asks_only_for_its_own_kickoffs` seeds through the database.
3. A manual `/refresh` on a Sunday must not request games already played or in progress — Task 5, `test_a_poll_asks_only_for_games_not_yet_started`.
4. A game SGO has not priced yet (or a wrong team id) must show as NO_MARKET with a skip line naming the ids, while the pull still succeeds and is recorded — Task 2, `test_a_requested_game_with_no_event_is_listed`; Task 5, `test_every_pull_is_recorded_for_the_budget`.
5. The budget boundary: exactly 800 is allowed, 801 is skipped — Task 4, `test_a_pull_that_fits_returns_the_games_already_used` and `test_a_pull_that_would_pass_the_share_is_skipped_and_recorded`.

---

### Task 1: SGO team ids and test fixtures

**Files:**
- Create: `src/pickem/ingest/sgo.py`
- Create: `tests/fixtures/sgo_ncaaf_events.json`, `tests/fixtures/sgo_fbs_team_ids.json` (generated)
- Test: `tests/test_sgo_adapter.py`

**Interfaces:**
- Produces: `sgo_team_id(team_id: str, sport: Sport, resolver: TeamResolver) -> str`; `TEAM_ID_OVERRIDES: dict[tuple[Sport, str], str]`; `LEAGUES: dict[Sport, str]`.

- [ ] **Step 1: Generate the fixtures from props' saved SGO pulls**

Run:

```bash
uv run python - <<'EOF'
import gzip, json, pathlib
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver, UnknownTeamError

src = pathlib.Path("/home/jmiller/props/data/evidence/sgo-shadow")
# Ohio State @ Indiana (away favourite), Mississippi State @ LSU (one book
# closed), New Mexico @ Hawai'i (apostrophe in the name).
keep = ["u8B8HcVMTXZiiYUKoPV9", "deObBbVgy71qE1pEiaUV", "QWeePByjZAKzscg6sanq"]
spread_odds = ("points-home-game-sp-home", "points-away-game-sp-away")
resolver = TeamResolver.default()
events, teams = {}, {}
for path in sorted(src.glob("*.json.gz")):
    for raw in json.loads(gzip.decompress(path.read_bytes()))["raw_documents"]:
        for event in json.loads(raw)["data"]:
            for side in ("home", "away"):
                team = event["teams"][side]
                try:
                    teams[resolver.resolve(team["names"]["long"], Sport.CFB)] = team["teamID"]
                except UnknownTeamError:
                    pass
            if event["eventID"] in keep and event["eventID"] not in events:
                events[event["eventID"]] = {
                    "eventID": event["eventID"],
                    "leagueID": event["leagueID"],
                    "teams": {
                        s: {"teamID": event["teams"][s]["teamID"],
                            "names": event["teams"][s]["names"]}
                        for s in ("home", "away")
                    },
                    "status": {"startsAt": event["status"]["startsAt"]},
                    "odds": {k: v for k, v in event["odds"].items() if k in spread_odds},
                }
fixtures = pathlib.Path("tests/fixtures")
(fixtures / "sgo_ncaaf_events.json").write_text(
    json.dumps({"success": True, "data": [events[i] for i in keep]}, indent=1) + "\n"
)
(fixtures / "sgo_fbs_team_ids.json").write_text(
    json.dumps(dict(sorted(teams.items())), indent=1) + "\n"
)
print(len(events), "events;", len(teams), "team ids")
EOF
```

Expected: `3 events; 82 team ids`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_sgo_adapter.py`:

```python
import json
from pathlib import Path

from pickem.ingest.sgo import TEAM_ID_OVERRIDES, sgo_team_id
from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

FIXTURES = Path(__file__).parent / "fixtures"
RESOLVER = TeamResolver.default()


def test_every_college_team_id_props_has_seen_is_reproduced():
    seen = json.loads((FIXTURES / "sgo_fbs_team_ids.json").read_text())

    built = {team: sgo_team_id(team, Sport.CFB, RESOLVER) for team in seen}

    assert built == seen


def test_apostrophes_are_dropped_not_turned_into_underscores():
    assert sgo_team_id("HAW", Sport.CFB, RESOLVER) == "HAWAII_NCAAF"


def test_miami_fl_uses_its_override():
    assert TEAM_ID_OVERRIDES[(Sport.CFB, "MIAFL")] == "MIAMI_NCAAF"
    assert sgo_team_id("MIAFL", Sport.CFB, RESOLVER) == "MIAMI_NCAAF"


def test_nfl_ids_are_the_full_team_name():
    assert sgo_team_id("KC", Sport.NFL, RESOLVER) == "KANSAS_CITY_CHIEFS_NFL"
    assert sgo_team_id("SF", Sport.NFL, RESOLVER) == "SAN_FRANCISCO_49ERS_NFL"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sgo_adapter.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pickem.ingest.sgo'`

- [ ] **Step 4: Write the module with the team-id rule**

Create `src/pickem/ingest/sgo.py`:

```python
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
import unicodedata

from pickem.models import Sport
from pickem.resolve.resolver import TeamResolver

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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sgo_adapter.py -q`
Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add src/pickem/ingest/sgo.py tests/test_sgo_adapter.py tests/fixtures/sgo_ncaaf_events.json tests/fixtures/sgo_fbs_team_ids.json
git commit -m "feat: build SportsGameOdds team ids from our team names

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 2: The SGO client

**Files:**
- Modify: `src/pickem/ingest/sgo.py`
- Modify: `src/pickem/ingest/odds.py` (add `KeyRejected` after `QuotaExhausted`, and to `__all__`)
- Modify: `src/pickem/models.py:75-81` (add `SGO_SOURCE`, `LIVE_SOURCES`)
- Test: `tests/test_sgo_adapter.py`

**Interfaces:**
- Consumes: `sgo_team_id`, `LEAGUES` (Task 1); `_parse_events`, `_skip`, `_log_poll`, `MAX_ATTEMPTS`, `BACKOFF_SECONDS`, `OddsApiError`, `QuotaExhausted` from `pickem.ingest.odds`.
- Produces:
  - `pickem.models.SGO_SOURCE = "sportsgameodds"`, `pickem.models.LIVE_SOURCES: frozenset[str]` = `{"oddsapi", "sportsgameodds"}`
  - `pickem.ingest.odds.KeyRejected(OddsApiError)`
  - `SgoPull(result: MarketLinesResult, events_returned: int)` (frozen dataclass)
  - `SgoClient(api_key: str, *, transport=None, sleep=time.sleep)`, a context manager with
    `fetch_spreads(*, resolver, sport, season, week, now, slate, window, games: Sequence[Game]) -> SgoPull`
    and `account_usage() -> dict | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sgo_adapter.py` (merge the imports into the file's import block):

```python
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from pickem.ingest.odds import KeyRejected, OddsApiError, QuotaExhausted
from pickem.ingest.sgo import SgoClient
from pickem.models import SGO_SOURCE, Game, make_game_id

NOW = datetime(2026, 10, 17, 14, 30, tzinfo=UTC)
WINDOW = (NOW, NOW + timedelta(days=1))
KEY = "sgo-test-key-123"


def _game(away: str, home: str, kickoff: str) -> Game:
    return Game(
        game_id=make_game_id(Sport.CFB, 2026, 7, away, home),
        sport=Sport.CFB,
        season=2026,
        week=7,
        kickoff_utc=datetime.fromisoformat(kickoff),
        home_team_id=home,
        away_team_id=away,
    )


OSU_AT_IU = _game("OSU", "IU", "2026-10-17T16:00:00+00:00")
MSST_AT_LSU = _game("MSST", "LSU", "2026-10-17T16:00:00+00:00")
UNM_AT_HAW = _game("UNM", "HAW", "2026-10-18T02:30:00+00:00")
GAMES = [OSU_AT_IU, MSST_AT_LSU, UNM_AT_HAW]


def _payload() -> dict:
    return json.loads((FIXTURES / "sgo_ncaaf_events.json").read_text())


def _client(handler) -> SgoClient:
    return SgoClient(KEY, transport=httpx.MockTransport(handler), sleep=lambda _seconds: None)


def _serve(payload: dict):
    return lambda _request: httpx.Response(200, json=payload)


def _fetch(client: SgoClient, games=GAMES, slate=None):
    return client.fetch_spreads(
        resolver=RESOLVER,
        sport=Sport.CFB,
        season=2026,
        week=7,
        now=NOW,
        slate=slate if slate is not None else [game.game_id for game in games],
        window=WINDOW,
        games=games,
    )


def _by_game(pull) -> dict[str, dict[str, float]]:
    lines: dict[str, dict[str, float]] = {}
    for line in pull.result.lines:
        lines.setdefault(line.game_id, {})[line.book] = line.spread_home
    return lines


def test_the_request_names_only_our_teams_and_the_spread_market():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_payload())

    _fetch(_client(handler))

    (request,) = seen
    params = request.url.params
    assert request.url.path == "/v2/events"
    assert request.headers["x-api-key"] == KEY
    assert KEY not in str(request.url)
    assert params["leagueID"] == "NCAAF"
    assert set(params["teamID"].split(",")) == {
        "OHIO_STATE_NCAAF", "INDIANA_NCAAF", "MISSISSIPPI_STATE_NCAAF",
        "LSU_NCAAF", "NEW_MEXICO_NCAAF", "HAWAII_NCAAF",
    }
    assert params["oddID"] == "points-home-game-sp-home"
    assert params["startsAfter"] == "2026-10-17T15:30:00Z"
    assert params["startsBefore"] == "2026-10-18T03:00:00Z"


def test_home_spreads_are_stored_unflipped():
    pull = _fetch(_client(_serve(_payload())))

    lines = _by_game(pull)
    # Indiana (home) is getting points against Ohio State.
    assert lines[OSU_AT_IU.game_id]["draftkings"] == 3.0
    assert lines[OSU_AT_IU.game_id]["fanduel"] == 2.5
    assert lines[UNM_AT_HAW.game_id]["bovada"] == 15.0
    assert {line.source for line in pull.result.lines} == {SGO_SOURCE}
    assert {line.captured_at for line in pull.result.lines} == {NOW}
    assert pull.events_returned == 3


def test_a_book_not_open_for_betting_is_skipped_and_listed():
    pull = _fetch(_client(_serve(_payload())))

    assert "unibet" not in _by_game(pull)[MSST_AT_LSU.game_id]
    assert len(_by_game(pull)[MSST_AT_LSU.game_id]) == 6
    assert "Mississippi State at LSU: unibet — not open for betting" in pull.result.skipped


def test_a_requested_game_with_no_event_is_listed():
    psu_at_mich = _game("PSU", "MICH", "2026-10-17T19:30:00+00:00")

    pull = _fetch(_client(_serve(_payload())), games=[*GAMES, psu_at_mich])

    assert (
        f"{psu_at_mich.game_id}: no SportsGameOdds lines "
        "(team ids PENN_STATE_NCAAF, MICHIGAN_NCAAF) — shows as NO_MARKET"
    ) in pull.result.skipped
    assert len(_by_game(pull)) == 3


def test_an_event_off_the_slate_is_not_stored():
    pull = _fetch(
        _client(_serve(_payload())),
        games=[OSU_AT_IU, MSST_AT_LSU],
        slate=[OSU_AT_IU.game_id, MSST_AT_LSU.game_id],
    )

    assert UNM_AT_HAW.game_id not in _by_game(pull)
    assert any("not in the cfb 2026 week 7 slate" in line for line in pull.result.skipped)


def test_a_neutral_site_game_keeps_the_boards_home_side():
    # The board lists Ohio State at home; SportsGameOdds lists Indiana.
    flipped = _game("IU", "OSU", "2026-10-17T16:00:00+00:00")

    pull = _fetch(_client(_serve(_payload())), games=[flipped], slate=[flipped.game_id])

    assert _by_game(pull)[flipped.game_id]["draftkings"] == -3.0


def test_an_unknown_team_is_reported_not_raised():
    payload = _payload()
    payload["data"][0]["teams"]["home"]["names"]["long"] = "Bloomsburg Huskies"

    pull = _fetch(_client(_serve(payload)))

    assert OSU_AT_IU.game_id not in _by_game(pull)
    assert any("not a team we track" in line for line in pull.result.skipped)
    assert any(line.startswith(f"{OSU_AT_IU.game_id}: no SportsGameOdds lines")
               for line in pull.result.skipped)


def test_paging_follows_next_cursor():
    events = _payload()["data"]
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"data": events[:1], "nextCursor": "page-2"})
        return httpx.Response(200, json={"data": events[1:]})

    pull = _fetch(_client(handler))

    assert [request.url.params.get("cursor") for request in seen] == [None, "page-2"]
    assert pull.events_returned == 3
    assert len(_by_game(pull)) == 3


def test_no_games_makes_no_request():
    def handler(_request):
        raise AssertionError("no request expected")

    pull = _fetch(_client(handler), games=[])

    assert (pull.result.lines, pull.events_returned) == ([], 0)


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_says_so_without_the_key(status):
    with pytest.raises(KeyRejected, match="rejected the API key") as error:
        _fetch(_client(lambda _request: httpx.Response(status, json={"error": "no"})))
    assert KEY not in str(error.value)


def test_a_rate_or_monthly_limit_is_quota_exhausted():
    with pytest.raises(QuotaExhausted):
        _fetch(_client(lambda _request: httpx.Response(429, json={})))


def test_another_client_error_is_an_odds_api_error():
    with pytest.raises(OddsApiError) as error:
        _fetch(_client(lambda _request: httpx.Response(400, text="bad teamID")))
    assert not isinstance(error.value, (KeyRejected, QuotaExhausted))


def test_a_server_error_is_retried():
    responses = iter([httpx.Response(502), httpx.Response(200, json=_payload())])

    pull = _fetch(_client(lambda _request: next(responses)))

    assert pull.events_returned == 3


def test_a_server_that_stays_down_raises():
    with pytest.raises(OddsApiError, match="unreachable"):
        _fetch(_client(lambda _request: httpx.Response(503)))


def test_account_usage_reads_the_monthly_block():
    usage = {"max-entities": 2500, "current-entities": 128}

    def handler(request):
        assert request.url.path == "/v2/account/usage"
        return httpx.Response(200, json={"data": {"rateLimits": {"per-month": usage}}})

    assert _client(handler).account_usage() == usage


def test_unreadable_account_usage_is_none():
    assert _client(lambda _request: httpx.Response(500)).account_usage() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sgo_adapter.py -q`
Expected: FAIL with `ImportError: cannot import name 'KeyRejected'`

- [ ] **Step 3: Add the source labels and the error**

In `src/pickem/models.py`, after `PINNACLE_SOURCE = "oddsapi:pinnacle"`:

```python
# Live spreads from SportsGameOdds, since The Odds API key was deactivated on
# 2026-10-20. Every reader of "the live market" accepts both labels, so weeks
# polled before the switch still render.
SGO_SOURCE = "sportsgameodds"
LIVE_SOURCES = frozenset({LIVE_SOURCE, SGO_SOURCE})
```

In `src/pickem/ingest/odds.py`, after the `QuotaExhausted` class:

```python
class KeyRejected(OddsApiError):
    """The provider refused the key itself: cancelled, deactivated or mistyped.

    Distinct from QuotaExhausted so an alert says what actually needs fixing.
    """
```

and add `"KeyRejected",` to that module's `__all__`.

- [ ] **Step 4: Write the client**

In `src/pickem/ingest/sgo.py`, replace the import block with:

```python
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
# SportsGameOdds' start times and the board's can differ by a few minutes.
RANGE_PADDING = timedelta(minutes=30)
PAGE_LIMIT = 100
```

keep `LEAGUES`, `TEAM_ID_OVERRIDES` and `sgo_team_id` as they are, and append:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sgo_adapter.py tests/test_odds_adapter.py -q`
Expected: all pass. If the neutral-site test fails, check that `_as_feed_event` adds the away outcome.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/pickem/ingest tests/test_sgo_adapter.py
git add src/pickem/ingest/sgo.py src/pickem/ingest/odds.py src/pickem/models.py tests/test_sgo_adapter.py
git commit -m "feat: SportsGameOdds client that asks only for our games

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 3: Both providers count as the live market; key setting and redaction

**Files:**
- Modify: `src/pickem/report/results.py:144` and `:168` (`== LIVE_SOURCE` → `in LIVE_SOURCES`; fix the import)
- Modify: `src/pickem/operations/preflight.py:85` (same; fix the import)
- Modify: `src/pickem/config.py` (add `sgo_api_key` after `odds_api_key`)
- Modify: `src/pickem/obs/log.py:20` (add the env var to `_SECRET_ENV_VARS`)
- Test: `tests/test_live_sources.py` (create), `tests/test_preflight.py`

**Interfaces:**
- Consumes: `LIVE_SOURCES`, `SGO_SOURCE` (Task 2).
- Produces: `pickem.config.sgo_api_key() -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_sources.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from pickem import config
from pickem.backtest.runner import split_proxies
from pickem.models import LIVE_SOURCE, LIVE_SOURCES, SGO_SOURCE, MarketLine
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


def test_both_providers_are_the_live_market():
    assert LIVE_SOURCES == {LIVE_SOURCE, SGO_SOURCE}


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
```

Append to `tests/test_preflight.py`:

```python
def test_sportsgameodds_lines_count_as_live_coverage():
    dataset = _dataset().model_copy(
        update={
            "market_lines": [
                line.model_copy(update={"source": "sportsgameodds"})
                for line in _dataset().market_lines
            ]
        }
    )

    result = _evaluate(dataset)

    assert "missing live market coverage" not in result.games[0].reasons
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_live_sources.py tests/test_preflight.py -q`
Expected: FAIL — `closing_spread` returns `None`, `sgo_api_key` missing, preflight reports missing coverage.

- [ ] **Step 3: Implement**

`src/pickem/report/results.py`: in the `pickem.models` import replace `LIVE_SOURCE,` with `LIVE_SOURCES,`; then

```python
    before = [ln for ln in lines if ln.source in LIVE_SOURCES and ln.captured_at < kickoff]
```

and

```python
    live = [ln for ln in before if ln.source in LIVE_SOURCES]
```

Update `closing_spread`'s docstring to "Live-market consensus (either provider) from quotes captured before kickoff."

`src/pickem/operations/preflight.py`: import `LIVE_SOURCES` instead of `LIVE_SOURCE`, and

```python
        if line.source in LIVE_SOURCES:
```

`src/pickem/config.py`, after `odds_api_key`:

```python
def sgo_api_key() -> str:
    """SportsGameOdds key; the same account props uses, so the plan is shared."""
    return _required("SPORTSGAMEODDS_API_KEY")
```

`src/pickem/obs/log.py`:

```python
_SECRET_ENV_VARS = (
    "ODDS_API_KEY",
    "SPORTSGAMEODDS_API_KEY",
    "CFBD_API_KEY",
    "DISCORD_BOT_TOKEN",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_live_sources.py tests/test_preflight.py tests/test_results_report.py tests/test_this_week.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/report/results.py src/pickem/operations/preflight.py src/pickem/config.py src/pickem/obs/log.py tests/test_live_sources.py tests/test_preflight.py
git commit -m "feat: treat SportsGameOdds rows as the live market; add its key

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 4: Pick'em's 800-game share

**Files:**
- Modify: `src/pickem/models.py` (add `PollTrigger`, `OddsPull`)
- Modify: `src/pickem/store/schema.sql` (add `odds_pulls`)
- Modify: `src/pickem/store/db.py` (add `record_odds_pull`, `odds_games_used`, `odds_skips_since`; import `OddsPull`)
- Create: `src/pickem/operations/odds_budget.py`
- Test: `tests/test_odds_budget.py`

**Interfaces:**
- Produces:
  - `PollTrigger(StrEnum)`: `WEEK_START="week_start"`, `MIDWEEK="midweek"`, `KICKOFF="kickoff"`, `MANUAL="manual"`, `CLI="cli"`
  - `OddsPull(BaseModel)`: `pulled_at: datetime, sport: Sport, season: int, week: int, trigger: PollTrigger, offset_hours: float | None = None, games_requested: int, games_returned: int, skipped: bool = False`
  - `Store.record_odds_pull(pull: OddsPull) -> None`, `Store.odds_games_used(since: datetime) -> int`, `Store.odds_skips_since(since: datetime) -> int`
  - `odds_budget.GAMES_PER_30_DAYS = 800`, `PROTECTED_OFFSETS_HOURS = (2.0, 1.0)`, `WINDOW = timedelta(days=30)`
  - `odds_budget.OddsBudgetSkipped(Exception)` with `.notify: bool`
  - `odds_budget.check_budget(store, *, now, sport, season, week, trigger, offset_hours, games_requested) -> int` (games used in the last 30 days; raises `OddsBudgetSkipped` after recording the skip)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_odds_budget.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from pickem.models import OddsPull, PollTrigger, Sport
from pickem.operations.odds_budget import OddsBudgetSkipped, check_budget
from pickem.store.db import Store

NOW = datetime(2026, 10, 22, 23, 15, tzinfo=UTC)
MONTH_AGO = NOW - timedelta(days=30)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "budget.duckdb") as opened:
        opened.init_schema()
        yield opened


def _pulled(store, games: int, *, days_ago: float = 1.0, skipped: bool = False) -> None:
    store.record_odds_pull(
        OddsPull(
            pulled_at=NOW - timedelta(days=days_ago),
            sport=Sport.NFL,
            season=2026,
            week=7,
            trigger=PollTrigger.MIDWEEK,
            games_requested=games,
            games_returned=0 if skipped else games,
            skipped=skipped,
        )
    )


def _check(store, games: int, trigger=PollTrigger.MIDWEEK, offset=None) -> int:
    return check_budget(
        store,
        now=NOW,
        sport=Sport.NFL,
        season=2026,
        week=7,
        trigger=trigger,
        offset_hours=offset,
        games_requested=games,
    )


def test_a_pull_that_fits_returns_the_games_already_used(store):
    _pulled(store, 700)

    assert _check(store, 100) == 700  # exactly 800 is allowed


def test_a_pull_that_would_pass_the_share_is_skipped_and_recorded(store):
    _pulled(store, 700)

    with pytest.raises(OddsBudgetSkipped) as skip:
        _check(store, 101)

    assert skip.value.notify is True
    assert "700 of its 800" in str(skip.value)
    assert store.odds_skips_since(MONTH_AGO) == 1
    assert store.odds_games_used(MONTH_AGO) == 700


def test_the_two_hour_and_one_hour_kickoff_pulls_always_run(store):
    _pulled(store, 800)

    assert _check(store, 14, PollTrigger.KICKOFF, 2.0) == 800
    assert _check(store, 14, PollTrigger.KICKOFF, 1.0) == 800


def test_earlier_kickoff_pulls_are_not_protected(store):
    _pulled(store, 800)

    with pytest.raises(OddsBudgetSkipped):
        _check(store, 14, PollTrigger.KICKOFF, 6.0)


def test_the_owner_is_told_once_per_thirty_days(store):
    _pulled(store, 800)
    _pulled(store, 30, days_ago=3, skipped=True)

    with pytest.raises(OddsBudgetSkipped) as skip:
        _check(store, 5)

    assert skip.value.notify is False


def test_pulls_older_than_thirty_days_no_longer_count(store):
    _pulled(store, 790, days_ago=31)

    assert _check(store, 30) == 0


def test_a_skip_is_logged(store, records):
    _pulled(store, 800)

    with pytest.raises(OddsBudgetSkipped):
        _check(store, 5)

    assert any(r["extra"].get("event") == "odds_budget_skipped" for r in records)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_odds_budget.py -q`
Expected: FAIL with `ImportError: cannot import name 'OddsPull'`

- [ ] **Step 3: Add the models**

In `src/pickem/models.py`, after `MarketLinesResult`:

```python
class PollTrigger(StrEnum):
    """Why a live odds pull ran. The budget and the logs read it."""

    WEEK_START = "week_start"
    MIDWEEK = "midweek"
    KICKOFF = "kickoff"
    MANUAL = "manual"
    CLI = "cli"


class OddsPull(BaseModel):
    """One live odds pull, or one the budget skipped. Append-only."""

    pulled_at: datetime
    sport: Sport
    season: int
    week: int
    trigger: PollTrigger
    offset_hours: float | None = None
    games_requested: int
    games_returned: int
    skipped: bool = False
```

- [ ] **Step 4: Add the table and the store methods**

Append to `src/pickem/store/schema.sql`:

```sql
-- APPEND-ONLY. One row per live odds pull, or per pull the budget skipped.
-- SportsGameOdds bills per game returned and props shares the account, so
-- pick'em counts its own use here (operations/odds_budget.py).
CREATE TABLE IF NOT EXISTS odds_pulls (
    pulled_at        TIMESTAMPTZ NOT NULL,
    sport            VARCHAR NOT NULL,
    season           INTEGER NOT NULL,
    week             INTEGER NOT NULL,
    trigger          VARCHAR NOT NULL,
    offset_hours     DOUBLE,
    games_requested  INTEGER NOT NULL,
    games_returned   INTEGER NOT NULL,
    skipped          BOOLEAN NOT NULL
);
```

In `src/pickem/store/db.py`, add `OddsPull` to the `pickem.models` import and, after `append_market_lines`:

```python
    def record_odds_pull(self, pull: OddsPull) -> None:
        self._con.execute(
            "INSERT INTO odds_pulls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                pull.pulled_at,
                pull.sport.value,
                pull.season,
                pull.week,
                pull.trigger.value,
                pull.offset_hours,
                pull.games_requested,
                pull.games_returned,
                pull.skipped,
            ],
        )

    def odds_games_used(self, since: datetime) -> int:
        """Games SportsGameOdds billed pick'em for after ``since``."""
        (total,) = self._con.execute(
            "SELECT COALESCE(SUM(games_returned), 0) FROM odds_pulls WHERE pulled_at > ?",
            [since],
        ).fetchone()
        return int(total)

    def odds_skips_since(self, since: datetime) -> int:
        (count,) = self._con.execute(
            "SELECT COUNT(*) FROM odds_pulls WHERE skipped AND pulled_at > ?", [since]
        ).fetchone()
        return int(count)
```

- [ ] **Step 5: Write the budget module**

Create `src/pickem/operations/odds_budget.py`:

```python
"""Pick'em's share of the SportsGameOdds plan.

The free plan bills one object per game returned, 2,500 per rolling 30 days for
the whole account, and props pulls on the same account. Pick'em counts its own
pulls in `odds_pulls` (the account's counter includes props) and keeps to its
share. The 2-hour and 1-hour kickoff polls always run, because they set the
picks; at worst pick'em overshoots by part of their ~250 games a month.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger

from pickem.models import OddsPull, PollTrigger, Sport
from pickem.store.db import Store

GAMES_PER_30_DAYS = 800
PROTECTED_OFFSETS_HOURS = (2.0, 1.0)
WINDOW = timedelta(days=30)


class OddsBudgetSkipped(Exception):
    """A pull was not made, to keep pick'em inside its share.

    ``notify`` is true when no other skip was recorded in the 30 days before,
    so the owner hears about it once rather than at every skipped poll.
    """

    def __init__(self, message: str, *, notify: bool) -> None:
        super().__init__(message)
        self.notify = notify


def is_protected(trigger: PollTrigger, offset_hours: float | None) -> bool:
    return trigger is PollTrigger.KICKOFF and offset_hours in PROTECTED_OFFSETS_HOURS


def check_budget(
    store: Store,
    *,
    now: datetime,
    sport: Sport,
    season: int,
    week: int,
    trigger: PollTrigger,
    offset_hours: float | None,
    games_requested: int,
) -> int:
    """Return the games used in the last 30 days, or record a skip and raise."""
    used = store.odds_games_used(now - WINDOW)
    if used + games_requested <= GAMES_PER_30_DAYS or is_protected(trigger, offset_hours):
        return used
    notify = store.odds_skips_since(now - WINDOW) == 0
    store.record_odds_pull(
        OddsPull(
            pulled_at=now,
            sport=sport,
            season=season,
            week=week,
            trigger=trigger,
            offset_hours=offset_hours,
            games_requested=games_requested,
            games_returned=0,
            skipped=True,
        )
    )
    message = (
        f"Skipped a {trigger.value} odds pull for {sport.value} week {week}: pick'em has "
        f"used {used} of its {GAMES_PER_30_DAYS} SportsGameOdds games in the last 30 days "
        f"and this pull needed {games_requested}. The 2-hour and 1-hour pulls before "
        "each kickoff still run."
    )
    logger.bind(
        event="odds_budget_skipped",
        sport=sport.value,
        season=season,
        week=week,
        trigger=trigger.value,
        offset_hours=offset_hours,
        games_requested=games_requested,
        used_30d=used,
        share=GAMES_PER_30_DAYS,
        notify=notify,
    ).warning(message)
    raise OddsBudgetSkipped(message, notify=notify)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_odds_budget.py tests/test_store.py tests/test_models.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/pickem/models.py src/pickem/store/schema.sql src/pickem/store/db.py src/pickem/operations/odds_budget.py tests/test_odds_budget.py
git commit -m "feat: count pick'em's SportsGameOdds games and keep to 800 a month

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 5: The poll asks SportsGameOdds for only the games it needs

**Files:**
- Modify: `src/pickem/operations/recommendations.py`
- Test: `tests/test_recommendations.py`

**Interfaces:**
- Consumes: `SgoClient`, `SgoPull` (Task 2); `sgo_api_key` (Task 3); `PollTrigger`, `OddsPull`, `check_budget`, `OddsBudgetSkipped`, `GAMES_PER_30_DAYS` (Task 4).
- Produces:
  - `RecommendationSnapshot.odds_notice: str | None = None` (new last field)
  - `poll_odds_snapshot(db, sport, season, week, now, *, days=7, window_start=None, kickoffs=None, trigger=PollTrigger.CLI, offset_hours=None, client_factory=None) -> MarketLinesResult`; raises `OddsBudgetSkipped`
  - `refresh_recommendations(db, sport, season, week, now, *, window_start=None, pending_as_of=None, kickoffs=None, trigger=PollTrigger.MANUAL, offset_hours=None, pull_odds=True) -> RecommendationSnapshot`

- [ ] **Step 1: Replace the Odds API fakes in the tests**

In `tests/test_recommendations.py`:

1. Delete `test_refresh_recommendations_appends_live_snapshot_before_generating`, the `RecordingOddsClient` class, the `recording_client` fixture, `test_poll_looks_twelve_hours_back_by_default`, `test_poll_can_exclude_games_already_underway`, `test_refresh_passes_its_window_start_through_to_the_poll`, the `TwoRequestClient` class, the `two_request_client` fixture, `test_each_poll_also_records_pinnacle_under_its_own_source`, `test_a_failed_pinnacle_request_keeps_the_main_poll` and `test_a_failed_pinnacle_request_never_logs_the_api_key`. Keep `test_pinnacle_lines_never_move_the_models_market_spread` and the `_line` helper.
2. Remove `from pickem.ingest.odds import OddsApiError` if nothing else uses it.
3. Extend the imports:

```python
from pickem.ingest.sgo import SgoPull
from pickem.models import SGO_SOURCE, OddsPull, PollTrigger
```

4. Add after the `seeded_week` fixture:

```python
class FakeSgoClient:
    """Stands in for SportsGameOdds: records each request, returns canned lines."""

    calls: list[dict] = []
    lines: list[MarketLine] = []
    usage_reads = 0

    def __init__(self, api_key):
        assert api_key == "test-key"

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def fetch_spreads(self, **kwargs):
        FakeSgoClient.calls.append(kwargs)
        result = MarketLinesResult(lines=list(FakeSgoClient.lines), skipped=[])
        return SgoPull(result, len(kwargs["games"]))

    def account_usage(self):
        FakeSgoClient.usage_reads += 1
        return {"max-entities": 2500, "current-entities": 40}


@pytest.fixture
def sgo(monkeypatch):
    FakeSgoClient.calls, FakeSgoClient.lines, FakeSgoClient.usage_reads = [], [], 0
    monkeypatch.setenv("SPORTSGAMEODDS_API_KEY", "test-key")
    monkeypatch.setattr("pickem.operations.recommendations.SgoClient", FakeSgoClient)
    return FakeSgoClient


def test_refresh_appends_a_sportsgameodds_snapshot_before_generating(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    sgo.lines = [
        MarketLine(game_id="nfl:away:home", source=SGO_SOURCE, book="draftkings",
                   spread_home=-6.0, captured_at=now)
    ]

    snapshot = refresh_recommendations(db, Sport.NFL, 2026, 1, now)

    assert snapshot.edges[0].market_spread == -6.0
    assert snapshot.odds_notice is None
    with Store(db, read_only=True) as store:
        assert store.picks_for_week(2026, 1) == []
        assert len(store.load_week(Sport.NFL, 2026, 1).market_lines) == 1


def test_poll_looks_twelve_hours_back_by_default(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now)

    assert sgo.calls[0]["window"] == (now - timedelta(hours=12), now + timedelta(days=7))


def test_poll_can_exclude_games_already_underway(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now, window_start=now)

    assert sgo.calls[0]["window"] == (now, now + timedelta(days=7))


def test_refresh_passes_its_window_start_through_to_the_poll(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    refresh_recommendations(db, Sport.NFL, 2026, 1, now, window_start=now)

    assert sgo.calls[0]["window"][0] == now


def test_no_pinnacle_request_is_made(db, seeded_week, sgo):
    poll_odds_snapshot(db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC))

    assert len(sgo.calls) == 1
```

5. Add after `_seed_two_games`:

```python
def test_a_poll_asks_only_for_games_not_yet_started(db, sgo):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, datetime(2026, 9, 10, 18, tzinfo=UTC))

    assert [game.game_id for game in sgo.calls[0]["games"]] == ["nfl:late"]


def test_a_kickoff_poll_asks_only_for_its_own_kickoffs(db, sgo):
    """The kickoffs come from the planner as datetime.UTC; the games from DuckDB."""
    early = datetime(2026, 9, 13, 17, tzinfo=UTC)
    late = datetime(2026, 9, 14, 0, 20, tzinfo=UTC)
    _seed_two_games(db, early, late)

    poll_odds_snapshot(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 13, 12, tzinfo=UTC),
        kickoffs=(late,), trigger=PollTrigger.KICKOFF, offset_hours=12.0,
    )

    assert [game.game_id for game in sgo.calls[0]["games"]] == ["nfl:late"]


def test_a_poll_with_nothing_left_to_ask_for_makes_no_request(db, sgo):
    early = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)
    late = datetime(2026, 9, 13, 17, tzinfo=UTC)
    _seed_two_games(db, early, late)

    result = poll_odds_snapshot(db, Sport.NFL, 2026, 1, datetime(2026, 9, 14, tzinfo=UTC))

    assert sgo.calls == []
    assert result.lines == []
    with Store(db, read_only=True) as store:
        assert store.odds_games_used(datetime(2026, 1, 1, tzinfo=UTC)) == 0


def test_every_pull_is_recorded_for_the_budget(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)

    poll_odds_snapshot(db, Sport.NFL, 2026, 1, now, trigger=PollTrigger.MIDWEEK)

    with Store(db, read_only=True) as store:
        assert store.odds_games_used(now - timedelta(days=30)) == 1


def _spend(db, games: int, at: datetime) -> None:
    with Store(db) as store:
        store.record_odds_pull(
            OddsPull(pulled_at=at, sport=Sport.CFB, season=2026, week=1,
                     trigger=PollTrigger.MIDWEEK, games_requested=games, games_returned=games)
        )


def test_a_pull_over_the_share_is_skipped_and_the_refresh_still_decides(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    _spend(db, 800, now - timedelta(days=1))

    snapshot = refresh_recommendations(db, Sport.NFL, 2026, 1, now, trigger=PollTrigger.MIDWEEK)

    assert sgo.calls == []
    assert [edge.game_id for edge in snapshot.edges] == ["nfl:away:home"]
    assert "800 of its 800" in snapshot.odds_notice


def test_the_owner_is_not_told_twice_in_thirty_days(db, seeded_week, sgo):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    _spend(db, 800, now - timedelta(days=1))
    refresh_recommendations(db, Sport.NFL, 2026, 1, now, trigger=PollTrigger.MIDWEEK)

    later = refresh_recommendations(
        db, Sport.NFL, 2026, 1, now + timedelta(hours=1), trigger=PollTrigger.MIDWEEK
    )

    assert later.odds_notice is None


def test_a_refresh_without_odds_makes_no_request(db, seeded_week, sgo):
    snapshot = refresh_recommendations(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC), pull_odds=False
    )

    assert sgo.calls == []
    assert len(snapshot.edges) == 1


def test_the_week_start_pull_reads_the_account_usage(db, seeded_week, sgo, records):
    poll_odds_snapshot(
        db, Sport.NFL, 2026, 1, datetime(2026, 9, 2, tzinfo=UTC), trigger=PollTrigger.WEEK_START
    )

    assert sgo.usage_reads == 1
    assert any(r["extra"].get("event") == "odds_account_usage" for r in records)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_recommendations.py -q`
Expected: FAIL — `pickem.operations.recommendations` has no attribute `SgoClient`.

- [ ] **Step 3: Rewrite the poll path**

In `src/pickem/operations/recommendations.py`:

Replace the imports from `collections.abc` through `pickem.store.db` with:

```python
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from pickem import config
from pickem.edge.divergence import Thresholds, rank_edges
from pickem.edge.pipeline import decide_edges
from pickem.ingest.odds import KeyRejected, OddsApiError, QuotaExhausted
from pickem.ingest.sgo import SgoClient
from pickem.models import (
    PINNACLE_SOURCE,
    Edge,
    Game,
    MarketLinesResult,
    OddsPull,
    PollTrigger,
    Sport,
    Tier,
)
from pickem.operations.odds_budget import GAMES_PER_30_DAYS, OddsBudgetSkipped, check_budget
from pickem.resolve.resolver import TeamResolver, UnknownTeamError
from pickem.store.db import Store
```

Add the field to `RecommendationSnapshot`:

```python
@dataclass(frozen=True)
class RecommendationSnapshot:
    sport: Sport
    season: int
    week: int
    generated_at: datetime
    edges: tuple[Edge, ...]
    # Set when this refresh skipped its odds pull to stay inside pick'em's
    # SportsGameOdds share and no other skip was recorded in the 30 days before.
    odds_notice: str | None = None
```

In `generate_recommendations`, change the Pinnacle comment to `# Pinnacle rows (recorded 2026-09-25 to 2026-10-19) never enter the consensus.`

Replace `poll_odds_snapshot`, `_poll_pinnacle` and `refresh_recommendations` with:

```python
def _games_to_request(
    dataset,
    now: datetime,
    window_end: datetime,
    kickoffs: Collection[datetime] | None,
) -> list[Game]:
    """The slate games a pull should pay for.

    SportsGameOdds bills per game, so: only games not yet started (a started
    game would return in-play odds), inside the window, and for a kickoff
    poll only the games whose kickoff asked for it.
    """
    slate = {line.game_id for line in dataset.league_lines}
    wanted = None if kickoffs is None else set(kickoffs)
    return sorted(
        (
            game
            for game in dataset.games
            if game.game_id in slate
            and now < game.kickoff_utc <= window_end
            and (wanted is None or game.kickoff_utc in wanted)
        ),
        key=lambda game: (game.kickoff_utc, game.game_id),
    )


def poll_odds_snapshot(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    days: int = 7,
    window_start: datetime | None = None,
    kickoffs: Collection[datetime] | None = None,
    trigger: PollTrigger = PollTrigger.CLI,
    offset_hours: float | None = None,
    client_factory: Callable[..., SgoClient] | None = None,
) -> MarketLinesResult:
    """Fetch and append the live market snapshot for one ingested week.

    The slate and kickoff-window guards are kept here with the append-only
    write. ``window_start`` overrides the lower edge of the kickoff window;
    the default reaches twelve hours back.

    Only games not yet started are requested -- for a kickoff poll, only the
    games at ``kickoffs`` -- because SportsGameOdds bills per game. A pull
    that would take pick'em past its share raises ``OddsBudgetSkipped``
    instead of requesting anything.
    """
    if not db.exists():
        raise RecommendationDatabaseMissing(f"no database at {db}; run ingest-cbs first")

    with _open_store(db) as store:
        dataset = store.load_week(sport, season, week)
        slate = [line.game_id for line in dataset.league_lines]
        if not slate:
            raise RecommendationSlateMissing(
                f"no {sport.value} {season} week {week} games in the store; run ingest-cbs first"
            )
        window = (
            window_start if window_start is not None else now - timedelta(hours=12),
            now + timedelta(days=days),
        )
        games = _games_to_request(dataset, now, window[1], kickoffs)
        if not games:
            logger.bind(
                event="odds_poll_empty",
                sport=sport.value,
                season=season,
                week=week,
                trigger=trigger.value,
            ).info("no games left to poll; nothing requested")
            return MarketLinesResult(lines=[], skipped=[])
        used = check_budget(
            store,
            now=now,
            sport=sport,
            season=season,
            week=week,
            trigger=trigger,
            offset_hours=offset_hours,
            games_requested=len(games),
        )
        factory = client_factory or SgoClient
        with factory(config.sgo_api_key()) as client:
            pull = client.fetch_spreads(
                resolver=TeamResolver.default(),
                sport=sport,
                season=season,
                week=week,
                now=now,
                slate=slate,
                window=window,
                games=games,
            )
            store.append_market_lines(pull.result.lines)
            store.record_odds_pull(
                OddsPull(
                    pulled_at=now,
                    sport=sport,
                    season=season,
                    week=week,
                    trigger=trigger,
                    offset_hours=offset_hours,
                    games_requested=len(games),
                    games_returned=pull.events_returned,
                )
            )
            total = used + pull.events_returned
            logger.bind(
                event="odds_usage",
                trigger=trigger.value,
                games_requested=len(games),
                games_returned=pull.events_returned,
                used_30d=total,
                share=GAMES_PER_30_DAYS,
            ).info(
                f"pick'em has used {total} of its {GAMES_PER_30_DAYS} "
                "SportsGameOdds games in 30 days"
            )
            if trigger is PollTrigger.WEEK_START:
                usage = client.account_usage()
                logger.bind(event="odds_account_usage", usage=usage).info(
                    f"SportsGameOdds account usage, props included: {usage}"
                )
    return pull.result


def refresh_recommendations(
    db: Path,
    sport: Sport,
    season: int,
    week: int,
    now: datetime,
    *,
    window_start: datetime | None = None,
    pending_as_of: datetime | None = None,
    kickoffs: Collection[datetime] | None = None,
    trigger: PollTrigger = PollTrigger.MANUAL,
    offset_hours: float | None = None,
    pull_odds: bool = True,
) -> RecommendationSnapshot:
    """Append a current market snapshot (unless ``pull_odds`` is off), then decide.

    A pull the budget skips is not a failure: the picks are re-decided from
    the lines already stored, and the snapshot carries the notice when the
    owner should hear about it.
    """
    notice: str | None = None
    if pull_odds:
        try:
            poll_odds_snapshot(
                db,
                sport,
                season,
                week,
                now,
                window_start=window_start,
                kickoffs=kickoffs,
                trigger=trigger,
                offset_hours=offset_hours,
            )
        except OddsBudgetSkipped as skipped:
            notice = str(skipped) if skipped.notify else None
    snapshot = generate_recommendations(
        db, sport, season, week, now, pending_as_of=pending_as_of
    )
    return replace(snapshot, odds_notice=notice)
```

Add `"KeyRejected"` and `"OddsBudgetSkipped"` to `__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_recommendations.py tests/test_monitor.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/pickem/operations tests/test_recommendations.py
git add src/pickem/operations/recommendations.py tests/test_recommendations.py
git commit -m "feat: poll SportsGameOdds for unstarted games only, within budget; drop Pinnacle

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 6: Each kickoff poll knows which kickoffs it covers

**Files:**
- Modify: `src/pickem/automation/poll_plan.py`
- Test: `tests/test_poll_plan.py`

**Interfaces:**
- Produces: `PollInstant.kickoffs: tuple[datetime, ...]` (default `()`, excluded from equality) and `PollInstant.covered_kickoffs -> tuple[datetime, ...]` (the kickoffs, or `(kickoff_utc,)` when built by hand).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_poll_plan.py`:

```python
def test_each_instant_names_the_kickoffs_it_polls():
    kickoff = at(13, 17)

    plan = plan_polls([kickoff, kickoff], offsets_hours=OFFSETS, now=NOW)

    assert {instant.kickoffs for instant in plan} == {(kickoff,)}


def test_a_shared_instant_polls_both_kickoffs():
    # 12h before the 13:00 kickoff and 1h before the 02:00 kickoff are both 01:00.
    afternoon, overnight = at(14, 13), at(14, 2)

    plan = plan_polls([afternoon, overnight], offsets_hours=(12.0, 1.0), now=NOW)

    shared = next(instant for instant in plan if instant.at == at(14, 1))
    assert shared.kickoffs == (overnight, afternoon)


def test_an_instant_built_by_hand_covers_its_own_kickoff():
    instant = PollInstant(at=at(13, 16), offset_hours=1.0, kickoff_utc=at(13, 17))

    assert instant.covered_kickoffs == (at(13, 17),)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_poll_plan.py -q`
Expected: FAIL with `AttributeError: 'PollInstant' object has no attribute 'kickoffs'`

- [ ] **Step 3: Implement**

In `src/pickem/automation/poll_plan.py`:

Replace the module docstring's last paragraph ("Cost scales with the number of distinct *instants*...") with:

```
SportsGameOdds bills per game, so a poll fetches only the games whose kickoff
asked for it. Games sharing a kickoff share their polls, and an instant two
kickoff slots both ask for is one poll that covers both slots' games.
```

Change the imports and the dataclass:

```python
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta


@dataclass(frozen=True)
class PollInstant:
    """One scheduled market poll, and the kickoff that earned it."""

    at: datetime
    offset_hours: float
    kickoff_utc: datetime
    # Every kickoff that asked for this instant; the poll fetches only their
    # games. Left out of equality so plans still compare by when and why.
    kickoffs: tuple[datetime, ...] = field(default=(), compare=False)

    @property
    def covered_kickoffs(self) -> tuple[datetime, ...]:
        return self.kickoffs or (self.kickoff_utc,)
```

Replace the end of `plan_polls`, from `chosen: dict[...] = {}` to the `return`, with:

```python
    chosen: dict[datetime, PollInstant] = {}
    askers: dict[datetime, set[datetime]] = defaultdict(set)
    for instant in candidates:
        if instant.at > now:
            chosen.setdefault(instant.at, instant)
            askers[instant.at].add(instant.kickoff_utc)
    return tuple(
        sorted(
            (
                replace(instant, kickoffs=tuple(sorted(askers[instant.at])))
                for instant in chosen.values()
            ),
            key=lambda instant: instant.at,
        )
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_poll_plan.py -q`
Expected: all pass, including the existing equality-based tests.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/automation/poll_plan.py tests/test_poll_plan.py
git commit -m "feat: each kickoff poll carries the kickoffs it covers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 7: The bot pulls only what each moment needs

**Files:**
- Modify: `src/pickem/discord_bot.py` (settings, `_weekday`, `_scheduled_refresh`, `_refresh_scopes`, `_run_kickoff_poll`)
- Modify: `config/discord-bot.yaml`
- Test: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `PollTrigger` (Task 4); `PollInstant.covered_kickoffs` (Task 6); `refresh_recommendations(..., kickoffs, trigger, offset_hours, pull_odds)` and `RecommendationSnapshot.odds_notice` (Task 5).
- Produces: `DiscordSettings.odds_days: tuple[str, ...]`; module function `_weekday(timezone: ZoneInfo) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discord_bot.py` (add `from dataclasses import replace`, `from pickem.models import PollTrigger` and `from pickem.operations.recommendations import RecommendationSnapshot` to the imports if not already there):

```python
class KwargsMonitor:
    def __init__(self, snapshot=None):
        self.seen: list[dict] = []
        self.snapshot = snapshot

    async def refresh(self, **kwargs):
        self.seen.append(kwargs)
        return RefreshResult(changed=False, snapshot=self.snapshot)


@pytest.mark.asyncio
async def test_a_kickoff_poll_asks_only_for_its_own_kickoffs(settings):
    add_pick_scope(settings, Sport.NFL)
    monitor = KwargsMonitor()
    bot = PickemBot(settings, monitor, scheduler=FakeScheduler())
    kickoffs = (datetime(2026, 9, 13, 17, tzinfo=UTC), datetime(2026, 9, 13, 20, tzinfo=UTC))
    instant = replace(_instant(13, 16), kickoffs=kickoffs)

    await bot._run_kickoff_poll(NFL_SCOPE, instant)

    (kwargs,) = monitor.seen
    assert kwargs["kickoffs"] == kickoffs
    assert kwargs["trigger"] is PollTrigger.KICKOFF
    assert kwargs["offset_hours"] == 1.0


@pytest.mark.asyncio
async def test_the_daily_refresh_pulls_odds_only_on_odds_days(settings, monkeypatch):
    add_pick_scope(settings, Sport.NFL)
    monitor = KwargsMonitor()
    bot = PickemBot(replace(settings, odds_days=("thu",)), monitor, scheduler=FakeScheduler())

    monkeypatch.setattr("pickem.discord_bot._weekday", lambda _tz: "wed")
    await bot._scheduled_refresh()
    monkeypatch.setattr("pickem.discord_bot._weekday", lambda _tz: "thu")
    await bot._scheduled_refresh()

    assert [kwargs.get("pull_odds", True) for kwargs in monitor.seen] == [False, True]
    assert monitor.seen[1]["trigger"] is PollTrigger.MIDWEEK


@pytest.mark.asyncio
async def test_a_budget_notice_is_sent_to_the_owner(settings):
    add_pick_scope(settings, Sport.NFL)
    snapshot = RecommendationSnapshot(
        Sport.NFL, 2026, 1, datetime(2026, 9, 10, tzinfo=UTC), (),
        odds_notice="Skipped a midweek odds pull",
    )
    bot = PickemBot(settings, KwargsMonitor(snapshot), scheduler=FakeScheduler())
    sent: list[str | None] = []

    async def record(message=None, *, embed=None):
        sent.append(message)

    bot._send_owner_dm = record

    await bot._refresh_scopes()

    assert sent == ["Skipped a midweek odds pull"]


def test_settings_read_the_odds_days(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    config_path = tmp_path / "discord-bot.yaml"
    config_path.write_text(
        "database: pickem.duckdb\n"
        "timezone: America/New_York\n"
        "schedule:\n"
        "  refresh:\n"
        "    days: [wed, thu, fri]\n"
        "    time: '10:00'\n"
        "    odds_days: [thu]\n"
    )

    settings = DiscordSettings.from_env(config_path=config_path)

    assert settings.odds_days == ("thu",)


def test_odds_days_default_to_the_refresh_days(settings):
    assert settings.odds_days == ("wed", "thu", "fri", "sat", "sun", "mon")


def test_an_unknown_odds_day_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "123")
    config_path = tmp_path / "discord-bot.yaml"
    config_path.write_text(
        "database: pickem.duckdb\n"
        "timezone: America/New_York\n"
        "schedule:\n"
        "  refresh:\n"
        "    days: [wed]\n"
        "    time: '10:00'\n"
        "    odds_days: [thursday]\n"
    )

    with pytest.raises(RuntimeError, match="odds_days"):
        DiscordSettings.from_env(config_path=config_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_discord_bot.py -q -k "odds_days or own_kickoffs or budget_notice"`
Expected: FAIL — `DiscordSettings` has no `odds_days`.

- [ ] **Step 3: Implement**

In `src/pickem/discord_bot.py`:

Import `PollTrigger` from `pickem.models`. Near `DEFAULT_POLL_OFFSETS_HOURS` add:

```python
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
```

Add to `DiscordSettings`, after `refresh_minute`:

```python
    # Days the daily refresh pulls odds; the other days re-decide from stored
    # lines. SportsGameOdds bills per game, so the config pulls only Thursday.
    odds_days: tuple[str, ...] = DAYS
```

In `from_env`, inside the `try` after `refresh_days = ...`:

```python
            odds_days = _parse_days(
                schedule["refresh"].get("odds_days", schedule["refresh"]["days"])
            )
```

and pass `odds_days=odds_days,` to `cls(...)`.

After `_parse_offsets`:

```python
def _parse_days(values: object) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise ValueError(f"odds_days must be a list, got {values!r}")
    days = tuple(str(value).lower() for value in values)
    unknown = [day for day in days if day not in DAYS]
    if unknown:
        raise ValueError(f"odds_days has unknown days {unknown}; use {', '.join(DAYS)}")
    return days


def _weekday(timezone: ZoneInfo) -> str:
    """Today's three-letter day in ``timezone``; a seam so tests can pick the day."""
    return DAYS[datetime.now(timezone).weekday()]
```

Change `_refresh_scopes`'s signature and the start of its body:

```python
    async def _refresh_scopes(
        self,
        season: int | None = None,
        week: int | None = None,
        *,
        sport: Sport | None = None,
        window_start: datetime | None = None,
        **poll: Any,
    ) -> tuple[tuple[MonitorScope, RefreshResult], ...]:
        # Automation only ever acts on games that have not kicked off: a locked
        # pick cannot be changed, so re-deciding it is noise and notifying on
        # it is misleading. The pick sheet still renders the whole week.
        refresh_kwargs: dict[str, Any] = {"pending_as_of": datetime.now(UTC)}
        # Passed through only when set, so an adapter whose refresh() takes no
        # keywords keeps working.
        if window_start is not None:
            refresh_kwargs["window_start"] = window_start
        refresh_kwargs.update(poll)
```

and, just before `self._request_dashboard_rebuild()` at the end of `_refresh_scopes`:

```python
        # A pull the budget skipped is news once per 30 days; the snapshot says when.
        for _scope, result in results:
            notice = getattr(result.snapshot, "odds_notice", None)
            if notice:
                await self._send_owner_dm(notice)
```

Replace `_scheduled_refresh`:

```python
    async def _scheduled_refresh(self) -> tuple[tuple[MonitorScope, RefreshResult], ...]:
        # SportsGameOdds bills per game: only the configured days pull odds.
        if _weekday(self.settings.timezone) in self.settings.odds_days:
            return await self._refresh_scopes(trigger=PollTrigger.MIDWEEK)
        return await self._refresh_scopes(pull_odds=False)
```

In `_run_kickoff_poll`, replace the `_refresh_scopes` call with:

```python
            results = await self._refresh_scopes(
                scope.season,
                scope.week,
                sport=scope.sport,
                window_start=instant.at,
                kickoffs=instant.covered_kickoffs,
                trigger=PollTrigger.KICKOFF,
                offset_hours=instant.offset_hours,
            )
```

In `config/discord-bot.yaml`, replace the `refresh` block's end and the `kickoff_polls` comment with:

```yaml
  refresh:
    days: [wed, thu, fri, sat, sun, mon]
    time: "10:00"
    # SportsGameOdds bills per game, so only Thursday's refresh pulls odds;
    # the other days re-decide from stored lines (docs/runbooks/odds-source.md).
    odds_days: [thu]
  # Polls anchored to each kickoff, on top of the daily refresh above. Each poll
  # fetches only the games kicking off then, so cost is per game: about 495 a
  # month at these offsets, of pick'em's 800.
  kickoff_polls:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_discord_bot.py tests/test_pick_check.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/discord_bot.py config/discord-bot.yaml tests/test_discord_bot.py
git commit -m "feat: kickoff polls fetch their own games; daily refresh pulls odds Thursdays

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 8: `poll-odds` and the week-start job use SportsGameOdds

**Files:**
- Modify: `src/pickem/cli.py` (the `poll-odds` command; imports)
- Modify: `scripts/start-week.sh:169-170`
- Test: `tests/test_cli.py`, `tests/test_start_week_script.py:71-75`

**Interfaces:**
- Consumes: `SgoClient` (Task 2); `KeyRejected` (Task 2); `PollTrigger`, `OddsBudgetSkipped` (Task 4); `poll_odds_snapshot(..., trigger, client_factory)` (Task 5).
- Produces: `pickem poll-odds --trigger week_start|cli` (default `cli`). Exit codes: 1 key rejected or feed error, 2 quota, 3 budget skip.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (add imports `from pickem.ingest.odds import KeyRejected`, `from pickem.ingest.sgo import SgoClient`, `from pickem.models import MarketLinesResult, PollTrigger`, `from pickem.operations.odds_budget import OddsBudgetSkipped` where missing):

```python
def _invoke_poll_odds(tmp_path, *extra):
    db = tmp_path / "poll.duckdb"
    db.touch()
    return runner.invoke(
        app,
        ["poll-odds", "--sport", "nfl", "--season", "2026", "--week", "7", "--db", str(db), *extra],
    )


def _record_poll(monkeypatch):
    calls: list[dict] = []

    def fake(*_args, **kwargs):
        calls.append(kwargs)
        return MarketLinesResult(lines=[], skipped=[])

    monkeypatch.setattr("pickem.cli.poll_odds_snapshot", fake)
    return calls


def test_poll_odds_pulls_from_sportsgameodds_with_its_trigger(tmp_path, monkeypatch):
    calls = _record_poll(monkeypatch)

    result = _invoke_poll_odds(tmp_path, "--trigger", "week_start")

    assert result.exit_code == 0, result.output
    assert calls[0]["trigger"] is PollTrigger.WEEK_START
    assert calls[0]["client_factory"] is SgoClient


def test_poll_odds_defaults_to_the_cli_trigger(tmp_path, monkeypatch):
    calls = _record_poll(monkeypatch)

    _invoke_poll_odds(tmp_path)

    assert calls[0]["trigger"] is PollTrigger.CLI


def _raise(error):
    def fake(*_args, **_kwargs):
        raise error

    return fake


def test_poll_odds_says_the_key_was_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pickem.cli.poll_odds_snapshot",
        _raise(KeyRejected("SportsGameOdds rejected the API key (HTTP 401)")),
    )

    result = _invoke_poll_odds(tmp_path)

    assert result.exit_code == 1
    assert "key rejected" in result.output


def test_poll_odds_exits_three_when_the_budget_skips_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pickem.cli.poll_odds_snapshot",
        _raise(OddsBudgetSkipped("Skipped a cli odds pull", notify=True)),
    )

    result = _invoke_poll_odds(tmp_path)

    assert result.exit_code == 3
    assert "Skipped a cli odds pull" in result.output
```

In `tests/test_start_week_script.py`, change `_poll` to:

```python
def _poll(sport: str, week: int, db: Path, days: int = 11) -> str:
    return (
        f"run pickem poll-odds --sport {sport} --season 2026 --week {week} "
        f"--days {days} --trigger week_start --db {db}"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_start_week_script.py -q -k "poll"`
Expected: FAIL — no `--trigger` option; the script omits it.

- [ ] **Step 3: Implement**

In `src/pickem/cli.py`: add imports `from pickem.ingest.sgo import SgoClient`, `KeyRejected` to the `pickem.ingest.odds` import, `PollTrigger` to the `pickem.models` import, and `from pickem.operations.odds_budget import OddsBudgetSkipped`. In the `poll_odds` command add the option after `days`:

```python
    trigger: PollTrigger = typer.Option(
        PollTrigger.CLI,
        help="Why this pull runs, for the SportsGameOdds budget: week_start from the "
        "Tuesday job, cli otherwise",
    ),
```

replace the `poll_odds_snapshot(...)` call with:

```python
            result = poll_odds_snapshot(
                db,
                sport,
                season,
                week,
                now,
                days=days,
                trigger=trigger,
                client_factory=SgoClient,
            )
```

(delete the comment about the `OddsClient` seam above it), and add these handlers — `OddsBudgetSkipped` first, `KeyRejected` before the existing `except OddsApiError`:

```python
        except OddsBudgetSkipped as exc:
            typer.secho(str(exc), fg="yellow", err=True)
            raise typer.Exit(code=3) from exc
```

```python
        except KeyRejected as exc:
            typer.secho(f"odds key rejected: {exc}", fg="red", err=True)
            raise typer.Exit(code=1) from exc
```

In `scripts/start-week.sh`:

```bash
    if ! uv run pickem poll-odds --sport "$sport" --season "$season" --week "$week" \
        --days "$days" --trigger week_start --db "$database"; then
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py tests/test_start_week_script.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/pickem/cli.py scripts/start-week.sh tests/test_cli.py tests/test_start_week_script.py
git commit -m "feat: poll-odds uses SportsGameOdds and says why a pull failed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 9: Pin the NFL shape with one real response

Costs 1 SportsGameOdds object on the shared account.

**Files:**
- Create: `tests/fixtures/sgo_nfl_event.json` (captured)
- Test: `tests/test_sgo_adapter.py`

**Interfaces:**
- Consumes: `SgoClient`, `sgo_team_id` (Tasks 1–2).

- [ ] **Step 1: Capture Thursday's game (New England at Chicago, 2026-10-23 00:15 UTC)**

Run:

```bash
uv run python - <<'EOF'
import json, pathlib
import httpx
from dotenv import dotenv_values

key = dotenv_values("/home/jmiller/props/.env")["PROPS_SPORTSGAMEODDS_API_KEY"]
response = httpx.get(
    "https://api.sportsgameodds.com/v2/events",
    headers={"x-api-key": key},
    params={
        "leagueID": "NFL",
        "teamID": "CHICAGO_BEARS_NFL,NEW_ENGLAND_PATRIOTS_NFL",
        "oddID": "points-home-game-sp-home",
        "startsAfter": "2026-10-22T23:45:00Z",
        "startsBefore": "2026-10-23T00:45:00Z",
        "limit": "10",
    },
    timeout=30,
)
response.raise_for_status()
data = response.json()["data"]
print(len(data), "event(s)")
event = data[0]
trimmed = {
    "eventID": event["eventID"],
    "leagueID": event["leagueID"],
    "teams": {s: {"teamID": event["teams"][s]["teamID"], "names": event["teams"][s]["names"]}
              for s in ("home", "away")},
    "status": {"startsAt": event["status"]["startsAt"]},
    "odds": {k: v for k, v in event["odds"].items() if k == "points-home-game-sp-home"},
}
pathlib.Path("tests/fixtures/sgo_nfl_event.json").write_text(
    json.dumps({"success": True, "data": [trimmed]}, indent=1) + "\n"
)
print(trimmed["teams"], trimmed["status"])
EOF
```

Expected: `1 event(s)` with home `CHICAGO_BEARS_NFL`. If it prints `0 event(s)`, the NFL rule is wrong: rerun once without the `teamID` param (one game in that hour, so still about 1 object), read the real ids, and add `TEAM_ID_OVERRIDES` rows before continuing.

- [ ] **Step 2: Write the test**

Append to `tests/test_sgo_adapter.py`:

```python
def test_a_real_nfl_event_parses_and_matches_the_team_id_rule():
    payload = json.loads((FIXTURES / "sgo_nfl_event.json").read_text())
    (event,) = payload["data"]
    game = Game(
        game_id=make_game_id(Sport.NFL, 2026, 7, "NE", "CHI"),
        sport=Sport.NFL,
        season=2026,
        week=7,
        kickoff_utc=datetime(2026, 10, 23, 0, 15, tzinfo=UTC),
        home_team_id="CHI",
        away_team_id="NE",
    )
    now = datetime(2026, 10, 22, 23, 15, tzinfo=UTC)

    pull = _client(_serve(payload)).fetch_spreads(
        resolver=RESOLVER,
        sport=Sport.NFL,
        season=2026,
        week=7,
        now=now,
        slate=[game.game_id],
        window=(now, now + timedelta(days=7)),
        games=[game],
    )

    assert event["teams"]["home"]["teamID"] == sgo_team_id("CHI", Sport.NFL, RESOLVER)
    assert event["teams"]["away"]["teamID"] == sgo_team_id("NE", Sport.NFL, RESOLVER)
    expected = {
        book: float(quote["spread"])
        for book, quote in event["odds"]["points-home-game-sp-home"]["byBookmaker"].items()
        if quote.get("available")
    }
    assert expected, "the capture should have at least one open book"
    assert {line.book: line.spread_home for line in pull.result.lines} == expected
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_sgo_adapter.py -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/sgo_nfl_event.json tests/test_sgo_adapter.py
git commit -m "test: pin the SportsGameOdds NFL shape with a real response

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 10: Documentation

**Files:**
- Create: `docs/runbooks/odds-source.md`
- Modify: `docs/invariants.md`, `CLAUDE.md` (`## Jobs`), `docs/system-map.md`

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/odds-source.md`:

```markdown
# Odds source: SportsGameOdds

Live spreads come from SportsGameOdds (SGO) since 2026-10-21. The Odds API key
was deactivated when its paid plan was cancelled; its code stays only for the
historical archive the backtest reads.

## The key

`SPORTSGAMEODDS_API_KEY` in `.env`. It is the same account props uses
(`PROPS_SPORTSGAMEODDS_API_KEY` in `~/props/.env`), so the free plan's 2,500
games per rolling 30 days are shared.

## Pick'em's share: 800 games per 30 days

SGO bills one object per game returned. Pick'em asks only for its own games:

| Pull | Games | Per month |
|---|---|---|
| Tuesday week start | every game on both boards | ~125 |
| Thursday 10:00 refresh | every game not yet started | ~125 |
| 12, 6, 2 and 1 hours before each kickoff | only that kickoff's games | ~495 |
| `/refresh` | every game not yet started | 15–30 each |

The 10:00 refresh on the other days re-decides from stored lines without
pulling (`odds_days` in `config/discord-bot.yaml`).

How much has pick'em used?

    uv run python -c "from datetime import UTC, datetime, timedelta; \
    from pickem.store.db import Store; \
    s = Store('data/pickem.duckdb', read_only=True); \
    print(s.odds_games_used(datetime.now(UTC) - timedelta(days=30)))"

The account-wide figure (props included) is logged as `odds_account_usage` by
each Tuesday week start.

## "Skipped an odds pull" DM

A pull would have taken pick'em past 800. Nothing is broken: picks are
re-decided from the lines already stored, and the 2-hour and 1-hour pulls
before each kickoff still run. You hear about this once per 30 days; every skip
is logged as `odds_budget_skipped`. To change the share, edit
`GAMES_PER_30_DAYS` in `src/pickem/operations/odds_budget.py`.

## "no SportsGameOdds lines" for a game

The pull listed `<game id>: no SportsGameOdds lines (team ids X, Y)`. Either
the books have not priced the game yet, or one of the two team ids is wrong.
Check the id against SGO's (an event's `teams.home.teamID`), then add a row to
`TEAM_ID_OVERRIDES` in `src/pickem/ingest/sgo.py`.

## "odds key rejected"

SGO refused the key (HTTP 401 or 403). Check the account, then the key in both
`.env` files.

## If the SGO account ever ends

SGO's terms require deleting all its data within 30 days. That is the one
exception to "`lines` is append-only": delete the rows with
`source = 'sportsgameodds'` (and the `odds_pulls` table), nothing else.
```

- [ ] **Step 2: Update the invariants**

In `docs/invariants.md`, replace the bullet that begins `- **Pinnacle is recorded, never used.**` with:

```markdown
- **Live odds come from SportsGameOdds, and cost is per game.** Since
  2026-10-21 the live market is SGO (`source = "sportsgameodds"`); the Odds API
  client remains only for the historical archive. SGO bills per game returned,
  so a poll asks only for unstarted slate games, by team id, and a kickoff poll
  only for its own kickoffs. Pick'em keeps to 800 games per 30 days
  (`operations/odds_budget.py`); the 2-hour and 1-hour kickoff polls always run.
  See `docs/runbooks/odds-source.md`.
- **Pinnacle is no longer recorded.** It was polled 2026-09-25 to 2026-10-19
  as `oddsapi:pinnacle`; SGO's free plan has no Pinnacle. A 91-game replay found
  Pinnacle-only picks no better than the US median (47–44 against 49–42; the
  picks differed on 4 games). Stored rows stay and never enter the consensus.
```

In the bullet `- **The \`lines\` table is append-only.**`, append:

```markdown
  One exception, forced by SGO's terms: if that account ends, its rows
  (`source = 'sportsgameodds'`) must be deleted within 30 days.
```

- [ ] **Step 3: Update `CLAUDE.md` and the system map**

In `CLAUDE.md` under `## Jobs`, add `odds_budget_skipped` to the "Alert events" list, and under "What alerts mean" add:

```markdown
   - A "Skipped an odds pull" DM means pick'em reached its 800-game SportsGameOdds share; the 2-hour and 1-hour pulls still run. "odds key rejected" means SportsGameOdds refused the key. Both are in `docs/runbooks/odds-source.md`.
```

In `docs/system-map.md`, after the `src/pickem/ingest/odds.py` entry, add:

```
src/pickem/ingest/sgo.py          SgoClient.fetch_spreads (live spreads from
                                  SportsGameOdds since 2026-10-21). Asks only
                                  for the requested games, by team id
                                  (sgo_team_id), and parses through odds.py's
                                  _parse_events so the guards are shared.
src/pickem/operations/odds_budget.py
                                  Pick'em's 800-games-per-30-days share of the
                                  SportsGameOdds plan, counted in odds_pulls.
```

- [ ] **Step 4: Full checks**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all tests pass; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/odds-source.md docs/invariants.md CLAUDE.md docs/system-map.md
git commit -m "docs: SportsGameOdds as the live odds source

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K8WykmZqfXrf4GoWEK9ogH"
```

---

### Task 11: Roll out and poll this week

Not code. Do it with the user watching; it changes the live system.

- [ ] **Step 1: Copy the key into this project's `.env` without printing it**

```bash
cd /home/jmiller/cfb && uv run python - <<'EOF'
from pathlib import Path
from dotenv import dotenv_values
key = dotenv_values("/home/jmiller/props/.env")["PROPS_SPORTSGAMEODDS_API_KEY"]
env = Path(".env")
text = env.read_text()
assert "SPORTSGAMEODDS_API_KEY=" not in text, "already set"
env.write_text(text.rstrip("\n") + f"\nSPORTSGAMEODDS_API_KEY={key}\n")
print("added")
EOF
```

- [ ] **Step 2: Ship with the `ship` skill**

It merges to `master`, runs the checks, pushes, restarts the bot and checks the timers. Nothing under `deploy/` changes.

- [ ] **Step 3: Poll pool week 8 by hand**

The Wednesday retry of `start-week.sh --auto` exits on a week already started, so:

```bash
cd /home/jmiller/cfb
uv run pickem poll-odds --sport nfl --season 2026 --week 7 --days 11 --trigger week_start
uv run pickem poll-odds --sport cfb --season 2026 --week 8 --days 11 --trigger week_start
```

Expected: `appended N market lines` for each, at most ~29 games used in all.

- [ ] **Step 4: Check every game has lines**

Read the yellow "market rows not stored" list from Step 3. For any `no SportsGameOdds lines (team ids X, Y)` on a game the books should be pricing, add a `TEAM_ID_OVERRIDES` row (test-first, in `tests/test_sgo_adapter.py`), ship, and poll that sport again. Then confirm coverage, which now counts SportsGameOdds rows:

```bash
uv run pickem preflight --sport nfl --season 2026 --week 7 --expected-games 14 --max-age-minutes 120
uv run pickem preflight --sport cfb --season 2026 --week 8 --expected-games 15 --max-age-minutes 120
```

Expected: both ready. A game short of books this early in the week is worth noting, not fixing.
