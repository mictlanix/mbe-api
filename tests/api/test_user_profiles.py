"""Spec 014 endpoint contracts: status codes and authorisation, with the service patched out.

This layer's job is the envelope — who may call, what a missing id answers, which refusal maps to
which code. The behaviour behind it (the 103-row write, the sparse/dense translation, the
collation trap) is covered in `tests/unit/` and `tests/integration/`, because a patched service
cannot show it.

Every route is `require_admin`, matching the existing `/users` endpoints — no new `SystemObject`
gates any of this (FR-028).
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user, require_admin
from app.db.session import get_db
from app.enums import EntityStatus
from app.main import app
from app.models.user import User, UserProfile, UserProfilePrivilege

BASE = '/api/v1/user-profiles'


def _profile(
    profile_id: int = 1, *, status: int = 0, masks: dict[int, int] | None = None
) -> UserProfile:
    profile = UserProfile(
        user_profile_id=profile_id,
        name='Cashier',
        description='Till operator',
        status=status,
    )
    profile.privileges = [
        UserProfilePrivilege(system_object=obj, privileges=mask)
        for obj, mask in (masks or {0: 2}).items()
    ]
    return profile


def _user(user_id: str = 'jdoe') -> User:
    user = User(
        user_id=user_id,
        password='hash',
        email=f'{user_id}@example.com',
        employee_id=7,
        administrator=False,
        status=EntityStatus.ACTIVE,
        session_version=1,
        profile_id=1,
    )
    user.settings = None
    user.privileges = []
    return user


def _admin() -> CurrentUser:
    return CurrentUser(
        user_id='admin',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=1,
        point_sale_id=None,
        cash_drawer_id=None,
    )


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """An administrator caller with the database dependency stubbed."""
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = _admin
    app.dependency_overrides[require_admin] = _admin
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
            yield http
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def anonymous() -> AsyncGenerator[AsyncClient, None]:
    """No auth overrides at all, so `get_current_user` runs and refuses."""
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
            yield http
    finally:
        app.dependency_overrides.clear()


class TestListAndRead:
    @pytest.mark.asyncio
    async def test_list_returns_items_and_total(self, client: AsyncClient) -> None:
        with patch(
            'app.services.user_profile_service.list_profiles',
            AsyncMock(return_value=([_profile()], 1)),
        ):
            response = await client.get(BASE)
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 1
        assert response.json()['items'][0]['name'] == 'Cashier'

    @pytest.mark.asyncio
    async def test_get_returns_the_entry_set(self, client: AsyncClient) -> None:
        with patch(
            'app.services.user_profile_service.get_profile',
            AsyncMock(return_value=_profile(masks={0: 2, 7: 3})),
        ):
            response = await client.get(f'{BASE}/1')
        assert response.status_code == 200, response.text
        assert {e['system_object'] for e in response.json()['privileges']} == {0, 7}

    @pytest.mark.asyncio
    async def test_get_unknown_id_is_404(self, client: AsyncClient) -> None:
        with patch(
            'app.services.user_profile_service.get_profile', AsyncMock(return_value=None)
        ):
            response = await client.get(f'{BASE}/999')
        assert response.status_code == 404
        assert response.json()['detail'] == 'Profile not found'


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_returns_201(self, client: AsyncClient) -> None:
        with patch(
            'app.services.user_profile_service.create_profile',
            AsyncMock(return_value=_profile()),
        ):
            response = await client.post(BASE, json={'name': 'Cashier'})
        assert response.status_code == 201, response.text

    @pytest.mark.asyncio
    async def test_an_unknown_system_object_is_422(self, client: AsyncClient) -> None:
        response = await client.post(
            BASE, json={'name': 'X', 'privileges': [{'system_object': 104, 'privileges': 2}]}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_mask_above_15_is_422(self, client: AsyncClient) -> None:
        response = await client.post(
            BASE, json={'name': 'X', 'privileges': [{'system_object': 0, 'privileges': 99}]}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_duplicate_object_is_422(self, client: AsyncClient) -> None:
        response = await client.post(
            BASE,
            json={
                'name': 'X',
                'privileges': [
                    {'system_object': 0, 'privileges': 2},
                    {'system_object': 0, 'privileges': 15},
                ],
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_an_empty_name_is_422(self, client: AsyncClient) -> None:
        assert (await client.post(BASE, json={'name': ''})).status_code == 422


class TestUpdateAndDelete:
    @pytest.mark.asyncio
    async def test_update_returns_200(self, client: AsyncClient) -> None:
        with (
            patch(
                'app.services.user_profile_service.get_profile',
                AsyncMock(return_value=_profile()),
            ),
            patch(
                'app.services.user_profile_service.update_profile',
                AsyncMock(return_value=_profile()),
            ),
        ):
            response = await client.put(f'{BASE}/1', json={'name': 'Head Cashier'})
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_update_unknown_id_is_404(self, client: AsyncClient) -> None:
        with patch('app.services.user_profile_service.get_profile', AsyncMock(return_value=None)):
            response = await client.put(f'{BASE}/999', json={'name': 'X'})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_returns_204(self, client: AsyncClient) -> None:
        with (
            patch(
                'app.services.user_profile_service.get_profile',
                AsyncMock(return_value=_profile()),
            ),
            patch('app.services.user_profile_service.delete_profile', AsyncMock()),
        ):
            response = await client.delete(f'{BASE}/1')
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_unknown_id_is_404(self, client: AsyncClient) -> None:
        with patch('app.services.user_profile_service.get_profile', AsyncMock(return_value=None)):
            response = await client.delete(f'{BASE}/999')
        assert response.status_code == 404


class TestApply:
    @pytest.mark.asyncio
    async def test_apply_returns_the_updated_user(self, client: AsyncClient) -> None:
        from app.schemas.user import UserResponse

        with (
            patch(
                'app.services.user_profile_service.get_profile',
                AsyncMock(return_value=_profile()),
            ),
            patch('app.services.user_service.get_user', AsyncMock(return_value=_user())),
            patch('app.services.user_service.apply_profile', AsyncMock(return_value=_user())),
            patch(
                'app.services.user_service.to_response',
                AsyncMock(return_value=UserResponse.model_validate(_user())),
            ),
        ):
            response = await client.post(f'{BASE}/1/apply/jdoe')
        assert response.status_code == 200, response.text
        assert response.json()['user_id'] == 'jdoe'

    @pytest.mark.asyncio
    async def test_apply_unknown_profile_is_404(self, client: AsyncClient) -> None:
        with patch('app.services.user_profile_service.get_profile', AsyncMock(return_value=None)):
            response = await client.post(f'{BASE}/999/apply/jdoe')
        assert response.status_code == 404
        assert response.json()['detail'] == 'Profile not found'

    @pytest.mark.asyncio
    async def test_apply_unknown_user_is_404(self, client: AsyncClient) -> None:
        with (
            patch(
                'app.services.user_profile_service.get_profile',
                AsyncMock(return_value=_profile()),
            ),
            patch('app.services.user_service.get_user', AsyncMock(return_value=None)),
        ):
            response = await client.post(f'{BASE}/1/apply/nobody')
        assert response.status_code == 404
        assert response.json()['detail'] == 'User not found'

    @pytest.mark.asyncio
    async def test_apply_inactive_profile_is_409(self, client: AsyncClient) -> None:
        """FR-017. 409 not 422: the request is well formed, the resource's state refuses."""
        with patch(
            'app.services.user_profile_service.get_profile',
            AsyncMock(return_value=_profile(status=EntityStatus.INACTIVE)),
        ):
            response = await client.post(f'{BASE}/1/apply/jdoe')
        assert response.status_code == 409
        assert response.json()['detail'] == 'Profile is not active'

    @pytest.mark.asyncio
    async def test_an_inactive_profile_is_refused_before_the_user_is_looked_up(
        self, client: AsyncClient
    ) -> None:
        """Ordering matters: nothing about the target is touched once the profile refuses."""
        get_user = AsyncMock(return_value=_user())
        with (
            patch(
                'app.services.user_profile_service.get_profile',
                AsyncMock(return_value=_profile(status=EntityStatus.INACTIVE)),
            ),
            patch('app.services.user_service.get_user', get_user),
        ):
            await client.post(f'{BASE}/1/apply/jdoe')
        get_user.assert_not_awaited()


