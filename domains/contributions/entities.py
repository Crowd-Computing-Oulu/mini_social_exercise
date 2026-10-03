"""
Contributions domain — entities.

Pure data objects shared by usecases.py and (unwrapped) by the interface layer.
No SQL, no Flask, no Jinja — entities know nothing about how they are stored
or displayed.
"""

from dataclasses import dataclass


@dataclass
class Streak:
    """A user's contribution streak, computed from their daily activity.

    current:  consecutive days with at least one contribution, ending today
              or yesterday.
    at_risk:  True when the streak is alive only via yesterday — one more
              day without contributing breaks it.
    best:     the longest streak this user ever had (0 when not computed;
              profile pages compute it, batch pages don't need it).
    """

    current: int
    at_risk: bool
    best: int = 0
