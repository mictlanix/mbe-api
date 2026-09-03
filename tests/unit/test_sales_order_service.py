"""The sales order state machine and the rules that guard it.

The lifecycle rules pinned here are the ones clarified into the spec, and they are the reason the
order is the spine of the feature: pay requires completed and uncancelled (FR-042), cancel requires
unpaid with no live applications (FR-019, FR-019b), refund requires paid (FR-060). Cancel and
refund must never both be available for the same order — SC-010.
"""

import inspect
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.enums import FulfillmentType, PaymentTerms
from app.schemas.sales_order import (
    SalesOrderCreate,
    SalesOrderLineCreate,
    SalesOrderLineUpdate,
    SalesOrderUpdate,
)
from app.services import sales_order_service
from app.services.sales_order_service import (
    assert_can_cancel,
    assert_quantity_allowed,
    derive_due_date,
    stock_shortfalls,
    zero_priced_lines,
)


def _line(product: int, warehouse: int | None, quantity: str, price: str = '10') -> SimpleNamespace:
    return SimpleNamespace(
        product=product,
        warehouse=warehouse,
        quantity=Decimal(quantity),
        price=Decimal(price),
        product_name=f'Product {product}',
        sales_order_detail_id=product * 100,
    )


class TestDeriveDueDate:
    def test_immediate_terms_due_on_the_order_date(self) -> None:
        date = datetime(2026, 7, 25)

        assert derive_due_date(date, PaymentTerms.IMMEDIATE, credit_days=30) == date

    def test_credit_terms_add_the_customer_credit_days(self) -> None:
        due = derive_due_date(datetime(2026, 7, 25), PaymentTerms.NET_D, credit_days=15)

        assert due == datetime(2026, 8, 9)

    def test_credit_terms_with_zero_days_due_immediately(self) -> None:
        date = datetime(2026, 7, 25)

        assert derive_due_date(date, PaymentTerms.NET_D, credit_days=0) == date


class TestAssertQuantityAllowed:
    def test_quantity_at_the_minimum_is_allowed(self) -> None:
        assert_quantity_allowed(Decimal('5'), min_order_qty=5)

    def test_quantity_above_the_minimum_is_allowed(self) -> None:
        assert_quantity_allowed(Decimal('6'), min_order_qty=5)

    def test_quantity_below_the_minimum_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_quantity_allowed(Decimal('4'), min_order_qty=5)

        assert exc.value.status_code == 422
        assert '5' in exc.value.detail


class TestZeroPricedLines:
    def test_names_the_offending_lines(self) -> None:
        lines = [_line(1, 1, '1', price='10'), _line(2, 1, '1', price='0')]

        offenders = zero_priced_lines(lines)

        assert len(offenders) == 1
        assert 'Product 2' in offenders[0]

    def test_no_offenders_when_all_priced(self) -> None:
        assert zero_priced_lines([_line(1, 1, '1', price='10')]) == []


class TestStockShortfalls:
    def test_aggregates_the_same_product_across_lines(self) -> None:
        """One product on several lines is checked once against the total (FR-018)."""
        lines = [_line(1, 5, '4'), _line(1, 5, '4')]

        shortfalls = stock_shortfalls(lines, available={(1, 5): Decimal('6')}, stocked={1})

        assert len(shortfalls) == 1
        assert '8' in shortfalls[0] and '6' in shortfalls[0]

    def test_sufficient_stock_yields_nothing(self) -> None:
        lines = [_line(1, 5, '4')]

        assert stock_shortfalls(lines, available={(1, 5): Decimal('10')}, stocked={1}) == []

    def test_missing_warehouse_on_a_stocked_line_is_a_shortfall(self) -> None:
        lines = [_line(1, None, '1')]

        shortfalls = stock_shortfalls(lines, available={}, stocked={1})

        assert len(shortfalls) == 1
        assert 'warehouse' in shortfalls[0].lower()

    def test_non_stocked_products_are_ignored(self) -> None:
        """A product that does not require stock never blocks confirmation."""
        lines = [_line(1, None, '99')]

        assert stock_shortfalls(lines, available={}, stocked=set()) == []

    def test_separate_warehouses_are_checked_separately(self) -> None:
        lines = [_line(1, 5, '4'), _line(1, 6, '4')]

        shortfalls = stock_shortfalls(
            lines, available={(1, 5): Decimal('10'), (1, 6): Decimal('1')}, stocked={1}
        )

        assert len(shortfalls) == 1


