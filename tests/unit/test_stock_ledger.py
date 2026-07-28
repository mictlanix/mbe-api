"""The inventory ledger: append-only, sign-carrying, and the only source of stock truth.

There is no stock-balance table — on-hand is the sum of `lot_serial_tracking.quantity`, positive
inbound and negative outbound (research R4). Nothing here ever updates or deletes a row: a
cancellation writes a *compensating* entry so the sale and its reversal both remain visible
(FR-019a), which is what SC-003 checks.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.enums import TransactionType
from app.models.inventory import LotSerialRqmt, LotSerialTracking
from app.services.stock_ledger import (
    available,
    on_hand,
    post_movement,
    release_reservation,
    release_reservations,
    reserve,
    reserved,
)


def _db() -> AsyncMock:
    db = AsyncMock()
    db.add = lambda obj: db.added.append(obj)
    db.added = []
    return db


class TestPostMovement:
    def test_sale_posts_a_negative_entry(self) -> None:
        db = _db()

        post_movement(
            db,
            source=TransactionType.SALES_ORDER,
            reference=42,
            product=1,
            warehouse=2,
            quantity=Decimal('3'),
            outbound=True,
        )

        entry = db.added[0]
        assert isinstance(entry, LotSerialTracking)
        assert entry.quantity == Decimal('-3')
        assert entry.source == int(TransactionType.SALES_ORDER)
        assert entry.reference == 42

    def test_refund_posts_a_positive_entry(self) -> None:
        db = _db()

        post_movement(
            db,
            source=TransactionType.CUSTOMER_REFUND,
            reference=7,
            product=1,
            warehouse=2,
            quantity=Decimal('3'),
            outbound=False,
        )

        assert db.added[0].quantity == Decimal('3')
        assert db.added[0].source == int(TransactionType.CUSTOMER_REFUND)

    def test_cancellation_compensates_a_sale_with_a_positive_entry(self) -> None:
        """Cancelling restores stock by adding a row, never by touching the original."""
        db = _db()

        post_movement(
            db,
            source=TransactionType.SALES_ORDER,
            reference=42,
            product=1,
            warehouse=2,
            quantity=Decimal('3'),
            outbound=False,
        )

        assert db.added[0].quantity == Decimal('3')
        assert db.added[0].reference == 42

    def test_records_product_warehouse_and_date(self) -> None:
        db = _db()

        post_movement(
            db,
            source=TransactionType.SALES_ORDER,
            reference=1,
            product=11,
            warehouse=22,
            quantity=Decimal('1'),
            outbound=True,
        )

        entry = db.added[0]
        assert entry.product == 11
        assert entry.warehouse == 22
        assert entry.date is not None

    def test_lot_and_serial_left_unset(self) -> None:
        """Lot/serial capture is out of scope for this feature."""
        db = _db()

        post_movement(
            db,
            source=TransactionType.SALES_ORDER,
            reference=1,
            product=1,
            warehouse=1,
            quantity=Decimal('1'),
            outbound=True,
        )

        entry = db.added[0]
        assert entry.lot_number is None
        assert entry.serial_number is None
        assert entry.expiration_date is None

    def test_negative_quantity_is_refused(self) -> None:
        """Direction is expressed by `outbound`, never by the caller pre-signing the quantity."""
        db = _db()

        with pytest.raises(ValueError):
            post_movement(
                db,
                source=TransactionType.SALES_ORDER,
                reference=1,
                product=1,
                warehouse=1,
                quantity=Decimal('-1'),
                outbound=True,
            )


class TestOnHand:
    @pytest.mark.asyncio
    async def test_sums_the_ledger(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: Decimal('12'))
        )

        assert await on_hand(db, product=1, warehouse=2) == Decimal('12')

    @pytest.mark.asyncio
    async def test_no_movements_is_zero_not_none(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        assert await on_hand(db, product=1, warehouse=2) == Decimal('0')

    @pytest.mark.asyncio
    async def test_filters_by_product_and_warehouse(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: Decimal('0'))
        )

        await on_hand(db, product=5, warehouse=9)

        sql = str(db.execute.await_args.args[0]).lower()
        assert 'product' in sql
        assert 'warehouse' in sql
        assert 'sum' in sql


class TestInTransitWarehouseStartupCheck:
    """The check that stops delivery movements being filed against a warehouse that isn't there.

    Left at its `0` default the inbound half of every departure would name warehouse 0 — stock
    silently misplaced rather than an error. The id is created by migration 008 and cannot be
    defaulted, so it is verified at startup instead (T015b, research R3).
    """

    @pytest.mark.asyncio
    async def test_refuses_when_unset(self, monkeypatch):
        from app.core.config import settings as app_settings
        from app.main import verify_in_transit_warehouse

        monkeypatch.setattr(app_settings, 'in_transit_warehouse_id', 0)
        with pytest.raises(RuntimeError, match='IN_TRANSIT_WAREHOUSE_ID is not set'):
            await verify_in_transit_warehouse()

    @pytest.mark.asyncio
    async def test_refuses_when_warehouse_missing(self, monkeypatch):
        import app.db.session as session_module
        from app.core.config import settings as app_settings
        from app.main import verify_in_transit_warehouse

        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        class _Factory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(app_settings, 'in_transit_warehouse_id', 999_999_999)
        monkeypatch.setattr(session_module, 'AsyncSessionLocal', _Factory())

        with pytest.raises(RuntimeError, match='names no warehouse'):
            await verify_in_transit_warehouse()

    @pytest.mark.asyncio
    async def test_accepts_a_warehouse_that_exists(self, monkeypatch):
        import app.db.session as session_module
        from app.core.config import settings as app_settings
        from app.main import verify_in_transit_warehouse

        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 20))

        class _Factory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(app_settings, 'in_transit_warehouse_id', 20)
        monkeypatch.setattr(session_module, 'AsyncSessionLocal', _Factory())

        await verify_in_transit_warehouse()


class TestReservations:
    """Reservations: what a confirmed sales order claims but has not yet taken off the shelf.

    Confirmation no longer decrements on-hand (FR-055), so the stock check has to compare against
    on-hand *minus* reservations or one physical unit would satisfy unlimited orders (FR-055a).
    """

    def test_reserve_stages_a_requirement_row(self) -> None:
        db = _db()

        reserve(db, sales_order=42, product=1, warehouse=2, quantity=Decimal('3'))

        (entry,) = db.added
        assert isinstance(entry, LotSerialRqmt)
        assert entry.source == int(TransactionType.SALES_ORDER_RESERVATION)
        assert entry.reference == 42
        assert entry.product == 1
        assert entry.warehouse == 2
        assert entry.quantity == Decimal('3')

    def test_reserve_is_namespaced_away_from_the_legacy_writer(self) -> None:
        """The legacy app wrote reservations under SALES_ORDER; ours must not collide (A2)."""
        db = _db()

        reserve(db, sales_order=42, product=1, warehouse=2, quantity=Decimal('3'))

        (entry,) = db.added
        assert entry.source != int(TransactionType.SALES_ORDER)

    def test_reserve_refuses_a_negative_quantity(self) -> None:
        db = _db()

        with pytest.raises(ValueError):
            reserve(db, sales_order=42, product=1, warehouse=2, quantity=Decimal('-1'))

    @pytest.mark.asyncio
    async def test_reserved_sums_only_our_own_rows(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: Decimal('4'))
        )

        assert await reserved(db, product=1, warehouse=2) == Decimal('4')

        statement = str(db.execute.await_args.args[0])
        assert 'lot_serial_rqmt' in statement

    @pytest.mark.asyncio
    async def test_reserved_is_zero_when_nothing_is_held(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        assert await reserved(db, product=1, warehouse=2) == Decimal(0)

    @pytest.mark.asyncio
    async def test_available_is_on_hand_minus_reservations(self, monkeypatch) -> None:
        async def fake_on_hand(db, *, product, warehouse):
            return Decimal('10')

        async def fake_reserved(db, *, product, warehouse):
            return Decimal('4')

        monkeypatch.setattr('app.services.stock_ledger.on_hand', fake_on_hand)
        monkeypatch.setattr('app.services.stock_ledger.reserved', fake_reserved)

        assert await available(AsyncMock(), product=1, warehouse=2) == Decimal('6')

    @pytest.mark.asyncio
    async def test_a_reservation_does_not_move_on_hand(self, monkeypatch) -> None:
        """The whole point: goods stay on the shelf and on the books until they depart."""
        db = _db()

        reserve(db, sales_order=42, product=1, warehouse=2, quantity=Decimal('3'))

        assert not any(isinstance(e, LotSerialTracking) for e in db.added)

    @pytest.mark.asyncio
    async def test_available_can_go_to_zero_while_on_hand_is_untouched(self, monkeypatch) -> None:
        async def fake_on_hand(db, *, product, warehouse):
            return Decimal('1')

        async def fake_reserved(db, *, product, warehouse):
            return Decimal('1')

        monkeypatch.setattr('app.services.stock_ledger.on_hand', fake_on_hand)
        monkeypatch.setattr('app.services.stock_ledger.reserved', fake_reserved)

        assert await available(AsyncMock(), product=1, warehouse=2) == Decimal(0)

    @pytest.mark.asyncio
    async def test_release_deletes_the_orders_reservations(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock()

        await release_reservations(db, sales_order=42)

        statement = str(db.execute.await_args.args[0])
        assert statement.startswith('DELETE FROM lot_serial_rqmt')


class TestPartialRelease:
    """Releasing part of an order must leave the rest of it holding its claim.

    A sales order reserves one row per line. Releasing by reference alone — which is what
    departure originally did — hands back the lines that never left, and those become sellable
    a second time. This is the oversell FR-055a exists to prevent, arriving by the back door.
    """

    def _rows(self, *rows):
        deleted = []

        class _Result:
            def __init__(self, values):
                self._values = values

            def scalars(self):
                return SimpleNamespace(all=lambda: self._values)

        class _Db:
            async def execute(self, _statement):
                return _Result(list(rows))

            async def delete(self, obj):
                deleted.append(obj)

        return _Db(), deleted

    def _row(self, product: int, warehouse: int, quantity: str, rid: int = 1):
        return SimpleNamespace(
            lot_serial_rqmt_id=rid,
            source=11,
            reference=42,
            product=product,
            warehouse=warehouse,
            quantity=Decimal(quantity),
        )

    @pytest.mark.asyncio
    async def test_releases_exactly_what_departed(self) -> None:
        row = self._row(1, 2, '10')
        db, deleted = self._rows(row)

        released = await release_reservation(
            db, sales_order=42, product=1, warehouse=2, quantity=Decimal('4')
        )

        assert released == Decimal('4')
        assert row.quantity == Decimal('6')
        assert deleted == []

    @pytest.mark.asyncio
    async def test_an_exhausted_row_is_deleted(self) -> None:
        row = self._row(1, 2, '4')
        db, deleted = self._rows(row)

        await release_reservation(
            db, sales_order=42, product=1, warehouse=2, quantity=Decimal('4')
        )

        assert row.quantity == Decimal('0')
        assert deleted == [row]

    @pytest.mark.asyncio
    async def test_consumes_across_rows_in_order(self) -> None:
        first, second = self._row(1, 2, '3', rid=1), self._row(1, 2, '5', rid=2)
        db, deleted = self._rows(first, second)

        released = await release_reservation(
            db, sales_order=42, product=1, warehouse=2, quantity=Decimal('6')
        )

        assert released == Decimal('6')
        assert first.quantity == Decimal('0')
        assert second.quantity == Decimal('2')
        assert deleted == [first]

    @pytest.mark.asyncio
    async def test_reports_when_the_order_held_less_than_asked_for(self) -> None:
        db, _ = self._rows(self._row(1, 2, '2'))

        released = await release_reservation(
            db, sales_order=42, product=1, warehouse=2, quantity=Decimal('5')
        )

        assert released == Decimal('2')

    @pytest.mark.asyncio
    async def test_releasing_nothing_touches_nothing(self) -> None:
        row = self._row(1, 2, '10')
        db, deleted = self._rows(row)

        assert await release_reservation(
            db, sales_order=42, product=1, warehouse=2, quantity=Decimal('0')
        ) == Decimal(0)
        assert row.quantity == Decimal('10')
        assert deleted == []
