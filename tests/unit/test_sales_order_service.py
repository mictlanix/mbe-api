"""The sales order state machine and the rules that guard it.

The lifecycle rules pinned here are the ones clarified into the spec, and they are the reason the
order is the spine of the feature: pay requires completed and uncancelled (FR-042), cancel requires
unpaid with no live applications (FR-019, FR-019b), refund requires paid (FR-060). Cancel and
refund must never both be available for the same order — SC-010.
"""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.enums import PaymentTerms
from app.services.sales_order_service import (
    assert_can_cancel,
    assert_margin_in_range,
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


class TestAssertMarginInRange:
    """`low_profit`/`high_profit` are profit *rates*, not price bounds.

    Every row in the production data has both between 0 and 1, so comparing a price against them
    directly refused any price above 1.00 — 98.8% of the catalogue. The check is on the derived
    margin `(price - cost) / price`.
    """

    def test_healthy_margin_passes(self) -> None:
        # 100 selling, 60 cost -> 40% margin, inside [0, 1]
        assert_margin_in_range(
            Decimal('100'), Decimal('60'),
            low_rate=Decimal('0'), high_rate=Decimal('1'), enabled=True, exempt=False,
        )

    def test_a_realistic_catalogue_price_is_not_refused(self) -> None:
        """The regression: a 23.00 price against rates 0.0-1.0 must not fail."""
        assert_margin_in_range(
            Decimal('23'), Decimal('10'),
            low_rate=Decimal('0'), high_rate=Decimal('1'), enabled=True, exempt=False,
        )

    def test_selling_below_cost_is_refused(self) -> None:
        """A negative margin falls below a floor of zero — the case FR-014 exists for."""
        with pytest.raises(HTTPException) as exc:
            assert_margin_in_range(
                Decimal('50'), Decimal('80'),
                low_rate=Decimal('0'), high_rate=Decimal('1'), enabled=True, exempt=False,
            )

        assert exc.value.status_code == 422

    def test_margin_below_the_floor_is_refused(self) -> None:
        # 100 selling, 95 cost -> 5% margin, below a 10% floor
        with pytest.raises(HTTPException):
            assert_margin_in_range(
                Decimal('100'), Decimal('95'),
                low_rate=Decimal('0.10'), high_rate=Decimal('1'), enabled=True, exempt=False,
            )

    def test_margin_above_the_ceiling_is_refused(self) -> None:
        # 100 selling, 10 cost -> 90% margin, above a 50% ceiling
        with pytest.raises(HTTPException):
            assert_margin_in_range(
                Decimal('100'), Decimal('10'),
                low_rate=Decimal('0'), high_rate=Decimal('0.50'), enabled=True, exempt=False,
            )

    def test_zero_cost_yields_a_full_margin_and_passes(self) -> None:
        """A product with no cost row recorded has margin 1.0 — the inclusive ceiling."""
        assert_margin_in_range(
            Decimal('100'), Decimal('0'),
            low_rate=Decimal('0'), high_rate=Decimal('1'), enabled=True, exempt=False,
        )

    def test_zero_price_is_not_judged_here(self) -> None:
        """Confirmation refuses a zero price outright (FR-017); dividing by it is meaningless."""
        assert_margin_in_range(
            Decimal('0'), Decimal('10'),
            low_rate=Decimal('0.5'), high_rate=Decimal('0.9'), enabled=True, exempt=False,
        )

    def test_privilege_102_bypasses_the_check(self) -> None:
        assert_margin_in_range(
            Decimal('50'), Decimal('80'),
            low_rate=Decimal('0'), high_rate=Decimal('1'), enabled=True, exempt=True,
        )

    def test_disabled_setting_bypasses_the_check(self) -> None:
        assert_margin_in_range(
            Decimal('50'), Decimal('80'),
            low_rate=Decimal('0'), high_rate=Decimal('1'), enabled=False, exempt=False,
        )

    def test_bounds_are_inclusive(self) -> None:
        assert_margin_in_range(
            Decimal('100'), Decimal('50'),
            low_rate=Decimal('0.5'), high_rate=Decimal('0.5'), enabled=True, exempt=False,
        )


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

        shortfalls = stock_shortfalls(lines, on_hand={(1, 5): Decimal('6')}, stocked={1})

        assert len(shortfalls) == 1
        assert '8' in shortfalls[0] and '6' in shortfalls[0]

    def test_sufficient_stock_yields_nothing(self) -> None:
        lines = [_line(1, 5, '4')]

        assert stock_shortfalls(lines, on_hand={(1, 5): Decimal('10')}, stocked={1}) == []

    def test_missing_warehouse_on_a_stocked_line_is_a_shortfall(self) -> None:
        lines = [_line(1, None, '1')]

        shortfalls = stock_shortfalls(lines, on_hand={}, stocked={1})

        assert len(shortfalls) == 1
        assert 'warehouse' in shortfalls[0].lower()

    def test_non_stocked_products_are_ignored(self) -> None:
        """A product that does not require stock never blocks confirmation."""
        lines = [_line(1, None, '99')]

        assert stock_shortfalls(lines, on_hand={}, stocked=set()) == []

    def test_separate_warehouses_are_checked_separately(self) -> None:
        lines = [_line(1, 5, '4'), _line(1, 6, '4')]

        shortfalls = stock_shortfalls(
            lines, on_hand={(1, 5): Decimal('10'), (1, 6): Decimal('1')}, stocked={1}
        )

        assert len(shortfalls) == 1


class TestAssertCanCancel:
    def test_draft_can_be_cancelled(self) -> None:
        assert_can_cancel(
            SimpleNamespace(cancelled=False, paid=False), live_applications=[]
        )

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
