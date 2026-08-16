"""Delivery-order service logic: quantities, editability, branching and settlement.

The arithmetic here is the feature's load-bearing part. SC-003 says ordered = delivered + returned
+ committed + open on every line at every point, and FR-026 is the same statement rearranged — if
those two ever disagree, a partial delivery double-counts its remainder.
"""

import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.enums import DeliveryOrderStatus as S
from app.enums import FulfillmentType
from app.models.sales import SalesOrder, SalesOrderDetail
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

    def test_an_empty_request_narrows_to_nothing_rather_than_everything(self) -> None:
        """#165 — `lines: []` means "carry nothing yet", the opposite of omitting the field."""
        assert service.narrow_to_requested(self._deliverable((1, '10'), (2, '5')), []) == []

    def test_the_two_are_distinguished_by_identity_not_truthiness(self) -> None:
        """The regression this pins: `if lines:` reads `[]` as omitted and claims the whole sale.

        Both cases are falsy, and the difference between them is the whole of #165 — an empty
        destination versus one carrying every quantity the sale still owes. Nothing else in the
        function would fail if the test were loosened, so it is pinned here.
        """
        # The body only: the docstring names the mistake in order to warn against it.
        body = inspect.getsource(service.create_from_sales_order).split('"""')[-1]

        assert 'if lines is not None:' in body
        assert 'if lines:' not in body

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


class TestAddingALineToAnExistingDraft:
    """#163 — a detail row that is not born inside `create_from_sales_order`.

    Before this, a line dropped with `DELETE` could never be restored and a line left out at
    creation could never be added, so every quantity had to be decided in the create call. The
    bound it checks against is `_covered_quantities`, the same figure create and `update_line` use:
    the three cannot between them over-claim a sales order line.
    """

    @staticmethod
    def _draft(status: S = S.DRAFT) -> SimpleNamespace:
        return SimpleNamespace(delivery_order_id=1, status=status, customer=5, facility=1)

    @staticmethod
    def _db(*, sales_line, sales_orders=None, existing: int | None = None) -> SimpleNamespace:
        # Sale 42 is the one the order was raised from; 99 is a second sale of the same customer,
        # which consolidation has to accept.
        sales = sales_orders or {42: SimpleNamespace(customer=5), 99: SimpleNamespace(customer=5)}
        objects = {(SalesOrderDetail, 21): sales_line} | {
            (SalesOrder, k): v for k, v in sales.items()
        }

        async def get(model, ident):  # noqa: ANN001, ANN202
            return objects.get((model, ident))

        db = SimpleNamespace(added=[])
        db.get = get
        db.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(first=lambda: existing)
            )
        )
        db.add = db.added.append
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    @staticmethod
    def _sales_line(ordered: str = '10') -> SimpleNamespace:
        return SimpleNamespace(
            sales_order_detail_id=21,
            sales_order=42,
            product=3,
            product_code='ABC',
            product_name='Widget',
            warehouse=2,
            quantity=Decimal(ordered),
        )

    @staticmethod
    def _request(sales_order_detail: int = 21, quantity: str = '4') -> DeliveryOrderLineRequest:
        return DeliveryOrderLineRequest(
            sales_order_detail=sales_order_detail, quantity=Decimal(quantity)
        )

    async def _add(self, db, order=None, request=None, covered=None):  # noqa: ANN001, ANN202
        with patch.object(
            service, 'sales_orders_of', AsyncMock(return_value={1: 42})
        ), patch.object(
            service, '_covered_quantities', AsyncMock(return_value=covered or {})
        ):
            return await service.add_line(db, order or self._draft(), request or self._request())

    @pytest.mark.asyncio
    async def test_it_copies_the_sales_line_onto_a_fresh_detail_row(self) -> None:
        db = self._db(sales_line=self._sales_line())

        line = await self._add(db)

        assert db.added == [line]
        assert (line.delivery_order, line.sales_order_detail) == (1, 21)
        assert (line.product, line.product_code, line.warehouse) == (3, 'ABC', 2)
        assert line.quantity == Decimal('4')

    @pytest.mark.asyncio
    async def test_a_new_row_starts_with_nothing_committed_delivered_or_returned(self) -> None:
        """Otherwise SC-003 breaks the moment the line is added."""
        db = self._db(sales_line=self._sales_line())

        line = await self._add(db)

        assert service.open_quantity(line) == line.quantity

    @pytest.mark.asyncio
    @pytest.mark.parametrize('status', [s for s in S if s is not S.DRAFT])
    async def test_only_a_draft_accepts_one(self, status: S) -> None:
        db = self._db(sales_line=self._sales_line())

        with pytest.raises(HTTPException) as exc:
            await self._add(db, order=self._draft(status))

        assert exc.value.status_code == 409
        assert db.added == []

    @pytest.mark.asyncio
    async def test_what_the_sale_no_longer_owes_is_refused(self) -> None:
        """Six already covered elsewhere leaves four, and five is asked for."""
        db = self._db(sales_line=self._sales_line('10'))

        with pytest.raises(HTTPException) as exc:
            await self._add(db, request=self._request(quantity='5'), covered={21: Decimal('6')})

        assert exc.value.status_code == 422
        assert '4 left to deliver' in exc.value.detail
        assert db.added == []

    @pytest.mark.asyncio
    async def test_exactly_what_is_left_is_allowed(self) -> None:
        """The boundary: `>` not `>=`, so the last destination can take the remainder."""
        db = self._db(sales_line=self._sales_line('10'))

        line = await self._add(db, request=self._request(quantity='4'), covered={21: Decimal('6')})

        assert line.quantity == Decimal('4')

    @pytest.mark.asyncio
    async def test_a_line_this_order_already_carries_is_refused_naming_it(self) -> None:
        """Not folded into the existing row: `PUT .../lines/{id}` is the one way to change an
        amount, so the response says which id to use."""
        db = self._db(sales_line=self._sales_line(), existing=11)

        with pytest.raises(HTTPException) as exc:
            await self._add(db)

        assert exc.value.status_code == 409
        assert 'as line 11' in exc.value.detail
        assert db.added == []

    @pytest.mark.asyncio
    async def test_an_unknown_sales_order_line_is_refused(self) -> None:
        db = self._db(sales_line=None)

        with pytest.raises(HTTPException) as exc:
            await self._add(db)

        assert exc.value.status_code == 422
        assert 'not a deliverable line of this customer' in exc.value.detail

    @pytest.mark.asyncio
    async def test_a_second_sales_orders_line_is_accepted(self) -> None:
        """Consolidation: one shipment carrying two of the customer's sales.

        This asserted a 422 when #163 shipped — the guard compared the line's sale against the one
        already on the order. 261 of the 27,921 sale-linked delivery orders in this database carry
        two or three sales, so the check refused an operation the business does.
        """
        second = self._sales_line()
        second.sales_order = 99
        db = self._db(sales_line=second)

        line = await self._add(db)

        assert db.added == [line]
        assert line.sales_order_detail == 21

    @pytest.mark.asyncio
    async def test_another_customers_line_is_refused(self) -> None:
        """The one thing that does hold: no consolidated order in the database spans customers."""
        db = self._db(
            sales_line=self._sales_line(), sales_orders={42: SimpleNamespace(customer=999)}
        )

        with pytest.raises(HTTPException) as exc:
            await self._add(db)

        assert exc.value.status_code == 422
        assert 'not a deliverable line of this customer' in exc.value.detail
        assert db.added == []

    @pytest.mark.asyncio
    async def test_the_customer_is_read_from_the_sale_not_the_order_it_is_already_on(self) -> None:
        """An empty draft — every line deleted — has no sale on it, and must still work.

        This was a special case with its own branch while sale identity was the rule. Now it is
        simply what the rule already says, so the empty draft needs no branch of its own.
        """
        db = self._db(sales_line=self._sales_line())

        with patch.object(service, '_covered_quantities', AsyncMock(return_value={})):
            line = await service.add_line(db, self._draft(), self._request())

        assert line.sales_order_detail == 21


