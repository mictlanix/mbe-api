from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.core.errors import not_found
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.models.fiscal import TaxpayerCertificate
from app.schemas import ListResponse
from app.schemas.fiscal import TaxpayerCertificateResponse
from app.services import csd_service, taxpayer_certificate_service

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


@router.post(
    '', response_model=TaxpayerCertificateResponse, status_code=http_status.HTTP_201_CREATED
)
async def upload_taxpayer_certificate(
    taxpayer: str = Form(..., min_length=12, max_length=13),
    certificate: UploadFile = File(..., description='DER encoded .cer'),
    key: UploadFile = File(..., description='DER encoded, password protected .key'),
    key_password: str = Form(...),
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerCertificateResponse:
    """Register a CSD pair. The certificate number and validity window are read from the
    certificate itself, not taken from the request."""
    certificate_data = await certificate.read()
    key_data = await key.read()
    password_bytes = key_password.encode()

    parsed = await csd_service.parse_csd(certificate_data, key_data, password_bytes)

    stored = await taxpayer_certificate_service.create_taxpayer_certificate(
        db,
        parsed,
        taxpayer=taxpayer,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=password_bytes,
    )
    return TaxpayerCertificateResponse.model_validate(stored)


@router.get('/{certificate_id}', response_model=TaxpayerCertificateResponse)
async def get_taxpayer_certificate(
    certificate_id: str,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerCertificateResponse:
    certificate = await taxpayer_certificate_service.get_taxpayer_certificate(db, certificate_id)
    if certificate is None:
        raise not_found(TaxpayerCertificate, certificate_id)
    return TaxpayerCertificateResponse.model_validate(certificate)