class TestAssertCanCancel:
    def test_draft_can_be_cancelled(self) -> None:
        assert_can_cancel(SimpleNamespace(cancelled=False, paid=False), live_applications=[])

    def test_completed_unpaid_order_can_be_cancelled(self) -> None:
        assert_can_cancel(SimpleNamespace(cancelled=False, paid=False), live_applications=[])

    def test_paid_order_is_refused_and_directed_to_refund(self) -> None:
        """SC-010: a paid order is unwound by refunding, never by cancelling."""
        with pytest.raises(HTTPException) as exc:
            assert_can_cancel(SimpleNamespace(cancelled=False, paid=True), live_applications=[])

        assert exc.value.status_code == 409
        assert 'refund' in exc.value.detail.lower()

    def test_already_cancelled_order_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_can_cancel(SimpleNamespace(cancelled=True, paid=False), live_applications=[])

        assert exc.value.status_code == 409

    def test_partial_payment_blocks_cancellation_and_names_it(self) -> None:
        """FR-019b: money never moves as a side effect of cancelling."""
        with pytest.raises(HTTPException) as exc:
            assert_can_cancel(
                SimpleNamespace(cancelled=False, paid=False),
                live_applications=[SimpleNamespace(sales_order_payment_id=9)],
            )

        assert exc.value.status_code == 409
        assert '9' in exc.value.detail


class TestConfirmReservesRatherThanConsuming:
    """Confirmation claims stock; it no longer takes it off the shelf (FR-055, FR-055a, FR-056).

    Consumption moved to delivery, so `confirm_order` writes a reservation instead of an outbound
    ledger entry and `cancel_order` releases it instead of posting a compensating one. The stock
    check has to move with it: an unreserved check would let one physical unit satisfy an
    unlimited number of orders, every confirmation succeeding and the shortfall surfacing only
    when the truck is loaded.
    """

    def test_confirm_reserves_and_posts_no_ledger_entry(self) -> None:
        source = inspect.getsource(sales_order_service.confirm_order)
        assert 'stock_ledger.reserve(' in source
        assert 'post_movement' not in source

    def test_cancel_releases_and_posts_no_compensating_entry(self) -> None:
        source = inspect.getsource(sales_order_service.cancel_order)
        assert 'stock_ledger.release_reservations(' in source
        assert 'post_movement' not in source

    def test_confirm_checks_availability_not_raw_on_hand(self) -> None:
        """The paired half of the change. Without it, FR-055 silently oversells."""
        source = inspect.getsource(sales_order_service.confirm_order)
        assert 'stock_ledger.available(' in source
        assert 'stock_ledger.on_hand(' not in source


class TestOversellGuard:
    """One unit on hand, one already reserved: the second order must be refused (FR-055a)."""

    def test_a_reserved_unit_is_not_available_to_a_second_order(self) -> None:
        lines = [SimpleNamespace(product=1, warehouse=5, quantity=Decimal('1'), product_name='X')]

        # on_hand is 1 and stays 1 — confirmation no longer decrements it. Availability is 0
        # because the first order reserved the unit.
        shortfalls = stock_shortfalls(lines, available={(1, 5): Decimal('0')}, stocked={1})

        assert shortfalls != []
        # Existing message shape, unchanged by this feature: names the product id and the figure
        # it was compared against, which is now availability rather than on-hand.
        assert 'Product 1' in shortfalls[0]
        assert 'available' in shortfalls[0]

    def test_an_unreserved_unit_is_still_sellable(self) -> None:
        lines = [SimpleNamespace(product=1, warehouse=5, quantity=Decimal('1'), product_name='X')]

        assert stock_shortfalls(lines, available={(1, 5): Decimal('1')}, stocked={1}) == []


