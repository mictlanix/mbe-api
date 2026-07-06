# API Contracts: Master Data REST Endpoints

**Base URL prefix**: `/api/v1`  
**Auth**: All endpoints require `Authorization: Bearer <jwt>` header.  
**Pagination**: All list endpoints accept `skip: int = 0` and `limit: int = 1..100` query params
and return `{"items": [...], "total": N}`.  
**Error format**: FastAPI default `{"detail": "..."}`.

---

## Common Response Codes

| Code | Meaning |
|------|---------|
| 200 | OK (list / get / update) |
| 201 | Created (post) |
| 204 | No Content (delete / merge) |
| 400 | Bad Request (e.g., merge self-reference) |
| 401 | Unauthenticated |
| 403 | Forbidden (insufficient privilege) |
| 404 | Not Found |
| 409 | Conflict (uniqueness violation / protected delete) |
| 422 | Validation Error (field constraint violation) |

---

## 1. Products

**Prefix**: `/api/v1/products`

### `GET /api/v1/products`

Query params: `search` (code, name, model, sku, brand), `label` (int, repeatable — e.g.
`?label=2&label=5`; when repeated, a product must carry **all** given labels), `deactivated` (bool),
`stockable` (bool), `salable` (bool), `purchasable` (bool), `supplier` (int), `skip`, `limit`.

Response `200`: `{"items": [ProductListItem, ...], "total": N}`

```
ProductListItem:
  product_id: int
  code: str
  name: str
  brand: str | null
  model: str | null
  unit_of_measurement: str
  tax_rate: Decimal
  deactivated: bool
```

### `POST /api/v1/products`

Body: `ProductCreate`

```
ProductCreate:
  code: str          # 1–25 chars, no whitespace
  name: str          # 4–250 chars
  photo: str | null
  sku: str | null
  brand: str | null
  model: str | null
  bar_code: str | null  # "" or exactly 13 digits
  location: str | null
  unit_of_measurement: str
  key: str | null
  tax_rate: Decimal | null      # defaults to config
  tax_included: bool | null     # defaults to config
  price_type: int | null        # 0=Fixed, 1=Variable; defaults to config
  currency: int                 # CurrencyCode
  supplier: int | null
  stockable: bool
  perishable: bool
  seriable: bool
  purchasable: bool
  salable: bool
  invoiceable: bool
  stock_required: bool | null   # defaults to true
  comment: str | null
```

Response `201`: `ProductResponse` (full product record)

### `GET /api/v1/products/{product_id}`

Response `200`: `ProductResponse`

```
ProductResponse:
  product_id: int
  code: str
  name: str
  photo: str | null
  sku: str | null
  brand: str | null
  model: str | null
  bar_code: str | null
  location: str | null
  unit_of_measurement: str
  key: str | null
  tax_rate: Decimal
  tax_included: bool
  price_type: int
  currency: int
  min_order_qty: int
  supplier: int | null
  stockable: bool
  perishable: bool
  seriable: bool
  purchasable: bool
  salable: bool
  invoiceable: bool
  stock_required: bool     # alias for stock_verification
  deactivated: bool
  comment: str | null
  prices: [ProductPriceResponse, ...]
```

```
ProductPriceResponse:
  product_price_id: int
  price_list: int
  price: Decimal
  low_profit: Decimal
  high_profit: Decimal
```

### `PUT /api/v1/products/{product_id}`

Body: `ProductUpdate` (same fields as `ProductCreate`, all optional)  
Response `200`: `ProductResponse`

### `DELETE /api/v1/products/{product_id}`

Response `204`

### `POST /api/v1/products/merge`

Requires: `AccessRight.CREATE` on `SystemObject.PRODUCTS_MERGE (73)`.

Body:
```
ProductMergeRequest:
  product_id: int      # canonical (kept)
  duplicate_id: int    # duplicate (deleted)
```

Response `204`  
Error `400` if `product_id == duplicate_id`.  
Error `403` if caller lacks PRODUCTS_MERGE privilege.

