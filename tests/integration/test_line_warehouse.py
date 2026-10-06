"""#234 — a document line's warehouse must exist and must not be an in-transit location.

Sales order and refund lines stored whatever warehouse they were given, unknown ids included.

Another facility's warehouse is deliberately **allowed**. #234 proposed refusing it, but legacy
sells across facilities as a matter of course on the shared database: 14% of 2026's sales order
lines on mbe_dev name another facility's warehouse (Casa Maestra stores selling from Zumpango,
Zitlaltepec and Block 1), and five live points of sale pair facility 52 with warehouse 12.

The seed has one facility (1) with a stock warehouse (1) and an in-transit location (2).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus, FacilityType
from app.models.core import Facility, Warehouse
from tests.integration.seed import ISSUER_RFC, POSTAL_CODE, seed_sales_order


@pytest.fixture
async def other_facility(db: AsyncSession, seeded: None) -> None:
    """Facility 2, with warehouse 3."""
    db.add(
        Facility(
            facility_id=2,
            code='F2',
            name='Sucursal',
            type=FacilityType.STORE,
            location=POSTAL_CODE,
            address=1,
            taxpayer=ISSUER_RFC,
            status=EntityStatus.ACTIVE,
        )
    )
    await db.flush()
    db.add(
        Warehouse(
            warehouse_id=3,
            facility=2,
            code='W3',
            name='Almacén sucursal',
            status=EntityStatus.ACTIVE,
            in_transit=False,
        )
    )
    await db.commit()


async def _draft(client: AsyncClient) -> int:
    created = await client.post('/api/v1/sales-orders', json={'customer': 1})
    assert created.status_code == 201, created.text
    return created.json()['sales_order_id']


async def test_a_line_from_an_in_transit_location_is_refused(
    client: AsyncClient, seeded: None
) -> None:
    order_id = await _draft(client)

    response = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'warehouse': 2}
    )

    assert response.status_code == 422, response.text
    assert response.json()['detail'] == 'Warehouse 2 is an in-transit location'


async def test_a_line_from_an_unknown_warehouse_is_404(client: AsyncClient, seeded: None) -> None:
    """404, as an unknown warehouse is for a point of sale (#102)."""
    order_id = await _draft(client)

    response = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'warehouse': 999}
    )

    assert response.status_code == 404, response.text
    assert response.json()['detail'] == 'Warehouse not found'


async def test_a_line_from_another_facilitys_warehouse_is_accepted(
    client: AsyncClient, other_facility: None
) -> None:
    order_id = await _draft(client)

    response = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'warehouse': 3}
    )

    assert response.status_code == 200, response.text
    assert response.json()['lines'][0]['warehouse'] == 3


async def test_moving_a_line_to_an_in_transit_location_is_refused(
    client: AsyncClient, seeded: None
) -> None:
    order_id = await _draft(client)
    lined = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'warehouse': 1}
    )
    line_id = lined.json()['lines'][0]['sales_order_detail_id']

    response = await client.put(
        f'/api/v1/sales-orders/{order_id}/lines/{line_id}', json={'warehouse': 2}
    )

    assert response.status_code == 422, response.text
    reread = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert reread.json()['lines'][0]['warehouse'] == 1


async def test_a_refund_line_into_an_in_transit_location_is_refused(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The stock comes back into the line's warehouse, so the same rule holds for refunds."""
    order_id = await seed_sales_order(db, completed=True, paid=True)
    refund = await client.post('/api/v1/customer-refunds', json={'sales_order': order_id})
    assert refund.status_code == 201, refund.text
    refund_id = refund.json()['customer_refund_id']
    line_id = refund.json()['lines'][0]['customer_refund_detail_id']

    unknown = await client.put(
        f'/api/v1/customer-refunds/{refund_id}/lines/{line_id}', json={'warehouse': 999}
    )
    in_transit = await client.put(
        f'/api/v1/customer-refunds/{refund_id}/lines/{line_id}', json={'warehouse': 2}
    )

    assert unknown.status_code == 404, unknown.text
    assert in_transit.status_code == 422, in_transit.text
    assert in_transit.json()['detail'] == 'Warehouse 2 is an in-transit location'
