"""Tests for /vehicles, /vehicle-operators, and /production-sites endpoints."""

import datetime
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

_FUTURE_DATE = datetime.date(2030, 1, 1)


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


def _vehicle(vehicle_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        vehicle_id=vehicle_id,
        license_plate="ABC-123",
        name="Truck 1",
        nickname="Big One",
        tons_capacity=5,
        active=True,
    )


def _employee(employee_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        employee_id=employee_id,
        first_name="Jane",
        last_name="Doe",
        nickname="JD",
        gender=0,
        birthday=datetime.date(1990, 1, 1),
        taxpayer_id=None,
        sales_person=False,
        active=True,
        personal_id=None,
        start_job_date=datetime.date(2020, 1, 1),
        enroll_number=None,
        comment=None,
        disabled=None,
    )


def _vehicle_operator(vo_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        vehicle_operator_id=vo_id,
        driver=_employee(),
        license_type="C",
        driver_license_number="L123456",
        issue_date=datetime.date(2024, 1, 1),
        expiration_date=_FUTURE_DATE,
        issuing_location="CDMX",
        creation_time=datetime.datetime(2024, 1, 1, 0, 0, 0),
        modification_time=datetime.datetime(2024, 1, 1, 0, 0, 0),
        creator=_employee(0),
        updater=_employee(0),
        active=True,
    )


def _store_summary(store_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        store_id=store_id,
        code="S1",
        name="Main Store",
        location="06000",
        address=1,
        taxpayer="RFC123456789A",
        logo="logo.png",
        receipt_message=None,
        default_batch=None,
        disabled=None,
    )


def _production_site(ps_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        production_site_id=ps_id,
        store=_store_summary(),
        code="KIT1",
        name="Kitchen",
        comment=None,
        disabled=None,
    )