---

## 2. Price Lists

**Prefix**: `/api/v1/price-lists`

### `GET /api/v1/price-lists`

Query: `search` (name), `skip`, `limit`  
Response `200`: `{"items": [PriceListResponse, ...], "total": N}`

### `POST /api/v1/price-lists`

Body:
```
PriceListCreate:
  name: str
  high_profit_margin: Decimal
  low_profit_margin: Decimal
```
Response `201`: `PriceListResponse`

### `GET /api/v1/price-lists/{price_list_id}`

Response `200`: `PriceListResponse`

```
PriceListResponse:
  price_list_id: int
  name: str
  high_profit_margin: Decimal
  low_profit_margin: Decimal
```

### `PUT /api/v1/price-lists/{price_list_id}`

Body: `PriceListUpdate` (all fields optional)  
Response `200`: `PriceListResponse`

### `DELETE /api/v1/price-lists/{price_list_id}`

Response `204`  
Error `409` if any Customer references this price list.

---

## 3. Customers

**Prefix**: `/api/v1/customers`

### `GET /api/v1/customers`

Query: `search` (code, name, zone), `disabled` (bool), `price_list` (int), `salesperson` (int), `skip`, `limit`  
Response `200`: `{"items": [CustomerListItem, ...], "total": N}`

```
CustomerListItem:
  customer_id: int
  code: str
  name: str
  zone: str | null
  credit_limit: Decimal
  credit_days: int
  price_list: int
  salesperson: int | null
  disabled: bool | null
```

### `POST /api/v1/customers`

Body: `CustomerCreate`

```
CustomerCreate:
  code: str
  name: str
  zone: str | null
  credit_limit: Decimal
  credit_days: int
  price_list: int
  shipping: bool
  shipping_required_document: bool
  salesperson: int | null
  comment: str | null
```

Response `201`: `CustomerResponse`

### `GET /api/v1/customers/{customer_id}`

Response `200`: `CustomerResponse`

```
CustomerResponse:
  customer_id: int
  code: str
  name: str
  zone: str | null
  credit_limit: Decimal
  credit_days: int
  price_list: int
  shipping: bool
  shipping_required_document: bool
  salesperson: int | null
  disabled: bool | null
  comment: str | null
```

### `PUT /api/v1/customers/{customer_id}`

Body: `CustomerUpdate` (all fields optional)  
Response `200`: `CustomerResponse`

### `DELETE /api/v1/customers/{customer_id}`

Response `204`  
Error `409` if `customer_id == settings.default_customer_id`.

---

## 4. Labels

**Prefix**: `/api/v1/labels`

### `GET /api/v1/labels`

Query: `search` (name), `skip`, `limit`  
Response `200`: `{"items": [LabelResponse, ...], "total": N}`

### `POST /api/v1/labels`

Body: `{"name": str, "comment": str | null}`  
Response `201`: `LabelResponse`

### `GET /api/v1/labels/{label_id}`  `PUT /api/v1/labels/{label_id}`  `DELETE /api/v1/labels/{label_id}`

```
LabelResponse:
  label_id: int
  name: str
  comment: str | null
```

---

## 5. Taxpayer Recipients

**Prefix**: `/api/v1/taxpayer-recipients`

### `GET /api/v1/taxpayer-recipients`

Query: `search` (taxpayer_recipient_id, name), `skip`, `limit`  
Response `200`: `{"items": [TaxpayerRecipientResponse, ...], "total": N}`

### `POST /api/v1/taxpayer-recipients`

Body:
```
TaxpayerRecipientCreate:
  taxpayer_recipient_id: str   # 12–13 chars (RFC), used as PK
  name: str | null
  email: str
  postal_code: str | null
  regime: str | null
```
Response `201`: `TaxpayerRecipientResponse`

### `GET /api/v1/taxpayer-recipients/{rfc}`  `PUT .../{rfc}`  `DELETE .../{rfc}`

PK is the RFC string.

