"""The transition chokepoint: legality, reasons, and the guarantee that nothing slips past it.

Every status change in this feature routes through `transition()`. If a service could move a
status without it, SC-008 ("every status change appears in the history") would depend on everyone
remembering — which is exactly what research R7 rejected the ORM event listener to avoid.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.enums import DeliveryOrderStatus as S
from app.enums import FulfillmentType
from app.services.delivery_events import (
    LEGAL,
    TERMINAL,
    TYPE_RESTRICTED,
    assert_legal,
    record_creation,
    transition,
)


def _db() -> AsyncMock:
    db = AsyncMock()
    db.added = []
    db.add = lambda obj: db.added.append(obj)
    return db


def _order(status: S, fulfillment: FulfillmentType = FulfillmentType.DELIVERY) -> SimpleNamespace:
    return SimpleNamespace(delivery_order_id=1, status=status, fulfillment_type=fulfillment)


class TestTransitionWritesExactlyOneEvent:
    def test_records_from_to_employee_and_time(self) -> None:
        db = _db()
        order = _order(S.IN_PREPARATION)

        transition(db, order, S.IN_TRANSIT, employee=7)

        (event,) = db.added
        assert event.from_status == S.IN_PREPARATION
        assert event.to_status == S.IN_TRANSIT
        assert event.employee == 7
        assert event.event_time is not None
        assert order.status == S.IN_TRANSIT

    def test_one_event_per_call_not_two(self) -> None:
        """Approval branches in a single transition; a paired APPROVED entry would be noise."""
        db = _db()

        transition(db, _order(S.PENDING_APPROVAL), S.IN_PREPARATION, employee=7)

        assert len(db.added) == 1

    def test_creation_records_a_null_from_status(self) -> None:
        db = _db()
        order = _order(S.DRAFT)

        record_creation(db, order, employee=7)

        (event,) = db.added
        assert event.from_status is None
        assert event.to_status == S.DRAFT


class TestLegality:
    def test_illegal_transition_names_both_statuses(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_legal(_order(S.DRAFT), S.DELIVERED)

        assert exc.value.status_code == 409
        assert 'DRAFT' in exc.value.detail and 'DELIVERED' in exc.value.detail

    @pytest.mark.parametrize('terminal', sorted(TERMINAL, key=lambda s: s.value))
    def test_every_terminal_status_refuses_all_further_moves(self, terminal: S) -> None:
        for target in S:
            with pytest.raises(HTTPException) as exc:
                assert_legal(_order(terminal), target)
            assert exc.value.status_code == 409

    def test_in_transit_cannot_be_cancelled(self) -> None:
        """Goods on the road are resolved at the stop, not by cancelling (FR-007)."""
        with pytest.raises(HTTPException):
            assert_legal(_order(S.IN_TRANSIT), S.CANCELLED)

    @pytest.mark.parametrize(
        'from_status',
        [S.DRAFT, S.PENDING_APPROVAL, S.APPROVED, S.READY_FOR_PICKUP, S.IN_PREPARATION, S.FAILED],
    )
    def test_every_other_non_terminal_status_can_be_cancelled(self, from_status: S) -> None:
        assert_legal(_order(from_status), S.CANCELLED)

    def test_failed_is_not_terminal(self) -> None:
        """It re-queues for another attempt or cancels (v2 D2, FR-051)."""
        assert S.FAILED not in TERMINAL
        assert_legal(_order(S.FAILED), S.IN_PREPARATION)


class TestFulfillmentTypeRestrictions:
    """The branch happens at approval, so legality is not a function of the statuses alone."""

    def test_delivery_order_cannot_rest_at_approved(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_legal(_order(S.PENDING_APPROVAL, FulfillmentType.DELIVERY), S.APPROVED)

        assert 'COUNTER_PICKUP' in exc.value.detail

    def test_delivery_order_cannot_be_marked_ready_for_pickup(self) -> None:
        with pytest.raises(HTTPException):
            assert_legal(_order(S.APPROVED, FulfillmentType.DELIVERY), S.READY_FOR_PICKUP)

    def test_counter_pickup_cannot_enter_preparation(self) -> None:
        order = _order(S.PENDING_APPROVAL, FulfillmentType.COUNTER_PICKUP)
        with pytest.raises(HTTPException) as exc:
            assert_legal(order, S.IN_PREPARATION)

        assert 'DELIVERY' in exc.value.detail

    def test_each_type_may_take_its_own_branch(self) -> None:
        assert_legal(_order(S.PENDING_APPROVAL, FulfillmentType.DELIVERY), S.IN_PREPARATION)
        assert_legal(_order(S.PENDING_APPROVAL, FulfillmentType.COUNTER_PICKUP), S.APPROVED)
        assert_legal(_order(S.APPROVED, FulfillmentType.COUNTER_PICKUP), S.READY_FOR_PICKUP)

    def test_every_restricted_pair_is_also_declared_legal(self) -> None:
        """A restriction on a transition the table never permits would be dead configuration."""
        for (from_status, to_status) in TYPE_RESTRICTED:
            assert to_status in LEGAL[from_status]


class TestReasons:
    @pytest.mark.parametrize('target', [S.DRAFT, S.FAILED, S.CANCELLED])
    def test_blank_reason_is_refused(self, target: S) -> None:
        source = {S.DRAFT: S.PENDING_APPROVAL, S.FAILED: S.IN_TRANSIT, S.CANCELLED: S.DRAFT}[target]

        with pytest.raises(HTTPException) as exc:
            transition(_db(), _order(source), target, employee=7, reason='   ')

        assert exc.value.status_code == 422

    def test_reason_is_stored_trimmed(self) -> None:
        db = _db()

        transition(db, _order(S.DRAFT), S.CANCELLED, employee=7, reason='  customer changed mind ')

        assert db.added[0].reason == 'customer changed mind'

    def test_transitions_that_need_no_reason_accept_none(self) -> None:
        transition(_db(), _order(S.IN_PREPARATION), S.IN_TRANSIT, employee=7)


class TestReachability:
    """SC-002 — every status is reachable under at least one supported configuration.

    Driven through `transition()` rather than by walking `LEGAL`, so it proves the guard admits
    each status rather than proving the table is self-consistent.
    """

    def _walk(self, path: list[S], fulfillment: FulfillmentType) -> set[S]:
        db = _db()
        order = _order(S.DRAFT, fulfillment)
        record_creation(db, order, employee=1)
        seen = {S.DRAFT}
        for step in path:
            reason = 'because' if step in (S.DRAFT, S.FAILED, S.CANCELLED) else None
            transition(db, order, step, employee=1, reason=reason)
            seen.add(step)
        return seen

    def test_approval_required_delivery_path(self) -> None:
        seen = self._walk(
            [S.PENDING_APPROVAL, S.DRAFT, S.PENDING_APPROVAL, S.IN_PREPARATION, S.IN_TRANSIT,
             S.DELIVERED],
            FulfillmentType.DELIVERY,
        )
        assert {S.DRAFT, S.PENDING_APPROVAL, S.IN_PREPARATION, S.IN_TRANSIT, S.DELIVERED} <= seen

    def test_counter_pickup_path(self) -> None:
        seen = self._walk(
            [S.PENDING_APPROVAL, S.APPROVED, S.READY_FOR_PICKUP, S.PICKED_UP],
            FulfillmentType.COUNTER_PICKUP,
        )
        assert {S.APPROVED, S.READY_FOR_PICKUP, S.PICKED_UP} <= seen

    def test_failure_retry_and_partial_paths(self) -> None:
        failed = self._walk(
            [S.IN_PREPARATION, S.IN_TRANSIT, S.FAILED, S.IN_PREPARATION, S.CANCELLED],
            FulfillmentType.DELIVERY,
        )
        partial = self._walk(
            [S.IN_PREPARATION, S.IN_TRANSIT, S.PARTIALLY_DELIVERED], FulfillmentType.DELIVERY
        )
        assert {S.FAILED, S.CANCELLED} <= failed
        assert S.PARTIALLY_DELIVERED in partial

    def test_all_eleven_statuses_are_reachable_across_configurations(self) -> None:
        seen = (
            self._walk(
                [S.PENDING_APPROVAL, S.IN_PREPARATION, S.IN_TRANSIT, S.DELIVERED],
                FulfillmentType.DELIVERY,
            )
            | self._walk(
                [S.PENDING_APPROVAL, S.APPROVED, S.READY_FOR_PICKUP, S.PICKED_UP],
                FulfillmentType.COUNTER_PICKUP,
            )
            | self._walk(
                [S.IN_PREPARATION, S.IN_TRANSIT, S.PARTIALLY_DELIVERED], FulfillmentType.DELIVERY
            )
            | self._walk(
                [S.IN_PREPARATION, S.IN_TRANSIT, S.FAILED, S.CANCELLED], FulfillmentType.DELIVERY
            )
        )

        assert seen == set(S), f'unreachable: {set(S) - seen}'
