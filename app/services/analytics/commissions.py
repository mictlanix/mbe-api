"""Commissions (spec 038, research R11): spec 025's lines, grouped in Python.

Spec 025 owns the rule. Each month the period touches is read with `commission_service.read_period`:
a closed or paid month's stored lines, an open month's live computation (`provisional`). Nothing
here computes an amount; the lines are only scoped, filtered and summed, so a figure always equals
what spec 025 shows for its months. A line belongs to its month as a whole, so a period starting or
ending mid-month reads the whole month.

A line's agent is its `salesperson`, the agent who earns it. Its facility is that agent's user's
(research R17): the lowest one when the agent has several users, none when they have no user.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserSettings
from app.schemas.analytics import DimensionKey, Grain
from app.services import commission_service
from app.services.analytics import facts

PROVISIONAL, CLOSED = 'provisional', 'closed'


@dataclass(frozen=True)
class Entry:
    """A spec 025 line (stored or computed: both carry the same attributes) and where it falls."""

    month: date
    status: str
    facility: int | None
    line: Any


def months(start: date, end: date) -> list[date]:
    """The first day of every month the period touches."""
    found, current = [], start.replace(day=1)
    while current <= end:
        found.append(current)
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)
    return found


async def agent_facilities(db: AsyncSession) -> dict[int, int]:
    """Each employee with a user, and that user's facility (the lowest, if several)."""
    rows = await db.execute(
        select(User.employee_id, func.min(UserSettings.facility_id))
        .join(UserSettings, UserSettings.user_id == User.user_id)
        .group_by(User.employee_id)
    )
    return dict(rows.all())


async def entries(db: AsyncSession, start: date, end: date, facility: int | None) -> list[Entry]:
    """Every line of the months the period touches, in the facility (None: all)."""
    places = await agent_facilities(db)
    found: list[Entry] = []
    for month in months(start, end):
        view = await commission_service.read_period(db, month.year, month.month)
        status = CLOSED if view.final else PROVISIONAL
        for line in view.lines:
            place = places.get(line.salesperson)
            if facility is None or place == facility:
                found.append(Entry(month, status, place, line))
    return found


def key(entry: Entry, dimension: DimensionKey, grain: Grain = Grain.MONTH) -> Any:
    """The line's value of a dimension. By date it is its month, or the quarter or year holding
    it; the engine raises a finer grain to the month (R11)."""
    line = entry.line
    match dimension:
        case DimensionKey.DATE:
            month = entry.month
            if grain is Grain.QUARTER:
                return month.replace(month=(month.month - 1) // 3 * 3 + 1)
            return month.replace(month=1) if grain is Grain.YEAR else month
        case DimensionKey.FACILITY:
            return entry.facility
        case DimensionKey.SALESPERSON:
            return line.salesperson
        case DimensionKey.CUSTOMER:
            return line.customer
        case DimensionKey.PRODUCT:
            return line.product
        case DimensionKey.LABEL:
            return line.label
        case DimensionKey.COMMISSION_PARTICIPATION:
            return line.participation
        case DimensionKey.COMMISSION_STATUS:
            return entry.status
    raise ValueError(f'commissions have no {dimension.value}')


def label(entry: Entry, dimension: DimensionKey) -> str | None:
    """The names the line carries: its commission label's (not a product label, which the engine
    would look up) and its participation's."""
    match dimension:
        case DimensionKey.LABEL:
            return entry.line.label_name
        case DimensionKey.COMMISSION_PARTICIPATION:
            return entry.line.participation_name
    return None


def selected(
    entries: Iterable[Entry], filters: Mapping[DimensionKey, Sequence[Any]], grain: Grain
) -> list[Entry]:
    return [e for e in entries if all(key(e, d, grain) in values for d, values in filters.items())]


def cents(value: Any) -> int:
    return int(Decimal(str(value)) * 100)


def group(entries: Iterable[Entry], slice_: facts.Slice) -> list[dict[str, Any]]:
    """Grouped rows like `facts.query`'s: `k`/`l` per dimension, then the commission, the base
    (price × quantity, negative for a refund) and the line count, in cents and units."""
    groups: dict[tuple, dict[str, Any]] = {}
    for entry in selected(entries, slice_.filters, slice_.grain):
        values = tuple(key(entry, d, slice_.grain) for d in slice_.dimensions)
        row = groups.get(values)
        if row is None:
            row = {f'k{i}': value for i, value in enumerate(values)}
            row |= {f'l{i}': label(entry, d) for i, d in enumerate(slice_.dimensions)}
            row |= {'commission_c': 0, 'base_c': 0, 'lines': 0}
            groups[values] = row
        row['commission_c'] += cents(entry.line.commission)
        row['base_c'] += cents(entry.line.total)
        row['lines'] += 1
    return list(groups.values())


async def fact(db: AsyncSession, slice_: facts.Slice) -> list[dict[str, Any]]:
    """The commission fact (R11), a Python flow fact of the engine."""
    return group(await entries(db, slice_.start, slice_.end, slice_.facility), slice_)
