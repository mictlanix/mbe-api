from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
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


@dataclass(frozen=True)
class SatCatalogConfig:
    model: type
    pk_field: str
    description_field: str | None = None
    extra_search_fields: tuple[str, ...] = field(default_factory=tuple)


SAT_CATALOG_MAP: dict[str, SatCatalogConfig] = {
    "cfdi-usages": SatCatalogConfig(SatCfdiUsage, "sat_cfdi_usage_id", "description"),
    "countries": SatCatalogConfig(SatCountry, "sat_country_id", "description"),
    "currencies": SatCatalogConfig(SatCurrency, "sat_currency_id", "description"),
    "postal-codes": SatCatalogConfig(
        SatPostalCode,
        "sat_postal_code_id",
        extra_search_fields=("state", "borough", "locality"),
    ),
    "product-services": SatCatalogConfig(
        SatProductService,
        "sat_product_service_id",
        "description",
        extra_search_fields=("keywords",),
    ),
    "reason-cancellations": SatCatalogConfig(
        SatReasonCancellation, "sat_reason_cancellation_id", "description"
    ),
    "tax-regimes": SatCatalogConfig(SatTaxRegime, "sat_tax_regime_id", "description"),
    "units-of-measurement": SatCatalogConfig(
        SatUnitOfMeasurement,
        "sat_unit_of_measurement_id",
        "name",
        extra_search_fields=("description", "symbol"),
    ),
}


async def list_sat(
    db: AsyncSession,
    config: SatCatalogConfig,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Any], int]:
    model = config.model
    base = select(model)
    count_q = select(func.count()).select_from(model)

    if search:
        term = f"%{search}%"
        description_fields = (config.description_field,) if config.description_field else ()
        search_fields = (config.pk_field, *description_fields, *config.extra_search_fields)
        condition = or_(*(getattr(model, f).ilike(term) for f in search_fields))
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_sat(db: AsyncSession, model: type, id: str) -> Any | None:
    return await db.get(model, id)
