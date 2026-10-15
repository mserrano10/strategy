"""US cash-session dating for `--end today`.

The backtest should stop on the last completed NYSE session, not on a
calendar day that has no close yet. Holidays are not listed here; callers
accept a download that ends a few days early when the venue is shut.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def required_through(end: date, now: datetime) -> date:
    """Last session the cache must already cover for this request.

    `now` is interpreted as UTC. The US cash close is treated as 21:00 UTC,
    which is after both the 20:00 UTC (EDT) and 21:00 UTC (EST) closes, so a
    same-day bar is not required until that hour has passed.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    target = min(end, now.date())
    if target == now.date() and now.hour < 21 and target.weekday() < 5:
        target -= timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target
