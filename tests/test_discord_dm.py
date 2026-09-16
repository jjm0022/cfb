import asyncio
from pathlib import Path

import discord
import pytest
from results_helpers import imported_store

from pickem.notify.discord_dm import _cap, build_results_embed, send_owner_dm
from pickem.report.results import build_results_report


@pytest.fixture
def report():
    store = imported_store()
    try:
        yield build_results_report(store, season=2026, pool_week=2, entry_name="Jota")
    finally:
        store.close()


def test_embed_carries_headline_boards_tiers_clv_and_path(report):
    embed = build_results_embed(report, Path("data/cbs/results/week2-report.md"))
    assert embed.title == "🏈 Pool week 2 results"
    assert "Jota: 16 pts, rank 17 of 4" in embed.description
    names = [field.name for field in embed.fields]
    assert "CFB board — 1 pts (median 0.5, best 2)" in names
    assert "NFL board — 0 pts (median 0, best 2)" in names
    assert "Season to date — model by tier" in names
    assert "Closing-line value (season)" in names
    assert embed.fields[-1].value == "data/cbs/results/week2-report.md"
    assert all(len(field.value) <= 1024 for field in embed.fields)


def test_cap_truncates_to_discords_field_limit():
    assert _cap("short") == "short"
    capped = _cap("x" * 2000)
    assert len(capped) == 1024
    assert capped.endswith("…")


class FakeUser:
    def __init__(self, calls, fail):
        self.calls = calls
        self.fail = fail

    async def send(self, *, embed):
        self.calls.append(("send", embed))
        if self.fail:
            raise RuntimeError("cannot DM")


class FakeClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def login(self, token):
        self.calls.append(("login", token))

    async def fetch_user(self, owner_id):
        self.calls.append(("fetch_user", owner_id))
        return FakeUser(self.calls, self.fail)

    async def close(self):
        self.calls.append(("close",))


def test_send_owner_dm_logs_in_sends_and_closes():
    client = FakeClient()
    embed = discord.Embed(title="t")
    asyncio.run(send_owner_dm(embed, token="tok", owner_id=123, client_factory=lambda: client))
    assert client.calls == [("login", "tok"), ("fetch_user", 123), ("send", embed), ("close",)]


def test_send_owner_dm_closes_the_session_when_sending_fails():
    client = FakeClient(fail=True)
    with pytest.raises(RuntimeError, match="cannot DM"):
        asyncio.run(send_owner_dm(discord.Embed(title="t"), token="tok", owner_id=123,
                                  client_factory=lambda: client))
    assert client.calls[-1] == ("close",)
