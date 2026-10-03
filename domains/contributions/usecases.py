"""
Contributions domain — use cases (streak rules).

Only business rules live here (what a streak is, when it is at risk, toast
copy). Repositories are passed in, so this module imports no sqlite3 or
Flask and is unit-testable with a fake repository.
"""

import datetime

from .entities import Streak

# Streak lengths celebrated with a special toast (design claim 13).
MILESTONES = (3, 7, 14, 30)

_DATE_FORMAT = '%Y-%m-%d'


def utc_today():
    """Today as the app stores it: the UTC date, 'YYYY-MM-DD'.

    Server time and SQLite timestamps are both UTC, so they share one
    timeline (and the day boundary is UTC midnight).
    """
    return datetime.datetime.utcnow().strftime(_DATE_FORMAT)


def compute_streak(daily_counts, today):
    """Streak for one user from their daily counts.

    daily_counts: {date_string: count} (from a repository).
    today: the current UTC date, 'YYYY-MM-DD'.
    Returns a Streak: consecutive days ending today, or ending yesterday
    when nothing today yet (at_risk=True); a missed day resets to 0.
    """
    current = 0
    day = _to_date(today)
    # Keys may be date strings ('2026-09-22'); compare everything as dates.
    days = {_to_date(k) for k in daily_counts}
    if day not in days:
        # Nothing today: the streak can only survive via yesterday,
        # and it is "at risk" until the user contributes today.
        day = _day_before(day)
        if day not in days:
            return Streak(current=0, at_risk=False)
        at_risk = True
    else:
        at_risk = False

    # Walk back, day by day, while the user contributed.
    while day in days:
        current += 1
        day = _day_before(day)
    return Streak(current=current, at_risk=at_risk)


def compute_best_streak(daily_counts):
    """Longest run of consecutive days in the user's full history (int)."""
    if not daily_counts:
        return 0
    best = 0
    run = 0
    previous = None
    for day in sorted(daily_counts):
        day_date = _to_date(day)
        if previous is not None and day_date == previous + datetime.timedelta(days=1):
            run += 1
        else:
            run = 1
        best = max(best, run)
        previous = day_date
    return best


def current_streak(repo, user_id, today=None):
    """Current streak for one user (badge/banner state; best not computed)."""
    if today is None:
        today = utc_today()
    return compute_streak(repo.get_daily_counts(user_id), today)


def get_user_streak(repo, user_id, today=None):
    """Full streak info for one user: current, at_risk and personal best."""
    if today is None:
        today = utc_today()
    days = repo.get_daily_counts(user_id)
    streak = compute_streak(days, today)
    streak.best = compute_best_streak(days)
    return streak


def get_streaks_for_users(repo, user_ids, today=None):
    """Batch version for pages showing many users at once.

    One repository query for the whole page, one rule pass per user.
    Returns {user_id: Streak} for every requested user (no history ->
    Streak(0, False), rendered as nothing by the badge macro).
    """
    user_ids = list(user_ids)
    if not user_ids:
        return {}
    if today is None:
        today = utc_today()
    days_by_user = repo.get_activity_days_for_users(user_ids)
    return {
        user_id: compute_streak(days_by_user.get(user_id, {}), today)
        for user_id in user_ids
    }


def streak_flash_message(current):
    """Toast copy for the contribution that grew the streak to `current`.

    Milestones get their own wording; everything else reports the length.
    """
    if current in MILESTONES:
        return f"🎉 You posted {current} days in a row!"
    return (f"🔥 Streak extended to {current} "
            f"day{'s' if current != 1 else ''}!")


def _to_date(value):
    """'YYYY-MM-DD' (or a full 'YYYY-MM-DD HH:MM:SS' timestamp) -> date."""
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value)[:10], _DATE_FORMAT).date()


def _day_before(day):
    return day - datetime.timedelta(days=1)