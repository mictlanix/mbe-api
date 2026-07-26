from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SessionState(StrEnum):
    """Three states, because a client routes differently for each (FR-053).

    `STALE` is an open session started before today: selling continues to be refused until it is
    closed, which is a different remedy from having no session at all.
    """

    NONE = 'none'
    OPEN = 'open'
    STALE = 'stale'


class DenominationCount(BaseModel):
    denomination: Decimal = Field(gt=0)
    quantity: int = Field(ge=0)


class CashSessionOpen(BaseModel):
    cash_drawer: int | None = None
    opening_amount: Decimal = Field(default=Decimal(0), ge=0)


class CashSessionClose(BaseModel):
    counts: list[DenominationCount] = []


class MethodTotal(BaseModel):
    method: int
    total: Decimal


class CashSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cash_session_id: int
    cash_drawer: int
    cashier: int
    start: datetime
    end: datetime | None
    cash_supervisor: int | None
    opening_amount: Decimal
    payments_by_method: list[MethodTotal] = []


class CurrentSessionResponse(BaseModel):
    state: SessionState
    session: CashSessionResponse | None = None
