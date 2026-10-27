"""Pure rules of purchase requests (spec 031, US1): serial, submit, warnings, defaults, refusals."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

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
    return SimpleNamespace(quantity=Decimal(quantity), product_name=name)


@pytest.mark.parametrize('highest, expected', [(None, 1), (0, 1), (41, 42)])
def test_next_request_serial(highest, expected):  # noqa: ANN001
    assert next_request_serial(highest) == expected


# ── Submit (FR-004) ───────────────────────────────────────────────────────────


def test_a_draft_with_lines_can_be_submitted():
    assert submit_refusal(DRAFT, [_line('1'), _line('0.5')]) is None


@pytest.mark.parametrize('state', [SUBMITTED, APPROVED, CANCELLED])
def test_only_a_draft_is_submitted(state):  # noqa: ANN001
    assert submit_refusal(state, [_line('1')]) == (409, 'Only a draft request can be submitted')


def test_a_request_without_lines_is_not_submitted():
    assert submit_refusal(DRAFT, []) == (409, 'The request has no lines')


def test_a_line_without_quantity_is_not_submitted():
    assert submit_refusal(DRAFT, [_line('1'), _line('0', 'Clavo')]) == (
        422,
        'Line Clavo has no quantity',
    )


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
    assert facility_move_refusal(5, 1, 2) == ('A numbered request cannot move to another facility')


# ── Approve, reject, cancel, mark ─────────────────────────────────────────────


@pytest.mark.parametrize('refusal', [approve_refusal, reject_refusal])
def test_approve_and_reject_need_a_submitted_request(refusal):  # noqa: ANN001
    assert refusal(SUBMITTED) is None
    for state in (DRAFT, APPROVED, CANCELLED):
        assert refusal(state) == 'Only a submitted request can be approved or rejected'


@pytest.mark.parametrize('state', [DRAFT, SUBMITTED, APPROVED])
def test_cancel_from_any_live_state(state):  # noqa: ANN001
    assert cancel_refusal(state, 0) is None


def test_cancel_twice_is_refused():
    assert cancel_refusal(CANCELLED, 0) == 'The request is already cancelled'


def test_cancel_is_refused_once_a_line_is_on_a_live_order():
    assert cancel_refusal(APPROVED, 2) == '2 lines of the request are on a live purchase order'


@pytest.mark.parametrize('state', [SUBMITTED, APPROVED])
@pytest.mark.parametrize('to_purchase', [True, False])
def test_marking_a_submitted_or_approved_request(state, to_purchase):  # noqa: ANN001
    assert mark_refusal(state, to_purchase, on_live_po=False) is None


@pytest.mark.parametrize('state', [DRAFT, CANCELLED])
def test_marking_needs_a_submitted_or_approved_request(state):  # noqa: ANN001
    assert mark_refusal(state, True, on_live_po=False) == (
        'Lines can be marked only on a submitted or approved request'
    )


def test_unmarking_a_line_on_a_live_order_is_refused():
    assert mark_refusal(APPROVED, False, on_live_po=True) == (
        'The line is on a live purchase order'
    )
    assert mark_refusal(APPROVED, True, on_live_po=True) is None