class TestTheDeliveryFlagIsGone:
    """`sales_order_detail.delivery` was removed by migration 009, and must not come back.

    It was written by this API and read by nothing. `docs/specs/06-logistics.md` presents it as
    selecting which lines a delivery order covers, which is why spec 012 implemented that rule
    first — and the rule selects nothing, because the column was 0 on all 910,891 rows. Honouring
    it broke delivery-order creation, the sales-order `delivered` write-back and the derived
    coverage figures simultaneously. Delivery orders are bounded by coverage now.
    """

    def test_line_creation_does_not_write_it(self) -> None:
        source = inspect.getsource(sales_order_service.add_line)

        assert 'delivery' not in source

    def test_it_is_not_an_updatable_field(self) -> None:
        source = inspect.getsource(sales_order_service.update_line)

        assert "'delivery'" not in source

    def test_the_model_no_longer_carries_it(self) -> None:
        from app.models.sales import SalesOrderDetail

        assert 'delivery' not in SalesOrderDetail.__table__.columns


class TestProductLookupReportsWhatCanBeSold:
    """Raw on-hand misleads a salesperson once confirmation checks availability instead.

    They see five units, promise them, and confirmation refuses — because those five are reserved
    by other confirmed orders. `available` is the figure that predicts the sale. `on_hand` stays
    beside it, because "we have five, three are spoken for" is more use than either alone.
    """

    def test_it_reports_availability_next_to_on_hand(self) -> None:
        source = inspect.getsource(sales_order_service.lookup_products)

        assert "'available': held - claimed" in source
        assert "'on_hand': held" in source

    def test_the_in_transit_warehouses_are_not_offered(self) -> None:
        """They are ordinary warehouse rows, so they would otherwise appear as pickable stock —
        goods already on a truck (spec 012, research R3).

        Excluded by flag rather than by a configured id: spec 013 made one per facility, so
        excluding a single id would leave thirteen of them offerable (FR-012).
        """
        source = inspect.getsource(sales_order_service.lookup_products)

        assert 'in_transit.is_(False)' in source
        assert 'in_transit_warehouse_id' not in source

    def test_stock_figures_are_batched_not_per_product(self) -> None:
        """Reporting two figures per warehouse per product would otherwise double an N+1."""
        source = inspect.getsource(sales_order_service.lookup_products)

        assert 'on_hand_by_warehouse(' in source
        assert 'reserved_by_warehouse(' in source
        assert 'await stock_ledger.on_hand(' not in source


