from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SupplierCreate(BaseModel):
    code: str
    name: str
    zone: str | None = None
    credit_limit: Decimal = Decimal("0")
    credit_days: int = 0
    comment: str | None = None


class SupplierUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    zone: str | None = None
    credit_limit: Decimal | None = None
    credit_days: int | None = None
    comment: str | None = None


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_id: int
    code: str
    name: str
    zone: str | None
    credit_limit: Decimal
    credit_days: int
    comment: str | None
