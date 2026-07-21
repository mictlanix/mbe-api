from collections.abc import Sequence

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.enums import EntityStatus
from app.models.fiscal import TaxpayerCertificate, TaxpayerIssuer
from app.services.csd_service import ParsedCsd

# The CSD binaries and the raw key password are never returned by the API, so they are kept
# out of every query rather than loaded and then dropped at serialization time.
_METADATA_ONLY = load_only(
    TaxpayerCertificate.taxpayer_certificate_id,
    TaxpayerCertificate.taxpayer,
    TaxpayerCertificate.valid_from,
    TaxpayerCertificate.valid_to,
    TaxpayerCertificate.status,
)


async def list_taxpayer_certificates(
    db: AsyncSession,
    *,
    taxpayer: str | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[TaxpayerCertificate], int]:
    base = select(TaxpayerCertificate).options(_METADATA_ONLY)
    count_q = select(func.count()).select_from(TaxpayerCertificate)

    if taxpayer is not None:
        base = base.where(TaxpayerCertificate.taxpayer == taxpayer)
        count_q = count_q.where(TaxpayerCertificate.taxpayer == taxpayer)

    if status is not None:
        base = base.where(TaxpayerCertificate.status == status)
        count_q = count_q.where(TaxpayerCertificate.status == status)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_taxpayer_certificate(
    db: AsyncSession, certificate_id: str
) -> TaxpayerCertificate | None:
    query = (
        select(TaxpayerCertificate)
        .options(_METADATA_ONLY)
        .where(TaxpayerCertificate.taxpayer_certificate_id == certificate_id)
    )
    return (await db.execute(query)).scalar_one_or_none()


async def create_taxpayer_certificate(
    db: AsyncSession,
    parsed: ParsedCsd,
    *,
    taxpayer: str,
    certificate_data: bytes,
    key_data: bytes,
    key_password: bytes,
) -> TaxpayerCertificate:
    """Store a CSD pair already validated by `csd_service.parse_csd`.

    `taxpayer` is the issuer the certificate is being attached to; when the certificate names
    a holder, the two must agree.
    """
    if parsed.taxpayer is not None and parsed.taxpayer != taxpayer.upper():
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Certificate belongs to {parsed.taxpayer}, not to {taxpayer.upper()}',
        )

    if await db.get(TaxpayerIssuer, taxpayer.upper()) is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Taxpayer issuer not found',
        )

    if await db.get(TaxpayerCertificate, parsed.certificate_id) is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f'Certificate {parsed.certificate_id} is already registered',
        )

    certificate = TaxpayerCertificate(
        taxpayer_certificate_id=parsed.certificate_id,
        taxpayer=taxpayer.upper(),
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=key_password,
        valid_from=parsed.valid_from,
        valid_to=parsed.valid_to,
        status=EntityStatus.ACTIVE,
    )
    db.add(certificate)
    await db.commit()
    return certificate
