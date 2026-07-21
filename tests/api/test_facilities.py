"""Tests for /facilities, /warehouses, /points-of-sale, and /cash-drawers endpoints."""

import io
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

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


def _make_png_bytes(width: int = 200, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Fake objects ──────────────────────────────────────────────────────────────


def _facility_summary(facility_id: int = 1) -> SimpleNamespace:
    """Flat Facility shape (as embedded in Warehouse/PointSale/CashDrawer FK fields)."""
    return SimpleNamespace(
        facility_id=facility_id,
        code="S1",
        name="Main Store",
        type=0,
        location="06000",
        address=1,
        taxpayer="RFC123456789A",
        logo="logo.png",
        receipt_message=None,
        default_batch=None,
        status=0,
    )


def _facility(facility_id: int = 1) -> SimpleNamespace:
    """Top-level Facility shape (as returned by /facilities) — location expanded to a SAT object."""
    summary = _facility_summary(facility_id)
    summary.location = {"id": "06000", "description": "CDMX"}
    return summary


def _warehouse_summary(warehouse_id: int = 1) -> SimpleNamespace:
    """Flat Warehouse shape (as embedded in PointSale/PaymentMethodOption FK fields)."""
    return SimpleNamespace(
        warehouse_id=warehouse_id, facility=1, code="WH1", name="Main Warehouse",
        comment=None, status=0,
    )


def _warehouse(warehouse_id: int = 1) -> SimpleNamespace:
    """Top-level Warehouse shape (as returned by /warehouses) — facility expanded."""
    w = _warehouse_summary(warehouse_id)
    w.facility = _facility_summary()
    return w


def _pos(point_sale_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        point_sale_id=point_sale_id, facility=_facility_summary(), code="POS1", name="Register 1",
        warehouse=_warehouse_summary(), comment=None, status=0,
    )


def _cash_drawer(cash_drawer_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        cash_drawer_id=cash_drawer_id, facility=_facility_summary(), code="CD1", name="Drawer 1",
        comment=None, status=0,
    )


# ── Facility tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_facilities_returns_200() -> None:
    _auth()
    with patch(
        "app.services.facility_service.list_facilities",
        new=AsyncMock(return_value=([_facility()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/facilities")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["code"] == "S1"


@pytest.mark.asyncio
async def test_get_facility_returns_200() -> None:
    _auth()
    with patch(
        "app.services.facility_service.get_facility", new=AsyncMock(return_value=_facility())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/facilities/1")
    assert r.status_code == 200
    assert r.json()["facility_id"] == 1
    assert "disabled" not in r.json()


@pytest.mark.asyncio
async def test_get_facility_returns_404() -> None:
    _auth()
    with patch("app.services.facility_service.get_facility", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/facilities/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_facility_returns_201() -> None:
    _auth()
    with patch(
        "app.services.facility_service.create_facility", new=AsyncMock(return_value=_facility())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/facilities",
                json={
                    "code": "S1", "name": "Main Store", "type": 0, "location": "Downtown",
                    "address": 1, "taxpayer": "RFC123456789A", "logo": "logo.png",
                },
            )
    assert r.status_code == 201
    assert r.json()["code"] == "S1"
    assert r.json()["status"] == 0


@pytest.mark.asyncio
async def test_update_facility_returns_200() -> None:
    _auth()
    updated = _facility()
    updated.name = "Branch Store"
    with (
        patch(
            "app.services.facility_service.get_facility", new=AsyncMock(return_value=_facility())
        ),
        patch(
            "app.services.facility_service.update_facility", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/facilities/1", json={"name": "Branch Store"})
    assert r.status_code == 200
    assert r.json()["name"] == "Branch Store"


@pytest.mark.asyncio
async def test_update_facility_returns_404() -> None:
    _auth()
    with patch("app.services.facility_service.get_facility", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/facilities/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_facility_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.facility_service.get_facility", new=AsyncMock(return_value=_facility())
        ),
        patch("app.services.facility_service.delete_facility", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/facilities/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_facility_returns_404() -> None:
    _auth()
    with patch("app.services.facility_service.get_facility", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/facilities/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_facilities_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/facilities")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_facilities_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_facility()], 1))
    with patch("app.services.facility_service.list_facilities", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/facilities?status=0")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 0


@pytest.mark.asyncio
async def test_list_facilities_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_facility()], 1))
    with patch("app.services.facility_service.list_facilities", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/facilities?search=abc")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "abc"


@pytest.mark.asyncio
async def test_list_facilities_no_status_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_facility()], 1))
    with patch("app.services.facility_service.list_facilities", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/facilities")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_facilities_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/facilities?status=9")
    assert r.status_code == 422


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
                "/api/v1/warehouses", json={"facility": 1, "code": "WH1", "name": "Main Warehouse"}
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


@pytest.mark.asyncio
async def test_list_warehouses_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_warehouse()], 1))
    with patch("app.services.warehouse_service.list_warehouses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/warehouses?status=1")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 1


@pytest.mark.asyncio
async def test_list_warehouses_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_warehouse()], 1))
    with patch("app.services.warehouse_service.list_warehouses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/warehouses?search=abc")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "abc"


@pytest.mark.asyncio
async def test_list_warehouses_no_status_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_warehouse()], 1))
    with patch("app.services.warehouse_service.list_warehouses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/warehouses")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_warehouses_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/warehouses?status=9")
    assert r.status_code == 422


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
                json={"facility": 1, "code": "POS1", "name": "Register 1", "warehouse": 1},
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
                json={"facility": 1, "code": "CD1", "name": "Drawer 1"},
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
async def test_list_points_of_sale_facility_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pos()], 1))
    with patch("app.services.point_sale_service.list_point_sales", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale?facility=1")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("facility") == 1


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
async def test_list_points_of_sale_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pos()], 1))
    with patch("app.services.point_sale_service.list_point_sales", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale?search=abc")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "abc"


@pytest.mark.asyncio
async def test_list_points_of_sale_no_filters() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pos()], 1))
    with patch("app.services.point_sale_service.list_point_sales", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("facility") is None
    assert kwargs.get("warehouse") is None
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_points_of_sale_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pos()], 1))
    with patch("app.services.point_sale_service.list_point_sales", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/points-of-sale?status=0")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 0


@pytest.mark.asyncio
async def test_list_points_of_sale_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/points-of-sale?status=9")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_cash_drawers_facility_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_cash_drawer()], 1))
    with patch("app.services.cash_drawer_service.list_cash_drawers", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers?facility=2")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("facility") == 2


@pytest.mark.asyncio
async def test_list_cash_drawers_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_cash_drawer()], 1))
    with patch("app.services.cash_drawer_service.list_cash_drawers", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers?search=abc")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "abc"


@pytest.mark.asyncio
async def test_list_cash_drawers_no_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_cash_drawer()], 1))
    with patch("app.services.cash_drawer_service.list_cash_drawers", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("facility") is None
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_cash_drawers_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_cash_drawer()], 1))
    with patch("app.services.cash_drawer_service.list_cash_drawers", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/cash-drawers?status=0")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 0


@pytest.mark.asyncio
async def test_list_cash_drawers_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/cash-drawers?status=9")
    assert r.status_code == 422


# ── Facility logo tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_facility_logo_returns_200_with_resolved_url() -> None:
    _auth()
    expected_filename = "abc123.png"
    updated = _facility()
    updated.logo = expected_filename
    with (
        patch(
            "app.services.facility_service.get_facility", new=AsyncMock(return_value=_facility())
        ),
        patch(
            "app.services.image_service.process_and_save_image",
            new=AsyncMock(return_value=expected_filename),
        ),
        patch(
            "app.services.facility_service.update_facility", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/facilities/1/logo",
                files={"file": ("logo.png", _make_png_bytes(), "image/png")},
            )
    assert r.status_code == 200
    assert r.json()["logo"] == f"/images/{expected_filename}"


@pytest.mark.asyncio
async def test_upload_facility_logo_returns_404_when_facility_missing() -> None:
    _auth()
    with patch("app.services.facility_service.get_facility", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/facilities/999/logo",
                files={"file": ("logo.png", _make_png_bytes(), "image/png")},
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_facility_logo_returns_422_on_invalid_image() -> None:
    _auth()
    with (
        patch(
            "app.services.facility_service.get_facility", new=AsyncMock(return_value=_facility())
        ),
        patch(
            "app.services.image_service.process_and_save_image",
            side_effect=ValueError("boom"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/facilities/1/logo",
                files={"file": ("logo.png", _make_png_bytes(), "image/png")},
            )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_facility_with_no_logo_serializes_as_null() -> None:
    _auth()
    facility = _facility()
    facility.logo = None
    with patch("app.services.facility_service.get_facility", new=AsyncMock(return_value=facility)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/facilities/1")
    assert r.status_code == 200
    assert r.json()["logo"] is None
