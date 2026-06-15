from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import TaxpayerRecipient
from app.schemas.customer import TaxpayerRecipientCreate, TaxpayerRecipientUpdate


async def list_taxpayer_recipients(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[TaxpayerRecipient], int]:
    base = select(TaxpayerRecipient)
    count_q = select(func.count()).select_from(TaxpayerRecipient)

    if search:
        term = f"%{search}%"
        condition = or_(
            TaxpayerRecipient.taxpayer_recipient_id.ilike(term),
            TaxpayerRecipient.name.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_taxpayer_recipient(db: AsyncSession, rfc: str) -> TaxpayerRecipient | None:
    return await db.get(TaxpayerRecipient, rfc)


async def create_taxpayer_recipient(
    db: AsyncSession, data: TaxpayerRecipientCreate
) -> TaxpayerRecipient:
    tr = TaxpayerRecipient(
        taxpayer_recipient_id=data.taxpayer_recipient_id,
        name=data.name,
        email=data.email,
        postal_code=data.postal_code,
        regime=data.regime,
    )
    db.add(tr)
    await db.commit()
    await db.refresh(tr)
    return tr


async def update_taxpayer_recipient(
    db: AsyncSession, tr: TaxpayerRecipient, data: TaxpayerRecipientUpdate
) -> TaxpayerRecipient:
    if data.name is not None:
        tr.name = data.name
    if data.email is not None:
        tr.email = data.email
    if data.postal_code is not None:
        tr.postal_code = data.postal_code
    if data.regime is not None:
        tr.regime = data.regime
    await db.commit()
    await db.refresh(tr)
    return tr


async def delete_taxpayer_recipient(db: AsyncSession, tr: TaxpayerRecipient) -> None:
    await db.delete(tr)
    await db.commit()
