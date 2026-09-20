"""Step 2 — trading-day arithmetic.

Reference week: Mon 2026-09-14 ... Fri 2026-09-18, Sat 19, Sun 20, Mon 21.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from psx.calendar import (
    PKT,
    count_trading_days,
    is_trading_day,
    iter_trading_days,
    last_completed_trading_day,
    last_n_trading_days,
    next_trading_day,
    previous_trading_day,
    range_for_last_n,
    today_pkt,
)

MON = date(2026, 9, 14)
THU = date(2026, 9, 17)
FRI = date(2026, 9, 18)
SAT = date(2026, 9, 19)
SUN = date(2026, 9, 20)
NEXT_MON = date(2026, 9, 21)


def test_pkt_is_utc_plus_five() -> None:
    assert PKT.utcoffset(None) == timedelta(hours=5)


def test_is_trading_day_excludes_the_weekend() -> None:
    assert is_trading_day(MON)
    assert is_trading_day(FRI)
    assert not is_trading_day(SAT)
    assert not is_trading_day(SUN)


def test_today_pkt_uses_the_pkt_clock() -> None:
    """At 22:00 UTC it is already the next day in PKT (+5)."""
    assert today_pkt() == datetime.now(PKT).date()


# --- iteration --------------------------------------------------------------


def test_iter_trading_days_skips_the_weekend() -> None:
    days = list(iter_trading_days(FRI, NEXT_MON))
    assert days == [FRI, NEXT_MON]
    assert SAT not in days and SUN not in days


def test_iter_trading_days_is_inclusive_at_both_ends() -> None:
    assert list(iter_trading_days(MON, FRI)) == [
        MON,
        date(2026, 9, 15),
        date(2026, 9, 16),
        THU,
        FRI,
    ]


def test_iter_trading_days_single_day() -> None:
    assert list(iter_trading_days(THU, THU)) == [THU]


def test_iter_trading_days_single_weekend_day_is_empty() -> None:
    assert list(iter_trading_days(SAT, SAT)) == []


def test_iter_trading_days_inverted_range_is_empty() -> None:
    assert list(iter_trading_days(FRI, MON)) == []


def test_count_trading_days_matches_iteration() -> None:
    assert count_trading_days(MON, NEXT_MON) == 6
    assert count_trading_days(SAT, SUN) == 0


def test_previous_and_next_trading_day_cross_the_weekend() -> None:
    assert previous_trading_day(NEXT_MON) == FRI
    assert next_trading_day(FRI) == NEXT_MON
    assert previous_trading_day(SUN) == FRI
    assert next_trading_day(SAT) == NEXT_MON


# --- last completed trading day --------------------------------------------


def test_before_publish_hour_on_a_weekday_means_yesterday() -> None:
    now = datetime(2026, 9, 18, 10, 0, tzinfo=PKT)  # Friday morning
    assert last_completed_trading_day(now=now, publish_hour=19) == THU


def test_after_publish_hour_on_a_weekday_means_today() -> None:
    now = datetime(2026, 9, 18, 19, 0, tzinfo=PKT)  # Friday, 19:00 sharp
    assert last_completed_trading_day(now=now, publish_hour=19) == FRI


def test_saturday_and_sunday_fall_back_to_friday() -> None:
    for day in (SAT, SUN):
        now = datetime(day.year, day.month, day.day, 23, 0, tzinfo=PKT)
        assert last_completed_trading_day(now=now, publish_hour=19) == FRI


def test_monday_before_publish_hour_falls_back_to_friday() -> None:
    now = datetime(2026, 9, 21, 9, 0, tzinfo=PKT)
    assert last_completed_trading_day(now=now, publish_hour=19) == FRI


def test_a_utc_timestamp_is_converted_to_pkt_first() -> None:
    """17:00 UTC on Thursday is 22:00 PKT, i.e. Thursday is already published."""
    now = datetime(2026, 9, 17, 17, 0, tzinfo=timezone.utc)
    assert last_completed_trading_day(now=now, publish_hour=19) == THU


def test_a_naive_timestamp_is_read_as_pkt() -> None:
    now = datetime(2026, 9, 18, 20, 0)
    assert last_completed_trading_day(now=now, publish_hour=19) == FRI


# --- last N -----------------------------------------------------------------


def test_last_n_trading_days_crosses_the_weekend_and_is_sorted() -> None:
    assert last_n_trading_days(5, end=NEXT_MON) == [
        date(2026, 9, 15),
        date(2026, 9, 16),
        THU,
        FRI,
        NEXT_MON,
    ]


def test_last_n_trading_days_from_a_weekend_end() -> None:
    """A Saturday end date is not itself a trading day and must not be counted."""
    assert last_n_trading_days(2, end=SAT) == [THU, FRI]


def test_last_n_trading_days_of_one() -> None:
    assert last_n_trading_days(1, end=NEXT_MON) == [NEXT_MON]


def test_last_n_trading_days_rejects_zero() -> None:
    with pytest.raises(ValueError, match="n must be >= 1"):
        last_n_trading_days(0, end=NEXT_MON)


def test_range_for_last_n() -> None:
    assert range_for_last_n(3, end=NEXT_MON) == (THU, NEXT_MON)


def test_range_for_last_n_covers_exactly_n_trading_days() -> None:
    date_from, date_to = range_for_last_n(7, end=NEXT_MON)
    assert count_trading_days(date_from, date_to) == 7
