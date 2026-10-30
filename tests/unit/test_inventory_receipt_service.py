"""Pure helpers of the receipt's purchase-order view (spec 029, US4, research R7 and R8).

The over-receipt warnings, the remaining quantity per order line, and the reasons a
complementary receipt is refused.
"""

from decimal import Decimal
from types import SimpleNamespace

from app.services import inventory_receipt_service as svc


def _line(order_line: int | None, quantity: str, code: str = 'W-1') -> SimpleNamespace:
    return SimpleNamespace(
        purchase_order_detail=order_line, quantity=Decimal(quantity), product_code=code
    )


def _order_line(line_id: int, quantity: str) -> SimpleNamespace:
    return SimpleNamespace(purchase_order_detail_id=line_id, quantity=Decimal(quantity))


def _order(*, completed: bool = True, cancelled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(completed=completed, cancelled=cancelled)


# ── over_receipt_warnings (R7, FR-022) ────────────────────────────────────────


class TestOverReceiptWarnings:
    def test_within_the_ordered_quantity_there_is_no_warning(self):
        lines = [_line(1, '4')]
        assert svc.over_receipt_warnings(lines, {1: Decimal(10)}, {1: Decimal(6)}) == []

    def test_over_the_ordered_quantity_names_the_code_and_the_excess(self):
        lines = [_line(1, '5', code='W-1')]
        warnings = svc.over_receipt_warnings(lines, {1: Decimal(10)}, {1: Decimal(6)})
        assert len(warnings) == 1
        assert 'W-1' in warnings[0]
        assert 'by 1' in warnings[0]

    def test_sums_this_receipts_lines_per_order_line_into_one_message(self):
        """A legacy receipt may link one order line twice."""
        lines = [_line(1, '3'), _line(1, '4')]
        warnings = svc.over_receipt_warnings(lines, {1: Decimal(5)}, {})
        assert len(warnings) == 1
        assert 'by 2' in warnings[0]

    def test_compares_against_the_order_line_not_the_stored_quantity_ordered(self):
        """A legacy complementary line stored the remaining 4 as ordered; the order said 10."""
        line = _line(1, '4')
        line.quantity_ordered = Decimal(4)
        assert svc.over_receipt_warnings([line], {1: Decimal(10)}, {1: Decimal(6)}) == []

    def test_one_message_per_order_line_over(self):
        lines = [_line(1, '11', code='A'), _line(2, '1', code='B'), _line(3, '9', code='C')]
        ordered = {1: Decimal(10), 2: Decimal(10), 3: Decimal(8)}
        warnings = svc.over_receipt_warnings(lines, ordered, {})
        assert len(warnings) == 2
        assert 'A' in warnings[0] and 'C' in warnings[1]

    def test_unlinked_lines_give_no_warning(self):
        assert svc.over_receipt_warnings([_line(None, '99')], {}, {}) == []


# ── remaining_lines (R8) ──────────────────────────────────────────────────────


class TestRemainingLines:
    def test_remaining_is_ordered_less_received(self):
        first, second = _order_line(1, '10'), _order_line(2, '5')
        assert svc.remaining_lines([first, second], {1: Decimal(6)}) == [
            (first, Decimal(4)),
            (second, Decimal(5)),
        ]

    def test_skips_lines_with_nothing_remaining(self):
        full, over, short = _order_line(1, '10'), _order_line(2, '5'), _order_line(3, '2')
        received = {1: Decimal(10), 2: Decimal(7), 3: Decimal('0.5')}
        assert svc.remaining_lines([full, over, short], received) == [(short, Decimal('1.5'))]

    def test_no_order_lines_leaves_nothing(self):
        assert svc.remaining_lines([], {}) == []


# ── complementary_refusal (R8, FR-023) ────────────────────────────────────────


class TestComplementaryRefusal:
    def test_a_completed_order_with_something_remaining_is_allowed(self):
        assert svc.complementary_refusal(_order(), [(_order_line(1, '10'), Decimal(4))]) is None

    def test_no_purchase_order(self):
        detail, reason = svc.complementary_refusal(None, [])
        assert 'no purchase order' in detail
        assert reason == 'no_purchase_order'

    def test_an_order_not_completed(self):
        detail, reason = svc.complementary_refusal(
            _order(completed=False), [(object(), Decimal(1))]
        )
        assert 'not completed' in detail
        assert reason == 'purchase_order_not_completed'

    def test_a_cancelled_order(self):
        detail, reason = svc.complementary_refusal(
            _order(cancelled=True), [(object(), Decimal(1))]
        )
        assert 'cancelled' in detail
        assert reason == 'purchase_order_cancelled'

    def test_a_fully_received_order(self):
        detail, reason = svc.complementary_refusal(_order(), [])
        assert 'fully received' in detail
        assert reason == 'purchase_order_fully_received'
