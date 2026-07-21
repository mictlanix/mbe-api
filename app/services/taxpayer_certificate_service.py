from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.enums import EntityStatus
from app.models.fiscal import TaxpayerCertificate

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
