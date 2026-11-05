"""Spec 038, T015: the analytics kardex, paged over any range with value (research R6).

Spec 030's kardex caps a range at 366 days; this one pages instead, so each page must start from
the balance its skipped entries left, exactly as one long read would.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.schemas.analytics import AnalyticsKardexRequest
from app.services import kardex_service
from app.services.analytics import kardex
from tests.integration.conftest import (  # noqa: F401
    client,
    current_user,
    db,
    engine,
    seeded,
    sessions,
)
from tests.integration.inventory_seed import cost, product
from tests.integration.stock_seed import T, entry

pytestmark = pytest.mark.usefixtures('seeded')

ADMIN = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)
W = 1
START, END = date(2025, 3, 1), date(2026, 9, 30)


@pytest.fixture
async def ledger(db: AsyncSession) -> int:  # noqa: F811
    """Seven entries over 18 months, one before the range."""
    p = await product(db, code='K')
    await cost(db, p, '4')
    for qty, at, source in (
        ('20', datetime(2025, 2, 20), T.INVENTORY_ADJUSTMENT),  # before the range
        ('10', datetime(2025, 3, 1, 9), T.INVENTORY_RECEIPT),
        ('-3', datetime(2025, 8, 1), T.SALES_ORDER),
        ('-2', datetime(2025, 12, 31, 23), T.SALES_ORDER),
        ('5', datetime(2026, 2, 1), T.INVENTORY_ADJUSTMENT),
        ('-4', datetime(2026, 7, 1), T.SALES_ORDER),
        ('-1', datetime(2026, 9, 30, 18), T.SALES_ORDER),
        ('9', datetime(2026, 10, 1), T.INVENTORY_ADJUSTMENT),  # after the range
    ):
        await entry(db, p, W, qty, at, source=source)
    return p


def _request(product_id: int, **kw: object) -> AnalyticsKardexRequest:
    return AnalyticsKardexRequest(
        **{'product': product_id, 'warehouse': W, 'start': START, 'end': END} | kw
    )


async def test_pages_continue_the_running_balance(db: AsyncSession, ledger: int) -> None:  # noqa: F811
    pages = [
        await kardex.read(db, _request(ledger, skip=skip, limit=2), cost=True) for skip in (0, 2, 4)
    ]

    assert [p.total for p in pages] == [6, 6, 6]
    rows = [row for page in pages for row in page.rows]
    assert [r.quantity for r in rows] == [10, -3, -2, 5, -4, -1]
    for before, page in zip(pages, pages[1:], strict=False):
        first = page.rows[0]
        assert first.balance - first.quantity == before.rows[-1].balance
    assert [r.balance for r in rows] == [30, 27, 25, 30, 26, 25]


async def test_the_balances_match_spec_030s_kardex(db: AsyncSession, ledger: int) -> None:  # noqa: F811
    """Spec 030's route covers at most 366 days, so it is read in two pieces."""
    found = await kardex.read(db, _request(ledger, limit=500), cost=True)
    first = await kardex_service.kardex(
        db, product=ledger, warehouse=W, from_=START, to=date(2025, 12, 31)
    )
    second = await kardex_service.kardex(
        db, product=ledger, warehouse=W, from_=date(2026, 1, 1), to=END
    )

    assert [r.balance for r in found.rows] == [r.balance for r in (*first.rows, *second.rows)]
    assert found.opening == first.opening and found.closing == second.closing


async def test_opening_and_closing_bound_the_range(db: AsyncSession, ledger: int) -> None:  # noqa: F811
    found = await kardex.read(db, _request(ledger, skip=2, limit=2), cost=True)

    assert found.opening == 20
    assert found.closing == 25
    assert (found.start, found.end, found.product, found.warehouse) == (START, END, ledger, W)


async def test_value_is_the_balance_at_the_current_cost(db: AsyncSession, ledger: int) -> None:  # noqa: F811
    found = await kardex.read(db, _request(ledger, limit=1), cost=True)
    hidden = await kardex.read(db, _request(ledger, limit=1), cost=False)

    assert found.unit_cost == Decimal('4')
    assert found.rows[0].value == Decimal('120.00')
    data = kardex.serialize(hidden, cost_visible=False)
    assert 'unit_cost' not in data
    assert all('value' not in row for row in data['rows'])
    assert 'unit_cost' in kardex.serialize(found, cost_visible=True)


async def test_a_start_before_the_history_is_refused(db: AsyncSession, ledger: int) -> None:  # noqa: F811
    with pytest.raises(DomainError) as refused:
        await kardex.check(db, _request(ledger, start=date(2025, 2, 6)), ADMIN)

    assert refused.value.code is ErrorCode.ANALYTICS_BEFORE_HISTORY
    assert refused.value.params['history'] == 'stock'


async def test_a_page_holds_at_most_500_entries(client: AsyncClient, ledger: int) -> None:  # noqa: F811
    params = {'product': ledger, 'warehouse': W, 'start': '2025-03-01', 'end': '2026-09-30'}
    too_many = await client.get('/api/v1/analytics/kardex', params={**params, 'limit': 501})
    allowed = await client.get('/api/v1/analytics/kardex', params={**params, 'limit': 500})

    assert too_many.status_code == 422
    assert allowed.status_code == 200, allowed.text
    assert [row['balance'] for row in allowed.json()['rows']][-1] == '25.0000'


async def test_the_kardex_exports_every_entry(client: AsyncClient, ledger: int) -> None:  # noqa: F811
    body = {
        'format': 'csv',
        'kardex': {
            'product': ledger, 'warehouse': W, 'start': '2025-03-01', 'end': '2026-09-30',
            'skip': 4, 'limit': 1,
        },
    }  # fmt: skip
    response = await client.post('/api/v1/analytics/exports', json=body)

    assert response.status_code == 200, response.text
    lines = response.content.decode('utf-8-sig').splitlines()
    assert lines[0].split(',') == [
        'date', 'movement_type', 'source', 'reference', 'document_id', 'quantity', 'balance',
        'value',
    ]  # fmt: skip
    assert len(lines) == 1 + 6 + 1  # skip and limit are ignored; a TOTAL row closes it
    assert lines[-1].split(',')[-2] == '25.0000'
