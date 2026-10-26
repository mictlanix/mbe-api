"""Static rules of the inventory document schemas (spec 029, contracts/inventory-documents.md).

One rejected value per rule, and one test per transfer-status branch (research R4).
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.inventory import (
    IssueCreate,
    IssueUpdate,
    LineAdd,
    LineUpdate,
    ReceiptCreate,
    ReceiptUpdate,
    SalesOrderItemsAdd,
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
