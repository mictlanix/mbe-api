"""Tests for the /api/v1/addresses endpoint."""

from collections.abc import Generator
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
        user_id="tester", session_version=1, administrator=True, facility_id=None
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


# ── Fake objects ──────────────────────────────────────────────────────────────


def _address(address_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        address_id=address_id,
        nickname="Home",
        type=1,
        street="Main St",
        exterior_number="123",
        interior_number=None,
        postal_code="06000",
        neighborhood="Centro",
        locality=None,
        borough="Cuauhtemoc",
        state="CDMX",
        city="Mexico City",
        country="MX",
        url_address=None,
        comment=None,
        status=0,
    )


# ── Address tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_addresses_returns_200() -> None:
    _auth()
    with patch(
        "app.services.address_service.list_addresses",
        new=AsyncMock(return_value=([_address()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/addresses")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["street"] == "Main St"


@pytest.mark.asyncio
async def test_get_address_returns_200() -> None:
    _auth()
    with patch(
        "app.services.address_service.get_address", new=AsyncMock(return_value=_address())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/addresses/1")
    assert r.status_code == 200
    assert r.json()["address_id"] == 1


@pytest.mark.asyncio
async def test_get_address_returns_404() -> None:
    _auth()
    with patch("app.services.address_service.get_address", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/addresses/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_address_returns_201() -> None:
    _auth()
    with patch(
        "app.services.address_service.create_address", new=AsyncMock(return_value=_address())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/addresses",
                json={
                    "street": "Main St", "exterior_number": "123", "postal_code": "06000",
                    "neighborhood": "Centro", "borough": "Cuauhtemoc", "state": "CDMX",
                    "country": "MX",
                },
            )
    assert r.status_code == 201
    assert r.json()["street"] == "Main St"
    assert r.json()["status"] == 0


@pytest.mark.asyncio
async def test_update_address_returns_200() -> None:
    _auth()
    updated = _address()
    updated.street = "Second St"
    with (
        patch(
            "app.services.address_service.get_address", new=AsyncMock(return_value=_address())
        ),
        patch(
            "app.services.address_service.update_address", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/addresses/1", json={"street": "Second St"})
    assert r.status_code == 200
    assert r.json()["street"] == "Second St"


@pytest.mark.asyncio
async def test_update_address_returns_404() -> None:
    _auth()
    with patch("app.services.address_service.get_address", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/addresses/999", json={"street": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_address_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.address_service.get_address", new=AsyncMock(return_value=_address())
        ),
        patch("app.services.address_service.delete_address", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/addresses/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_address_returns_404() -> None:
    _auth()
    with patch("app.services.address_service.get_address", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/addresses/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_addresses_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/addresses")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_addresses_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_address()], 1))
    with patch("app.services.address_service.list_addresses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/addresses?search=abc")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "abc"


@pytest.mark.asyncio
async def test_list_addresses_type_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_address()], 1))
    with patch("app.services.address_service.list_addresses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/addresses?type=1")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("type") == 1


@pytest.mark.asyncio
async def test_list_addresses_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_address()], 1))
    with patch("app.services.address_service.list_addresses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/addresses?status=0")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 0


@pytest.mark.asyncio
async def test_list_addresses_no_filters() -> None:
    _auth()
    mock = AsyncMock(return_value=([_address()], 1))
    with patch("app.services.address_service.list_addresses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/addresses")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") is None
    assert kwargs.get("type") is None
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_addresses_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/addresses?status=9")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_addresses_invalid_type_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/addresses?type=9")
    assert r.status_code == 422
