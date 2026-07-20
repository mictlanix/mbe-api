from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import EntityStatus
from app.schemas import ListResponse
from app.schemas.customer import CustomerCreate, CustomerListItem, CustomerResponse, CustomerUpdate
from app.services import customer_service

router = APIRouter()


@router.get("", response_model=ListResponse[CustomerListItem])
async def list_customers(
    search: str | None = Query(None),
    status: EntityStatus | None = Query(None),
    price_list: int | None = Query(None),
    salesperson: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CustomerListItem]:
    items, total = await customer_service.list_customers(
        db,
        search=search,
        status=status,
        price_list=price_list,
        salesperson=salesperson,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=CustomerResponse, status_code=http_status.HTTP_201_CREATED)
async def create_customer(
    data: CustomerCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    customer = await customer_service.create_customer(db, data)
    return CustomerResponse.model_validate(customer)


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    customer_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    customer = await customer_service.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Customer not found")
    return CustomerResponse.model_validate(customer)


@router.put("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    customer = await customer_service.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Customer not found")
    customer = await customer_service.update_customer(db, customer, data)
    return CustomerResponse.model_validate(customer)


@router.delete("/{customer_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_customer(
    customer_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    customer = await customer_service.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Customer not found")
    await customer_service.delete_customer(db, customer, settings.default_customer_id)
