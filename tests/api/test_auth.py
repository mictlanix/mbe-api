from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user, require_admin
from app.core.security import create_access_token
from app.db.session import get_db
from app.enums import EntityStatus
from app.main import app
from app.models.core import CashDrawer, Facility, PointSale
from app.models.user import User, UserSettings


def _make_user(
    user_id: str = 'jdoe',
    administrator: bool = False,
    session_version: int = 1,
    status: int = 0,
) -> User:
    user = User(
        user_id=user_id,
        password='hash',
        email=f'{user_id}@example.com',
        employee_id=None,
        administrator=administrator,
        status=status,
        session_version=session_version,
    )
    user.settings = None
    user.privileges = []
    return user


def _make_settings(
    user_id: str = 'jdoe',
    point_sale: PointSale | None = None,
    cash_drawer: CashDrawer | None = None,
) -> UserSettings:
    settings = UserSettings(user_id=user_id, facility_id=51)
    settings.facility = Facility(facility_id=51, code='CMZ', name='CASA MAESTRA ZUMPANGO')
    settings.point_sale = point_sale
    settings.cash_drawer = cash_drawer
    settings.point_sale_id = point_sale.point_sale_id if point_sale else None
    settings.cash_drawer_id = cash_drawer.cash_drawer_id if cash_drawer else None
    return settings


class _FakeSession:
    def __init__(self, user: User | None) -> None:
        self._user = user

    async def get(self, model: type, pk: Any) -> User | None:
        if self._user is not None and pk == self._user.user_id:
            return self._user
        return None


def _db_override(user: User | None) -> Any:
    async def _get_db() -> AsyncGenerator[_FakeSession, None]:
        yield _FakeSession(user)

    return _get_db


@pytest.fixture(autouse=True)
def _clear_overrides() -> AsyncGenerator[None, None]:
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_auth_me_returns_own_profile_for_non_admin() -> None:
    user = _make_user(user_id='jdoe', administrator=False, session_version=1)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user.user_id,
        session_version=user.session_version,
        administrator=False,
        facility_id=None,
    )
    app.dependency_overrides[get_db] = _db_override(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer dummy'})

    assert response.status_code == 200
    body = response.json()
    assert body['user_id'] == 'jdoe'
    assert body['administrator'] is False
    assert body['status'] == 0
    assert 'disabled' not in body
    assert body['session_version'] == 1
    assert body['settings'] is None
    assert body['privileges'] == []


@pytest.mark.asyncio
async def test_auth_me_returns_own_profile_for_admin() -> None:
    user = _make_user(user_id='admin', administrator=True, session_version=2)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user.user_id,
        session_version=user.session_version,
        administrator=True,
        facility_id=1,
    )
    app.dependency_overrides[get_db] = _db_override(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer dummy'})

    assert response.status_code == 200
    assert response.json()['administrator'] is True


@pytest.mark.asyncio
async def test_auth_me_includes_location_names() -> None:
    user = _make_user(user_id='jdoe')
    user.settings = _make_settings(
        user_id='jdoe',
        point_sale=PointSale(point_sale_id=18, code='01', name='PV ZUMPANGO'),
        cash_drawer=CashDrawer(cash_drawer_id=14, code='01', name='CC ZUMPANGO'),
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user.user_id, session_version=1, administrator=False, facility_id=51
    )
    app.dependency_overrides[get_db] = _db_override(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer dummy'})

    assert response.status_code == 200
    settings = response.json()['settings']
    assert settings['facility_id'] == 51
    assert settings['facility_code'] == 'CMZ'
    assert settings['facility_name'] == 'CASA MAESTRA ZUMPANGO'
    assert settings['point_sale_id'] == 18
    assert settings['point_sale_code'] == '01'
    assert settings['point_sale_name'] == 'PV ZUMPANGO'
    assert settings['cash_drawer_id'] == 14
    assert settings['cash_drawer_code'] == '01'
    assert settings['cash_drawer_name'] == 'CC ZUMPANGO'


@pytest.mark.asyncio
async def test_auth_me_omits_names_for_unset_locations() -> None:
    user = _make_user(user_id='jdoe')
    user.settings = _make_settings(user_id='jdoe', point_sale=None, cash_drawer=None)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user.user_id, session_version=1, administrator=False, facility_id=51
    )
    app.dependency_overrides[get_db] = _db_override(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer dummy'})

    assert response.status_code == 200
    settings = response.json()['settings']
    assert settings['facility_name'] == 'CASA MAESTRA ZUMPANGO'
    assert settings['point_sale_id'] is None
    assert settings['point_sale_code'] is None
    assert settings['point_sale_name'] is None
    assert settings['cash_drawer_id'] is None
    assert settings['cash_drawer_code'] is None
    assert settings['cash_drawer_name'] is None


@pytest.mark.asyncio
async def test_auth_me_requires_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me')

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_rejects_stale_session_version() -> None:
    stored_user = _make_user(user_id='jdoe', session_version=2)
    token = create_access_token(
        user_id='jdoe', session_version=1, administrator=False, facility_id=None
    )
    app.dependency_overrides[get_db] = _db_override(stored_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me', headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_rejects_disabled_user() -> None:
    stored_user = _make_user(user_id='jdoe', session_version=1, status=1)
    token = create_access_token(
        user_id='jdoe', session_version=1, administrator=False, facility_id=None
    )
    app.dependency_overrides[get_db] = _db_override(stored_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me', headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_user_rejects_invalid_status() -> None:
    app.dependency_overrides[require_admin] = lambda: CurrentUser(
        user_id='admin', session_version=1, administrator=True, facility_id=None
    )
    app.dependency_overrides[get_db] = _db_override(None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/api/v1/users',
            json={
                'user_id': 'newuser1',
                'password': 'secret',
                'email': 'new@example.com',
                'status': 5,
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_users_filters_by_status() -> None:
    app.dependency_overrides[require_admin] = lambda: CurrentUser(
        user_id='admin', session_version=1, administrator=True, facility_id=None
    )
    app.dependency_overrides[get_db] = _db_override(None)
    inactive = _make_user(user_id='jdoe', status=1)

    with patch(
        'app.services.user_service.list_users',
        new=AsyncMock(return_value=([inactive], 1)),
    ) as mock_list:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/v1/users?status=1')

    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 1
    assert body['items'][0]['status'] == 1
    assert mock_list.await_args.kwargs['status'] == EntityStatus.INACTIVE


@pytest.mark.asyncio
async def test_list_users_rejects_invalid_status_filter() -> None:
    app.dependency_overrides[require_admin] = lambda: CurrentUser(
        user_id='admin', session_version=1, administrator=True, facility_id=None
    )
    app.dependency_overrides[get_db] = _db_override(None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/users?status=9')

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_auth_me_rejects_archived_user() -> None:
    stored_user = _make_user(user_id='jdoe', session_version=1, status=2)
    token = create_access_token(
        user_id='jdoe', session_version=1, administrator=False, facility_id=None
    )
    app.dependency_overrides[get_db] = _db_override(stored_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/auth/me', headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == 401
