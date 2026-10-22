from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    get_current_user,
    has_any_privilege,
    require_any_privilege,
    require_privilege,
)
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.customer import (
    CustomerCreate,
    CustomerDiscountCreate,
    CustomerDiscountResponse,
    CustomerDiscountUpdate,
    CustomerListItem,
    CustomerResponse,
    CustomerUpdate,
)
from app.services import customer_discount_service, customer_service

router = APIRouter()

_READ = require_privilege(SystemObject.CUSTOMERS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.CUSTOMERS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.CUSTOMERS, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.CUSTOMERS, AccessRight.DELETE)
# Also read from other modules' screens (spec 022 FR-005).
_LOOKUP = require_any_privilege(
    (SystemObject.CUSTOMERS, AccessRight.READ),
    (SystemObject.SALES_ORDERS, AccessRight.READ),
    (SystemObject.SALES_QUOTES, AccessRight.READ),
    (SystemObject.POS, AccessRight.READ),
)


@router.get('', response_model=ListResponse[CustomerListItem])
async def list_customers(
    search: str | None = Query(None),
    status: EntityStatus | None = Query(None),
    price_list: int | None = Query(None),
    salesperson: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_LOOKUP),
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


@router.post('', response_model=CustomerResponse, status_code=http_status.HTTP_201_CREATED)
async def create_customer(
    data: CustomerCreate,
    _: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    customer = await customer_service.create_customer(db, data)
    return CustomerResponse.model_validate(customer)


@router.get('/{customer_id}', response_model=CustomerResponse)
async def get_customer(
    customer_id: int,
    _: CurrentUser = Depends(_LOOKUP),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    customer = await customer_service.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Customer not found')
    return CustomerResponse.model_validate(customer)


@router.put('/{customer_id}', response_model=CustomerResponse)
async def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    # mbe-ui links a new ship-to address or contact from the sales-order editor through this
    # route, so a body that touches nothing else also accepts a sales Update (spec 022 FR-006).
    grants = [(SystemObject.CUSTOMERS, AccessRight.UPDATE)]
    if data.model_dump(exclude_none=True).keys() <= {'addresses', 'contacts'}:
        grants += [
            (SystemObject.SALES_ORDERS, AccessRight.UPDATE),
            (SystemObject.SALES_QUOTES, AccessRight.UPDATE),
        ]
    if not current.administrator and not await has_any_privilege(db, current.user_id, grants):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN, detail='Insufficient privileges'
        )
    customer = await customer_service.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Customer not found')
    customer = await customer_service.update_customer(db, customer, data)
    return CustomerResponse.model_validate(customer)


@router.delete('/{customer_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_customer(
    customer_id: int,
    _: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> None:
    customer = await customer_service.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Customer not found')
    await customer_service.delete_customer(db, customer, settings.default_customer_id)


async def _require_customer(db: AsyncSession, customer_id: int) -> None:
    if await customer_service.get_customer(db, customer_id) is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Customer not found')


async def _require_discount(db: AsyncSession, customer_id: int, customer_discount_id: int):
    await _require_customer(db, customer_id)
    discount = await customer_discount_service.get_discount(db, customer_id, customer_discount_id)
    if discount is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Discount not found')
    return discount


@router.get('/{customer_id}/discounts', response_model=list[CustomerDiscountResponse])
async def list_discounts(
    customer_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[CustomerDiscountResponse]:
    await _require_customer(db, customer_id)
    discounts = await customer_discount_service.list_discounts(db, customer_id)
    return [CustomerDiscountResponse.model_validate(d) for d in discounts]


@router.post(
    '/{customer_id}/discounts',
    response_model=CustomerDiscountResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_discount(
    customer_id: int,
    data: CustomerDiscountCreate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerDiscountResponse:
    await _require_customer(db, customer_id)
    discount = await customer_discount_service.create_discount(db, customer_id, data)
    return CustomerDiscountResponse.model_validate(discount)


@router.put(
    '/{customer_id}/discounts/{customer_discount_id}', response_model=CustomerDiscountResponse
)
async def update_discount(
    customer_id: int,
    customer_discount_id: int,
    data: CustomerDiscountUpdate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerDiscountResponse:
    discount = await _require_discount(db, customer_id, customer_discount_id)
    discount = await customer_discount_service.update_discount(db, discount, data)
    return CustomerDiscountResponse.model_validate(discount)


@router.delete(
    '/{customer_id}/discounts/{customer_discount_id}',
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_discount(
    customer_id: int,
    customer_discount_id: int,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> None:
    discount = await _require_discount(db, customer_id, customer_discount_id)
    await customer_discount_service.delete_discount(db, discount)
