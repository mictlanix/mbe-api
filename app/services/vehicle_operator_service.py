from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Employee, VehicleOperator
from app.schemas.core import VehicleOperatorCreate, VehicleOperatorUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, operators: Sequence[VehicleOperator]) -> None:
    if not operators:
        return
    employee_ids = {i for vo in operators for i in (vo.driver, vo.creator, vo.updater)}
    employees_by_id = await batch_fetch(db, Employee, Employee.employee_id, employee_ids)
    for vo in operators:
        vo.__dict__['driver'] = employees_by_id.get(vo.driver)
        vo.__dict__['creator'] = employees_by_id.get(vo.creator)
        vo.__dict__['updater'] = employees_by_id.get(vo.updater)


async def list_vehicle_operators(
    db: AsyncSession,
    *,
    search: str | None = None,
    employee: int | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[VehicleOperator], int]:
    base = select(VehicleOperator)
    count_q = select(func.count()).select_from(VehicleOperator)

    if search:
        term = f'%{search}%'
        join_on = VehicleOperator.driver == Employee.employee_id
        condition = or_(
            VehicleOperator.driver_license_number.ilike(term),
            Employee.first_name.ilike(term),
            Employee.last_name.ilike(term),
            Employee.nickname.ilike(term),
        )
        base = base.join(Employee, join_on).where(condition)
        count_q = count_q.join(Employee, join_on).where(condition)

    if employee is not None:
        base = base.where(VehicleOperator.driver == employee)
        count_q = count_q.where(VehicleOperator.driver == employee)
    if status is not None:
        base = base.where(VehicleOperator.status == status)
        count_q = count_q.where(VehicleOperator.status == status)
    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_vehicle_operator(
    db: AsyncSession, vehicle_operator_id: int
) -> VehicleOperator | None:
    vo = await db.get(VehicleOperator, vehicle_operator_id)
    if vo is None:
        return None
    await _attach_relations(db, [vo])
    return vo


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
        status=data.status,
        creation_time=now,
        modification_time=now,
        creator=creator_id,
        updater=creator_id,
    )
    db.add(vo)
    await db.commit()
    await db.refresh(vo)
    await _attach_relations(db, [vo])
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
    if data.status is not None:
        vo.status = data.status
    vo.modification_time = datetime.now(tz=UTC).replace(tzinfo=None)
    vo.updater = updater_id
    await db.commit()
    await db.refresh(vo)
    await _attach_relations(db, [vo])
    return vo


async def delete_vehicle_operator(db: AsyncSession, vo: VehicleOperator) -> None:
    await db.delete(vo)
    await db.commit()
