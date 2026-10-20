import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from pickem.ingest.odds import KeyRejected, OddsApiError, QuotaExhausted
from pickem.ingest.sgo import TEAM_ID_OVERRIDES, SgoClient, sgo_team_id
from pickem.models import SGO_SOURCE, Game, Sport, make_game_id
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
