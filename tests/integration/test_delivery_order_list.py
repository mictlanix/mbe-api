"""`GET /delivery-orders`: several statuses in one request, and the facility rule (#312, #314)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, DeliveryOrderStatus, SystemObject
from app.main import app as fastapi_app
from app.models.logistics import DeliveryOrder
from app.models.user import AccessPrivilege
from tests.integration.seed import seed_facility, seed_sales_order
from tests.problem import assert_problem

ALL = int(AccessRight.CREATE | AccessRight.READ | AccessRight.UPDATE | AccessRight.DELETE)
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)


@pytest.fixture
def _clerk(client: AsyncClient) -> None:
    # After `client`, which installs the administrator and clears every override on teardown.
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK


async def _grant(db: AsyncSession, *objects: SystemObject) -> None:
    for obj in objects:
        db.add(AccessPrivilege(user_id='tester', system_object=int(obj), privileges=ALL))
    await db.commit()


async def _delivery(
    client: AsyncClient, db: AsyncSession, status: DeliveryOrderStatus, facility: int = 1
) -> int:
    sale = await seed_sales_order(db, completed=True)
    raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sale})
    assert raised.status_code == 201, raised.text
    delivery = raised.json()['delivery_order_id']
    await db.execute(
        update(DeliveryOrder)
        .where(DeliveryOrder.delivery_order_id == delivery)
        .values(status=status, facility=facility)
    )
    await db.commit()
    return delivery


async def test_status_repeats_to_select_several(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    draft = await _delivery(client, db, DeliveryOrderStatus.DRAFT)
    in_transit = await _delivery(client, db, DeliveryOrderStatus.IN_TRANSIT)
    await _delivery(client, db, DeliveryOrderStatus.DELIVERED)

    several = await client.get(
        '/api/v1/delivery-orders',
        params=[
            ('status', int(DeliveryOrderStatus.DRAFT)),
            ('status', int(DeliveryOrderStatus.IN_TRANSIT)),
        ],
    )
    assert several.status_code == 200, several.text
    assert several.json()['total'] == 2
    assert sorted(o['delivery_order_id'] for o in several.json()['items']) == [draft, in_transit]

    # The single-value form still works.
    one = await client.get(
        '/api/v1/delivery-orders', params={'status': int(DeliveryOrderStatus.IN_TRANSIT)}
    )
    assert [o['delivery_order_id'] for o in one.json()['items']] == [in_transit]

    everything = await client.get('/api/v1/delivery-orders')
    assert everything.json()['total'] == 3


async def test_another_facility_without_the_privilege_is_403(
    client: AsyncClient, db: AsyncSession, seeded: None, _clerk: None
) -> None:
    other = await seed_facility(db)
    await _grant(db, SystemObject.DELIVERY_ORDERS)

    response = await client.get('/api/v1/delivery-orders', params={'facility': other})

    body = assert_problem(response, 403, 'facility.other_facility')
    assert body['params'] == {
        'resource': 'delivery_order',
        'required': [
            {
                'system_object': int(SystemObject.SEARCH_CREDITS_FROM_ALL_STORES),
                'right': int(AccessRight.READ),
            },
            {
                'system_object': int(SystemObject.DELIVERY_ITINERARIES),
                'right': int(AccessRight.READ),
            },
        ],
    }

    # Naming one's own facility is not "another".
    own = await client.get('/api/v1/delivery-orders', params={'facility': 1})
    assert own.status_code == 200, own.text


@pytest.mark.parametrize(
    'cross', [SystemObject.SEARCH_CREDITS_FROM_ALL_STORES, SystemObject.DELIVERY_ITINERARIES]
)
async def test_another_facility_with_the_privilege_is_listed(
    client: AsyncClient, db: AsyncSession, seeded: None, cross: SystemObject
) -> None:
    other = await seed_facility(db)
    there = await _delivery(client, db, DeliveryOrderStatus.DRAFT, facility=other)
    await _grant(db, SystemObject.DELIVERY_ORDERS, cross)
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK

    response = await client.get('/api/v1/delivery-orders', params={'facility': other})

    assert response.status_code == 200, response.text
    assert [o['delivery_order_id'] for o in response.json()['items']] == [there]


# ── By id (#314) ──────────────────────────────────────────────────────────────


async def test_another_facilitys_order_by_id_is_404(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    other = await seed_facility(db)
    there = await _delivery(client, db, DeliveryOrderStatus.DRAFT, facility=other)
    here = await _delivery(client, db, DeliveryOrderStatus.DRAFT)
    await _grant(db, SystemObject.DELIVERY_ORDERS)
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK

    # The same 404 as an id that does not exist, so existence is not revealed.
    for path in ('', '/events', '/proof'):
        body = assert_problem(
            await client.get(f'/api/v1/delivery-orders/{there}{path}'), 404, 'resource.not_found'
        )
        assert body['params'] == {'resource': 'delivery_order', 'id': there}
    cancel = await client.post(f'/api/v1/delivery-orders/{there}/cancel', json={'reason': 'x'})
    assert_problem(cancel, 404, 'resource.not_found')

    own = await client.get(f'/api/v1/delivery-orders/{here}')
    assert own.status_code == 200, own.text
    own_events = await client.get(f'/api/v1/delivery-orders/{here}/events')
    assert own_events.status_code == 200, own_events.text


@pytest.mark.parametrize(
    'cross', [SystemObject.SEARCH_CREDITS_FROM_ALL_STORES, SystemObject.DELIVERY_ITINERARIES]
)
async def test_another_facilitys_order_by_id_with_the_privilege(
    client: AsyncClient, db: AsyncSession, seeded: None, cross: SystemObject
) -> None:
    other = await seed_facility(db)
    there = await _delivery(client, db, DeliveryOrderStatus.DRAFT, facility=other)
    await _grant(db, SystemObject.DELIVERY_ORDERS, cross)
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK

    response = await client.get(f'/api/v1/delivery-orders/{there}')
    assert response.status_code == 200, response.text
    assert response.json()['delivery_order_id'] == there
    events = await client.get(f'/api/v1/delivery-orders/{there}/events')
    assert events.status_code == 200, events.text
