import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.core import LabelResponse
from app.schemas.sat_catalog import SatCatalogResponse, SatUnitOfMeasurementResponse
from app.schemas.supplier import SupplierResponse

_WHITESPACE_RE = re.compile(r"\s")


def _validate_code(v: str) -> str:
    if _WHITESPACE_RE.search(v):
        raise ValueError("Code must not contain whitespace")
    return v


def _validate_bar_code(v: str | None) -> str | None:
    if v and not re.fullmatch(r"\d{13}", v):
        raise ValueError("Barcode must be empty or exactly 13 digits (EAN-13)")
    return v


# ── Price List ────────────────────────────────────────────────────────────────


class PriceListCreate(BaseModel):
    name: str
    high_profit_margin: Decimal = Decimal("0")
    low_profit_margin: Decimal = Decimal("0")


class PriceListUpdate(BaseModel):
    name: str | None = None
    high_profit_margin: Decimal | None = None
    low_profit_margin: Decimal | None = None


class PriceListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price_list_id: int
    name: str
    high_profit_margin: Decimal
    low_profit_margin: Decimal


# ── Product ───────────────────────────────────────────────────────────────────


class ProductCreate(BaseModel):
    code: str = Field(min_length=1, max_length=25)
    name: str = Field(min_length=4, max_length=250)
    photo: str | None = None
    sku: str | None = None
    brand: str | None = None
    model: str | None = None
    bar_code: str | None = None
    location: str | None = None
    unit_of_measurement: str
    key: str | None = None
    tax_rate: Decimal | None = None
    tax_included: bool | None = None
    price_type: int | None = None
    currency: int = 0
    supplier: int | None = None
    stockable: bool = False
    perishable: bool = False
    seriable: bool = False
    purchasable: bool = False
    salable: bool = False
    invoiceable: bool = False
    stock_required: bool | None = None
    comment: str | None = None
    labels: list[int] | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return _validate_code(v)

    @field_validator("bar_code")
    @classmethod
    def validate_bar_code(cls, v: str | None) -> str | None:
        return _validate_bar_code(v)


class ProductUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=25)
    name: str | None = Field(default=None, min_length=4, max_length=250)
    photo: str | None = None
    sku: str | None = None
    brand: str | None = None
    model: str | None = None
    bar_code: str | None = None
    location: str | None = None
    unit_of_measurement: str | None = None
    key: str | None = None
    tax_rate: Decimal | None = None
    tax_included: bool | None = None
    price_type: int | None = None
    currency: int | None = None
    min_order_qty: int | None = None
    supplier: int | None = None
    stockable: bool | None = None
    perishable: bool | None = None
    seriable: bool | None = None
    purchasable: bool | None = None
    salable: bool | None = None
    invoiceable: bool | None = None
    stock_required: bool | None = None
    deactivated: bool | None = None
    comment: str | None = None
    labels: list[int] | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_code(v)
        return v

    @field_validator("bar_code")
    @classmethod
    def validate_bar_code(cls, v: str | None) -> str | None:
        return _validate_bar_code(v)


class ProductListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    code: str
    name: str
    sku: str | None
    photo: str | None
    brand: str | None
    model: str | None
    unit_of_measurement: SatUnitOfMeasurementResponse
    tax_rate: Decimal
    deactivated: bool


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    product_id: int
    code: str
    name: str
    photo: str | None
    sku: str | None
    brand: str | None
    model: str | None
    bar_code: str | None
    location: str | None
    unit_of_measurement: SatUnitOfMeasurementResponse
    key: SatCatalogResponse | None
    tax_rate: Decimal
    tax_included: bool
    price_type: int
    currency: int
    min_order_qty: int
    supplier: SupplierResponse | None
    stockable: bool
    perishable: bool
    seriable: bool
    purchasable: bool
    salable: bool
    invoiceable: bool
    stock_required: bool = Field(alias="stock_verification")
    deactivated: bool
    comment: str | None
    labels: list[LabelResponse] = []


class ProductLabelFacet(BaseModel):
    label_id: int
    count: int


# ── Merge ─────────────────────────────────────────────────────────────────────


class ProductMergeRequest(BaseModel):
    product_id: int
    duplicate_id: int
