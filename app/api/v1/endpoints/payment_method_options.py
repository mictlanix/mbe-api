from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import EntityStatus
from app.schemas import ListResponse
from app.schemas.core import (
    PaymentMethodOptionCreate,
    PaymentMethodOptionResponse,
    PaymentMethodOptionUpdate,
)
from app.services import payment_method_option_service

router = APIRouter()


@router.get("", response_model=ListResponse[PaymentMethodOptionResponse])
async def list_payment_method_options(
    facility: int | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[PaymentMethodOptionResponse]:
    items, total = await payment_method_option_service.list_payment_method_options(
        db, facility=facility, status=status, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=PaymentMethodOptionResponse, status_code=status.HTTP_201_CREATED)
async def create_payment_method_option(
    data: PaymentMethodOptionCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentMethodOptionResponse:
    pmo = await payment_method_option_service.create_payment_method_option(db, data)
    return PaymentMethodOptionResponse.model_validate(pmo)


@router.get("/{payment_method_option_id}", response_model=PaymentMethodOptionResponse)
async def get_payment_method_option(
    payment_method_option_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentMethodOptionResponse:
    pmo = await payment_method_option_service.get_payment_method_option(
        db, payment_method_option_id
    )
    if pmo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment method option not found"
        )
    return PaymentMethodOptionResponse.model_validate(pmo)


@router.put("/{payment_method_option_id}", response_model=PaymentMethodOptionResponse)
async def update_payment_method_option(
    payment_method_option_id: int,
    data: PaymentMethodOptionUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentMethodOptionResponse:
    pmo = await payment_method_option_service.get_payment_method_option(
        db, payment_method_option_id
    )
    if pmo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment method option not found"
        )
    pmo = await payment_method_option_service.update_payment_method_option(db, pmo, data)
    return PaymentMethodOptionResponse.model_validate(pmo)


@router.delete("/{payment_method_option_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_payment_method_option(
    payment_method_option_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    pmo = await payment_method_option_service.get_payment_method_option(
        db, payment_method_option_id
    )
    if pmo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment method option not found"
        )
    await payment_method_option_service.delete_payment_method_option(db, pmo)
