"""Coded errors (spec 036): one exception, one 404 helper, one body, one handler.

Every error the API answers is an RFC 9457 Problem body served as `application/problem+json`:
`status`, a `code` from `ErrorCode`, today's English `detail`, and raw `params` a client formats in
its own language. This is the only module that constructs an `HTTPException` (a test enforces it).
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.error_codes import ErrorCode

PROBLEM_MEDIA_TYPE = 'application/problem+json'


def _raw(value: Any) -> Any:
    """A param as the client receives it: money keeps its scale as a string, never a float.

    FastAPI's encoder would turn `Decimal('18450.00')` into `18450.0`. Normalizing here, not in the
    handler, keeps `exc.params` equal to the body for tests and for the 200 bodies that copy them.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _raw(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [_raw(v) for v in value]
    return value


class DomainError(HTTPException):
    """An error with a catalog code. Its status is the code's; there is no status argument.

    A subclass of `HTTPException`, so `except HTTPException` and `exc.detail` keep working.
    """

    def __init__(
        self,
        code: ErrorCode,
        detail: str | dict[str, Any],
        params: dict[str, Any] | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=code.status, detail=detail, headers=headers)
        self.code = code
        self.params: dict[str, Any] = _raw(params or {})
        self.errors: list[dict[str, Any]] | None = _raw(errors) if errors else None


# ── Public resource names ─────────────────────────────────────────────────────
#
# Every table maps to the name a client sees, so no error body carries a table name (SC-006).
# Deliberate rather than derived: 93 of 94 model classes are named after their tables, so a
# derived name would expose the table by another route. A test fails when a table is missing.

RESOURCE_NAMES: dict[str, str] = {
    'access_privilege': 'privilege',
    'address': 'address',
    'bank_account': 'bank_account',
    'cash_count': 'cash_count',
    'cash_drawer': 'cash_drawer',
    'cash_session': 'cash_session',
    'commission': 'commission',
    'commission_agent': 'commission_agent',
    'commission_participation': 'commission_participation',
    'commission_period': 'commission_period',
    'commission_period_line': 'commission_period_line',
    'commission_product': 'commission_product',
    'commission_salesperson': 'commission_salesperson',
    'commissions_history': 'commission_history',
    'contact': 'contact',
    'credit_note': 'credit_note',
    'customer': 'customer',
    'customer_address': 'customer_address',
    'customer_contact': 'customer_contact',
    'customer_discount': 'customer_discount',
    'customer_payment': 'customer_payment',
    'customer_refund': 'customer_refund',
    'customer_refund_detail': 'customer_refund_line',
    'customer_taxpayer': 'customer_taxpayer',
    'deliveries_itinerary': 'delivery_itinerary',
    'deliveries_itinerary_detail': 'delivery_itinerary_line',
    'deliveries_itinerary_stop': 'delivery_itinerary_stop',
    'delivery_order': 'delivery_order',
    'delivery_order_detail': 'delivery_order_line',
    'delivery_order_event': 'delivery_order_event',
    'employee': 'employee',
    'exchange_rate': 'exchange_rate',
    'expense_voucher': 'expense_voucher',
    'expense_voucher_detail': 'expense_voucher_line',
    'expenses': 'expense',
    'facility': 'facility',
    'fiscal_document': 'fiscal_document',
    'fiscal_document_detail': 'fiscal_document_line',
    'fiscal_document_relation': 'fiscal_document_relation',
    'fiscal_document_xml': 'fiscal_document_xml',
    'incidence': 'incidence',
    'inventory_count': 'inventory_count',
    'inventory_count_detail': 'inventory_count_line',
    'inventory_issue': 'inventory_issue',
    'inventory_issue_detail': 'inventory_issue_line',
    'inventory_receipt': 'inventory_receipt',
    'inventory_receipt_detail': 'inventory_receipt_line',
    'inventory_transfer': 'inventory_transfer',
    'inventory_transfer_detail': 'inventory_transfer_line',
    'label': 'label',
    'lot_serial_rqmt': 'stock_reservation',
    'lot_serial_tracking': 'stock_movement',
    'notarization': 'notarization',
    'payment_method_option': 'payment_method_option',
    'point_sale': 'point_of_sale',
    'postal_code': 'postal_code',
    'price_list': 'price_list',
    'product': 'product',
    'product_cost_change': 'product_cost_change',
    'product_label': 'product_label',
    'product_price': 'product_price',
    'proof_of_delivery': 'proof_of_delivery',
    'purchase_order': 'purchase_order',
    'purchase_order_detail': 'purchase_order_line',
    'purchase_order_payment': 'purchase_order_payment',
    'purchase_request': 'purchase_request',
    'purchase_request_detail': 'purchase_request_line',
    'sales_order': 'sales_order',
    'sales_order_detail': 'sales_order_line',
    'sales_order_payment': 'sales_order_payment',
    'sales_quote': 'sales_quote',
    'sales_quote_detail': 'sales_quote_line',
    'sat_cfdi_usage': 'sat_cfdi_usage',
    'sat_country': 'sat_country',
    'sat_currency': 'sat_currency',
    'sat_postal_code': 'sat_postal_code',
    'sat_product_service': 'sat_product_service',
    'sat_reason_cancellation': 'sat_reason_cancellation',
    'sat_tax_regime': 'sat_tax_regime',
    'sat_unit_of_measurement': 'sat_unit_of_measurement',
    'supplier': 'supplier',
    'supplier_address': 'supplier_address',
    'supplier_agreement': 'supplier_agreement',
    'supplier_balance_cutoff': 'supplier_balance_cutoff',
    'supplier_bank_account': 'supplier_bank_account',
    'supplier_contact': 'supplier_contact',
    'supplier_payment': 'supplier_payment',
    'supplier_return': 'supplier_return',
    'supplier_return_detail': 'supplier_return_line',
    'taxpayer_batch': 'taxpayer_batch',
    'taxpayer_certificate': 'taxpayer_certificate',
    'taxpayer_issuer': 'taxpayer_issuer',
    'taxpayer_recipient': 'taxpayer_recipient',
    'translation_request': 'translation_request',
    'user': 'user',
    'user_profile': 'user_profile',
    'user_profile_privilege': 'user_profile_privilege',
    'user_settings': 'user_settings',
    'vehicle': 'vehicle',
    'vehicle_operator': 'vehicle_operator',
    'warehouse': 'warehouse',
}

#: Things a 404 can name that are not tables.
EXTRA_RESOURCES = frozenset(
    {'proof_of_delivery_image', 'license_photo', 'receivable', 'product_image'}
)


def resource_name(resource: Any) -> str:
    """The public name of a model class, a table name, or an already public name."""
    table = getattr(resource, '__tablename__', resource)
    if table in RESOURCE_NAMES:
        return RESOURCE_NAMES[table]
    if table in RESOURCE_NAMES.values() or table in EXTRA_RESOURCES:
        return table
    raise ValueError(f'No public resource name for {table!r}')


def not_found(resource: Any, id: Any = None, detail: str | None = None) -> DomainError:
    """`resource.not_found`. Raise what it returns: `raise not_found(Customer, customer_id)`.

    `detail` defaults to "<Name> not found"; pass today's text where it differs.
    """
    name = resource_name(resource)
    if detail is None:
        detail = f'{name.replace("_", " ").capitalize()} not found'
    return DomainError(ErrorCode.RESOURCE_NOT_FOUND, detail, {'resource': name, 'id': id})


# ── The body and the handler ──────────────────────────────────────────────────


def problem_body(
    status: int,
    code: ErrorCode,
    detail: Any,
    params: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A Problem body. `params` and `errors` are left out when empty."""
    body: dict[str, Any] = {'status': status, 'code': code.value, 'detail': detail}
    if params:
        body['params'] = params
    if errors:
        body['errors'] = errors
    return body


def problem_response(body: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        body, status_code=body['status'], headers=headers, media_type=PROBLEM_MEDIA_TYPE
    )


#: Codes for the HTTP errors the framework raises itself: a missing token, an unknown route, a
#: method the route lacks, a body it cannot parse.
FRAMEWORK_CODES = {
    400: ErrorCode.REQUEST_MALFORMED,
    401: ErrorCode.AUTH_NOT_AUTHENTICATED,
    404: ErrorCode.REQUEST_ROUTE_NOT_FOUND,
    405: ErrorCode.REQUEST_METHOD_NOT_ALLOWED,
}


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Every HTTP exception, ours or the framework's, as a Problem. Headers are kept."""
    if isinstance(exc, DomainError):
        body = problem_body(exc.status_code, exc.code, exc.detail, exc.params, exc.errors)
    else:
        code = FRAMEWORK_CODES.get(exc.status_code, ErrorCode.REQUEST_MALFORMED)
        body = problem_body(exc.status_code, code, exc.detail)
    return problem_response(body, exc.headers)
