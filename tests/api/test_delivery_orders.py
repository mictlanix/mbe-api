"""Tests for the /delivery-orders endpoints.

Follows the established pattern: `dependency_overrides` swap out auth and the database and the
service is patched, so these assert the HTTP contract rather than re-testing service logic covered
in tests/unit/test_delivery_order_service.py.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import DeliveryOrderStatus, FulfillmentType
from app.main import app

SERVICE = 'app.services.delivery_order_service'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, employee_id: int = 7) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=employee_id,
        point_sale_id=3,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _order(
    *,
    status_: DeliveryOrderStatus = DeliveryOrderStatus.DRAFT,
    fulfillment: FulfillmentType = FulfillmentType.DELIVERY,
    serial: int | None = None,
    proof: int | None = None,
) -> SimpleNamespace:
    now = datetime(2026, 7, 27, 9, 0)
    return SimpleNamespace(
        delivery_order_id=1,
        facility=1,
        serial=serial,
        customer=5,
        ship_to=9,
        contact=None,
        date=now,
        priority=1,
        status=status_,
        fulfillment_type=fulfillment,
        parent_delivery_order=None,
        comment=None,
        rejection_reason=None,
        proof_of_delivery=proof,
        creation_time=now,
        modification_time=now,
    )


def _line() -> SimpleNamespace:
    return SimpleNamespace(
        delivery_order_detail_id=11,
        sales_order_detail=21,
        product=3,
        product_code='ABC',
        product_name='Widget',
        warehouse=2,
        quantity=Decimal('10'),
        committed_quantity=Decimal('4'),
        delivered_quantity=Decimal('3'),
        returned_quantity=Decimal('1'),
    )


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── Authentication and authorisation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_listing_requires_authentication() -> None:
    async with _client() as client:
        assert (await client.get('/api/v1/delivery-orders')).status_code == 401


@pytest.mark.asyncio
async def test_creating_requires_authentication() -> None:
    async with _client() as client:
        response = await client.post('/api/v1/delivery-orders', json={'sales_order': 1})
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_proof_image_requires_authentication() -> None:
    """SC-006a — a signature must never be reachable without a session."""
    async with _client() as client:
        assert (await client.get('/api/v1/delivery-orders/1/proof/image')).status_code == 401


# ── Creation ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_a_draft_with_lines() -> None:
    _auth()
    with patch(
        f'{SERVICE}.create_from_sales_order', AsyncMock(return_value=_order())
    ), patch(f'{SERVICE}.lines_of', AsyncMock(return_value=[_line()])):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders', json={'sales_order': 42})

    assert response.status_code == 201
    body = response.json()
    assert body['status'] == DeliveryOrderStatus.DRAFT
    assert body['serial'] is None
    # 10 ordered - 3 delivered - 1 returned - 4 committed
    assert body['lines'][0]['open_quantity'] == '2'


@pytest.mark.asyncio
async def test_create_from_an_uncompleted_sales_order_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='Only a completed, uncancelled sales order')
    with patch(f'{SERVICE}.create_from_sales_order', AsyncMock(side_effect=conflict)):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders', json={'sales_order': 42})

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_when_already_fully_delivered_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='already fully delivered')
    with patch(f'{SERVICE}.create_from_sales_order', AsyncMock(side_effect=conflict)):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders', json={'sales_order': 42})

    assert response.status_code == 409
    assert 'fully delivered' in response.json()['detail']


@pytest.mark.asyncio
async def test_create_passes_a_requested_line_subset_through() -> None:
    """#138 — splitting a sale across destinations no longer needs create-then-trim."""
    _auth()
    creating = AsyncMock(return_value=_order())
    with patch(f'{SERVICE}.create_from_sales_order', creating), patch(
        f'{SERVICE}.lines_of', AsyncMock(return_value=[_line()])
    ):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-orders',
                json={
                    'sales_order': 42,
                    'lines': [
                        {'sales_order_detail': 7, 'quantity': '4'},
                        {'sales_order_detail': 8, 'quantity': '1'},
                    ],
                },
            )

    assert response.status_code == 201
    requested = creating.await_args.kwargs['lines']
    assert [(item.sales_order_detail, item.quantity) for item in requested] == [
        (7, Decimal('4')),
        (8, Decimal('1')),
    ]


@pytest.mark.asyncio
async def test_create_without_lines_still_claims_everything_uncovered() -> None:
    """The default is unchanged, so every existing caller is unaffected."""
    _auth()
    creating = AsyncMock(return_value=_order())
    with patch(f'{SERVICE}.create_from_sales_order', creating), patch(
        f'{SERVICE}.lines_of', AsyncMock(return_value=[_line()])
    ):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders', json={'sales_order': 42})

    assert response.status_code == 201
    assert creating.await_args.kwargs['lines'] is None


