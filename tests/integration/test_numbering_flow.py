"""Spec 035, T017 and T025: per-facility numbering and the stock check, through the API.

SQLite cannot show a race, so these pin the sequential behaviour the locks must preserve: each
confirmation takes its facility's next serial for its document type, a refused confirmation takes
none, two facilities number independently, and a sales order confirmed twice reserves its stock
once. The races themselves are for MariaDB (spec 035, SC-001 and SC-005).

Delivery orders and refunds take their facility from the sales order they are raised from, so a
second-facility document is one whose source order is moved there.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TransactionType
from app.models.inventory import LotSerialRqmt
from app.models.logistics import DeliveryOrder
from app.models.sales import CustomerRefund, SalesOrder, SalesQuote
from app.services import stock_ledger
from tests.integration.inventory_seed import Inventory, setup_inventory, stock
from tests.integration.seed import seed_sales_order

pytestmark = pytest.mark.usefixtures('seeded')

MODELS = {
    'sales order': SalesOrder,
    'sales quote': SalesQuote,
    'delivery order': DeliveryOrder,
    'customer refund': CustomerRefund,
}


@pytest.fixture
async def inventory(db: AsyncSession) -> Inventory:
    """Facility 1 (the baseline) and facility 2, each with a warehouse and a stockable product."""
    return await setup_inventory(db)


@pytest.fixture
async def cash_session(client: AsyncClient) -> None:
    """A refund needs the cashier's open session."""
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '0'}
    )
    assert opened.status_code == 201, opened.text


async def _max_serial(db: AsyncSession, model: type, facility: int) -> int:
    query = select(func.max(model.serial)).where(model.facility == facility)
    return (await db.execute(query)).scalar_one_or_none() or 0


async def _draft_sales_order(
    client: AsyncClient,
    db: AsyncSession,
    *,
    facility: int = 1,
    product: int = 1,
    quantity: str = '1',
    price: str = '100',
) -> int:
    created = await client.post('/api/v1/sales-orders', json={'customer': 1, 'payment_terms': 0})
    assert created.status_code == 201, created.text
    order_id = created.json()['sales_order_id']
    lined = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines',
        json={'product': product, 'quantity': quantity, 'warehouse': 1, 'price': price},
    )
    assert lined.status_code == 200, lined.text
    if facility != 1:
        await db.execute(
            update(SalesOrder)
            .where(SalesOrder.sales_order_id == order_id)
            .values(facility=facility)
        )
        await db.commit()
    return order_id


async def _draft_sales_quote(client: AsyncClient, db: AsyncSession, facility: int) -> int:
    created = await client.post('/api/v1/sales-quotes', json={'customer': 1, 'payment_terms': 0})
    assert created.status_code == 201, created.text
    quote_id = created.json()['sales_quote_id']
    lined = await client.post(
        f'/api/v1/sales-quotes/{quote_id}/lines', json={'product': 1, 'quantity': '1'}
    )
    assert lined.status_code == 200, lined.text
    await db.execute(
        update(SalesQuote).where(SalesQuote.sales_quote_id == quote_id).values(facility=facility)
    )
    await db.commit()
    return quote_id


async def _completed_sale(db: AsyncSession, facility: int) -> int:
    """A paid, completed sale of `facility`, the source of a delivery order or a refund."""
    order_id = await seed_sales_order(db, completed=True, paid=True)
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == order_id).values(facility=facility)
    )
    await db.commit()
    return order_id


async def _confirm(kind: str, client: AsyncClient, db: AsyncSession, facility: int) -> int:
    """Draft a document of `kind` in `facility`, confirm it and return the serial it got."""
    if kind == 'sales order':
        order_id = await _draft_sales_order(client, db, facility=facility)
        response = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    elif kind == 'sales quote':
        quote_id = await _draft_sales_quote(client, db, facility)
        response = await client.post(f'/api/v1/sales-quotes/{quote_id}/confirm')
    elif kind == 'delivery order':
        sale = await _completed_sale(db, facility)
        raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sale})
        assert raised.status_code == 201, raised.text
        delivery_id = raised.json()['delivery_order_id']
        response = await client.post(f'/api/v1/delivery-orders/{delivery_id}/confirm')
    else:
        sale = await _completed_sale(db, facility)
        raised = await client.post('/api/v1/customer-refunds', json={'sales_order': sale})
        assert raised.status_code == 201, raised.text
        refund_id = raised.json()['customer_refund_id']
        line_id = raised.json()['lines'][0]['customer_refund_detail_id']
        entered = await client.put(
            f'/api/v1/customer-refunds/{refund_id}/lines/{line_id}', json={'quantity': '1'}
        )
        assert entered.status_code == 200, entered.text
        response = await client.post(
            f'/api/v1/customer-refunds/{refund_id}/confirm', json={'payout': 'cash'}
        )
    assert response.status_code == 200, response.text
    return response.json()['serial']


