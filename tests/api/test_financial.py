"""Tests for /exchange-rates, /expenses, and /payment-method-options endpoints."""

import datetime
from collections.abc import Generator
from decimal import Decimal
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


def _exchange_rate(er_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        exchange_rate_id=er_id,
        date=datetime.date(2026, 1, 1),
        rate=Decimal("17.50"),
        base=0,
        target=1,
    )


def _expense(expense_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(expense_id=expense_id, expense="Office Supplies", comment=None)


def _facility_summary(facility_id: int = 1) -> SimpleNamespace:
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


def _pmo(pmo_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        payment_method_option_id=pmo_id,
        facility=_facility_summary(),
        warehouse=None,
        name="Cash",
        number_of_payments=1,
        display_on_ticket=True,
        payment_method=0,
        commission=Decimal("0"),
        status=0,
    )


# ── Exchange Rate tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_exchange_rates_returns_200() -> None:
    _auth()
    with patch(
        "app.services.exchange_rate_service.list_exchange_rates",
        new=AsyncMock(return_value=([_exchange_rate()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/exchange-rates")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["rate"] == "17.50"


@pytest.mark.asyncio
async def test_get_exchange_rate_returns_200() -> None:
    _auth()
    with patch(
        "app.services.exchange_rate_service.get_exchange_rate",
        new=AsyncMock(return_value=_exchange_rate()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/exchange-rates/1")
    assert r.status_code == 200
    assert r.json()["exchange_rate_id"] == 1


@pytest.mark.asyncio
async def test_get_exchange_rate_returns_404() -> None:
    _auth()
    with patch(
        "app.services.exchange_rate_service.get_exchange_rate",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/exchange-rates/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_exchange_rate_returns_201() -> None:
    _auth()
    with patch(
        "app.services.exchange_rate_service.create_exchange_rate",
        new=AsyncMock(return_value=_exchange_rate()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/exchange-rates",
                json={"date": "2026-01-01", "rate": "17.50", "base": 0, "target": 1},
            )
    assert r.status_code == 201
    assert r.json()["rate"] == "17.50"


@pytest.mark.asyncio
async def test_update_exchange_rate_returns_200() -> None:
    _auth()
    updated = _exchange_rate()
    updated.rate = Decimal("18.00")
    with (
        patch(
            "app.services.exchange_rate_service.get_exchange_rate",
            new=AsyncMock(return_value=_exchange_rate()),
        ),
        patch(
            "app.services.exchange_rate_service.update_exchange_rate",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/exchange-rates/1", json={"rate": "18.00"})
    assert r.status_code == 200
    assert r.json()["rate"] == "18.00"


@pytest.mark.asyncio
async def test_update_exchange_rate_returns_404() -> None:
    _auth()
    with patch(
        "app.services.exchange_rate_service.get_exchange_rate",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/exchange-rates/999", json={"rate": "1.0"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_exchange_rate_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.exchange_rate_service.get_exchange_rate",
            new=AsyncMock(return_value=_exchange_rate()),
        ),
        patch(
            "app.services.exchange_rate_service.delete_exchange_rate",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/exchange-rates/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_exchange_rate_returns_404() -> None:
    _auth()
    with patch(
        "app.services.exchange_rate_service.get_exchange_rate",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/exchange-rates/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_exchange_rates_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/exchange-rates")
    assert r.status_code == 401


# ── Expense tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_expenses_returns_200() -> None:
    _auth()
    with patch(
        "app.services.expense_service.list_expenses",
        new=AsyncMock(return_value=([_expense()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/expenses")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["expense"] == "Office Supplies"


@pytest.mark.asyncio
async def test_list_expenses_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_expense()], 1))
    with patch("app.services.expense_service.list_expenses", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/expenses?search=office")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("search") == "office"


@pytest.mark.asyncio
async def test_get_expense_returns_200() -> None:
    _auth()
    with patch(
        "app.services.expense_service.get_expense", new=AsyncMock(return_value=_expense())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/expenses/1")
    assert r.status_code == 200
    assert r.json()["expense_id"] == 1
    assert r.json()["expense"] == "Office Supplies"


@pytest.mark.asyncio
async def test_get_expense_returns_404() -> None:
    _auth()
    with patch(
        "app.services.expense_service.get_expense", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/expenses/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_expense_returns_201() -> None:
    _auth()
    with patch(
        "app.services.expense_service.create_expense", new=AsyncMock(return_value=_expense())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/v1/expenses", json={"name": "Office Supplies"})
    assert r.status_code == 201
    assert r.json()["expense"] == "Office Supplies"


@pytest.mark.asyncio
async def test_update_expense_returns_200() -> None:
    _auth()
    updated = _expense()
    updated.expense = "Travel"
    with (
        patch(
            "app.services.expense_service.get_expense", new=AsyncMock(return_value=_expense())
        ),
        patch(
            "app.services.expense_service.update_expense", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/expenses/1", json={"name": "Travel"})
    assert r.status_code == 200
    assert r.json()["expense"] == "Travel"


@pytest.mark.asyncio
async def test_update_expense_returns_404() -> None:
    _auth()
    with patch(
        "app.services.expense_service.get_expense", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/expenses/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_expense_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.expense_service.get_expense", new=AsyncMock(return_value=_expense())
        ),
        patch(
            "app.services.expense_service.delete_expense", new=AsyncMock(return_value=None)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/expenses/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_expense_returns_404() -> None:
    _auth()
    with patch(
        "app.services.expense_service.get_expense", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/expenses/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_expenses_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/expenses")
    assert r.status_code == 401


# ── Payment Method Option tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_payment_method_options_returns_200() -> None:
    _auth()
    with patch(
        "app.services.payment_method_option_service.list_payment_method_options",
        new=AsyncMock(return_value=([_pmo()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/payment-method-options")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["name"] == "Cash"


@pytest.mark.asyncio
async def test_get_payment_method_option_returns_200() -> None:
    _auth()
    with patch(
        "app.services.payment_method_option_service.get_payment_method_option",
        new=AsyncMock(return_value=_pmo()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/payment-method-options/1")
    assert r.status_code == 200
    assert r.json()["payment_method_option_id"] == 1
    assert "enabled" not in r.json()


@pytest.mark.asyncio
async def test_get_payment_method_option_returns_404() -> None:
    _auth()
    with patch(
        "app.services.payment_method_option_service.get_payment_method_option",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/payment-method-options/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_payment_method_option_returns_201() -> None:
    _auth()
    with patch(
        "app.services.payment_method_option_service.create_payment_method_option",
        new=AsyncMock(return_value=_pmo()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/payment-method-options",
                json={"facility": 1, "name": "Cash", "payment_method": 0},
            )
    assert r.status_code == 201
    assert r.json()["name"] == "Cash"
    assert r.json()["status"] == 0


@pytest.mark.asyncio
async def test_update_payment_method_option_returns_200() -> None:
    _auth()
    updated = _pmo()
    updated.name = "Card"
    with (
        patch(
            "app.services.payment_method_option_service.get_payment_method_option",
            new=AsyncMock(return_value=_pmo()),
        ),
        patch(
            "app.services.payment_method_option_service.update_payment_method_option",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/payment-method-options/1", json={"name": "Card"})
    assert r.status_code == 200
    assert r.json()["name"] == "Card"


@pytest.mark.asyncio
async def test_update_payment_method_option_returns_404() -> None:
    _auth()
    with patch(
        "app.services.payment_method_option_service.get_payment_method_option",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/payment-method-options/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_payment_method_option_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.payment_method_option_service.get_payment_method_option",
            new=AsyncMock(return_value=_pmo()),
        ),
        patch(
            "app.services.payment_method_option_service.delete_payment_method_option",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/payment-method-options/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_payment_method_option_returns_404() -> None:
    _auth()
    with patch(
        "app.services.payment_method_option_service.get_payment_method_option",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/payment-method-options/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_payment_method_options_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/payment-method-options")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_payment_method_options_status_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pmo()], 1))
    with patch(
        "app.services.payment_method_option_service.list_payment_method_options", new=mock
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/payment-method-options?status=0")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") == 0


@pytest.mark.asyncio
async def test_list_payment_method_options_no_status_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pmo()], 1))
    with patch(
        "app.services.payment_method_option_service.list_payment_method_options", new=mock
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/payment-method-options")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("status") is None


@pytest.mark.asyncio
async def test_list_payment_method_options_invalid_status_returns_422() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/payment-method-options?status=9")
    assert r.status_code == 422
