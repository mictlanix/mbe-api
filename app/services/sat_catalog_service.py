from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sat_catalog import (
    SatCfdiUsage,
    SatCountry,
    SatCurrency,
    SatPostalCode,
    SatProductService,
    SatReasonCancellation,
    SatTaxRegime,
    SatUnitOfMeasurement,
)

SAT_CATALOG_MAP: dict[str, tuple[type, str]] = {
    "cfdi-usages": (SatCfdiUsage, "sat_cfdi_usage_id"),
    "countries": (SatCountry, "sat_country_id"),
    "currencies": (SatCurrency, "sat_currency_id"),
    "postal-codes": (SatPostalCode, "sat_postal_code_id"),
    "product-services": (SatProductService, "sat_product_service_id"),
    "reason-cancellations": (SatReasonCancellation, "sat_reason_cancellation_id"),
    "tax-regimes": (SatTaxRegime, "sat_tax_regime_id"),
    "units-of-measurement": (SatUnitOfMeasurement, "sat_unit_of_measurement_id"),
}


async def list_sat(
    db: AsyncSession, model: type, *, skip: int = 0, limit: int = 20
) -> tuple[Sequence[Any], int]:
    total: int = (await db.execute(select(func.count()).select_from(model))).scalar_one()
    items = (await db.execute(select(model).offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_sat(db: AsyncSession, model: type, id: str) -> Any | None:
    return await db.get(model, id)
