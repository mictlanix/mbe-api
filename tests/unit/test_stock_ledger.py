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
from app.models.inventory import LotSerialTracking
from app.services.stock_ledger import on_hand, post_movement


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
