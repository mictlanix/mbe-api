"""Money computation for every sales document.

Orders, quotes, refunds and credit notes all compute the same figures, so they compute them here
rather than three times over. Divergent rounding between two of them would break SC-004, which
requires an order's balance to equal its total less every non-cancelled application *exactly*.

Two rules the callers must not re-implement:

- `tax_included` is a per-product flag, so both conventions appear in one document. A tax-included
  price already contains its tax and the subtotal is back-derived; a tax-excluded price has tax
  added on top.
- Quantization happens **once**, when a document's lines are summed. Rounding each line and then
  adding produces cent-level drift.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal('0.01')


@dataclass(frozen=True)
class Line:
    """The five values any document line needs to price itself."""

    quantity: Decimal
    price: Decimal
    discount_rate: Decimal
    tax_rate: Decimal
    tax_included: bool


@dataclass(frozen=True)
class DocumentTotals:
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


def line_amounts(
    *,
    quantity: Decimal,
    price: Decimal,
    discount_rate: Decimal,
    tax_rate: Decimal,
    tax_included: bool,
) -> tuple[Decimal, Decimal]:
    """Return this line's `(subtotal, tax)`, unrounded.

    Unrounded on purpose — the caller sums many of these and quantizes the sum.
    """
    gross = quantity * price * (Decimal(1) - discount_rate)

    if tax_included:
        subtotal = gross / (Decimal(1) + tax_rate)
        return subtotal, gross - subtotal

    return gross, gross * tax_rate


def document_totals(lines: Iterable[Line]) -> DocumentTotals:
    """Sum every line, then round — never the other way round."""
    subtotal = Decimal(0)
    tax_total = Decimal(0)

    for line in lines:
        line_subtotal, line_tax = line_amounts(
            quantity=line.quantity,
            price=line.price,
            discount_rate=line.discount_rate,
            tax_rate=line.tax_rate,
            tax_included=line.tax_included,
        )
        subtotal += line_subtotal
        tax_total += line_tax

    subtotal = subtotal.quantize(CENTS, rounding=ROUND_HALF_UP)
    tax_total = tax_total.quantize(CENTS, rounding=ROUND_HALF_UP)
    return DocumentTotals(subtotal=subtotal, tax_total=tax_total, total=subtotal + tax_total)


def remaining(total: Decimal, applied: Iterable[Decimal]) -> Decimal:
    """What is still owed on `total` after `applied` amounts.

    Floored at zero: change handed back is recorded on the application as `amount_change` and must
    never drive a balance negative (FR-042a).
    """
    balance = total - sum(applied, Decimal(0))
    if balance < 0:
        balance = Decimal(0)
    return balance.quantize(CENTS, rounding=ROUND_HALF_UP)
