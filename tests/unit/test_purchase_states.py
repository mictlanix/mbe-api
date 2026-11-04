"""States of purchase requests and purchase orders on their legacy flags (spec 031, research R1)."""

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import mysql

from app.schemas.purchases import RequestStatus
from app.schemas.sales_order import DocumentStatus
from app.services.purchase_order_service import order_status, order_status_clause
from app.services.purchase_request_service import request_status, request_status_clause


@pytest.mark.parametrize(
    'completed, approved, cancelled, expected',
    [
        (False, False, False, RequestStatus.DRAFT),
        (None, False, None, RequestStatus.DRAFT),
        (True, False, False, RequestStatus.SUBMITTED),
        (True, True, False, RequestStatus.APPROVED),
        (False, False, True, RequestStatus.CANCELLED),
        # In the data: submitted, then cancelled.
        (True, False, True, RequestStatus.CANCELLED),
        (True, True, True, RequestStatus.CANCELLED),
        # Approved without being submitted never happens through the API; it is still a draft.
        (False, True, False, RequestStatus.DRAFT),
    ],
)
def test_request_status(completed, approved, cancelled, expected):  # noqa: ANN001
    assert request_status(completed, approved, cancelled) is expected


@pytest.mark.parametrize(
    'completed, cancelled, expected',
    [
        (False, False, DocumentStatus.DRAFT),
        (True, False, DocumentStatus.COMPLETED),
        (False, True, DocumentStatus.CANCELLED),
        (True, True, DocumentStatus.CANCELLED),
    ],
)
def test_order_status(completed, cancelled, expected):  # noqa: ANN001
    assert order_status(completed, cancelled) is expected


def _sql(clause) -> str:  # noqa: ANN001
    return str(clause.compile(dialect=mysql.dialect(), compile_kwargs={'literal_binds': True}))


@pytest.mark.parametrize('value', ['draft', 'submitted', 'approved', 'cancelled'])
def test_every_request_status_filters(value):  # noqa: ANN001
    assert 'purchase_request' in _sql(request_status_clause(value))


@pytest.mark.parametrize('value', ['draft', 'completed', 'cancelled'])
def test_every_order_status_filters(value):  # noqa: ANN001
    assert 'purchase_order' in _sql(order_status_clause(value))


@pytest.mark.parametrize(
    'clause, value, resource',
    [
        (request_status_clause, 'completed', 'purchase_request'),
        (request_status_clause, 'paid', 'purchase_request'),
        (order_status_clause, 'submitted', 'purchase_order'),
        (order_status_clause, 'paid', 'purchase_order'),
    ],
)
def test_an_unknown_status_is_refused(clause, value, resource):  # noqa: ANN001
    with pytest.raises(HTTPException) as caught:
        clause(value)
    assert caught.value.status_code == 422
    assert caught.value.code == 'status_filter.unsupported'
    assert caught.value.params == {'resource': resource, 'status': value}


def test_a_null_flag_counts_as_false_in_the_filter():
    assert 'coalesce' in _sql(request_status_clause('draft')).lower()
