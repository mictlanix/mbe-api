"""Static rules of the purchasing schemas (spec 031, contracts/purchasing.md).

One rejected value per rule.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.clock import local_today
from app.schemas.purchases import (
    AddRequestLines,
    BulkApprove,
    OrderLineAdd,
    OrderLineUpdate,
    PurchaseOrderCreate,
    PurchaseOrderEmailRequest,
    PurchaseOrderUpdate,
    PurchaseRequestCreate,
    PurchaseRequestUpdate,
    RejectRequest,
    RequestLineAdd,
    RequestLineUpdate,
    ToPurchaseCreate,
    ToPurchaseSet,
)

# ── Accepted ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'schema, fields',
    [
        (PurchaseRequestCreate, {}),
        (PurchaseRequestUpdate, {}),
        (RequestLineAdd, {'product': 1}),
        (RequestLineUpdate, {}),
        (ToPurchaseSet, {'to_purchase': False}),
        (RejectRequest, {'reason': 'Sin presupuesto'}),
        (BulkApprove, {'ids': [1]}),
        (PurchaseOrderCreate, {'supplier': 1}),
        (PurchaseOrderUpdate, {}),
        (OrderLineAdd, {'product': 1, 'quantity': '1'}),
        (OrderLineUpdate, {}),
        (AddRequestLines, {'purchase_request': 1}),
        (ToPurchaseCreate, {'supplier': 1, 'facility': 1}),
        (PurchaseOrderEmailRequest, {'to': ['a@example.com']}),
    ],
)
def test_minimal_bodies_are_accepted(schema, fields):  # noqa: ANN001
    schema.model_validate(fields)


# ── Requests ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'schema, fields',
    [
        (PurchaseRequestCreate, {'comment': 'x' * 501}),
        (PurchaseRequestUpdate, {'comment': 'x' * 501}),
        (RequestLineAdd, {'product': 1, 'quantity': '0'}),
        (RequestLineAdd, {'product': 1, 'quantity': '-1'}),
        (RequestLineAdd, {'product': 1, 'quantity': '1.005'}),
        (RequestLineAdd, {'product': 1, 'product_name': 'x' * 251}),
        (RequestLineAdd, {'product': 1, 'product_name': ''}),
        (RequestLineUpdate, {'quantity': '0'}),
        (RequestLineUpdate, {'product_name': 'x' * 251}),
        (ToPurchaseSet, {}),
        (RejectRequest, {'reason': ''}),
        (RejectRequest, {'reason': '   '}),
        (RejectRequest, {'reason': 'x' * 501}),
        (BulkApprove, {'ids': []}),
        (BulkApprove, {'ids': list(range(101))}),
    ],
)
def test_request_rules(schema, fields):  # noqa: ANN001
    with pytest.raises(ValidationError):
        schema.model_validate(fields)


def test_a_reject_reason_is_stripped():
    assert RejectRequest(reason='  Duplicado  ').reason == 'Duplicado'


# ── Purchase orders ───────────────────────────────────────────────────────────


@pytest.mark.parametrize('schema', [PurchaseOrderCreate, PurchaseOrderUpdate])
def test_the_estimated_receipt_date_is_not_in_the_past(schema):  # noqa: ANN001
    yesterday = local_today() - timedelta(days=1)
    with pytest.raises(ValidationError):
        schema.model_validate({'supplier': 1, 'estimated_receipt_date': yesterday})
    schema.model_validate({'supplier': 1, 'estimated_receipt_date': local_today()})


@pytest.mark.parametrize(
    'schema, fields',
    [
        (PurchaseOrderCreate, {}),
        (PurchaseOrderCreate, {'supplier': 1, 'comment': 'x' * 501}),
        (OrderLineAdd, {'product': 1}),
        (OrderLineAdd, {'product': 1, 'quantity': '0'}),
        (OrderLineAdd, {'product': 1, 'quantity': '1.00001'}),
        (OrderLineAdd, {'product': 1, 'quantity': '1', 'price': '-0.01'}),
        (OrderLineAdd, {'product': 1, 'quantity': '1', 'price': '1.00000001'}),
        (OrderLineAdd, {'product': 1, 'quantity': '1', 'tax_rate': '1'}),
        (OrderLineAdd, {'product': 1, 'quantity': '1', 'tax_rate': '-0.16'}),
        (OrderLineAdd, {'product': 1, 'quantity': '1', 'tax_rate': '0.16001'}),
        (OrderLineUpdate, {'quantity': '0'}),
        (OrderLineUpdate, {'price': '-1'}),
        (OrderLineUpdate, {'tax_rate': '1.5'}),
        (ToPurchaseCreate, {'supplier': None, 'facility': 1}),
        (ToPurchaseCreate, {'supplier': 1}),
        (PurchaseOrderEmailRequest, {'to': []}),
        (PurchaseOrderEmailRequest, {'to': ['not-an-address']}),
        (PurchaseOrderEmailRequest, {'to': ['a@example.com'], 'message': 'x' * 1001}),
    ],
)
def test_order_rules(schema, fields):  # noqa: ANN001
    with pytest.raises(ValidationError):
        schema.model_validate(fields)


def test_order_line_values_keep_their_precision():
    line = OrderLineAdd(product=1, quantity='2.5', price='12.3456789', tax_rate='0.16')
    assert (line.quantity, line.price, line.tax_rate) == (
        Decimal('2.5'),
        Decimal('12.3456789'),
        Decimal('0.16'),
    )
