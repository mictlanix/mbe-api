"""A trip: built, loaded, dispatched, settled — the flow with the most rows behind it.

An itinerary is the case mocks serve worst. Committing a line moves quantity between two running
totals on `delivery_order_detail`, departure posts stock into the in-transit warehouse and
settlement posts it out again, and every guard reads a figure some earlier request wrote.

`SC-003` — ordered = delivered + returned + committed + open, on every line at every point — is an
invariant across rows, so a mocked session cannot even express it, let alone break it.

`PRAGMA foreign_keys=ON` matters here: a stop points at a delivery order and a line at a delivery
order line, so a wrong id fails rather than writing an orphan.
"""

import io
import json
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import DeliveryOrderStatus, ItineraryStatus
from app.models.logistics import DeliveryOrderDetail
from tests.integration.seed import seed_sales_order


def _png() -> bytes:
    """A real 1×1 PNG. The proof-image service validates the format, so a fake header is refused."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (1, 1)).save(buffer, format='PNG')
    return buffer.getvalue()


async def _approved_delivery_order(client: AsyncClient, db: AsyncSession) -> int:
    """A delivery order confirmed as far as the flow allows, ready to be loaded onto a trip."""
    sales_order = await seed_sales_order(db, completed=True, paid=True)
    raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sales_order})
    assert raised.status_code == 201, raised.text
    delivery_id = raised.json()['delivery_order_id']

    confirmed = await client.post(f'/api/v1/delivery-orders/{delivery_id}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    return delivery_id


async def test_an_itinerary_is_opened_and_read_back(client: AsyncClient, seeded: None) -> None:
    created = await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})

    assert created.status_code == 201, created.text
    itinerary_id = created.json()['deliveries_itinerary_id']
    assert created.json()['status'] == ItineraryStatus.OPEN

    read = await client.get(f'/api/v1/delivery-itineraries/{itinerary_id}')
    assert read.status_code == 200, read.text
    assert read.json()['stops'] == []


async def test_a_stop_is_added_and_its_lines_committed(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Committing is the write the whole flow turns on: open quantity becomes committed quantity."""
    delivery_id = await _approved_delivery_order(client, db)
    itinerary_id = (
        await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})
    ).json()['deliveries_itinerary_id']

    stopped = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops',
        json={'delivery_order': delivery_id},
    )
    assert stopped.status_code == 200, stopped.text
    stop_id = stopped.json()['stops'][0]['deliveries_itinerary_stop_id']

    loaded = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops/{stop_id}/lines/all',
        json={'delivery_order': delivery_id},
    )
    assert loaded.status_code == 200, loaded.text

    # SC-003 read off the row the request wrote: ten ordered, ten now claimed by this trip.
    line = (
        await db.execute(
            select(DeliveryOrderDetail).where(DeliveryOrderDetail.delivery_order == delivery_id)
        )
    ).scalars().one()
    await db.refresh(line)
    assert line.committed_quantity == Decimal('10')
    assert line.quantity - line.delivered_quantity - line.returned_quantity - (
        line.committed_quantity
    ) == Decimal('0')


