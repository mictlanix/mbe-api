"""Mechanics shared by every sales document: folio assignment and the editability guard.

Sales orders, quotes and refunds all number themselves per facility and all freeze on completion.
Writing that three times would risk three subtly different behaviours; folio assignment in
particular has no database constraint behind it, so a divergent copy would break SC-005 silently.
"""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Facility


async def assign_folio(db: AsyncSession, model: type, *, facility: int) -> int:
    """Return the next `serial` for `facility`, serialising concurrent callers.

    There is no unique index on `(facility, serial)` on any document table (research R1), so
    `MAX(serial) + 1` on its own would hand the same number to two simultaneous confirmations.
    Locking the owning facility row first makes them queue instead. InnoDB holds the lock until the
    surrounding transaction commits, so the caller must run this inside the same transaction that
    writes the folio.
    """
    locked = (
        await db.execute(
            select(Facility.facility_id)
            .where(Facility.facility_id == facility)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if locked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Facility not found')

    highest = (
        await db.execute(select(func.max(model.serial)).where(model.facility == facility))
    ).scalar_one_or_none()

    return (highest or 0) + 1


def assert_editable(document: object) -> None:
    """Refuse a document that has been committed or retired.

    Confirmation is the point of no return: after it, state changes are recorded as their own
    auditable records rather than by editing the document (SC-007).
    """
    if getattr(document, 'cancelled', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Document is cancelled and can no longer be edited',
        )

    if getattr(document, 'completed', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Document is completed and can no longer be edited',
        )
