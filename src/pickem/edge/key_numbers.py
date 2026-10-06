"""Football's common winning margins, and whether a small gap spans one.

NFL games end exactly 3 points apart about 15% of the time and exactly 7 apart
about 8% (2021-2025). CBS posts half points, so when the market sits on 3 or 7
the half point between them decides every game that lands there. That is worth
far more than a half point anywhere else.

Pure functions only. No network, no database, no filesystem.
"""

from __future__ import annotations

KEY_NUMBERS = (3, 7)


def key_number_crossed(league_spread: float, market_spread: float) -> int | None:
    """The key number lying between the two lines, inclusive; None if neither does.

    Both spreads are home-perspective, so either sign of 3 or 7 counts.
    """
    low, high = sorted((league_spread, market_spread))
    for key in KEY_NUMBERS:
        if low <= key <= high or low <= -key <= high:
            return key
    return None
