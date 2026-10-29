"""The coded-error machinery (spec 036): the enum, `DomainError`, the body and the handler."""

from datetime import date, datetime
from decimal import Decimal
from enum import IntEnum, StrEnum

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, http_exception_handler, not_found, problem_body
from app.models.customer import Customer
from tests.problem import assert_problem


class _Level(IntEnum):
    HIGH = 3


class _State(StrEnum):
    OPEN = 'open'


def test_a_code_carries_its_status() -> None:
    assert ErrorCode('resource.conflict').status == 409
    assert ErrorCode.RESOURCE_NOT_FOUND.status == 404
    assert ErrorCode.RESOURCE_NOT_FOUND == 'resource.not_found'


def test_the_enum_exports_as_a_plain_string_enum() -> None:
    class _M(BaseModel):
        code: ErrorCode

    schema = _M.model_json_schema()['$defs']['ErrorCode']
    assert schema['type'] == 'string'
    assert 'resource.not_found' in schema['enum']


def test_a_domain_error_takes_its_status_from_its_code() -> None:
    exc = DomainError(ErrorCode.CASH_SESSION_DRAWER_BUSY, 'Drawer busy', {'cash_drawer_id': 4})
    assert exc.status_code == 409
    assert exc.code is ErrorCode.CASH_SESSION_DRAWER_BUSY
    assert exc.params == {'cash_drawer_id': 4}


def test_except_http_exception_still_catches_it_with_its_detail() -> None:
    with pytest.raises(HTTPException) as caught:
        raise DomainError(ErrorCode.SALES_ORDER_NO_LINES, 'Cannot confirm an order with no lines')
    assert caught.value.detail == 'Cannot confirm an order with no lines'


def test_params_are_raw_values() -> None:
    exc = DomainError(
        ErrorCode.CUSTOMER_CREDIT_LIMIT_EXCEEDED,
        'Over the limit',
        {
            'outstanding': Decimal('18450.00'),
            'limit': Decimal('15000.00'),
            'on': date(2026, 10, 28),
            'at': datetime(2026, 10, 28, 9, 30),
            'level': _Level.HIGH,
            'state': _State.OPEN,
            'nested': [{'system_object': _Level.HIGH, 'right': None, 'qty': Decimal('1.50')}],
            'gone': None,
        },
    )
    assert exc.params == {
        'outstanding': '18450.00',
        'limit': '15000.00',
        'on': '2026-10-28',
        'at': '2026-10-28T09:30:00',
        'level': 3,
        'state': 'open',
        'nested': [{'system_object': 3, 'qty': '1.50'}],
    }


def test_not_found_names_the_public_resource_and_id() -> None:
    exc = not_found(Customer, 999999)
    assert exc.status_code == 404
    assert exc.detail == 'Customer not found'
    assert exc.params == {'resource': 'customer', 'id': 999999}


def test_not_found_maps_a_renamed_table_and_omits_a_missing_id() -> None:
    exc = not_found('point_sale', detail='Point of sale not found')
    assert exc.params == {'resource': 'point_of_sale'}


def test_not_found_refuses_an_unknown_name() -> None:
    with pytest.raises(ValueError):
        not_found('no_such_thing')


def test_problem_body_leaves_out_empty_parts_and_keeps_a_dict_detail() -> None:
    detail = {'message': 'Order has lines priced at zero', 'lines': [{'line': 1}]}
    assert problem_body(409, ErrorCode.SALES_ORDER_ZERO_PRICED_LINES, detail) == {
        'status': 409,
        'code': 'sales_order.zero_priced_lines',
        'detail': detail,
    }


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    @app.get('/domain')
    async def _domain() -> None:
        raise DomainError(
            ErrorCode.AUTH_NOT_AUTHENTICATED,
            'Not authenticated',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    @app.get('/batch')
    async def _batch() -> None:
        raise DomainError(
            ErrorCode.INVENTORY_COUNT_BATCH_INVALID,
            '1 invalid entry; nothing was applied',
            errors=[{'index': 0, 'reason': 'x', 'code': ErrorCode.INVENTORY_COUNT_LINE_REPEATED}],
        )

    @app.get('/plain')
    async def _plain() -> None:
        raise HTTPException(status_code=401, detail='Not authenticated')

    return app


@pytest.mark.asyncio
async def test_the_handler_serves_a_problem_and_keeps_headers() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url='http://t') as c:
        r = await c.get('/domain')
    body = assert_problem(r, 401, 'auth.not_authenticated')
    assert r.headers['www-authenticate'] == 'Bearer'
    assert 'params' not in body


@pytest.mark.asyncio
async def test_the_handler_passes_errors_through() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url='http://t') as c:
        r = await c.get('/batch')
    body = assert_problem(r, 422, 'inventory_count.batch_invalid')
    assert body['errors'] == [
        {'index': 0, 'reason': 'x', 'code': 'inventory_count_line.repeated'}
    ]


@pytest.mark.asyncio
async def test_a_framework_exception_is_coded_by_status() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url='http://t') as c:
        r = await c.get('/plain')
    assert_problem(r, 401, 'auth.not_authenticated')