class TestTheOriginatingSalesAreDerived:
    """#147 — "which delivery orders belong to sale N?", answerable at last.

    Nothing stores the link on the header: it lives on the lines, and a child order raised by a
    partial delivery inherits it with its lines. Deriving it is therefore the version that cannot
    drift; the cost is one query, which must stay one query for a whole page.

    Nor *could* it be stored: the relation is many-to-many, so no column on `delivery_order` could
    hold it. The line is the join row, which is why deriving was right for a stronger reason than
    "nothing to keep in step".
    """

    @staticmethod
    def _db(rows: list) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(all=lambda: rows, scalars=lambda: None)
        )
        return db

    @pytest.mark.asyncio
    async def test_it_maps_each_delivery_order_to_its_sales(self) -> None:
        db = self._db([(1, 42), (2, 42), (3, 51)])

        assert await service.sales_orders_of(db, [1, 2, 3]) == {1: [42], 2: [42], 3: [51]}

    @pytest.mark.asyncio
    async def test_a_consolidated_shipment_reports_every_sale_it_carries(self) -> None:
        """`func.min` answered 42 here and dropped 51 — silently, since one int cannot show that
        it is a truncation. 261 delivery orders in the database carry two or three sales."""
        db = self._db([(1, 42), (1, 51), (1, 63)])

        assert await service.sales_orders_of(db, [1]) == {1: [42, 51, 63]}

    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', [1, 5, 50])
    async def test_one_query_regardless_of_page_size(self, size: int) -> None:
        db = self._db([])

        await service.sales_orders_of(db, list(range(1, size + 1)))

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_an_empty_page_issues_no_query(self) -> None:
        db = self._db([])

        assert await service.sales_orders_of(db, []) == {}
        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_attaching_writes_the_sales_onto_every_order(self) -> None:
        db = self._db([(1, 42)])
        orders = [_order(), SimpleNamespace(delivery_order_id=2)]

        await service.attach_sales_orders(db, orders)

        assert orders[0].sales_orders == [42]
        # Not in the result set: no line of it links to a sale — an empty list, not a failure, and
        # not `None`, so a client can iterate the field without checking it first.
        assert orders[1].sales_orders == []

    @pytest.mark.asyncio
    async def test_the_query_orders_the_ids_so_the_list_is_stable(self) -> None:
        """Left to the database's row order, the same shipment could answer [42, 51] then
        [51, 42] and a client diffing the two would see a change that did not happen."""
        source = inspect.getsource(service.sales_orders_of)

        assert '.distinct()' in source
        assert '.order_by(' in source

    @pytest.mark.asyncio
    async def test_the_filter_matches_through_the_lines(self) -> None:
        """The header has no `sales_order` column, so the filter has to reach the sale's lines."""
        source = inspect.getsource(service.list_orders)

        assert 'if sales_order is not None:' in source
        assert 'SalesOrderDetail.sales_order == sales_order' in source
