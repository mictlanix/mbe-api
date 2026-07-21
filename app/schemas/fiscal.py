from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.enums import FiscalCertificationProvider
from app.schemas.sat_catalog import SatCatalogResponse

# ── Taxpayer Issuer ───────────────────────────────────────────────────────────


class TaxpayerIssuerCreate(BaseModel):
    taxpayer_issuer_id: str = Field(min_length=12, max_length=13)
    name: str | None = None
    regime: str
    provider: FiscalCertificationProvider = FiscalCertificationProvider.NONE
    postal_code: str | None = None
    comment: str | None = None


class TaxpayerIssuerUpdate(BaseModel):
    name: str | None = None
    regime: str | None = None
    provider: FiscalCertificationProvider | None = None
    postal_code: str | None = None
    comment: str | None = None


class TaxpayerIssuerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    taxpayer_issuer_id: str
    name: str | None
    regime: SatCatalogResponse | None = Field(
        validation_alias=AliasChoices('regime_detail', 'regime')
    )
    provider: FiscalCertificationProvider
    postal_code: SatCatalogResponse | None = Field(
        validation_alias=AliasChoices('postal_code_detail', 'postal_code')
    )
    comment: str | None
