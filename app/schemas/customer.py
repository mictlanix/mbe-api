from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    postal_code: str | None
    regime: str | None


# ── Customer ──────────────────────────────────────────────────────────────────


class CustomerCreate(BaseModel):
    code: str
    name: str
    zone: str | None = None
    credit_limit: Decimal = Decimal("0")
    credit_days: int = 0
    price_list: int
    shipping: bool = False
    shipping_required_document: bool = False
    salesperson: int | None = None
    comment: str | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Code must not be blank")
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
    disabled: bool | None = None
    comment: str | None = None


class CustomerListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: int
    code: str
    name: str
    zone: str | None
    credit_limit: Decimal
    credit_days: int
    price_list: int
    salesperson: int | None
    disabled: bool | None


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: int
    code: str
    name: str
    zone: str | None
    credit_limit: Decimal
    credit_days: int
    price_list: int
    shipping: bool
    shipping_required_document: bool
    salesperson: int | None
    disabled: bool | None
    comment: str | None
