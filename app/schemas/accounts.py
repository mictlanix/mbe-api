"""Accounts receivable and accounts payable views and statements (spec 033, contracts/accounts.md).

Both sides share these shapes. Fields that exist on one side only are optional: the sales agent
on customers, the facility and salesperson on orders, the cutoff on suppliers.
"""

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import CurrencyCode, PaymentMethod
from app.schemas import LocalDateTime
from app.schemas.sales_quote import QuoteEmailRequest


class Buckets(BaseModel):
    """Aging by days past due. Each document lands in exactly one, so they add up to owed."""

    model_config = ConfigDict(populate_by_name=True)

    current: Decimal
    days_1_30: Decimal = Field(alias='1-30')
    days_31_60: Decimal = Field(alias='31-60')
    days_61_90: Decimal = Field(alias='61-90')
    days_90_plus: Decimal = Field(alias='90+')


class PartySummary(BaseModel):
    party: int
    name: str
    #: Customers only: `customer.salesperson`.
    sales_agent: int | None = None
    sales_agent_name: str | None = None
    owed: Decimal
    overdue: Decimal
    buckets: Buckets
    oldest: LocalDateTime | None
    credit: Decimal
    net: Decimal
    credit_limit: Decimal | None
    credit_days: int | None
    #: Owed across every facility; utilization and available credit read it (FR-015).
    owed_company: Decimal
    utilization: Decimal | None
    available_credit: Decimal | None


class AccountScope(BaseModel):
    facility: int | None
    all_facilities: bool


class AccountTotals(BaseModel):
    owed: Decimal
    overdue: Decimal
    buckets: Buckets


class AccountsSummaryResponse(BaseModel):
    items: list[PartySummary]
    total: int
    scope: AccountScope
    totals: AccountTotals
    #: Suppliers only: balances count from this date; null until go-live sets it.
    cutoff: LocalDateTime | None = None


class LinkedDocument(BaseModel):
    id: int
    label: str


class OpenDocument(BaseModel):
    id: int
    #: Null for a PO, which has no serial.
    serial: int | None
    date: LocalDateTime
    due_date: LocalDateTime | None
    days_past_due: int
    bucket: str
    #: Orders only.
    facility: int | None = None
    facility_code: str | None = None
    salesperson: int | None = None
    linked: list[LinkedDocument]
    currency: CurrencyCode
    exchange_rate: Decimal
    total: Decimal
    #: Refunds and credit notes on an order; returns on a PO, always 0.
    deductions: Decimal
    applied: Decimal
    balance: Decimal


class AccountPayment(BaseModel):
    id: int
    serial: int | None
    date: LocalDateTime
    method: PaymentMethod | None
    currency: CurrencyCode
    amount: Decimal
    #: What can still be applied: unapplied on a customer payment, credit on a supplier payment.
    unapplied: Decimal


class PartyDetail(BaseModel):
    summary: PartySummary
    documents: list[OpenDocument]
    payments: list[AccountPayment]


class StatementEmailRequest(BaseModel):
    """Contacts of the party and typed addresses; at most 10 recipients in all (FR-024)."""

    contacts: list[int] = Field(default=[], max_length=10)
    to: list[str] = Field(default=[], max_length=10)

    @field_validator('to')
    @classmethod
    def _valid_and_distinct(cls, value: list[str]) -> list[str]:
        """Quotes' rule (spec 024): trimmed, checked, deduplicated case-insensitively."""
        return QuoteEmailRequest._valid_and_distinct(value)

    @model_validator(mode='after')
    def _someone(self) -> Self:
        if not self.contacts and not self.to:
            raise ValueError('Choose at least one contact or type an address')
        return self


class StatementEmailResponse(BaseModel):
    sent_at: LocalDateTime
    to: list[str]
