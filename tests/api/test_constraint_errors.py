"""Constraint violations reach the client as 409, never 500 (#107)."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app


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


def _warehouse() -> SimpleNamespace:
    return SimpleNamespace(
        warehouse_id=1, facility=1, code='WH1', name='Main', comment=None, status=0
    )


# ── Class A: delete refused while referenced ──────────────────────────────────


@pytest.mark.asyncio
async def test_delete_referenced_record_returns_409_naming_the_blockers() -> None:
    _auth()
    refused = HTTPException(
        status_code=409, detail='Still referenced by point_sale (3) — remove those records first'
    )
    with (
        patch(
            'app.services.warehouse_service.get_warehouse',
            new=AsyncMock(return_value=_warehouse()),
        ),
        patch(
            'app.services.warehouse_service.delete_warehouse',
            new=AsyncMock(side_effect=refused),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.delete('/api/v1/warehouses/1')
    assert r.status_code == 409
    assert 'point_sale (3)' in r.json()['detail']


# ── Class B: duplicate unique key ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_code_returns_409() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='Warehouse code already exists')
    with patch(
        'app.services.warehouse_service.create_warehouse', new=AsyncMock(side_effect=conflict)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.post(
                '/api/v1/warehouses', json={'facility': 1, 'code': 'WH1', 'name': 'Duplicate'}
            )
    assert r.status_code == 409
    assert r.json()['detail'] == 'Warehouse code already exists'


# ── The backstop, for constraints no service checks up front ──────────────────


@pytest.mark.asyncio
async def test_unchecked_integrity_error_becomes_409_not_500() -> None:
    _auth()
    boom = IntegrityError(
        'INSERT INTO warehouse ...',
        {},
        Exception("(1062, \"Duplicate entry 'WH1' for key 'code_UNIQUE'\")"),
    )
    with patch('app.services.warehouse_service.create_warehouse', new=AsyncMock(side_effect=boom)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.post(
                '/api/v1/warehouses', json={'facility': 1, 'code': 'WH1', 'name': 'Duplicate'}
            )
    assert r.status_code == 409
    assert r.json() == {'detail': 'The request conflicts with existing data'}


@pytest.mark.asyncio
async def test_backstop_does_not_leak_the_driver_message() -> None:
    """The driver names tables, columns and index names — not for an API consumer."""
    _auth()
    boom = IntegrityError(
        'INSERT INTO warehouse ...',
        {},
        Exception("(1062, \"Duplicate entry 'WH1' for key 'code_UNIQUE'\")"),
    )
    with patch('app.services.warehouse_service.create_warehouse', new=AsyncMock(side_effect=boom)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.post(
                '/api/v1/warehouses', json={'facility': 1, 'code': 'WH1', 'name': 'Duplicate'}
            )
    body = r.text
    assert 'code_UNIQUE' not in body
    assert '1062' not in body
    assert 'warehouse' not in body.lower() or 'conflicts with existing data' in body
