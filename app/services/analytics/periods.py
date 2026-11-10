"""A saved view's relative period, resolved against a business day (spec 038, R14, FR-151).

The "this" periods run to date, the "last" periods are whole, weeks are ISO weeks (Monday to
Sunday), and the last N days end today. `today` is the business date (`local_today()`), so a view
opened just after midnight in the business timezone already resolves against the new day.
"""

from datetime import date, timedelta

from app.schemas.analytics import AnalyticsPeriod, RelativePeriod

ONE_DAY = timedelta(days=1)


def _week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _quarter(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def resolve(period: AnalyticsPeriod, today: date) -> tuple[date, date]:
    """`(start, end)`, both inclusive."""
    match period.key:
        case RelativePeriod.TODAY:
            return today, today
        case RelativePeriod.YESTERDAY:
            return today - ONE_DAY, today - ONE_DAY
        case RelativePeriod.THIS_WEEK:
            return _week(today), today
        case RelativePeriod.THIS_MONTH:
            return today.replace(day=1), today
        case RelativePeriod.THIS_QUARTER:
            return _quarter(today), today
        case RelativePeriod.THIS_YEAR:
            return date(today.year, 1, 1), today
        case RelativePeriod.LAST_WEEK:
            start = _week(today) - timedelta(days=7)
            return start, start + timedelta(days=6)
        case RelativePeriod.LAST_MONTH:
            end = today.replace(day=1) - ONE_DAY
            return end.replace(day=1), end
        case RelativePeriod.LAST_QUARTER:
            end = _quarter(today) - ONE_DAY
            return _quarter(end), end
        case RelativePeriod.LAST_YEAR:
            return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
        case RelativePeriod.LAST_N_DAYS:
            return today - timedelta(days=period.days - 1), today
        case RelativePeriod.FIXED:
            return period.start, period.end