```
TaxpayerRecipientResponse:
  taxpayer_recipient_id: str
  name: str | null
  email: str
  postal_code: str | null
  regime: str | null
```

---

## 6. Suppliers

**Prefix**: `/api/v1/suppliers`

Standard CRUD. Search by `code`, `name`, `zone`.

```
SupplierCreate / SupplierUpdate:
  code: str
  name: str
  zone: str | null
  credit_limit: Decimal
  credit_days: int
  comment: str | null

SupplierResponse:
  supplier_id: int
  code: str
  name: str
  zone: str | null
  credit_limit: Decimal
  credit_days: int
  comment: str | null
```

---

## 7. Employees

**Prefix**: `/api/v1/employees`

Search: `first_name`, `last_name`, `nickname`. Filters: `active` (bool), `sales_person` (bool).

```
EmployeeCreate / EmployeeUpdate:
  first_name: str
  last_name: str
  nickname: str
  gender: int
  birthday: date
  taxpayer_id: str | null
  sales_person: bool
  active: bool
  personal_id: str | null
  start_job_date: date
  enroll_number: int | null
  comment: str | null

EmployeeResponse:
  employee_id: int
  first_name: str
  last_name: str
  nickname: str
  gender: int
  birthday: date
  taxpayer_id: str | null
  sales_person: bool
  active: bool
  personal_id: str | null
  start_job_date: date
  enroll_number: int | null
  comment: str | null
  disabled: bool | null
```

---

## 8. Warehouses

**Prefix**: `/api/v1/warehouses`

Standard CRUD. Filter by `store`.

```
WarehouseCreate / WarehouseUpdate:
  store: int
  code: str
  name: str
  comment: str | null
  disabled: bool | null

WarehouseResponse:
  warehouse_id: int
  store: int
  code: str
  name: str
  comment: str | null
  disabled: bool | null
```

---

## 9. Points of Sale

**Prefix**: `/api/v1/points-of-sale`

List filter params: `store` (int), `warehouse` (int), `skip`, `limit`.

```
PointSaleCreate / PointSaleUpdate:
  store: int
  code: str
  name: str
  warehouse: int
  comment: str | null
  disabled: bool | null

PointSaleResponse:
  point_sale_id: int
  store: int
  code: str
  name: str
  warehouse: int
  comment: str | null
  disabled: bool | null
```

---

## 10. Cash Drawers

**Prefix**: `/api/v1/cash-drawers`

List filter params: `store` (int), `skip`, `limit`.

```
CashDrawerCreate / CashDrawerUpdate:
  store: int
  code: str
  name: str
  comment: str | null
  disabled: bool | null

CashDrawerResponse:
  cash_drawer_id: int
  store: int
  code: str
  name: str
  comment: str | null
  disabled: bool | null
```

---

## 11. Stores

**Prefix**: `/api/v1/stores`

```
StoreCreate / StoreUpdate:
  code: str
  name: str
  location: str          # FK sat_postal_code
  address: int           # FK address
  taxpayer: str          # FK taxpayer_issuer
  logo: str
  receipt_message: str | null
  default_batch: str | null
  disabled: bool | null

StoreResponse:
  store_id: int
  code: str
  name: str
  location: str
  address: int
  taxpayer: str
  logo: str
  receipt_message: str | null
  default_batch: str | null
  disabled: bool | null
```

---

## 12. Exchange Rates

**Prefix**: `/api/v1/exchange-rates`

Query filters: `date_from` (date), `date_to` (date), `base` (int CurrencyCode), `target` (int CurrencyCode).

```
ExchangeRateCreate / ExchangeRateUpdate:
  date: date
  rate: Decimal
  base: int     # CurrencyCode
  target: int   # CurrencyCode

ExchangeRateResponse:
  exchange_rate_id: int
  date: date
  rate: Decimal
  base: int
  target: int
```

Conflict `409` on duplicate `(date, base, target)`.

---

## 13. Expenses

**Prefix**: `/api/v1/expenses`

