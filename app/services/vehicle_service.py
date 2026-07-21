from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Vehicle
from app.schemas.core import VehicleCreate, VehicleUpdate


async def list_vehicles(
    db: AsyncSession,
    *,
    search: str | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Vehicle], int]:
    base = select(Vehicle)
    count_q = select(func.count()).select_from(Vehicle)

    if search:
        term = f'%{search}%'
        condition = or_(
            Vehicle.license_plate.ilike(term),
            Vehicle.name.ilike(term),
            Vehicle.nickname.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    if status is not None:
        base = base.where(Vehicle.status == status)
        count_q = count_q.where(Vehicle.status == status)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_vehicle(db: AsyncSession, vehicle_id: int) -> Vehicle | None:
    return await db.get(Vehicle, vehicle_id)


async def create_vehicle(db: AsyncSession, data: VehicleCreate) -> Vehicle:
    vehicle = Vehicle(
        license_plate=data.license_plate,
        name=data.name,
        nickname=data.nickname,
        tons_capacity=data.tons_capacity,
        status=data.status,
    )
    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def update_vehicle(db: AsyncSession, vehicle: Vehicle, data: VehicleUpdate) -> Vehicle:
    if data.license_plate is not None:
        vehicle.license_plate = data.license_plate
    if data.name is not None:
        vehicle.name = data.name
    if data.nickname is not None:
        vehicle.nickname = data.nickname
    if data.tons_capacity is not None:
        vehicle.tons_capacity = data.tons_capacity
    if data.status is not None:
        vehicle.status = data.status
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def delete_vehicle(db: AsyncSession, vehicle: Vehicle) -> None:
    await db.delete(vehicle)
    await db.commit()
