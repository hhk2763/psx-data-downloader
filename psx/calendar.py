"""Trading-day arithmetic in Pakistan Standard Time.

Weekends are skipped without a request. Holidays are deliberately *not*
hardcoded: they come back as 404s and get recorded as ``missing``.

Note the module is ``psx.calendar``; it does not shadow the standard library's
top-level ``calendar``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterator

#: Pakistan Standard Time. A fixed UTC+5 offset: Pakistan observes no DST, and
#: `zoneinfo` would need the `tzdata` package on Windows.
PKT = timezone(timedelta(hours=5), "PKT")

SATURDAY = 5
SUNDAY = 6

#: Default PKT hour after which today's files are expected to be published.
DEFAULT_PUBLISH_HOUR = 19

ONE_DAY = timedelta(days=1)


def now_pkt() -> datetime:
    """Current time in PKT."""
    return datetime.now(PKT)


def today_pkt() -> date:
    """Today's date in PKT, which is what 'today' means everywhere in this tool."""
    return now_pkt().date()


def is_trading_day(day: date) -> bool:
    """True for Mon-Fri. Holidays are unknowable in advance, so they count here."""
    return day.weekday() < SATURDAY


def previous_trading_day(day: date) -> date:
    """The most recent weekday strictly before ``day``."""
    cursor = day - ONE_DAY
    while not is_trading_day(cursor):
        cursor -= ONE_DAY
    return cursor


def next_trading_day(day: date) -> date:
    """The next weekday strictly after ``day``."""
    cursor = day + ONE_DAY
    while not is_trading_day(cursor):
        cursor += ONE_DAY
    return cursor


def iter_trading_days(date_from: date, date_to: date) -> Iterator[date]:
    """Yield each weekday from ``date_from`` to ``date_to``, both inclusive.

    Yields nothing when the range is inverted.
    """
    cursor = date_from
    while cursor <= date_to:
        if is_trading_day(cursor):
            yield cursor
        cursor += ONE_DAY


def count_trading_days(date_from: date, date_to: date) -> int:
    """How many weekdays the range covers. Used for the pre-run estimate."""
    return sum(1 for _ in iter_trading_days(date_from, date_to))


def last_completed_trading_day(
    *,
    now: datetime | None = None,
    publish_hour: int = DEFAULT_PUBLISH_HOUR,
) -> date:
    """The latest day whose files should exist by now.

    Today counts only if it is a weekday *and* the PKT clock has passed
    ``publish_hour`` -- PSX publishes after the close. Otherwise it is the
    previous weekday.
    """
    moment = now or now_pkt()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=PKT)
    moment = moment.astimezone(PKT)

    today = moment.date()
    if is_trading_day(today) and moment.hour >= publish_hour:
        return today
    return previous_trading_day(today)


def last_n_trading_days(n: int, *, end: date | None = None, publish_hour: int = DEFAULT_PUBLISH_HOUR) -> list[date]:
    """The ``n`` most recent trading days ending at ``end`` (inclusive).

    ``end`` defaults to :func:`last_completed_trading_day`. Returns oldest
    first, so it pairs directly with a ``date_from``/``date_to`` range.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")

    cursor = end if end is not None else last_completed_trading_day(publish_hour=publish_hour)
    days: list[date] = []
    if is_trading_day(cursor):
        days.append(cursor)
    while len(days) < n:
        cursor = previous_trading_day(cursor)
        days.append(cursor)
    return sorted(days)


def range_for_last_n(n: int, *, end: date | None = None, publish_hour: int = DEFAULT_PUBLISH_HOUR) -> tuple[date, date]:
    """``(date_from, date_to)`` covering the last ``n`` trading days."""
    days = last_n_trading_days(n, end=end, publish_hour=publish_hour)
    return days[0], days[-1]
