"""The facility rule on payment and order writes, and on the lists (spec 034, US3 and US4).

The caller is a clerk of facility 1 who can do everything with customer payments, verification and
sales orders, but holds neither PaymentsEditor (100) nor SearchCreditsFromAllStores (101). Another
facility's records answer as if they didn't exist; verification and order reads stay company-wide.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, CurrencyCode, PaymentType, SystemObject
from app.main import app as fastapi_app
from app.models.sales import CustomerPayment, SalesOrder, SalesOrderDetail, SalesOrderPayment
from app.models.user import AccessPrivilege
from tests.integration.seed import seed_facility, seed_sales_order
from tests.integration.test_credit_notes_flow import _note

NOW = datetime(2026, 9, 1, 10, 0)
ALL = int(AccessRight.CREATE | AccessRight.READ | AccessRight.UPDATE | AccessRight.DELETE)
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)


def _as(user: CurrentUser) -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: user


async def _grant(db: AsyncSession, *objects: SystemObject) -> None:
    for obj in objects:
        db.add(AccessPrivilege(user_id='tester', system_object=int(obj), privileges=ALL))
    await db.commit()


async def _payment(db: AsyncSession, facility: int, amount: str = '100') -> int:
    payment = CustomerPayment(
        facility=facility, customer=1, amount=Decimal(amount), method=3, date=NOW,
        currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=NOW,
        modification_time=NOW, payment_type=int(PaymentType.CREDIT_PAYMENT), serial=0,
    )  # fmt: skip
    db.add(payment)
    await db.commit()
    return payment.customer_payment_id


async def _order(db: AsyncSession, facility: int, *, completed: bool) -> tuple[int, int]:
    """A 1,160.00 order (ten at 100 plus 16%) moved to `facility`, and its line."""
    order = await seed_sales_order(db, completed=completed)
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == order).values(facility=facility)
    )
    await db.commit()
    line = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == order
            )
        )
    ).scalar_one()
    return order, line


@pytest.fixture
async def world(db: AsyncSession, seeded: None) -> dict[str, int]:
    other = await seed_facility(db)
    await _grant(
        db,
        SystemObject.CUSTOMER_PAYMENTS,
        SystemObject.PAYMENTS_VERIFICATION,
        SystemObject.SALES_ORDERS,
        SystemObject.CREDIT_PAYMENTS,
    )
    here_order, _ = await _order(db, 1, completed=True)
    there_order, _ = await _order(db, other, completed=True)
    draft, line = await _order(db, other, completed=False)
    there_payment = await _payment(db, other)
    applied = SalesOrderPayment(
        sales_order=there_order, customer_payment=there_payment, amount=Decimal('10'),
        amount_change=Decimal(0), applier=1, date=NOW, confirmed=True, cancelled=False,
    )  # fmt: skip
    db.add(applied)
    await db.commit()
    return {
        'other': other,
        'here_order': here_order,
        'there_order': there_order,
        'draft': draft,
        'line': line,
        'here_payment': await _payment(db, 1),
        'there_payment': there_payment,
        'application': applied.sales_order_payment_id,
    }


async def _live_applications(db: AsyncSession, payment: int) -> int:
    db.expire_all()
    rows = await db.execute(
        select(SalesOrderPayment).where(
            SalesOrderPayment.customer_payment == payment, SalesOrderPayment.cancelled.is_(False)
        )
    )
    return len(rows.scalars().all())


class TestPaymentWrites:
    async def test_another_facilitys_payment_cannot_be_applied_or_reversed(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        """US3 scenario 1."""
        _as(CLERK)
        payment = world['there_payment']

        applied = await client.post(
            f'/api/v1/customer-payments/{payment}/applications',
            json={'sales_order': world['there_order'], 'amount': '5'},
        )
        reversed_ = await client.post(
            f'/api/v1/customer-payments/{payment}/applications/{world["application"]}/reverse',
            json={'reason': 'Prueba'},
        )

        assert applied.status_code == 404, applied.text
        assert applied.json()['code'] == 'resource.not_found'
        assert reversed_.status_code == 404, reversed_.text
        assert reversed_.json()['code'] == 'resource.not_found'
        assert await _live_applications(db, payment) == 1, 'nothing changed'

    async def test_an_own_payment_may_pay_another_facilitys_order(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        """Scenario 3: the rule is about the payment's facility (spec 033 FR-021)."""
        _as(CLERK)

        applied = await client.post(
            f'/api/v1/customer-payments/{world["here_payment"]}/applications',
            json={'sales_order': world['there_order'], 'amount': '5'},
        )

        assert applied.status_code == 201, applied.text

    async def test_verification_is_company_wide(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        """Scenario 4: as the current system's verification screen, any facility."""
        _as(CLERK)
        payment = world['there_payment']

        rejected = await client.post(
            f'/api/v1/customer-payments/{payment}/reject', json={'reason': 'Revisar'}
        )
        verified = await client.post(f'/api/v1/customer-payments/{payment}/verify')

        assert rejected.status_code == 200, rejected.text
        assert verified.status_code == 200, verified.text

    async def test_the_editor_privilege_opens_another_facility(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        """Scenario 2."""
        await _grant(db, SystemObject.PAYMENTS_EDITOR)
        _as(CLERK)

        applied = await client.post(
            f'/api/v1/customer-payments/{world["there_payment"]}/applications',
            json={'sales_order': world['there_order'], 'amount': '5'},
        )

        assert applied.status_code == 201, applied.text


class TestOrderWrites:
    async def test_another_facilitys_order_cannot_be_changed(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        """Scenario 5: every write answers 404, and the order is untouched."""
        _as(CLERK)
        draft, line = world['draft'], world['line']
        base = f'/api/v1/sales-orders/{draft}'

        answers = [
            await client.put(base, json={}),
            await client.post(f'{base}/lines', json={'product': 1, 'quantity': '1'}),
            await client.put(f'{base}/lines/{line}', json={'quantity': '2'}),
            await client.delete(f'{base}/lines/{line}'),
            await client.post(f'{base}/confirm'),
            await client.post(f'{base}/cancel'),
        ]

        assert [a.status_code for a in answers] == [404] * 6, [a.text for a in answers]
        db.expire_all()
        order = await db.get(SalesOrder, draft)
        assert (order.completed, order.cancelled) == (False, False)
        lines = await db.execute(
            select(SalesOrderDetail).where(SalesOrderDetail.sales_order == draft)
        )
        assert [d.quantity for d in lines.scalars()] == [Decimal('10')]

    async def test_another_facilitys_order_can_still_be_read(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        _as(CLERK)
        base = f'/api/v1/sales-orders/{world["there_order"]}'

        assert (await client.get(base)).status_code == 200
        assert (await client.get(f'{base}/payments')).status_code == 200

    async def test_101_opens_another_facilitys_order(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        await _grant(db, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)
        _as(CLERK)

        updated = await client.put(f'/api/v1/sales-orders/{world["draft"]}', json={})

        assert updated.status_code == 200, updated.text

    async def test_an_administrator_passes_every_check(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        """The fixture's own caller is an administrator."""
        updated = await client.put(f'/api/v1/sales-orders/{world["draft"]}', json={})
        applied = await client.post(
            f'/api/v1/customer-payments/{world["there_payment"]}/applications',
            json={'sales_order': world['there_order'], 'amount': '5'},
        )

        assert updated.status_code == 200, updated.text
        assert applied.status_code == 201, applied.text


class TestLists:
    """US4: lists come from the caller's facility; naming another needs the cross-facility
    privilege; verification covers every facility."""

    async def test_the_sales_order_list_keeps_to_the_callers_facility(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        _as(CLERK)

        own = await client.get('/api/v1/sales-orders', params={'limit': 100})
        other = await client.get('/api/v1/sales-orders', params={'facility': world['other']})

        assert own.status_code == 200, own.text
        ids = {row['sales_order_id'] for row in own.json()['items']}
        assert world['here_order'] in ids
        assert not ids & {world['there_order'], world['draft']}
        assert other.status_code == 403, other.text

    async def test_credit_notes_keep_to_the_callers_facility(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        here, _ = await _note(db, world['here_order'], '10', customer=1)
        there, _ = await _note(db, world['there_order'], '10', customer=1, facility=world['other'])
        _as(CLERK)

        listed = await client.get('/api/v1/credit-notes', params={'limit': 100})
        named = await client.get('/api/v1/credit-notes', params={'facility': world['other']})
        read = await client.get(f'/api/v1/credit-notes/{there}')

        assert [row['credit_note_id'] for row in listed.json()['items']] == [here]
        assert named.status_code == 403, named.text
        assert read.status_code == 404, read.text
        assert read.json()['code'] == 'resource.not_found'

    async def test_the_unverified_list_covers_every_facility(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        """FR-012, SC-005: as the current system's verification screen."""
        _as(CLERK)

        every = await client.get('/api/v1/customer-payments/unverified', params={'limit': 100})
        narrowed = await client.get(
            '/api/v1/customer-payments/unverified', params={'facility': world['other']}
        )

        assert every.status_code == 200, every.text
        assert {row['facility'] for row in every.json()['items']} == {1, world['other']}
        assert {row['facility'] for row in narrowed.json()['items']} == {world['other']}

    async def test_the_cross_facility_privileges_open_the_lists(
        self, client: AsyncClient, db: AsyncSession, world: dict
    ) -> None:
        there, _ = await _note(db, world['there_order'], '10', customer=1, facility=world['other'])
        await _grant(db, SystemObject.PAYMENTS_EDITOR, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)
        _as(CLERK)
        other = {'facility': world['other']}

        orders = await client.get('/api/v1/sales-orders', params=other)
        notes = await client.get('/api/v1/credit-notes', params=other)
        read = await client.get(f'/api/v1/credit-notes/{there}')

        assert orders.status_code == 200, orders.text
        ids = {row['sales_order_id'] for row in orders.json()['items']}
        assert ids == {world['there_order'], world['draft']}
        assert [row['credit_note_id'] for row in notes.json()['items']] == [there]
        assert read.status_code == 200, read.text
