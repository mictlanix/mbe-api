"""The refund list as the current system's (#285): search by number or customer name, the caller's
own refunds, and open refunds first."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus
from app.models.core import Employee
from app.models.customer import Customer
from app.models.sales import CustomerRefund
from tests.integration.seed import seed_sales_order

URL = '/api/v1/customer-refunds'


@pytest.fixture
async def other(db: AsyncSession, seeded: None) -> None:
    """Customer 2 and employee 2, beside the seeded customer 1 and employee 1 (the caller)."""
    db.add_all(
        [
            Customer(
                customer_id=2, code='C2', name='Ferretería Dos', credit_limit=Decimal(0),
                credit_days=0, price_list=1, status=EntityStatus.ACTIVE,
            ),
            Employee(
                employee_id=2, first_name='Beto', last_name='Paz', nickname='beto', gender=1,
                birthday=date(1990, 1, 1), sales_person=True, status=EntityStatus.ACTIVE,
                start_job_date=date(2020, 1, 1),
            ),
        ]
    )  # fmt: skip
    await db.commit()


async def _refund(
    db: AsyncSession,
    *,
    serial: int,
    day: int,
    customer: int = 1,
    creator: int = 1,
    updater: int | None = None,
    completed: bool = True,
    cancelled: bool = False,
) -> int:
    order = await seed_sales_order(db, completed=True, paid=True)
    when = datetime(2026, 10, day, 12, 0)
    refund = CustomerRefund(
        sales_order=order, customer=customer, creator=creator, updater=updater or creator,
        sales_person=1, creation_time=when, modification_time=when, completed=completed,
        cancelled=cancelled, facility=1, serial=serial, date=when, currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
    )  # fmt: skip
    db.add(refund)
    await db.commit()
    return refund.customer_refund_id


async def _ids(client: AsyncClient, **params: object) -> list[int]:
    listed = await client.get(URL, params=params)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body['total'] == len(body['items'])
    return [row['customer_refund_id'] for row in body['items']]


async def test_open_refunds_come_first_then_newest(
    client: AsyncClient, db: AsyncSession, other: None
) -> None:
    old_closed = await _refund(db, serial=1, day=1)
    new_closed = await _refund(db, serial=2, day=20)
    old_open = await _refund(db, serial=3, day=2, completed=False)
    cancelled = await _refund(db, serial=4, day=30, completed=False, cancelled=True)

    assert await _ids(client) == [old_open, cancelled, new_closed, old_closed]


async def test_a_number_matches_the_id_the_folio_or_the_order(
    client: AsyncClient, db: AsyncSession, other: None
) -> None:
    by_serial = await _refund(db, serial=900, day=1)
    await _refund(db, serial=901, day=2)

    assert await _ids(client, search='900') == [by_serial]
    assert await _ids(client, search=str(by_serial)) == [by_serial]
    listed = await client.get(URL, params={'search': str(by_serial)})
    order = listed.json()['items'][0]['sales_order']
    assert by_serial in await _ids(client, search=str(order))


async def test_text_matches_the_customers_name(
    client: AsyncClient, db: AsyncSession, other: None
) -> None:
    await _refund(db, serial=1, day=1)
    theirs = await _refund(db, serial=2, day=2, customer=2)

    assert await _ids(client, search='ferre') == [theirs]


async def test_mine_is_what_the_caller_created_or_last_updated(
    client: AsyncClient, db: AsyncSession, other: None
) -> None:
    created = await _refund(db, serial=1, day=1)
    updated = await _refund(db, serial=2, day=2, creator=2, updater=1)
    await _refund(db, serial=3, day=3, creator=2)

    assert await _ids(client, mine='true') == [updated, created]
    assert await _ids(client, mine='true', search='Uno') == [updated, created]
    assert len(await _ids(client)) == 3
