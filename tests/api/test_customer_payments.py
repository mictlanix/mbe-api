"""Tests for the /customer-payments endpoints, including verification and the payments editor.

The behaviours worth guarding here are the ones the clarifications pinned down: a payment only
attaches to a completed, uncancelled order; a reversal needs a stated reason; and a cancelled
application stays visible rather than disappearing.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app
from tests.problem import assert_problem


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
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _payment(**overrides) -> SimpleNamespace:
    base = dict(
        customer_payment_id=1,
        customer=2,
        amount=Decimal('290.00'),
        currency=0,
        method=1,
        payment_charge=None,
        reference=None,
        date='2026-07-25T00:00:00',
        facility=1,
        serial=18714,
        cash_session=None,
        payment_type=1,
        verifier=None,
        unapplied=Decimal('290.00'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _application(**overrides) -> SimpleNamespace:
    base = dict(
        sales_order_payment_id=9,
        sales_order=1,
        customer_payment=1,
        amount=Decimal('290.00'),
        amount_change=Decimal('0'),
        applier=7,
        date='2026-07-25T00:00:00',
        cancelled=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Authentication ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/customer-payments')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_apply_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.post(
            '/api/v1/customer-payments/1/applications',
            json={'sales_order': 1, 'amount': '10'},
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── Recording ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_a_payment() -> None:
    _auth()
    with (
        patch(
            'app.services.customer_payment_service.create_payment',
            AsyncMock(return_value=_payment()),
        ),
        patch('app.services.customer_payment_service.attach_customer_names', AsyncMock()),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments',
                json={'customer': 2, 'amount': '290.00', 'method': 1},
            )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()['unapplied'] == '290.00'


@pytest.mark.asyncio
async def test_zero_amount_payment_is_rejected() -> None:
    _auth()
    async with await _client() as client:
        response = await client.post(
            '/api/v1/customer-payments',
            json={'customer': 2, 'amount': '0', 'method': 1},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_list_does_not_implicitly_scope_to_a_cash_session() -> None:
    """FR-009a — session scoping is an explicit filter, never a hidden default."""
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.customer_payment_service.list_payments', listing):
        async with await _client() as client:
            await client.get('/api/v1/customer-payments')

    assert listing.await_args.kwargs['cash_session'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('search', ['18714', 'Acme'])
async def test_the_search_reaches_the_service_unchanged(search: str) -> None:
    """Spec 035, T029 — an all-digits search is the id or the serial, otherwise the customer's name.

    The service decides which; the endpoint must hand it the text as typed. The real matching,
    inside the facility scope, is in `tests/integration/test_numbering_flow.py`.
    """
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.customer_payment_service.list_payments', listing):
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments', params={'search': search})

    assert response.status_code == status.HTTP_200_OK
    assert listing.await_args.kwargs['search'] == search


# ── Applying ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_a_payment_to_an_order() -> None:
    _auth()
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch(
            'app.services.customer_payment_service.apply_payment',
            AsyncMock(return_value=_application()),
        ),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications',
                json={'sales_order': 1, 'amount': '290.00'},
            )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()['cancelled'] is False


@pytest.mark.asyncio
async def test_applying_to_a_draft_order_is_409() -> None:
    """FR-042 — an order must be confirmed before it can take money."""
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='Only a completed order can be paid; confirm it first',
    )
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch(
            'app.services.customer_payment_service.apply_payment', AsyncMock(side_effect=conflict)
        ),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications',
                json={'sales_order': 1, 'amount': '10'},
            )

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
@pytest.mark.parametrize('detail', ['Sales order is already settled', 'The order is busy, retry'])
async def test_a_settled_or_busy_order_is_409(detail: str) -> None:
    """Spec 033 FR-021, FR-022: the service's refusal reaches the caller as it is."""
    _auth()
    conflict = HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch(
            'app.services.customer_payment_service.apply_payment', AsyncMock(side_effect=conflict)
        ),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications',
                json={'sales_order': 1, 'amount': '10'},
            )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == detail


