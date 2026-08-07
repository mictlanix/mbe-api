"""Delivery-order service logic: quantities, editability, branching and settlement.

The arithmetic here is the feature's load-bearing part. SC-003 says ordered = delivered + returned
+ committed + open on every line at every point, and FR-026 is the same statement rearranged — if
those two ever disagree, a partial delivery double-counts its remainder.
"""

import inspect
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.enums import DeliveryOrderStatus as S
from app.enums import FulfillmentType
from app.schemas.delivery_order import DeliveryOrderLineRequest
from app.services import delivery_order_service as service


def _line(
    ordered: str = '10',
    committed: str = '0',
    delivered: str = '0',
    returned: str = '0',
) -> SimpleNamespace:
    return SimpleNamespace(
        delivery_order_detail_id=11,
        quantity=Decimal(ordered),
        committed_quantity=Decimal(committed),
        delivered_quantity=Decimal(delivered),
        returned_quantity=Decimal(returned),
    )


def _order(status: S = S.DRAFT, fulfillment: FulfillmentType = FulfillmentType.DELIVERY):
    return SimpleNamespace(delivery_order_id=1, status=status, fulfillment_type=fulfillment)


class TestOpenQuantity:
    def test_a_fresh_line_is_entirely_open(self) -> None:
        assert service.open_quantity(_line('10')) == Decimal('10')

    def test_committing_reduces_it(self) -> None:
        assert service.open_quantity(_line('10', committed='4')) == Decimal('6')

    def test_delivering_reduces_it(self) -> None:
        assert service.open_quantity(_line('10', delivered='3')) == Decimal('7')

    def test_returned_goods_are_subtracted_too(self) -> None:
        """The FR-026 term that stops a partial delivery being counted twice."""
        assert service.open_quantity(_line('10', delivered='3', returned='2')) == Decimal('5')

    def test_a_settled_partial_delivery_leaves_nothing_open(self) -> None:
        """5 sent, 3 accepted, 2 refused: the parent closes at 0 and the child carries the 2."""
        line = _line('5', committed='0', delivered='3', returned='2')

        assert service.open_quantity(line) == Decimal('0')

    def test_sc003_invariant_holds(self) -> None:
        line = _line('10', committed='2', delivered='3', returned='1')

        total = (
            line.delivered_quantity
            + line.returned_quantity
            + line.committed_quantity
            + service.open_quantity(line)
        )
        assert total == line.quantity


class TestAssertEditable:
    def test_a_draft_is_editable(self) -> None:
        service.assert_editable(_order(S.DRAFT))

    @pytest.mark.parametrize(
        'status',
        [S.PENDING_APPROVAL, S.APPROVED, S.IN_PREPARATION, S.IN_TRANSIT, S.DELIVERED, S.CANCELLED],
    )
    def test_everything_else_is_refused(self, status: S) -> None:
        with pytest.raises(HTTPException) as exc:
            service.assert_editable(_order(status))

        assert exc.value.status_code == 409
        assert status.name in exc.value.detail

    def test_does_not_reuse_the_sales_document_guard(self) -> None:
        """`documents.assert_editable` reads columns migration 008 dropped (research R8).

        Its `getattr(..., False)` defaults mean it would wave every delivery order through
        instead of failing loudly — a guard that always says yes.
        """
        source = inspect.getsource(service.assert_editable)
        # The docstring names it to explain the choice; the body must not call it.
        body = source.split('\"\"\"')[2]
        assert 'documents.assert_editable' not in body


class TestBranchTarget:
    """One transition at approval, branching on type — never a transient APPROVED (FR-024)."""

    def test_a_delivery_goes_to_preparation(self) -> None:
        target = service._branch_target(_order(fulfillment=FulfillmentType.DELIVERY))
        assert target is S.IN_PREPARATION

    def test_a_counter_pickup_rests_at_approved(self) -> None:
        assert (
            service._branch_target(_order(fulfillment=FulfillmentType.COUNTER_PICKUP)) is S.APPROVED
        )


