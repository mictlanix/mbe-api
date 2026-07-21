from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Facility, PaymentMethodOption, Warehouse
from app.schemas.core import PaymentMethodOptionCreate, PaymentMethodOptionUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, options: Sequence[PaymentMethodOption]) -> None:
    if not options:
        return
    facilities_by_id = await batch_fetch(
        db, Facility, Facility.facility_id, (o.facility for o in options)
    )
    warehouses_by_id = await batch_fetch(
        db, Warehouse, Warehouse.warehouse_id, (o.warehouse for o in options)
    )
    for o in options:
        # Written under a separate key: the mapped column is shared through the session
        # identity map, so overwriting it corrupts every reader of the raw FK (#95, #104).
        o.__dict__['facility_detail'] = facilities_by_id.get(o.facility)
        o.__dict__['warehouse_detail'] = (
            warehouses_by_id.get(o.warehouse) if o.warehouse is not None else None
        )


async def list_payment_method_options(
    db: AsyncSession,
    *,
    facility: int | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[PaymentMethodOption], int]:
    base = select(PaymentMethodOption)
    count_q = select(func.count()).select_from(PaymentMethodOption)

    if facility is not None:
        base = base.where(PaymentMethodOption.facility == facility)
        count_q = count_q.where(PaymentMethodOption.facility == facility)
    if status is not None:
        base = base.where(PaymentMethodOption.status == status)
        count_q = count_q.where(PaymentMethodOption.status == status)

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
        facility=data.facility,
        warehouse=data.warehouse,
        name=data.name,
        number_of_payments=data.number_of_payments,
        display_on_ticket=data.display_on_ticket,
        payment_method=data.payment_method,
        commission=data.commission,
        status=data.status,
    )
    db.add(pmo)
    await db.commit()
    await db.refresh(pmo)
    await _attach_relations(db, [pmo])
    return pmo


async def update_payment_method_option(
    db: AsyncSession, pmo: PaymentMethodOption, data: PaymentMethodOptionUpdate
) -> PaymentMethodOption:
    if data.facility is not None:
        pmo.facility = data.facility
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
    if data.status is not None:
        pmo.status = data.status
    await db.commit()
    await db.refresh(pmo)
    await _attach_relations(db, [pmo])
    return pmo


async def delete_payment_method_option(db: AsyncSession, pmo: PaymentMethodOption) -> None:
    await db.delete(pmo)
    await db.commit()