async def test_the_same_line_cannot_be_committed_to_two_trips(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The double-assignment guard, which reads `open_quantity` off a row another trip changed."""
    delivery_id = await _approved_delivery_order(client, db)
    first = (await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})).json()
    second = (await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})).json()

    for itinerary in (first, second):
        stop = await client.post(
            f'/api/v1/delivery-itineraries/{itinerary["deliveries_itinerary_id"]}/stops',
            json={'delivery_order': delivery_id},
        )
        assert stop.status_code == 200, stop.text
        itinerary['stop'] = stop.json()['stops'][0]['deliveries_itinerary_stop_id']

    first_path = f'/api/v1/delivery-itineraries/{first["deliveries_itinerary_id"]}'
    claimed = await client.post(
        f'{first_path}/stops/{first["stop"]}/lines/all',
        json={'delivery_order': delivery_id},
    )
    assert claimed.status_code == 200, claimed.text

    second_path = f'/api/v1/delivery-itineraries/{second["deliveries_itinerary_id"]}'
    again = await client.post(
        f'{second_path}/stops/{second["stop"]}/lines/all',
        json={'delivery_order': delivery_id},
    )

    # Not an error: nothing is open, so there is nothing to load and the stop stays empty. The
    # invariant is what matters — the line is claimed once, not twice.
    assert again.status_code == 200, again.text
    assert again.json()['stops'][0]['lines'] == []
    line = (
        await db.execute(
            select(DeliveryOrderDetail).where(DeliveryOrderDetail.delivery_order == delivery_id)
        )
    ).scalars().one()
    await db.refresh(line)
    assert line.committed_quantity == Decimal('10')


async def test_departure_needs_something_to_carry(client: AsyncClient, seeded: None) -> None:
    """An empty trip cannot leave — the guard reads the stops, which is a query, not an argument."""
    itinerary_id = (
        await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})
    ).json()['deliveries_itinerary_id']

    response = await client.post(f'/api/v1/delivery-itineraries/{itinerary_id}/depart')

    assert response.status_code == 409, response.text


async def test_a_trip_departs_and_moves_its_goods_into_transit(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    delivery_id = await _approved_delivery_order(client, db)
    itinerary_id = (
        await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})
    ).json()['deliveries_itinerary_id']
    stop = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops', json={'delivery_order': delivery_id}
    )
    stop_id = stop.json()['stops'][0]['deliveries_itinerary_stop_id']
    await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops/{stop_id}/lines/all',
        json={'delivery_order': delivery_id},
    )

    departed = await client.post(f'/api/v1/delivery-itineraries/{itinerary_id}/depart')

    assert departed.status_code == 200, departed.text
    assert departed.json()['status'] == ItineraryStatus.DEPARTED
    # The delivery order follows the trip.
    order = await client.get(f'/api/v1/delivery-orders/{delivery_id}')
    assert order.json()['status'] == DeliveryOrderStatus.IN_TRANSIT


async def test_a_stop_is_settled_with_proof_and_per_line_outcomes(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Settlement: proof, stock out of transit, and the sale marked delivered (FR-043, FR-071)."""
    delivery_id = await _approved_delivery_order(client, db)
    itinerary_id = (
        await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})
    ).json()['deliveries_itinerary_id']
    stop = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops', json={'delivery_order': delivery_id}
    )
    stop_id = stop.json()['stops'][0]['deliveries_itinerary_stop_id']
    loaded = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops/{stop_id}/lines/all',
        json={'delivery_order': delivery_id},
    )
    line_id = loaded.json()['stops'][0]['lines'][0]['deliveries_itinerary_detail_id']
    await client.post(f'/api/v1/delivery-itineraries/{itinerary_id}/depart')

    settled = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops/{stop_id}/close',
        data={
            'receiver_name': 'Juan Pérez',
            'receiver_id_shown': 'INE 1234',
            # JSON inside a multipart field: the signature travels with the outcomes.
            'lines': json.dumps([{'line': line_id, 'delivered_quantity': '10'}]),
        },
        files={'image': ('signature.png', _png(), 'image/png')},
    )

    assert settled.status_code == 200, settled.text
    order = await client.get(f'/api/v1/delivery-orders/{delivery_id}')
    assert order.json()['status'] == DeliveryOrderStatus.DELIVERED
    # The proof is readable, and only through an authenticated route (FR-044a).
    proof = await client.get(f'/api/v1/delivery-orders/{delivery_id}/proof')
    assert proof.status_code == 200, proof.text
    assert proof.json()['receiver_name'] == 'Juan Pérez'


async def test_settling_without_proof_is_refused(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """FR-043 — no handover without evidence; the multipart parser enforces the shape."""
    delivery_id = await _approved_delivery_order(client, db)
    itinerary_id = (
        await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})
    ).json()['deliveries_itinerary_id']
    stop = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops', json={'delivery_order': delivery_id}
    )
    stop_id = stop.json()['stops'][0]['deliveries_itinerary_stop_id']

    response = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops/{stop_id}/close',
        data={'receiver_name': 'Juan', 'receiver_id_shown': 'INE', 'lines': '[]'},
    )

    assert response.status_code == 422, response.text


async def test_cancelling_a_trip_releases_what_it_held(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The commitment must go back into the open pool, or the goods are stranded."""
    delivery_id = await _approved_delivery_order(client, db)
    itinerary_id = (
        await client.post('/api/v1/delivery-itineraries', json={'warehouse': 1})
    ).json()['deliveries_itinerary_id']
    stop = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops', json={'delivery_order': delivery_id}
    )
    stop_id = stop.json()['stops'][0]['deliveries_itinerary_stop_id']
    await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/stops/{stop_id}/lines/all',
        json={'delivery_order': delivery_id},
    )

    cancelled = await client.post(
        f'/api/v1/delivery-itineraries/{itinerary_id}/cancel', json={'reason': 'Camión averiado'}
    )

    assert cancelled.status_code == 200, cancelled.text
    line = (
        await db.execute(
            select(DeliveryOrderDetail).where(DeliveryOrderDetail.delivery_order == delivery_id)
        )
    ).scalars().one()
    await db.refresh(line)
    assert line.committed_quantity == Decimal('0')


async def test_the_pending_deliveries_view_lists_what_is_loadable(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The loading queue, which is a query over rows the delivery flow wrote."""
    delivery_id = await _approved_delivery_order(client, db)

    response = await client.get('/api/v1/delivery-itineraries/deliveries')

    assert response.status_code == 200, response.text
    listed = [
        row['delivery_order']
        for bucket in response.json()['buckets']
        for row in bucket['items']
    ]
    assert delivery_id in listed
