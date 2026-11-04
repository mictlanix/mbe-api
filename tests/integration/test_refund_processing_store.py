"""A refund belongs to the store that processes it, whichever store sold the order.

The goods go back to that store's own warehouses, its folio and payout are that store's, and another
store's refund answers as if it didn't exist without SearchCreditsFromAllStores (101), as another
store's sales orders do (spec 034).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, SystemObject, TransactionType
from app.main import app as fastapi_app
from app.models.core import PointSale
from app.models.inventory import LotSerialTracking
from app.models.product import Product
from app.models.sales import CustomerPayment, CustomerRefund, SalesOrder, SalesOrderDetail
from app.models.user import AccessPrivilege
from tests.integration.inventory_seed import warehouse
from tests.integration.seed import seed_facility, seed_sales_order
from tests.problem import assert_problem

URL = '/api/v1/customer-refunds'
ALL = int(AccessRight.CREATE | AccessRight.READ | AccessRight.UPDATE | AccessRight.DELETE)


@pytest.fixture
async def stores(client: AsyncClient, db: AsyncSession, seeded: None) -> dict[str, int]:
    """Facility 2 with its own warehouse, the caller's open session at facility 1, and product 1
    kept in stock, so a refund restocks it."""
    await db.execute(update(Product).where(Product.product_id == 1).values(stock_verification=True))
    await db.commit()
    other = await seed_facility(db)
    there = await warehouse(db, facility=other, code='W2')
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '0'}
    )
    assert opened.status_code == 201, opened.text
    counter = (await db.get(PointSale, 1)).warehouse
    return {'other': other, 'there': there, 'counter': counter}


async def _order(db: AsyncSession, facility: int, warehouse_id: int) -> int:
    """A paid order sold by `facility` from `warehouse_id`."""
    order = await seed_sales_order(db, completed=True, paid=True)
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == order).values(facility=facility)
    )
    await db.execute(
        update(SalesOrderDetail)
        .where(SalesOrderDetail.sales_order == order)
        .values(warehouse=warehouse_id)
    )
    await db.commit()
    return order


async def _open(client: AsyncClient, order: int) -> dict:
    opened = await client.post(URL, json={'sales_order': order})
    assert opened.status_code == 201, opened.text
    return opened.json()


async def _enter(client: AsyncClient, refund: dict, **changes: object) -> dict:
    line = refund['lines'][0]['customer_refund_detail_id']
    body = {'quantity': '1', **changes}
    entered = await client.put(f'{URL}/{refund["customer_refund_id"]}/lines/{line}', json=body)
    return entered


async def test_another_stores_sale_is_refunded_by_the_processing_store(
    client: AsyncClient, db: AsyncSession, stores: dict
) -> None:
    order = await _order(db, stores['other'], stores['there'])

    refund = await _open(client, order)
    assert (refund['facility'], refund['lines'][0]['warehouse']) == (1, stores['counter'])
    assert (await _enter(client, refund)).status_code == 200
    confirmed = await client.post(
        f'{URL}/{refund["customer_refund_id"]}/confirm', json={'payout': 'cash'}
    )

    assert confirmed.status_code == 200, confirmed.text
    payment = await db.get(CustomerPayment, confirmed.json()['customer_payment'])
    assert payment.facility == 1
    restocked = (
        (
            await db.execute(
                select(LotSerialTracking.warehouse).where(
                    LotSerialTracking.source == int(TransactionType.CUSTOMER_REFUND),
                    LotSerialTracking.reference == refund['customer_refund_id'],
                )
            )
        )
        .scalars()
        .all()
    )
    assert restocked == [stores['counter']]
    listed = await client.get(URL)
    assert refund['customer_refund_id'] in [r['customer_refund_id'] for r in listed.json()['items']]


async def test_without_a_counter_the_line_keeps_its_own_store_warehouse(
    client: AsyncClient, db: AsyncSession, stores: dict, current_user: CurrentUser
) -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        **{**current_user.__dict__, 'point_sale_id': None}
    )
    mine = await _open(client, await _order(db, 1, 1))
    theirs = await _open(client, await _order(db, stores['other'], stores['there']))

    assert mine['lines'][0]['warehouse'] == 1
    assert theirs['lines'][0]['warehouse'] is None
    assert (await _enter(client, theirs)).status_code == 200
    refused = await client.post(
        f'{URL}/{theirs["customer_refund_id"]}/confirm', json={'payout': 'cash'}
    )
    body = assert_problem(refused, 422, 'warehouse.required')
    assert body['params'] == {'resource': 'customer_refund'}


async def test_goods_go_back_only_to_the_stores_own_warehouses(
    client: AsyncClient, db: AsyncSession, stores: dict
) -> None:
    refund = await _open(client, await _order(db, 1, 1))

    refused = await _enter(client, refund, warehouse=stores['there'])
    allowed = await _enter(client, refund, warehouse=1)

    body = assert_problem(refused, 422, 'refund.warehouse_other_facility')
    assert body['params'] == {'warehouse_id': stores['there'], 'facility_id': 1}
    assert allowed.status_code == 200, allowed.text


class TestTheFacilityRule:
    """A clerk of facility 1 with every refund privilege but not SearchCreditsFromAllStores."""

    @pytest.fixture
    async def theirs(
        self, client: AsyncClient, db: AsyncSession, stores: dict, current_user: CurrentUser
    ) -> int:
        refund = await _open(client, await _order(db, 1, 1))
        await db.execute(
            update(CustomerRefund)
            .where(CustomerRefund.customer_refund_id == refund['customer_refund_id'])
            .values(facility=stores['other'])
        )
        for obj in (SystemObject.CUSTOMER_REFUNDS, SystemObject.CUSTOMER_REFUND_CONFIRM):
            db.add(AccessPrivilege(user_id='tester', system_object=int(obj), privileges=ALL))
        await db.commit()
        fastapi_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            **{**current_user.__dict__, 'administrator': False}
        )
        return refund['customer_refund_id']

    @pytest.mark.parametrize(
        ('method', 'path', 'body'),
        [
            ('get', '', None),
            ('get', '/ticket', None),
            ('post', '/confirm', {'payout': 'cash'}),
            ('post', '/cancel', None),
        ],
    )
    async def test_another_stores_refund_is_not_found(
        self, client: AsyncClient, theirs: int, method: str, path: str, body: dict | None
    ) -> None:
        response = await client.request(method, f'{URL}/{theirs}{path}', json=body)

        assert_problem(response, 404, 'resource.not_found')

    async def test_another_stores_list_is_forbidden(
        self, client: AsyncClient, theirs: int, stores: dict
    ) -> None:
        response = await client.get(URL, params={'facility': stores['other']})

        body = assert_problem(response, 403, 'facility.other_facility')
        assert body['params']['resource'] == 'customer_refund'

    async def test_the_cross_store_privilege_opens_them(
        self, client: AsyncClient, db: AsyncSession, theirs: int, stores: dict
    ) -> None:
        db.add(
            AccessPrivilege(
                user_id='tester',
                system_object=int(SystemObject.SEARCH_CREDITS_FROM_ALL_STORES),
                privileges=int(AccessRight.READ),
            )
        )
        await db.commit()

        read = await client.get(f'{URL}/{theirs}')
        listed = await client.get(URL, params={'facility': stores['other']})

        assert read.status_code == 200, read.text
        assert [r['customer_refund_id'] for r in listed.json()['items']] == [theirs]
