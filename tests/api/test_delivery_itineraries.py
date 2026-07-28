"""Tests for the /delivery-itineraries endpoints, including the pending-deliveries view."""

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import ItineraryStatus, StopOutcome
from app.main import app

SERVICE = 'app.services.delivery_itinerary_service'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=7,
        point_sale_id=3,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _itinerary(status_: ItineraryStatus = ItineraryStatus.OPEN) -> SimpleNamespace:
    return SimpleNamespace(
        deliveries_itinerary_id=1,
        date=date(2026, 7, 27),
        vehicle=4,
        vehicle_operator=5,
        warehouse=2,
        status=status_,
        departure_time=None,
        return_time=None,
        comment=None,
    )


def _stop() -> SimpleNamespace:
    return SimpleNamespace(
        deliveries_itinerary_stop_id=10,
        deliveries_itinerary=1,
        sequence=1,
        arrival_time=None,
        outcome=StopOutcome.PENDING,
        proof_of_delivery=None,
        comment=None,
    )


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _no_stops():
    return patch(f'{SERVICE}.stops_of', AsyncMock(return_value=[]))


# ── Authentication ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listing_requires_authentication() -> None:
    async with _client() as client:
        assert (await client.get('/api/v1/delivery-itineraries')).status_code == 401


@pytest.mark.asyncio
async def test_pending_view_requires_authentication() -> None:
    async with _client() as client:
        assert (await client.get('/api/v1/delivery-itineraries/deliveries')).status_code == 401


# ── Pending deliveries ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_view_returns_six_buckets_always() -> None:
    """Empty buckets are still present, so a client can render the tabs unconditionally."""
    _auth()
    empty = {
        'earlier': [],
        'yesterday': [],
        'today': [],
        'tomorrow': [],
        'day_after': [],
        'later': [],
    }
    with patch(f'{SERVICE}.pending_deliveries', AsyncMock(return_value=empty)):
        async with _client() as client:
            response = await client.get('/api/v1/delivery-itineraries/deliveries')

    body = response.json()
    assert [b['key'] for b in body['buckets']] == list(empty)
    assert all(b['total'] == 0 for b in body['buckets'])


@pytest.mark.asyncio
async def test_pending_view_is_not_matched_as_an_itinerary_id() -> None:
    _auth()
    empty = dict.fromkeys(
        ('earlier', 'yesterday', 'today', 'tomorrow', 'day_after', 'later')
    )
    with patch(
        f'{SERVICE}.pending_deliveries',
        AsyncMock(return_value={k: [] for k in empty}),
    ) as called:
        async with _client() as client:
            await client.get('/api/v1/delivery-itineraries/deliveries')

    assert called.await_count == 1


@pytest.mark.asyncio
async def test_pending_line_carries_open_quantity() -> None:
    _auth()
    grouped = {k: [] for k in ('earlier', 'yesterday', 'today', 'tomorrow', 'day_after', 'later')}
    grouped['today'] = [
        {
            'delivery_order': 1,
            'delivery_order_detail': 11,
            'serial': 42,
            'customer': 5,
            'ship_to': 9,
            'date': datetime(2026, 7, 27, 9, 0),
            'priority': 2,
            'product': 3,
            'product_code': 'ABC',
            'product_name': 'Widget',
            'warehouse': 2,
            'open_quantity': Decimal('6'),
        }
    ]
    with patch(f'{SERVICE}.pending_deliveries', AsyncMock(return_value=grouped)):
        async with _client() as client:
            response = await client.get('/api/v1/delivery-itineraries/deliveries')

    today = next(b for b in response.json()['buckets'] if b['key'] == 'today')
    assert today['total'] == 1
    assert today['items'][0]['open_quantity'] == '6'


# ── Itineraries ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_the_open_itinerary() -> None:
    _auth()
    with patch(
        f'{SERVICE}.create_itinerary', AsyncMock(return_value=(_itinerary(), []))
    ), _no_stops():
        async with _client() as client:
            response = await client.post('/api/v1/delivery-itineraries', json={'vehicle': 4})

    assert response.status_code == 201
    assert response.json()['status'] == ItineraryStatus.OPEN


@pytest.mark.asyncio
async def test_expired_operator_licence_warns_but_does_not_refuse() -> None:
    """FR-035 — advisory, never a refusal."""
    _auth()
    warning = ['Operator licence A123 expired on 2020-01-01']
    with patch(
        f'{SERVICE}.create_itinerary', AsyncMock(return_value=(_itinerary(), warning))
    ), _no_stops():
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-itineraries', json={'vehicle': 4, 'vehicle_operator': 5}
            )

    assert response.status_code == 201
    assert response.json()['warnings'] == warning