@pytest.mark.asyncio
async def test_create_passes_the_destination_header_through() -> None:
    """#146 — a destination is created complete, with no follow-up `PUT` to correct its address."""
    _auth()
    creating = AsyncMock(return_value=_order())
    with patch(f'{SERVICE}.create_from_sales_order', creating), patch(
        f'{SERVICE}.lines_of', AsyncMock(return_value=[_line()])
    ):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-orders',
                json={
                    'sales_order': 42,
                    'ship_to': 91,
                    'contact': 12,
                    'date': '2026-08-10T15:00:00',
                    'comment': 'Leave with the porter',
                },
            )

    assert response.status_code == 201
    passed = creating.await_args.kwargs
    assert passed['ship_to'] == 91
    assert passed['contact'] == 12
    assert passed['date'] == datetime(2026, 8, 10, 15, 0)
    assert passed['comment'] == 'Leave with the porter'


@pytest.mark.asyncio
async def test_create_without_a_destination_header_falls_back_to_the_sale() -> None:
    """Every existing caller is unaffected: nothing supplied means nothing overridden."""
    _auth()
    creating = AsyncMock(return_value=_order())
    with patch(f'{SERVICE}.create_from_sales_order', creating), patch(
        f'{SERVICE}.lines_of', AsyncMock(return_value=[_line()])
    ):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders', json={'sales_order': 42})

    assert response.status_code == 201
    passed = creating.await_args.kwargs
    assert (passed['ship_to'], passed['contact'], passed['date'], passed['comment']) == (
        None,
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_an_empty_line_list_is_rejected_by_the_schema() -> None:
    """"Deliver nothing" is not a request; omitting `lines` is how you ask for everything."""
    _auth()
    async with _client() as client:
        response = await client.post(
            '/api/v1/delivery-orders', json={'sales_order': 42, 'lines': []}
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_zero_line_quantity_is_rejected_by_the_schema() -> None:
    _auth()
    async with _client() as client:
        response = await client.post(
            '/api/v1/delivery-orders',
            json={'sales_order': 42, 'lines': [{'sales_order_detail': 7, 'quantity': '0'}]},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_overclaiming_a_line_surfaces_the_services_422() -> None:
    _auth()
    refusal = HTTPException(status_code=422, detail='Line 7 has 4 undelivered, 5 requested')
    with patch(f'{SERVICE}.create_from_sales_order', AsyncMock(side_effect=refusal)):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-orders',
                json={'sales_order': 42, 'lines': [{'sales_order_detail': 7, 'quantity': '5'}]},
            )

    assert response.status_code == 422
    assert '4 undelivered' in response.json()['detail']


@pytest.mark.asyncio
async def test_unknown_order_is_404() -> None:
    _auth()
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=None)):
        async with _client() as client:
            assert (await client.get('/api/v1/delivery-orders/999')).status_code == 404


# ── Confirmation ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_returns_the_numbered_order() -> None:
    _auth()
    confirmed = _order(status_=DeliveryOrderStatus.IN_PREPARATION, serial=42)
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())), patch(
        f'{SERVICE}.confirm', AsyncMock(return_value=confirmed)
    ), patch(f'{SERVICE}.lines_of', AsyncMock(return_value=[_line()])):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders/1/confirm')

    body = response.json()
    assert response.status_code == 200
    assert body['serial'] == 42
    assert body['status'] == DeliveryOrderStatus.IN_PREPARATION


@pytest.mark.asyncio
async def test_editing_outside_draft_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='can no longer be edited')
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())), patch(
        f'{SERVICE}.update_order', AsyncMock(side_effect=conflict)
    ):
        async with _client() as client:
            response = await client.put('/api/v1/delivery-orders/1', json={'priority': 3})

    assert response.status_code == 409


# ── Approval queue ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approval_queue_is_not_matched_as_an_id() -> None:
    """Route ordering: `/approval` must not resolve to `/{delivery_order_id}`."""
    _auth()
    with patch(f'{SERVICE}.list_orders', AsyncMock(return_value=([], 0))) as listed:
        async with _client() as client:
            response = await client.get('/api/v1/delivery-orders/approval')

    assert response.status_code == 200
    assert listed.await_args.kwargs['order_status'] == DeliveryOrderStatus.PENDING_APPROVAL


@pytest.mark.asyncio
async def test_reject_requires_a_reason() -> None:
    _auth()
    async with _client() as client:
        response = await client.post(
            '/api/v1/delivery-orders/approval/1/reject', json={'reason': ''}
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reject_returns_the_order_to_draft() -> None:
    _auth()
    rejected = _order(status_=DeliveryOrderStatus.DRAFT)
    rejected.rejection_reason = 'Wrong address'
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())), patch(
        f'{SERVICE}.reject', AsyncMock(return_value=rejected)
    ), patch(f'{SERVICE}.lines_of', AsyncMock(return_value=[])):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-orders/approval/1/reject', json={'reason': 'Wrong address'}
            )

    body = response.json()
    assert body['status'] == DeliveryOrderStatus.DRAFT
    assert body['rejection_reason'] == 'Wrong address'