# ── Vehicle tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_vehicles_returns_200() -> None:
    _auth()
    with patch(
        "app.services.vehicle_service.list_vehicles",
        new=AsyncMock(return_value=([_vehicle()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicles")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["license_plate"] == "ABC-123"


@pytest.mark.asyncio
async def test_list_vehicles_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle()], 1))
    with patch("app.services.vehicle_service.list_vehicles", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicles?search=truck")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "truck"


@pytest.mark.asyncio
async def test_get_vehicle_returns_200() -> None:
    _auth()
    with patch(
        "app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=_vehicle())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicles/1")
    assert r.status_code == 200
    assert r.json()["vehicle_id"] == 1


@pytest.mark.asyncio
async def test_get_vehicle_returns_404() -> None:
    _auth()
    with patch(
        "app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicles/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_vehicle_returns_201() -> None:
    _auth()
    with patch(
        "app.services.vehicle_service.create_vehicle", new=AsyncMock(return_value=_vehicle())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/vehicles",
                json={
                    "license_plate": "ABC-123",
                    "name": "Truck 1",
                    "nickname": "Big One",
                    "tons_capacity": 5,
                },
            )
    assert r.status_code == 201
    assert r.json()["license_plate"] == "ABC-123"


@pytest.mark.asyncio
async def test_update_vehicle_returns_200() -> None:
    _auth()
    updated = _vehicle()
    updated.name = "Truck 2"
    with (
        patch(
            "app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=_vehicle())
        ),
        patch(
            "app.services.vehicle_service.update_vehicle", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/vehicles/1", json={"name": "Truck 2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Truck 2"


@pytest.mark.asyncio
async def test_update_vehicle_returns_404() -> None:
    _auth()
    with patch(
        "app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/vehicles/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_vehicle_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=_vehicle())
        ),
        patch(
            "app.services.vehicle_service.delete_vehicle", new=AsyncMock(return_value=None)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/vehicles/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_vehicle_returns_404() -> None:
    _auth()
    with patch(
        "app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/vehicles/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_vehicles_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/vehicles")
    assert r.status_code == 401


# ── Vehicle Operator tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_vehicle_operators_returns_200() -> None:
    _auth()
    with patch(
        "app.services.vehicle_operator_service.list_vehicle_operators",
        new=AsyncMock(return_value=([_vehicle_operator()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["license_type"] == "C"


@pytest.mark.asyncio
async def test_get_vehicle_operator_returns_200_with_days_until_expiry() -> None:
    _auth()
    with patch(
        "app.services.vehicle_operator_service.get_vehicle_operator",
        new=AsyncMock(return_value=_vehicle_operator()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators/1")
    assert r.status_code == 200
    body = r.json()
    assert body["vehicle_operator_id"] == 1
    assert body["days_until_expiry"] == (_FUTURE_DATE - datetime.date.today()).days


@pytest.mark.asyncio
async def test_get_vehicle_operator_returns_404() -> None:
    _auth()
    with patch(
        "app.services.vehicle_operator_service.get_vehicle_operator",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_vehicle_operator_returns_201() -> None:
    _auth()
    with patch(
        "app.services.vehicle_operator_service.create_vehicle_operator",
        new=AsyncMock(return_value=_vehicle_operator()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/vehicle-operators",
                json={
                    "driver": 1,
                    "license_type": "C",
                    "driver_license_number": "L123456",
                    "issue_date": "2024-01-01",
                    "expiration_date": "2030-01-01",
                    "issuing_location": "CDMX",
                },
            )
    assert r.status_code == 201
    assert r.json()["license_type"] == "C"


@pytest.mark.asyncio
async def test_update_vehicle_operator_returns_200() -> None:
    _auth()
    updated = _vehicle_operator()
    updated.issuing_location = "GDL"
    with (
        patch(
            "app.services.vehicle_operator_service.get_vehicle_operator",
            new=AsyncMock(return_value=_vehicle_operator()),
        ),
        patch(
            "app.services.vehicle_operator_service.update_vehicle_operator",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put(
                "/api/v1/vehicle-operators/1", json={"issuing_location": "GDL"}
            )
    assert r.status_code == 200
    assert r.json()["issuing_location"] == "GDL"


@pytest.mark.asyncio
async def test_update_vehicle_operator_returns_404() -> None:
    _auth()
    with patch(
        "app.services.vehicle_operator_service.get_vehicle_operator",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/vehicle-operators/999", json={"active": False})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_vehicle_operator_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.vehicle_operator_service.get_vehicle_operator",
            new=AsyncMock(return_value=_vehicle_operator()),
        ),
        patch(
            "app.services.vehicle_operator_service.delete_vehicle_operator",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/vehicle-operators/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_vehicle_operator_returns_404() -> None:
    _auth()
    with patch(
        "app.services.vehicle_operator_service.get_vehicle_operator",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/vehicle-operators/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_vehicle_operators_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/vehicle-operators")
    assert r.status_code == 401


# ── Production Site tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_production_sites_returns_200() -> None:
    _auth()
    with patch(
        "app.services.production_site_service.list_production_sites",
        new=AsyncMock(return_value=([_production_site()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/production-sites")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["code"] == "KIT1"


@pytest.mark.asyncio
async def test_get_production_site_returns_200() -> None:
    _auth()
    with patch(
        "app.services.production_site_service.get_production_site",
        new=AsyncMock(return_value=_production_site()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/production-sites/1")
    assert r.status_code == 200
    assert r.json()["production_site_id"] == 1


@pytest.mark.asyncio
async def test_get_production_site_returns_404() -> None:
    _auth()
    with patch(
        "app.services.production_site_service.get_production_site",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/production-sites/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_production_site_returns_201() -> None:
    _auth()
    with patch(
        "app.services.production_site_service.create_production_site",
        new=AsyncMock(return_value=_production_site()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/production-sites",
                json={"store": 1, "code": "KIT1", "name": "Kitchen"},
            )
    assert r.status_code == 201
    assert r.json()["code"] == "KIT1"


@pytest.mark.asyncio
async def test_update_production_site_returns_200() -> None:
    _auth()
    updated = _production_site()
    updated.name = "Bakery"
    with (
        patch(
            "app.services.production_site_service.get_production_site",
            new=AsyncMock(return_value=_production_site()),
        ),
        patch(
            "app.services.production_site_service.update_production_site",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/production-sites/1", json={"name": "Bakery"})
    assert r.status_code == 200
    assert r.json()["name"] == "Bakery"


@pytest.mark.asyncio
async def test_update_production_site_returns_404() -> None:
    _auth()
    with patch(
        "app.services.production_site_service.get_production_site",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/production-sites/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_production_site_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.production_site_service.get_production_site",
            new=AsyncMock(return_value=_production_site()),
        ),
        patch(
            "app.services.production_site_service.delete_production_site",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/production-sites/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_production_site_returns_404() -> None:
    _auth()
    with patch(
        "app.services.production_site_service.get_production_site",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/production-sites/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_production_sites_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/production-sites")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_vehicle_operators_employee_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle_operator()], 1))
    with patch("app.services.vehicle_operator_service.list_vehicle_operators", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators?employee=7")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("employee") == 7


@pytest.mark.asyncio
async def test_list_vehicle_operators_no_employee_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle_operator()], 1))
    with patch("app.services.vehicle_operator_service.list_vehicle_operators", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("employee") is None


@pytest.mark.asyncio
async def test_list_vehicle_operators_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle_operator()], 1))
    with patch("app.services.vehicle_operator_service.list_vehicle_operators", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators?search=jane")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "jane"
