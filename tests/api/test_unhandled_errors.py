"""An unhandled exception reaches a browser as a readable 500, not a network error (#266)."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=None, employee_id=7
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


@pytest.mark.asyncio
async def test_an_unhandled_exception_is_a_json_500_with_cors_headers() -> None:
    _auth()
    with patch(
        'app.services.warehouse_service.get_warehouse',
        new=AsyncMock(side_effect=RuntimeError('disk on fire')),
    ):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url='http://test') as c:
            r = await c.get('/api/v1/warehouses/1', headers={'Origin': 'http://localhost:5000'})

    assert r.status_code == 500
    assert r.headers['access-control-allow-origin'] == '*'
    assert r.json() == {'detail': 'Internal server error'}
    assert 'disk on fire' not in r.text
