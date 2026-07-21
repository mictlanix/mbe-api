from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Employee
from app.models.customer import Customer
from app.models.product import PriceList
from app.schemas.customer import CustomerCreate, CustomerUpdate


async def _attach_customer_relations(db: AsyncSession, customers: Sequence[Customer]) -> None:
    if not customers:
        return
    list_ids = {c.price_list for c in customers}
    price_lists = (
        (await db.execute(select(PriceList).where(PriceList.price_list_id.in_(list_ids))))
        .scalars()
        .all()
    )
    lists_by_id = {pl.price_list_id: pl for pl in price_lists}

    salesperson_ids = {c.salesperson for c in customers if c.salesperson is not None}
    employees_by_id: dict[int, Employee] = {}
    if salesperson_ids:
        employees = (
            (await db.execute(select(Employee).where(Employee.employee_id.in_(salesperson_ids))))
            .scalars()
            .all()
        )
        employees_by_id = {e.employee_id: e for e in employees}

    for c in customers:
        c.__dict__['price_list'] = lists_by_id.get(c.price_list)
        c.__dict__['salesperson'] = (
            employees_by_id.get(c.salesperson) if c.salesperson is not None else None
        )


async def list_customers(
    db: AsyncSession,
    *,
    search: str | None = None,
    status: EntityStatus | None = None,
    price_list: int | None = None,
    salesperson: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Customer], int]:
    base = select(Customer)
    count_q = select(func.count()).select_from(Customer)

    if search:
        term = f'%{search}%'
        condition = or_(
            Customer.code.ilike(term),
            Customer.name.ilike(term),
            Customer.zone.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    if status is not None:
        base = base.where(Customer.status == status)
        count_q = count_q.where(Customer.status == status)
    if price_list is not None:
        base = base.where(Customer.price_list == price_list)
        count_q = count_q.where(Customer.price_list == price_list)
    if salesperson is not None:
        base = base.where(Customer.salesperson == salesperson)
        count_q = count_q.where(Customer.salesperson == salesperson)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_customer_relations(db, items)
    return items, total


async def get_customer(db: AsyncSession, customer_id: int) -> Customer | None:
    customer = await db.get(Customer, customer_id)
    if customer is None:
        return None
    await _attach_customer_relations(db, [customer])
    return customer


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
        status=data.status,
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    await _attach_customer_relations(db, [customer])
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
    if data.status is not None:
        customer.status = data.status
    if data.comment is not None:
        customer.comment = data.comment
    await db.commit()
    await db.refresh(customer)
    await _attach_customer_relations(db, [customer])
    return customer


async def delete_customer(db: AsyncSession, customer: Customer, default_customer_id: int) -> None:
    if customer.customer_id == default_customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Cannot delete the system default customer',
        )
    await db.delete(customer)
    await db.commit()
