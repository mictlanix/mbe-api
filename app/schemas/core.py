import datetime as dt
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Label ─────────────────────────────────────────────────────────────────────


class LabelCreate(BaseModel):
    name: str
    comment: str | None = None


class LabelUpdate(BaseModel):
    name: str | None = None
    comment: str | None = None


class LabelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label_id: int
    name: str
    comment: str | None


# ── Employee ──────────────────────────────────────────────────────────────────


class EmployeeCreate(BaseModel):
    first_name: str
    last_name: str
    nickname: str
    gender: int
    birthday: dt.date
    taxpayer_id: str | None = None
    sales_person: bool = False
    active: bool = True
    personal_id: str | None = None
    start_job_date: dt.date
    enroll_number: int | None = None
    comment: str | None = None


class EmployeeUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    nickname: str | None = None
    gender: int | None = None
    birthday: dt.date | None = None
    taxpayer_id: str | None = None
    sales_person: bool | None = None
    active: bool | None = None
    personal_id: str | None = None
    start_job_date: dt.date | None = None
    enroll_number: int | None = None
    comment: str | None = None
    disabled: bool | None = None


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: int
    first_name: str
    last_name: str
    nickname: str
    gender: int
    birthday: dt.date
    taxpayer_id: str | None
    sales_person: bool
    active: bool
    personal_id: str | None
    start_job_date: dt.date
    enroll_number: int | None
    comment: str | None
    disabled: bool | None


# ── Store ─────────────────────────────────────────────────────────────────────


class StoreCreate(BaseModel):
    code: str
    name: str
    location: str
    address: int
    taxpayer: str
    logo: str
    receipt_message: str | None = None
    default_batch: str | None = None
    disabled: bool | None = None


class StoreUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    location: str | None = None
    address: int | None = None
    taxpayer: str | None = None
    logo: str | None = None
    receipt_message: str | None = None
    default_batch: str | None = None
    disabled: bool | None = None


class StoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    store_id: int
    code: str
    name: str
    location: str
    address: int
    taxpayer: str
    logo: str
    receipt_message: str | None
    default_batch: str | None
    disabled: bool | None


# ── Warehouse ─────────────────────────────────────────────────────────────────


class WarehouseCreate(BaseModel):
    store: int
    code: str
    name: str
    comment: str | None = None
    disabled: bool | None = None


class WarehouseUpdate(BaseModel):
    store: int | None = None
    code: str | None = None
    name: str | None = None
    comment: str | None = None
    disabled: bool | None = None


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    warehouse_id: int
    store: int
    code: str
    name: str
    comment: str | None
    disabled: bool | None


# ── Point of Sale ─────────────────────────────────────────────────────────────


class PointSaleCreate(BaseModel):
    store: int
    code: str
    name: str
    warehouse: int
    comment: str | None = None
    disabled: bool | None = None


class PointSaleUpdate(BaseModel):
    store: int | None = None
    code: str | None = None
    name: str | None = None
    warehouse: int | None = None
    comment: str | None = None
    disabled: bool | None = None


class PointSaleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    point_sale_id: int
    store: int
    code: str
    name: str
    warehouse: int
    comment: str | None
    disabled: bool | None


# ── Cash Drawer ───────────────────────────────────────────────────────────────


class CashDrawerCreate(BaseModel):
    store: int
    code: str
    name: str
    comment: str | None = None
    disabled: bool | None = None


class CashDrawerUpdate(BaseModel):
    store: int | None = None
    code: str | None = None
    name: str | None = None
    comment: str | None = None
    disabled: bool | None = None


class CashDrawerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cash_drawer_id: int
    store: int
    code: str
    name: str
    comment: str | None
    disabled: bool | None


# ── Exchange Rate ─────────────────────────────────────────────────────────────


class ExchangeRateCreate(BaseModel):
    date: dt.date
    rate: Decimal
    base: int
    target: int


class ExchangeRateUpdate(BaseModel):
    date: dt.date | None = None
    rate: Decimal | None = None
    base: int | None = None
    target: int | None = None


class ExchangeRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    exchange_rate_id: int
    date: dt.date
    rate: Decimal
    base: int
    target: int


# ── Expense ───────────────────────────────────────────────────────────────────


class ExpenseCreate(BaseModel):
    name: str = Field(alias="name")
    comment: str | None = None


class ExpenseUpdate(BaseModel):
    name: str | None = None
    comment: str | None = None


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    expense_id: int
    name: str = Field(alias="expense")
    comment: str | None


# ── Payment Method Option ─────────────────────────────────────────────────────


class PaymentMethodOptionCreate(BaseModel):
    store: int
    warehouse: int | None = None
    name: str
    number_of_payments: int = 1
    display_on_ticket: bool = True
    payment_method: int
    commission: Decimal = Decimal("0")
    enabled: bool = True


class PaymentMethodOptionUpdate(BaseModel):
    store: int | None = None
    warehouse: int | None = None
    name: str | None = None
    number_of_payments: int | None = None
    display_on_ticket: bool | None = None
    payment_method: int | None = None
    commission: Decimal | None = None
    enabled: bool | None = None


class PaymentMethodOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_method_option_id: int
    store: int
    warehouse: int | None
    name: str
    number_of_payments: int
    display_on_ticket: bool
    payment_method: int
    commission: Decimal
    enabled: bool


# ── Vehicle ───────────────────────────────────────────────────────────────────


class VehicleCreate(BaseModel):
    license_plate: str
    name: str
    nickname: str
    tons_capacity: int
    active: bool = True


class VehicleUpdate(BaseModel):
    license_plate: str | None = None
    name: str | None = None
    nickname: str | None = None
    tons_capacity: int | None = None
    active: bool | None = None


class VehicleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: int
    license_plate: str
    name: str
    nickname: str
    tons_capacity: int
    active: bool


# ── Vehicle Operator ──────────────────────────────────────────────────────────


class VehicleOperatorCreate(BaseModel):
    driver: int
    license_type: str
    driver_license_number: str
    issue_date: dt.date
    expiration_date: dt.date
    issuing_location: str
    active: bool = True


class VehicleOperatorUpdate(BaseModel):
    driver: int | None = None
    license_type: str | None = None
    driver_license_number: str | None = None
    issue_date: dt.date | None = None
    expiration_date: dt.date | None = None
    issuing_location: str | None = None
    active: bool | None = None


class VehicleOperatorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_operator_id: int
    driver: int
    license_type: str
    driver_license_number: str
    issue_date: dt.date
    expiration_date: dt.date
    issuing_location: str
    creation_time: datetime
    modification_time: datetime
    creator: int
    updater: int
    active: bool
    days_until_expiry: int = 0

    @model_validator(mode="after")
    def compute_days_until_expiry(self) -> "VehicleOperatorResponse":
        self.days_until_expiry = (self.expiration_date - dt.date.today()).days
        return self


# ── Production Site ───────────────────────────────────────────────────────────


class ProductionSiteCreate(BaseModel):
    store: int
    code: str
    name: str
    comment: str | None = None
    disabled: bool | None = None


class ProductionSiteUpdate(BaseModel):
    store: int | None = None
    code: str | None = None
    name: str | None = None
    comment: str | None = None
    disabled: bool | None = None


class ProductionSiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    production_site_id: int
    store: int
    code: str
    name: str
    comment: str | None
    disabled: bool | None
