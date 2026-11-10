"""Spec 038, T026: receivables and payables against real tables (SQLite), research R7–R10.

Today's receivable is spec 033's figure, so it must equal `GET /accounts-receivable` to the cent.
A past date reads the month-end the snapshot job stored. DSO, CEI and the collection measures are
checked against a March 2026 worked by hand.
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.enums import CurrencyCode, EntityStatus
from app.jobs import snapshot_receivables as job
from app.models.customer import Customer
from app.models.inventory import InventoryReceipt
from app.models.purchases import PurchaseOrder, PurchaseOrderDetail, SupplierBalanceCutoff
from app.models.supplier import Supplier
from app.schemas.analytics import MeasureKey
from app.services import accounts_service
from app.services.analytics import cache
from app.services.analytics.catalog import MEASURES
from tests.integration import inventory_seed
from tests.integration.detail_export import assert_export_matches
from tests.integration.receivable_seed import apply, credit_order
from tests.integration.seed import seed_facility
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

URL = '/api/v1/analytics'
TODAY = local_now().date()
NOW = local_now().replace(microsecond=0)
MARCH = {'start': '2026-03-01', 'end': '2026-03-31'}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


def _days(n: int) -> datetime:
    return NOW + timedelta(days=n)


async def _query(client: AsyncClient, *measures: str, **body: object) -> dict:
    period = {'start': TODAY.isoformat(), 'end': TODAY.isoformat()}
    response = await client.post(
        f'{URL}/query', json={'measures': list(measures), **period, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _by_keys(body: dict, measure: str) -> dict[tuple, Decimal | None]:
    return {
        tuple(k['value'] for k in row['keys']): (
            None if row['values'][measure] is None else Decimal(row['values'][measure])
        )
        for row in body['rows']
    }


@pytest.fixture
async def book(db: AsyncSession) -> dict[str, int]:
    """Open credit orders today: customer 1 (limit 5000) in facility 1, customer 2 (no limit) in
    facilities 1 and 2, one of them in dollars, across every aging and due bucket."""
    facility = await seed_facility(db)
    await db.execute(
        update(Customer).where(Customer.customer_id == 1).values(credit_limit=5000, salesperson=1)
    )
    db.add(
        Customer(
            customer_id=2, code='C2', name='Cliente 2', credit_limit=Decimal('0'), credit_days=30,
            price_list=1, status=EntityStatus.ACTIVE,
        )  # fmt: skip
    )
    await db.commit()
    late = await credit_order(db, confirmed=_days(-60), due=_days(-45))
    await apply(db, late, '200', _days(-50))
    soon = await credit_order(db, confirmed=_days(-5), due=_days(10), price='500')
    dollars = await credit_order(
        db, confirmed=_days(-20), due=_days(-5), price='100.01', customer=2, facility=facility,
        currency=CurrencyCode.USD, rate='17.2345',
    )  # fmt: skip
    week = await credit_order(db, confirmed=_days(-3), due=_days(3), price='250', customer=2)
    far = await credit_order(db, confirmed=_days(-2), due=_days(90), price='100')
    return {'late': late, 'soon': soon, 'dollars': dollars, 'week': week, 'far': far}


# ── Today ─────────────────────────────────────────────────────────────────────


async def test_today_by_customer_and_bucket_equals_accounts_receivable(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    summary = await accounts_service.summary(db, accounts_service.CUSTOMERS, facility=None)
    expected = {
        (str(row['party']), name): value
        for row in summary['items']
        for name, value in row['buckets'].items()
        if value
    }

    body = await _query(client, 'receivable', dimensions=['customer', 'aging_bucket'])

    assert _by_keys(body, 'receivable') == expected
    assert Decimal(body['totals']['values']['receivable']) == summary['totals']['owed']
    assert expected[('2', '1-30')] == Decimal('1723.62')


async def test_grouped_by_payment_terms_the_rows_add_up_to_the_total(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, 'receivable', 'open_orders', dimensions=['payment_terms'])

    rows = _by_keys(body, 'receivable')
    assert set(rows) == {('credit',)}
    assert sum(rows.values()) == Decimal(body['totals']['values']['receivable'])
    assert body['totals']['values']['open_orders'] == '5'


def test_the_receivable_definition_states_the_2025_caveat() -> None:
    assert '2025 month-ends understate' in MEASURES[MeasureKey.RECEIVABLE].definition


async def test_utilization_is_null_without_a_limit_and_from_parts_across_customers(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(
        client, 'receivable', 'credit_limit', 'credit_utilization', dimensions=['customer']
    )

    rows = {row['keys'][0]['value']: row['values'] for row in body['rows']}
    assert rows['1']['credit_utilization'] == '0.2800', '1400 of 5000'
    assert rows['2']['credit_utilization'] is None
    assert rows['2']['credit_limit'] == '0.00'
    totals = body['totals']['values']
    assert (totals['credit_limit'], totals['credit_utilization']) == ('5000.00', '0.2800')


async def test_the_forecast_buckets_add_up_to_the_receivable(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, 'receivable', dimensions=['due_bucket'])

    rows = _by_keys(body, 'receivable')
    assert rows == {
        ('overdue',): Decimal('2523.62'),
        ('1-7',): Decimal('250.00'),
        ('8-30',): Decimal('500.00'),
        ('60+',): Decimal('100.00'),
    }
    assert sum(rows.values()) == Decimal(body['totals']['values']['receivable'])


async def test_the_overdue_detail_sums_to_the_query(
    client: AsyncClient, book: dict[str, int]
) -> None:
    period = {'start': TODAY.isoformat(), 'end': TODAY.isoformat()}
    query = await _query(client, 'receivable', 'receivable_overdue')
    overdue = await client.post(
        f'{URL}/detail', json={'kind': 'receivables', **period, 'flag': 'overdue', 'limit': 100}
    )
    every = await client.post(f'{URL}/detail', json={'kind': 'receivables', **period})

    assert overdue.status_code == 200, overdue.text
    assert overdue.json()['total'] == 2
    assert overdue.json()['sums']['amount'] == query['totals']['values']['receivable_overdue']
    assert {item['id'] for item in overdue.json()['items']} == {book['late'], book['dollars']}
    late = next(i for i in overdue.json()['items'] if i['id'] == book['late'])
    assert late['figures']['values']['days_past_due'] == '45'
    assert late['figures']['values']['bucket'] == '31-60'
    assert every.json()['total'] == 5
    assert every.json()['sums']['amount'] == query['totals']['values']['receivable']


async def test_the_receivables_export_has_the_buckets(
    client: AsyncClient, book: dict[str, int]
) -> None:
    period = {'start': TODAY.isoformat(), 'end': TODAY.isoformat()}

    lines = await assert_export_matches(client, {'kind': 'receivables', **period})

    late = next(line for line in lines if line['document_id'] == str(book['late']))
    assert (late['days_past_due'], late['bucket']) == ('45', '31-60')


# ── Month-ends ────────────────────────────────────────────────────────────────


@pytest.fixture
async def march(db: AsyncSession) -> dict[str, int]:
    """February ends with one order of 1000 due on March 12; March sells 2000 on credit and
    collects 600 of the first and 100 of the second. Both month-ends are stored."""
    await db.execute(
        update(Customer).where(Customer.customer_id == 1).values(credit_limit=5000, salesperson=1)
    )
    await db.commit()
    first = await credit_order(
        db, confirmed=datetime(2026, 2, 10, 9), due=datetime(2026, 3, 12, 9)
    )
    second = await credit_order(
        db, confirmed=datetime(2026, 3, 5, 9), due=datetime(2026, 4, 4, 9), price='2000'
    )
    await apply(db, first, '600', datetime(2026, 3, 20, 12))
    await apply(db, second, '100', datetime(2026, 3, 25, 12))
    written = datetime(2026, 4, 2, 6)
    for month_end in (date(2026, 2, 28), date(2026, 3, 31)):
        await job.write_month(db, month_end, now=written)
    return {'first': first, 'second': second}


async def test_a_past_date_reads_the_stored_month_end(
    client: AsyncClient, db: AsyncSession, march: dict[str, int]
) -> None:
    """An application dated in March but recorded after the snapshot doesn't change it."""
    await apply(db, march['second'], '500', datetime(2026, 3, 28, 12))

    month_end = await _query(client, 'receivable', 'receivable_current', **MARCH)
    mid_month = await _query(client, 'receivable', start='2026-03-01', end='2026-03-15')

    assert month_end['totals']['values'] == {
        'receivable': '2300.00',
        'receivable_current': '1900.00',
    }
    assert mid_month['totals']['values']['receivable'] == '1000.00', "February's end"


