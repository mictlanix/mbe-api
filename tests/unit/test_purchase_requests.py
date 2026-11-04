"""Pure rules of purchase requests (spec 031, US1): serial, submit, warnings, defaults, refusals."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.error_codes import ErrorCode
from app.schemas.purchases import RequestStatus
from app.services.purchase_request_service import (
    approve_refusal,
    below_minimum_warning,
    cancel_refusal,
    default_quantity,
    facility_move_refusal,
    mark_refusal,
    next_request_serial,
    reject_refusal,
    submit_refusal,
)

DRAFT, SUBMITTED, APPROVED, CANCELLED = (
    RequestStatus.DRAFT,
    RequestStatus.SUBMITTED,
    RequestStatus.APPROVED,
    RequestStatus.CANCELLED,
)


def _line(quantity: str, name: str = 'Tornillo') -> SimpleNamespace:
    return SimpleNamespace(
        quantity=Decimal(quantity), product_name=name, purchase_request_detail_id=9
    )


@pytest.mark.parametrize('highest, expected', [(None, 1), (0, 1), (41, 42)])
def test_next_request_serial(highest, expected):  # noqa: ANN001
    assert next_request_serial(highest) == expected


# ── Submit (FR-004) ───────────────────────────────────────────────────────────


def test_a_draft_with_lines_can_be_submitted():
    assert submit_refusal(DRAFT, [_line('1'), _line('0.5')]) is None


@pytest.mark.parametrize('state', [SUBMITTED, APPROVED, CANCELLED])
def test_only_a_draft_is_submitted(state):  # noqa: ANN001
    assert submit_refusal(state, [_line('1')]) == (
        ErrorCode.PURCHASE_REQUEST_NOT_DRAFT,
        'Only a draft request can be submitted',
        {'status': state},
    )
    assert ErrorCode('purchase_request.not_draft').status == 409


def test_a_request_without_lines_is_not_submitted():
    assert submit_refusal(DRAFT, []) == (
        ErrorCode.PURCHASE_REQUEST_NO_LINES,
        'The request has no lines',
        {},
    )
    assert ErrorCode('purchase_request.no_lines').status == 409


def test_a_line_without_quantity_is_not_submitted():
    assert submit_refusal(DRAFT, [_line('1'), _line('0', 'Clavo')]) == (
        ErrorCode.PURCHASE_REQUEST_LINE_WITHOUT_QUANTITY,
        'Line Clavo has no quantity',
        {'line_id': 9},
    )
    assert ErrorCode('purchase_request.line_without_quantity').status == 422


# ── Minimum order quantity (FR-002, FR-003) ───────────────────────────────────


@pytest.mark.parametrize(
    'quantity, minimum',
    [('1', 0), ('1', 1), ('6', 6), ('7', 6)],
)
def test_no_warning_at_or_above_the_minimum(quantity, minimum):  # noqa: ANN001
    assert below_minimum_warning('P1', Decimal(quantity), minimum) is None


def test_a_warning_below_the_minimum_names_it():
    assert below_minimum_warning('P1', Decimal('2'), 6) == (
        'P1: quantity 2 is below the minimum order quantity of 6'
    )


@pytest.mark.parametrize('minimum, expected', [(0, '1'), (1, '1'), (12, '12')])
def test_default_quantity(minimum, expected):  # noqa: ANN001
    assert default_quantity(minimum) == Decimal(expected)


# ── Facility (FR-002) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'serial, old, new',
    [(None, 1, 2), (None, 1, 1), (5, 1, 1)],
)
def test_a_move_is_allowed_before_a_serial_or_within_the_facility(serial, old, new):  # noqa: ANN001
    assert facility_move_refusal(serial, old, new) is None


def test_a_numbered_request_stays_in_its_facility():
    assert facility_move_refusal(5, 1, 2) == (
        ErrorCode.PURCHASE_REQUEST_FACILITY_LOCKED,
        'A numbered request cannot move to another facility',
        {},
    )


# ── Approve, reject, cancel, mark ─────────────────────────────────────────────


@pytest.mark.parametrize('refusal', [approve_refusal, reject_refusal])
def test_approve_and_reject_need_a_submitted_request(refusal):  # noqa: ANN001
    assert refusal(SUBMITTED) is None
    for state in (DRAFT, APPROVED, CANCELLED):
        assert refusal(state) == (
            ErrorCode.PURCHASE_REQUEST_NOT_SUBMITTED,
            'Only a submitted request can be approved or rejected',
            {'status': state},
        )


@pytest.mark.parametrize('state', [DRAFT, SUBMITTED, APPROVED])
def test_cancel_from_any_live_state(state):  # noqa: ANN001
    assert cancel_refusal(state, 0) is None


def test_cancel_twice_is_refused():
    assert cancel_refusal(CANCELLED, 0) == (
        ErrorCode.PURCHASE_REQUEST_ALREADY_CANCELLED,
        'The request is already cancelled',
        {},
    )


def test_cancel_is_refused_once_a_line_is_on_a_live_order():
    assert cancel_refusal(APPROVED, 2) == (
        ErrorCode.PURCHASE_REQUEST_LINES_ON_LIVE_ORDER,
        '2 lines of the request are on a live purchase order',
        {'count': 2},
    )


@pytest.mark.parametrize('state', [SUBMITTED, APPROVED])
@pytest.mark.parametrize('to_purchase', [True, False])
def test_marking_a_submitted_or_approved_request(state, to_purchase):  # noqa: ANN001
    assert mark_refusal(state, to_purchase, on_live_po=False, line_id=4) is None


@pytest.mark.parametrize('state', [DRAFT, CANCELLED])
def test_marking_needs_a_submitted_or_approved_request(state):  # noqa: ANN001
    assert mark_refusal(state, True, on_live_po=False, line_id=4) == (
        ErrorCode.PURCHASE_REQUEST_LINES_NOT_MARKABLE,
        'Lines can be marked only on a submitted or approved request',
        {'status': state},
    )


def test_unmarking_a_line_on_a_live_order_is_refused():
    assert mark_refusal(APPROVED, False, on_live_po=True, line_id=4) == (
        ErrorCode.PURCHASE_REQUEST_LINE_ON_LIVE_ORDER,
        'The line is on a live purchase order',
        {'line_id': 4},
    )
    assert mark_refusal(APPROVED, True, on_live_po=True, line_id=4) is None


# ── Coded errors (spec 036) ───────────────────────────────────────────────────


def test_the_codes_of_the_refusals_are_in_the_catalog():
    for code in (
        'purchase_request.not_draft',
        'purchase_request.no_lines',
        'purchase_request.line_without_quantity',
        'purchase_request.facility_locked',
        'purchase_request.not_submitted',
        'purchase_request.already_cancelled',
        'purchase_request.lines_on_live_order',
        'purchase_request.lines_not_markable',
        'purchase_request.line_on_live_order',
        'purchase_request.warehouse_facility_changed',
    ):
        assert ErrorCode(code)


def test_a_status_filter_it_does_not_know_is_coded():
    from app.core.errors import DomainError
    from app.services.purchase_request_service import request_status_clause

    with pytest.raises(DomainError) as exc:
        request_status_clause('paid')
    assert exc.value.code == 'status_filter.unsupported'
    assert exc.value.status_code == 422
    assert exc.value.params == {'resource': 'purchase_request', 'status': 'paid'}


def test_a_stale_facility_is_coded():
    from app.core.errors import DomainError
    from app.services.purchase_request_service import _assert_mapped_facility

    with pytest.raises(DomainError) as exc:
        _assert_mapped_facility(1, 2)
    assert exc.value.code == 'purchase_request.warehouse_facility_changed'
    assert exc.value.status_code == 409
    assert exc.value.params == {}


def test_changing_a_submitted_request_is_coded():
    from app.core.errors import DomainError
    from app.services.purchase_request_service import _assert_draft

    request = SimpleNamespace(completed=True, approved=False, cancelled=False)
    with pytest.raises(DomainError) as exc:
        _assert_draft(request)
    assert exc.value.code == 'purchase_request.not_draft'
    assert exc.value.params == {'status': 'submitted'}


@pytest.mark.asyncio
async def test_bulk_approve_copies_the_code_and_params_beside_the_reason():
    from unittest.mock import patch

    from app.core.errors import DomainError
    from app.services import purchase_request_service as service

    async def approve(db, request_id, *, current):  # noqa: ANN001, ANN202
        if request_id == 2:
            raise DomainError(
                ErrorCode.PURCHASE_REQUEST_NOT_SUBMITTED,
                'Only a submitted request can be approved or rejected',
                {'status': DRAFT},
            )

    with patch.object(service, 'approve', approve):
        results = await service.approve_many(None, [1, 2], current=None)  # type: ignore[arg-type]

    assert results[0] == {
        'purchase_request_id': 1,
        'approved': True,
        'reason': None,
        'code': None,
        'params': None,
    }
    assert results[1] == {
        'purchase_request_id': 2,
        'approved': False,
        'reason': 'Only a submitted request can be approved or rejected',
        'code': 'purchase_request.not_submitted',
        'params': {'status': 'draft'},
    }
