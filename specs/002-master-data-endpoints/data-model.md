# Data Model: Master Data REST Endpoints

All ORM models already exist. This document is the authoritative field reference for schema
authors and implementers. No database migrations are required.

---

## Conventions

- All models extend `app.db.base.Base` (SQLAlchemy declarative base).
- Primary key field: `<tablename>_id` (integer, auto-increment) unless noted.
- Optional fields: `Mapped[T | None]`.
- FK fields: hold the integer/string PK of the referenced table (no ORM relationship objects).
- Currency: `Decimal` via `Numeric(18, 4)`.
- Boolean flags exposed as `bool` in API (never `int`).

---

## 1. Product

**ORM class**: `Product` in `app/models/product.py`  
**Table**: `product`

| Python field | Column | Type | Constraints |
|--------------|--------|------|-------------|
| `product_id` | `product_id` | `int` | PK, auto |
| `code` | `code` | `str` | 1–25 chars, no whitespace, unique |
| `name` | `name` | `str` | 4–250 chars |
| `photo` | `photo` | `str \| None` | default from config |
| `sku` | `sku` | `str \| None` | |
| `brand` | `brand` | `str \| None` | |
| `model` | `model` | `str \| None` | |
| `bar_code` | `bar_code` | `str \| None` | exactly 13 digits or empty |
| `location` | `location` | `str \| None` | shelf/bin code |
| `unit_of_measurement` | `unit_of_measurement` | `str` | FK → `sat_unit_of_measurement` |
| `stockable` | `stockable` | `bool` | |
| `perishable` | `perishable` | `bool` | lot/expiry tracking |
| `seriable` | `seriable` | `bool` | serial number tracking |
| `purchasable` | `purchasable` | `bool` | |
| `salable` | `salable` | `bool` | |
| `invoiceable` | `invoiceable` | `bool` | |
| `tax_rate` | `tax_rate` | `Decimal` | 0–1; default from config |
| `tax_included` | `tax_included` | `bool` | default from config |
| `price_type` | `price_type` | `int` | 0=Fixed, 1=Variable; default from config |
| `currency` | `currency` | `CurrencyCode` | IntEnum (0=MXN, 1=USD, 2=EUR) |
| `min_order_qty` | `min_order_qty` | `int` | default = 1 on create |
| `comment` | `comment` | `str \| None` | |
| `supplier` | `supplier` | `int \| None` | FK → `supplier` |
| `key` | `key` | `str \| None` | FK → `sat_product_service` |
| `deactivated` | `deactivated` | `bool` | |
| `stock_verification` | `stock_verification` | `bool` | **API alias**: `stock_required`; default = true on create |

**API alias note**: `stock_verification` is exposed as `stock_required` in all request/response
schemas via a Pydantic alias. The column name in MariaDB is unchanged.

---

## 2. ProductPrice

**ORM class**: `ProductPrice` in `app/models/product.py`  
**Table**: `product_price`

| Python field | Column | Type | Constraints |
|--------------|--------|------|-------------|
| `product_price_id` | `product_price_id` | `int` | PK, auto |
| `product` | `product` | `int` | FK → `product` |
| `price_list` | `list` | `int` | FK → `price_list` (DB column is `list`) |
| `price` | `price` | `Decimal` | default = 0 on create |
| `low_profit` | `low_profit` | `Decimal` | |
| `high_profit` | `high_profit` | `Decimal` | |

**Auto-create rule**: On `POST /products`, the service creates one `ProductPrice` row per
existing `PriceList` with `price = 0`, `low_profit = 0`, `high_profit = 0`.

---

## 3. PriceList

**ORM class**: `PriceList` in `app/models/product.py`  
**Table**: `price_list`

| Python field | Column | Type | Constraints |
|--------------|--------|------|-------------|
| `price_list_id` | `price_list_id` | `int` | PK, auto |
| `name` | `name` | `str` | required, unique |
| `high_profit_margin` | `high_profit_margin` | `Decimal` | 0–1 |
| `low_profit_margin` | `low_profit_margin` | `Decimal` | 0–1 |

**Delete constraint**: Cannot delete if any `Customer.price_list` references this record.

---

## 4. Label

**ORM class**: `Label` in `app/models/core.py`  
**Table**: `label`

| Python field | Column | Type |
|--------------|--------|------|
| `label_id` | `label_id` | `int` PK |
| `name` | `name` | `str` unique |
| `comment` | `comment` | `str \| None` |

---

## 5. Customer

**ORM class**: `Customer` in `app/models/customer.py`  
**Table**: `customer`

