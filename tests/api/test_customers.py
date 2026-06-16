"""Tests for /customers, /labels, and /taxpayer-recipients endpoints."""

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


def _label(label_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(label_id=label_id, name="Tag A", comment=None)


def _taxpayer(rfc: str = "RFC123456789A") -> SimpleNamespace:
    return SimpleNamespace(
        taxpayer_recipient_id=rfc,
        name="Acme SA de CV",
        email="acme@example.com",
        postal_code=None,
        regime=None,
    )


def _customer(cust_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        customer_id=cust_id,
        code="CUST1",
        name="Acme Corp",
        zone=None,
        credit_limit=Decimal("0"),
        credit_days=0,
        price_list=1,
        shipping=False,
        shipping_required_document=False,
        salesperson=None,
        disabled=None,
        comment=None,
    )


# ── Label tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_labels_returns_200() -> None:
    _auth()
    with patch(
        "app.services.label_service.list_labels",
        new=AsyncMock(return_value=([_label()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/labels")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["name"] == "Tag A"


@pytest.mark.asyncio
async def test_get_label_returns_200() -> None:
    _auth()
    with patch("app.services.label_service.get_label", new=AsyncMock(return_value=_label())):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/labels/1")
    assert r.status_code == 200
    assert r.json()["label_id"] == 1


@pytest.mark.asyncio
async def test_get_label_returns_404() -> None:
    _auth()
    with patch("app.services.label_service.get_label", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/labels/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_label_returns_201() -> None:
    _auth()
    with patch(
        "app.services.label_service.create_label", new=AsyncMock(return_value=_label())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/v1/labels", json={"name": "Tag A"})
    assert r.status_code == 201
    assert r.json()["name"] == "Tag A"


@pytest.mark.asyncio
async def test_update_label_returns_200() -> None:
    _auth()
    updated = _label()
    updated.name = "Tag B"
    with (
        patch("app.services.label_service.get_label", new=AsyncMock(return_value=_label())),
        patch("app.services.label_service.update_label", new=AsyncMock(return_value=updated)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/labels/1", json={"name": "Tag B"})
    assert r.status_code == 200
    assert r.json()["name"] == "Tag B"


@pytest.mark.asyncio
async def test_update_label_returns_404() -> None:
    _auth()
    with patch("app.services.label_service.get_label", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/labels/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_label_returns_204() -> None:
    _auth()
    with (
        patch("app.services.label_service.get_label", new=AsyncMock(return_value=_label())),
        patch("app.services.label_service.delete_label", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/labels/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_label_returns_404() -> None:
    _auth()
    with patch("app.services.label_service.get_label", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/labels/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_labels_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/labels")
    assert r.status_code == 401


# ── Taxpayer Recipient tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_taxpayer_recipients_returns_200() -> None:
    _auth()
    with patch(
        "app.services.taxpayer_recipient_service.list_taxpayer_recipients",
        new=AsyncMock(return_value=([_taxpayer()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/taxpayer-recipients")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_taxpayer_recipient_returns_200() -> None:
    _auth()
    with patch(
        "app.services.taxpayer_recipient_service.get_taxpayer_recipient",
        new=AsyncMock(return_value=_taxpayer()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/taxpayer-recipients/RFC123456789A")
    assert r.status_code == 200
    assert r.json()["taxpayer_recipient_id"] == "RFC123456789A"


@pytest.mark.asyncio
async def test_get_taxpayer_recipient_returns_404() -> None:
    _auth()
    with patch(
        "app.services.taxpayer_recipient_service.get_taxpayer_recipient",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/taxpayer-recipients/RFCNOTFOUND")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_taxpayer_recipient_returns_201() -> None:
    _auth()
    with patch(
        "app.services.taxpayer_recipient_service.create_taxpayer_recipient",
        new=AsyncMock(return_value=_taxpayer()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/taxpayer-recipients",
                json={
                    "taxpayer_recipient_id": "RFC123456789A",
                    "email": "acme@example.com",
                },
            )
    assert r.status_code == 201
    assert r.json()["taxpayer_recipient_id"] == "RFC123456789A"


@pytest.mark.asyncio
async def test_create_taxpayer_recipient_rejects_short_rfc() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/taxpayer-recipients",
            json={"taxpayer_recipient_id": "SHORT", "email": "x@x.com"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_taxpayer_recipient_returns_200() -> None:
    _auth()
    updated = _taxpayer()
    updated.name = "Updated Name"
    with (
        patch(
            "app.services.taxpayer_recipient_service.get_taxpayer_recipient",
            new=AsyncMock(return_value=_taxpayer()),
        ),
        patch(
            "app.services.taxpayer_recipient_service.update_taxpayer_recipient",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put(
                "/api/v1/taxpayer-recipients/RFC123456789A", json={"name": "Updated Name"}
            )
    assert r.status_code == 200
    assert r.json()["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_taxpayer_recipient_returns_404() -> None:
    _auth()
    with patch(
        "app.services.taxpayer_recipient_service.get_taxpayer_recipient",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/taxpayer-recipients/NOTFOUND", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_taxpayer_recipient_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.taxpayer_recipient_service.get_taxpayer_recipient",
            new=AsyncMock(return_value=_taxpayer()),
        ),
        patch(
            "app.services.taxpayer_recipient_service.delete_taxpayer_recipient",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/taxpayer-recipients/RFC123456789A")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_taxpayer_recipient_returns_404() -> None:
    _auth()
    with patch(
        "app.services.taxpayer_recipient_service.get_taxpayer_recipient",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/taxpayer-recipients/NOTFOUND")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_taxpayer_recipients_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/taxpayer-recipients")
    assert r.status_code == 401


# ── Customer tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_customers_returns_200() -> None:
    _auth()
    with patch(
        "app.services.customer_service.list_customers",
        new=AsyncMock(return_value=([_customer()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/customers")
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_customer_returns_200() -> None:
    _auth()
    with patch(
        "app.services.customer_service.get_customer", new=AsyncMock(return_value=_customer())
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/customers/1")
    assert r.status_code == 200
    assert r.json()["customer_id"] == 1


@pytest.mark.asyncio
async def test_get_customer_returns_404() -> None:
    _auth()
    with patch(
        "app.services.customer_service.get_customer", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/v1/customers/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_customer_returns_201() -> None:
    _auth()
    with patch(
        "app.services.customer_service.create_customer",
        new=AsyncMock(return_value=_customer()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/customers",
                json={"code": "CUST1", "name": "Acme Corp", "price_list": 1},
            )
    assert r.status_code == 201
    assert r.json()["code"] == "CUST1"


@pytest.mark.asyncio
async def test_update_customer_returns_200() -> None:
    _auth()
    updated = _customer()
    updated.name = "Updated Corp"
    with (
        patch(
            "app.services.customer_service.get_customer",
            new=AsyncMock(return_value=_customer()),
        ),
        patch(
            "app.services.customer_service.update_customer",
            new=AsyncMock(return_value=updated),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/customers/1", json={"name": "Updated Corp"})
    assert r.status_code == 200
    assert r.json()["name"] == "Updated Corp"


@pytest.mark.asyncio
async def test_update_customer_returns_404() -> None:
    _auth()
    with patch(
        "app.services.customer_service.get_customer", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put("/api/v1/customers/999", json={"name": "X"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_customer_returns_204() -> None:
    _auth()
    with (
        patch(
            "app.services.customer_service.get_customer",
            new=AsyncMock(return_value=_customer()),
        ),
        patch(
            "app.services.customer_service.delete_customer", new=AsyncMock(return_value=None)
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/customers/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_customer_returns_404() -> None:
    _auth()
    with patch(
        "app.services.customer_service.get_customer", new=AsyncMock(return_value=None)
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/api/v1/customers/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_customers_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/customers")
    assert r.status_code == 401
