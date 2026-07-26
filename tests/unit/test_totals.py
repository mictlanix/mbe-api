"""Money computation shared by orders, quotes, refunds and credit notes.

One helper computes every figure so the document types cannot round differently (research R5).
The exactness these tests pin is what SC-004 depends on: an order's balance must equal its total
less every non-cancelled application, and reversing an application must restore it exactly.
"""

from decimal import Decimal

from app.services.totals import Line, document_totals, line_amounts, remaining


def _d(v: str) -> Decimal:
    return Decimal(v)


class TestLineAmounts:
    def test_tax_excluded_adds_tax_on_top(self) -> None:
        subtotal, tax = line_amounts(
            quantity=_d('2'),
            price=_d('100'),
            discount_rate=_d('0'),
            tax_rate=_d('0.16'),
            tax_included=False,
        )

        assert subtotal == _d('200')
        assert tax == _d('32')

    def test_tax_included_back_derives_subtotal(self) -> None:
        """A tax-included price already contains the tax; the subtotal is derived, not added to."""
        subtotal, tax = line_amounts(
            quantity=_d('1'),
            price=_d('116'),
            discount_rate=_d('0'),
            tax_rate=_d('0.16'),
            tax_included=True,
        )

        assert subtotal + tax == _d('116')
        assert subtotal.quantize(_d('0.01')) == _d('100.00')
        assert tax.quantize(_d('0.01')) == _d('16.00')

    def test_discount_applies_before_tax(self) -> None:
        subtotal, tax = line_amounts(
            quantity=_d('1'),
            price=_d('100'),
            discount_rate=_d('0.10'),
            tax_rate=_d('0.16'),
            tax_included=False,
        )

        assert subtotal == _d('90')
        assert tax == _d('14.4')

    def test_zero_tax_rate_yields_no_tax(self) -> None:
        subtotal, tax = line_amounts(
            quantity=_d('3'),
            price=_d('50'),
            discount_rate=_d('0'),
            tax_rate=_d('0'),
            tax_included=True,
        )

        assert subtotal == _d('150')
        assert tax == _d('0')

    def test_full_discount_yields_zero(self) -> None:
        subtotal, tax = line_amounts(
            quantity=_d('5'),
            price=_d('80'),
            discount_rate=_d('1'),
            tax_rate=_d('0.16'),
            tax_included=False,
        )

        assert subtotal == _d('0')
        assert tax == _d('0')

    def test_fractional_quantity_supported(self) -> None:
        subtotal, _ = line_amounts(
            quantity=_d('2.5'),
            price=_d('4'),
            discount_rate=_d('0'),
            tax_rate=_d('0'),
            tax_included=False,
        )

        assert subtotal == _d('10')


class TestDocumentTotals:
    def test_sums_lines(self) -> None:
        totals = document_totals(
            [
                Line(_d('2'), _d('100'), _d('0'), _d('0.16'), False),
                Line(_d('1'), _d('50'), _d('0'), _d('0.16'), False),
            ]
        )

        assert totals.subtotal == _d('250.00')
        assert totals.tax_total == _d('40.00')
        assert totals.total == _d('290.00')

    def test_mixed_tax_inclusion_in_one_document(self) -> None:
        """`tax_included` is per product, so both conventions occur in the same document."""
        totals = document_totals(
            [
                Line(_d('1'), _d('116'), _d('0'), _d('0.16'), True),
                Line(_d('1'), _d('100'), _d('0'), _d('0.16'), False),
            ]
        )

        assert totals.subtotal == _d('200.00')
        assert totals.tax_total == _d('32.00')
        assert totals.total == _d('232.00')

    def test_quantizes_once_at_document_level_not_per_line(self) -> None:
        """Rounding each line then summing drifts; three thirds must come back to exactly 10.00."""
        third = _d('3.333333')
        totals = document_totals([Line(_d('1'), third, _d('0'), _d('0'), False)] * 3)

        assert totals.subtotal == _d('10.00')
        assert totals.total == _d('10.00')

    def test_empty_document_is_zero(self) -> None:
        totals = document_totals([])

        assert totals.subtotal == _d('0.00')
        assert totals.tax_total == _d('0.00')
        assert totals.total == _d('0.00')

    def test_total_is_subtotal_plus_tax(self) -> None:
        totals = document_totals([Line(_d('7'), _d('13.37'), _d('0.05'), _d('0.16'), False)])

        assert totals.total == totals.subtotal + totals.tax_total


class TestRemaining:
    def test_balance_is_total_less_applications(self) -> None:
        assert remaining(_d('290.00'), [_d('100.00'), _d('90.00')]) == _d('100.00')

    def test_no_applications_leaves_full_total(self) -> None:
        assert remaining(_d('290.00'), []) == _d('290.00')

    def test_fully_covered_is_exactly_zero(self) -> None:
        assert remaining(_d('290.00'), [_d('290.00')]) == _d('0.00')

    def test_reversal_restores_balance_exactly(self) -> None:
        """SC-004: reversing an application must restore the balance exactly, not approximately."""
        total = _d('123.45')
        applied = [_d('23.45'), _d('100.00')]

        assert remaining(total, applied) == _d('0.00')
        assert remaining(total, applied[:1]) == _d('100.00')

    def test_never_reports_negative_balance(self) -> None:
        """Change given back is recorded separately and must not push a balance below zero."""
        assert remaining(_d('100.00'), [_d('150.00')]) == _d('0.00')