class TestBuildProof:
    def _db(self):
        db = SimpleNamespace(added=[])
        db.add = db.added.append
        return db

    def test_stores_the_structured_evidence(self) -> None:
        db = self._db()

        proof = service.build_proof(
            db,
            receiver_name='  Juan Pérez ',
            receiver_id_shown=' INE 1234 ',
            image_file='abc.png',
            employee=7,
        )

        assert proof.receiver_name == 'Juan Pérez'
        assert proof.receiver_id_shown == 'INE 1234'
        assert proof.captured_by == 7
        assert proof.captured_time is not None
        assert db.added == [proof]

    @pytest.mark.parametrize(
        ('name', 'ident'), [('', 'INE 1'), ('Juan', ''), ('   ', '   ')]
    )
    def test_blank_evidence_is_refused(self, name: str, ident: str) -> None:
        with pytest.raises(HTTPException) as exc:
            service.build_proof(
                self._db(),
                receiver_name=name,
                receiver_id_shown=ident,
                image_file='abc.png',
                employee=7,
            )

        assert exc.value.status_code == 422


class TestTransitionsRouteThroughTheChokepoint:
    """Nothing here may set `order.status` directly, or SC-008 stops holding."""

    @pytest.mark.parametrize(
        'name',
        ['confirm', 'approve', 'reject', 'requeue', 'cancel', 'mark_ready_for_pickup',
         'confirm_pickup'],
    )
    def test_every_lifecycle_function_calls_transition(self, name: str) -> None:
        source = inspect.getsource(getattr(service, name))

        assert 'delivery_events.transition(' in source
        assert 'order.status =' not in source

    def test_requeue_returns_the_goods_to_the_open_pool(self) -> None:
        """FR-051a — without this a re-queued order has no open quantity and cannot be sent."""
        source = inspect.getsource(service.requeue)

        assert 'returned_quantity = Decimal(0)' in source

    def test_cancel_releases_commitments(self) -> None:
        source = inspect.getsource(service.cancel)

        assert 'committed_quantity = Decimal(0)' in source


class TestFallbackWarehouse:
    """Spec 013 FR-012. This was a live defect, not a port of an existing exclusion.

    Before spec 013 the fallback took `MIN(warehouse_id)` across every warehouse in the facility
    with no exclusion whatsoever, so a facility whose in-transit row happened to hold the lowest
    id would hand a delivery line the virtual location as its dispatch warehouse.
    """

    def test_in_transit_locations_are_excluded_from_the_automatic_choice(self) -> None:
        source = inspect.getsource(service._fallback_warehouse)

        assert 'in_transit.is_(False)' in source

    def test_the_exclusion_is_a_predicate_not_a_post_filter(self) -> None:
        """MIN() must be computed over the filtered set, or it still returns the transit row."""
        source = inspect.getsource(service._fallback_warehouse)

        min_at = source.index('func.min(')
        exclusion_at = source.index('in_transit.is_(False)')
        assert exclusion_at > min_at, 'the exclusion belongs inside the same query as MIN()'


class TestCounterPickupInventory:
    def test_pickup_consumes_from_the_store_with_no_transit_step(self) -> None:
        """FR-060 — the goods never travelled, so there is no in-transit leg."""
        source = inspect.getsource(service.confirm_pickup)

        # Asserted on identifiers, not prose: `inspect.getsource` includes the docstring, which
        # says "in-transit" in the course of explaining that there isn't one. Spec 013 retired
        # `in_transit_warehouse_id`, so naming it here would be a test that can never fail again.
        assert 'in_transit' not in source
        assert 'transit_warehouses_for' not in source
        assert 'outbound=True' in source

    def test_pickup_releases_only_this_order_s_lines(self) -> None:
        """A pickup may cover part of a sale; the rest must keep its claim."""
        source = inspect.getsource(service.confirm_pickup)

        assert 'release_reservation(' in source
        assert 'release_reservations(' not in source