async def test_the_definition_shows_the_month_end_a_mid_month_date_reads(
    client: AsyncClient, march: dict[str, int]
) -> None:
    """Contract "Receivable history": the balance is February's end, and the definition says so;
    a query that also sums a flow keeps its period, which the flow is summed over."""
    mid_month = await _query(client, 'receivable', start='2026-03-01', end='2026-03-15')
    late_start = await _query(client, 'receivable', start='2026-01-10', end='2026-03-15')
    mixed = await _query(client, 'receivable', 'net_sales', start='2026-03-01', end='2026-03-15')
    month_end = await _query(client, 'receivable', **MARCH)

    assert (mid_month['definition']['start'], mid_month['definition']['end']) == (
        '2026-02-28', '2026-02-28',
    )  # fmt: skip
    assert mid_month['totals']['values']['receivable'] == '1000.00'
    assert (late_start['definition']['start'], late_start['definition']['end']) == (
        '2026-01-10', '2026-02-28',
    )  # fmt: skip
    assert mixed['definition']['end'] == '2026-03-15'
    assert month_end['definition']['end'] == '2026-03-31'


async def test_by_month_each_bucket_holds_its_month_end(
    client: AsyncClient, march: dict[str, int]
) -> None:
    body = await _query(
        client, 'receivable', dimensions=['date'], grain='month', start='2026-02-01',
        end='2026-03-31',
    )  # fmt: skip

    assert _by_keys(body, 'receivable') == {
        ('2026-02-01',): Decimal('1000.00'),
        ('2026-03-01',): Decimal('2300.00'),
    }
    assert body['totals']['values']['receivable'] == '2300.00'


