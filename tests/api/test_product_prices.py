"""Tests for /product-prices endpoints."""

from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
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


def _pl(pl_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        price_list_id=pl_id,
        name="Retail",
        high_profit_margin=Decimal("0.30"),
        low_profit_margin=Decimal("0.10"),
    )


def _pp(pp_id: int = 1, product: int = 1, price_list: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        product_price_id=pp_id,
        product=product,
        price_list=_pl(price_list),
        price=Decimal("199.9900"),
        low_profit=Decimal("10.000000"),
        high_profit=Decimal("30.000000"),
    )


# ── Create ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_product_price_returns_201() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.create_product_price",
        new=AsyncMock(return_value=_pp()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/product-prices",
                json={
                    "product": 1,
                    "price_list": 1,
                    "price": "199.99",
                    "low_profit": "10",
                    "high_profit": "30",
                },
            )
    assert r.status_code == 201
    body = r.json()
    assert body["product"] == 1
    assert body["price_list"]["name"] == "Retail"


@pytest.mark.asyncio
async def test_create_product_price_returns_404_product_not_found() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.create_product_price",
        new=AsyncMock(
            side_effect=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
            )
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/product-prices",
                json={
                    "product": 999,
                    "price_list": 1,
                    "price": "1",
                    "low_profit": "0",
                    "high_profit": "0",
                },
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_product_price_returns_404_price_list_not_found() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.create_product_price",
        new=AsyncMock(
            side_effect=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Price list not found"
            )
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/product-prices",
                json={
                    "product": 1,
                    "price_list": 999,
                    "price": "1",
                    "low_profit": "0",
                    "high_profit": "0",
                },
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_product_price_returns_409_duplicate() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.create_product_price",
        new=AsyncMock(
            side_effect=HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Price already exists for this product and price list",
            )
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/product-prices",
                json={
                    "product": 1,
                    "price_list": 1,
                    "price": "1",
                    "low_profit": "0",
                    "high_profit": "0",
                },
            )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_product_price_rejects_negative_price() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/product-prices",
            json={
                "product": 1,
                "price_list": 1,
                "price": "-1",
                "low_profit": "0",
                "high_profit": "0",
            },
        )
    assert r.status_code == 422


# ── Get ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_product_price_returns_200() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.get_product_price",
        new=AsyncMock(return_value=_pp()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/product-prices/1")
    assert r.status_code == 200
    assert r.json()["product_price_id"] == 1


@pytest.mark.asyncio
async def test_get_product_price_returns_404() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.get_product_price",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/product-prices/999")
    assert r.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_product_price_returns_200() -> None:
    _auth()
    updated = _pp()
    updated.price = Decimal("210.00")
    with (
        patch(
            "app.services.product_price_service.get_product_price",
            new=AsyncMock(return_value=_pp()),
        ),
        patch(
            "app.services.product_price_service.update_product_price",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/product-prices/1", json={"price": "210.00"})
    assert r.status_code == 200
    assert r.json()["price"] == "210.00"


@pytest.mark.asyncio
async def test_update_product_price_returns_404() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.get_product_price",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/product-prices/999", json={"price": "1"})
    assert r.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_product_price_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.product_price_service.get_product_price",
            new=AsyncMock(return_value=_pp()),
        ),
        patch(
            "app.services.product_price_service.delete_product_price",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/product-prices/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_product_price_returns_404() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.get_product_price",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/product-prices/999")
    assert r.status_code == 404


# ── List / filter ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_product_prices_returns_200() -> None:
    _auth()
    with patch(
        "app.services.product_price_service.list_product_prices",
        new=AsyncMock(return_value=([_pp()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/product-prices")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["product"] == 1


@pytest.mark.asyncio
async def test_list_product_prices_filters_by_product() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pp()], 1))
    with patch("app.services.product_price_service.list_product_prices", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/product-prices?product=1")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("product") == 1
    assert kwargs.get("price_list") is None


@pytest.mark.asyncio
async def test_list_product_prices_filters_by_price_list() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pp()], 1))
    with patch("app.services.product_price_service.list_product_prices", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/product-prices?price_list=1")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("price_list") == 1
    assert kwargs.get("product") is None


@pytest.mark.asyncio
async def test_list_product_prices_filters_by_both() -> None:
    _auth()
    mock = AsyncMock(return_value=([_pp()], 1))
    with patch("app.services.product_price_service.list_product_prices", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/product-prices?product=1&price_list=1")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("product") == 1
    assert kwargs.get("price_list") == 1


# ── Auth ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_product_prices_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/product-prices")
    assert r.status_code == 401