class TestEveryLineIsDeliverable:
    """The per-line `delivery` flag is not consulted, and that is deliberate.

    `06-logistics.md` defines the deliverable set as the lines flagged `delivery = true`, and
    FR-012 originally followed it. Measured against the database, that rule yields the empty set:
    the column is 0 on all 910,891 rows, including the 54,741 the legacy delivery orders were
    actually raised from. Honouring it makes every call return "already fully delivered" — the
    feature cannot raise a single delivery order. What the data shows is the whole order being
    taken: 22,976 of 23,774 delivered sales orders carried every line.

    Pinned as a test because it reads like an omission, and the next person to compare the code
    against the source document will otherwise "fix" it back.
    """

    def test_creation_does_not_filter_on_the_delivery_flag(self) -> None:
        source = inspect.getsource(service.create_from_sales_order)

        assert 'delivery.is_(True)' not in source

    def test_coverage_and_writeback_do_not_filter_either(self) -> None:
        """They shared the filter, so FR-070 and FR-071 were dead for the same reason.

        `refresh_sales_order_delivered` returns early on an empty line set, so with the filter in
        place no sales order would ever have been marked delivered, and `delivery_coverage` would
        always have returned nothing.
        """
        for fn in (service.refresh_sales_order_delivered, service.delivery_coverage):
            assert 'delivery.is_(True)' not in inspect.getsource(fn)


class TestFulfillmentTypeIsPerDeliveryOrder:
    """One sales order can split across both kinds, so the type belongs to the delivery order.

    The customer collects part of the order at the counter and has the rest shipped. Deriving the
    type from the sale's ship-to address gives one answer per sale, which cannot express that —
    detection is the default, not the rule (FR-005, FR-005a).
    """

    def test_creation_accepts_an_explicit_type(self) -> None:
        signature = inspect.signature(service.create_from_sales_order)

        assert 'fulfillment_type' in signature.parameters
        assert signature.parameters['fulfillment_type'].default is None

    def test_detection_only_runs_when_no_type_is_given(self) -> None:
        source = inspect.getsource(service.create_from_sales_order)

        assert 'if fulfillment_type is None:' in source
        assert '_is_facility_address(' in source

    def test_the_type_still_cannot_be_changed_afterwards(self) -> None:
        """Selectable at creation, immutable after — FR-004 is unaffected."""
        source = inspect.getsource(service.update_order)

        assert 'fulfillment_type' not in source