| Python field | Column | Type | Constraints |
|--------------|--------|------|-------------|
| `customer_id` | `customer_id` | `int` | PK |
| `code` | `code` | `str` | unique |
| `name` | `name` | `str` | required |
| `zone` | `zone` | `str \| None` | |
| `credit_limit` | `credit_limit` | `Decimal` | |
| `credit_days` | `credit_days` | `int` | |
| `comment` | `comment` | `str \| None` | |
| `price_list` | `price_list` | `int` | FK → `price_list` |
| `shipping` | `shipping` | `bool` | |
| `shipping_required_document` | `shipping_required_document` | `bool` | |
| `salesperson` | `salesperson` | `int \| None` | FK → `employee` |
| `disabled` | `disabled` | `bool \| None` | |
| `creator` | `creator` | `int \| None` | FK → `employee` |

**Delete constraint**: `default_customer_id` (from Settings) cannot be deleted.

---

## 6. TaxpayerRecipient

**ORM class**: `TaxpayerRecipient` in `app/models/customer.py`  
**Table**: `taxpayer_recipient`

| Python field | Column | Type | Constraints |
|--------------|--------|------|-------------|
| `taxpayer_recipient_id` | `taxpayer_recipient_id` | `str` | PK, 12–13 chars (RFC) |
| `name` | `name` | `str \| None` | |
| `email` | `email` | `str` | |
| `postal_code` | `postal_code` | `str \| None` | FK → `sat_postal_code` |
| `regime` | `regime` | `str \| None` | FK → `sat_tax_regime` |

---

## 7. Supplier

**ORM class**: `Supplier` in `app/models/supplier.py`  
**Table**: `supplier`

| Python field | Column | Type |
|--------------|--------|------|
| `supplier_id` | `supplier_id` | `int` PK |
| `code` | `code` | `str` unique |
| `name` | `name` | `str` required |
| `zone` | `zone` | `str \| None` |
| `credit_limit` | `credit_limit` | `Decimal` |
| `credit_days` | `credit_days` | `int` |
| `comment` | `comment` | `str \| None` |

---

## 8. Employee

**ORM class**: `Employee` in `app/models/core.py`  
**Table**: `employee`

| Python field | Column | Type |
|--------------|--------|------|
| `employee_id` | `employee_id` | `int` PK |
| `first_name` | `first_name` | `str` |
| `last_name` | `last_name` | `str` |
| `nickname` | `nickname` | `str` |
| `gender` | `gender` | `int` (SmallInteger enum) |
| `birthday` | `birthday` | `date` |
| `taxpayer_id` | `taxpayer_id` | `str \| None` |
| `sales_person` | `sales_person` | `bool` |
| `active` | `active` | `bool` |
| `personal_id` | `personal_id` | `str \| None` |
| `start_job_date` | `start_job_date` | `date` |
| `comment` | `comment` | `str \| None` |
| `enroll_number` | `enroll_number` | `int \| None` |
| `disabled` | `disabled` | `bool \| None` |

---

## 9. Store

**ORM class**: `Store` in `app/models/core.py`  
**Table**: `store`

| Python field | Column | Type |
|--------------|--------|------|
| `store_id` | `store_id` | `int` PK |
| `code` | `code` | `str` |
| `name` | `name` | `str` |
| `location` | `location` | `str` FK → `sat_postal_code` |
| `address` | `address` | `int` FK → `address` |
| `taxpayer` | `taxpayer` | `str` FK → `taxpayer_issuer` |
| `logo` | `logo` | `str` |
| `receipt_message` | `receipt_message` | `str \| None` |
| `default_batch` | `default_batch` | `str \| None` |
| `disabled` | `disabled` | `bool \| None` |

---

## 10. Warehouse

**ORM class**: `Warehouse` in `app/models/core.py`  
**Table**: `warehouse`

| Python field | Column | Type |
|--------------|--------|------|
| `warehouse_id` | `warehouse_id` | `int` PK |
| `store` | `store` | `int` FK → `store` |
| `code` | `code` | `str` unique |
| `name` | `name` | `str` |
| `comment` | `comment` | `str \| None` |
| `disabled` | `disabled` | `int \| None` (SmallInteger) |

---

## 11. PointSale

**ORM class**: `PointSale` in `app/models/core.py`  
**Table**: `point_sale`

| Python field | Column | Type |
|--------------|--------|------|
| `point_sale_id` | `point_sale_id` | `int` PK |
| `store` | `store` | `int` FK → `store` |
| `code` | `code` | `str` unique |
| `name` | `name` | `str` |
| `warehouse` | `warehouse` | `int` FK → `warehouse` |
| `comment` | `comment` | `str \| None` |
| `disabled` | `disabled` | `bool \| None` |

---

## 12. CashDrawer

