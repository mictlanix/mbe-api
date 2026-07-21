from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fiscal import (
    TaxpayerIssuer,
)
from app.models.sat_catalog import SatPostalCode, SatTaxRegime
from app.schemas.fiscal import TaxpayerIssuerCreate, TaxpayerIssuerUpdate
from app.services.fk_expansion import batch_fetch
from app.services.references import assert_not_referenced
from app.services.sat_catalog_service import SAT_CATALOG_MAP, to_response


async def _attach_relations(db: AsyncSession, issuers: Sequence[TaxpayerIssuer]) -> None:
    if not issuers:
        return
    postal_config = SAT_CATALOG_MAP['postal-codes']
    regime_config = SAT_CATALOG_MAP['tax-regimes']

    postal_codes_by_id = await batch_fetch(
        db, SatPostalCode, SatPostalCode.sat_postal_code_id, (i.postal_code for i in issuers)
    )
    regimes_by_id = await batch_fetch(
        db, SatTaxRegime, SatTaxRegime.sat_tax_regime_id, (i.regime for i in issuers)
    )

    for i in issuers:
        # Written under separate keys: `postal_code` and `regime` are mapped columns and these
        # instances are shared through the session identity map, so overwriting them corrupts
        # every other response that reads the raw FKs (cf. #95).
        postal_row = postal_codes_by_id.get(i.postal_code)
        i.__dict__['postal_code_detail'] = (
            to_response(postal_row, postal_config) if postal_row else None
        )
        regime_row = regimes_by_id.get(i.regime)
        i.__dict__['regime_detail'] = to_response(regime_row, regime_config) if regime_row else None


async def list_taxpayer_issuers(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[TaxpayerIssuer], int]:
    base = select(TaxpayerIssuer)
    count_q = select(func.count()).select_from(TaxpayerIssuer)

    if search:
        term = f'%{search}%'
        condition = or_(
            TaxpayerIssuer.taxpayer_issuer_id.ilike(term), TaxpayerIssuer.name.ilike(term)
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_taxpayer_issuer(db: AsyncSession, rfc: str) -> TaxpayerIssuer | None:
    issuer = await db.get(TaxpayerIssuer, rfc)
    if issuer is None:
        return None
    await _attach_relations(db, [issuer])
    return issuer


async def create_taxpayer_issuer(db: AsyncSession, data: TaxpayerIssuerCreate) -> TaxpayerIssuer:
    issuer = TaxpayerIssuer(
        taxpayer_issuer_id=data.taxpayer_issuer_id,
        name=data.name,
        regime=data.regime,
        provider=data.provider,
        postal_code=data.postal_code,
        comment=data.comment,
    )
    db.add(issuer)
    await db.commit()
    await db.refresh(issuer)
    await _attach_relations(db, [issuer])
    return issuer


async def update_taxpayer_issuer(
    db: AsyncSession, issuer: TaxpayerIssuer, data: TaxpayerIssuerUpdate
) -> TaxpayerIssuer:
    if data.name is not None:
        issuer.name = data.name
    if data.regime is not None:
        issuer.regime = data.regime
    if data.provider is not None:
        issuer.provider = data.provider
    if data.postal_code is not None:
        issuer.postal_code = data.postal_code
    if data.comment is not None:
        issuer.comment = data.comment
    await db.commit()
    await db.refresh(issuer)
    await _attach_relations(db, [issuer])
    return issuer


async def delete_taxpayer_issuer(db: AsyncSession, issuer: TaxpayerIssuer) -> None:
    await assert_not_referenced(db, issuer)
    await db.delete(issuer)
    await db.commit()
