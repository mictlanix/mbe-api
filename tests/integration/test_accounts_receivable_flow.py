"""Accounts receivable over real rows (spec 033, US2).

Customer 21 owes 30,000 overdue at facility 1 and 10,000 not yet due at facility 2, against a
50,000 limit. A caller scoped to facility 1 sees the 30,000; utilization reads the whole 40,000
whoever asks (FR-015).
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, CurrencyCode, EntityStatus, PaymentTerms, Priority, SystemObject
from app.main import app as fastapi_app
from app.models.customer import Customer
from app.models.sales import CustomerPayment, SalesOrder, SalesOrderDetail, SalesOrderPayment
from app.models.user import AccessPrivilege
from tests.integration.seed import seed_credit_note, seed_facility, seed_invoice

URL = '/api/v1/accounts-receivable'
NOW = local_now().replace(microsecond=0)
PAST = NOW - timedelta(days=40)
LATER = NOW + timedelta(days=20)
SCOPED = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)
ADMIN = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)


def _as(user: CurrentUser) -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: user


async def _customer(db: AsyncSession, customer_id: int, *, limit: str) -> None:
    db.add(
        Customer(
            customer_id=customer_id,
            code=f'C{customer_id}',
            name=f'Cliente {customer_id}',
            credit_limit=Decimal(limit),
            credit_days=30,
            price_list=1,
            salesperson=1,
            status=EntityStatus.ACTIVE,
        )  # fmt: skip
    )
    await db.commit()


async def _order(
    db: AsyncSession,
    customer: int,
    price: str,
    *,
    facility: int = 1,
    due: datetime = LATER,
    date: datetime = PAST,
    paid: bool = False,
) -> tuple[int, int]:
    order = SalesOrder(
        creator=1, updater=1, creation_time=date, modification_time=date,
        facility=facility, point_sale=1, salesperson=1, customer=customer,
        date=date, promise_date=date, due_date=due,
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.NET_D, priority=Priority.NORMAL,
        completed=True, cancelled=False, paid=paid, delivered=False, serial=4410,
    )  # fmt: skip
    db.add(order)
    await db.flush()
    line = SalesOrderDetail(
        sales_order=order.sales_order_id, product=1, quantity=Decimal('1'),
        cost=Decimal('1'), price=Decimal(price), discount_rate=Decimal('0'),
        tax_rate=Decimal('0'), product_code='P1', product_name='Producto Uno',
        warehouse=1, exchange_rate=Decimal('1'), currency=CurrencyCode.MXN,
        tax_included=False,
    )  # fmt: skip
    db.add(line)
    await db.commit()
    return order.sales_order_id, line.sales_order_detail_id


async def _payment(
    db: AsyncSession, customer: int, amount: str, *, facility: int = 1, applied: tuple = (),
    change: str = '0',
) -> int:  # fmt: skip
    """A payment of `amount`, applied to `(order, amount)` pairs."""
    payment = CustomerPayment(
        facility=facility, customer=customer, amount=Decimal(amount), method=1, date=NOW,
        currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=NOW,
        modification_time=NOW, payment_type=2, serial=88,
    )  # fmt: skip
    db.add(payment)
    await db.flush()
    for order_id, part in applied:
        db.add(
            SalesOrderPayment(
                sales_order=order_id,
                customer_payment=payment.customer_payment_id,
                amount=Decimal(part),
                amount_change=Decimal(change),
                applier=1,
                date=NOW,
                confirmed=True,
                cancelled=False,
            )  # fmt: skip
        )
    await db.commit()
    return payment.customer_payment_id


@pytest.fixture
async def ledger(db: AsyncSession, seeded: None) -> dict:
    await seed_facility(db)
    db.add(
        AccessPrivilege(
            user_id='tester',
            system_object=int(SystemObject.ACCOUNTS_RECEIVABLE),
            privileges=int(AccessRight.READ),
        )
    )
    await db.commit()
    await _customer(db, 21, limit='50000')
    await _customer(db, 22, limit='0')
    await _customer(db, 23, limit='5000')
    overdue, overdue_line = await _order(db, 21, '30000', due=PAST)
    elsewhere, _ = await _order(db, 21, '10000', facility=2)
    unlimited, _ = await _order(db, 22, '1000')
    legacy_paid, _ = await _order(db, 23, '5000', paid=True)
    settled, _ = await _order(db, 23, '700')
    await _payment(db, 23, '700', applied=((settled, '700'),))
    walk_in, _ = await _order(db, 1, '500')
    return {
        'overdue': overdue, 'overdue_line': overdue_line, 'elsewhere': elsewhere,
        'unlimited': unlimited, 'legacy_paid': legacy_paid, 'settled': settled,
        'walk_in': walk_in,
    }  # fmt: skip


def _rows(response) -> dict[int, dict]:  # noqa: ANN001
    assert response.status_code == 200, response.text
    return {row['party']: row for row in response.json()['items']}


class TestTheSummary:
    async def test_a_scoped_caller_sees_their_facility_only(
        self, client: AsyncClient, ledger: dict
    ) -> None:
        _as(SCOPED)

        response = await client.get(URL)

        rows = _rows(response)
        assert response.json()['scope'] == {'facility': 1, 'all_facilities': False}
        assert rows[21]['owed'] == '30000.00'
        assert rows[21]['overdue'] == '30000.00'
        assert rows[21]['buckets']['31-60'] == '30000.00'

    async def test_an_administrator_sees_every_facility_and_can_narrow(
        self, client: AsyncClient, ledger: dict
    ) -> None:
        _as(ADMIN)

        every = _rows(await client.get(URL))
        narrowed = _rows(await client.get(URL, params={'facility': 2}))

        assert every[21]['owed'] == '40000.00'
        assert every[21]['buckets']['current'] == '10000.00'
        assert set(narrowed) == {21}
        assert narrowed[21]['owed'] == '10000.00'

    async def test_the_limit_reads_what_is_owed_company_wide(
        self, client: AsyncClient, ledger: dict
    ) -> None:
        """US2 scenario 3: 40,000 of a 50,000 limit is 80%, whoever asks."""
        _as(SCOPED)
        scoped = _rows(await client.get(URL))[21]
        _as(ADMIN)
        unscoped = _rows(await client.get(URL))[21]

        for row in (scoped, unscoped):
            assert row['owed_company'] == '40000.00'
            assert row['utilization'] == '0.8000'
            assert Decimal(row['available_credit']) == Decimal('10000')
        assert (scoped['sales_agent'], scoped['sales_agent_name']) == (1, 'Ana Ruiz')

    async def test_no_limit_leaves_utilization_blank(
        self, client: AsyncClient, ledger: dict
    ) -> None:
        _as(ADMIN)

        row = _rows(await client.get(URL))[22]

        assert row['utilization'] is None and row['available_credit'] is None

    async def test_paid_and_settled_orders_are_not_owed(
        self, client: AsyncClient, ledger: dict
    ) -> None:
        _as(ADMIN)

        assert 23 not in _rows(await client.get(URL))

    async def test_the_walk_in_customer_is_listed(self, client: AsyncClient, ledger: dict) -> None:
        _as(ADMIN)

        assert _rows(await client.get(URL))[1]['owed'] == '500.00'

    async def test_the_buckets_add_up_to_owed(self, client: AsyncClient, ledger: dict) -> None:
        _as(ADMIN)

        response = await client.get(URL)

        for row in [*response.json()['items'], response.json()['totals']]:
            assert sum(Decimal(v) for v in row['buckets'].values()) == Decimal(row['owed'])

    async def test_filters_and_sorts(self, client: AsyncClient, ledger: dict) -> None:
        _as(ADMIN)

        overdue = _rows(await client.get(URL, params={'overdue': 'true'}))
        party = _rows(await client.get(URL, params={'party': 22}))
        agent = _rows(await client.get(URL, params={'sales_agent': 99}))
        by_owed = await client.get(URL)
        by_oldest = await client.get(URL, params={'sort': 'oldest'})

        assert set(overdue) == {21}
        assert set(party) == {22}
        assert agent == {}
        assert [r['party'] for r in by_owed.json()['items']] == [21, 22, 1]
        oldest = [r['oldest'] for r in by_oldest.json()['items']]
        assert oldest == sorted(oldest)


class TestTheDrillDown:
    async def test_open_orders_with_their_invoices_and_deductions(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        invoice = await seed_invoice(db, [('30000', ledger['overdue_line'])])
        await seed_credit_note(db, invoice, '1000.00')
        _as(ADMIN)

        response = await client.get(f'{URL}/21')

        assert response.status_code == 200, response.text
        documents = response.json()['documents']
        assert [d['id'] for d in documents] == [ledger['overdue'], ledger['elsewhere']]
        first = documents[0]
        assert first['linked'] == [{'id': invoice, 'label': 'RH9001'}]
        assert (first['facility_code'], first['salesperson']) == ('F1', 1)
        assert (first['deductions'], first['balance']) == ('1000.00', '29000.00')
        assert first['days_past_due'] == 40 and first['bucket'] == '31-60'
        assert response.json()['summary']['owed'] == '39000.00'

    async def test_a_scoped_caller_sees_their_facilitys_orders(
        self, client: AsyncClient, ledger: dict
    ) -> None:
        _as(SCOPED)

        response = await client.get(f'{URL}/21')

        assert [d['id'] for d in response.json()['documents']] == [ledger['overdue']]

    async def test_payments_with_something_left_to_apply(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        open_payment = await _payment(db, 21, '800')
        used_up = await _payment(
            db, 21, '600', applied=((ledger['elsewhere'], '530'),), change='70'
        )
        elsewhere = await _payment(db, 21, '300', facility=2)
        _as(SCOPED)

        scoped = (await client.get(f'{URL}/21')).json()['payments']
        _as(ADMIN)
        every = (await client.get(f'{URL}/21')).json()['payments']

        assert [p['id'] for p in scoped] == [open_payment], '#271: their facility only'
        assert {p['id'] for p in every} == {open_payment, elsewhere}
        assert used_up not in {p['id'] for p in every}, 'change used it up (US4 scenario 6)'
        assert scoped[0]['unapplied'] == '800.00'

    async def test_nothing_open_is_an_empty_drill_down(
        self, client: AsyncClient, ledger: dict
    ) -> None:
        _as(ADMIN)

        response = await client.get(f'{URL}/23')

        assert response.status_code == 200, response.text
        assert response.json()['documents'] == []
        assert response.json()['summary']['owed'] == '0.00'

    async def test_an_unknown_customer_is_404(self, client: AsyncClient, ledger: dict) -> None:
        _as(ADMIN)

        assert (await client.get(f'{URL}/999')).status_code == 404


async def test_customer_credit_counts_change_handed_back(
    client: AsyncClient, db: AsyncSession, ledger: dict
) -> None:
    """FR-005a: credit is what payments have left once applied amounts and change are out."""
    await _payment(db, 21, '500')
    await _payment(db, 21, '600', applied=((ledger['elsewhere'], '530'),), change='70')
    _as(ADMIN)

    row = _rows(await client.get(URL))[21]

    assert row['credit'] == '500.00'
    assert row['owed'] == '39470.00', '40,000 less the 530 applied'
    assert row['net'] == '38970.00', 'less 500 of credit'
