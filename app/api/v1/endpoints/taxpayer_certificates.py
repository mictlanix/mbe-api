from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.fiscal import TaxpayerCertificateResponse
from app.services import taxpayer_certificate_service

router = APIRouter()


@router.get('', response_model=ListResponse[TaxpayerCertificateResponse])
async def list_taxpayer_certificates(
    taxpayer: str | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[TaxpayerCertificateResponse]:
    items, total = await taxpayer_certificate_service.list_taxpayer_certificates(
        db, taxpayer=taxpayer, status=status, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.get('/{certificate_id}', response_model=TaxpayerCertificateResponse)
async def get_taxpayer_certificate(
    certificate_id: str,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerCertificateResponse:
    certificate = await taxpayer_certificate_service.get_taxpayer_certificate(db, certificate_id)
    if certificate is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Taxpayer certificate not found'
        )
    return TaxpayerCertificateResponse.model_validate(certificate)
