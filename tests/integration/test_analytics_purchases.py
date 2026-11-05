"""Spec 038, T055: purchases, price history and purchase against sale (SQLite), research R13.

Product 1's purchases, before tax:
- April 10, supplier S1, warehouse 1: 10 at 100.00 (before the period: only a previous price);
- May 4, S1, warehouse 1: 5 at 105.00 (+5%), with 2 of product 2 at 50.00 on the same order;
- May 12, S2, warehouse 3 of facility 2: 4 at 120.00 (+14.29%, flagged at the default 10%);
- May 20, S1, warehouse 1: 1 at 0 (a free unit, left out of the history), and 2 at 125.00 less
  20%, so 100.00 (−16.67% from 120.00, the price-0 line skipped, flagged);
- a cancelled order (May 25), a draft (May 26) and June's order count nowhere in May.

May therefore buys 1,305.00 in 14 units on 3 orders. Product 1 also sells 2 units at 150.00 before
VAT on May 6, so its average sale price is 150.00 against an average purchase price of 100.42.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, CurrencyCode, SystemObject
from app.main import app as fastapi_app
from app.models.purchases import PurchaseOrder, PurchaseOrderDetail
from app.models.user import AccessPrivilege
from app.services.analytics import cache
from tests.integration import inventory_seed
from tests.integration.analytics_seed import L, sale, seed_catalog
from tests.integration.seed import seed_facility
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

URL = '/api/v1/analytics'
MAY = {'start': '2026-05-01', 'end': '2026-05-31'}
D = Decimal
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


@dataclass(frozen=True)
class P:
    """One purchase line: product, warehouse, quantity, price before tax, discount rate."""

    product: int
    warehouse: int
    qty: str
    price: str
    discount: str = '0'


async def purchase(
    db: AsyncSession,
    supplier: int,
    at: datetime,
    lines: list[P],
    *,
    completed: bool = True,
    cancelled: bool = False,
) -> tuple[int, list[int]]:
    """A purchase order confirmed at `at`; its id and line ids. Every line says its price includes
    tax, which spec 031 ignores (FR-034)."""
    order = PurchaseOrder(
        supplier=supplier, creation_time=at, modification_time=at, creator=1, updater=1,
        completed=completed, cancelled=cancelled, approved=completed,
    )  # fmt: skip
    db.add(order)
    await db.flush()
    details = [
        PurchaseOrderDetail(
            purchase_order=order.purchase_order_id, product=line.product,
            warehouse=line.warehouse, quantity=D(line.qty), price=D(line.price),
            discount=D(line.discount), tax_rate=D('0.16'), product_code=f'P{line.product}',
            product_name='Producto', exchange_rate=D(1), currency=CurrencyCode.MXN,
            tax_included=True,
        )
        for line in lines
    ]  # fmt: skip
    db.add_all(details)
    await db.commit()
    return order.purchase_order_id, [d.purchase_order_detail_id for d in details]


@pytest.fixture
async def book(db: AsyncSession) -> dict[str, int]:
    other = await seed_facility(db)
    away = await inventory_seed.warehouse(db, facility=other, code='W3')
    await seed_catalog(db)
    s1 = await inventory_seed.supplier(db, code='S1', name='Uno')
    s2 = await inventory_seed.supplier(db, code='S2', name='Dos')
    _, [april] = await purchase(db, s1, datetime(2026, 4, 10, 9), [P(1, 1, '10', '100')])
    _, [may4, product2] = await purchase(
        db, s1, datetime(2026, 5, 4, 9), [P(1, 1, '5', '105'), P(2, 1, '2', '50')]
    )
    _, [may12] = await purchase(db, s2, datetime(2026, 5, 12, 9), [P(1, away, '4', '120')])
    _, [free, may20] = await purchase(
        db, s1, datetime(2026, 5, 20, 9), [P(1, 1, '1', '0'), P(1, 1, '2', '125', '0.2')]
    )
    await purchase(db, s1, datetime(2026, 5, 25, 9), [P(1, 1, '9', '500')], cancelled=True)
    await purchase(db, s1, datetime(2026, 5, 26, 9), [P(1, 1, '9', '1')], completed=False)
    await purchase(db, s1, datetime(2026, 6, 2, 9), [P(1, 1, '9', '200')])
    await sale(db, datetime(2026, 5, 6, 10), [L('2', '174')])
    return {
        's1': s1, 's2': s2, 'away': away, 'april': april, 'may4': may4, 'product2': product2,
        'may12': may12, 'free': free, 'may20': may20,
    }  # fmt: skip


async def _query(client: AsyncClient, *measures: str, **body: object) -> dict:
    response = await client.post(f'{URL}/query', json={'measures': list(measures), **MAY, **body})
    assert response.status_code == 200, response.text
    return response.json()


async def _detail(client: AsyncClient, **body: object) -> dict:
    response = await client.post(
        f'{URL}/detail', json={'kind': 'purchases', **MAY, 'limit': 100, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rows(body: dict, *measures: str) -> dict[tuple, tuple]:
    return {
        tuple(k['value'] for k in row['keys']): tuple(D(row['values'][m]) for m in measures)
        for row in body['rows']
    }


def _totals(body: dict) -> dict[str, Decimal | None]:
    return {k: None if v is None else D(v) for k, v in body['totals']['values'].items()}


async def _grant(db: AsyncSession, *objects: SystemObject) -> None:
    for system_object in objects:
        db.add(
            AccessPrivilege(
                user_id='tester', system_object=int(system_object), privileges=AccessRight.READ
            )
        )
    await db.commit()
    cache.responses.clear()


def _as_clerk() -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK


PURCHASES = ('purchases', 'purchase_quantity')
PRODUCT_1 = [{'dimension': 'product', 'values': ['1']}]


# ── Purchases ─────────────────────────────────────────────────────────────────


async def test_purchases_by_supplier_product_and_warehouse(
    client: AsyncClient, book: dict[str, int]
) -> None:
    total = await _query(client, *PURCHASES, 'purchase_orders', 'average_purchase_price')
    by_supplier = await _query(client, *PURCHASES, dimensions=['supplier'])
    by_product = await _query(client, *PURCHASES, dimensions=['product'])
    by_warehouse = await _query(client, *PURCHASES, dimensions=['warehouse'])

    assert _totals(total) == {
        'purchases': D('1305.00'), 'purchase_quantity': D(14), 'purchase_orders': D(3),
        'average_purchase_price': D('93.21'),
    }  # fmt: skip
    assert _rows(by_supplier, *PURCHASES) == {
        (str(book['s1']),): (D('825.00'), D(10)),
        (str(book['s2']),): (D('480.00'), D(4)),
    }
    assert _rows(by_product, *PURCHASES) == {
        ('1',): (D('1205.00'), D(12)),
        ('2',): (D('100.00'), D(2)),
    }
    assert _rows(by_warehouse, *PURCHASES) == {
        ('1',): (D('825.00'), D(10)),
        (str(book['away']),): (D('480.00'), D(4)),
    }


async def test_purchase_against_sale(client: AsyncClient, book: dict[str, int]) -> None:
    body = await _query(
        client, 'average_purchase_price', 'average_sale_price', 'purchase_sale_spread',
        'purchase_sale_spread_pct', filters=PRODUCT_1,
    )  # fmt: skip

    # 1,205.00 ÷ 12 = 100.42; 300.00 ÷ 2 = 150.00; 49.58 ÷ 150.00.
    assert _totals(body) == {
        'average_purchase_price': D('100.42'), 'average_sale_price': D('150.00'),
        'purchase_sale_spread': D('49.58'), 'purchase_sale_spread_pct': D('0.3305'),
    }  # fmt: skip


# ── Price history ─────────────────────────────────────────────────────────────


def _history(body: dict) -> dict[int, tuple]:
    return {
        item['id']: (
            item['figures']['values']['unit_price'],
            item['figures']['values']['previous_price'],
            item['figures']['values']['change'],
            item['figures']['values']['change_pct'],
        )
        for item in body['items']
    }


async def test_the_detail_has_the_previous_price_through_the_window(
    client: AsyncClient, book: dict[str, int]
) -> None:
    """The first May line's previous price is April's; the price-0 line is listed (it is a
    purchase) but is nobody's previous price and has no change."""
    listed = await _detail(client, filters=PRODUCT_1, sort='date')
    everything = await _detail(client)
    body = await _query(client, *PURCHASES)

    assert _history(listed) == {
        book['may4']: ('105.0000', '100.0000', '5.0000', '0.0500'),
        book['may12']: ('120.0000', '105.0000', '15.0000', '0.1429'),
        book['free']: ('0.0000', None, None, None),
        book['may20']: ('100.0000', '120.0000', '-20.0000', '-0.1667'),
    }
    assert [i['id'] for i in listed['items']] == [
        book['may4'], book['may12'], book['free'], book['may20'],
    ]  # fmt: skip
    may12 = next(i for i in listed['items'] if i['id'] == book['may12'])
    assert (may12['amount'], may12['quantity'], may12['warehouse'], may12['facility']) == (
        '480.00', '4.0000', book['away'], 2,
    )  # fmt: skip
    values = may12['figures']['values']
    assert (values['supplier'], values['supplier_name']) == (str(book['s2']), 'Dos')
    assert values['received'] == '0.0000'
    # Product 2 has no earlier purchase.
    assert _history(everything)[book['product2']][1:] == (None, None, None)
    # The sums are the query's.
    assert everything['total'] == 5
    assert D(everything['sums']['amount']) == _totals(body)['purchases']
    assert D(everything['sums']['quantity']) == _totals(body)['purchase_quantity']


