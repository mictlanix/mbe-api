"""Delivery list rows carry the names they show, not just ids (#324, #325, #326).

The seed's customer 1 is "Cliente Uno", facility 1 "Matriz", warehouse 1 "Almacén", and address 1
is Av. Reforma 100, Centro, Ciudad de México.
"""

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import DeliveryOrderStatus, FulfillmentType
from app.models.logistics import DeliveryOrder
from tests.integration.seed import seed_sales_order

LABEL = 'Av. Reforma 100, Centro, Ciudad de México'


async def _delivery(
    client: AsyncClient, db: AsyncSession, *, ship_to: int | None, **values: object
) -> int:
    sale = await seed_sales_order(db, completed=True, paid=True)
    raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sale})
    assert raised.status_code == 201, raised.text
    delivery = raised.json()['delivery_order_id']
    await db.execute(
        update(DeliveryOrder)
        .where(DeliveryOrder.delivery_order_id == delivery)
        .values(ship_to=ship_to, **values)
    )
    await db.commit()
    return delivery


def _row(body: dict, delivery: int) -> dict:
    return next(row for row in body['items'] if row['delivery_order_id'] == delivery)


async def test_a_delivery_row_names_its_customer_destination_and_facility(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    delivery = await _delivery(client, db, ship_to=1)

    response = await client.get('/api/v1/delivery-orders')

    assert response.status_code == 200, response.text
    row = _row(response.json(), delivery)
    assert row['customer_display_name'] == 'Cliente Uno'
    assert row['ship_to_label'] == LABEL
    assert row['facility_name'] == 'Matriz'


async def test_a_counter_pickup_has_no_ship_to_label(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    delivery = await _delivery(client, db, ship_to=None, fulfillment_type=FulfillmentType.PICKUP)

    row = _row((await client.get('/api/v1/delivery-orders')).json(), delivery)

    assert row['ship_to_label'] is None
    assert row['customer_display_name'] == 'Cliente Uno'
    assert row['facility_name'] == 'Matriz'


async def test_the_approval_queue_carries_the_same_names(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    delivery = await _delivery(client, db, ship_to=1, status=DeliveryOrderStatus.PENDING_APPROVAL)

    response = await client.get('/api/v1/delivery-orders/approval')

    assert response.status_code == 200, response.text
    row = _row(response.json(), delivery)
    assert row['customer_display_name'] == 'Cliente Uno'
    assert row['ship_to_label'] == LABEL
    assert row['facility_name'] == 'Matriz'


async def test_a_pending_delivery_line_names_its_customer_destination_and_warehouse(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    delivery = await _delivery(
        client,
        db,
        ship_to=1,
        status=DeliveryOrderStatus.IN_PREPARATION,
        fulfillment_type=FulfillmentType.DELIVERY,
    )

    response = await client.get('/api/v1/delivery-itineraries/deliveries')

    assert response.status_code == 200, response.text
    (line,) = [
        row
        for bucket in response.json()['buckets']
        for row in bucket['items']
        if row['delivery_order'] == delivery
    ]
    assert line['customer_display_name'] == 'Cliente Uno'
    assert line['ship_to_label'] == LABEL
    assert line['warehouse_name'] == 'Almacén'