@pytest.mark.parametrize('kind', MODELS)
async def test_three_confirmations_take_the_next_three_serials(
    kind: str, client: AsyncClient, db: AsyncSession, inventory: Inventory, cash_session: None
) -> None:
    before = await _max_serial(db, MODELS[kind], 1)

    serials = [await _confirm(kind, client, db, 1) for _ in range(3)]

    assert serials == [before + 1, before + 2, before + 3]


@pytest.mark.parametrize('kind', MODELS)
async def test_a_second_facility_numbers_independently(
    kind: str, client: AsyncClient, db: AsyncSession, inventory: Inventory, cash_session: None
) -> None:
    model = MODELS[kind]
    a, b = inventory.a.facility, inventory.b.facility
    a_before, b_before = await _max_serial(db, model, a), await _max_serial(db, model, b)

    first_a = await _confirm(kind, client, db, a)
    first_b = await _confirm(kind, client, db, b)
    second_a = await _confirm(kind, client, db, a)

    assert (first_a, second_a) == (a_before + 1, a_before + 2)
    assert first_b == b_before + 1
    assert await _max_serial(db, model, b) == b_before + 1


async def test_a_refused_confirmation_takes_no_serial(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    before = await _max_serial(db, SalesOrder, 1)
    first = await _confirm('sales order', client, db, 1)

    zero = await _draft_sales_order(client, db, price='0')
    unpriced = await client.post(f'/api/v1/sales-orders/{zero}/confirm')
    short = await _draft_sales_order(client, db, product=inventory.stockable, quantity='3')
    unstocked = await client.post(f'/api/v1/sales-orders/{short}/confirm')

    assert unpriced.status_code == 409, unpriced.text
    assert unpriced.json()['detail']['message'] == 'Order has lines priced at zero'
    assert unstocked.status_code == 409, unstocked.text
    assert unstocked.json()['detail']['message'] == 'Insufficient stock'
    assert await _max_serial(db, SalesOrder, 1) == first == before + 1
    assert await _confirm('sales order', client, db, 1) == before + 2


# ── Stock under confirmation (T025) ───────────────────────────────────────────


async def _reservations(db: AsyncSession, order_id: int) -> int:
    query = select(func.count()).where(
        LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
        LotSerialRqmt.reference == order_id,
    )
    return (await db.execute(query)).scalar_one()


async def test_a_second_order_for_the_same_five_units_is_refused(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    product = inventory.stockable
    await stock(db, product, 1, '5')
    first = await _draft_sales_order(client, db, product=product, quantity='5')
    second = await _draft_sales_order(client, db, product=product, quantity='5')

    confirmed = await client.post(f'/api/v1/sales-orders/{first}/confirm')
    refused = await client.post(f'/api/v1/sales-orders/{second}/confirm')

    assert confirmed.status_code == 200, confirmed.text
    assert refused.status_code == 409, refused.text
    detail = refused.json()['detail']
    assert detail['message'] == 'Insufficient stock'
    assert detail['lines']
    assert await _reservations(db, second) == 0
    assert await _reservations(db, first) == 1
    assert await stock_ledger.available(db, product=product, warehouse=1) == 0
    drafted = await client.get(f'/api/v1/sales-orders/{second}')
    assert drafted.json()['serial'] is None


async def test_confirming_a_confirmed_order_changes_nothing(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    product = inventory.stockable
    await stock(db, product, 1, '5')
    order_id = await _draft_sales_order(client, db, product=product, quantity='5')
    first = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    assert first.status_code == 200, first.text

    again = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

    assert again.status_code == 409, again.text
    assert again.json()['detail'] == 'Document is completed and can no longer be edited'
    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert read.json()['serial'] == first.json()['serial']
    assert await _max_serial(db, SalesOrder, 1) == first.json()['serial']
    assert await _reservations(db, order_id) == 1


async def test_a_line_cannot_be_added_to_a_confirmed_order(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    order_id = await _draft_sales_order(client, db)
    confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    assert confirmed.status_code == 200, confirmed.text

    added = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'quantity': '1'}
    )

    assert added.status_code == 409, added.text
    assert added.json()['detail'] == 'Document is completed and can no longer be edited'
    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert len(read.json()['lines']) == 1
