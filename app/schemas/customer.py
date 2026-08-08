from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.enums import EntityStatus
from app.schemas.core import AddressResponse, ContactResponse, EmployeeResponse
from app.schemas.product import PriceListResponse
from app.schemas.sat_catalog import SatCatalogResponse

# ── Taxpayer Recipient ────────────────────────────────────────────────────────


class TaxpayerRecipientCreate(BaseModel):
    taxpayer_recipient_id: str = Field(min_length=12, max_length=13)
    name: str | None = None
    email: str
    postal_code: str | None = None
    regime: str | None = None


class TaxpayerRecipientUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    postal_code: str | None = None
    regime: str | None = None


class TaxpayerRecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    taxpayer_recipient_id: str
    name: str | None
    email: str
    postal_code: SatCatalogResponse | None = Field(
        validation_alias=AliasChoices('postal_code_detail', 'postal_code')
    )
    regime: SatCatalogResponse | None = Field(
        validation_alias=AliasChoices('regime_detail', 'regime')
    )


# ── Customer ──────────────────────────────────────────────────────────────────


class CustomerCreate(BaseModel):
    code: str
    name: str
    zone: str | None = None
    credit_limit: Decimal = Decimal('0')
    credit_days: int = 0
    price_list: int
    shipping: bool = False
    shipping_required_document: bool = False
    salesperson: int | None = None
    status: EntityStatus = EntityStatus.ACTIVE
    comment: str | None = None
    #: Existing address and contact ids to link (#132, #133), and the RFCs this customer invoices
    #: under (#150). Replace-all: omitted leaves the links alone, `[]` unlinks everything. Create
    #: the rows themselves via /addresses, /contacts, /taxpayer-recipients.
    addresses: list[int] | None = None
    contacts: list[int] | None = None
    taxpayers: list[str] | None = None

    @field_validator('code')
    @classmethod
    def validate_code(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('Code must not be blank')
        return v


class CustomerUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    zone: str | None = None
    credit_limit: Decimal | None = None
    credit_days: int | None = None
    price_list: int | None = None
    shipping: bool | None = None
    shipping_required_document: bool | None = None
    salesperson: int | None = None
    status: EntityStatus | None = None
    comment: str | None = None
    addresses: list[int] | None = None
    contacts: list[int] | None = None
    taxpayers: list[str] | None = None


class CustomerListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: int
    code: str
    name: str
    zone: str | None
    credit_limit: Decimal
    credit_days: int
    price_list: PriceListResponse = Field(
        validation_alias=AliasChoices('price_list_detail', 'price_list')
    )
    salesperson: EmployeeResponse | None = Field(
        validation_alias=AliasChoices('salesperson_detail', 'salesperson')
    )
    status: EntityStatus


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: int
    code: str
    name: str
    zone: str | None
    credit_limit: Decimal
    credit_days: int
    price_list: PriceListResponse = Field(
        validation_alias=AliasChoices('price_list_detail', 'price_list')
    )
    shipping: bool
    shipping_required_document: bool
    salesperson: EmployeeResponse | None = Field(
        validation_alias=AliasChoices('salesperson_detail', 'salesperson')
    )
    status: EntityStatus
    comment: str | None
    # Detail only — a page of customers must not cost an extra query per row for each of these.
    addresses: list[AddressResponse] = []
    contacts: list[ContactResponse] = []
    # A list, not a scalar: `customer_taxpayer` is many-to-many and the legacy data uses it that
    # way — a customer may invoice under more than one RFC (#150).
    taxpayers: list[TaxpayerRecipientResponse] = []
