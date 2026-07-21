from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.sat_catalog import SatCatalogResponse

# ── Taxpayer Issuer ───────────────────────────────────────────────────────────


class TaxpayerIssuerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    taxpayer_issuer_id: str
    name: str | None
    regime: SatCatalogResponse | None = Field(
        validation_alias=AliasChoices('regime_detail', 'regime')
    )
    postal_code: SatCatalogResponse | None = Field(
        validation_alias=AliasChoices('postal_code_detail', 'postal_code')
    )
    comment: str | None
