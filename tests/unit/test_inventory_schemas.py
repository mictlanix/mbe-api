"""Static rules of the inventory document schemas (spec 029, contracts/inventory-documents.md),
and of the count, stock and kardex schemas (spec 030, contracts/count-stock-kardex.md).

One rejected value per rule, and one test per transfer-status branch (research R4).
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.inventory import (
    CountConfirm,
    CountCreate,
    CountLinesSet,
    CountLineUpdate,
    CountUpdate,
    InventoryCountConfirmResponse,
    InventoryCountResponse,
    InventoryCountSummary,
    IssueCreate,
    IssueUpdate,
    Kardex,
    KardexDocument,
    KardexRow,
    LineAdd,
    LineUpdate,
    ProductStock,
    ReceiptCreate,
    ReceiptUpdate,
    SalesOrderItemsAdd,
    StockRow,
    TransferCreate,
    TransferReceive,
    TransferStatus,
    TransferUpdate,
    derive_transfer_status,
)

# ── Headers ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'schema, fields',
    [
        (ReceiptCreate, {'warehouse': 1}),
        (ReceiptUpdate, {}),
        (IssueCreate, {'warehouse': 1}),
        (IssueUpdate, {}),
        (TransferCreate, {'warehouse': 1, 'warehouse_to': 3}),
        (TransferUpdate, {}),
    ],
)
def test_comment_over_500_characters_is_refused(schema, fields):
    assert schema(**fields, comment='x' * 500).comment == 'x' * 500
    with pytest.raises(ValidationError):
        schema(**fields, comment='x' * 501)


@pytest.mark.parametrize('schema', [ReceiptCreate, IssueCreate])
def test_create_requires_a_warehouse(schema):
    with pytest.raises(ValidationError):
        schema(comment='no warehouse')


def test_transfer_create_requires_a_destination():
    with pytest.raises(ValidationError):
        TransferCreate(warehouse=1)


def test_updates_leave_unsent_fields_unset():
    assert ReceiptUpdate(comment='x').model_dump(exclude_unset=True) == {'comment': 'x'}
    assert TransferUpdate(warehouse_to=3).model_dump(exclude_unset=True) == {'warehouse_to': 3}


# ── Lines ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize('schema, fields', [(LineAdd, {'product': 1}), (LineUpdate, {})])
class TestLineQuantity:
    def test_zero_is_refused(self, schema, fields):
        with pytest.raises(ValidationError):
            schema(**fields, quantity=Decimal('0'))

    def test_negative_is_refused(self, schema, fields):
        with pytest.raises(ValidationError):
            schema(**fields, quantity=Decimal('-1'))

    def test_more_than_four_decimal_places_is_refused(self, schema, fields):
        assert schema(**fields, quantity=Decimal('1.2345')).quantity == Decimal('1.2345')
        with pytest.raises(ValidationError):
            schema(**fields, quantity=Decimal('1.23456'))


def test_line_add_requires_a_product():
    with pytest.raises(ValidationError):
        LineAdd(quantity=Decimal('1'))


def test_sales_order_items_requires_an_order():
    assert SalesOrderItemsAdd(sales_order=7).sales_order == 7
    with pytest.raises(ValidationError):
        SalesOrderItemsAdd()


# ── Receive ───────────────────────────────────────────────────────────────────


class TestTransferReceive:
    def test_omitted_lines_mean_everything_outstanding(self):
        assert TransferReceive().lines is None

    def test_empty_list_is_refused(self):
        with pytest.raises(ValidationError):
            TransferReceive(lines=[])

    def test_same_line_twice_is_refused(self):
        with pytest.raises(ValidationError):
            TransferReceive(
                lines=[{'line_id': 1, 'quantity': '1'}, {'line_id': 1, 'quantity': '2'}]
            )

    def test_zero_quantity_is_refused(self):
        with pytest.raises(ValidationError):
            TransferReceive(lines=[{'line_id': 1, 'quantity': '0'}])

    def test_more_than_four_decimal_places_is_refused(self):
        with pytest.raises(ValidationError):
            TransferReceive(lines=[{'line_id': 1, 'quantity': '0.00001'}])

    def test_distinct_lines_are_accepted(self):
        receive = TransferReceive(
            lines=[{'line_id': 1, 'quantity': '1'}, {'line_id': 2, 'quantity': '2.5'}]
        )
        assert [(r.line_id, r.quantity) for r in receive.lines] == [
            (1, Decimal('1')),
            (2, Decimal('2.5')),
        ]


# ── Transfer status (R4) ──────────────────────────────────────────────────────


class TestDeriveTransferStatus:
    def test_cancelled_wins_over_everything(self):
        status = derive_transfer_status(completed=True, cancelled=True, in_transit=True)
        assert status == TransferStatus.CANCELLED

    def test_not_completed_is_draft(self):
        status = derive_transfer_status(completed=False, cancelled=False, in_transit=False)
        assert status == TransferStatus.DRAFT

    def test_completed_and_in_transit(self):
        status = derive_transfer_status(completed=True, cancelled=False, in_transit=True)
        assert status == TransferStatus.IN_TRANSIT

    def test_completed_and_not_in_transit_is_received(self):
        status = derive_transfer_status(completed=True, cancelled=False, in_transit=False)
        assert status == TransferStatus.RECEIVED

    def test_values(self):
        assert [s.value for s in TransferStatus] == ['draft', 'in_transit', 'received', 'cancelled']


# ── Counts (spec 030, contracts/count-stock-kardex.md) ───────────────────────


@pytest.mark.parametrize('schema, fields', [(CountCreate, {'warehouse': 1}), (CountUpdate, {})])
def test_count_comment_over_500_characters_is_refused(schema, fields):
    assert schema(**fields, comment='x' * 500).comment == 'x' * 500
    with pytest.raises(ValidationError):
        schema(**fields, comment='x' * 501)


def test_count_create_requires_a_warehouse():
    with pytest.raises(ValidationError):
        CountCreate(comment='no warehouse')


def test_count_update_leaves_unsent_fields_unset():
    assert CountUpdate(comment='x').model_dump(exclude_unset=True) == {'comment': 'x'}


def _item(**fields) -> dict:
    return {'product': 1, 'quantity': '1', **fields}


class TestCountLinesSet:
    def test_by_id_and_by_code_are_accepted(self):
        lines = CountLinesSet(
            lines=[{'product': 1, 'quantity': '2'}, {'product_code': 'A1', 'quantity': '0'}]
        ).lines
        assert [(ln.product, ln.product_code, ln.quantity) for ln in lines] == [
            (1, None, Decimal('2')),
            (None, 'A1', Decimal('0')),
        ]

    def test_empty_batch_is_refused(self):
        with pytest.raises(ValidationError):
            CountLinesSet(lines=[])

    def test_more_than_2000_items_is_refused(self):
        assert len(CountLinesSet(lines=[_item(product=i) for i in range(2000)]).lines) == 2000
        with pytest.raises(ValidationError):
            CountLinesSet(lines=[_item(product=i) for i in range(2001)])

    def test_both_id_and_code_is_refused(self):
        with pytest.raises(ValidationError):
            CountLinesSet(lines=[_item(product_code='A1')])

    def test_neither_id_nor_code_is_refused(self):
        with pytest.raises(ValidationError):
            CountLinesSet(lines=[{'quantity': '1'}])

    def test_negative_quantity_is_refused(self):
        with pytest.raises(ValidationError):
            CountLinesSet(lines=[_item(quantity='-0.0001')])

    def test_more_than_four_decimal_places_is_refused(self):
        with pytest.raises(ValidationError):
            CountLinesSet(lines=[_item(quantity='1.23456')])


class TestCountLineUpdate:
    def test_zero_is_allowed(self):
        assert CountLineUpdate(quantity=Decimal('0')).quantity == Decimal('0')

    def test_negative_is_refused(self):
        with pytest.raises(ValidationError):
            CountLineUpdate(quantity=Decimal('-1'))

    def test_more_than_four_decimal_places_is_refused(self):
        with pytest.raises(ValidationError):
            CountLineUpdate(quantity=Decimal('1.23456'))


def test_count_confirm_does_not_include_uncounted_by_default():
    assert CountConfirm().include_uncounted is False
    assert CountConfirm(include_uncounted=True).include_uncounted is True


def _count(**fields) -> dict:
    return {
        'inventory_count_id': 1,
        'facility': 1,
        'warehouse': 5,
        'serial': None,
        'status': 'draft',
        'comment': None,
        'creator': 1,
        'updater': 1,
        'creation_time': datetime(2026, 10, 1, 9, 0),
        'modification_time': datetime(2026, 10, 1, 9, 0),
        **fields,
    }


_DRAFT_LINE = {
    'count_detail_id': 1,
    'product': 7,
    'product_code': 'P7',
    'product_name': 'Seven',
    'quantity': '3',
}


def test_a_draft_line_is_blind():
    line = InventoryCountResponse(**_count(lines=[_DRAFT_LINE])).lines[0]
    assert line.quantity_on_hand is None
    assert line.difference is None


def test_the_confirm_response_carries_both_warnings():
    response = InventoryCountConfirmResponse(
        **_count(serial=4, status='completed'),
        negative_available=[
            {'product': 7, 'product_code': 'P7', 'counted': '1', 'reserved': '3', 'available': '-2'}
        ],
        corrected_by_other_counts=[
            {'product': 7, 'product_code': 'P7', 'inventory_count_id': 2, 'serial': 3}
        ],
    )
    assert response.negative_available[0].available == Decimal('-2')
    assert response.corrected_by_other_counts[0].serial == 3
    assert InventoryCountConfirmResponse(**_count()).negative_available == []


def test_count_status_is_a_document_status():
    with pytest.raises(ValidationError):
        InventoryCountSummary(**_count(status='in_transit'))


# ── Stock and kardex ──────────────────────────────────────────────────────────


def test_product_stock_has_warehouses_and_in_transit():
    row = {
        'product': 7,
        'product_code': 'P7',
        'product_name': 'Seven',
        'warehouse': 5,
        'on_hand': '10',
        'reserved': '3',
        'available': '7',
        'incoming': '2',
    }
    stock = ProductStock(
        product=7,
        warehouses=[StockRow(**row)],
        in_transit=[{'facility': 1, 'warehouse': 2, 'on_hand': '4'}],
    )
    assert stock.warehouses[0].available == Decimal('7')
    assert stock.in_transit[0].on_hand == Decimal('4')


def test_kardex_serialises_its_start_as_from():
    kardex = Kardex(
        product=7,
        warehouse=5,
        **{'from': date(2026, 1, 1)},
        to=date(2026, 1, 31),
        opening='10',
        rows=[
            KardexRow(
                lot_serial_tracking_id=1,
                date=datetime(2026, 1, 2, 9, 0),
                source=12,
                source_name='Physical count',
                document=KardexDocument(type=12, id=3, facility=1, serial=4),
                reference=3,
                quantity_in='0',
                quantity_out='3',
                balance='7',
            )
        ],
        total_in='0',
        total_out='3',
        closing='7',
    )
    dumped = kardex.model_dump(by_alias=True)
    assert dumped['from'] == date(2026, 1, 1)
    assert dumped['rows'][0]['document'] == {'type': 12, 'id': 3, 'facility': 1, 'serial': 4}


def test_an_unresolved_kardex_row_has_no_document():
    row = KardexRow(
        lot_serial_tracking_id=1,
        date=datetime(2026, 1, 2, 9, 0),
        source=8,
        source_name='Adjustment',
        document=None,
        reference=0,
        quantity_in='1',
        quantity_out='0',
        balance='1',
    )
    assert row.document is None
