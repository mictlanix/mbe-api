"""Pure helpers of the two-step transfer (spec 029, T016).

Line figures for new and legacy lines, the per-line receive rules, and the sales-order items
(research R4, R9). The status branches are tested once, in `test_inventory_schemas.py`.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import inventory_documents as inv
from app.services import inventory_transfer_service as svc

D = Decimal


def _line(line_id: int, quantity: str, received: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        transfer_detail_id=line_id,
        product=line_id * 10,
        product_code=f'P{line_id}',
        quantity=D(quantity),
        quantity_received=None if received is None else D(received),
    )


def _order_line(product: int, quantity: str, stockable: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        product=product,
        quantity=D(quantity),
        stockable=stockable,
        code=f'P{product}',
        name=f'Producto {product}',
    )


# ── transfer_line_figures (R4, FR-044a) ───────────────────────────────────────


@pytest.mark.parametrize(
    'received, completed, in_transit, expected',
    [
        # A draft has received nothing and owes nothing.
        (None, False, False, (D(0), D(0))),
        # Dispatched: everything is outstanding.
        ('0', True, True, (D(0), D(10))),
        # Partly received, still in transit.
        ('8', True, True, (D(8), D(2))),
        # Received in full.
        ('10', True, False, (D(10), D(0))),
        # Remainder closed: 8 of 10 received, nothing outstanding.
        ('8', True, False, (D(8), D(0))),
        # Legacy one-step transfer: no recorded receipt reads as received in full.
        (None, True, False, (D(10), D(0))),
    ],
    ids=['draft', 'dispatched', 'partial', 'received', 'closed', 'legacy'],
)
def test_line_figures(received, completed, in_transit, expected):
    received = None if received is None else D(received)
    assert svc.transfer_line_figures(D(10), received, completed, in_transit) == expected


def test_a_cancelled_transfer_keeps_what_had_been_received():
    """Cancel clears `in_transit` and leaves `quantity_received` as it was (R3)."""
    assert svc.transfer_line_figures(D(10), D(8), True, False) == (D(8), D(0))


def test_a_cancelled_draft_received_nothing():
    assert svc.transfer_line_figures(D(10), None, False, False) == (D(0), D(0))


# ── apply_receipt (FR-042) ────────────────────────────────────────────────────


class TestApplyReceipt:
    def test_no_request_receives_everything_outstanding(self):
        lines = [_line(1, '10', '0'), _line(2, '5', '5'), _line(3, '4', '1')]
        received = svc.apply_receipt(lines, None)
        assert [(line.transfer_detail_id, qty) for line, qty in received] == [(1, D(10)), (3, D(3))]

    def test_a_partial_request_receives_only_what_it_names(self):
        lines = [_line(1, '10', '0'), _line(2, '5', '0')]
        received = svc.apply_receipt(lines, [(1, D(8))])
        assert [(line.transfer_detail_id, qty) for line, qty in received] == [(1, D(8))]

    def test_the_whole_outstanding_quantity_is_accepted(self):
        received = svc.apply_receipt([_line(1, '10', '8')], [(1, D(2))])
        assert received[0][1] == D(2)

    def test_a_line_of_another_transfer_is_404(self):
        with pytest.raises(HTTPException) as exc:
            svc.apply_receipt([_line(1, '10', '0')], [(99, D(1))])
        assert exc.value.status_code == 404

    def test_the_same_line_twice_is_422(self):
        with pytest.raises(HTTPException) as exc:
            svc.apply_receipt([_line(1, '10', '0')], [(1, D(1)), (1, D(1))])
        assert exc.value.status_code == 422
        assert exc.value.code == 'inventory_transfer.line_repeated'
        assert exc.value.params == {'line_id': 1}

    def test_more_than_outstanding_is_409(self):
        with pytest.raises(HTTPException) as exc:
            svc.apply_receipt([_line(1, '10', '8')], [(1, D(3))])
        assert exc.value.status_code == 409
        assert 'P1' in exc.value.detail
        assert exc.value.code == 'inventory_transfer.over_received'
        assert exc.value.params == {'product_code': 'P1', 'received': '3', 'outstanding': '2'}

    def test_a_line_with_nothing_outstanding_is_409(self):
        with pytest.raises(HTTPException) as exc:
            svc.apply_receipt([_line(1, '10', '0'), _line(2, '5', '5')], [(2, D(1))])
        assert exc.value.status_code == 409


# ── sales_order_additions (FR-046, R9) ────────────────────────────────────────


class TestSalesOrderAdditions:
    def test_sums_repeated_products(self):
        lines = [_order_line(7, '2'), _order_line(8, '1'), _order_line(7, '3')]
        additions = svc.sales_order_additions(lines, set())
        assert {product: row.quantity for product, row in additions.items()} == {
            7: D(5),
            8: D(1),
        }
        assert additions[7].code == 'P7'

    def test_skips_products_already_on_the_transfer(self):
        lines = [_order_line(7, '2'), _order_line(8, '1')]
        assert list(svc.sales_order_additions(lines, {7})) == [8]

    def test_drops_non_stockable_products(self):
        lines = [_order_line(7, '2'), _order_line(9, '1', stockable=False)]
        assert list(svc.sales_order_additions(lines, set())) == [7]

    def test_repeating_with_every_product_present_adds_nothing(self):
        lines = [_order_line(7, '2'), _order_line(8, '1')]
        assert svc.sales_order_additions(lines, {7, 8}) == {}


# ── transfer_status_clause (R4, R5) ───────────────────────────────────────────


def test_a_status_transfers_do_not_have_is_refused_with_the_allowed_ones():
    with pytest.raises(HTTPException) as exc:
        svc.transfer_status_clause(inv.TRANSFER, 'completed')
    assert exc.value.status_code == 422
    assert exc.value.code == 'status_filter.unsupported'
    assert exc.value.params == {'allowed': ['draft', 'in_transit', 'received', 'cancelled']}
