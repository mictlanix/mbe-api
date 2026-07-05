"""Tests for POST /products/{product_id}/image, photo-clear via PUT, and GET /images serving."""

import io
from collections.abc import Generator
from decimal import Decimal
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


@pytest.fixture
def _static_dir(tmp_path):
    """Mount a StaticFiles route at /images pointing to tmp_path for serving tests."""
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles

    saved = list(app.router.routes)
    app.router.routes[:] = [r for r in saved if not (isinstance(r, Mount) and r.path == "/images")]
    app.mount("/images", StaticFiles(directory=str(tmp_path)), name="images")
    yield tmp_path
    app.router.routes[:] = saved


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="tester", session_version=1, administrator=True, store_id=None
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _make_png_bytes(width: int = 200, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(width: int = 200, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _product(prod_id: int = 1, photo: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        product_id=prod_id,
        code="P001",
        name="Widget",
        photo=photo,
        sku=None,
        brand=None,
        model=None,
        bar_code=None,
        location=None,
        unit_of_measurement={"id": "PCS", "name": "Piece", "description": None, "symbol": None},
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
        stock_verification=False,
        deactivated=False,
        comment=None,
        prices=[],
    )


# ── Upload tests (US1) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_image_returns_200_with_photo_url() -> None:
    _auth()
    expected_filename = "abc123.png"
    updated = _product(photo=expected_filename)
    with (
        patch("app.services.product_service.get_product", new=AsyncMock(return_value=_product())),
        patch(
            "app.services.image_service.process_and_save_image",
            new=AsyncMock(return_value=expected_filename),
        ),
        patch(
            "app.services.product_service.update_product", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/products/1/image",
                files={"file": ("photo.png", _make_png_bytes(), "image/png")},
            )
    assert r.status_code == 200
    assert r.json()["stock_verification"] is False
    assert r.json()["photo"] == f"/images/{expected_filename}"


@pytest.mark.asyncio
async def test_upload_image_returns_404_when_product_missing() -> None:
    _auth()
    with patch("app.services.product_service.get_product", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/products/999/image",
                files={"file": ("photo.png", _make_png_bytes(), "image/png")},
            )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_image_returns_401_when_unauthenticated() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/products/1/image",
            files={"file": ("photo.png", _make_png_bytes(), "image/png")},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_upload_image_returns_422_when_file_too_large() -> None:
    _auth()
    oversized = b"x" * (2 * 1024 * 1024 + 1)
    with (
        patch("app.services.product_service.get_product", new=AsyncMock(return_value=_product())),
        patch(
            "app.services.image_service.process_and_save_image",
            side_effect=ValueError("Image exceeds maximum allowed size of 2 MB"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/products/1/image",
                files={"file": ("big.png", oversized, "image/png")},
            )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_image_returns_422_for_non_image_file() -> None:
    _auth()
    with (
        patch("app.services.product_service.get_product", new=AsyncMock(return_value=_product())),
        patch(
            "app.services.image_service.process_and_save_image",
            side_effect=ValueError("Unsupported or unrecognizable image format"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/products/1/image",
                files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_same_image_twice_reuses_existing_file() -> None:
    """Service returns same filename on second call — endpoint should not try to re-create."""
    _auth()
    same_filename = "dedup123.png"
    updated = _product(photo=same_filename)
    with (
        patch("app.services.product_service.get_product", new=AsyncMock(return_value=_product())),
        patch(
            "app.services.image_service.process_and_save_image",
            new=AsyncMock(return_value=same_filename),
        ),
        patch(
            "app.services.product_service.update_product", new=AsyncMock(return_value=updated)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.post(
                "/api/v1/products/1/image",
                files={"file": ("photo.png", _make_png_bytes(), "image/png")},
            )
            r2 = await c.post(
                "/api/v1/products/1/image",
                files={"file": ("photo.png", _make_png_bytes(), "image/png")},
            )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["photo"] == r2.json()["photo"] == f"/images/{same_filename}"


# ── Photo clear tests (US2) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_photo_null_clears_photo() -> None:
    _auth()
    cleared = _product(photo=None)
    with (
        patch(
            "app.services.product_service.get_product",
            new=AsyncMock(return_value=_product(photo="old.png")),
        ),
        patch(
            "app.services.product_service.update_product", new=AsyncMock(return_value=cleared)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/products/1", json={"photo": None})
    assert r.status_code == 200
    assert r.json()["photo"] is None


@pytest.mark.asyncio
async def test_put_without_photo_key_preserves_existing_photo() -> None:
    _auth()
    unchanged = _product(photo="keep.png")
    with (
        patch(
            "app.services.product_service.get_product",
            new=AsyncMock(return_value=_product(photo="keep.png")),
        ),
        patch(
            "app.services.product_service.update_product", new=AsyncMock(return_value=unchanged)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/products/1", json={"name": "Updated Name"})
    assert r.status_code == 200
    assert r.json()["photo"] == "/images/keep.png"


# ── Serving tests (US3) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_image_returns_200(_static_dir) -> None:
    (_static_dir / "testimg.png").write_bytes(_make_png_bytes(50, 50))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/images/testimg.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")


@pytest.mark.asyncio
async def test_serve_image_returns_404_for_missing(_static_dir) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/images/nonexistent.png")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_serve_image_accessible_without_auth(_static_dir) -> None:
    (_static_dir / "public.png").write_bytes(_make_png_bytes(50, 50))
    # No auth override, no Authorization header
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/images/public.png")
    assert r.status_code == 200
