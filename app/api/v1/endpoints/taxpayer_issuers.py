from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.fiscal import (
    TaxpayerIssuerCreate,
    TaxpayerIssuerResponse,
    TaxpayerIssuerUpdate,
)
from app.services import taxpayer_issuer_service

router = APIRouter()


@router.get('', response_model=ListResponse[TaxpayerIssuerResponse])
async def list_taxpayer_issuers(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[TaxpayerIssuerResponse]:
    items, total = await taxpayer_issuer_service.list_taxpayer_issuers(
        db, search=search, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=TaxpayerIssuerResponse, status_code=status.HTTP_201_CREATED)
async def create_taxpayer_issuer(
    data: TaxpayerIssuerCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerIssuerResponse:
    issuer = await taxpayer_issuer_service.create_taxpayer_issuer(db, data)
    return TaxpayerIssuerResponse.model_validate(issuer)


@router.get('/{rfc}', response_model=TaxpayerIssuerResponse)
async def get_taxpayer_issuer(
    rfc: str,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerIssuerResponse:
    issuer = await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc)
    if issuer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )
    return TaxpayerIssuerResponse.model_validate(issuer)


@router.put('/{rfc}', response_model=TaxpayerIssuerResponse)
async def update_taxpayer_issuer(
    rfc: str,
    data: TaxpayerIssuerUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerIssuerResponse:
    issuer = await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc)
    if issuer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )
    issuer = await taxpayer_issuer_service.update_taxpayer_issuer(db, issuer, data)
    return TaxpayerIssuerResponse.model_validate(issuer)


@router.delete('/{rfc}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_taxpayer_issuer(
    rfc: str,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    issuer = await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc)
    if issuer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )
    await taxpayer_issuer_service.delete_taxpayer_issuer(db, issuer)
