"""The /kardex route (spec 030, US4, T018): the HTTP contract, with the service patched."""

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.main import app
from app.schemas.inventory import Kardex, KardexDocument, KardexRow

BASE = '/api/v1/kardex'
SERVICE = 'app.services.kardex_service'
QUERY = {'product': 3, 'warehouse': 1, 'from': '2026-10-01', 'to': '2026-10-31'}
D = Decimal


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True, rights: int | None = None) -> None:
    """`rights` is the privilege mask a non-administrator holds on every object."""
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


def _kardex() -> Kardex:
    row = KardexRow(
        lot_serial_tracking_id=9,
        date=datetime(2026, 10, 2, 11, 30),
        source=4,
        source_name='inventory_receipt',
        document=KardexDocument(type=4, id=5, facility=1, serial=12),
        reference=5,
        quantity_in=D('10'),
        quantity_out=D('0'),
        balance=D('14'),
    )
    return Kardex(
        product=3,
        warehouse=1,
        from_=date(2026, 10, 1),
        to=date(2026, 10, 31),
        opening=D('4'),
        rows=[row],
        total_in=D('10'),
        total_out=D('0'),
        closing=D('14'),
    )


@pytest.mark.asyncio
async def test_without_a_token_it_is_401() -> None:
    async with _client() as client:
        response = await client.get(BASE, params=QUERY)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_without_read_it_is_403() -> None:
    """Every other right is held, and only Read is missing."""
    _auth(administrator=False, rights=0b1111 & ~int(AccessRight.READ))
    async with _client() as client:
        response = await client.get(BASE, params=QUERY)

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_the_right_is_read_on_kardex() -> None:
    """FR-033: object 32, the current system's own."""
    route = next(r for r in app.routes if getattr(r, 'path', None) == BASE)
    grants = [
        grant
        for dependency in route.dependant.dependencies
        for grant in getattr(dependency.call, 'grants', ())
    ]
    assert grants == [(SystemObject.KARDEX, AccessRight.READ)]


@pytest.mark.asyncio
async def test_read_returns_the_kardex_with_from_on_the_wire() -> None:
    _auth(administrator=False, rights=int(AccessRight.READ))
    service = AsyncMock(return_value=_kardex())
    with patch(f'{SERVICE}.kardex', service):
        async with _client() as client:
            response = await client.get(BASE, params=QUERY)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert (body['from'], body['to'], body['opening'], body['closing']) == (
        '2026-10-01',
        '2026-10-31',
        '4',
        '14',
    )
    assert body['rows'][0]['document'] == {'type': 4, 'id': 5, 'facility': 1, 'serial': 12}
    assert body['rows'][0]['lot_serial_tracking_id'] == 9
    kwargs = service.await_args.kwargs
    assert (kwargs['product'], kwargs['warehouse']) == (3, 1)
    assert (kwargs['from_'], kwargs['to']) == (date(2026, 10, 1), date(2026, 10, 31))


@pytest.mark.asyncio
@pytest.mark.parametrize('detail', ['Product not found', 'Warehouse not found'])
async def test_an_unknown_product_or_warehouse_is_404(detail: str) -> None:
    _auth()
    missing = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    with patch(f'{SERVICE}.kardex', AsyncMock(side_effect=missing)):
        async with _client() as client:
            response = await client.get(BASE, params=QUERY)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('start', 'end'),
    [('2026-10-02', '2026-10-01'), ('2026-01-01', '2027-01-02')],
    ids=['to before from', 'over 366 days'],
)
async def test_a_bad_range_is_422_before_any_query(start: str, end: str) -> None:
    """The real service: the range is refused before it touches the (mocked) database."""
    _auth()
    async with _client() as client:
        response = await client.get(BASE, params=QUERY | {'from': start, 'to': end})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['product', 'warehouse', 'from', 'to'])
async def test_every_parameter_is_required(missing: str) -> None:
    _auth()
    query = {k: v for k, v in QUERY.items() if k != missing}
    async with _client() as client:
        response = await client.get(BASE, params=query)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
