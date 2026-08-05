from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.core import CashDrawerSummary, EmployeeResponse


class SessionState(StrEnum):
    """Three states, because a client routes differently for each (FR-053).

    `STALE` is an open session started before today: selling continues to be refused until it is
    closed, which is a different remedy from having no session at all.
    """

    NONE = 'none'
    OPEN = 'open'
    STALE = 'stale'


class CashSessionStatus(StrEnum):
    """A stored session's own state, used as a list facet (#142).

    Deliberately not `SessionState`: `NONE` describes a cashier with no session, which no row can
    be, and a stored session can be closed, which `SessionState` has no member for. The three
    members here derive from `end` and `start` exactly as `session_state` does.
    """

    OPEN = 'open'
    STALE = 'stale'
    CLOSED = 'closed'


class CashSessionSort(StrEnum):
    """Ordering for the session list; a `-` prefix reads descending.

    `ID_DESC` is the default because it is the ordering the list has always had.
    """

    ID_DESC = '-id'
    START_ASC = 'start'
    START_DESC = '-start'


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
    cash_drawer: CashDrawerSummary = Field(
        validation_alias=AliasChoices('cash_drawer_detail', 'cash_drawer')
    )
    cashier: EmployeeResponse = Field(validation_alias=AliasChoices('cashier_detail', 'cashier'))
    start: datetime
    end: datetime | None
    cash_supervisor: EmployeeResponse | None = Field(
        validation_alias=AliasChoices('cash_supervisor_detail', 'cash_supervisor')
    )
    opening_amount: Decimal
    payments_by_method: list[MethodTotal] = []


class CurrentSessionResponse(BaseModel):
    state: SessionState
    session: CashSessionResponse | None = None
