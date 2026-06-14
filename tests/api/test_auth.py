from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.models.user import User


def _make_user(
    user_id: str = "jdoe",
    administrator: bool = False,
    session_version: int = 1,
    disabled: bool = False,
) -> User:
    user = User(
        user_id=user_id,
        password="hash",
        email=f"{user_id}@example.com",
        employee_id=None,
        administrator=administrator,
        disabled=disabled,
        session_version=session_version,
    )
    user.settings = None
    user.privileges = []
    return user


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
    user = _make_user(user_id="jdoe", administrator=False, session_version=1)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user.user_id,
        session_version=user.session_version,
        administrator=False,
        store_id=None,
    )
    app.dependency_overrides[get_db] = _db_override(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer dummy"})

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "jdoe"
    assert body["administrator"] is False
    assert body["disabled"] is False
    assert body["session_version"] == 1
    assert body["settings"] is None
    assert body["privileges"] == []


@pytest.mark.asyncio
async def test_auth_me_returns_own_profile_for_admin() -> None:
    user = _make_user(user_id="admin", administrator=True, session_version=2)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user.user_id,
        session_version=user.session_version,
        administrator=True,
        store_id=1,
    )
    app.dependency_overrides[get_db] = _db_override(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer dummy"})

    assert response.status_code == 200
    assert response.json()["administrator"] is True


@pytest.mark.asyncio
async def test_auth_me_requires_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_rejects_stale_session_version() -> None:
    stored_user = _make_user(user_id="jdoe", session_version=2)
    token = create_access_token(
        user_id="jdoe", session_version=1, administrator=False, store_id=None
    )
    app.dependency_overrides[get_db] = _db_override(stored_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_rejects_disabled_user() -> None:
    stored_user = _make_user(user_id="jdoe", session_version=1, disabled=True)
    token = create_access_token(
        user_id="jdoe", session_version=1, administrator=False, store_id=None
    )
    app.dependency_overrides[get_db] = _db_override(stored_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
