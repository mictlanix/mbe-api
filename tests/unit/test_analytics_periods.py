"""Spec 038, T047: a saved view's relative period, resolved against the day it runs (R14).

Weeks are ISO weeks (Monday to Sunday); the "this" periods run to date; the "last" periods are
whole. The edge dates: a month's 31st after a 28-day February, a leap day, New Year's Day, a
Sunday, and the last days of ISO week 53 of 2026 (28 December 2026 to 3 January 2027).
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.analytics import AnalyticsPeriod, RelativePeriod
from app.services.analytics import periods

R = RelativePeriod

CASES = [
    # 31 March 2026, a Tuesday in ISO week 14, after a 28-day February.
    (date(2026, 3, 31), R.TODAY, (date(2026, 3, 31), date(2026, 3, 31))),
    (date(2026, 3, 31), R.YESTERDAY, (date(2026, 3, 30), date(2026, 3, 30))),
    (date(2026, 3, 31), R.THIS_WEEK, (date(2026, 3, 30), date(2026, 3, 31))),
    (date(2026, 3, 31), R.THIS_MONTH, (date(2026, 3, 1), date(2026, 3, 31))),
    (date(2026, 3, 31), R.THIS_QUARTER, (date(2026, 1, 1), date(2026, 3, 31))),
    (date(2026, 3, 31), R.THIS_YEAR, (date(2026, 1, 1), date(2026, 3, 31))),
    (date(2026, 3, 31), R.LAST_WEEK, (date(2026, 3, 23), date(2026, 3, 29))),
    (date(2026, 3, 31), R.LAST_MONTH, (date(2026, 2, 1), date(2026, 2, 28))),
    (date(2026, 3, 31), R.LAST_QUARTER, (date(2025, 10, 1), date(2025, 12, 31))),
    (date(2026, 3, 31), R.LAST_YEAR, (date(2025, 1, 1), date(2025, 12, 31))),
    # 29 February 2028, a leap day.
    (date(2028, 2, 29), R.YESTERDAY, (date(2028, 2, 28), date(2028, 2, 28))),
    (date(2028, 2, 29), R.THIS_WEEK, (date(2028, 2, 28), date(2028, 2, 29))),
    (date(2028, 2, 29), R.THIS_MONTH, (date(2028, 2, 1), date(2028, 2, 29))),
    (date(2028, 2, 29), R.THIS_QUARTER, (date(2028, 1, 1), date(2028, 2, 29))),
    (date(2028, 2, 29), R.LAST_MONTH, (date(2028, 1, 1), date(2028, 1, 31))),
    (date(2028, 2, 29), R.LAST_QUARTER, (date(2027, 10, 1), date(2027, 12, 31))),
    (date(2028, 2, 29), R.LAST_YEAR, (date(2027, 1, 1), date(2027, 12, 31))),
    # 1 March 2028: last month is the leap February.
    (date(2028, 3, 1), R.LAST_MONTH, (date(2028, 2, 1), date(2028, 2, 29))),
    # 1 January 2026, a Thursday in ISO week 1 of 2026, which began on 29 December 2025.
    (date(2026, 1, 1), R.TODAY, (date(2026, 1, 1), date(2026, 1, 1))),
    (date(2026, 1, 1), R.YESTERDAY, (date(2025, 12, 31), date(2025, 12, 31))),
    (date(2026, 1, 1), R.THIS_WEEK, (date(2025, 12, 29), date(2026, 1, 1))),
    (date(2026, 1, 1), R.THIS_MONTH, (date(2026, 1, 1), date(2026, 1, 1))),
    (date(2026, 1, 1), R.THIS_QUARTER, (date(2026, 1, 1), date(2026, 1, 1))),
    (date(2026, 1, 1), R.THIS_YEAR, (date(2026, 1, 1), date(2026, 1, 1))),
    (date(2026, 1, 1), R.LAST_WEEK, (date(2025, 12, 22), date(2025, 12, 28))),
    (date(2026, 1, 1), R.LAST_MONTH, (date(2025, 12, 1), date(2025, 12, 31))),
    (date(2026, 1, 1), R.LAST_QUARTER, (date(2025, 10, 1), date(2025, 12, 31))),
    (date(2026, 1, 1), R.LAST_YEAR, (date(2025, 1, 1), date(2025, 12, 31))),
    # Sunday 8 November 2026 closes ISO week 45.
    (date(2026, 11, 8), R.THIS_WEEK, (date(2026, 11, 2), date(2026, 11, 8))),
    (date(2026, 11, 8), R.LAST_WEEK, (date(2026, 10, 26), date(2026, 11, 1))),
    (date(2026, 11, 8), R.THIS_QUARTER, (date(2026, 10, 1), date(2026, 11, 8))),
    (date(2026, 11, 8), R.LAST_QUARTER, (date(2026, 7, 1), date(2026, 9, 30))),
    # Sunday 3 January 2027 is the last day of ISO week 53 of 2026; Monday the 4th opens week 1.
    (date(2027, 1, 3), R.THIS_WEEK, (date(2026, 12, 28), date(2027, 1, 3))),
    (date(2027, 1, 3), R.LAST_WEEK, (date(2026, 12, 21), date(2026, 12, 27))),
    (date(2027, 1, 3), R.THIS_YEAR, (date(2027, 1, 1), date(2027, 1, 3))),
    (date(2027, 1, 4), R.THIS_WEEK, (date(2027, 1, 4), date(2027, 1, 4))),
    (date(2027, 1, 4), R.LAST_WEEK, (date(2026, 12, 28), date(2027, 1, 3))),
]


@pytest.mark.parametrize(('today', 'key', 'expected'), CASES, ids=lambda v: str(v))
def test_a_relative_period_resolves_against_today(
    today: date, key: RelativePeriod, expected: tuple[date, date]
) -> None:
    assert periods.resolve(AnalyticsPeriod(key=key), today) == expected


def test_every_relative_period_is_covered() -> None:
    covered = {key for _, key, _ in CASES} | {R.LAST_N_DAYS, R.FIXED}
    assert covered == set(RelativePeriod)


@pytest.mark.parametrize('days', [1, 2, 7, 30, 365, 366, 3650])
@pytest.mark.parametrize('today', [date(2026, 3, 31), date(2028, 2, 29), date(2026, 1, 1)])
def test_the_last_n_days_end_today(days: int, today: date) -> None:
    period = AnalyticsPeriod(key=R.LAST_N_DAYS, days=days)

    start, end = periods.resolve(period, today)

    assert end == today
    assert (end - start).days + 1 == days


@pytest.mark.parametrize('days', [None, 0, 3651])
def test_the_last_n_days_need_between_1_and_3650_days(days: int | None) -> None:
    with pytest.raises(ValidationError):
        AnalyticsPeriod(key=R.LAST_N_DAYS, days=days)


def test_a_fixed_period_is_its_own_dates_whatever_the_day() -> None:
    period = AnalyticsPeriod(key=R.FIXED, start=date(2026, 2, 1), end=date(2026, 2, 28))

    for today in (date(2026, 3, 31), date(2028, 2, 29)):
        assert periods.resolve(period, today) == (date(2026, 2, 1), date(2026, 2, 28))


def test_a_fixed_period_of_one_day_is_allowed() -> None:
    day = date(2026, 2, 1)
    assert periods.resolve(AnalyticsPeriod(key=R.FIXED, start=day, end=day), day) == (day, day)


@pytest.mark.parametrize(
    'dates',
    [
        {'start': date(2026, 3, 1), 'end': date(2026, 2, 28)},
        {'start': date(2026, 3, 1)},
        {'end': date(2026, 3, 1)},
        {},
    ],
)
def test_a_fixed_period_needs_a_start_not_after_its_end(dates: dict) -> None:
    with pytest.raises(ValidationError):
        AnalyticsPeriod(key=R.FIXED, **dates)


def test_the_week_starts_on_monday() -> None:
    monday = date(2026, 11, 2)
    for offset in range(7):
        today = monday + timedelta(days=offset)
        assert periods.resolve(AnalyticsPeriod(key=R.THIS_WEEK), today) == (monday, today)
