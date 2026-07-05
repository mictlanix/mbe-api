from pydantic import BaseModel


class SatCatalogResponse(BaseModel):
    id: str
    description: str | None = None


class SatUnitOfMeasurementResponse(BaseModel):
    """Full sat_unit_of_measurement record, used when embedding it as a product's
    unit_of_measurement FK (as opposed to the generic id/description shape used by the
    standalone /api/v1/sat/units-of-measurement endpoints)."""

    id: str
    name: str
    description: str | None = None
    symbol: str | None = None
