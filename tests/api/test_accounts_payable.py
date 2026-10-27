"""The /accounts-payable routes (spec 033, US3): the HTTP contract, with the services patched.

Figures against a real database are in tests/integration/test_accounts_payable_flow.py.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight
from app.main import app

BASE = '/api/v1/accounts-payable'
ROUTE = 'app.api.v1.endpoints.accounts_payable'
WHEN = datetime(2026, 10, 27, 12, 0)
ZERO = Decimal('0.00')


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, rights: int | None = AccessRight.READ, facility: int = 3) -> None:
    """A non-administrator of `facility` holding `rights` on 19; None holds nothing."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=False,
        facility_id=facility,
        employee_id=7,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = (
        None if rights is None else SimpleNamespace(privileges=rights)
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _summary() -> dict:
    return dict(
        party=5, name='Acme', sales_agent=None, sales_agent_name=None,
        owed=Decimal('5000.00'), overdue=ZERO,
        buckets={'current': Decimal('5000.00'), '1-30': ZERO, '31-60': ZERO, '61-90': ZERO,
                 '90+': ZERO},
        oldest=WHEN, credit=Decimal('1000.00'), net=Decimal('4000.00'), credit_limit=None,
        credit_days=30, owed_company=Decimal('5000.00'), utilization=None, available_credit=None,
    )  # fmt: skip


def _listing() -> dict:
    return dict(
        items=[_summary()],
        total=1,
        totals=dict(owed=Decimal('5000.00'), overdue=ZERO, buckets=_summary()['buckets']),
    )


def _document() -> dict:
    return dict(
        id=9, serial=None, date=WHEN, due_date=None, days_past_due=0, bucket='current',
        facility=None, facility_code=None, salesperson=None, linked=[],
        currency=0, exchange_rate=Decimal('1'), total=Decimal('8000.00'), deductions=ZERO,
        applied=Decimal('3000.00'), balance=Decimal('5000.00'),
    )  # fmt: skip


class TestTheSummary:
    async def test_lists_every_supplier_with_the_cutoff(self) -> None:
        _auth()
        summary = AsyncMock(return_value=_listing())
        with (
            patch(f'{ROUTE}.accounts_service.summary', summary),
            patch(f'{ROUTE}.supplier_payment_service.get_cutoff', AsyncMock(return_value=WHEN)),
        ):
            async with _client() as client:
                response = await client.get(BASE)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body['items'][0]['party'] == 5
        assert body['items'][0]['sales_agent'] is None
        assert body['cutoff'] == '2026-10-27T12:00:00'
        assert body['scope'] == {'facility': None, 'all_facilities': True}
        assert summary.await_args.kwargs['facility'] is None, 'company-wide for any facility'

    async def test_a_facility_parameter_is_not_taken(self) -> None:
        """FR-019: POs have no facility, so there's nothing to narrow by."""
        _auth()
        summary = AsyncMock(return_value=_listing())
        with (
            patch(f'{ROUTE}.accounts_service.summary', summary),
            patch(f'{ROUTE}.supplier_payment_service.get_cutoff', AsyncMock(return_value=None)),
        ):
            async with _client() as client:
                await client.get(BASE, params={'facility': 2, 'sales_agent': 1})

        assert summary.await_args.kwargs['facility'] is None
        assert 'sales_agent' not in summary.await_args.kwargs

    async def test_without_a_login_is_401(self) -> None:
        async with _client() as client:
            response = await client.get(BASE)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_without_read_on_19_is_403(self) -> None:
        _auth(rights=None)
        async with _client() as client:
            response = await client.get(BASE)

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestTheDrillDown:
    async def test_the_pos_and_the_payments_with_credit(self) -> None:
        _auth()
        payment = SimpleNamespace(
            supplier_payment_id=3, serial=7, date=WHEN, method=3, amount=Decimal('4000.0000'),
            facility=1,
        )  # fmt: skip
        with (
            patch(
                f'{ROUTE}.accounts_service.detail',
                AsyncMock(return_value={'summary': _summary(), 'documents': [_document()]}),
            ),
            patch(
                f'{ROUTE}.supplier_payment_service.payments_with_credit',
                AsyncMock(return_value=[(payment, Decimal('1000.00'))]),
            ),
        ):
            async with _client() as client:
                response = await client.get(f'{BASE}/5')

        assert response.status_code == 200, response.text
        document = response.json()['documents'][0]
        assert document['serial'] is None
        assert document['facility'] is None and document['salesperson'] is None
        assert response.json()['payments'] == [
            {'id': 3, 'serial': 7, 'date': '2026-10-27T12:00:00', 'method': 3, 'currency': 0,
             'amount': '4000.00', 'unapplied': '1000.00'}
        ]  # fmt: skip

    async def test_an_unknown_supplier_is_404(self) -> None:
        _auth()
        missing = HTTPException(status_code=404, detail='Supplier not found')
        with patch(f'{ROUTE}.accounts_service.detail', AsyncMock(side_effect=missing)):
            async with _client() as client:
                response = await client.get(f'{BASE}/999')

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_without_read_on_19_is_403(self) -> None:
        _auth(rights=None)
        async with _client() as client:
            response = await client.get(f'{BASE}/5')

        assert response.status_code == status.HTTP_403_FORBIDDEN
