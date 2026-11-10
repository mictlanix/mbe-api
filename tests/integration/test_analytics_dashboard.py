"""Spec 037, T028 and T029: the dashboard against real tables (SQLite).

Every KPI must equal the query's answer for the same measure, period and scope (FR-031), and
every alert must count exactly what its drill-down lists (US3 scenario 3; the detail side is
checked in T036).
"""

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.enums import PaymentTerms
from app.services import sales_order_service
from app.services.analytics import balances, cache, engine
from tests.integration.analytics_seed import (
    CREDIT_CUSTOMER,
    L,
    payment,
    refund,
    sale,
    seed_catalog,
)
from tests.integration.seed import seed_facility

pytestmark = pytest.mark.usefixtures('seeded')

MAY = {'start': '2026-05-01', 'end': '2026-05-31'}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


async def _dashboard(client: AsyncClient, **params: str) -> dict:
    response = await client.get('/api/v1/analytics/dashboard', params={**MAY, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def _query(client: AsyncClient, body: dict) -> dict:
    response = await client.post('/api/v1/analytics/query', json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def may(db: AsyncSession) -> None:
    await seed_catalog(db)
    await sale(db, datetime(2026, 4, 8, 10, 0), [L('1', '116', cost='58')])
    await sale(db, datetime(2026, 5, 4, 9, 15), [L('2', '116', cost='58'), L('1', '58', product=2)])
    await sale(db, datetime(2026, 5, 12, 16, 0), [L('3', '232', product=3, cost='300')])
    await payment(db, datetime(2026, 5, 4, 9, 20), '290')


# ── T028: tiles ───────────────────────────────────────────────────────────────


async def test_each_fact_query_runs_once(
    client: AsyncClient, may: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tiles share their totals and comparisons, and the alerts reuse the tiles' facts (R9)."""
    ran: list = []
    run_fact = engine.run_fact

    async def counted(db: AsyncSession, fact, slice_):  # noqa: ANN001, ANN202
        ran.append(engine.fact_key(fact, slice_))
        return await run_fact(db, fact, slice_)

    monkeypatch.setattr(engine, 'run_fact', counted)

    await _dashboard(client)

    assert ran
    assert len(ran) == len(set(ran))


async def test_each_kpi_equals_the_query_for_its_measure(client: AsyncClient, may: None) -> None:
    body = await _dashboard(client)

    for kpi in body['kpis']:
        answered = await _query(client, kpi['drill'])
        measure = kpi['drill']['measures'][0]
        assert kpi['value'] == answered['totals']['values'][measure], kpi['key']
        assert kpi['comparison'] == answered['totals']['comparison'][measure], kpi['key']
    keys = [k['key'] for k in body['kpis']]
    assert keys == [
        'net_sales', 'margin_pct', 'cost_coverage', 'orders', 'average_ticket', 'collected',
        'collected_today', 'refunds', 'stock_value',  # spec 038: the administrator reads stock
        'receivable_overdue', 'dso',  # and receivables
        'uninvoiced_sales',  # and fiscal
    ]  # fmt: skip


async def test_average_ticket_drills_with_vat_and_the_rest_without(
    client: AsyncClient, may: None
) -> None:
    kpis = {k['key']: k for k in (await _dashboard(client))['kpis']}

    assert kpis['average_ticket']['drill']['vat'] == 'included'
    assert {k['drill']['vat'] for key, k in kpis.items() if key != 'average_ticket'} == {'excluded'}
    # 290 and 696 with VAT over two orders.
    assert kpis['average_ticket']['value'] == '493.00'


async def test_a_month_sparkline_is_daily_and_covers_every_day(
    client: AsyncClient, may: None
) -> None:
    net = next(k for k in (await _dashboard(client))['kpis'] if k['key'] == 'net_sales')

    assert len(net['sparkline']) == 31
    points = {p['date']: p['value'] for p in net['sparkline']}
    assert points['2026-05-04'] == '250.00'
    assert points['2026-05-05'] == '0.00'


async def test_the_series(client: AsyncClient, may: None) -> None:
    series = {s['key']: s for s in (await _dashboard(client))['series']}

    assert set(series) == {
        'sales_over_time', 'sales_by_facility', 'sales_by_label', 'sales_by_brand',
        'top_products', 'top_customers', 'payment_mix', 'heatmap',
        'stock_value_by_facility',  # spec 038
        'receivable_overdue_trend', 'top_debtors', 'collected_vs_due',
        'invoice_coverage',
    }  # fmt: skip
    over_time = series['sales_over_time']['query']
    assert over_time['totals']['comparison']['net_sales'] == '100.00'  # April 8
    assert series['top_products']['query']['definition']['top'] == 10
    assert series['top_customers']['query']['definition']['top'] == 10
    heat = {
        (r['keys'][0]['value'], r['keys'][1]['value']): r['values']['orders']
        for r in series['heatmap']['query']['rows']
    }
    assert heat == {('1', '9'): '1', ('2', '16'): '1'}
    assert series['payment_mix']['chart'] == 'donut'


async def test_collected_today_is_today_whatever_the_period(
    client: AsyncClient, db: AsyncSession, may: None
) -> None:
    await payment(db, local_now(), '75')

    kpis = {k['key']: k for k in (await _dashboard(client))['kpis']}

    assert kpis['collected_today']['value'] == '75.00'
    assert kpis['collected_today']['drill']['start'] == local_now().date().isoformat()
    assert kpis['collected']['value'] == '290.00'


# ── T029: alerts ──────────────────────────────────────────────────────────────


async def test_overdue_credit_orders_are_the_receivables_overdue_set(
    client: AsyncClient, db: AsyncSession, may: None
) -> None:
    await sale(
        db, datetime(2026, 5, 6, 11, 0), [L('1', '1160')], customer=CREDIT_CUSTOMER,
        terms=PaymentTerms.NET_D,
    )  # fmt: skip

    alerts = {a['key']: a for a in (await _dashboard(client))['alerts']}
    expected = await sales_order_service.open_credit_orders(
        db, facility=None, due_before=local_now()
    )

    overdue = alerts['overdue_credit_orders']
    assert overdue['count'] == len(expected) == 1
    assert Decimal(overdue['amount']) == sum(b.balance for _, b in expected)
    assert overdue['detail']['kind'] == 'orders' and overdue['detail']['flag'] == 'overdue'


async def test_lines_below_and_without_cost(client: AsyncClient, may: None) -> None:
    alerts = {a['key']: a for a in (await _dashboard(client))['alerts']}

    # May 12: price 232 below a cost of 300. May 4: the product-2 line has no cost.
    assert alerts['lines_below_cost']['count'] == 1
    assert alerts['lines_below_cost']['amount'] == '600.00'
    assert alerts['lines_without_cost']['count'] == 1
    assert alerts['lines_without_cost']['amount'] == '50.00'
    assert alerts['lines_without_cost']['detail']['vat'] == 'excluded'


async def _month(
    db: AsyncSession, month: date, gross: str, refunded: str, *, facility: int = 1
) -> None:
    """One sale of `gross` (with VAT) in `month`, and a refund of `refunded` of it."""
    at = datetime(month.year, month.month, 10, 10, 0)
    line = L('1', gross)
    order, lines = await sale(db, at, [line], facility=facility)
    if Decimal(refunded):
        await refund(
            db, order, [(lines[0], str(Decimal(refunded) / Decimal(gross)), line)],
            datetime(month.year, month.month, 20, 10, 0), facility=facility,
        )  # fmt: skip


@pytest.fixture
async def history(db: AsyncSession) -> None:
    """Twelve months at a 5% refund rate in facility 1, then May 2026 at 25% (fires), and
    facility 2 at 25% too but with less than $20,000 refunded (doesn't)."""
    await seed_facility(db)
    for offset in range(12):
        total = 2025 * 12 + 4 + offset  # May 2025 … April 2026
        await _month(db, date(total // 12, total % 12 + 1, 1), '116000', '5800')
        await _month(db, date(total // 12, total % 12 + 1, 1), '116000', '5800', facility=2)
    await _month(db, date(2026, 5, 1), '116000', '29000')
    await _month(db, date(2026, 5, 1), '87000', '21750', facility=2)


async def test_the_refund_rate_fires_per_facility_on_every_threshold(
    client: AsyncClient, history: None
) -> None:
    rates = [a for a in (await _dashboard(client))['alerts'] if a['key'] == 'refund_rate']

    assert [(a['facility'], a['count'], a['amount']) for a in rates] == [(1, 1, '25000.00')]
    assert rates[0]['detail']['filters'] == [{'dimension': 'facility', 'values': ['1']}]


async def test_a_normal_month_raises_no_refund_alert(client: AsyncClient, history: None) -> None:
    alerts = await _dashboard(client, start='2026-04-01', end='2026-04-30')

    assert [a for a in alerts['alerts'] if a['key'] == 'refund_rate'] == []


async def test_the_stock_comparison_is_read_at_its_end_only(
    client: AsyncClient, may: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 038, R18: the stock value KPI shows the comparison period's end, never its buckets, so
    the stock fact reads one moment before the period, not a sparkline's worth."""
    read: list = []
    many = balances.stock_many

    async def spied(db: AsyncSession, slices):  # noqa: ANN001, ANN202
        read.extend(m for s in slices for m in s.moments)
        return await many(db, slices)

    monkeypatch.setattr(balances, 'stock_many', spied)

    body = await _dashboard(client)

    assert 'stock_value' in {k['key'] for k in body['kpis']}
    assert {m for m in read if m < date(2026, 5, 1)} == {date(2026, 4, 30)}
