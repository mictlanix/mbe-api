"""Spec 038, T058: when a schedule runs next, in business time (research R15, FR-160, FR-163).

`next_occurrence(schedule, after)` is the first occurrence strictly after `after`; `due(schedule,
now)` is what a run claims: the latest occurrence at or before `now`, and the next one after it.
A monthly day past the month's end means its last day. 2026-06-01 is a Monday.
"""

from datetime import datetime, time

import pytest

from app.models.analytics import AnalyticsSchedule
from app.services.analytics import schedules

MONDAY, WEDNESDAY, FRIDAY, SUNDAY = 1, 4, 16, 64


def _schedule(cadence: int, at: time = time(8), weekdays: int = 0, month_day: int | None = None):
    return AnalyticsSchedule(
        cadence=cadence, weekdays=weekdays, month_day=month_day, time_of_day=at
    )


DAILY = _schedule(0)


def test_daily_before_and_after_the_time() -> None:
    assert schedules.next_occurrence(DAILY, datetime(2026, 6, 1, 7, 59)) == datetime(2026, 6, 1, 8)
    assert schedules.next_occurrence(DAILY, datetime(2026, 6, 1, 8)) == datetime(2026, 6, 2, 8)


def test_a_time_already_passed_today_is_tomorrow() -> None:
    evening = _schedule(0, time(18, 30))

    assert schedules.next_occurrence(evening, datetime(2026, 6, 30, 19)) == datetime(
        2026, 7, 1, 18, 30
    )


@pytest.mark.parametrize(
    ('after', 'expected'),
    [
        (datetime(2026, 6, 1, 7), datetime(2026, 6, 1, 8)),  # Monday, before the time
        (datetime(2026, 6, 1, 9), datetime(2026, 6, 3, 8)),  # Monday after it: Wednesday
        (datetime(2026, 6, 3, 8), datetime(2026, 6, 5, 8)),  # Wednesday at it: Friday
        (datetime(2026, 6, 5, 9), datetime(2026, 6, 7, 8)),  # Friday after it: Sunday
        (datetime(2026, 6, 7, 9), datetime(2026, 6, 8, 8)),  # Sunday after it: next Monday
    ],
)
def test_weekly_on_several_weekdays(after: datetime, expected: datetime) -> None:
    weekly = _schedule(1, weekdays=MONDAY | WEDNESDAY | FRIDAY | SUNDAY)

    assert schedules.next_occurrence(weekly, after) == expected


@pytest.mark.parametrize(
    ('after', 'expected'),
    [
        (datetime(2026, 1, 31, 9), datetime(2026, 2, 28, 8)),  # February: its last day
        (datetime(2026, 2, 28, 9), datetime(2026, 3, 31, 8)),
        (datetime(2026, 3, 31, 9), datetime(2026, 4, 30, 8)),  # April has 30
        (datetime(2028, 1, 31, 9), datetime(2028, 2, 29, 8)),  # a leap year
        (datetime(2026, 12, 31, 9), datetime(2027, 1, 31, 8)),
    ],
)
def test_monthly_on_day_31(after: datetime, expected: datetime) -> None:
    assert schedules.next_occurrence(_schedule(2, month_day=31), after) == expected


def test_monthly_on_an_ordinary_day() -> None:
    fifteenth = _schedule(2, month_day=15)

    assert schedules.next_occurrence(fifteenth, datetime(2026, 2, 15, 7)) == datetime(
        2026, 2, 15, 8
    )
    assert schedules.next_occurrence(fifteenth, datetime(2026, 2, 15, 8)) == datetime(
        2026, 3, 15, 8
    )


def test_a_run_missed_for_three_days_gives_the_latest_occurrence_then_the_next() -> None:
    # Due on 1 June at 08:00; the scheduler was down until 4 June at 09:00.
    occurrence, following = schedules.due(DAILY, datetime(2026, 6, 4, 9))

    assert occurrence == datetime(2026, 6, 4, 8)
    assert following == datetime(2026, 6, 5, 8)


def test_due_at_the_occurrence_itself() -> None:
    weekly = _schedule(1, weekdays=MONDAY)

    assert schedules.due(weekly, datetime(2026, 6, 8, 8)) == (
        datetime(2026, 6, 8, 8),
        datetime(2026, 6, 15, 8),
    )


def test_a_missed_monthly_run_on_the_last_day() -> None:
    last = _schedule(2, month_day=31)

    assert schedules.due(last, datetime(2026, 3, 2, 12)) == (
        datetime(2026, 2, 28, 8),
        datetime(2026, 3, 31, 8),
    )