@pytest.mark.asyncio
async def test_second_open_itinerary_for_a_vehicle_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='already has an open itinerary')
    with patch(f'{SERVICE}.create_itinerary', AsyncMock(side_effect=conflict)):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-itineraries', json={'vehicle': 4})

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_unknown_itinerary_is_404() -> None:
    _auth()
    with patch(f'{SERVICE}.get_itinerary', AsyncMock(return_value=None)):
        async with _client() as client:
            assert (await client.get('/api/v1/delivery-itineraries/999')).status_code == 404


@pytest.mark.asyncio
async def test_all_six_filters_reach_the_service() -> None:
    """FR-068 — every filter the contract advertises."""
    _auth()
    with patch(f'{SERVICE}.list_itineraries', AsyncMock(return_value=([], 0))) as listed:
        async with _client() as client:
            await client.get(
                '/api/v1/delivery-itineraries'
                '?date_from=2026-07-01&date_to=2026-07-31&vehicle=4'
                '&vehicle_operator=5&warehouse=2&status=1'
            )

    kwargs = listed.await_args.kwargs
    assert kwargs['date_from'] == date(2026, 7, 1)
    assert kwargs['date_to'] == date(2026, 7, 31)
    assert kwargs['vehicle'] == 4
    assert kwargs['vehicle_operator'] == 5
    assert kwargs['warehouse'] == 2
    assert kwargs['itinerary_status'] == ItineraryStatus.DEPARTED


# ── Commitments ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_committing_above_open_quantity_states_what_is_available() -> None:
    _auth()
    refusal = HTTPException(status_code=422, detail='Only 6 is available on this line')
    with patch(f'{SERVICE}.get_itinerary', AsyncMock(return_value=_itinerary())), patch(
        f'{SERVICE}.stops_of', AsyncMock(return_value=[_stop()])
    ), patch(f'{SERVICE}.commit_line', AsyncMock(side_effect=refusal)):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-itineraries/1/stops/10/lines',
                json={'delivery_order_detail': 11, 'quantity': 9},
            )

    assert response.status_code == 422
    assert '6 is available' in response.json()['detail']


@pytest.mark.asyncio
async def test_committing_to_an_unknown_stop_is_404() -> None:
    _auth()
    with patch(f'{SERVICE}.get_itinerary', AsyncMock(return_value=_itinerary())), _no_stops():
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-itineraries/1/stops/999/lines',
                json={'delivery_order_detail': 11},
            )

    assert response.status_code == 404


# ── Departure ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_departing_an_empty_itinerary_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='Nothing is committed to this itinerary')
    with patch(f'{SERVICE}.get_itinerary', AsyncMock(return_value=_itinerary())), patch(
        f'{SERVICE}.depart', AsyncMock(side_effect=conflict)
    ):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-itineraries/1/depart')

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_cancelling_after_departure_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='DEPARTED and accepts no further changes')
    with patch(
        f'{SERVICE}.get_itinerary', AsyncMock(return_value=_itinerary(ItineraryStatus.DEPARTED))
    ), patch(f'{SERVICE}.cancel_itinerary', AsyncMock(side_effect=conflict)):
        async with _client() as client:
            response = await client.post('/api/v1/delivery-itineraries/1/cancel')

    assert response.status_code == 409


# ── Closing a stop ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closing_without_an_image_is_422() -> None:
    """The evidence travels with the quantities; neither is optional (FR-043)."""
    _auth()
    with patch(
        f'{SERVICE}.get_itinerary', AsyncMock(return_value=_itinerary(ItineraryStatus.DEPARTED))
    ), patch(f'{SERVICE}.stops_of', AsyncMock(return_value=[_stop()])):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-itineraries/1/stops/10/close',
                data={
                    'receiver_name': 'Juan',
                    'receiver_id_shown': 'INE 1',
                    'lines': '[]',
                },
            )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_closing_with_malformed_lines_is_422() -> None:
    _auth()
    with patch(
        f'{SERVICE}.get_itinerary', AsyncMock(return_value=_itinerary(ItineraryStatus.DEPARTED))
    ), patch(f'{SERVICE}.stops_of', AsyncMock(return_value=[_stop()])):
        async with _client() as client:
            response = await client.post(
                '/api/v1/delivery-itineraries/1/stops/10/close',
                data={
                    'receiver_name': 'Juan',
                    'receiver_id_shown': 'INE 1',
                    'lines': 'not json',
                },
                files={'image': ('sig.png', b'\x89PNG\r\n\x1a\n', 'image/png')},
            )

    assert response.status_code == 422
    assert 'JSON' in response.json()['detail']