class TestRepricingOnACustomerChange:
    """#131 — a line tracks whichever customer is on the order, unconditionally.

    The alternative considered was preserving a hand-entered price. `sales_order_detail` stores no
    marker distinguishing one from a listed price, so that could only have been a guess at what the
    previous customer's list would have charged — and a wrong guess silently keeps the *old*
    customer's price on the new customer's order.
    """

    @staticmethod
    def _db(lines: list, price_rows: list) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: lines)),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: price_rows)),
            ]
        )
        return db

    @staticmethod
    def _line(product: int, price: str, tax_rate: str = '0.16') -> SimpleNamespace:
        return SimpleNamespace(product=product, price=Decimal(price), tax_rate=Decimal(tax_rate))

    @pytest.mark.asyncio
    async def test_every_line_takes_the_new_customer_price(self) -> None:
        lines = [self._line(1, '100'), self._line(2, '50')]
        rows = [
            SimpleNamespace(product=1, price=Decimal('90')),
            SimpleNamespace(product=2, price=Decimal('45')),
        ]
        db = self._db(lines, rows)

        await sales_order_service._reprice_lines(
            db, SimpleNamespace(sales_order_id=1), SimpleNamespace(price_list=3)
        )

        assert [line.price for line in lines] == [Decimal('90'), Decimal('45')]

    @pytest.mark.asyncio
    async def test_a_hand_entered_price_is_overwritten_too(self) -> None:
        """The decision on #131: no override survives a customer change."""
        negotiated = self._line(1, '73.50')
        db = self._db([negotiated], [SimpleNamespace(product=1, price=Decimal('90'))])

        await sales_order_service._reprice_lines(
            db, SimpleNamespace(sales_order_id=1), SimpleNamespace(price_list=3)
        )

        assert negotiated.price == Decimal('90')

    @pytest.mark.asyncio
    async def test_tax_rate_is_left_alone(self) -> None:
        """Tax follows the product, not the customer — including a per-line override (#135)."""
        line = self._line(1, '100', tax_rate='0')
        db = self._db([line], [SimpleNamespace(product=1, price=Decimal('90'))])

        await sales_order_service._reprice_lines(
            db, SimpleNamespace(sales_order_id=1), SimpleNamespace(price_list=3)
        )

        assert line.tax_rate == Decimal('0')

    @pytest.mark.asyncio
    async def test_a_product_absent_from_the_new_list_prices_at_zero(self) -> None:
        """Same as `add_line` would produce for that customer; confirmation's gate catches it."""
        line = self._line(1, '100')
        db = self._db([line], [])

        await sales_order_service._reprice_lines(
            db, SimpleNamespace(sales_order_id=1), SimpleNamespace(price_list=3)
        )

        assert line.price == Decimal('0')

    @pytest.mark.asyncio
    async def test_an_empty_order_asks_for_no_prices(self) -> None:
        db = self._db([], [])

        await sales_order_service._reprice_lines(
            db, SimpleNamespace(sales_order_id=1), SimpleNamespace(price_list=3)
        )

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_prices_are_fetched_in_one_query_not_one_per_line(self) -> None:
        """A twenty-line register sale must not cost twenty round trips (the N+1 rule)."""
        lines = [self._line(n, '100') for n in range(1, 21)]
        rows = [SimpleNamespace(product=n, price=Decimal('90')) for n in range(1, 21)]
        db = self._db(lines, rows)

        await sales_order_service._reprice_lines(
            db, SimpleNamespace(sales_order_id=1), SimpleNamespace(price_list=3)
        )

        # One for the lines, one for their prices — flat regardless of line count.
        assert db.execute.await_count == 2


class TestCustomerChangeGuard:
    """Repricing is a response to a change; a `PUT` echoing the current customer is not one.

    Without this, a client sending the whole order back to edit its comment would wipe every
    negotiated price on it.
    """

    @staticmethod
    async def _update(
        *,
        from_customer: int,
        to_customer: int,
        order_salesperson: int = 4,
        customer_salesperson: int | None = None,
        sent: SalesOrderUpdate | None = None,
    ) -> tuple[AsyncMock, SimpleNamespace]:
        order = SimpleNamespace(
            sales_order_id=1,
            customer=from_customer,
            salesperson=order_salesperson,
            completed=False,
            cancelled=False,
            date=datetime(2026, 7, 25),
            updater=None,
            modification_time=None,
        )
        reprice = AsyncMock()
        incoming = SimpleNamespace(
            customer_id=to_customer, price_list=3, salesperson=customer_salesperson
        )
        with (
            patch.object(sales_order_service, '_customer_or_404', AsyncMock(return_value=incoming)),
            patch.object(sales_order_service, '_reprice_lines', reprice),
            patch.object(sales_order_service, 'attach_derived', AsyncMock(return_value=order)),
        ):
            await sales_order_service.update_order(
                AsyncMock(),
                order,
                sent if sent is not None else SalesOrderUpdate(customer=to_customer),
                current=SimpleNamespace(employee_id=7),
            )
        return reprice, order

    @pytest.mark.asyncio
    async def test_a_real_customer_change_reprices(self) -> None:
        reprice, _ = await self._update(from_customer=2, to_customer=5)

        assert reprice.await_count == 1

    @pytest.mark.asyncio
    async def test_resending_the_same_customer_does_not(self) -> None:
        reprice, _ = await self._update(from_customer=2, to_customer=2)

        assert reprice.await_count == 0


