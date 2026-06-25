"""Tests for /products and /price-lists endpoints."""

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
        user_id="tester", session_version=1, administrator=True, store_id=None
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


def _product_item(prod_id: int = 1, photo: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        product_id=prod_id,
        code="P001",
        name="Widget Alpha",
        photo=photo,
        brand=None,
        model=None,
        unit_of_measurement="PCS",
        tax_rate=Decimal("0.16"),
        deactivated=False,
    )


def _product(prod_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        product_id=prod_id,
        code="P001",
        name="Widget Alpha",
        photo=None,
        sku=None,
        brand=None,
        model=None,
        bar_code=None,
        location=None,
        unit_of_measurement="PCS",
        key=None,
        tax_rate=Decimal("0.16"),
        tax_included=False,
        price_type=0,
        currency=0,
        min_order_qty=1,
        supplier=None,
        stockable=False,
        perishable=False,
        seriable=False,
        purchasable=False,
        salable=False,
        invoiceable=False,
        stock_verification=True,
        deactivated=False,
        comment=None,
        prices=[],
    )


# ── Price List tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_price_lists_returns_200() -> None:
    _auth()
    with patch(
        "app.services.price_list_service.list_price_lists",
        new=AsyncMock(return_value=([_pl()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/price-lists")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Retail"


@pytest.mark.asyncio
async def test_get_price_list_returns_200() -> None:
    _auth()
    with patch(
        "app.services.price_list_service.get_price_list", new=AsyncMock(return_value=_pl())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/price-lists/1")
    assert r.status_code == 200
    assert r.json()["price_list_id"] == 1


@pytest.mark.asyncio
async def test_get_price_list_returns_404() -> None:
    _auth()
    with patch(
        "app.services.price_list_service.get_price_list", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/price-lists/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_price_list_returns_201() -> None:
    _auth()
    with patch(
        "app.services.price_list_service.create_price_list", new=AsyncMock(return_value=_pl())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/price-lists",
                json={"name": "Retail", "high_profit_margin": "0.30", "low_profit_margin": "0.10"},
            )
    assert r.status_code == 201
    assert r.json()["name"] == "Retail"


@pytest.mark.asyncio
async def test_update_price_list_returns_200() -> None:
    _auth()
    updated = _pl()
    updated.name = "Wholesale"
    with (
        patch(
            "app.services.price_list_service.get_price_list", new=AsyncMock(return_value=_pl())
        ),
        patch(
            "app.services.price_list_service.update_price_list",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/price-lists/1", json={"name": "Wholesale"})
    assert r.status_code == 200
    assert r.json()["name"] == "Wholesale"


@pytest.mark.asyncio
async def test_update_price_list_returns_404() -> None:
    _auth()
    with patch(
        "app.services.price_list_service.get_price_list", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/price-lists/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_price_list_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.price_list_service.get_price_list", new=AsyncMock(return_value=_pl())
        ),
        patch(
            "app.services.price_list_service.delete_price_list", new=AsyncMock(return_value=None)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/price-lists/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_price_list_returns_404() -> None:
    _auth()
    with patch(
        "app.services.price_list_service.get_price_list", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/price-lists/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_price_lists_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/price-lists")
    assert r.status_code == 401


# ── Product tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_products_returns_200() -> None:
    _auth()
    with patch(
        "app.services.product_service.list_products",
        new=AsyncMock(return_value=([_product_item()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/products")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["photo"] is None


@pytest.mark.asyncio
async def test_list_products_resolves_photo_url() -> None:
    _auth()
    with patch(
        "app.services.product_service.list_products",
        new=AsyncMock(return_value=([_product_item(photo="widget.jpg")], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/products")
    assert r.status_code == 200
    assert r.json()["items"][0]["photo"].endswith("/images/widget.jpg")


@pytest.mark.asyncio
async def test_get_product_returns_200() -> None:
    _auth()
    with patch(
        "app.services.product_service.get_product", new=AsyncMock(return_value=_product())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/products/1")
    assert r.status_code == 200
    assert r.json()["code"] == "P001"
    assert r.json()["stock_verification"] is True


@pytest.mark.asyncio
async def test_get_product_returns_404() -> None:
    _auth()
    with patch(
        "app.services.product_service.get_product", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/products/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_product_returns_201() -> None:
    _auth()
    with patch(
        "app.services.product_service.create_product", new=AsyncMock(return_value=_product())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/products",
                json={"code": "P001", "name": "Widget Alpha", "unit_of_measurement": "PCS"},
            )
    assert r.status_code == 201
    assert r.json()["code"] == "P001"


@pytest.mark.asyncio
async def test_create_product_rejects_whitespace_code() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/products",
            json={"code": "P 001", "name": "Widget Alpha", "unit_of_measurement": "PCS"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_product_returns_200() -> None:
    _auth()
    updated = _product()
    updated.name = "Widget Beta"
    with (
        patch(
            "app.services.product_service.get_product", new=AsyncMock(return_value=_product())
        ),
        patch(
            "app.services.product_service.update_product", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/products/1", json={"name": "Widget Beta"})
    assert r.status_code == 200
    assert r.json()["name"] == "Widget Beta"


@pytest.mark.asyncio
async def test_update_product_returns_404() -> None:
    _auth()
    with patch(
        "app.services.product_service.get_product", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/products/999", json={"name": "XXXX"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_product_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.product_service.get_product", new=AsyncMock(return_value=_product())
        ),
        patch(
            "app.services.product_service.delete_product", new=AsyncMock(return_value=None)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/products/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_product_returns_404() -> None:
    _auth()
    with patch(
        "app.services.product_service.get_product", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/products/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_merge_products_returns_204() -> None:
    _auth()
    with patch(
        "app.services.product_service.merge_products", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/products/merge", json={"product_id": 1, "duplicate_id": 2}
            )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_list_products_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/products")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_products_supplier_filter_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_product_item()], 1))
    with patch("app.services.product_service.list_products", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/products?supplier=3")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("supplier") == 3


@pytest.mark.asyncio
async def test_list_products_no_supplier_filter() -> None:
    _auth()
    mock = AsyncMock(return_value=([_product_item()], 1))
    with patch("app.services.product_service.list_products", new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/products")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs.get("supplier") is None