class TestAuthorisation:
    """FR-028, FR-029 — every route, both refusals."""

    ROUTES = [
        ('get', BASE),
        ('post', BASE),
        ('get', f'{BASE}/1'),
        ('put', f'{BASE}/1'),
        ('delete', f'{BASE}/1'),
        ('post', f'{BASE}/1/apply/jdoe'),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(('method', 'path'), ROUTES)
    async def test_unauthenticated_is_401(
        self, anonymous: AsyncClient, method: str, path: str
    ) -> None:
        # `request` rather than the per-verb helpers: httpx's get/delete take no json body
        response = await anonymous.request(method, path, json={'name': 'X'})
        assert response.status_code == 401, f'{method.upper()} {path} → {response.status_code}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize(('method', 'path'), ROUTES)
    async def test_non_administrator_is_403(self, method: str, path: str) -> None:
        non_admin = CurrentUser(
            user_id='clerk',
            session_version=1,
            administrator=False,
            facility_id=1,
            employee_id=2,
            point_sale_id=None,
            cash_drawer_id=None,
        )
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        app.dependency_overrides[get_current_user] = lambda: non_admin
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url='http://test'
            ) as http:
                response = await http.request(method, path, json={'name': 'X'})
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 403, f'{method.upper()} {path} → {response.status_code}'
