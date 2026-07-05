from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import PaymentMethodOption, Store, Warehouse
from app.schemas.core import PaymentMethodOptionCreate, PaymentMethodOptionUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, options: Sequence[PaymentMethodOption]) -> None:
    if not options:
        return
    stores_by_id = await batch_fetch(db, Store, Store.store_id, (o.store for o in options))
    warehouses_by_id = await batch_fetch(
        db, Warehouse, Warehouse.warehouse_id, (o.warehouse for o in options)
    )
    for o in options:
        o.__dict__["store"] = stores_by_id.get(o.store)
        o.__dict__["warehouse"] = (
            warehouses_by_id.get(o.warehouse) if o.warehouse is not None else None
        )


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
    await _attach_relations(db, items)
    return items, total


async def get_payment_method_option(
    db: AsyncSession, payment_method_option_id: int
) -> PaymentMethodOption | None:
    pmo = await db.get(PaymentMethodOption, payment_method_option_id)
    if pmo is None:
        return None
    await _attach_relations(db, [pmo])
    return pmo


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
    await _attach_relations(db, [pmo])
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
    await _attach_relations(db, [pmo])
    return pmo


async def delete_payment_method_option(db: AsyncSession, pmo: PaymentMethodOption) -> None:
    await db.delete(pmo)
    await db.commit()