class TestTheSalespersonFollowsTheCustomer:
    """#195 — prices followed a customer change and the rep did not, so an order moved from A to
    B priced as B and commissioned as A's rep.

    The branch that matters is the new customer having *no* rep: `customer.salesperson` is null
    for 8,034 of 10,933 customers, so it is the common case, not an edge one.
    """

    _update = staticmethod(TestCustomerChangeGuard._update)

    @pytest.mark.asyncio
    async def test_it_follows_a_customer_that_has_one(self) -> None:
        _, order = await self._update(
            from_customer=2, to_customer=5, order_salesperson=4, customer_salesperson=9
        )

        assert order.salesperson == 9

    @pytest.mark.asyncio
    async def test_a_customer_without_one_leaves_the_order_alone(self) -> None:
        """Decision A. A move to an unassigned customer is not information about who owns the
        sale, and `sales_order.salesperson` is NOT NULL with an owner already in it."""
        _, order = await self._update(
            from_customer=2, to_customer=5, order_salesperson=4, customer_salesperson=None
        )

        assert order.salesperson == 4

    @pytest.mark.asyncio
    async def test_an_explicit_salesperson_beats_the_customer(self) -> None:
        """Mirrors create's precedence: `data.salesperson` first."""
        _, order = await self._update(
            from_customer=2,
            to_customer=5,
            order_salesperson=4,
            customer_salesperson=9,
            sent=SalesOrderUpdate(customer=5, salesperson=11),
        )

        assert order.salesperson == 11

    @pytest.mark.asyncio
    async def test_resending_the_same_customer_does_not_re_derive_it(self) -> None:
        """A `PUT` that has not moved the customer must not overwrite a deliberate assignment
        with that customer's default — the same reason repricing is gated on the flag."""
        _, order = await self._update(
            from_customer=2, to_customer=2, order_salesperson=4, customer_salesperson=9
        )

        assert order.salesperson == 4

    @pytest.mark.asyncio
    async def test_a_sent_null_is_ignored_rather_than_written(self) -> None:
        """The column is NOT NULL. The flat loop this came out of tested presence, so a sent
        `null` went straight in and failed the commit with error 1048."""
        _, order = await self._update(
            from_customer=2,
            to_customer=2,
            order_salesperson=4,
            sent=SalesOrderUpdate(customer=2, salesperson=None),
        )

        assert order.salesperson == 4


class TestPerLineTaxRateOverride:
    """#135 — the product's rate is the default, not the only possible value."""

    def test_line_creation_prefers_an_explicit_rate(self) -> None:
        source = inspect.getsource(sales_order_service.add_line)

        assert 'data.tax_rate if data.tax_rate is not None else product.tax_rate' in source

    def test_it_is_an_updatable_field(self) -> None:
        source = inspect.getsource(sales_order_service.update_line)

        assert "'tax_rate'" in source

    def test_the_schemas_bound_it_to_a_rate(self) -> None:
        """A rate, not a percentage — the column is `Numeric(5, 4)`."""
        for schema in (SalesOrderLineCreate, SalesOrderLineUpdate):
            metadata = schema.model_fields['tax_rate'].metadata
            assert [m.ge for m in metadata if hasattr(m, 'ge')] == [0]
            assert [m.le for m in metadata if hasattr(m, 'le')] == [1]


class TestTheUnitOfMeasurementIsProjectedOntoWhatThePointOfSaleReads:
    """#145 — a capture grid shows a unit per line, and neither shape it reads carried one.

    `product_lookup` alone would let a client cache it per product at scan time, but a resumed sale
    re-reads its lines through `attach_derived` and never re-runs the lookup — so the rows already
    captured, the ones a resume exists to show, are exactly the ones that would be blank. Both
    shapes are needed for the column to be reliable.
    """

    @staticmethod
    def _db(rows: list) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
        return db

    @staticmethod
    def _unit(code: str = 'H87', name: str = 'Pieza') -> SimpleNamespace:
        return SimpleNamespace(
            sat_unit_of_measurement_id=code, name=name, description=None, symbol=None
        )

    @pytest.mark.asyncio
    async def test_it_returns_the_full_record_keyed_by_product(self) -> None:
        """The same shape `unit_of_measurement` has on the product endpoints, not a bare string."""
        db = self._db([(1, self._unit()), (2, self._unit('ROL', 'Rollo'))])

        units = await sales_order_service.units_by_product(db, [1, 2])

        assert units[1].id == 'H87'
        assert units[1].name == 'Pieza'
        assert units[2].name == 'Rollo'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', [1, 5, 50])
    async def test_one_query_regardless_of_how_many_products(self, size: int) -> None:
        """A line-by-line read would be an N+1 on every order read and every lookup page."""
        db = self._db([])

        await sales_order_service.units_by_product(db, range(1, size + 1))

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_no_products_issues_no_query(self) -> None:
        db = self._db([])

        assert await sales_order_service.units_by_product(db, []) == {}
        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_a_product_with_no_catalog_row_maps_to_nothing(self) -> None:
        """An inner join drops it, and the field is `None` rather than a fabricated unit."""
        db = self._db([(1, self._unit())])

        units = await sales_order_service.units_by_product(db, [1, 2])

        assert units.get(2) is None

    def test_order_reads_attach_it_to_every_line(self) -> None:
        source = inspect.getsource(sales_order_service.attach_derived)

        assert 'units_by_product(' in source
        assert "line.__dict__['unit_of_measurement'] = units.get(line.product)" in source

    def test_the_lookup_reports_it_too(self) -> None:
        source = inspect.getsource(sales_order_service.lookup_products)

        assert "'unit_of_measurement': units.get(product.product_id)" in source


