"""The business clock (#228).

Every datetime the API stores, returns or accepts is naive wall-clock time in
`settings.business_timezone` — the zone the legacy monolith sharing the database writes. These are
the only places that read the current time or convert an offset, so the host's timezone never
leaks into a stored value. JWT expiry is the one exception: it is an absolute UTC instant, never
stored, and stays in `app/core/security.py`.
"""

from datetime import date, datetime

from app.core.config import settings


def local_now() -> datetime:
    """The current wall-clock time in the business timezone, naive like every stored column."""
    return datetime.now(settings.business_timezone).replace(tzinfo=None)


def local_today() -> date:
    """Today's date in the business timezone."""
    return local_now().date()


def to_local(value: datetime) -> datetime:
    """An offset-aware value converted to business wall-clock time; a naive one is already local."""
    if value.tzinfo is None:
        return value
    return value.astimezone(settings.business_timezone).replace(tzinfo=None)
