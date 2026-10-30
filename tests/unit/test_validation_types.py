"""Validation error types (spec 036, FR-015): each former plain `value_error` has its own `type`.

`msg` keeps today's sentence without the "Value error, " prefix, and `ctx` carries the raw values.
"""

import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.clock import local_now, local_today
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.schemas.accounts import StatementEmailRequest
from app.schemas.core import ContactCreate
from app.schemas.fiscal import (
    InvoiceLineUpdate,
    InvoiceUpdate,
    TaxpayerBatchCreate,
)
from app.schemas.inventory import CountLineSet, TransferReceive
from app.schemas.product import ProductCreate
from app.schemas.purchases import PurchaseOrderCreate, RejectRequest
from app.schemas.sales_quote import QuoteEmailRequest
from app.schemas.supplier import SupplierAgreementCreate
from app.schemas.supplier_payments import SupplierPaymentCreate
from app.schemas.user import ProfilePrivilegeUpdate, UserCreate, UserProfileCreate

TOMORROW = local_today() + timedelta(days=1)
YESTERDAY = local_today() - timedelta(days=1)
LATER = local_now() + timedelta(days=2)
PRODUCT = dict(code='A1', name='Widget', unit_of_measurement='H87')
PAYMENT = dict(supplier=1, amount='10.00', method=1)
BATCH_TEMPLATE = dict(name='default')


def _check_ctx(actual: dict | None, expected: dict) -> None:
    assert actual is not None
    for key, value in expected.items():
        assert actual[key] == value, key
        assert type(actual[key]) is type(value), key


CASES = [
    ('contact_name_blank', ContactCreate, dict(name='  '), 'Name must not be blank', {}),
    (
        'email_address_invalid',
        QuoteEmailRequest,
        dict(to=['nope']),
        "Not a valid email address: 'nope'",
        {'value': 'nope'},
    ),
    (
        'fiscal_document_payment_date_in_future',
        InvoiceUpdate,
        dict(payment_date=TOMORROW),
        'The payment date cannot be in the future',
        {'date': TOMORROW.isoformat()},
    ),
    (
        'fiscal_document_payment_form_unknown',
        InvoiceUpdate,
        dict(payment_method=9999),
        'Not a SAT payment form',
        {'payment_form': 9999},
    ),
    (
        'fiscal_document_tax_rate_invalid',
        InvoiceLineUpdate,
        dict(tax_rate='0.5'),
        'The tax rate must be 0.16, 0 or -1 (exempt)',
        {'tax_rate': '0.5'},
    ),
    (
        'inventory_count_product_reference',
        CountLineSet,
        dict(quantity='1'),
        'Give exactly one of product and product_code',
        {},
    ),
    (
        'inventory_transfer_duplicate_line',
        TransferReceive,
        dict(lines=[dict(line_id=1, quantity='1'), dict(line_id=1, quantity='2')]),
        'Each line may be received only once per request',
        {},
    ),
    (
        'product_barcode_invalid',
        ProductCreate,
        dict(PRODUCT, bar_code='123'),
        'Barcode must be empty or exactly 13 digits (EAN-13)',
        {},
    ),
    (
        'product_code_has_whitespace',
        ProductCreate,
        dict(PRODUCT, code='A 1'),
        'Code must not contain whitespace',
        {},
    ),
    (
        'profile_duplicate_system_object',
        UserProfileCreate,
        dict(
            name='p',
            privileges=[dict(system_object=1, privileges=1), dict(system_object=1, privileges=2)],
        ),
        'Duplicate system_object in privileges',
        {},
    ),
    (
        'profile_unknown_system_object',
        ProfilePrivilegeUpdate,
        dict(system_object=999999, privileges=1),
        'Unknown system object: 999999',
        {'system_object': 999999},
    ),
    (
        'purchase_receipt_date_in_past',
        PurchaseOrderCreate,
        dict(supplier=1, estimated_receipt_date=YESTERDAY),
        'The estimated receipt date cannot be in the past',
        {'date': YESTERDAY.isoformat(), 'today': local_today().isoformat()},
    ),
    (
        'purchase_request_reason_blank',
        RejectRequest,
        dict(reason='   '),
        'A reason is required and cannot be blank',
        {},
    ),
    (
        'statement_email_no_recipient',
        StatementEmailRequest,
        dict(),
        'Choose at least one contact or type an address',
        {},
    ),
    (
        'supplier_agreement_end_before_start',
        SupplierAgreementCreate,
        dict(start=date(2026, 5, 2), end=date(2026, 5, 1)),
        'End must not be before start',
        {'start': '2026-05-02', 'end': '2026-05-01'},
    ),
    (
        'supplier_payment_cash_has_date',
        SupplierPaymentCreate,
        dict(PAYMENT, date=datetime(2020, 1, 1)),
        'A cash payment is dated now; send no date',
        {},
    ),
    (
        'supplier_payment_cash_has_facility',
        SupplierPaymentCreate,
        dict(PAYMENT, facility=2),
        "A cash payment takes its session's facility; send no facility",
        {},
    ),
    (
        'supplier_payment_date_in_future',
        SupplierPaymentCreate,
        dict(PAYMENT, method=2, date=LATER),
        'The payment date cannot be in the future',
        {'date': LATER.isoformat()},
    ),
    (
        'supplier_payment_duplicate_order',
        SupplierPaymentCreate,
        dict(
            PAYMENT,
            applications=[
                dict(purchase_order=5, amount='1.00'),
                dict(purchase_order=5, amount='2.00'),
            ],
        ),
        'Each purchase order can appear only once',
        {},
    ),
    (
        'taxpayer_batch_series_blank',
        TaxpayerBatchCreate,
        dict(batch='  ', type=0, template=BATCH_TEMPLATE),
        'The series cannot be blank',
        {},
    ),
    (
        'user_id_not_alphanumeric',
        UserCreate,
        dict(
            user_id='bad id!',
            password='Str0ng-Passw0rd!x',
            email='a@b.mx',
            employee_id=1,
        ),
        'Username must contain only alphanumeric characters',
        {},
    ),
]


@pytest.mark.parametrize(
    ('error_type', 'model', 'payload', 'msg', 'ctx'), CASES, ids=[c[0] for c in CASES]
)
def test_validation_type(error_type, model, payload, msg, ctx):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)

    entries = [e for e in caught.value.errors() if e['type'] == error_type]
    assert entries, caught.value.errors()
    entry = entries[0]
    assert entry['msg'] == msg
    assert not entry['msg'].startswith('Value error, ')
    if ctx:
        _check_ctx(entry.get('ctx'), ctx)
    else:
        assert not entry.get('ctx')


def test_password_policy_type():
    with pytest.raises(ValidationError) as caught:
        UserCreate.model_validate(dict(user_id='abcd', password='a', email='a@b.mx', employee_id=1))

    entry = next(e for e in caught.value.errors() if e['type'] == 'password_policy')
    assert isinstance(entry['ctx']['failed'], list)


@pytest.mark.asyncio
async def test_future_payment_date_is_a_typed_422_over_http():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=7
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=MagicMock()))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post(
                '/api/v1/supplier-payments',
                json=dict(
                    supplier=1, amount='10.00', method=2, date=LATER.isoformat(), comment=None
                ),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422, response.text
    assert response.headers['content-type'].startswith('application/json')
    entry = next(
        e for e in response.json()['detail'] if e['type'] == 'supplier_payment_date_in_future'
    )
    assert entry['msg'] == 'The payment date cannot be in the future'
    assert re.match(r'\d{4}-\d{2}-\d{2}T', entry['ctx']['date'])