async def test_price_change_flags_changes_above_the_threshold(
    client: AsyncClient, book: dict[str, int]
) -> None:
    default = await _detail(client, flag='price_change')
    wider = await _detail(client, flag='price_change', price_change_pct=15)
    widest = await _detail(client, flag='price_change', price_change_pct=20)

    assert {i['id'] for i in default['items']} == {book['may12'], book['may20']}
    assert default['total'] == 2
    assert {i['id'] for i in wider['items']} == {book['may20']}
    assert widest['items'] == []


# ── Who sees what ─────────────────────────────────────────────────────────────


async def test_purchases_need_20_or_75(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    _as_clerk()
    await _grant(db, SystemObject.SALES_BY_CUSTOMER)
    query = await client.post(f'{URL}/query', json={'measures': ['purchases'], **MAY})
    detail = await client.post(f'{URL}/detail', json={'kind': 'purchases', **MAY})

    body = assert_problem(query, 403, 'auth.forbidden')
    assert sorted(g['system_object'] for g in body['params']['required']) == [
        SystemObject.PURCHASES_ORDERS, SystemObject.PRODUCTS_BY_SUPPLIER_REPORT,
    ]  # fmt: skip
    assert_problem(detail, 403, 'auth.forbidden')

    # The report opens the family; the detail also needs read on purchase orders.
    await _grant(db, SystemObject.PRODUCTS_BY_SUPPLIER_REPORT)
    query = await client.post(f'{URL}/query', json={'measures': ['purchases'], **MAY})
    detail = await client.post(f'{URL}/detail', json={'kind': 'purchases', **MAY})
    assert query.status_code == 200, query.text
    assert_problem(detail, 403, 'auth.forbidden')

    await _grant(db, SystemObject.PURCHASES_ORDERS)
    detail = await client.post(f'{URL}/detail', json={'kind': 'purchases', **MAY})
    assert detail.status_code == 200, detail.text


async def test_the_spread_needs_the_cost_privilege_but_average_prices_do_not(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    _as_clerk()
    await _grant(db, SystemObject.PURCHASES_ORDERS)
    catalog = await client.get(f'{URL}/catalog')
    averages = await _query(client, 'average_purchase_price', 'average_sale_price')
    spread = await client.post(
        f'{URL}/query', json={'measures': ['purchase_sale_spread'], **MAY}
    )
    spread_pct = await client.post(
        f'{URL}/query', json={'measures': ['purchase_sale_spread_pct'], **MAY}
    )

    keys = {m['key'] for m in catalog.json()['measures']}
    assert {'average_purchase_price', 'average_sale_price'} <= keys
    assert not {'purchase_sale_spread', 'purchase_sale_spread_pct'} & keys
    assert _totals(averages)['average_purchase_price'] == D('82.50')  # facility 1: 825.00 ÷ 10
    body = assert_problem(spread, 403, 'auth.forbidden')
    assert sorted(g['system_object'] for g in body['params']['required']) == [
        SystemObject.GROSS_PROFITS_BY_CUSTOMER, SystemObject.GROSS_PROFITS_BY_SALES_PERSON,
        SystemObject.GROSS_PROFITS_BY_PRODUCT,
    ]  # fmt: skip
    assert_problem(spread_pct, 403, 'auth.forbidden')

    await _grant(db, SystemObject.GROSS_PROFITS_BY_PRODUCT)
    catalog = await client.get(f'{URL}/catalog')
    spread = await _query(client, 'purchase_sale_spread', 'purchase_sale_spread_pct')
    keys = {m['key'] for m in catalog.json()['measures']}
    assert {'purchase_sale_spread', 'purchase_sale_spread_pct'} <= keys
    assert set(_totals(spread)) == {'purchase_sale_spread', 'purchase_sale_spread_pct'}


async def test_purchases_scope_is_each_lines_warehouse_facility(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    """A clerk of facility 1 sees none of the May 12 line, bought into facility 2's warehouse."""
    _as_clerk()
    await _grant(db, SystemObject.PURCHASES_ORDERS)

    body = await _query(client, *PURCHASES, 'purchase_orders')
    listed = await _detail(client)
    elsewhere = await client.post(
        f'{URL}/query', json={'measures': ['purchases'], **MAY, 'facility': 2}
    )

    assert _totals(body) == {
        'purchases': D('825.00'), 'purchase_quantity': D(10), 'purchase_orders': D(2),
    }  # fmt: skip
    assert book['may12'] not in {i['id'] for i in listed['items']}
    assert {i['facility'] for i in listed['items']} == {1}
    assert_problem(elsewhere, 403, 'facility.other_facility')
