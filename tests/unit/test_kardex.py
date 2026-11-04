"""Pure helpers of the kardex (spec 030, US4, T016).

The running balance, the range bounds and the resolution of ledger entries to their documents
(research R6).
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.enums import TransactionType as T
from app.services import kardex_service as svc
from app.services.kardex_service import Document

D = Decimal
WHEN = datetime(2026, 10, 1, 10, 0)


def _entry(
    entry_id: int, source: int, reference: int, quantity: str = '1', product: int = 7
) -> SimpleNamespace:
    return SimpleNamespace(
        lot_serial_tracking_id=entry_id,
        date=WHEN,
        source=int(source),
        reference=reference,
        product=product,
        quantity=D(quantity),
    )


# ── kardex_rows ───────────────────────────────────────────────────────────────


class TestKardexRows:
    def test_running_balance_totals_and_closing(self):
        entries = [_entry(1, T.INVENTORY_RECEIPT, 5, '10'), _entry(2, T.SALES_ORDER, 9, '-3')]

        rows, total_in, total_out, closing = svc.kardex_rows(D('4'), entries)

        assert [(r.quantity_in, r.quantity_out, r.balance) for r in rows] == [
            (D('10'), D('0'), D('14')),
            (D('0'), D('3'), D('11')),
        ]
        assert (total_in, total_out, closing) == (D('10'), D('3'), D('11'))

    def test_each_row_carries_its_entry(self):
        rows, *_ = svc.kardex_rows(D('0'), [_entry(1, T.INVENTORY_ISSUE, 5, '-2')])

        row = rows[0]
        assert (row.lot_serial_tracking_id, row.date, row.source, row.reference) == (1, WHEN, 3, 5)
        assert row.source_name == 'inventory_issue'
        assert row.document is None

    def test_an_unknown_type_is_named_as_such(self):
        rows, *_ = svc.kardex_rows(D('0'), [_entry(1, 99, 5)])
        assert rows[0].source_name == 'unknown'

    def test_documents_are_attached_by_entry_id(self):
        document = svc.KardexDocument(type=4, id=5, facility=1, serial=3)
        rows, *_ = svc.kardex_rows(D('0'), [_entry(1, T.INVENTORY_RECEIPT, 5)], {1: document})
        assert rows[0].document == document

    def test_no_entries_closes_at_the_opening(self):
        rows, total_in, total_out, closing = svc.kardex_rows(D('-2'), [])
        assert (rows, total_in, total_out, closing) == ([], D('0'), D('0'), D('-2'))

    def test_a_zero_entry_is_neither_in_nor_out(self):
        rows, *_ = svc.kardex_rows(D('1'), [_entry(1, T.CUSTOMER_REFUND, 5, '0')])
        assert (rows[0].quantity_in, rows[0].quantity_out, rows[0].balance) == (0, 0, D('1'))


# ── range_bounds (FR-031) ─────────────────────────────────────────────────────


class TestRangeBounds:
    def test_the_end_is_inclusive_of_its_whole_day(self):
        start, end = svc.range_bounds(date(2026, 10, 1), date(2026, 10, 5))
        assert start == datetime(2026, 10, 1)
        assert end == datetime(2026, 10, 6)

    def test_a_single_day(self):
        assert svc.range_bounds(date(2026, 10, 1), date(2026, 10, 1)) == (
            datetime(2026, 10, 1),
            datetime(2026, 10, 2),
        )

    def test_366_days_is_the_limit(self):
        # A leap year, start to end inclusive, is 366 days.
        svc.range_bounds(date(2028, 1, 1), date(2028, 12, 31))

        with pytest.raises(HTTPException) as exc:
            svc.range_bounds(date(2026, 1, 1), date(2027, 1, 2))
        assert exc.value.status_code == 422
        assert exc.value.code == 'kardex.range_too_long'
        assert exc.value.params == {'max_days': 366}

    def test_to_before_from_is_422(self):
        with pytest.raises(HTTPException) as exc:
            svc.range_bounds(date(2026, 10, 2), date(2026, 10, 1))
        assert exc.value.status_code == 422
        assert exc.value.code == 'kardex.range_reversed'
        assert exc.value.params == {}


# ── resolve (FR-032) ──────────────────────────────────────────────────────────


FOLIO = Document(facility=2, serial=40)
#: R6's table: every type that resolves, and whether it shows facility and serial.
RESOLVED = [
    (T.SALES_ORDER, True),
    (T.INVENTORY_ISSUE, True),
    (T.INVENTORY_RECEIPT, True),
    (T.INVENTORY_TRANSFER, True),
    (T.PURCHASE_ORDER, False),
    (T.INVENTORY_ADJUSTMENT, True),
    (T.DELIVERY_ORDER, False),
    (T.PHYSICAL_COUNT, True),
    (T.DELIVERY_PICKUP, True),
]


class TestResolve:
    @pytest.mark.parametrize(('source', 'folio'), RESOLVED)
    def test_each_type_resolves_to_its_document(self, source, folio):
        found = FOLIO if folio else Document()
        resolved = svc.resolve([_entry(1, source, 5)], {int(source): {5: found}})

        expected = (int(source), 5, 2, 40) if folio else (int(source), 5, None, None)
        document = resolved[1]
        assert (document.type, document.id, document.facility, document.serial) == expected

    def test_a_refund_resolves_when_the_product_is_on_its_lines(self):
        refund = Document(facility=1, serial=8, products=frozenset({7}))
        resolved = svc.resolve([_entry(1, T.CUSTOMER_REFUND, 5)], {2: {5: refund}})
        assert resolved[1] == svc.KardexDocument(type=2, id=5, facility=1, serial=8)

    def test_a_refund_without_the_product_on_its_lines_is_unresolved(self):
        """A payment cancellation's legacy row, colliding with a refund id (R6)."""
        refund = Document(facility=1, serial=8, products=frozenset({99}))
        assert svc.resolve([_entry(1, T.CUSTOMER_REFUND, 5)], {2: {5: refund}}) == {}

    def test_type_8_reference_0_is_unresolved(self):
        entries = [_entry(1, T.INVENTORY_ADJUSTMENT, 0), _entry(2, T.INVENTORY_ADJUSTMENT, 5)]
        resolved = svc.resolve(entries, {8: {5: FOLIO}})
        assert set(resolved) == {2}

    def test_a_missing_document_is_unresolved(self):
        assert svc.resolve([_entry(1, T.INVENTORY_RECEIPT, 6)], {4: {5: FOLIO}}) == {}

    @pytest.mark.parametrize('source', [T.SUPPLIER_RETURN, T.PRODUCT_CONVERSION, 99])
    def test_other_types_stay_raw(self, source):
        assert svc.resolve([_entry(1, source, 5)], {int(source): {5: FOLIO}}) == {}

    def test_references_are_grouped_by_type(self):
        entries = [_entry(1, T.INVENTORY_RECEIPT, 5), _entry(2, T.INVENTORY_ISSUE, 5, '-1')]
        references = svc.references_by_type(entries)
        assert references == {4: {5}, 3: {5}}

    def test_unresolvable_types_and_reference_0_are_not_queried(self):
        entries = [_entry(1, T.INVENTORY_ADJUSTMENT, 0), _entry(2, T.SUPPLIER_RETURN, 5)]
        assert svc.references_by_type(entries) == {}


def test_type_10_is_labelled_as_the_itinerary_it_always_references():
    # Counter pickups moved to type 13 (FR-034), so 10 is only ever a departure or settlement
    assert svc.source_name(10) == 'delivery_itinerary'
    assert svc.source_name(13) == 'delivery_pickup'
