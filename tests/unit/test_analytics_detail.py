"""Spec 037, T038: the detail's own rules, without a database."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.dialects import mysql

from app.core.errors import DomainError
from app.schemas.analytics import (
    AnalyticsDetailItem,
    AnalyticsDetailResponse,
    DetailKind,
)
from app.services.analytics import detail, facts


@pytest.mark.parametrize(
    ('sort', 'expected'),
    [
        (None, ('date', True)),
        ('date', ('date', False)),
        ('-amount', ('amount', True)),
        ('document', ('document', False)),
        ('-document', ('document', True)),
    ],
)
def test_sort_parsing(sort: str | None, expected: tuple[str, bool]) -> None:
    assert detail.parse_sort(sort) == expected


def test_an_unknown_sort_is_refused() -> None:
    with pytest.raises(DomainError) as refused:
        detail.parse_sort('-customer')

    assert refused.value.code == 'analytics.sort_invalid'


def _response(cost: Decimal | None) -> AnalyticsDetailResponse:
    item = AnalyticsDetailItem(
        kind=DetailKind.LINES, id=1, document_id=1, folio='1', date=datetime(2026, 5, 4),
        facility=1, customer=1, customer_name='C', salesperson=1, product=1, product_code='P',
        product_name='P', quantity=Decimal('1'), amount=Decimal('100.00'), cost=cost,
        margin=None if cost is None else Decimal('50.00'), payment_method=None,
    )  # fmt: skip
    return AnalyticsDetailResponse(
        as_of=datetime(2026, 5, 4),
        items=[item],
        total=1,
        sums={'amount': Decimal('100.00'), 'cost': Decimal('0.00'), 'margin': Decimal('0.00')},
    )


def test_without_the_cost_privilege_the_keys_are_absent() -> None:
    data = detail.serialize_items(_response(Decimal('50.00')), cost_visible=False)

    assert 'cost' not in data['items'][0] and 'margin' not in data['items'][0]
    assert set(data['sums']) == {'amount'}


def test_with_it_an_uncosted_line_keeps_a_null_cost() -> None:
    data = detail.serialize_items(_response(None), cost_visible=True)

    assert data['items'][0]['cost'] is None
    assert data['items'][0]['margin'] is None
    assert 'cost' in data['sums']


def test_the_cost_flags_are_the_facts_own_predicates() -> None:
    """`below` and `costed` are computed once, in the sale-line rows the fact sums."""
    rows = facts.sale_lines(datetime(2026, 5, 1).date(), datetime(2026, 5, 31).date(), None)
    sql = str(rows.select().compile(dialect=mysql.dialect())).upper()

    assert 'SALES_ORDER_DETAIL.COST > ' in sql
    assert 'AS BELOW' in sql and 'AS COSTED' in sql