```
ExpenseCreate / ExpenseUpdate:
  name: str       # maps to expense.expense column
  comment: str | null

ExpenseResponse:
  expense_id: int
  name: str
  comment: str | null
```

---

## 14. Payment Method Options

**Prefix**: `/api/v1/payment-method-options`

Filter by `store`.

```
PaymentMethodOptionCreate / PaymentMethodOptionUpdate:
  store: int
  warehouse: int | null
  name: str
  number_of_payments: int
  display_on_ticket: bool
  payment_method: int
  commission: Decimal
  enabled: bool

PaymentMethodOptionResponse:
  payment_method_option_id: int
  store: int
  warehouse: int | null
  name: str
  number_of_payments: int
  display_on_ticket: bool
  payment_method: int
  commission: Decimal
  enabled: bool
```

---

## 15. Vehicles

**Prefix**: `/api/v1/vehicles`

```
VehicleCreate / VehicleUpdate:
  license_plate: str   # unique
  name: str
  nickname: str
  tons_capacity: int
  active: bool

VehicleResponse:
  vehicle_id: int
  license_plate: str
  name: str
  nickname: str
  tons_capacity: int
  active: bool
```

---

## 16. Vehicle Operators

**Prefix**: `/api/v1/vehicle-operators`

List filter params: `employee` (int — filters by `VehicleOperator.driver`), `skip`, `limit`.

```
VehicleOperatorCreate / VehicleOperatorUpdate:
  driver: int
  license_type: str
  driver_license_number: str
  issue_date: date
  expiration_date: date
  issuing_location: str
  active: bool

VehicleOperatorResponse:
  vehicle_operator_id: int
  driver: int
  license_type: str
  driver_license_number: str
  issue_date: date
  expiration_date: date
  issuing_location: str
  active: bool
  days_until_expiry: int   # computed: negative = expired
```

---

## 17. Production Sites

**Prefix**: `/api/v1/production-sites`

Filter by `store`.

```
ProductionSiteCreate / ProductionSiteUpdate:
  store: int
  code: str
  name: str
  comment: str | null
  disabled: bool | null

ProductionSiteResponse:
  production_site_id: int
  store: int
  code: str
  name: str
  comment: str | null
  disabled: bool | null
```

---

## 18–25. SAT Catalog Reference Data (Read-Only)

**Prefix**: `/api/v1/sat`

All 8 SAT catalogs follow the same pattern — list and get-by-id only. No write operations.

| # | Path prefix | ID field | ID type |
|---|------------|----------|---------|
| 18 | `/api/v1/sat/cfdi-usages` | `sat_cfdi_usage_id` | str(4) |
| 19 | `/api/v1/sat/countries` | `sat_country_id` | str(3) |
| 20 | `/api/v1/sat/currencies` | `sat_currency_id` | str(3) |
| 21 | `/api/v1/sat/postal-codes` | `sat_postal_code_id` | str(5) |
| 22 | `/api/v1/sat/product-services` | `sat_product_service_id` | str(8) |
| 23 | `/api/v1/sat/reason-cancellations` | `sat_reason_cancellation_id` | str(2) |
| 24 | `/api/v1/sat/tax-regimes` | `sat_tax_regime_id` | str(3) |
| 25 | `/api/v1/sat/units-of-measurement` | `sat_unit_of_measurement_id` | str(3) |

**For each catalog:**

```
GET /api/v1/sat/{resource}
  Query: skip (int, default 0), limit (int, 1–100, default 20)
  Response 200: {"items": [SatXxxResponse, ...], "total": N}
  Response 401: unauthenticated

GET /api/v1/sat/{resource}/{id}
  Response 200: SatXxxResponse
  Response 404: {"detail": "Not found"}
  Response 401: unauthenticated

SatXxxResponse:
  id: str   # the PK value (e.g. "H87", "MXN", "G01")
```

**Write operations**: POST, PUT, DELETE are not registered. FastAPI returns 405 Method Not Allowed automatically.
