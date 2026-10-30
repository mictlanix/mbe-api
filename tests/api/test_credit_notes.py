"""Tests for the /credit-notes endpoints.

Note what is absent: there is no redemption route. Redeeming a credit note is an ordinary payment
application against its backing payment (FR-070a), and `test_no_redemption_route_exists` guards
that decision so nobody adds a parallel path later.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app
from tests.problem import assert_problem


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=7
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _note(**overrides) -> SimpleNamespace:
    base = dict(
        credit_note_id=1,
        customer=2,
        sales_order=5,
        customer_refund=3,
        customer_payment=9,
        facility=1,
        amount=Decimal('116.00'),
        remaining=Decimal('116.00'),
        refunded=Decimal('0.00'),
        refunded_to_customer=False,
        cash_session=None,
        date=datetime(2026, 7, 25),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_list_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/credit-notes')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_list_reports_issued_and_remaining_and_its_origin() -> None:
    """FR-070 — each note names the refund and order that produced it."""
    _auth()
    with patch(
        'app.services.credit_note_service.list_credit_notes',
        AsyncMock(return_value=([_note()], 1)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes?customer=2')

    assert response.status_code == status.HTTP_200_OK
    row = response.json()['items'][0]
    assert row['amount'] == '116.00'
    assert row['remaining'] == '116.00'
    # Spec 034: `refunded` is what was handed back in cash, the column's meaning; none here.
    assert row['refunded'] == '0.00'
    assert row['refunded_to_customer'] is False
    assert row['facility'] == 1
    assert row['customer_refund'] == 3
    assert row['sales_order'] == 5


@pytest.mark.asyncio
async def test_partially_redeemed_note_shows_the_reduced_remainder() -> None:
    _auth()
    partly = _note(remaining=Decimal('16.00'))
    with patch(
        'app.services.credit_note_service.list_credit_notes',
        AsyncMock(return_value=([partly], 1)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes')

    row = response.json()['items'][0]
    assert row['amount'] == '116.00'
    assert row['remaining'] == '16.00'


@pytest.mark.asyncio
async def test_open_only_filter_is_passed_through() -> None:
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.credit_note_service.list_credit_notes', listing):
        async with await _client() as client:
            await client.get('/api/v1/credit-notes?open_only=true')

    assert listing.await_args.kwargs['open_only'] is True


@pytest.mark.asyncio
async def test_get_a_single_note() -> None:
    _auth()
    with patch('app.services.credit_note_service.get_credit_note', AsyncMock(return_value=_note())):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes/1')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['credit_note_id'] == 1


@pytest.mark.asyncio
async def test_unknown_note_is_404() -> None:
    _auth()
    with patch(
        'app.services.credit_note_service.get_credit_note', AsyncMock(return_value=None)
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_no_redemption_route_exists() -> None:
    """FR-070a — redemption is a payment application, not a credit-note endpoint.

    Guards against a well-meaning addition that would bypass the unapplied-amount bound and the
    reversal path the payment application already provides.
    """
    # Store credit only; `/fiscal-documents/credit-notes` is the CFDI credit note (spec 028).
    credit_note_paths = {
        route.path
        for route in app.routes
        if str(getattr(route, 'path', '')).startswith('/api/v1/credit-notes')
    }

    assert credit_note_paths == {'/api/v1/credit-notes', '/api/v1/credit-notes/{credit_note_id}'}


@pytest.mark.asyncio
async def test_a_note_paid_back_in_cash_says_so() -> None:
    """Spec 034 FR-003: note 158's shape."""
    _auth()
    paid_back = _note(
        amount=Decimal('1025.00'),
        remaining=Decimal('0'),
        refunded=Decimal('1025.00'),
        refunded_to_customer=True,
        cash_session=7916,
    )
    with patch(
        'app.services.credit_note_service.get_credit_note', AsyncMock(return_value=paid_back)
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes/158')

    body = response.json()
    assert (body['refunded_to_customer'], body['refunded'], body['remaining']) == (
        True,
        '1025.00',
        '0',
    )


# ── Facility scope (spec 034, FR-013) ─────────────────────────────────────────


def _clerk(*, editor: bool):  # noqa: ANN202
    """A non-administrator at facility 1 with CreditPayments read, and PaymentsEditor read only
    when `editor`."""
    from app.api.v1.endpoints import credit_notes

    clerk = CurrentUser(
        user_id='clerk', session_version=1, administrator=False, facility_id=1, employee_id=7
    )
    app.dependency_overrides[credit_notes._READ] = lambda: clerk

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db
    return patch('app.core.deps.has_any_privilege', AsyncMock(return_value=editor))


@pytest.mark.asyncio
async def test_the_list_defaults_to_the_callers_facility() -> None:
    listing = AsyncMock(return_value=([], 0))
    with _clerk(editor=False), patch('app.services.credit_note_service.list_credit_notes', listing):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes')

    assert response.status_code == status.HTTP_200_OK
    assert listing.await_args.kwargs['facility'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('editor', [False, True])
async def test_another_facilitys_notes_need_the_editor_right(editor: bool) -> None:
    listing = AsyncMock(return_value=([], 0))
    with (
        _clerk(editor=editor),
        patch('app.services.credit_note_service.list_credit_notes', listing),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes', params={'facility': 51})

    assert response.status_code == (status.HTTP_200_OK if editor else status.HTTP_403_FORBIDDEN)
    if editor:
        assert listing.await_args.kwargs['facility'] == 51
    else:
        body = assert_problem(response, 403, 'facility.other_facility')
        assert body['params'] == {
            'resource': 'credit_note',
            'required': [{'system_object': 100, 'right': 2}],
        }


@pytest.mark.asyncio
@pytest.mark.parametrize('editor', [False, True])
async def test_another_facilitys_note_is_not_found_without_the_editor_right(editor: bool) -> None:
    with (
        _clerk(editor=editor),
        patch(
            'app.services.credit_note_service.get_credit_note',
            AsyncMock(return_value=_note(facility=51)),
        ),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes/1')

    # 404, not 403: a refusal would confirm the note exists at another facility.
    assert response.status_code == (status.HTTP_200_OK if editor else status.HTTP_404_NOT_FOUND)