class TestNarrowingToARequestedSubset:
    """#138 — one sale splitting across several destinations, without create-then-trim.

    The property that has to hold across a split is that every destination's quantities sum to the
    ordered amount, no more and no less. The bound each request is checked against is the same
    uncovered figure the default path uses, so the two cannot disagree.
    """

    @staticmethod
    def _deliverable(*pairs: tuple[int, str]) -> list:
        return [
            (SimpleNamespace(sales_order_detail_id=line_id), Decimal(remaining))
            for line_id, remaining in pairs
        ]

    @staticmethod
    def _request(sales_order_detail: int, quantity: str) -> DeliveryOrderLineRequest:
        return DeliveryOrderLineRequest(
            sales_order_detail=sales_order_detail, quantity=Decimal(quantity)
        )

    def test_it_keeps_only_the_named_lines_at_the_named_quantities(self) -> None:
        chosen = service.narrow_to_requested(
            self._deliverable((1, '10'), (2, '5'), (3, '4')),
            [self._request(1, '4'), self._request(3, '4')],
        )

        assert [(line.sales_order_detail_id, qty) for line, qty in chosen] == [
            (1, Decimal('4')),
            (3, Decimal('4')),
        ]

    def test_a_partial_quantity_of_one_line_is_allowed(self) -> None:
        """The whole point: this destination takes six of ten, the next takes the rest."""
        chosen = service.narrow_to_requested(
            self._deliverable((1, '10')), [self._request(1, '6')]
        )

        assert chosen[0][1] == Decimal('6')

    def test_two_destinations_can_between_them_claim_the_whole_line(self) -> None:
        first = service.narrow_to_requested(self._deliverable((1, '10')), [self._request(1, '6')])
        # The second create sees what the first left uncovered.
        second = service.narrow_to_requested(self._deliverable((1, '4')), [self._request(1, '4')])

        assert first[0][1] + second[0][1] == Decimal('10')

    def test_asking_for_more_than_is_undelivered_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            service.narrow_to_requested(self._deliverable((1, '4')), [self._request(1, '5')])

        assert exc.value.status_code == 422
        assert '4 undelivered' in exc.value.detail
        assert '5 requested' in exc.value.detail

    def test_a_line_from_another_sale_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            service.narrow_to_requested(self._deliverable((1, '10')), [self._request(99, '1')])

        assert exc.value.status_code == 422
        assert 'not an undelivered line' in exc.value.detail

    def test_a_line_already_covered_elsewhere_is_refused_the_same_way(self) -> None:
        """It is absent from `deliverable`, so it is indistinguishable from a foreign id — and the
        client can act no differently on the two."""
        with pytest.raises(HTTPException) as exc:
            service.narrow_to_requested(self._deliverable((2, '5')), [self._request(1, '1')])

        assert 'not an undelivered line' in exc.value.detail

    def test_naming_the_same_line_twice_is_refused(self) -> None:
        """Otherwise the second entry silently replaces the first, or double-claims the line."""
        with pytest.raises(HTTPException) as exc:
            service.narrow_to_requested(
                self._deliverable((1, '10')), [self._request(1, '4'), self._request(1, '4')]
            )

        assert exc.value.status_code == 422
        assert 'more than once' in exc.value.detail

    def test_claiming_exactly_what_is_left_is_allowed(self) -> None:
        """The boundary: `>` not `>=`, so the last destination can take the remainder."""
        chosen = service.narrow_to_requested(
            self._deliverable((1, '4')), [self._request(1, '4')]
        )

        assert chosen[0][1] == Decimal('4')

    def test_omitting_lines_leaves_the_default_path_untouched(self) -> None:
        """Existing callers must be unaffected — the narrowing is skipped, not applied empty."""
        source = inspect.getsource(service.create_from_sales_order)

        assert 'if lines is not None:' in source
        assert 'narrow_to_requested(deliverable, lines)' in source

    def test_the_requested_lines_are_not_rebound_before_the_narrowing(self) -> None:
        """The bug this pins: the sale's own lines were read into `lines`, shadowing the argument.

        `if lines is not None` then tested the query result, which is a list and never `None`, so
        the narrowing ran on every create — and ran against `SalesOrderDetail` rows, which carry no
        `sales_order_detail` attribute. `POST /delivery-orders` raised `AttributeError` for every
        caller, subset or not. Neither existing test caught it: the API tests patch the service out,
        and the unit tests call `narrow_to_requested` directly.
        """
        source = inspect.getsource(service.create_from_sales_order)

        assert 'sales_lines = list(' in source
        # Leading space, so `sales_lines` does not satisfy it.
        assert ' lines = list(' not in source


class TestTheDestinationHeaderAtCreation:
    """#146 — one sale sending goods to several addresses, each created complete in one call.

    `ship_to` was inherited from the sale, so every destination after the first had to be corrected
    with a follow-up `PUT`: two calls, with a window in which a draft holding committed quantities
    pointed at the wrong address.
    """

    def test_creation_accepts_the_four_header_fields(self) -> None:
        parameters = inspect.signature(service.create_from_sales_order).parameters

        for field in ('ship_to', 'contact', 'date', 'comment'):
            assert field in parameters
            assert parameters[field].default is None

    def test_each_one_falls_back_to_the_sale(self) -> None:
        """Omitting them all must leave an existing caller with exactly what it had before."""
        source = inspect.getsource(service.create_from_sales_order)

        assert 'ship_to if ship_to is not None else order.ship_to' in source
        assert 'contact if contact is not None else order.contact' in source
        assert 'date if date is not None else order.promise_date' in source

    def test_detection_reads_the_destination_rather_than_the_sale(self) -> None:
        """A counter pickup is one because of where the goods end up (FR-005, FR-005a)."""
        source = inspect.getsource(service.create_from_sales_order)

        assert '_is_facility_address(db, destination)' in source
        assert '_is_facility_address(db, order.ship_to)' not in source

    def test_the_header_is_still_editable_afterwards(self) -> None:
        """`PUT` keeps its job: this adds a creation path, it does not replace later edits."""
        source = inspect.getsource(service.update_order)

        for field in ('ship_to', 'contact', 'date', 'comment'):
            assert field in source
