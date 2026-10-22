"""A supplier's agreements — the date ranges its commercial terms apply for (#13).

`supplier_agreement` is a child of `supplier` and means nothing without it, so every lookup is
scoped to the supplier in the path: an agreement belonging to another supplier is reported as
absent, never touched.

Agreements of one supplier may overlap, and an agreement may start and end on the same day.
"""

from collections.abc import Sequence
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import SupplierAgreement
from app.schemas.supplier import SupplierAgreementCreate, SupplierAgreementUpdate


def _assert_period(start: date, end: date) -> None:
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='End must not be before start',
        )


async def list_agreements(db: AsyncSession, supplier_id: int) -> Sequence[SupplierAgreement]:
    return (
        (
            await db.execute(
                select(SupplierAgreement)
                .where(SupplierAgreement.supplier == supplier_id)
                .order_by(SupplierAgreement.start, SupplierAgreement.supplier_agreement_id)
            )
        )
        .scalars()
        .all()
    )


async def get_agreement(
    db: AsyncSession, supplier_id: int, agreement_id: int
) -> SupplierAgreement | None:
    agreement = await db.get(SupplierAgreement, agreement_id)
    if agreement is None or agreement.supplier != supplier_id:
        return None
    return agreement


async def create_agreement(
    db: AsyncSession, supplier_id: int, data: SupplierAgreementCreate
) -> SupplierAgreement:
    _assert_period(data.start, data.end)
    agreement = SupplierAgreement(
        supplier=supplier_id, start=data.start, end=data.end, comment=data.comment
    )
    db.add(agreement)
    await db.commit()
    await db.refresh(agreement)
    return agreement


async def update_agreement(
    db: AsyncSession, agreement: SupplierAgreement, data: SupplierAgreementUpdate
) -> SupplierAgreement:
    # The rule is on the pair, so a request that sends only one side is checked against the other.
    start = data.start if data.start is not None else agreement.start
    end = data.end if data.end is not None else agreement.end
    _assert_period(start, end)
    agreement.start = start
    agreement.end = end
    if data.comment is not None:
        agreement.comment = data.comment
    await db.commit()
    await db.refresh(agreement)
    return agreement


async def delete_agreement(db: AsyncSession, agreement: SupplierAgreement) -> None:
    await db.delete(agreement)
    await db.commit()
