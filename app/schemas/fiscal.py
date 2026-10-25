from decimal import Decimal
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.enums import (
    EntityStatus,
    FiscalCertificationProvider,
    FiscalDocumentType,
    PaymentMethod,
    PaymentTerms,
)
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
    status: Literal['draft', 'sealed', 'issued', 'cancelled']
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
    product: int | None = None
    order_detail: int | None = None


class FiscalDocumentRelationResponse(BaseModel):
    """A related stamped document. `type` is the SAT code; legacy stores cfdlib's enum number."""

    model_config = ConfigDict(from_attributes=True)

    fiscal_document_relation_id: int
    relation: int
    type: Literal['04', '07'] | None
    stamp_uuid: str | None
    batch: str | None
    serial: int | None

    @field_validator('type', mode='before')
    @classmethod
    def _sat_code(cls, value: object) -> object:
        return relation_code(value)


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
    customer: int | None = None
    facility: int | None = None
    lines: list[FiscalDocumentLine]
    relations: list[FiscalDocumentRelationResponse] = []


def relation_code(stored: object) -> str | None:
    """`'4'` → `'04'`, `'7'` → `'07'`; legacy's untyped `'0'` and null → None (research R5)."""
    if stored is None or str(stored) in ('', '0'):
        return None
    return str(stored).zfill(2)


# ── Taxpayer Batch (#44, spec 027) ────────────────────────────────────────────


class BatchTemplate(BaseModel):
    """The print template, stored as legacy's JSON (`cfdi.batch_settings` reads it)."""

    name: str = Field(min_length=1, max_length=50)
    logo: str | None = None
    header_height: int = Field(0, ge=0)
    footer_height: int = Field(0, ge=0)
    extra_info: list[str] = []


class TaxpayerBatchCreate(BaseModel):
    batch: str = Field(min_length=1, max_length=10)
    type: FiscalDocumentType
    template: BatchTemplate

    @field_validator('batch')
    @classmethod
    def _trim(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('The series cannot be blank')
        return value


class TaxpayerBatchUpdate(TaxpayerBatchCreate):
    pass


class TaxpayerBatchResponse(BaseModel):
    taxpayer_batch_id: int
    taxpayer: str
    batch: str
    type: int
    template: BatchTemplate
    in_use: bool


# ── Invoice editor (#47, spec 027) ────────────────────────────────────────────

#: Tax rates a line may carry: 16 %, 0 % and exempt (stored as -1, as legacy does).
TAX_RATES = (Decimal('0.16'), Decimal('0'), Decimal('-1'))
_PAYMENT_FORMS = frozenset(m for m in PaymentMethod if 1 <= m <= 99)


class InvoiceCreate(BaseModel):
    customer: int
    recipient: str = Field(min_length=12, max_length=13)
    issuer: str | None = Field(None, min_length=12, max_length=13)
    batch: str | None = Field(None, min_length=1, max_length=10)


class InvoiceUpdate(BaseModel):
    """Only the fields sent change (`model_fields_set`)."""

    recipient: str | None = Field(None, min_length=12, max_length=13)
    batch: str | None = Field(None, min_length=1, max_length=10)
    usage: str | None = Field(None, min_length=3, max_length=3)
    payment_terms: PaymentTerms | None = None
    payment_method: int | None = None
    issued: LocalDateTime | None = None
    facility: int | None = None
    reference: str | None = Field(None, max_length=25)
    comment: str | None = Field(None, max_length=1000)

    @field_validator('payment_method')
    @classmethod
    def _sat_payment_form(cls, value: int | None) -> int | None:
        if value is not None and value not in _PAYMENT_FORMS:
            raise ValueError('Not a SAT payment form')
        return value


class InvoiceSalesOrderAdd(BaseModel):
    sales_order: int


class InvoiceLineAdd(BaseModel):
    product: int


class InvoiceLineUpdate(BaseModel):
    """Only the fields sent change (`model_fields_set`)."""

    quantity: Decimal | None = Field(None, gt=0, max_digits=18, decimal_places=4)
    price: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=7)
    discount: Decimal | None = Field(None, ge=0, le=1, decimal_places=8)
    tax_rate: Decimal | None = None
    product_code: str | None = Field(None, max_length=35)
    product_name: str | None = Field(None, max_length=1000)
    comment: str | None = Field(None, max_length=1000)

    @field_validator('tax_rate')
    @classmethod
    def _known_rate(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value not in TAX_RATES:
            raise ValueError('The tax rate must be 0.16, 0 or -1 (exempt)')
        return value


class InvoiceRelationAdd(BaseModel):
    relation: int
    type: Literal['04', '07']
