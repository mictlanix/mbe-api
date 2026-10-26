"""The /stock routes (spec 030, US3): the HTTP contract, with the service patched."""

from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight
from app.main import app
from app.schemas.inventory import InTransitStock, ProductStock, StockRow

BASE = '/api/v1/stock'
SERVICE = 'app.services.stock_query_service'
ROUTES = [(BASE + '?warehouse=1'), (BASE + '/products/3')]


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True, rights: int | None = None) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=1,
        employee_id=7,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = (
        None if rights is None else SimpleNamespace(privileges=rights)
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _row() -> StockRow:
    return StockRow(
        product=3,
        product_code='W-1',
        product_name='Widget',
        warehouse=1,
        on_hand=Decimal('10'),
        reserved=Decimal('4'),
        available=Decimal('6'),
        incoming=Decimal('2'),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ROUTES)
async def test_without_a_token_it_is_401(url: str) -> None:
    async with _client() as client:
        response = await client.get(url)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ROUTES)
async def test_without_read_on_the_stock_report_it_is_403(url: str) -> None:
    _auth(administrator=False, rights=0b1111 & ~int(AccessRight.READ))
    async with _client() as client:
        response = await client.get(url)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_listing_returns_items_and_total() -> None:
    _auth()
    with patch(f'{SERVICE}.list_stock', AsyncMock(return_value=([_row()], 1))) as listing:
        async with _client() as client:
            response = await client.get(
                BASE + '?warehouse=1&q=wid&include_zero=true&skip=2&limit=5'
            )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body['total'] == 1
    assert body['items'][0]['available'] == '6.0000' or Decimal(body['items'][0]['available']) == 6
    kwargs = listing.await_args.kwargs
    assert (kwargs['warehouse'], kwargs['q'], kwargs['include_zero']) == (1, 'wid', True)
    assert (kwargs['skip'], kwargs['limit']) == (2, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['', '?warehouse=1&limit=501', '?warehouse=1&limit=0'])
async def test_listing_validates_its_parameters(query: str) -> None:
    _auth()
    async with _client() as client:
        response = await client.get(BASE + query)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [status.HTTP_404_NOT_FOUND, status.HTTP_422_UNPROCESSABLE_CONTENT])
async def test_listing_passes_the_warehouse_refusals_through(code: int) -> None:
    _auth()
    with patch(f'{SERVICE}.list_stock', AsyncMock(side_effect=HTTPException(code, 'no'))):
        async with _client() as client:
            response = await client.get(BASE + '?warehouse=9')

    assert response.status_code == code


@pytest.mark.asyncio
async def test_product_view_returns_warehouses_and_in_transit() -> None:
    _auth()
    view = ProductStock(
        product=3,
        warehouses=[_row()],
        in_transit=[InTransitStock(facility=2, warehouse=5, on_hand=Decimal('3'))],
    )
    with patch(f'{SERVICE}.product_stock', AsyncMock(return_value=view)):
        async with _client() as client:
            response = await client.get(BASE + '/products/3')

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body['product'] == 3
    assert body['warehouses'][0]['warehouse'] == 1
    assert body['in_transit'][0]['facility'] == 2


@pytest.mark.asyncio
async def test_product_view_of_an_unknown_product_is_404() -> None:
    _auth()
    error = HTTPException(status.HTTP_404_NOT_FOUND, 'Product not found')
    with patch(f'{SERVICE}.product_stock', AsyncMock(side_effect=error)):
        async with _client() as client:
            response = await client.get(BASE + '/products/99')

    assert response.status_code == status.HTTP_404_NOT_FOUND
