"""Mechanics shared by every sales document: folio assignment, editability, the salesperson.

Sales orders, quotes, delivery orders, refunds, supplier payments and customer payments all number
themselves per facility. One numbering rule serves them all (spec 035, research R2): a step that
numbers commits away its opening snapshot, then locks in this order: cash session, its own
documents, their lines (`FOR SHARE`), the facility rows ascending (`lock_facility`), the warehouses
ascending (`inventory_documents.lock_warehouses`). Only then does it read, so the plain
`MAX(serial)` in `assign_folio` sees every earlier holder's commit.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.models.core import Facility


async def lock_facility(db: AsyncSession, facility: int) -> None:
    """Take the facility row `FOR UPDATE`: the numbering lock (spec 035, research R2).

    Steps that number take it after their own document rows and before any warehouse, so that no
    path holds a warehouse and then waits on a facility. A header insert's foreign-key check takes
    a share lock on the facility before its lines take warehouses.
    """
    locked = (
        await db.execute(
            select(Facility.facility_id).where(Facility.facility_id == facility).with_for_update()
        )
    ).scalar_one_or_none()

    if locked is None:
        raise not_found(Facility, facility)


async def assign_folio(db: AsyncSession, model: type, *, facility: int) -> int:
    """Return the next `serial` for `facility`, serialising concurrent callers.

    `MAX(serial) + 1` on its own would hand the same number to two simultaneous confirmations.
    Locking the owning facility row first makes them queue instead, and InnoDB holds the lock until
    the surrounding transaction commits, so the caller must write the folio in this transaction.
    Most document tables also carry a unique `(facility, serial)` index; customer payments and
    purchase requests don't, because of the current system's duplicates.

    Precondition: no ordinary read in the transaction before the facility lock. Under REPEATABLE
    READ the snapshot opens at the first plain read, and a snapshot older than the lock misses the
    serial the previous holder committed (mictlanix/mbe#45). The lock is a no-op when the step
    already holds it.
    """
    await lock_facility(db, facility)

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
        raise DomainError(
            ErrorCode.DOCUMENT_NOT_EDITABLE,
            'Document is cancelled and can no longer be edited',
            {'status': 'cancelled'},
        )

    if getattr(document, 'completed', False):
        raise DomainError(
            ErrorCode.DOCUMENT_NOT_EDITABLE,
            'Document is completed and can no longer be edited',
            {'status': 'completed'},
        )


def default_salesperson(customer_salesperson: int | None, caller_employee: int) -> int:
    """FR-030 — the customer's assigned salesperson, falling back to whoever is selling.

    Shared rather than written twice (#195): `create_order` spelled it `or customer.salesperson
    or employee`, which differs only for employee id 0 and does not say what it means.

    Not used on update, where the order already has a rep to fall back to — see `update_order`.
    """
    return customer_salesperson if customer_salesperson is not None else caller_employee
