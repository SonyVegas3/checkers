"""Time budget for one move, derived from the clock the site reports.

What is confirmed (from the saved games in ``logs/``): the checkers client
publishes ``info.step_timeout`` and it is **reset on every move** - every sample
of every logged game shows 57-60 s, whatever the step number.  It is therefore a
per-move clock of about 60 s, not a game clock.

What is *not* known: what the site does when that clock runs out (forfeit the
move, skip the turn, lose the game).  The bot therefore never plans to use all of
it: the search gets the clock minus the time the surrounding work needs minus a
safety margin, and a soft limit stops it from starting an iteration it cannot
finish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: Reading the board, probing the site (one click per stone) and clicking the move
#: all happen inside the same clock, so their cost is taken off the budget first.
DEFAULT_OVERHEAD_S = 6.0
#: Part of the remaining clock kept free for a lost response or a retry.
DEFAULT_SAFETY = 0.20
#: Never plan less than this, even on a tiny clock.
MIN_BUDGET_S = 0.8
#: Budget when the page reports no clock at all (the old fixed value).
DEFAULT_BUDGET_S = 12.0
#: The soft limit is this share of the hard limit: the last started iteration may
#: finish, and an aspiration re-search still fits.
SOFT_SHARE = 0.75


@dataclass(frozen=True)
class TimeBudget:
    """Soft/hard search limits for one move, plus where they came from."""

    soft_s: float
    hard_s: float
    clock_s: Optional[float]
    overhead_s: float
    source: str

    def to_dict(self) -> dict:
        return {
            "soft_s": round(self.soft_s, 2),
            "hard_s": round(self.hard_s, 2),
            "clock_s": self.clock_s,
            "overhead_s": self.overhead_s,
            "source": self.source,
        }


def plan_budget(
    clock_s: Optional[float],
    *,
    overhead_s: float = DEFAULT_OVERHEAD_S,
    safety: float = DEFAULT_SAFETY,
    floor_s: float = MIN_BUDGET_S,
    ceiling_s: Optional[float] = None,
    default_s: float = DEFAULT_BUDGET_S,
) -> TimeBudget:
    """Turn the site's per-move clock into soft/hard search limits.

    ``clock_s`` is ``info.step_timeout`` (seconds) or ``None`` when the page did
    not report one.  ``ceiling_s`` is the user's own cap (``--time``).
    """
    if clock_s is None or clock_s <= 0:
        hard = float(default_s)
        source = "default (no site clock)"
        if ceiling_s is not None:
            hard = min(hard, float(ceiling_s))
    else:
        usable = float(clock_s) - max(0.0, float(overhead_s)) - float(safety) * float(clock_s)
        hard = max(floor_s, usable)
        source = "site clock %.0fs" % float(clock_s)
        if ceiling_s is not None:
            hard = max(floor_s, min(hard, float(ceiling_s)))
    hard = max(floor_s, hard)
    if ceiling_s is not None:
        hard = min(hard, max(floor_s, float(ceiling_s)))
    return TimeBudget(
        soft_s=round(hard * SOFT_SHARE, 3),
        hard_s=round(hard, 3),
        clock_s=None if clock_s is None else float(clock_s),
        overhead_s=float(overhead_s),
        source=source,
    )
