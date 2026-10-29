"""Pure helpers of the shared inventory mechanics (spec 029, T005).

Line merging, per-product totals, shortages, the reversal of what a document posted, and the set
of warehouses a step locks (research R1–R3). Then the count kind of spec 030 (research R1).
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.errors import DomainError
from app.enums import SystemObject, TransactionType
from app.models.inventory import InventoryCount, InventoryCountDetail
from app.services import inventory_documents as inv
from app.services.inventory_documents import COUNT, ISSUE, RECEIPT, TRANSFER


def _line(line_id: int, product: int, quantity: str) -> SimpleNamespace:
    return SimpleNamespace(receipt_detail_id=line_id, product=product, quantity=Decimal(quantity))


def _entry(warehouse: int, product: int, quantity: str) -> SimpleNamespace:
    return SimpleNamespace(warehouse=warehouse, product=product, quantity=Decimal(quantity))


class TestAssertFacility:
    def test_another_facility_is_refused_with_its_resource(self):
        current = SimpleNamespace(administrator=False, facility_id=2)
        with pytest.raises(DomainError) as exc:
            inv.assert_facility(current, 1, resource='inventory_receipt')
        assert exc.value.status_code == 403
        assert exc.value.code == 'facility.other_facility'
        assert exc.value.params == {'resource': 'inventory_receipt'}
        assert exc.value.detail == 'Document belongs to another facility'

    def test_own_facility_and_administrators_pass(self):
        inv.assert_facility(
            SimpleNamespace(administrator=False, facility_id=1), 1, 3, resource='inventory_issue'
        )
        inv.assert_facility(
            SimpleNamespace(administrator=True, facility_id=2), 1, resource='inventory_issue'
        )


# ── merge_line_quantity (FR-003, R9) ──────────────────────────────────────────


class TestMergeLineQuantity:
    def test_no_line_of_that_product_starts_a_new_one(self):
        line, quantity = inv.merge_line_quantity(RECEIPT, [_line(1, 7, '2')], 8, Decimal('3'))
        assert line is None
        assert quantity == Decimal('3')

    def test_adds_to_the_existing_line(self):
        existing = _line(1, 7, '2')
        line, quantity = inv.merge_line_quantity(RECEIPT, [existing], 7, Decimal('3'))
        assert line is existing
        assert quantity == Decimal('5')

    def test_a_repeated_product_adds_to_the_lowest_id(self):
        lines = [_line(9, 7, '1'), _line(4, 7, '2'), _line(6, 7, '3')]
        line, quantity = inv.merge_line_quantity(RECEIPT, lines, 7, Decimal('1.5'))
        assert line.receipt_detail_id == 4
        assert quantity == Decimal('3.5')


# ── per_product_totals ────────────────────────────────────────────────────────


def test_per_product_totals_sums_repeated_products():
    lines = [_line(1, 7, '2'), _line(2, 8, '1'), _line(3, 7, '0.5')]
    assert inv.per_product_totals(lines) == {7: Decimal('2.5'), 8: Decimal('1')}


def test_per_product_totals_of_no_lines_is_empty():
    assert inv.per_product_totals([]) == {}


# ── shortages ─────────────────────────────────────────────────────────────────


class TestShortages:
    def test_empty_when_everything_fits(self):
        required = {7: Decimal('2'), 8: Decimal('3')}
        assert inv.shortages(required, {7: Decimal('2'), 8: Decimal('10')}) == []

    def test_names_each_short_product_with_available_and_required(self):
        required = {8: Decimal('3'), 7: Decimal('2')}
        available = {7: Decimal('1'), 8: Decimal('3')}
        assert inv.shortages(required, available) == [(7, Decimal('1'), Decimal('2'))]

    def test_a_product_missing_from_available_has_none(self):
        assert inv.shortages({7: Decimal('1')}, {}) == [(7, Decimal('0'), Decimal('1'))]

    def test_ordered_by_product(self):
        required = {9: Decimal('1'), 3: Decimal('1')}
        assert [p for p, _, _ in inv.shortages(required, {})] == [3, 9]


# ── reversal_entries and net_outbound (R3) ────────────────────────────────────


def test_reversal_negates_each_entry():
    entries = [_entry(1, 7, '5'), _entry(2, 7, '-5'), _entry(1, 8, '0')]
    assert inv.reversal_entries(entries) == [
        inv.Movement(warehouse=1, product=7, quantity=Decimal('-5')),
        inv.Movement(warehouse=2, product=7, quantity=Decimal('5')),
        inv.Movement(warehouse=1, product=8, quantity=Decimal('0')),
    ]


class TestNetOutbound:
    def test_only_keys_whose_net_is_outbound(self):
        # A receipt of 5 at warehouse 1: the reversal takes 5 out of warehouse 1.
        reversal = inv.reversal_entries([_entry(1, 7, '5')])
        assert inv.net_outbound(reversal) == {(1, 7): Decimal('5')}

    def test_inbound_reversals_need_no_stock(self):
        # An issue of 5: the reversal puts 5 back.
        reversal = inv.reversal_entries([_entry(1, 7, '-5')])
        assert inv.net_outbound(reversal) == {}

    def test_nets_per_warehouse_and_product(self):
        # A transfer of 10, 4 received: origin −10, transit +10 −4, destination +4.
        entries = [_entry(1, 7, '-10'), _entry(2, 7, '10'), _entry(2, 7, '-4'), _entry(3, 7, '4')]
        assert inv.net_outbound(inv.reversal_entries(entries)) == {
            (2, 7): Decimal('6'),
            (3, 7): Decimal('4'),
        }

    def test_entries_that_cancel_out_need_nothing(self):
        entries = [_entry(2, 7, '10'), _entry(2, 7, '-10')]
        assert inv.net_outbound(inv.reversal_entries(entries)) == {}


# ── warehouses_to_lock (R1, R2) ───────────────────────────────────────────────


TRANSIT = {1: 2, 3: 4}


class TestWarehousesToLock:
    def test_receipt_locks_its_warehouse_and_its_facilitys_transit(self):
        receipt = SimpleNamespace(facility=1, warehouse=5)
        assert inv.warehouses_to_lock(RECEIPT, receipt, TRANSIT, set()) == [2, 5]

    def test_issue_too(self):
        issue = SimpleNamespace(facility=3, warehouse=1)
        assert inv.warehouses_to_lock(ISSUE, issue, TRANSIT, set()) == [1, 4]

    def test_transfer_locks_origin_destination_and_the_origin_facilitys_transit(self):
        transfer = SimpleNamespace(facility=1, warehouse=9, warehouse_to=6)
        assert inv.warehouses_to_lock(TRANSFER, transfer, TRANSIT, set()) == [2, 6, 9]

    def test_the_transit_is_the_documents_facility_not_the_warehouses(self):
        # The warehouse was moved to facility 3 after the draft was made; the document keeps 1.
        receipt = SimpleNamespace(facility=1, warehouse=5)
        assert 4 not in inv.warehouses_to_lock(RECEIPT, receipt, TRANSIT, set())

    def test_adds_ledger_warehouses_deduplicated_and_sorted(self):
        receipt = SimpleNamespace(facility=1, warehouse=5)
        assert inv.warehouses_to_lock(RECEIPT, receipt, TRANSIT, {8, 5, 2, 3}) == [2, 3, 5, 8]

    def test_a_facility_with_no_transit_is_refused(self):
        receipt = SimpleNamespace(facility=7, warehouse=5)
        with pytest.raises(HTTPException) as exc:
            inv.warehouses_to_lock(RECEIPT, receipt, TRANSIT, set())
        assert exc.value.status_code == 422
        assert exc.value.detail == 'Facility 7 has no in-transit location'
        assert exc.value.code == 'facility.no_in_transit_location'
        assert exc.value.params == {'facility_ids': [7]}


# ── The count kind (spec 030, R1) ─────────────────────────────────────────────


class TestCountKind:
    def test_is_the_count_tables_with_its_own_type_and_object(self):
        assert COUNT.header is InventoryCount
        assert COUNT.line is InventoryCountDetail
        assert COUNT.line_fk == 'count_id'
        assert COUNT.id_attr == 'inventory_count_id'
        assert COUNT.line_id_attr == 'count_detail_id'
        assert COUNT.warehouses == ('warehouse',)
        assert COUNT.transit is False
        assert COUNT.transaction_type == TransactionType.PHYSICAL_COUNT == 12
        assert COUNT.system_object == SystemObject.PHYSICAL_COUNT_ADJUSTMENT
        assert COUNT.label == 'Count'

    def test_line_fk_names_the_count_column(self):
        assert getattr(InventoryCountDetail, COUNT.line_fk).property.columns[0].name == 'count'

    def test_only_a_count_is_final_once_completed(self):
        assert COUNT.cancellable_when_completed is False
        assert all(k.cancellable_when_completed for k in (RECEIPT, ISSUE, TRANSFER))

    def test_counts_lock_their_warehouse_and_their_facilitys_transit(self):
        count = SimpleNamespace(facility=1, warehouse=5)
        assert inv.warehouses_to_lock(COUNT, count, TRANSIT, set()) == [2, 5]


def test_delivery_pickup_is_13():
    assert TransactionType.DELIVERY_PICKUP == 13


class _Session:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


def _locked(document: SimpleNamespace):  # noqa: ANN202
    async def begin_locked(db, kind, document_id, **_):  # noqa: ANN001, ANN202
        return document

    return begin_locked


async def test_cancelling_a_confirmed_count_is_409(monkeypatch):
    count = SimpleNamespace(facility=1, completed=True, cancelled=False, serial=3)
    monkeypatch.setattr(inv, 'begin_locked', _locked(count))
    db = _Session()

    with pytest.raises(HTTPException) as exc:
        await inv.cancel(db, COUNT, 10, current=SimpleNamespace(administrator=True))

    assert exc.value.status_code == 409
    assert exc.value.detail == 'A confirmed count cannot be cancelled'
    assert exc.value.code == 'inventory_count.confirmed_cannot_cancel'
    assert exc.value.params == {}
    assert count.cancelled is False
    assert db.rolled_back
