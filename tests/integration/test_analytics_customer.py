"""Spec 038, T034: the customer 360 against real tables (SQLite), FR-125.

Each section is an engine run with the customer's filter forced, so it must equal `POST
/analytics/query` for the same definition; the credit figures are spec 033's. Customer 2 buys in
May 2026 (the period), in January and on the first day of the trailing 12 months, and once a day
before them; customer 1 buys too, and must not show.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, SystemObject
from app.main import app as fastapi_app
from app.models.user import AccessPrivilege
from app.services import accounts_service
from app.services.analytics import cache
from tests.integration.analytics_seed import CREDIT_CUSTOMER, L, payment, refund, sale, seed_catalog
from tests.integration.receivable_seed import credit_order
from tests.integration.seed import seed_facility
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

URL = '/api/v1/analytics'
MAY = {'start': '2026-05-01', 'end': '2026-05-31'}
LAST = datetime(2026, 5, 10, 11, 30)
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)
SALES = SystemObject.SALES_BY_CUSTOMER
PAYMENTS = SystemObject.RECEIVED_PAYMENTS
RECEIVABLES = SystemObject.ACCOUNTS_RECEIVABLE
COST = SystemObject.GROSS_PROFITS_BY_CUSTOMER


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


@pytest.fixture
async def book(db: AsyncSession) -> dict[str, int]:
    await seed_catalog(db)
    customer = CREDIT_CUSTOMER
    may, lines = await sale(
        db, LAST, [L('2', '116', cost='50'), L('1', '58', cost='20', product=2)], customer=customer
    )
    returned = [(lines[0], '1', L('2', '116'))]
    await refund(db, may, returned, datetime(2026, 5, 12), customer=customer)
    await payment(db, datetime(2026, 5, 10, 12), '100', customer=customer, applied_to=may)
    await sale(db, datetime(2026, 1, 15), [L('1', '232', product=3)], customer=customer)
    await sale(db, datetime(2025, 6, 1, 9), [L('1', '10')], customer=customer)
    await sale(db, datetime(2025, 5, 31, 18), [L('1', '10')], customer=customer)
    await sale(db, datetime(2026, 5, 11), [L('5', '116')], customer=1)
    now = local_now().replace(microsecond=0)
    late = await credit_order(
        db, confirmed=now - timedelta(days=60), due=now - timedelta(days=40), customer=customer
    )
    soon = await credit_order(
        db, confirmed=now - timedelta(days=5), due=now + timedelta(days=20), price='400',
        customer=customer,
    )  # fmt: skip
    await credit_order(db, confirmed=now - timedelta(days=5), due=now, price='900', customer=1)
    return {'may': may, 'late': late, 'soon': soon}


async def _customer(client: AsyncClient, customer: int = CREDIT_CUSTOMER, **params: object) -> dict:
    response = await client.get(f'{URL}/customers/{customer}', params={**MAY, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def _as_clerk(db: AsyncSession, *objects: int) -> None:
    """From here on the caller is a facility-1 clerk holding these report privileges."""
    for system_object in objects:
        db.add(
            AccessPrivilege(
                user_id='tester', system_object=int(system_object), privileges=AccessRight.READ
            )
        )
    await db.commit()
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK


async def _spec_033(db: AsyncSession) -> dict:
    found = await accounts_service.detail(
        db, accounts_service.CUSTOMERS, CREDIT_CUSTOMER, facility=None
    )
    return found['summary']


def _without_as_of(body: dict) -> dict:
    return {key: value for key, value in body.items() if key != 'as_of'}


# ── Sections ──────────────────────────────────────────────────────────────────


async def test_each_section_equals_the_query_filtered_by_the_customer(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _customer(client)

    assert (body['customer'], body['customer_name']) == (CREDIT_CUSTOMER, 'Constructora Dos')
    assert (body['start'], body['end']) == (MAY['start'], MAY['end'])
    for name in ('sales', 'receivable', 'top_products'):
        section = body[name]
        assert {'dimension': 'customer', 'values': [str(CREDIT_CUSTOMER)]} in section['definition'][
            'filters'
        ]
        queried = await client.post(f'{URL}/query', json=section['definition'])
        assert queried.status_code == 200, queried.text
        assert _without_as_of(section) == _without_as_of(queried.json()), name


async def test_sales_hold_net_sales_refunds_payments_and_margin_with_comparison(
    client: AsyncClient, book: dict[str, int]
) -> None:
    sales = (await _customer(client))['sales']

    assert set(sales['definition']['measures']) == {
        'net_sales', 'refunds', 'collected', 'gross_margin', 'margin_pct',
    }  # fmt: skip
    assert sales['definition']['comparison'] == 'previous_period'
    values = sales['totals']['values']
    # 2 × 116 + 58 with VAT, less one 116 refunded: 232 without VAT, 100 refunded.
    assert (values['net_sales'], values['refunds'], values['collected']) == (
        '150.00',
        '100.00',
        '100.00',
    )


async def test_the_receivable_is_now_by_bucket_and_equals_spec_033(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    receivable = (await _customer(client))['receivable']
    summary = await _spec_033(db)

    today = local_now().date().isoformat()
    assert (receivable['definition']['start'], receivable['definition']['end']) == (today, today)
    assert receivable['definition']['dimensions'] == ['aging_bucket']
    totals = receivable['totals']['values']
    assert Decimal(totals['receivable']) == summary['owed'] == Decimal('1400.00')
    assert Decimal(totals['receivable_overdue']) == summary['overdue'] == Decimal('1000.00')
    buckets = {
        row['keys'][0]['value']: Decimal(row['values']['receivable']) for row in receivable['rows']
    }
    assert buckets == {name: value for name, value in summary['buckets'].items() if value}


async def test_top_products_are_the_customers_ten_best(
    client: AsyncClient, book: dict[str, int]
) -> None:
    top = (await _customer(client))['top_products']

    assert top['definition']['dimensions'] == ['product']
    assert (top['definition']['top'], top['definition']['sort']) == (10, '-net_sales')
    assert [row['keys'][0]['value'] for row in top['rows']] == ['1', '2']


# ── Customer figures ──────────────────────────────────────────────────────────


async def test_last_purchase_and_frequency_cover_the_12_months_before_end(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _customer(client)

    # May 10, January 15 and 2025-06-01; 2025-05-31 is a day too early.
    assert body['last_purchase'] == LAST.isoformat()
    assert Decimal(body['orders_per_month']) == Decimal('0.25')

    earlier = await _customer(client, start='2026-02-01', end='2026-04-30')
    assert earlier['last_purchase'] == '2026-01-15T00:00:00'


async def test_no_purchase_in_the_12_months_gives_null_and_zero(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _customer(client, start='2024-01-01', end='2024-12-31')

    assert body['last_purchase'] is None
    assert Decimal(body['orders_per_month']) == 0


async def test_credit_limit_and_utilization_are_spec_033s(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    body = await _customer(client)
    summary = await _spec_033(db)

    assert Decimal(body['credit_limit']) == summary['credit_limit'] == Decimal('50000')
    assert Decimal(body['credit_utilization']) == summary['utilization'] == Decimal('0.028')


# ── Privileges and scope ──────────────────────────────────────────────────────


async def test_payments_need_the_payments_family(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    await _as_clerk(db, SALES)

    sales = (await _customer(client))['sales']

    assert sales['definition']['measures'] == ['net_sales', 'refunds']
    assert 'collected' not in sales['totals']['values']
    assert sales['totals']['values']['net_sales'] == '150.00'

    await _as_clerk(db, PAYMENTS)
    sales = (await _customer(client))['sales']
    assert sales['totals']['values']['collected'] == '100.00'


async def test_margin_appears_only_with_the_cost_privilege(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    await _as_clerk(db, SALES)
    assert 'gross_margin' not in (await _customer(client))['sales']['totals']['values']

    await _as_clerk(db, COST)
    values = (await _customer(client))['sales']['totals']['values']
    assert {'gross_margin', 'margin_pct'} <= set(values)


async def test_a_section_is_null_without_its_family(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    await _as_clerk(db, SALES)
    body = await _customer(client)
    assert body['sales'] is not None and body['top_products'] is not None
    assert body['last_purchase'] is not None
    assert (body['receivable'], body['credit_limit'], body['credit_utilization']) == (None,) * 3

    await db.execute(AccessPrivilege.__table__.delete())
    await _as_clerk(db, RECEIVABLES)
    body = await _customer(client)
    assert body['receivable'] is not None and body['credit_limit'] is not None
    assert (
        body['sales'],
        body['top_products'],
        body['last_purchase'],
        body['orders_per_month'],
    ) == (None,) * 4


async def test_without_sales_or_receivables_the_route_is_refused(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    await _as_clerk(db, PAYMENTS)

    response = await client.get(f'{URL}/customers/{CREDIT_CUSTOMER}', params=MAY)

    assert_problem(response, 403, 'auth.forbidden')


async def test_an_explicit_other_facility_is_refused(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    other = await seed_facility(db)
    await _as_clerk(db, SALES, RECEIVABLES)

    response = await client.get(
        f'{URL}/customers/{CREDIT_CUSTOMER}', params={**MAY, 'facility': other}
    )

    assert_problem(response, 403, 'facility.other_facility')


async def test_an_unknown_customer_is_not_found(client: AsyncClient, book: dict[str, int]) -> None:
    response = await client.get(f'{URL}/customers/999999', params=MAY)

    assert_problem(response, 404, 'resource.not_found')
