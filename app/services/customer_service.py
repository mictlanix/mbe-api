from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.schemas.customer import CustomerCreate, CustomerUpdate


async def list_customers(
    db: AsyncSession,
    *,
    search: str | None = None,
    disabled: bool | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Customer], int]:
    base = select(Customer)
    count_q = select(func.count()).select_from(Customer)

    if search:
        term = f"%{search}%"
        condition = or_(
            Customer.code.ilike(term),
            Customer.name.ilike(term),
            Customer.zone.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    if disabled is not None:
        base = base.where(Customer.disabled == disabled)
        count_q = count_q.where(Customer.disabled == disabled)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_customer(db: AsyncSession, customer_id: int) -> Customer | None:
    return await db.get(Customer, customer_id)


async def create_customer(db: AsyncSession, data: CustomerCreate) -> Customer:
    customer = Customer(
        code=data.code,
        name=data.name,
        zone=data.zone,
        credit_limit=data.credit_limit,
        credit_days=data.credit_days,
        price_list=data.price_list,
        shipping=data.shipping,
        shipping_required_document=data.shipping_required_document,
        salesperson=data.salesperson,
        comment=data.comment,
        disabled=False,
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


async def update_customer(db: AsyncSession, customer: Customer, data: CustomerUpdate) -> Customer:
    if data.code is not None:
        customer.code = data.code
    if data.name is not None:
        customer.name = data.name
    if data.zone is not None:
        customer.zone = data.zone
    if data.credit_limit is not None:
        customer.credit_limit = data.credit_limit
    if data.credit_days is not None:
        customer.credit_days = data.credit_days
    if data.price_list is not None:
        customer.price_list = data.price_list
    if data.shipping is not None:
        customer.shipping = data.shipping
    if data.shipping_required_document is not None:
        customer.shipping_required_document = data.shipping_required_document
    if data.salesperson is not None:
        customer.salesperson = data.salesperson
    if data.disabled is not None:
        customer.disabled = data.disabled
    if data.comment is not None:
        customer.comment = data.comment
    await db.commit()
    await db.refresh(customer)
    return customer


async def delete_customer(db: AsyncSession, customer: Customer, default_customer_id: int) -> None:
    if customer.customer_id == default_customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete the system default customer",
        )
    await db.delete(customer)
    await db.commit()