@pytest.mark.asyncio
async def test_mine_filter_is_the_rejection_discovery_path() -> None:
    """No notification is sent; this listing is how an author finds a rejected draft (FR-067)."""
    _auth()
    with patch(f'{SERVICE}.list_orders', AsyncMock(return_value=([], 0))) as listed:
        async with _client() as client:
            await client.get('/api/v1/delivery-orders?mine=true&status=0')

    assert listed.await_args.kwargs['mine'] is True
    assert listed.await_args.kwargs['order_status'] == DeliveryOrderStatus.DRAFT


# ── Cancellation and retry ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_requires_a_reason() -> None:
    _auth()
    async with _client() as client:
        response = await client.post('/api/v1/delivery-orders/1/cancel', json={'reason': ''})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_in_transit_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='IN_TRANSIT')
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())), patch(
        f'{SERVICE}.cancel', AsyncMock(side_effect=conflict)
    ):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-orders/1/cancel', json={'reason': 'no longer needed'}
            )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_requeue_returns_the_order_to_preparation() -> None:
    _auth()
    requeued = _order(status_=DeliveryOrderStatus.IN_PREPARATION)
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())), patch(
        f'{SERVICE}.requeue', AsyncMock(return_value=requeued)
    ), patch(f'{SERVICE}.lines_of', AsyncMock(return_value=[])):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders/1/requeue')

    assert response.json()['status'] == DeliveryOrderStatus.IN_PREPARATION


# ── Counter pickup ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ready_for_pickup_on_a_delivery_order_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='only valid for a COUNTER_PICKUP order')
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())), patch(
        f'{SERVICE}.mark_ready_for_pickup', AsyncMock(side_effect=conflict)
    ):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-orders/1/ready-for-pickup')

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_pickup_without_an_image_is_422() -> None:
    """Proof is not optional at a terminal handover (FR-043)."""
    _auth()
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-orders/1/pickup',
                data={'receiver_name': 'Ana', 'receiver_id_shown': 'INE 1'},
            )

    assert response.status_code == 422


# ── Events ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_events_return_the_ordered_history() -> None:
    _auth()
    events = [
        SimpleNamespace(
            delivery_order_event_id=1,
            from_status=None,
            to_status=DeliveryOrderStatus.DRAFT,
            employee=7,
            event_time=datetime(2026, 7, 27, 9, 0),
            reason=None,
        ),
        SimpleNamespace(
            delivery_order_event_id=2,
            from_status=DeliveryOrderStatus.DRAFT,
            to_status=DeliveryOrderStatus.CANCELLED,
            employee=7,
            event_time=datetime(2026, 7, 27, 10, 0),
            reason='customer changed mind',
        ),
    ]
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order())), patch(
        f'{SERVICE}.events_of', AsyncMock(return_value=events)
    ):
        async with _client() as client:
            response = await client.get('/api/v1/delivery-orders/1/events')

    body = response.json()
    assert body[0]['from_status'] is None
    assert body[1]['reason'] == 'customer changed mind'


# ── Proof of delivery ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proof_before_settlement_is_404() -> None:
    _auth()
    with patch(f'{SERVICE}.get_order', AsyncMock(return_value=_order(proof=None))):
        async with _client() as client:
            assert (await client.get('/api/v1/delivery-orders/1/proof')).status_code == 404


@pytest.mark.asyncio
async def test_proof_images_are_not_served_from_the_public_mount() -> None:
    """The clarification that made FR-044a exist: `/images` is unauthenticated."""
    from app.core.config import settings

    assert settings.pod_dir != settings.images_dir
    mounts = [r.path for r in app.routes if getattr(r, 'name', None) == 'images']
    assert mounts == ['/images']
    assert settings.pod_dir not in mounts


@pytest.mark.asyncio
async def test_creation_passes_an_explicit_fulfillment_type_through() -> None:
    """Splitting a sale across both kinds is a per-delivery-order choice (FR-005a)."""
    _auth()
    pickup = _order(fulfillment=FulfillmentType.COUNTER_PICKUP)
    with patch(
        f'{SERVICE}.create_from_sales_order', AsyncMock(return_value=pickup)
    ) as created, patch(f'{SERVICE}.lines_of', AsyncMock(return_value=[])):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-orders', json={'sales_order': 42, 'fulfillment_type': 1}
            )

    assert response.status_code == 201
    assert created.await_args.kwargs['fulfillment_type'] == FulfillmentType.COUNTER_PICKUP
    assert response.json()['fulfillment_type'] == FulfillmentType.COUNTER_PICKUP


@pytest.mark.asyncio
async def test_omitting_the_type_leaves_detection_in_charge() -> None:
    _auth()
    with patch(
        f'{SERVICE}.create_from_sales_order', AsyncMock(return_value=_order())
    ) as created, patch(f'{SERVICE}.lines_of', AsyncMock(return_value=[])):
        async with _client() as client:
            await client.post('/api/v1/delivery-orders', json={'sales_order': 42})

    assert created.await_args.kwargs['fulfillment_type'] is None
