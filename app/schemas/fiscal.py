from decimal import Decimal
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.enums import EntityStatus, FiscalCertificationProvider
from app.schemas import LocalDateTime
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


# ── Taxpayer Certificate ──────────────────────────────────────────────────────


class TaxpayerCertificateResponse(BaseModel):
    """Metadata only. `certificate_data`, `key_data` and `key_password` hold the uploaded
    CSD binaries and its raw password, and are never returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    taxpayer_certificate_id: str
    taxpayer: str
    valid_from: LocalDateTime
    valid_to: LocalDateTime
    status: EntityStatus


# ── Fiscal Document (#230, spec 020) ──────────────────────────────────────────


class FiscalDocumentSummary(BaseModel):
    """One row of the list. `total` is the stamped XML's, `None` when there is no XML."""

    model_config = ConfigDict(from_attributes=True)

    fiscal_document_id: int
    type: int
    version: Decimal
    batch: str | None
    serial: int | None
    issuer: str
    issuer_name: str | None
    recipient: str | None
    recipient_name: str | None
    issued: LocalDateTime | None
    stamp_uuid: str | None
    status: Literal['draft', 'issued', 'cancelled']
    total: Decimal | None


class FiscalDocumentLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fiscal_document_detail_id: int
    product_service: str | None
    product_code: str | None
    product_name: str
    unit_of_measurement: str | None
    unit_of_measurement_name: str | None
    quantity: Decimal
    price: Decimal
    discount: Decimal
    tax_rate: Decimal
    tax_included: bool
    comment: str | None


class FiscalDocumentResponse(FiscalDocumentSummary):
    issuer_regime: str | None
    issuer_regime_name: str | None
    taxpayer_regime: str | None
    taxpayer_postal_code: str | None
    usage: str | None
    payment_method: int
    payment_terms: int
    currency: int
    exchange_rate: Decimal
    issued_location: str
    reference: str | None
    comment: str | None
    stamped: LocalDateTime | None
    cancellation_date: LocalDateTime | None
    cancellation_reason: str | None
    lines: list[FiscalDocumentLine]
