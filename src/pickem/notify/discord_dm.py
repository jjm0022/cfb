"""One-shot Discord DM for a finished results report.

Uses discord.py's REST session only — log in, fetch the owner, send, close —
with no gateway connection. It works whether or not the bot service is running
and never touches that process. The report file stays the record; the DM is a
pointer to it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import discord

from pickem.report.results import (
    BACKTEST_TIER_RATES,
    ResultsReport,
    Strategy,
    clv_summary,
    record_for,
)

_FIELD_VALUE_LIMIT = 1024
_BOARD_STRATEGIES = (Strategy.US, Strategy.MODEL, Strategy.FIELD, Strategy.CLOSE_DIVERGENCE)


def _cap(text: str) -> str:
    if len(text) <= _FIELD_VALUE_LIMIT:
        return text
    return text[: _FIELD_VALUE_LIMIT - 1] + "…"


_DESCRIPTION_LIMIT = 4096


def build_message_embed(title: str, message: str) -> discord.Embed:
    """A plain notice for the scheduled scripts: a title and one message."""
    if len(message) > _DESCRIPTION_LIMIT:
        message = message[: _DESCRIPTION_LIMIT - 1] + "…"
    return discord.Embed(title=title, description=message)


def build_results_embed(report: ResultsReport, report_path: Path) -> discord.Embed:
    week = report.current
    embed = discord.Embed(
        title=f"🏈 Pool week {report.pool_week} results",
        description=(
            f"{report.entry_name}: {week.our_points} pts, rank {week.our_rank} of "
            f"{week.entrants} · median {week.median_points:g} · winner {week.winner_points} "
            f"(gap {week.gap_to_winner})"
        ),
        color=discord.Color.blue(),
    )
    for board in week.boards:
        games = [game for game in report.week_games if game.game.sport is board.sport]
        value = "\n".join(f"{st.value}: {record_for(games, st)}" for st in _BOARD_STRATEGIES)
        embed.add_field(
            name=(
                f"{board.sport.value.upper()} board — {board.our_points} pts "
                f"(median {board.median_points:g}, best {board.best_points})"
            ),
            value=_cap(value),
            inline=False,
        )
    tiers = "\n".join(
        f"{tier.value}: {record_for(report.season_games, Strategy.MODEL, tier=tier)} "
        f"(NFL backtest {rate:.1%})"
        for tier, rate in BACKTEST_TIER_RATES.items()
    )
    embed.add_field(name="Season to date — model by tier", value=_cap(tiers), inline=False)
    clv = clv_summary(report.season_games)
    clv_text = (
        "no closing lines stored"
        if not clv.n
        else f"mean {clv.mean:+.2f} pts over {clv.n} picks, {clv.positive_share:.0%} positive"
    )
    embed.add_field(name="Closing-line value (season)", value=_cap(clv_text), inline=False)
    embed.add_field(name="Full report", value=_cap(str(report_path)), inline=False)
    return embed


def _default_client() -> discord.Client:
    return discord.Client(intents=discord.Intents.none())


async def send_owner_dm(
    embed: discord.Embed,
    *,
    token: str,
    owner_id: int,
    client_factory: Callable[[], Any] = _default_client,
) -> None:
    """Deliver one embed to the owner over REST, always closing the session."""
    client = client_factory()
    try:
        await client.login(token)
        user = await client.fetch_user(owner_id)
        await user.send(embed=embed)
    finally:
        await client.close()
