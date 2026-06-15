from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import VehicleOperator
from app.schemas.core import VehicleOperatorCreate, VehicleOperatorUpdate


async def list_vehicle_operators(
    db: AsyncSession, *, skip: int = 0, limit: int = 20
) -> tuple[Sequence[VehicleOperator], int]:
    total: int = (await db.execute(select(func.count()).select_from(VehicleOperator))).scalar_one()
    items = (await db.execute(select(VehicleOperator).offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_vehicle_operator(
    db: AsyncSession, vehicle_operator_id: int
) -> VehicleOperator | None:
    return await db.get(VehicleOperator, vehicle_operator_id)


async def create_vehicle_operator(
    db: AsyncSession, data: VehicleOperatorCreate, creator_id: int = 0
) -> VehicleOperator:
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    vo = VehicleOperator(
        driver=data.driver,
        license_type=data.license_type,
        driver_license_number=data.driver_license_number,
        issue_date=data.issue_date,
        expiration_date=data.expiration_date,
        issuing_location=data.issuing_location,
        active=data.active,
        creation_time=now,
        modification_time=now,
        creator=creator_id,
        updater=creator_id,
    )
    db.add(vo)
    await db.commit()
    await db.refresh(vo)
    return vo


async def update_vehicle_operator(
    db: AsyncSession, vo: VehicleOperator, data: VehicleOperatorUpdate, updater_id: int = 0
) -> VehicleOperator:
    if data.driver is not None:
        vo.driver = data.driver
    if data.license_type is not None:
        vo.license_type = data.license_type
    if data.driver_license_number is not None:
        vo.driver_license_number = data.driver_license_number
    if data.issue_date is not None:
        vo.issue_date = data.issue_date
    if data.expiration_date is not None:
        vo.expiration_date = data.expiration_date
    if data.issuing_location is not None:
        vo.issuing_location = data.issuing_location
    if data.active is not None:
        vo.active = data.active
    vo.modification_time = datetime.now(tz=UTC).replace(tzinfo=None)
    vo.updater = updater_id
    await db.commit()
    await db.refresh(vo)
    return vo


async def delete_vehicle_operator(db: AsyncSession, vo: VehicleOperator) -> None:
    await db.delete(vo)
    await db.commit()
