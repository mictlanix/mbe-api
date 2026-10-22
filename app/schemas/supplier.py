from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.core import AddressResponse, BankAccountResponse, ContactResponse


class SupplierCreate(BaseModel):
    code: str
    name: str
    zone: str | None = None
    credit_limit: Decimal = Decimal('0')
    credit_days: int = 0
    comment: str | None = None
    #: Existing address, contact and bank account ids to link (#13). Replace-all: omitted leaves
    #: the links alone, `[]` unlinks everything. Create the rows themselves via /addresses,
    #: /contacts, /bank-accounts.
    addresses: list[int] | None = None
    contacts: list[int] | None = None
    bank_accounts: list[int] | None = None


class SupplierUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    zone: str | None = None
    credit_limit: Decimal | None = None
    credit_days: int | None = None
    comment: str | None = None
    addresses: list[int] | None = None
    contacts: list[int] | None = None
    bank_accounts: list[int] | None = None


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_id: int
    code: str
    name: str
    zone: str | None
    credit_limit: Decimal
    credit_days: int
    comment: str | None


class SupplierAgreementCreate(BaseModel):
    start: date
    end: date
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def validate_period(self) -> 'SupplierAgreementCreate':
        if self.end < self.start:
            raise ValueError('End must not be before start')
        return self


class SupplierAgreementUpdate(BaseModel):
    start: date | None = None
    end: date | None = None
    comment: str | None = Field(default=None, max_length=500)


class SupplierAgreementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_agreement_id: int
    supplier: int
    start: date
    end: date
    comment: str | None


class SupplierDetailResponse(SupplierResponse):
    """The supplier with its sub-panels. Only the detail carries them: a page of suppliers, and the
    `ProductResponse.supplier` embed, must not cost four queries per row."""

    addresses: list[AddressResponse] = []
    contacts: list[ContactResponse] = []
    bank_accounts: list[BankAccountResponse] = []
    agreements: list[SupplierAgreementResponse] = []
