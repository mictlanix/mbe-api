from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Employee
from app.schemas.core import EmployeeCreate, EmployeeUpdate


async def list_employees(
    db: AsyncSession,
    *,
    search: str | None = None,
    active: bool | None = None,
    sales_person: bool | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Employee], int]:
    base = select(Employee)
    count_q = select(func.count()).select_from(Employee)

    if search:
        term = f"%{search}%"
        condition = or_(
            Employee.first_name.ilike(term),
            Employee.last_name.ilike(term),
            Employee.nickname.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    if active is not None:
        base = base.where(Employee.active == active)
        count_q = count_q.where(Employee.active == active)
    if sales_person is not None:
        base = base.where(Employee.sales_person == sales_person)
        count_q = count_q.where(Employee.sales_person == sales_person)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_employee(db: AsyncSession, employee_id: int) -> Employee | None:
    return await db.get(Employee, employee_id)


async def create_employee(db: AsyncSession, data: EmployeeCreate) -> Employee:
    employee = Employee(
        first_name=data.first_name,
        last_name=data.last_name,
        nickname=data.nickname,
        gender=data.gender,
        birthday=data.birthday,
        taxpayer_id=data.taxpayer_id,
        sales_person=data.sales_person,
        active=data.active,
        personal_id=data.personal_id,
        start_job_date=data.start_job_date,
        enroll_number=data.enroll_number,
        comment=data.comment,
        disabled=False,
    )
    db.add(employee)
    await db.commit()
    await db.refresh(employee)
    return employee


async def update_employee(db: AsyncSession, employee: Employee, data: EmployeeUpdate) -> Employee:
    if data.first_name is not None:
        employee.first_name = data.first_name
    if data.last_name is not None:
        employee.last_name = data.last_name
    if data.nickname is not None:
        employee.nickname = data.nickname
    if data.gender is not None:
        employee.gender = data.gender
    if data.birthday is not None:
        employee.birthday = data.birthday
    if data.taxpayer_id is not None:
        employee.taxpayer_id = data.taxpayer_id
    if data.sales_person is not None:
        employee.sales_person = data.sales_person
    if data.active is not None:
        employee.active = data.active
    if data.personal_id is not None:
        employee.personal_id = data.personal_id
    if data.start_job_date is not None:
        employee.start_job_date = data.start_job_date
    if data.enroll_number is not None:
        employee.enroll_number = data.enroll_number
    if data.comment is not None:
        employee.comment = data.comment
    if data.disabled is not None:
        employee.disabled = data.disabled
    await db.commit()
    await db.refresh(employee)
    return employee


async def delete_employee(db: AsyncSession, employee: Employee) -> None:
    await db.delete(employee)
    await db.commit()
