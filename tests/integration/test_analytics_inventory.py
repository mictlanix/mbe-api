"""Spec 038, T014: inventory measures against real tables (SQLite), research R4 and R5.

Every threshold of R5 has a fixture pair on each side of it, read as of the end of June 30, 2026,
a past day, so available is on hand. The `stock` and `movements` detail kinds must list exactly
what the query counts.
"""

from collections.abc import Iterator
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.analytics import cache
from tests.integration.analytics_seed import L, sale
from tests.integration.inventory_seed import cost, product
from tests.integration.stock_seed import T, entry
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

JUNE = {'start': '2026-06-01', 'end': '2026-06-30'}
W = 1  # the baseline's warehouse, facility 1
#: Velocity over 30 days, so the thresholds below are easy to read: 30 units sold is 1 a day.
THIRTY = {'velocity_days': 30, 'lead_days': 7, 'cover_days': 30, 'slow_cover_days': 180}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


async def _query(client: AsyncClient, *measures: str, products: list[int], **body: object) -> dict:
    response = await client.post(
        '/api/v1/analytics/query',
        json={
            'measures': list(measures),
            'filters': [{'dimension': 'product', 'values': [str(p) for p in products]}],
            **JUNE,
            **body,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()['totals']['values']


async def _detail(client: AsyncClient, kind: str, **body: object) -> dict:
    response = await client.post(
        '/api/v1/analytics/detail', json={'kind': kind, **JUNE, 'limit': 100, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _product(db: AsyncSession, code: str, *entries: tuple) -> int:
    """A product with entries `(qty, when[, type])` in warehouse 1."""
    found = await product(db, code=code)
    for qty, at, *source in entries:
        await entry(db, found, W, qty, at, source=source[0] if source else T.INVENTORY_ADJUSTMENT)
    return found


START = datetime(2025, 3, 1)
IN_JUNE = datetime(2026, 6, 15, 10)


# ── Velocity and cover ────────────────────────────────────────────────────────


async def test_velocity_over_30_90_and_365_days_nets_refunds(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Sales of every selling type count, refunds net them, other outflows don't; each window
    takes the entries from its first day's midnight up to the end of June 30."""
    v = await _product(
        db, 'V',
        ('500', START),
        ('-30', IN_JUNE, T.DELIVERY_PICKUP),
        ('6', datetime(2026, 6, 20), T.CUSTOMER_REFUND),
        ('-5', datetime(2026, 6, 10), T.INVENTORY_ISSUE),
        ('-1', datetime(2026, 5, 31, 23), T.DELIVERY_ORDER),  # the day before the 30-day window
        ('-60', datetime(2026, 4, 2, 8), T.SALES_ORDER),  # the 90-day window's first day
        ('-7', datetime(2026, 4, 1, 23), T.SALES_ORDER),
        ('-90', datetime(2025, 7, 1, 10), T.SALES_ORDER),  # the 365-day window's first day
        ('-11', datetime(2025, 6, 30, 10), T.SALES_ORDER),
    )  # fmt: skip

    velocity = {
        days: (
            await _query(
                client, 'sales_velocity', products=[v], stock_options={'velocity_days': days}
            )
        )['sales_velocity']
        for days in (30, 90, 365)
    }

    assert velocity == {30: '0.8000', 90: '0.9444', 365: '0.4986'}  # 24/30, 85/90, 182/365


async def test_days_of_cover_is_null_without_sales(client: AsyncClient, db: AsyncSession) -> None:
    idle = await _product(db, 'Z', ('5', START))
    selling = await _product(db, 'C', ('60', START), ('-30', IN_JUNE, T.SALES_ORDER))

    assert (await _query(client, 'days_of_cover', products=[idle], stock_options=THIRTY)) == {
        'days_of_cover': None
    }
    found = await _query(client, 'days_of_cover', products=[selling], stock_options=THIRTY)
    assert found['days_of_cover'] == '30.0'  # 30 on hand at 1 a day


# ── Stock-outs and restock ────────────────────────────────────────────────────


@pytest.fixture
async def restock(db: AsyncSession) -> dict[str, int]:
    """Each pair sells 30 units in June (1 a day), so its reorder point is 37 (7 + 30 days)."""
    sold = ('-30', IN_JUNE, T.SALES_ORDER)
    return {
        'out': await _product(db, 'OUT', ('30', START), sold),  # 0 on hand
        'one': await _product(db, 'ONE', ('31', START), sold),  # 1 on hand
        'at_point': await _product(db, 'AT', ('67', START), sold),  # 37: not below
        'fraction': await _product(
            db,
            'FR',
            ('65.5', START),
            ('-1.5', IN_JUNE, T.SALES_ORDER),
            ('-28.5', IN_JUNE, T.SALES_ORDER),
        ),  # fmt: skip
        'whole': await _product(db, 'WH', ('65.5', START), sold),  # 35.5, sold in whole units
        'idle_empty': await _product(db, 'IE', ('5', START), ('-5', START)),  # no sales, 0
    }


async def test_stock_outs_need_sales_and_nothing_available(
    client: AsyncClient, restock: dict[str, int]
) -> None:
    for name, expected in (('out', '1'), ('one', '0'), ('idle_empty', '0')):
        found = await _query(
            client, 'stock_out_pairs', products=[restock[name]], stock_options=THIRTY
        )
        assert found['stock_out_pairs'] == expected, name


async def test_below_reorder_and_the_suggested_quantity(
    client: AsyncClient, restock: dict[str, int]
) -> None:
    """Reorder point − available, rounded up for a whole-unit product, not for one that sells
    fractions."""
    expected = {
        'out': ('1', '37.0000'),
        'one': ('1', '36.0000'),
        'at_point': ('0', '0.0000'),
        'fraction': ('1', '1.5000'),
        'whole': ('1', '2.0000'),
        'idle_empty': ('0', '0.0000'),
    }
    for name, (below, suggested) in expected.items():
        found = await _query(
            client, 'below_reorder_pairs', 'suggested_quantity', products=[restock[name]],
            stock_options=THIRTY,
        )  # fmt: skip
        assert (found['below_reorder_pairs'], found['suggested_quantity']) == (below, suggested), (
            name
        )


# ── Dead and slow stock ───────────────────────────────────────────────────────


@pytest.fixture
async def aging(db: AsyncSession) -> dict[str, int]:
    return {
        # Last entry 180 days before June 30: dead at 90 and 180 days, not at 365.
        'day_180': await _product(db, 'D180', ('4', datetime(2026, 1, 1, 12))),
        # One day later: dead only at 90.
        'day_179': await _product(db, 'D179', ('4', datetime(2026, 1, 2, 9))),
        # 365 days before: dead at every window.
        'day_365': await _product(db, 'D365', ('4', datetime(2025, 6, 30, 9))),
        # 1 sold in June over 30 days: 7 on hand covers 210 days, 6 covers exactly 180.
        'slow': await _product(db, 'SL', ('8', START), ('-1', IN_JUNE, T.SALES_ORDER)),
        'not_slow': await _product(db, 'SL6', ('7', START), ('-1', IN_JUNE, T.SALES_ORDER)),
    }


async def test_dead_stock_at_90_180_and_365_days(
    client: AsyncClient, aging: dict[str, int]
) -> None:
    expected = {
        90: {'day_180': '4.0000', 'day_179': '4.0000', 'day_365': '4.0000'},
        180: {'day_180': '4.0000', 'day_179': '0.0000', 'day_365': '4.0000'},
        365: {'day_180': '0.0000', 'day_179': '0.0000', 'day_365': '4.0000'},
    }
    for days, by_name in expected.items():
        for name, quantity in by_name.items():
            found = await _query(
                client, 'dead_stock_quantity', products=[aging[name]],
                stock_options={'dead_days': days},
            )  # fmt: skip
            assert found['dead_stock_quantity'] == quantity, (days, name)


async def test_slow_stock_is_cover_above_the_limit(
    client: AsyncClient, aging: dict[str, int]
) -> None:
    for name, quantity in (('slow', '7.0000'), ('not_slow', '6.0000'), ('day_180', '4.0000')):
        found = await _query(
            client, 'slow_stock_quantity', 'dead_stock_quantity', products=[aging[name]],
            stock_options=THIRTY,
        )  # fmt: skip
        slow = quantity if name == 'slow' else '0.0000'
        assert found['slow_stock_quantity'] == slow, name  # a dead pair is never slow


# ── Turnover ──────────────────────────────────────────────────────────────────


async def test_turnover_and_days_of_inventory(client: AsyncClient, db: AsyncSession) -> None:
    """Cost of sales ÷ the average of the stock value at the start and at the end (R4)."""
    tv = await _product(
        db, 'TV', ('100', datetime(2026, 5, 10)), ('-40', datetime(2026, 6, 10), T.SALES_ORDER)
    )
    await cost(db, tv, '10')
    await sale(db, datetime(2026, 6, 10, 9), [L('40', '20', cost='10', product=tv)])

    found = await _query(client, 'turnover', 'days_of_inventory', 'stock_value', products=[tv])

    # Value 1,000 on May 31 and 600 on June 30: average 800; cost of sales 400.
    assert found == {'turnover': '0.5000', 'days_of_inventory': '60.0', 'stock_value': '600.00'}


# ── Refusals ──────────────────────────────────────────────────────────────────


async def test_a_period_before_the_history_is_refused(client: AsyncClient) -> None:
    body = {'measures': ['on_hand'], 'start': '2025-02-06', 'end': '2025-03-31'}
    response = await client.post('/api/v1/analytics/query', json=body)

    problem = assert_problem(response, 422, 'analytics.before_history')
    assert problem['params'] == {
        'history': 'stock',
        'requested': '2025-02-06',
        'earliest': '2025-02-07',
    }

    # Turnover also reads the day before the period.
    opening = {**body, 'measures': ['turnover'], 'start': '2025-02-07'}
    refused = await client.post('/api/v1/analytics/query', json=opening)
    assert assert_problem(refused, 422, 'analytics.before_history')['params']['requested'] == (
        '2025-02-06'
    )


async def test_stock_options_out_of_range_are_refused(client: AsyncClient) -> None:
    body = {'measures': ['sales_velocity'], **JUNE, 'stock_options': {'velocity_days': 20}}
    response = await client.post('/api/v1/analytics/query', json=body)

    problem = assert_problem(response, 422, 'analytics.stock_options_invalid')
    assert problem['params'] == {'option': 'velocity_days', 'value': 20, 'min': 30, 'max': 365}


# ── Detail ────────────────────────────────────────────────────────────────────


async def test_each_stock_flag_lists_what_the_query_counts(
    client: AsyncClient, db: AsyncSession, restock: dict[str, int], aging: dict[str, int]
) -> None:
    """At the default options (velocity over 90 days, dead after 180), which the detail uses."""
    uncosted = await _product(db, 'UC', ('3', START))
    await cost(db, uncosted, '99999')
    costed = await _product(db, 'CO', ('2', datetime(2026, 5, 1)))
    await cost(db, costed, '5')
    await cost(db, aging['day_180'], '2.5')
    products = [*restock.values(), *aging.values(), uncosted, costed]
    filters = [{'dimension': 'product', 'values': [str(p) for p in products]}]
    measures = (
        'on_hand', 'stock_value', 'stock_out_pairs', 'below_reorder_pairs', 'suggested_quantity',
        'dead_stock_quantity', 'dead_stock_value', 'slow_stock_quantity', 'uncosted_quantity',
    )  # fmt: skip
    totals = await _query(client, *measures, products=products)

    everything = await _detail(client, 'stock', filters=filters)
    assert everything['sums']['quantity'] == totals['on_hand']
    assert everything['sums']['value'] == totals['stock_value']

    stock_out = await _detail(client, 'stock', filters=filters, flag='stock_out')
    assert str(stock_out['total']) == totals['stock_out_pairs'] != '0'

    below = await _detail(client, 'stock', filters=filters, flag='below_reorder')
    assert str(below['total']) == totals['below_reorder_pairs'] != '0'
    assert below['sums']['suggested'] == totals['suggested_quantity']

    dead = await _detail(client, 'stock', filters=filters, flag='dead')
    assert dead['sums']['quantity'] == totals['dead_stock_quantity'] != '0.0000'
    assert dead['sums']['value'] == totals['dead_stock_value'] == '10.00'

    slow = await _detail(client, 'stock', filters=filters, flag='slow')
    assert slow['sums']['quantity'] == totals['slow_stock_quantity']

    flagged = await _detail(client, 'stock', filters=filters, flag='uncosted')
    assert flagged['sums']['quantity'] == totals['uncosted_quantity'] != '0.0000'
    assert {item['product'] for item in flagged['items']} >= {uncosted}

    item = next(i for i in dead['items'] if i['product'] == aging['day_180'])
    assert item['warehouse'] == W and item['facility'] == 1
    assert item['document_id'] is None and item['date'] is None and item['amount'] is None
    assert item['figures']['values']['on_hand'] == '4.0000'
    assert item['figures']['values']['value'] == '10.00'
    assert item['figures']['values']['last_movement'].startswith('2026-01-01')
    assert item['figures']['values']['reserved'] is None  # a past moment


async def test_the_movements_detail_lists_the_ledger_entries(
    client: AsyncClient, db: AsyncSession
) -> None:
    m = await _product(
        db, 'MV', ('9', datetime(2026, 6, 2)), ('-4', IN_JUNE, T.SALES_ORDER),
        ('3', datetime(2026, 5, 2)),
    )  # fmt: skip
    filters = [{'dimension': 'product', 'values': [str(m)]}]

    found = await _detail(client, 'movements', filters=filters)
    totals = await _query(client, 'units_in', 'units_out', 'movement_documents', products=[m])

    assert found['total'] == 2
    assert found['sums']['units_in'] == totals['units_in'] == '9.0000'
    assert found['sums']['units_out'] == totals['units_out'] == '4.0000'
    assert found['sums']['quantity'] == '5.0000'
    newest = found['items'][0]
    assert newest['quantity'] == '-4.0000' and newest['warehouse'] == W
    assert newest['figures']['values']['source'] == 'sales_order'
