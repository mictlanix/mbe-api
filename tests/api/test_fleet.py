"""Tests for /vehicles and /vehicle-operators endpoints."""

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
        user_id="tester", session_version=1, administrator=True, facility_id=None
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
        status=0,
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
        status=0,
        personal_id=None,
        start_job_date=datetime.date(2020, 1, 1),
        enroll_number=None,
        comment=None,
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
        status=0,
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
    with patch("app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=_vehicle())):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicles/1")
    assert r.status_code == 200
    assert r.json()["vehicle_id"] == 1
    assert "active" not in r.json()


@pytest.mark.asyncio
async def test_get_vehicle_returns_404() -> None:
    _auth()
    with patch("app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=None)):
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
    assert r.json()["status"] == 0


@pytest.mark.asyncio
async def test_update_vehicle_returns_200() -> None:
    _auth()
    updated = _vehicle()
    updated.name = "Truck 2"
    with (
        patch("app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=_vehicle())),
        patch("app.services.vehicle_service.update_vehicle", new=AsyncMock(return_value=updated)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/vehicles/1", json={"name": "Truck 2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Truck 2"


@pytest.mark.asyncio
async def test_update_vehicle_returns_404() -> None:
    _auth()
    with patch("app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/vehicles/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_vehicle_returns_204() -> None:
    _auth()
    with (
        patch("app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=_vehicle())),
        patch("app.services.vehicle_service.delete_vehicle", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/vehicles/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_vehicle_returns_404() -> None:
    _auth()
    with patch("app.services.vehicle_service.get_vehicle", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/vehicles/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_vehicles_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/vehicles")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_vehicles_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle()], 1))
    with patch("app.services.vehicle_service.list_vehicles", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicles?status=0")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 0


@pytest.mark.asyncio
async def test_list_vehicles_no_status_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle()], 1))
    with patch("app.services.vehicle_service.list_vehicles", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicles")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_vehicles_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/vehicles?status=9")
    assert r.status_code == 422


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
            r = await c.put("/api/v1/vehicle-operators/1", json={"issuing_location": "GDL"})
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
            r = await c.put("/api/v1/vehicle-operators/999", json={"status": 1})
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


@pytest.mark.asyncio
async def test_list_vehicle_operators_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle_operator()], 1))
    with patch("app.services.vehicle_operator_service.list_vehicle_operators", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators?status=0")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 0


@pytest.mark.asyncio
async def test_list_vehicle_operators_no_status_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_vehicle_operator()], 1))
    with patch("app.services.vehicle_operator_service.list_vehicle_operators", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/vehicle-operators")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_vehicle_operators_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/vehicle-operators?status=9")
    assert r.status_code == 422
