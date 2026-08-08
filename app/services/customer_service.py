from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Address, Contact, Employee
from app.models.customer import (
    Customer,
    TaxpayerRecipient,
    customer_address,
    customer_contact,
    customer_taxpayer,
)
from app.models.product import PriceList
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.services import taxpayer_recipient_service
from app.services.references import assert_not_referenced


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
        # Written under a separate key: the mapped column is shared through the session
        # identity map, so overwriting it corrupts every reader of the raw FK (#95, #104).
        c.__dict__['price_list_detail'] = lists_by_id.get(c.price_list)
        c.__dict__['salesperson_detail'] = (
            employees_by_id.get(c.salesperson) if c.salesperson is not None else None
        )


async def _get_links(
    db: AsyncSession, customer_id: int
) -> tuple[list[Address], list[Contact], list[TaxpayerRecipient]]:
    """A customer's linked addresses, contacts and taxpayers (#132, #133, #150).

    `customer_address`, `customer_contact` and `customer_taxpayer` are real junction tables with
    real rows that nothing exposed: a client needing "one of this customer's addresses" as a
    delivery destination had to fall back to an unfiltered global address search, a per-destination
    contact had nowhere to go but a delivery order's free-text comment, and a customer's tax
    registration could not be recorded at all — both records could be created and nothing could
    associate them.

    Attached to the detail response only, never to `CustomerListItem` — a page of customers must
    not cost three queries per row.
    """
    addresses = (
        (
            await db.execute(
                select(Address)
                .join(customer_address, customer_address.c['address'] == Address.address_id)
                .where(customer_address.c['customer'] == customer_id)
                .order_by(Address.address_id)
            )
        )
        .scalars()
        .all()
    )
    contacts = (
        (
            await db.execute(
                select(Contact)
                .join(customer_contact, customer_contact.c['contact'] == Contact.contact_id)
                .where(customer_contact.c['customer'] == customer_id)
                .order_by(Contact.contact_id)
            )
        )
        .scalars()
        .all()
    )
    taxpayers = (
        (
            await db.execute(
                select(TaxpayerRecipient)
                .join(
                    customer_taxpayer,
                    customer_taxpayer.c['taxpayer_recipient']
                    == TaxpayerRecipient.taxpayer_recipient_id,
                )
                .where(customer_taxpayer.c['customer'] == customer_id)
                .order_by(TaxpayerRecipient.taxpayer_recipient_id)
            )
        )
        .scalars()
        .all()
    )
    return list(addresses), list(contacts), list(taxpayers)


async def _attach_links(db: AsyncSession, customer: Customer) -> None:
    addresses, contacts, taxpayers = await _get_links(db, customer.customer_id)
    # The recipients carry their own FK expansions, and `TaxpayerRecipientResponse` cannot be
    # validated without them, so they go through the owning service rather than being expanded here.
    await taxpayer_recipient_service.attach_relations(db, taxpayers)
    # Written under a separate key for consistency with the FK details above, though these three
    # shadow no mapped column.
    customer.__dict__['addresses'] = addresses
    customer.__dict__['contacts'] = contacts
    customer.__dict__['taxpayers'] = taxpayers


async def _set_links(
    db: AsyncSession,
    customer_id: int,
    *,
    addresses: list[int] | None,
    contacts: list[int] | None,
    taxpayers: list[str] | None,
) -> None:
    """Replace-all, and only for a collection the caller actually sent.

    `None` means "leave the links alone", which is what makes an ordinary `PUT` that does not
    mention addresses safe. An empty list is a real instruction: unlink everything.
    """
    if addresses is not None:
        await db.execute(
            delete(customer_address).where(customer_address.c['customer'] == customer_id)
        )
        if addresses:
            await db.execute(
                insert(customer_address),
                [{'customer': customer_id, 'address': a} for a in addresses],
            )
    if contacts is not None:
        await db.execute(
            delete(customer_contact).where(customer_contact.c['customer'] == customer_id)
        )
        if contacts:
            await db.execute(
                insert(customer_contact),
                [{'customer': customer_id, 'contact': c} for c in contacts],
            )
    if taxpayers is not None:
        await db.execute(
            delete(customer_taxpayer).where(customer_taxpayer.c['customer'] == customer_id)
        )
        if taxpayers:
            await db.execute(
                insert(customer_taxpayer),
                [{'customer': customer_id, 'taxpayer_recipient': t} for t in taxpayers],
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
    await _attach_links(db, customer)
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
    await db.flush()  # get customer_id before writing the junction rows
    await _set_links(
        db,
        customer.customer_id,
        addresses=data.addresses,
        contacts=data.contacts,
        taxpayers=data.taxpayers,
    )
    await db.commit()
    await db.refresh(customer)
    await _attach_customer_relations(db, [customer])
    await _attach_links(db, customer)
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
    await _set_links(
        db,
        customer.customer_id,
        addresses=data.addresses,
        contacts=data.contacts,
        taxpayers=data.taxpayers,
    )
    await db.commit()
    await db.refresh(customer)
    await _attach_customer_relations(db, [customer])
    await _attach_links(db, customer)
    return customer


async def delete_customer(db: AsyncSession, customer: Customer, default_customer_id: int) -> None:
    if customer.customer_id == default_customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Cannot delete the system default customer',
        )
    await assert_not_referenced(db, customer)
    await db.delete(customer)
    await db.commit()
