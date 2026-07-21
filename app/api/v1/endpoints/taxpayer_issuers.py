from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.fiscal import TaxpayerIssuerResponse
from app.services import taxpayer_issuer_service

router = APIRouter()


@router.get('', response_model=ListResponse[TaxpayerIssuerResponse])
async def list_taxpayer_issuers(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[TaxpayerIssuerResponse]:
    items, total = await taxpayer_issuer_service.list_taxpayer_issuers(
        db, search=search, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.get('/{rfc}', response_model=TaxpayerIssuerResponse)
async def get_taxpayer_issuer(
    rfc: str,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerIssuerResponse:
    issuer = await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc)
    if issuer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )
    return TaxpayerIssuerResponse.model_validate(issuer)
