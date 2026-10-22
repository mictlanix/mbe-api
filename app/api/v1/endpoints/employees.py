from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_any_privilege, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.core import EmployeeCreate, EmployeeResponse, EmployeeUpdate
from app.services import employee_service

router = APIRouter()

_CREATE = require_privilege(SystemObject.EMPLOYEES, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.EMPLOYEES, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.EMPLOYEES, AccessRight.DELETE)
# Also read from other modules' screens (spec 022 FR-005).
_LOOKUP = require_any_privilege(
    (SystemObject.EMPLOYEES, AccessRight.READ),
    (SystemObject.SALES_ORDERS, AccessRight.READ),
    (SystemObject.SALES_QUOTES, AccessRight.READ),
    (SystemObject.POS, AccessRight.READ),
    (SystemObject.CUSTOMERS, AccessRight.READ),
    (SystemObject.VEHICLE_OPERATORS, AccessRight.READ),
)


@router.get('', response_model=ListResponse[EmployeeResponse])
async def list_employees(
    search: str | None = Query(None),
    status: EntityStatus | None = Query(None),
    sales_person: bool | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_LOOKUP),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[EmployeeResponse]:
    items, total = await employee_service.list_employees(
        db, search=search, status=status, sales_person=sales_person, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=EmployeeResponse, status_code=http_status.HTTP_201_CREATED)
async def create_employee(
    data: EmployeeCreate,
    _: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.create_employee(db, data)
    return EmployeeResponse.model_validate(employee)


@router.get('/{employee_id}', response_model=EmployeeResponse)
async def get_employee(
    employee_id: int,
    _: CurrentUser = Depends(_LOOKUP),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Employee not found')
    return EmployeeResponse.model_validate(employee)


@router.put('/{employee_id}', response_model=EmployeeResponse)
async def update_employee(
    employee_id: int,
    data: EmployeeUpdate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> EmployeeResponse:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Employee not found')
    employee = await employee_service.update_employee(db, employee, data)
    return EmployeeResponse.model_validate(employee)


@router.delete('/{employee_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_employee(
    employee_id: int,
    _: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> None:
    employee = await employee_service.get_employee(db, employee_id)
    if employee is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Employee not found')
    await employee_service.delete_employee(db, employee)