@pytest.mark.asyncio
async def test_over_application_is_422() -> None:
    _auth()
    error = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='Amount 500 exceeds the unapplied balance of 290.00',
    )
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch('app.services.customer_payment_service.apply_payment', AsyncMock(side_effect=error)),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications',
                json={'sales_order': 1, 'amount': '500'},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_cross_currency_application_is_422() -> None:
    _auth()
    error = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='Payment currency does not match the order currency',
    )
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch('app.services.customer_payment_service.apply_payment', AsyncMock(side_effect=error)),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications',
                json={'sales_order': 1, 'amount': '10'},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert 'currency' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_unknown_payment_is_404() -> None:
    _auth()
    with patch('app.services.customer_payment_service.get_payment', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Reversing ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reversal_without_a_reason_is_422() -> None:
    """SC-009 — no reversal is anonymous or unexplained."""
    _auth()
    with patch(
        'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications/9/reverse', json={}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_reversal_with_a_blank_reason_is_422() -> None:
    _auth()
    with patch(
        'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications/9/reverse', json={'reason': ''}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_reversal_marks_cancelled_without_deleting() -> None:
    """FR-045 — the application survives as evidence, flagged rather than removed."""
    _auth()
    reversed_app = _application(cancelled=True)
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch(
            'app.services.customer_payment_service.get_application',
            AsyncMock(return_value=_application()),
        ),
        patch(
            'app.services.customer_payment_service.reverse_application',
            AsyncMock(return_value=reversed_app),
        ),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications/9/reverse',
                json={'reason': 'Applied to the wrong order'},
            )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['cancelled'] is True
    assert response.json()['sales_order_payment_id'] == 9


@pytest.mark.asyncio
async def test_reversing_an_unknown_application_is_404() -> None:
    _auth()
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch(
            'app.services.customer_payment_service.get_application', AsyncMock(return_value=None)
        ),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-payments/1/applications/999/reverse',
                json={'reason': 'nope'},
            )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_applications_listing_includes_cancelled_ones() -> None:
    """FR-073 — the payments editor needs the whole history, not the live subset."""
    _auth()
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch(
            'app.services.customer_payment_service.list_applications',
            AsyncMock(
                return_value=[
                    _application(),
                    _application(sales_order_payment_id=10, cancelled=True),
                ]
            ),
        ),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments/1/applications')

    assert response.status_code == status.HTTP_200_OK
    assert [a['cancelled'] for a in response.json()] == [False, True]


# ── Verification ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unverified_queue_lists_only_unverified() -> None:
    _auth()
    listing = AsyncMock(return_value=([_payment()], 1))
    with patch('app.services.customer_payment_service.list_payments', listing):
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments/unverified')

    assert response.status_code == status.HTTP_200_OK
    assert listing.await_args.kwargs['unverified_only'] is True


@pytest.mark.asyncio
async def test_verify_records_the_supervisor() -> None:
    _auth()
    with (
        patch(
            'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
        ),
        patch(
            'app.services.customer_payment_service.verify_payment',
            AsyncMock(return_value=_payment(verifier=7)),
        ),
        patch('app.services.customer_payment_service.attach_customer_names', AsyncMock()),
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-payments/1/verify')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['verifier'] == 7


@pytest.mark.asyncio
async def test_reject_requires_a_reason() -> None:
    _auth()
    with patch(
        'app.services.customer_payment_service.get_payment', AsyncMock(return_value=_payment())
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-payments/1/reject', json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── Outstanding orders ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outstanding_orders_carry_their_balances() -> None:
    _auth()
    row = {
        'sales_order_id': 1,
        'serial': 42,
        'customer': 2,
        'customer_name': None,
        'date': '2026-07-25T00:00:00',
        'due_date': '2026-08-24T00:00:00',
        'currency': 0,
        'total': Decimal('290.00'),
        'balance': Decimal('190.00'),
    }
    with patch(
        'app.services.customer_payment_service.search_outstanding',
        AsyncMock(return_value=([row], 1)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments/outstanding-orders')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['items'][0]['balance'] == '190.00'


@pytest.mark.asyncio
async def test_a_utc_date_filter_is_converted_to_business_wall_clock_time() -> None:
    """#228 — the driver would drop the offset unconverted and filter six hours off."""
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.customer_payment_service.list_payments', listing):
        async with await _client() as client:
            await client.get(
                '/api/v1/customer-payments', params={'date_to': '2026-09-27T06:00:00Z'}
            )

    date_to = listing.await_args.kwargs['date_to']
    assert date_to == datetime(2026, 9, 27, 0, 0)
    assert date_to.tzinfo is None


# ── Facility scope (#271) ─────────────────────────────────────────────────────


def _clerk(*, editor: bool):  # noqa: ANN202
    """A non-administrator at facility 1 who holds CustomerPayments read, and PaymentsEditor read
    only when `editor`."""
    from app.api.v1.endpoints import customer_payments

    clerk = CurrentUser(
        user_id='clerk', session_version=1, administrator=False, facility_id=1, employee_id=7
    )
    app.dependency_overrides[customer_payments._READ] = lambda: clerk

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db
    return patch('app.core.deps.has_any_privilege', AsyncMock(return_value=editor))


_BY_ID = ['/api/v1/customer-payments/1', '/api/v1/customer-payments/1/applications']


def _by_id_patches(payment: SimpleNamespace):  # noqa: ANN202
    return (
        patch('app.services.customer_payment_service.get_payment', AsyncMock(return_value=payment)),
        patch('app.services.customer_payment_service.attach_unapplied', AsyncMock()),
        patch(
            'app.services.customer_payment_service.list_applications', AsyncMock(return_value=[])
        ),
        patch('app.services.customer_payment_service.attach_customer_names', AsyncMock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('path', _BY_ID)
async def test_another_facilitys_payment_is_not_found_without_the_editor_right(path) -> None:
    get, attach, apps, names = _by_id_patches(_payment(facility=51))
    with _clerk(editor=False), get, attach, apps, names:
        async with await _client() as client:
            response = await client.get(path)

    # 404, not 403: a refusal would confirm the id exists at another facility.
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize('path', _BY_ID)
async def test_the_editor_right_reads_another_facilitys_payment(path) -> None:
    get, attach, apps, names = _by_id_patches(_payment(facility=51))
    with _clerk(editor=True), get, attach, apps, names:
        async with await _client() as client:
            response = await client.get(path)

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
@pytest.mark.parametrize('path', _BY_ID)
async def test_an_own_facility_payment_needs_no_editor_right(path) -> None:
    get, attach, apps, names = _by_id_patches(_payment(facility=1))
    with _clerk(editor=False) as editor_check, get, attach, apps, names:
        async with await _client() as client:
            response = await client.get(path)

    assert response.status_code == status.HTTP_200_OK
    editor_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_listing_another_facility_is_forbidden_without_the_editor_right() -> None:
    listing = AsyncMock(return_value=([], 0))
    with (
        _clerk(editor=False),
        patch('app.services.customer_payment_service.list_payments', listing),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments?facility=51')

    body = assert_problem(response, 403, 'facility.other_facility')
    assert body['params'] == {
        'resource': 'customer_payment',
        'required': [{'system_object': 100, 'right': 2}],
    }
    listing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('query', 'editor'), [('?facility=51', True), ('?facility=1', False), ('', False)]
)
async def test_listing_is_allowed_for_own_facility_or_with_the_editor_right(query, editor) -> None:
    listing = AsyncMock(return_value=([], 0))
    with (
        _clerk(editor=editor),
        patch('app.services.customer_payment_service.list_payments', listing),
    ):
        async with await _client() as client:
            response = await client.get(f'/api/v1/customer-payments{query}')

    assert response.status_code == status.HTTP_200_OK
    listing.assert_awaited_once()


@pytest.mark.asyncio
async def test_another_facilitys_payment_ticket_is_not_found_without_the_editor_right() -> None:
    """#272 — printing follows the same facility rule as reading (#271)."""
    get, attach, apps, names = _by_id_patches(_payment(facility=51, payment_type=2))
    with _clerk(editor=False), get, attach, apps, names:
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments/1/ticket')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_a_payment_without_a_ticket_is_refused_naming_its_type() -> None:
    get, attach, apps, names = _by_id_patches(_payment(facility=1, payment_type=1))
    with _clerk(editor=False), get, attach, apps, names:
        async with await _client() as client:
            response = await client.get('/api/v1/customer-payments/1/ticket')

    body = assert_problem(response, 409, 'payment.no_ticket')
    assert body['params'] == {'payment_type': 1}


# ── Facility scope on writes (spec 034, FR-010) ───────────────────────────────


def _writing_clerk(*, editor: bool):  # noqa: ANN202
    """`_clerk`, holding every payment write and verification right as well."""
    from app.api.v1.endpoints import customer_payments

    clerk = CurrentUser(
        user_id='clerk', session_version=1, administrator=False, facility_id=1, employee_id=7
    )
    for dependency in (
        customer_payments._CREATE,
        customer_payments._UPDATE,
        customer_payments._VERIFY_UPDATE,
    ):
        app.dependency_overrides[dependency] = lambda: clerk

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db
    return patch('app.core.deps.has_any_privilege', AsyncMock(return_value=editor))


_REACHED = HTTPException(status_code=status.HTTP_418_IM_A_TEAPOT, detail='reached the service')


async def _write(path: str, body: dict | None) -> int:
    async with await _client() as client:
        return (await client.post(path, json=body)).status_code


_WRITES = [
    (
        '/api/v1/customer-payments/1/applications',
        {'sales_order': 1, 'amount': '5'},
        'apply_payment',
    ),
    (
        '/api/v1/customer-payments/1/applications/4/reverse',
        {'reason': 'Duplicado'},
        'reverse_application',
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(('path', 'body', 'service'), _WRITES)
@pytest.mark.parametrize('editor', [False, True])
async def test_another_facilitys_payment_is_not_written_without_the_editor_right(
    path: str, body: dict, service: str, editor: bool
) -> None:
    with (
        _writing_clerk(editor=editor),
        patch(
            'app.services.customer_payment_service.get_payment',
            AsyncMock(return_value=_payment(facility=51)),
        ),
        patch(
            'app.services.customer_payment_service.get_application',
            AsyncMock(return_value=SimpleNamespace(sales_order_payment_id=4)),
        ),
        patch(f'app.services.customer_payment_service.{service}', AsyncMock(side_effect=_REACHED)),
    ):
        code = await _write(path, body)

    assert code == (status.HTTP_418_IM_A_TEAPOT if editor else status.HTTP_404_NOT_FOUND)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('path', 'body', 'service'),
    [
        ('/api/v1/customer-payments/1/verify', None, 'verify_payment'),
        ('/api/v1/customer-payments/1/reject', {'reason': 'Revisar'}, 'reject_payment'),
    ],
)
async def test_verification_reaches_any_facilitys_payment(path: str, body, service: str) -> None:  # noqa: ANN001
    """Company-wide for PaymentsVerification, as the current system's screen (clarified)."""
    with (
        _writing_clerk(editor=False),
        patch(
            'app.services.customer_payment_service.get_payment',
            AsyncMock(return_value=_payment(facility=51)),
        ),
        patch(f'app.services.customer_payment_service.{service}', AsyncMock(side_effect=_REACHED)),
    ):
        code = await _write(path, body)

    assert code == status.HTTP_418_IM_A_TEAPOT


@pytest.mark.asyncio
@pytest.mark.parametrize('facility', [None, 51])
async def test_the_unverified_list_covers_every_facility(facility: int | None) -> None:
    """Spec 034, FR-012: as the current system's verification screen; a named facility narrows."""
    from app.api.v1.endpoints import customer_payments

    clerk = CurrentUser(
        user_id='clerk', session_version=1, administrator=False, facility_id=1, employee_id=7
    )
    app.dependency_overrides[customer_payments._VERIFY_READ] = lambda: clerk

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.customer_payment_service.list_payments', listing):
        async with await _client() as client:
            params = {} if facility is None else {'facility': facility}
            response = await client.get('/api/v1/customer-payments/unverified', params=params)

    assert response.status_code == status.HTTP_200_OK
    assert listing.await_args.kwargs['cross_facility'] is True
    assert listing.await_args.kwargs['facility'] == facility
