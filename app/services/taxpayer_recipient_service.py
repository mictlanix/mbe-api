from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import TaxpayerRecipient
from app.models.sat_catalog import SatPostalCode, SatTaxRegime
from app.schemas.customer import TaxpayerRecipientCreate, TaxpayerRecipientUpdate
from app.services.sat_catalog_service import SAT_CATALOG_MAP, to_response


async def _attach_relations(db: AsyncSession, recipients: Sequence[TaxpayerRecipient]) -> None:
    if not recipients:
        return
    postal_config = SAT_CATALOG_MAP['postal-codes']
    regime_config = SAT_CATALOG_MAP['tax-regimes']

    postal_ids = {r.postal_code for r in recipients if r.postal_code is not None}
    postal_codes_by_id: dict[str, SatPostalCode] = {}
    if postal_ids:
        rows = (
            (
                await db.execute(
                    select(SatPostalCode).where(SatPostalCode.sat_postal_code_id.in_(postal_ids))
                )
            )
            .scalars()
            .all()
        )
        postal_codes_by_id = {p.sat_postal_code_id: p for p in rows}

    regime_ids = {r.regime for r in recipients if r.regime is not None}
    regimes_by_id: dict[str, SatTaxRegime] = {}
    if regime_ids:
        rows = (
            (
                await db.execute(
                    select(SatTaxRegime).where(SatTaxRegime.sat_tax_regime_id.in_(regime_ids))
                )
            )
            .scalars()
            .all()
        )
        regimes_by_id = {r.sat_tax_regime_id: r for r in rows}

    for r in recipients:
        # Written under a separate key: the mapped column is shared through the session
        # identity map, so overwriting it corrupts every reader of the raw FK (#95, #104).
        postal_row = postal_codes_by_id.get(r.postal_code) if r.postal_code is not None else None
        r.__dict__['postal_code_detail'] = (
            to_response(postal_row, postal_config) if postal_row else None
        )
        regime_row = regimes_by_id.get(r.regime) if r.regime is not None else None
        r.__dict__['regime_detail'] = to_response(regime_row, regime_config) if regime_row else None


async def list_taxpayer_recipients(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[TaxpayerRecipient], int]:
    base = select(TaxpayerRecipient)
    count_q = select(func.count()).select_from(TaxpayerRecipient)

    if search:
        term = f'%{search}%'
        condition = or_(
            TaxpayerRecipient.taxpayer_recipient_id.ilike(term),
            TaxpayerRecipient.name.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_taxpayer_recipient(db: AsyncSession, rfc: str) -> TaxpayerRecipient | None:
    tr = await db.get(TaxpayerRecipient, rfc)
    if tr is None:
        return None
    await _attach_relations(db, [tr])
    return tr


async def create_taxpayer_recipient(
    db: AsyncSession, data: TaxpayerRecipientCreate
) -> TaxpayerRecipient:
    tr = TaxpayerRecipient(
        taxpayer_recipient_id=data.taxpayer_recipient_id,
        name=data.name,
        email=data.email,
        postal_code=data.postal_code,
        regime=data.regime,
    )
    db.add(tr)
    await db.commit()
    await db.refresh(tr)
    await _attach_relations(db, [tr])
    return tr


async def update_taxpayer_recipient(
    db: AsyncSession, tr: TaxpayerRecipient, data: TaxpayerRecipientUpdate
) -> TaxpayerRecipient:
    if data.name is not None:
        tr.name = data.name
    if data.email is not None:
        tr.email = data.email
    if data.postal_code is not None:
        tr.postal_code = data.postal_code
    if data.regime is not None:
        tr.regime = data.regime
    await db.commit()
    await db.refresh(tr)
    await _attach_relations(db, [tr])
    return tr


async def delete_taxpayer_recipient(db: AsyncSession, tr: TaxpayerRecipient) -> None:
    await db.delete(tr)
    await db.commit()
