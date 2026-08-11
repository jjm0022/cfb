"""Render the weekly pick sheet.

Every row carries the two numbers that produced the pick, so the user can audit
any selection without opening the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pickem.edge.divergence import rank_edges
from pickem.models import Edge, Game, Side


def render_sheet(
    edges: Sequence[Edge],
    games: Sequence[Game],
    *,
    generated_at: datetime,
    snapshot_age_minutes: float | None = None,
) -> str:
    by_id = {game.game_id: game for game in games}

    header = [f"# Pick Sheet — generated {generated_at:%Y-%m-%d %H:%M UTC}", ""]
    if snapshot_age_minutes is not None:
        header.append(
            f"> Market snapshot is **{snapshot_age_minutes:.0f} minutes old**. "
            "Re-run `pickem poll-odds` for fresher numbers."
        )
        header.append("")

    rows = [
        "| # | Matchup | Pick | Tier | Edge | League | Market |",
        "|---|---------|------|------|------|--------|--------|",
    ]
    for index, edge in enumerate(rank_edges(edges), start=1):
        game = by_id.get(edge.game_id)
        if game is None:
            matchup, pick = edge.game_id, edge.side.value
        else:
            matchup = f"{game.away_team_id} at {game.home_team_id}"
            pick = game.home_team_id if edge.side is Side.HOME else game.away_team_id
        market = "—" if edge.market_spread is None else f"{edge.market_spread:+.1f}"
        rows.append(
            f"| {index} | {matchup} | **{pick}** | {edge.tier.value} | "
            f"{abs(edge.delta):.1f} | {edge.league_spread:+.1f} | {market} |"
        )

    return "\n".join([*header, *rows, ""])
