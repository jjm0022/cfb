"""The frozen board's favorite and underdog — the COINFLIP tiebreak takes the underdog.

Since 2026-11-09 a COINFLIP resolves to the underdog. Neither side has a
measured edge, but most of the pool takes the favorite, so the underdog makes
our card different at no cost in expected points: simulated over pool weeks 1–9
of 2026 it raised the chance of winning a week from 3.5% to 5.7%. That reverses
if coinflip favorites cover about 53% or more. See "Weekly-win simulation" in
`docs/results.md`. What follows is why the favorite was the rule before that.

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


def underdog_side(league_spread: float) -> Side:
    """Return the side the frozen board does not favor; AWAY on an exact pick'em."""
    return Side.AWAY if favorite_side(league_spread) is Side.HOME else Side.HOME
