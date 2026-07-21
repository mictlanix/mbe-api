"""Tests for /suppliers and /employees endpoints."""

import datetime
from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=None
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


# ── Fake objects ──────────────────────────────────────────────────────────────


def _supplier(supplier_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        supplier_id=supplier_id,
        code='SUP1',
        name='Supplier Co',
        zone=None,
        credit_limit=Decimal('0'),
        credit_days=0,
        comment=None,
    )


def _employee(employee_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        employee_id=employee_id,
        first_name='John',
        last_name='Doe',
        nickname='jdoe',
        gender=1,
        birthday=datetime.date(1990, 1, 1),
        taxpayer_id=None,
        sales_person=False,
        status=0,
        personal_id=None,
        start_job_date=datetime.date(2020, 1, 1),
        enroll_number=None,
        comment=None,
    )


# ── Supplier tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_suppliers_returns_200() -> None:
    _auth()
    with patch(
        'app.services.supplier_service.list_suppliers',
        new=AsyncMock(return_value=([_supplier()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/suppliers')
    assert r.status_code == 200
    assert r.json()['total'] == 1
    assert r.json()['items'][0]['code'] == 'SUP1'


@pytest.mark.asyncio
async def test_get_supplier_returns_200() -> None:
    _auth()
    with patch(
        'app.services.supplier_service.get_supplier', new=AsyncMock(return_value=_supplier())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/suppliers/1')
    assert r.status_code == 200
    assert r.json()['supplier_id'] == 1


@pytest.mark.asyncio
async def test_get_supplier_returns_404() -> None:
    _auth()
    with patch('app.services.supplier_service.get_supplier', new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/suppliers/999')
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_supplier_returns_201() -> None:
    _auth()
    with patch(
        'app.services.supplier_service.create_supplier',
        new=AsyncMock(return_value=_supplier()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.post(
                '/api/v1/suppliers',
                json={'code': 'SUP1', 'name': 'Supplier Co'},
            )
    assert r.status_code == 201
    assert r.json()['code'] == 'SUP1'


@pytest.mark.asyncio
async def test_update_supplier_returns_200() -> None:
    _auth()
    updated = _supplier()
    updated.name = 'Supplier Ltd'
    with (
        patch(
            'app.services.supplier_service.get_supplier',
            new=AsyncMock(return_value=_supplier()),
        ),
        patch(
            'app.services.supplier_service.update_supplier',
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.put('/api/v1/suppliers/1', json={'name': 'Supplier Ltd'})
    assert r.status_code == 200
    assert r.json()['name'] == 'Supplier Ltd'


@pytest.mark.asyncio
async def test_update_supplier_returns_404() -> None:
    _auth()
    with patch('app.services.supplier_service.get_supplier', new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.put('/api/v1/suppliers/999', json={'name': 'X'})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_supplier_returns_204() -> None:
    _auth()
    with (
        patch(
            'app.services.supplier_service.get_supplier',
            new=AsyncMock(return_value=_supplier()),
        ),
        patch('app.services.supplier_service.delete_supplier', new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.delete('/api/v1/suppliers/1')
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_supplier_returns_404() -> None:
    _auth()
    with patch('app.services.supplier_service.get_supplier', new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.delete('/api/v1/suppliers/999')
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_suppliers_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.get('/api/v1/suppliers')
    assert r.status_code == 401


# ── Employee tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_employees_returns_200() -> None:
    _auth()
    with patch(
        'app.services.employee_service.list_employees',
        new=AsyncMock(return_value=([_employee()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/employees')
    assert r.status_code == 200
    assert r.json()['total'] == 1
    assert r.json()['items'][0]['first_name'] == 'John'


@pytest.mark.asyncio
async def test_get_employee_returns_200() -> None:
    _auth()
    with patch(
        'app.services.employee_service.get_employee', new=AsyncMock(return_value=_employee())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/employees/1')
    assert r.status_code == 200
    assert r.json()['employee_id'] == 1
    assert 'active' not in r.json()
    assert 'disabled' not in r.json()


@pytest.mark.asyncio
async def test_get_employee_returns_404() -> None:
    _auth()
    with patch('app.services.employee_service.get_employee', new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/employees/999')
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_employee_returns_201() -> None:
    _auth()
    with patch(
        'app.services.employee_service.create_employee',
        new=AsyncMock(return_value=_employee()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.post(
                '/api/v1/employees',
                json={
                    'first_name': 'John',
                    'last_name': 'Doe',
                    'nickname': 'jdoe',
                    'gender': 1,
                    'birthday': '1990-01-01',
                    'start_job_date': '2020-01-01',
                },
            )
    assert r.status_code == 201
    assert r.json()['first_name'] == 'John'
    assert r.json()['status'] == 0


@pytest.mark.asyncio
async def test_update_employee_returns_200() -> None:
    _auth()
    updated = _employee()
    updated.first_name = 'Jane'
    with (
        patch(
            'app.services.employee_service.get_employee',
            new=AsyncMock(return_value=_employee()),
        ),
        patch(
            'app.services.employee_service.update_employee',
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.put('/api/v1/employees/1', json={'first_name': 'Jane'})
    assert r.status_code == 200
    assert r.json()['first_name'] == 'Jane'


@pytest.mark.asyncio
async def test_update_employee_returns_404() -> None:
    _auth()
    with patch('app.services.employee_service.get_employee', new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.put('/api/v1/employees/999', json={'first_name': 'X'})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_employee_returns_204() -> None:
    _auth()
    with (
        patch(
            'app.services.employee_service.get_employee',
            new=AsyncMock(return_value=_employee()),
        ),
        patch('app.services.employee_service.delete_employee', new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.delete('/api/v1/employees/1')
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_employee_returns_404() -> None:
    _auth()
    with patch('app.services.employee_service.get_employee', new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.delete('/api/v1/employees/999')
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_employees_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.get('/api/v1/employees')
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_employees_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_employee()], 1))
    with patch('app.services.employee_service.list_employees', new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/employees?status=0')
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get('status') == 0


@pytest.mark.asyncio
async def test_list_employees_no_status_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_employee()], 1))
    with patch('app.services.employee_service.list_employees', new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/employees')
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get('status') is None


@pytest.mark.asyncio
async def test_list_employees_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.get('/api/v1/employees?status=9')
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_employee_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.post(
            '/api/v1/employees',
            json={
                'first_name': 'John',
                'last_name': 'Doe',
                'nickname': 'jdoe',
                'gender': 1,
                'birthday': '1990-01-01',
                'start_job_date': '2020-01-01',
                'status': 5,
            },
        )
    assert r.status_code == 422
