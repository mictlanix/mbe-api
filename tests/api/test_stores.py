"""Tests for /stores, /warehouses, /points-of-sale, and /cash-drawers endpoints."""

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
        user_id="tester", session_version=1, administrator=True, store_id=None
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


# ── Fake objects ──────────────────────────────────────────────────────────────


def _store(store_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        store_id=store_id,
        code="S1",
        name="Main Store",
        location="Downtown",
        address=1,
        taxpayer="RFC123456789A",
        logo="logo.png",
        receipt_message=None,
        default_batch=None,
        disabled=None,
    )


def _warehouse(warehouse_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        warehouse_id=warehouse_id, store=1, code="WH1", name="Main Warehouse",
        comment=None, disabled=None,
    )


def _pos(point_sale_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        point_sale_id=point_sale_id, store=1, code="POS1", name="Register 1",
        warehouse=1, comment=None, disabled=None,
    )


def _cash_drawer(cash_drawer_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        cash_drawer_id=cash_drawer_id, store=1, code="CD1", name="Drawer 1",
        comment=None, disabled=None,
    )


# ── Store tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_stores_returns_200() -> None:
    _auth()
    with patch(
        "app.services.store_service.list_stores",
        new=AsyncMock(return_value=([_store()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/stores")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["code"] == "S1"


@pytest.mark.asyncio
async def test_get_store_returns_200() -> None:
    _auth()
    with patch("app.services.store_service.get_store", new=AsyncMock(return_value=_store())):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/stores/1")
    assert r.status_code == 200
    assert r.json()["store_id"] == 1


@pytest.mark.asyncio
async def test_get_store_returns_404() -> None:
    _auth()
    with patch("app.services.store_service.get_store", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/stores/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_store_returns_201() -> None:
    _auth()
    with patch(
        "app.services.store_service.create_store", new=AsyncMock(return_value=_store())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/stores",
                json={
                    "code": "S1", "name": "Main Store", "location": "Downtown",
                    "address": 1, "taxpayer": "RFC123456789A", "logo": "logo.png",
                },
            )
    assert r.status_code == 201
    assert r.json()["code"] == "S1"


@pytest.mark.asyncio
async def test_update_store_returns_200() -> None:
    _auth()
    updated = _store()
    updated.name = "Branch Store"
    with (
        patch("app.services.store_service.get_store", new=AsyncMock(return_value=_store())),
        patch(
            "app.services.store_service.update_store", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/stores/1", json={"name": "Branch Store"})
    assert r.status_code == 200
    assert r.json()["name"] == "Branch Store"


@pytest.mark.asyncio
async def test_update_store_returns_404() -> None:
    _auth()
    with patch("app.services.store_service.get_store", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/stores/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_store_returns_204() -> None:
    _auth()
    with (
        patch("app.services.store_service.get_store", new=AsyncMock(return_value=_store())),
        patch("app.services.store_service.delete_store", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/stores/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_store_returns_404() -> None:
    _auth()
    with patch("app.services.store_service.get_store", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/stores/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_stores_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/stores")
    assert r.status_code == 401


# ── Warehouse tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_warehouses_returns_200() -> None:
    _auth()
    with patch(
        "app.services.warehouse_service.list_warehouses",
        new=AsyncMock(return_value=([_warehouse()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/warehouses")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_warehouse_returns_200() -> None:
    _auth()
    with patch(
        "app.services.warehouse_service.get_warehouse", new=AsyncMock(return_value=_warehouse())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/warehouses/1")
    assert r.status_code == 200
    assert r.json()["warehouse_id"] == 1


@pytest.mark.asyncio
async def test_get_warehouse_returns_404() -> None:
    _auth()
    with patch(
        "app.services.warehouse_service.get_warehouse", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/warehouses/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_warehouse_returns_201() -> None:
    _auth()
    with patch(
        "app.services.warehouse_service.create_warehouse",
        new=AsyncMock(return_value=_warehouse()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/warehouses", json={"store": 1, "code": "WH1", "name": "Main Warehouse"}
            )
    assert r.status_code == 201
    assert r.json()["code"] == "WH1"


@pytest.mark.asyncio
async def test_update_warehouse_returns_200() -> None:
    _auth()
    updated = _warehouse()
    updated.name = "Secondary Warehouse"
    with (
        patch(
            "app.services.warehouse_service.get_warehouse",
            new=AsyncMock(return_value=_warehouse()),
        ),
        patch(
            "app.services.warehouse_service.update_warehouse",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/warehouses/1", json={"name": "Secondary Warehouse"})
    assert r.status_code == 200
    assert r.json()["name"] == "Secondary Warehouse"


@pytest.mark.asyncio
async def test_update_warehouse_returns_404() -> None:
    _auth()
    with patch(
        "app.services.warehouse_service.get_warehouse", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/warehouses/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_warehouse_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.warehouse_service.get_warehouse",
            new=AsyncMock(return_value=_warehouse()),
        ),
        patch(
            "app.services.warehouse_service.delete_warehouse", new=AsyncMock(return_value=None)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/warehouses/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_warehouse_returns_404() -> None:
    _auth()
    with patch(
        "app.services.warehouse_service.get_warehouse", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/warehouses/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_warehouses_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/warehouses")
    assert r.status_code == 401


# ── Point of Sale tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_points_of_sale_returns_200() -> None:
    _auth()
    with patch(
        "app.services.point_sale_service.list_point_sales",
        new=AsyncMock(return_value=([_pos()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_point_of_sale_returns_200() -> None:
    _auth()
    with patch(
        "app.services.point_sale_service.get_point_sale", new=AsyncMock(return_value=_pos())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale/1")
    assert r.status_code == 200
    assert r.json()["point_sale_id"] == 1


@pytest.mark.asyncio
async def test_get_point_of_sale_returns_404() -> None:
    _auth()
    with patch(
        "app.services.point_sale_service.get_point_sale", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_point_of_sale_returns_201() -> None:
    _auth()
    with patch(
        "app.services.point_sale_service.create_point_sale", new=AsyncMock(return_value=_pos())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/points-of-sale",
                json={"store": 1, "code": "POS1", "name": "Register 1", "warehouse": 1},
            )
    assert r.status_code == 201
    assert r.json()["code"] == "POS1"


@pytest.mark.asyncio
async def test_update_point_of_sale_returns_200() -> None:
    _auth()
    updated = _pos()
    updated.name = "Register 2"
    with (
        patch(
            "app.services.point_sale_service.get_point_sale",
            new=AsyncMock(return_value=_pos()),
        ),
        patch(
            "app.services.point_sale_service.update_point_sale",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/points-of-sale/1", json={"name": "Register 2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Register 2"


@pytest.mark.asyncio
async def test_update_point_of_sale_returns_404() -> None:
    _auth()
    with patch(
        "app.services.point_sale_service.get_point_sale", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/points-of-sale/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_point_of_sale_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.point_sale_service.get_point_sale",
            new=AsyncMock(return_value=_pos()),
        ),
        patch(
            "app.services.point_sale_service.delete_point_sale",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/points-of-sale/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_point_of_sale_returns_404() -> None:
    _auth()
    with patch(
        "app.services.point_sale_service.get_point_sale", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/points-of-sale/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_points_of_sale_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/points-of-sale")
    assert r.status_code == 401


# ── Cash Drawer tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_cash_drawers_returns_200() -> None:
    _auth()
    with patch(
        "app.services.cash_drawer_service.list_cash_drawers",
        new=AsyncMock(return_value=([_cash_drawer()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_cash_drawer_returns_200() -> None:
    _auth()
    with patch(
        "app.services.cash_drawer_service.get_cash_drawer",
        new=AsyncMock(return_value=_cash_drawer()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers/1")
    assert r.status_code == 200
    assert r.json()["cash_drawer_id"] == 1


@pytest.mark.asyncio
async def test_get_cash_drawer_returns_404() -> None:
    _auth()
    with patch(
        "app.services.cash_drawer_service.get_cash_drawer", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_cash_drawer_returns_201() -> None:
    _auth()
    with patch(
        "app.services.cash_drawer_service.create_cash_drawer",
        new=AsyncMock(return_value=_cash_drawer()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/cash-drawers",
                json={"store": 1, "code": "CD1", "name": "Drawer 1"},
            )
    assert r.status_code == 201
    assert r.json()["code"] == "CD1"


@pytest.mark.asyncio
async def test_update_cash_drawer_returns_200() -> None:
    _auth()
    updated = _cash_drawer()
    updated.name = "Drawer 2"
    with (
        patch(
            "app.services.cash_drawer_service.get_cash_drawer",
            new=AsyncMock(return_value=_cash_drawer()),
        ),
        patch(
            "app.services.cash_drawer_service.update_cash_drawer",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/cash-drawers/1", json={"name": "Drawer 2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Drawer 2"


@pytest.mark.asyncio
async def test_update_cash_drawer_returns_404() -> None:
    _auth()
    with patch(
        "app.services.cash_drawer_service.get_cash_drawer", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/cash-drawers/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_cash_drawer_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.cash_drawer_service.get_cash_drawer",
            new=AsyncMock(return_value=_cash_drawer()),
        ),
        patch(
            "app.services.cash_drawer_service.delete_cash_drawer",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/cash-drawers/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_cash_drawer_returns_404() -> None:
    _auth()
    with patch(
        "app.services.cash_drawer_service.get_cash_drawer", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/cash-drawers/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_cash_drawers_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/cash-drawers")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_points_of_sale_store_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pos()], 1))
    with patch("app.services.point_sale_service.list_point_sales", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale?store=1")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("store") == 1


@pytest.mark.asyncio
async def test_list_points_of_sale_warehouse_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pos()], 1))
    with patch("app.services.point_sale_service.list_point_sales", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale?warehouse=4")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("warehouse") == 4


@pytest.mark.asyncio
async def test_list_points_of_sale_no_filters() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pos()], 1))
    with patch("app.services.point_sale_service.list_point_sales", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("store") is None
    assert kwargs.get("warehouse") is None


@pytest.mark.asyncio
async def test_list_cash_drawers_store_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_cash_drawer()], 1))
    with patch("app.services.cash_drawer_service.list_cash_drawers", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers?store=2")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("store") == 2


@pytest.mark.asyncio
async def test_list_cash_drawers_no_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_cash_drawer()], 1))
    with patch("app.services.cash_drawer_service.list_cash_drawers", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("store") is None