async def test_a_month_end_before_the_history_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        f'{URL}/query',
        json={'measures': ['receivable'], 'start': '2024-12-01', 'end': '2024-12-31'},
    )

    body = assert_problem(response, 422, 'analytics.before_history')
    assert body['params'] == {
        'history': 'receivables',
        'requested': '2024-12-31',
        'earliest': '2025-01-31',
    }


async def test_dso_and_cei_match_the_hand_computed_march(
    client: AsyncClient, march: dict[str, int]
) -> None:
    """DSO = 2300 ÷ 2000 × 31; CEI = (1000 + 2000 − 2300) ÷ (1000 + 2000 − 1900)."""
    body = await _query(client, 'dso', 'cei', 'credit_sales', **MARCH)

    assert body['totals']['values'] == {'dso': '35.7', 'cei': '0.6364', 'credit_sales': '2000.00'}


async def test_dso_needs_a_period_of_whole_months(client: AsyncClient) -> None:
    response = await client.post(
        f'{URL}/query', json={'measures': ['dso'], 'start': '2026-03-02', 'end': '2026-03-31'}
    )

    body = assert_problem(response, 422, 'analytics.period_not_month_end')
    assert body['params'] == {'measure': 'dso', 'start': '2026-03-02', 'end': '2026-03-31'}


async def test_collected_due_and_due_collected_follow_r9(
    client: AsyncClient, march: dict[str, int]
) -> None:
    """Collected: every application in March. Due: February's balance of the order due in March.
    Due collected: the applications to that order only."""
    body = await _query(
        client, 'collected_on_credit', 'due_in_period', 'due_collected', **MARCH
    )

    assert body['totals']['values'] == {
        'collected_on_credit': '700.00',
        'due_in_period': '1000.00',
        'due_collected': '600.00',
    }


# ── Payables ──────────────────────────────────────────────────────────────────


async def test_payables_are_zero_until_the_cutoff_then_follow_po_counts(
    client: AsyncClient, db: AsyncSession
) -> None:
    vendor = await inventory_seed.supplier(db)
    await db.execute(update(Supplier).where(Supplier.supplier_id == vendor).values(credit_days=30))
    product = await inventory_seed.product(db, code='AP1')
    counted, _ = await inventory_seed.purchase_order(
        db, lines=[(product, '1', 1)], supplier=vendor, price='8000'
    )
    earlier, _ = await inventory_seed.purchase_order(
        db, lines=[(product, '1', 1)], supplier=vendor, price='500'
    )
    await db.execute(update(PurchaseOrderDetail).values(tax_rate=Decimal(0)))
    await db.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.purchase_order_id == earlier)
        .values(modification_time=inventory_seed.WHEN - timedelta(days=1))
    )
    received = _days(-45)
    db.add(
        InventoryReceipt(
            facility=1, warehouse=1, purchase_order=counted, creator=1, updater=1,
            creation_time=received, modification_time=received, completed=True,
            cancelled=False, serial=12,
        )  # fmt: skip
    )
    db.add(SupplierBalanceCutoff(supplier_balance_cutoff_id=1, cutoff=None))
    await db.commit()
    measures = ('payable', 'payable_overdue', 'open_purchase_orders')

    before = await _query(client, *measures)
    await db.execute(update(SupplierBalanceCutoff).values(cutoff=inventory_seed.WHEN))
    await db.commit()
    cache.responses.clear()
    after = await _query(client, *measures, dimensions=['supplier', 'aging_bucket'])
    past = await _query(client, *measures, **MARCH)

    assert before['totals']['values'] == {
        'payable': '0.00',
        'payable_overdue': '0.00',
        'open_purchase_orders': '0',
    }
    summary = await accounts_service.summary(db, accounts_service.SUPPLIERS, facility=None)
    assert after['totals']['values'] == {
        'payable': str(summary['totals']['owed']),
        'payable_overdue': '8000.00',
        'open_purchase_orders': '1',
    }
    assert _by_keys(after, 'payable') == {(str(vendor), '1-30'): Decimal('8000.00')}
    assert set(past['totals']['values'].values()) == {None}, 'today only'
