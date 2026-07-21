"""Covers GH #102: a point of sale must not pair a warehouse with a facility it does not
belong to, on create or on update."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.enums import EntityStatus
from app.models.core import PointSale, Warehouse
from app.schemas.core import PointSaleCreate, PointSaleUpdate
from app.services import point_sale_service


def _warehouse(facility: int) -> Warehouse:
    return Warehouse(
        warehouse_id=7,
        facility=facility,
        code='WH1',
        name='Main',
        comment=None,
        status=EntityStatus.ACTIVE,
    )


def _db(warehouse: Warehouse | None) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=warehouse)
    return db


def _point_sale() -> PointSale:
    return PointSale(
        point_sale_id=1,
        facility=1,
        code='POS1',
        name='Register 1',
        warehouse=7,
        comment=None,
        status=EntityStatus.ACTIVE,
    )


def _create(facility: int = 1, warehouse: int = 7) -> PointSaleCreate:
    return PointSaleCreate(facility=facility, code='POS1', name='Register 1', warehouse=warehouse)


@pytest.mark.asyncio
async def test_create_rejects_warehouse_from_another_facility() -> None:
    with pytest.raises(HTTPException) as exc:
        await point_sale_service.create_point_sale(_db(_warehouse(facility=2)), _create())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_unknown_warehouse() -> None:
    with pytest.raises(HTTPException) as exc:
        await point_sale_service.create_point_sale(_db(None), _create())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_rejects_warehouse_from_another_facility() -> None:
    with pytest.raises(HTTPException) as exc:
        await point_sale_service.update_point_sale(
            _db(_warehouse(facility=2)), _point_sale(), PointSaleUpdate(warehouse=9)
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_update_facility_alone_is_checked_against_the_existing_warehouse() -> None:
    """Changing only the facility must not strand the warehouse already on the record."""
    with pytest.raises(HTTPException) as exc:
        await point_sale_service.update_point_sale(
            _db(_warehouse(facility=1)), _point_sale(), PointSaleUpdate(facility=3)
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_update_without_facility_or_warehouse_skips_the_check() -> None:
    db = _db(None)
    with patch.object(point_sale_service, '_attach_relations', new=AsyncMock()):
        ps = await point_sale_service.update_point_sale(
            db, _point_sale(), PointSaleUpdate(name='R2')
        )
    assert ps.name == 'R2'
    db.get.assert_not_awaited()