class TestThePhotoIsProjectedOntoWhatThePointOfSaleReads:
    """#157 — the capture grid reserves a thumbnail beside each line and neither shape carried one.

    Both shapes again, for the reason the unit needed both (#145): the lookup covers a scan, and a
    resumed sale reads its lines through `attach_derived` without re-running the lookup. Only
    `products` resolved a photo before this, and that is a privilege a cashier need not hold.
    """

    @staticmethod
    def _db(rows: list) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
        return db

    @pytest.mark.asyncio
    async def test_it_returns_a_resolved_url_keyed_by_product(self) -> None:
        """A URL, not the stored filename — the client renders it directly."""
        db = self._db([(1, 'a.png'), (2, 'b.png')])

        photos = await sales_order_service.photos_by_product(db, [1, 2])

        assert photos[1] == '/images/a.png'
        assert photos[2] == '/images/b.png'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', [1, 5, 50])
    async def test_one_query_regardless_of_how_many_products(self, size: int) -> None:
        db = self._db([])

        await sales_order_service.photos_by_product(db, range(1, size + 1))

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_no_products_issues_no_query(self) -> None:
        db = self._db([])

        assert await sales_order_service.photos_by_product(db, []) == {}
        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_a_product_with_no_photo_maps_to_none(self) -> None:
        """The column is nullable, and nothing is fabricated in its place."""
        db = self._db([(1, None)])

        assert await sales_order_service.photos_by_product(db, [1]) == {1: None}

    def test_order_reads_attach_it_to_every_line(self) -> None:
        source = inspect.getsource(sales_order_service.attach_derived)

        assert 'photos_by_product(' in source
        assert "line.__dict__['photo'] = photos.get(line.product)" in source

    def test_the_lookup_reports_it_too(self) -> None:
        source = inspect.getsource(sales_order_service.lookup_products)

        assert "'photo': image_service.image_url(product.photo)" in source


