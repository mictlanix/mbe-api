from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import PaymentMethodOption
from app.schemas.core import PaymentMethodOptionCreate, PaymentMethodOptionUpdate


async def list_payment_method_options(
    db: AsyncSession,
    *,
    store: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[PaymentMethodOption], int]:
    base = select(PaymentMethodOption)
    count_q = select(func.count()).select_from(PaymentMethodOption)

    if store is not None:
        base = base.where(PaymentMethodOption.store == store)
        count_q = count_q.where(PaymentMethodOption.store == store)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_payment_method_option(
    db: AsyncSession, payment_method_option_id: int
) -> PaymentMethodOption | None:
    return await db.get(PaymentMethodOption, payment_method_option_id)


async def create_payment_method_option(
    db: AsyncSession, data: PaymentMethodOptionCreate
) -> PaymentMethodOption:
    pmo = PaymentMethodOption(
        store=data.store,
        warehouse=data.warehouse,
        name=data.name,
        number_of_payments=data.number_of_payments,
        display_on_ticket=data.display_on_ticket,
        payment_method=data.payment_method,
        commission=data.commission,
        enabled=data.enabled,
    )
    db.add(pmo)
    await db.commit()
    await db.refresh(pmo)
    return pmo


async def update_payment_method_option(
    db: AsyncSession, pmo: PaymentMethodOption, data: PaymentMethodOptionUpdate
) -> PaymentMethodOption:
    if data.store is not None:
        pmo.store = data.store
    if data.warehouse is not None:
        pmo.warehouse = data.warehouse
    if data.name is not None:
        pmo.name = data.name
    if data.number_of_payments is not None:
        pmo.number_of_payments = data.number_of_payments
    if data.display_on_ticket is not None:
        pmo.display_on_ticket = data.display_on_ticket
    if data.payment_method is not None:
        pmo.payment_method = data.payment_method
    if data.commission is not None:
        pmo.commission = data.commission
    if data.enabled is not None:
        pmo.enabled = data.enabled
    await db.commit()
    await db.refresh(pmo)
    return pmo


async def delete_payment_method_option(db: AsyncSession, pmo: PaymentMethodOption) -> None:
    await db.delete(pmo)
    await db.commit()
