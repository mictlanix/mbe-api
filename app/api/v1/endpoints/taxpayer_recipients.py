from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.customer import (
    TaxpayerRecipientCreate,
    TaxpayerRecipientResponse,
    TaxpayerRecipientUpdate,
)
from app.services import taxpayer_recipient_service

router = APIRouter()


@router.get('', response_model=ListResponse[TaxpayerRecipientResponse])
async def list_taxpayer_recipients(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[TaxpayerRecipientResponse]:
    items, total = await taxpayer_recipient_service.list_taxpayer_recipients(
        db, search=search, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=TaxpayerRecipientResponse, status_code=status.HTTP_201_CREATED)
async def create_taxpayer_recipient(
    data: TaxpayerRecipientCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerRecipientResponse:
    tr = await taxpayer_recipient_service.create_taxpayer_recipient(db, data)
    return TaxpayerRecipientResponse.model_validate(tr)


@router.get('/{rfc}', response_model=TaxpayerRecipientResponse)
async def get_taxpayer_recipient(
    rfc: str,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerRecipientResponse:
    tr = await taxpayer_recipient_service.get_taxpayer_recipient(db, rfc)
    if tr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer recipient not found'
        )
    return TaxpayerRecipientResponse.model_validate(tr)


@router.put('/{rfc}', response_model=TaxpayerRecipientResponse)
async def update_taxpayer_recipient(
    rfc: str,
    data: TaxpayerRecipientUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerRecipientResponse:
    tr = await taxpayer_recipient_service.get_taxpayer_recipient(db, rfc)
    if tr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer recipient not found'
        )
    tr = await taxpayer_recipient_service.update_taxpayer_recipient(db, tr, data)
    return TaxpayerRecipientResponse.model_validate(tr)


@router.delete('/{rfc}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_taxpayer_recipient(
    rfc: str,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    tr = await taxpayer_recipient_service.get_taxpayer_recipient(db, rfc)
    if tr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer recipient not found'
        )
    await taxpayer_recipient_service.delete_taxpayer_recipient(db, tr)