class TestTheFulfilmentIntent:
    """#170 — what the cashier said, recorded before the sale is confirmed.

    The point of sale asks how the goods reach the customer and the answer has three values. It was
    encoded into `ship_to`, which is one bit, so delivery and mixed were indistinguishable once the
    capturing session was gone.
    """

    def test_it_carries_the_third_state_the_address_cannot(self) -> None:
        assert [i.name for i in FulfillmentType] == ['PICKUP', 'DELIVERY', 'MIXED']

    def test_the_common_case_is_zero(self) -> None:
        """Pickup is the ordinary counter sale: 310,609 of 335,763 sales orders — 92.5% — never
        produced a delivery order at all.

        This is also the value migration 018 renumbered `delivery_order.fulfillment_type` to, where
        delivery had been 0 since 008 derived the column from the legacy `picked_up` boolean. One
        enum now serves both columns, so a value read from either means the same thing.
        """
        assert int(FulfillmentType.PICKUP) == 0
        assert int(FulfillmentType.DELIVERY) == 1
        assert int(FulfillmentType.MIXED) == 2

    def test_omitting_it_records_nothing_rather_than_defaulting(self) -> None:
        """`null` means "not stated". Defaulting to `DELIVERY` would make every sale that never
        answered indistinguishable from one that answered "delivered" — the bug again, moved."""
        assert SalesOrderCreate().fulfillment_intent is None

    def test_an_unknown_value_is_refused_by_the_schema(self) -> None:
        with pytest.raises(ValidationError):
            SalesOrderCreate(fulfillment_intent=3)

    def test_clearing_it_is_distinguishable_from_leaving_it_alone(self) -> None:
        """`exclude_unset` is what `update_order` reads, so an explicit `null` has to survive it —
        otherwise the field could be set and never taken back."""
        assert SalesOrderUpdate().model_dump(exclude_unset=True) == {}
        assert SalesOrderUpdate(fulfillment_intent=None).model_dump(exclude_unset=True) == {
            'fulfillment_intent': None
        }

    def test_the_creation_path_does_not_infer_it_from_the_address(self) -> None:
        """The one inference that must not happen. `ship_to` can say delivery or counter pickup and
        cannot say mixed, so deriving here would record a confident wrong answer for exactly the
        case the column exists to carry."""
        source = inspect.getsource(sales_order_service.create_order)

        assert 'fulfillment_intent=(' in source
        assert 'data.fulfillment_intent' in source
        assert '_is_facility_address' not in source

    def test_it_is_stored_as_an_int_like_priority(self) -> None:
        """The column is a plain SmallInteger; storing the enum member would leave the attribute an
        enum on the instance that wrote it and an int on the next one read back."""
        source = inspect.getsource(sales_order_service.update_order)

        assert "if 'fulfillment_intent' in changes:" in source
        assert 'None if value is None else int(value)' in source


class TestTheListSearchMatchesTheCustomersOwnName:
    """#172 — `search` matched only `sales_order.customer_name`, the per-document override.

    That column is null on every sale that did not set one, so a cashier typing a customer's name
    matched nothing and the list came back empty rather than erroring. The integration test proves
    the behaviour end to end, but only against SQLite; these assert the clause the MySQL deployment
    will actually run, which is the gap `tests/integration/conftest.py` documents.
    """

    @staticmethod
    def _db() -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one=lambda: 0),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
            ]
        )
        return db

    @staticmethod
    def _current() -> object:
        from app.core.deps import CurrentUser

        return CurrentUser(
            user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=7
        )

    @pytest.mark.asyncio
    async def test_a_name_term_reaches_the_customer_table(self) -> None:
        db = self._db()

        await sales_order_service.list_orders(db, current=self._current(), search='Cliente')

        sql = str(db.execute.await_args_list[0].args[0]).lower()
        assert 'customer.name' in sql

    @pytest.mark.asyncio
    async def test_the_override_stays_in_the_clause(self) -> None:
        """ORed in beside the customer's name, not swapped for it — a sale that overrides the name
        on the document is still findable by what the document says."""
        db = self._db()

        await sales_order_service.list_orders(db, current=self._current(), search='Cliente')

        sql = str(db.execute.await_args_list[0].args[0]).lower()
        assert 'sales_order.customer_name' in sql

    @pytest.mark.asyncio
    async def test_a_numeric_term_is_still_an_identifier(self) -> None:
        """Unchanged: digits mean an order id or folio, never a name fragment."""
        db = self._db()

        await sales_order_service.list_orders(db, current=self._current(), search='4212')

        sql = str(db.execute.await_args_list[0].args[0]).lower()
        assert 'customer.name' not in sql
        assert 'sales_order.serial' in sql

    @pytest.mark.asyncio
    async def test_the_count_and_the_page_carry_the_same_filter(self) -> None:
        """A filter applied to only one of the two makes `total` disagree with `items`."""
        db = self._db()

        await sales_order_service.list_orders(db, current=self._current(), search='Cliente')

        count_sql = str(db.execute.await_args_list[0].args[0]).lower()
        page_sql = str(db.execute.await_args_list[1].args[0]).lower()
        assert 'customer.name' in count_sql
        assert 'customer.name' in page_sql
