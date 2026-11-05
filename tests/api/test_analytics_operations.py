"""The spec 038 analytics routes: authentication, privileges per family, scope and the tiles.

As in `test_analytics.py`, these run the real engine on the integration database, so a missing
privilege is seen in a real response rather than a mocked one.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.main import app
from app.models.user import AccessPrivilege
from app.services.analytics import cache
from tests.integration.conftest import db, engine, seeded, sessions  # noqa: F401  (fixtures)
from tests.integration.inventory_seed import Inventory, cost, product, setup_inventory
from tests.integration.stock_seed import T, entry
from tests.problem import assert_problem

BASE = '/api/v1/analytics'
MAY = {'start': '2026-05-01', 'end': '2026-05-31'}
COST = (37, 38, 39)
INVENTORY = (67, 32, 99)
STOCK_REPORT = SystemObject.WAREHOUSE_STOCK_REPORT
SALES = SystemObject.SALES_BY_CUSTOMER
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)


@pytest.fixture
async def http(sessions: async_sessionmaker, seeded: None) -> AsyncIterator[AsyncClient]:  # noqa: F811
    async def _db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


@pytest.fixture(autouse=True)
def _clerk() -> Iterator[None]:
    """Every test runs as a non-administrator of facility 1."""
    app.dependency_overrides[get_current_user] = lambda: CLERK
    yield


async def grant(db: AsyncSession, *objects: int) -> None:  # noqa: F811
    for system_object in objects:
        db.add(
            AccessPrivilege(
                user_id='tester', system_object=int(system_object), privileges=AccessRight.READ
            )
        )
    await db.commit()


@pytest.fixture
async def stock(db: AsyncSession) -> Inventory:  # noqa: F811
    """At the end of May: a stock-out in facility 1, a dead and an uncosted pair, and stock in
    facility 2."""
    site = await setup_inventory(db)
    sold_out = await product(db, code='OUT')
    dead = await product(db, code='DEAD')
    uncosted = await product(db, code='UC')
    await cost(db, dead, '3')
    await cost(db, site.stockable, '2')
    await entry(db, sold_out, site.a.warehouse, '5', datetime(2026, 5, 2))
    await entry(db, sold_out, site.a.warehouse, '-5', datetime(2026, 5, 20), source=T.SALES_ORDER)
    await entry(db, dead, site.a.warehouse, '4', datetime(2025, 3, 1))
    await entry(db, uncosted, site.a.warehouse, '6', datetime(2026, 5, 3))
    await entry(db, site.stockable, site.b.warehouse, '10', datetime(2026, 5, 3))
    return site


def query(*measures: str, **definition: object) -> dict:
    return {'measures': list(measures), **MAY, **definition}


async def test_the_kardex_needs_a_token(http: AsyncClient) -> None:
    app.dependency_overrides.pop(get_current_user)

    response = await http.get(f'{BASE}/kardex', params={'product': 1, 'warehouse': 1, **MAY})

    assert_problem(response, 401, 'auth.not_authenticated')


async def test_inventory_measures_need_an_inventory_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES)

    answered = await http.post(f'{BASE}/query', json=query('on_hand'))
    kardex = await http.get(f'{BASE}/kardex', params={'product': 1, 'warehouse': 1, **MAY})

    body = assert_problem(answered, 403, 'auth.forbidden')
    assert sorted(g['system_object'] for g in body['params']['required']) == sorted(INVENTORY)
    assert_problem(kardex, 403, 'auth.forbidden')


async def test_stock_value_needs_the_cost_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, STOCK_REPORT)

    on_hand = await http.post(f'{BASE}/query', json=query('on_hand'))
    value = await http.post(f'{BASE}/query', json=query('on_hand', 'stock_value'))

    assert on_hand.status_code == 200, on_hand.text
    body = assert_problem(value, 403, 'auth.forbidden')
    assert sorted(g['system_object'] for g in body['params']['required']) == list(COST)


async def test_facility_scope_is_the_warehouses_facility(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    stock: Inventory,
) -> None:
    """A clerk of facility 1 sees the stock in facility 1's warehouses only, and can't open a
    kardex of another facility's warehouse."""
    await grant(db, STOCK_REPORT)

    answered = await http.post(f'{BASE}/query', json=query('on_hand', dimensions=['warehouse']))
    other = await http.get(
        f'{BASE}/kardex',
        params={'product': stock.stockable, 'warehouse': stock.b.warehouse, **MAY},
    )
    own = await http.get(
        f'{BASE}/kardex',
        params={'product': stock.stockable, 'warehouse': stock.a.warehouse, **MAY},
    )

    assert answered.status_code == 200, answered.text
    warehouses = {row['keys'][0]['value'] for row in answered.json()['rows']}
    assert warehouses == {str(stock.a.warehouse)}
    assert answered.json()['totals']['values']['on_hand'] == '10.0000'
    assert_problem(other, 403, 'facility.other_facility')
    assert own.status_code == 200, own.text
    assert 'unit_cost' not in own.json()


async def _dashboard(http: AsyncClient) -> dict:
    response = await http.get(f'{BASE}/dashboard', params=MAY)
    assert response.status_code == 200, response.text
    return response.json()


def _tiles(body: dict) -> tuple[set[str], set[str], set[str]]:
    return (
        {k['key'] for k in body['kpis']},
        {s['key'] for s in body['series']},
        {a['key'] for a in body['alerts']},
    )


async def test_the_dashboard_shows_inventory_only_to_an_inventory_reader(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    stock: Inventory,
) -> None:
    await grant(db, SALES)
    kpis, series, alerts = _tiles(await _dashboard(http))
    assert 'stock_value' not in kpis
    assert 'stock_value_by_facility' not in series
    assert not alerts & {'stock_outs', 'below_reorder', 'dead_stock', 'uncosted_stock'}

    await grant(db, STOCK_REPORT)
    cache.responses.clear()
    body = await _dashboard(http)
    kpis, series, alerts = _tiles(body)
    assert 'stock_value' not in kpis and 'stock_value_by_facility' not in series
    assert {'stock_outs', 'below_reorder', 'uncosted_stock'} <= alerts
    assert 'dead_stock' not in alerts
    by_key = {a['key']: a for a in body['alerts']}
    assert by_key['stock_outs']['count'] == 1
    assert by_key['stock_outs']['detail']['kind'] == 'stock'
    assert by_key['stock_outs']['detail']['flag'] == 'stock_out'
    assert by_key['uncosted_stock']['count'] == 1


async def test_value_tiles_need_the_cost_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    stock: Inventory,
) -> None:
    await grant(db, STOCK_REPORT, SystemObject.GROSS_PROFITS_BY_PRODUCT)
    body = await _dashboard(http)
    kpis, series, alerts = _tiles(body)

    assert 'stock_value' in kpis and 'stock_value_by_facility' in series and 'dead_stock' in alerts
    dead = next(a for a in body['alerts'] if a['key'] == 'dead_stock')
    assert (dead['count'], dead['amount']) == (1, '12.00')
    value = next(k for k in body['kpis'] if k['key'] == 'stock_value')
    assert value['value'] == '12.00'  # the dead pair; the clerk's scope leaves facility 2 out

    detail = await http.post(f'{BASE}/detail', json=dead['detail'] | {'limit': 100})
    assert detail.status_code == 200, detail.text
    assert detail.json()['total'] == dead['count']
    assert detail.json()['sums']['value'] == dead['amount']