**ORM class**: `CashDrawer` in `app/models/core.py`  
**Table**: `cash_drawer`

| Python field | Column | Type |
|--------------|--------|------|
| `cash_drawer_id` | `cash_drawer_id` | `int` PK |
| `store` | `store` | `int` FK → `store` |
| `code` | `code` | `str` unique |
| `name` | `name` | `str` |
| `comment` | `comment` | `str \| None` |
| `disabled` | `disabled` | `bool \| None` |

---

## 13. ExchangeRate

**ORM class**: `ExchangeRate` in `app/models/core.py`  
**Table**: `exchange_rate`

| Python field | Column | Type | Constraints |
|--------------|--------|------|-------------|
| `exchange_rate_id` | `exchange_rate_id` | `int` | PK |
| `date` | `date` | `date` | unique per (date, base, target) |
| `rate` | `rate` | `Decimal` | |
| `base` | `base` | `CurrencyCode` | IntEnum |
| `target` | `target` | `CurrencyCode` | IntEnum |

---

## 14. Expense

**ORM class**: `Expense` in `app/models/core.py`  
**Table**: `expenses`

| Python field | Column | Type |
|--------------|--------|------|
| `expense_id` | `expense_id` | `int` PK |
| `expense` | `expense` | `str` |
| `comment` | `comment` | `str \| None` |

**Note**: the category name field is called `expense` (same as the table). Use `name` in the
API schema, mapping to `expense` via alias.

---

## 15. PaymentMethodOption

**ORM class**: `PaymentMethodOption` in `app/models/core.py`  
**Table**: `payment_method_option`

| Python field | Column | Type |
|--------------|--------|------|
| `payment_method_option_id` | `payment_method_option_id` | `int` PK |
| `warehouse` | `warehouse` | `int \| None` FK → `warehouse` |
| `store` | `store` | `int` FK → `store` |
| `name` | `name` | `str` |
| `number_of_payments` | `number_of_payments` | `int` |
| `display_on_ticket` | `display_on_ticket` | `bool` |
| `payment_method` | `payment_method` | `int` |
| `commission` | `commission` | `Decimal` |
| `enabled` | `enabled` | `bool` |

---

## 16. Vehicle

**ORM class**: `Vehicle` in `app/models/core.py`  
**Table**: `vehicle`

| Python field | Column | Type |
|--------------|--------|------|
| `vehicle_id` | `vehicle_id` | `int` PK |
| `license_plate` | `license_plate` | `str` unique |
| `name` | `name` | `str` |
| `nickname` | `nickname` | `str` |
| `tons_capacity` | `tons_capacity` | `int` |
| `active` | `active` | `bool` |

---

## 17. VehicleOperator

**ORM class**: `VehicleOperator` in `app/models/core.py`  
**Table**: `vehicle_operator`

| Python field | Column | Type |
|--------------|--------|------|
| `vehicle_operator_id` | `vehicle_operator_id` | `int` PK |
| `driver` | `driver` | `int` FK → `employee` |
| `license_type` | `license_type` | `str` |
| `driver_license_number` | `driver_license_number` | `str` |
| `issue_date` | `issue_date` | `date` |
| `expiration_date` | `expiration_date` | `date` |
| `issuing_location` | `issuing_location` | `str` |
| `creation_time` | `creation_time` | `datetime` |
| `modification_time` | `modification_time` | `datetime` |
| `creator` | `creator` | `int` FK → `employee` |
| `updater` | `updater` | `int` FK → `employee` |
| `active` | `active` | `bool` |

**Computed API field**: `days_until_expiry: int` — days until `expiration_date` from today
(negative = expired). Computed in Pydantic response schema `@model_validator(mode='after')`.

---

## 18. ProductionSite

**ORM class**: `ProductionSite` in `app/models/core.py`  
**Table**: `production_site`

| Python field | Column | Type |
|--------------|--------|------|
| `production_site_id` | `production_site_id` | `int` PK |
| `store` | `store` | `int` FK → `store` |
| `code` | `code` | `str` unique |
| `name` | `name` | `str` |
| `comment` | `comment` | `str \| None` |
| `disabled` | `disabled` | `int \| None` |

---

## Shared Schema: ListResponse

A single generic wrapper in `app/schemas/core.py` (or `app/schemas/__init__.py`):

```python
class ListResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
```

Used by all 17 list endpoints.

---

## Config Additions (`app/core/config.py`)

```python
from decimal import Decimal

default_vat: Decimal = Decimal("0.160000")
is_tax_included: bool = False
default_price_type: int = 0        # 0 = Fixed
default_photo_file: str = "no-image.png"
default_customer_id: int = 1
```
