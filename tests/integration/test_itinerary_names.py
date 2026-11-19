"""What an itinerary, its stops and its lines show, without a lookup per id (#343).

A stop keeps the delivery order it was added for, so a stop with no lines yet still names its
customer and ship-to. Lines carry their order, product and unit; rows carry the vehicle, operator
and warehouse names.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import DeliveryOrderStatus, FulfillmentType, ItineraryStatus
from tests.integration import delivery_seed as ds

SHIP_TO_LABEL = 'Calle Obra 45, Colonia Obra, CDMX'


async def _loadable_order(db: AsyncSession, *, customer: int = 1) -> int:
    return await ds.delivery_order(
        db,
        fulfillment_type=FulfillmentType.DELIVERY,
        status=DeliveryOrderStatus.IN_PREPARATION,
        customer=customer,
        ship_to=await ds.address(db),
        lines=[(1, '5', 1, None)],
    )


async def _open_trip(db: AsyncSession, **kwargs: object) -> int:
    return await ds.itinerary(db, status=ItineraryStatus.OPEN, **kwargs)


async def test_an_empty_stop_names_its_order_customer_and_ship_to(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    order = await _loadable_order(db)
    trip = await _open_trip(db)

    added = await client.post(
        f'/api/v1/delivery-itineraries/{trip}/stops', json={'delivery_order': order}
    )
    assert added.status_code == 200, added.text

    # Read back fresh: the order must be stored, not echoed from the request.
    read = await client.get(f'/api/v1/delivery-itineraries/{trip}')
    assert read.status_code == 200, read.text
    [stop] = read.json()['stops']
    assert stop['lines'] == []
    assert stop['delivery_order'] == order
    assert stop['delivery_orders'] == [order]
    assert stop['customer_display_name'] == 'Cliente Uno'
    assert stop['ship_to_label'] == SHIP_TO_LABEL


async def test_a_stop_with_lines_of_two_orders_lists_both(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    own = await _loadable_order(db)
    other = await _loadable_order(db, customer=await ds.customer(db, name='Otra Obra'))
    trip = await _open_trip(db)
    path = f'/api/v1/delivery-itineraries/{trip}'

    added = await client.post(f'{path}/stops', json={'delivery_order': other})
    stop_id = added.json()['stops'][0]['deliveries_itinerary_stop_id']
    # A stop takes lines of any order (06a §4): this one was opened for `other`, then loaded with
    # a line of `own` as well.
    for order in (own, other):
        [detail] = await ds.detail_ids(db, order)
        loaded = await client.post(
            f'{path}/stops/{stop_id}/lines', json={'delivery_order_detail': detail}
        )
        assert loaded.status_code == 200, loaded.text

    [stop] = (await client.get(path)).json()['stops']
    assert stop['delivery_order'] == other
    assert stop['delivery_orders'] == sorted([own, other])
    # The names follow the stored order, not whichever line came first.
    assert stop['customer_display_name'] == 'Otra Obra'
    assert stop['ship_to_label'] == SHIP_TO_LABEL
    assert [line['delivery_order'] for line in stop['lines']] == [own, other]


async def test_a_line_carries_its_order_product_and_unit(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    order = await _loadable_order(db)
    trip = await _open_trip(db)
    path = f'/api/v1/delivery-itineraries/{trip}'
    added = await client.post(f'{path}/stops', json={'delivery_order': order})
    stop_id = added.json()['stops'][0]['deliveries_itinerary_stop_id']

    loaded = await client.post(f'{path}/stops/{stop_id}/lines/all', json={'delivery_order': order})
    assert loaded.status_code == 200, loaded.text

    [line] = loaded.json()['stops'][0]['lines']
    assert line['delivery_order'] == order
    assert line['delivery_order_detail'] == (await ds.detail_ids(db, order))[0]
    assert line['product_code'] == 'P1'
    assert line['product_name'] == 'Producto Uno'
    assert line['unit_of_measurement'] == {
        'id': 'H87',
        'name': 'Pieza',
        'description': None,
        'symbol': 'pz',
    }


async def test_the_list_and_the_detail_name_vehicle_operator_and_warehouse(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    named = await _open_trip(db)
    bare = await ds.itinerary(
        db, status=ItineraryStatus.CLOSED, vehicle=False, operator=False, warehouse=None
    )
    expected = {
        named: ('Torton · ABC-123', 'Chofer Gomez', 'Almacén'),
        bare: (None, None, None),
    }

    def names(row: dict) -> tuple:
        return row['vehicle_name'], row['vehicle_operator_name'], row['warehouse_name']

    listed = await client.get('/api/v1/delivery-itineraries')
    assert listed.status_code == 200, listed.text
    assert {
        row['deliveries_itinerary_id']: names(row) for row in listed.json()['items']
    } == expected

    for trip, row_names in expected.items():
        read = await client.get(f'/api/v1/delivery-itineraries/{trip}')
        assert read.status_code == 200, read.text
        assert names(read.json()) == row_names


async def test_a_stop_without_a_stored_order_names_nothing(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The stops migration 008 made from legacy itineraries: no stored order, lines of several."""
    order = await _loadable_order(db)
    trip = await ds.itinerary(db, status=ItineraryStatus.CLOSED)
    [detail] = await ds.detail_ids(db, order)
    await ds.stop(db, trip, 1, [(detail, '5')])

    [stop] = (await client.get(f'/api/v1/delivery-itineraries/{trip}')).json()['stops']
    assert stop['delivery_order'] is None
    assert stop['delivery_orders'] == [order]
    assert stop['customer_display_name'] is None
    assert stop['ship_to_label'] is None
    assert stop['lines'][0]['delivery_order'] == order
