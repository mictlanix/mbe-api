"""Vehicle operator timestamps are business wall-clock time, like every other record's (#228).

This service alone stamped UTC wall-clock time into the same naive columns every other module fills
with local time, so its `modification_time` read six hours ahead of the rest of the database.
"""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.core import VehicleOperator
from app.services import vehicle_operator_service

_NOW = datetime(2026, 9, 27, 12, 0)


def _db() -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _fields(**overrides) -> SimpleNamespace:
    base = dict(
        driver=1,
        license_type='A',
        driver_license_number='X1',
        issue_date=date(2026, 1, 1),
        expiration_date=date(2030, 1, 1),
        issuing_location='CDMX',
        status=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clock_and_relations():
    with (
        patch.object(vehicle_operator_service, 'local_now', return_value=_NOW),
        patch.object(vehicle_operator_service, '_attach_relations', AsyncMock()),
    ):
        yield


@pytest.mark.asyncio
async def test_creation_is_stamped_with_business_time() -> None:
    db = _db()

    vo = await vehicle_operator_service.create_vehicle_operator(db, _fields(), creator_id=7)

    assert vo.creation_time == _NOW
    assert vo.modification_time == _NOW


@pytest.mark.asyncio
async def test_an_update_is_stamped_with_business_time() -> None:
    vo = SimpleNamespace(modification_time=datetime(2020, 1, 1), updater=0)
    unchanged = _fields(**{k: None for k in vars(_fields())})

    await vehicle_operator_service.update_vehicle_operator(_db(), vo, unchanged, updater_id=7)

    assert vo.modification_time == _NOW


@pytest.mark.asyncio
async def test_setting_a_license_photo_returns_the_previous_name() -> None:
    db = _db()
    vo = SimpleNamespace(license_photo='old.png')

    previous = await vehicle_operator_service.set_license_photo(db, vo, 'new.png')

    assert previous == 'old.png'
    assert vo.license_photo == 'new.png'
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_setting_a_first_license_photo_has_no_previous_name() -> None:
    vo = SimpleNamespace(license_photo=None)

    assert await vehicle_operator_service.set_license_photo(_db(), vo, 'new.png') is None


@pytest.mark.asyncio
async def test_clearing_a_license_photo_returns_the_previous_name() -> None:
    db = _db()
    vo = SimpleNamespace(license_photo='old.png')

    previous = await vehicle_operator_service.clear_license_photo(db, vo)

    assert previous == 'old.png'
    assert vo.license_photo is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleting_an_operator_returns_its_photo_name() -> None:
    db = _db()
    db.delete = AsyncMock()
    vo = SimpleNamespace(license_photo='old.png')

    with patch.object(vehicle_operator_service, 'assert_not_referenced', AsyncMock()):
        removed = await vehicle_operator_service.delete_vehicle_operator(db, vo)

    assert removed == 'old.png'
    db.commit.assert_awaited_once()


def test_has_license_photo_is_true_only_when_a_photo_is_set() -> None:
    assert VehicleOperator(license_photo='a.png').has_license_photo is True
    assert VehicleOperator(license_photo=None).has_license_photo is False
