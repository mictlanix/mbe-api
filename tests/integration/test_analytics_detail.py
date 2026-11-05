"""Spec 037, T036: the drill-down against real tables (SQLite).

Each detail figure must equal the measure the contract's table names for it, across pages, and
every dashboard alert's detail must list exactly its count (SC-007, US3 scenario 3).
"""

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.enums import PaymentTerms, PaymentType
from app.services import sales_order_service
from app.services.analytics import cache
from tests.integration.analytics_seed import (
    CREDIT_CUSTOMER,
    L,
    credit_note,
    payment,
    refund,
    sale,
    seed_catalog,
)

pytestmark = pytest.mark.usefixtures('seeded')

MAY = {'start': '2026-05-01', 'end': '2026-05-31'}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


async def _detail(client: AsyncClient, kind: str, **body: object) -> dict:
    response = await client.post('/api/v1/analytics/detail', json={'kind': kind, **MAY, **body})
    assert response.status_code == 200, response.text
    return response.json()


async def _all_pages(client: AsyncClient, kind: str, **body: object) -> list[dict]:
    items: list[dict] = []
    skip = 0
    while True:
        page = await _detail(client, kind, skip=skip, limit=2, **body)
        items += page['items']
        skip += 2
        if skip >= page['total']:
            return items


async def _query(client: AsyncClient, *measures: str, **body: object) -> dict:
    response = await client.post(
        '/api/v1/analytics/query', json={'measures': list(measures), **MAY, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()['totals']['values']


SOLD = [L('2', '116', cost='58'), L('1', '9.99', included=False), L('3', '0.125', rate='0')]


@pytest.fixture
async def may(db: AsyncSession) -> dict:
    await seed_catalog(db)
    first, lines = await sale(db, datetime(2026, 5, 4, 9, 0), SOLD)
    await sale(db, datetime(2026, 5, 6, 10, 0), [L('1', '58', product=2, cost='70')])
    await sale(db, datetime(2026, 5, 9, 11, 0), [L('5', '23.20', product=3)], customer=2)
    returned = await refund(db, first, [(lines[0], '1', SOLD[0])], datetime(2026, 5, 10, 12, 0))
    await credit_note(db, datetime(2026, 5, 12, 12, 0), [L('1', '11.60')], serial=7401)
    await payment(db, datetime(2026, 5, 4, 9, 5), '300')
    await payment(db, datetime(2026, 5, 10, 12, 5), '-116')
    await payment(db, datetime(2026, 5, 11, 9, 0), '40', kind=PaymentType.CREDIT_NOTE, method=0)
    return {'first': first, 'lines': lines, 'refund': returned}


@pytest.mark.parametrize('vat', ['excluded', 'included'])
@pytest.mark.parametrize(
    ('measure', 'kind', 'figure', 'flag'),
    [
        ('gross_sales', 'lines', 'amount', None),
        ('gross_sales', 'orders', 'amount', None),
        ('discounts', 'lines', 'discount', None),
        ('units_sold', 'lines', 'quantity', None),
        ('refunds', 'refunds', 'amount', None),
        ('units_refunded', 'refunds', 'quantity', None),
        ('credit_notes', 'credit_notes', 'amount', None),
        ('collected', 'payments', 'amount', 'collected'),
        ('refunds_paid_out', 'payments', 'amount', 'paid_out'),
        ('store_credit_issued', 'payments', 'amount', 'store_credit'),
    ],
)
async def test_each_detail_figure_equals_its_measure(
    client: AsyncClient, may: dict, vat: str, measure: str, kind: str, figure: str, flag: str | None
) -> None:
    body = {'vat': vat, **({'flag': flag} if flag else {})}
    page = await _detail(client, kind, **body)
    value = (await _query(client, measure, vat=vat))[measure]

    assert Decimal(page['sums'][figure]) == Decimal(value), (measure, kind)
    every = await _all_pages(client, kind, **body)
    key = 'quantity' if figure == 'quantity' else 'amount'
    if figure != 'discount':
        assert sum(Decimal(i[key]) for i in every) == Decimal(page['sums'][figure])


@pytest.mark.parametrize(('measure', 'kind'), [('orders', 'orders'), ('payments', 'payments')])
async def test_counts_equal_the_detail_total(
    client: AsyncClient, may: dict, measure: str, kind: str
) -> None:
    flag = {'flag': 'collected'} if kind == 'payments' else {}

    page = await _detail(client, kind, **flag)

    assert page['total'] == int((await _query(client, measure))[measure])


async def test_derived_measures_reconcile_through_their_parts(
    client: AsyncClient, may: dict
) -> None:
    values = await _query(client, 'net_sales', 'units', 'cost_of_sales', 'gross_margin')
    lines, refunds = (
        (await _detail(client, 'lines'))['sums'],
        (await _detail(client, 'refunds'))['sums'],
    )
    notes = (await _detail(client, 'credit_notes'))['sums']

    def d(sums: dict, key: str) -> Decimal:
        return Decimal(sums[key])

    assert d(lines, 'amount') - d(refunds, 'amount') - d(notes, 'amount') == Decimal(
        values['net_sales']
    )
    assert d(lines, 'quantity') - d(refunds, 'quantity') == Decimal(values['units'])
    assert d(lines, 'cost') - d(refunds, 'cost') == Decimal(values['cost_of_sales'])
    assert d(lines, 'margin') - d(refunds, 'margin') == Decimal(values['gross_margin'])


async def test_line_amounts_carry_the_residual(client: AsyncClient, may: dict) -> None:
    """Three 0.125 lines round to 0.13 each, but the order to 0.38: the lines still sum to it."""
    order = await _detail(
        client, 'orders', filters=[{'dimension': 'customer', 'values': ['1']}], sort='date'
    )
    lines = await _all_pages(client, 'lines', sort='date')
    first = order['items'][0]

    own = [i for i in lines if i['document_id'] == first['document_id']]
    assert sum(Decimal(i['amount']) for i in own) == Decimal(first['amount'])


@pytest.mark.parametrize('sort', ['date', '-date', 'amount', '-amount', 'document', '-document'])
async def test_sorting(client: AsyncClient, may: dict, sort: str) -> None:
    items = (await _detail(client, 'orders', sort=sort))['items']

    field = {'date': 'date', 'amount': 'amount', 'document': 'document_id'}[sort.lstrip('-')]
    values = [Decimal(i[field]) if field == 'amount' else i[field] for i in items]
    assert values == sorted(values, reverse=sort.startswith('-'))


async def test_paging(client: AsyncClient, may: dict) -> None:
    first = await _detail(client, 'lines', skip=0, limit=2, sort='document')
    second = await _detail(client, 'lines', skip=2, limit=2, sort='document')

    assert first['total'] == second['total'] == 5
    assert len(first['items']) == 2 and len(second['items']) == 2
    assert not {i['id'] for i in first['items']} & {i['id'] for i in second['items']}


async def test_each_item_names_its_document(client: AsyncClient, may: dict) -> None:
    lines = (await _detail(client, 'lines', sort='document'))['items']
    refunds = (await _detail(client, 'refunds'))['items']

    assert lines[0]['document_id'] == may['first'] and lines[0]['id'] == may['lines'][0]
    assert lines[0]['product_code'] == 'P1' and lines[0]['customer_name'] == 'Cliente Uno'
    assert refunds[0]['kind'] == 'refunds' and refunds[0]['document_id'] == may['refund']


async def test_the_cost_flags_list_what_the_fact_counts(client: AsyncClient, may: dict) -> None:
    below = await _detail(client, 'lines', flag='below_cost')
    without = await _detail(client, 'lines', flag='without_cost')

    assert [i['product'] for i in below['items']] == [2]  # 58 under a cost of 70
    assert below['total'] == 1
    assert without['total'] == 3
    assert {i['cost'] for i in without['items']} == {None}


async def test_overdue_lists_the_receivables_overdue_set(
    client: AsyncClient, db: AsyncSession, may: dict
) -> None:
    await sale(
        db, datetime(2026, 2, 6, 11, 0), [L('1', '1160')], customer=CREDIT_CUSTOMER,
        terms=PaymentTerms.NET_D,
    )  # fmt: skip

    page = await _detail(client, 'orders', flag='overdue')
    expected = await sales_order_service.open_credit_orders(
        db, facility=None, due_before=local_now()
    )

    assert page['total'] == len(expected) == 1
    assert Decimal(page['items'][0]['amount']) == expected[0][1].balance
    assert page['sums']['amount'] == page['items'][0]['amount']


async def test_every_alert_drills_to_exactly_its_count(
    client: AsyncClient, db: AsyncSession, may: dict
) -> None:
    await sale(
        db, datetime(2026, 2, 6, 11, 0), [L('1', '1160')], customer=CREDIT_CUSTOMER,
        terms=PaymentTerms.NET_D,
    )  # fmt: skip
    response = await client.get('/api/v1/analytics/dashboard', params=MAY)
    alerts = response.json()['alerts']

    assert {a['key'] for a in alerts} >= {
        'overdue_credit_orders', 'lines_below_cost', 'lines_without_cost',
    }  # fmt: skip
    for alert in alerts:
        listed = await client.post('/api/v1/analytics/detail', json=alert['detail'])
        assert listed.status_code == 200, listed.text
        assert listed.json()['total'] == alert['count'], alert['key']
        assert listed.json()['sums']['amount'] == alert['amount'], alert['key']
