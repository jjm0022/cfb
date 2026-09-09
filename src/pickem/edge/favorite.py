"""Take the frozen board's favorite — the COINFLIP tiebreak.

A COINFLIP is a game where the frozen league line and the live market agree, so
there is no divergence to trade and no side the strategy actually prefers. That
also means picking one is a bet against the market's own number, which a public
rating is not equipped to win.

Walk-forward evaluation over 2022–2025 (1,549 CFB coinflips) measured this rule
at 51.00% against 48.93% for the Elo tiebreak it replaced. Both sit inside the
noise band around 50%: the simpler rule is preferred because it is cheaper and
carries no fitted state, not because it has demonstrated skill. See
`docs/research/2026-08-27-cfb-coinflip-residual-result.md`.

Pure functions only. No network, no database, no filesystem.
"""

from __future__ import annotations

from pickem.models import Side


def favorite_side(league_spread: float) -> Side:
    """Return the side the frozen board favors.

    `league_spread` is home-perspective, so a negative number means the home
    team is laying points and is therefore the favorite. An exact pick'em
    resolves to HOME, which is the convention the evaluated baseline used.
    """
    return Side.HOME if league_spread <= 0 else Side.AWAY
