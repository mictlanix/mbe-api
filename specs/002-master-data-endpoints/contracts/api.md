# API Contracts: Master Data REST Endpoints

**Base URL prefix**: `/api/v1`  
**Auth**: All endpoints require `Authorization: Bearer <jwt>` header.  
**Pagination**: All list endpoints accept `skip: int = 0` and `limit: int = 1..100` query params
and return `{"items": [...], "total": N}`.  
**Shape of a resource**: every section below is standard CRUD — `GET` the collection, `POST` to
create, and `GET` / `PUT` / `DELETE` by id — so only the filters and the payload shapes are spelled
out. Exceptions (uploads, merge, facets, read-only catalogs) are named where they occur.  
**Error format**: FastAPI default `{"detail": "..."}`.

> **Reconciled against the running application on 2026-08-07**, field by field, from
> `GET /openapi.json`. This document was written before spec 005 (which replaced every
> `disabled`/`deactivated`/`active`/`enabled` boolean with the unified `status: EntityStatus`),
> before FR-039's FK expansion reached most resources, and before #132/#133/#150 added a customer's
> linked collections — so it described shapes no endpoint had returned for some time. Every
> pseudo-schema below now matches the live component of the same name, including nested types.
>
> `EntityStatus` is an **integer** enum: `0` ACTIVE, `1` INACTIVE, `2` ARCHIVED.

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

**Privileges**: All product endpoints require the `PRODUCTS (0)` privilege with the access right
matching the operation (`READ` for GETs, `CREATE` for POST, `UPDATE` for PUT and image upload,
`DELETE` for DELETE); insufficient privilege returns `403`. The merge endpoint requires
`PRODUCTS_MERGE (73)` / `CREATE` instead.

### `GET /api/v1/products`

Query params: `search` (code, name, model, sku, brand), `label` (int, repeatable — e.g.
`?label=2&label=5`; when repeated, a product must carry **all** given labels), `status`
(EntityStatus), `stockable` (bool), `salable` (bool), `purchasable` (bool), `supplier` (int),
`skip`, `limit`.

Response `200`: `{"items": [ProductListItem, ...], "total": N}`

```
ProductListItem:
  product_id: int
  code: str
  name: str
  sku: str | null
  photo: str | null            # absolute image URL, or null
  brand: str | null
  model: str | null
  unit_of_measurement: SatUnitOfMeasurementResponse   # {id, name, description, symbol}
  tax_rate: Decimal
  status: EntityStatus
```

### `GET /api/v1/products/labels/facets`

Added for the faceted product-filter UI (GH #78). Accepts the **same filter query params** as
`GET /api/v1/products` — `search`, `label` (repeatable), `status`, `stockable`, `salable`,
`purchasable`, `supplier` — but **no `skip`/`limit`**: it summarizes the whole matching set, not a
page. The `label` filter is applied with the same AND semantics as the list endpoint, so passing
`label=3` restricts the base set before computing co-occurring labels.

Response `200`: `[ProductLabelFacet, ...]` (plain array, not `{items, total}`)

```
ProductLabelFacet:
  label_id: int
  count: int   # number of matching products carrying this label
```

A label ID absent from the response carries none of the currently-matching products (selecting it
would yield an empty result).

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
  labels: [int, ...] | null     # label IDs to attach on create
```

Response `201`: `ProductResponse` (full product record). Per-product pricing is **not** created
here — `product_price` rows are managed independently via `/api/v1/product-prices`
(`specs/004-price-management-service`); creating a product no longer auto-provisions price rows.

### `GET /api/v1/products/{product_id}`

Response `200`: `ProductResponse`

```
ProductResponse:
  product_id: int
  code: str
  name: str
  photo: str | null                              # absolute image URL, or null
  sku: str | null
  brand: str | null
  model: str | null
  bar_code: str | null
  location: str | null
  unit_of_measurement: SatUnitOfMeasurementResponse   # {id, name, description, symbol}
  key: SatCatalogResponse | null                      # {id, description}
  tax_rate: Decimal
  tax_included: bool
  price_type: int
  currency: int
  min_order_qty: int
  supplier: SupplierResponse | null
  stockable: bool
  perishable: bool
  seriable: bool
  purchasable: bool
  salable: bool
  invoiceable: bool
  stock_verification: bool   # the column's own name; `stock_required` is the *request* field
  status: EntityStatus
  comment: str | null
  labels: [LabelResponse, ...]
```

Per-product prices are **not** embedded in `ProductResponse` — fetch them via
`GET /api/v1/product-prices?product={product_id}` (see `specs/004-price-management-service`).

### `PUT /api/v1/products/{product_id}`

Body: `ProductUpdate` (same fields as `ProductCreate`, all optional, plus `min_order_qty: int | null`
and `status: EntityStatus | null`)  
Response `200`: `ProductResponse`

### `POST /api/v1/products/{product_id}/image`

Multipart `image` upload (`UPDATE` right); returns the updated `ProductResponse` with `photo` set to
the absolute URL. See `specs/003-product-image-upload` for the accepted types and size limit.

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

### `GET /api/v1/products/merge/preview`

Added by [feature 010](../../010-product-merge-integrity/contracts/product-merge.md), which also
specifies what a `204` from the merge above guarantees — including the duplicate's prices,
labels, commission assignment and per-customer discounts being discarded rather than moved.

Query: `product_id`, `duplicate_id` (both required).  
Requires: `AccessRight.READ` on `SystemObject.PRODUCTS_MERGE (73)`.  
Response `200`: `{"items": [{"category": "table.column", "count": N}, ...], "total": N}`

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

Query: `search` (code, name, zone), `status` (EntityStatus), `price_list` (int),
`salesperson` (int), `skip`, `limit`  
Response `200`: `{"items": [CustomerListItem, ...], "total": N}`

```
CustomerListItem:
  customer_id: int
  code: str
  name: str
  zone: str | null
  credit_limit: Decimal
  credit_days: int
  price_list: PriceListResponse            # expanded per FR-039
  salesperson: EmployeeResponse | null     # expanded per FR-039
  status: EntityStatus
```

The linked collections are **detail only** — a page of customers must not cost a query per row for
each of them, so they appear on `CustomerResponse` and not here.

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
  status: EntityStatus
  comment: str | null
  addresses: [int, ...] | null      # existing address ids to link (#132)
  contacts: [int, ...] | null       # existing contact ids to link (#133)
  taxpayers: [str, ...] | null      # RFCs this customer invoices under (#150)
```

The three link collections are **replace-all for a collection the caller actually sent**: omitting
one leaves those links alone, `[]` unlinks everything. Without that distinction an ordinary `PUT`
editing a comment would silently unlink every address, contact and RFC on the customer. The rows
themselves are created through `/api/v1/addresses`, `/api/v1/contacts` and
`/api/v1/taxpayer-recipients`; an id or RFC that does not exist is refused by the foreign key and
reaches the client as `409` (#107).

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
  price_list: PriceListResponse            # expanded per FR-039
  shipping: bool
  shipping_required_document: bool
  salesperson: EmployeeResponse | null     # expanded per FR-039
  status: EntityStatus
  comment: str | null
  addresses: [AddressResponse, ...]        # #132
  contacts: [ContactResponse, ...]         # #133
  taxpayers: [TaxpayerRecipientResponse, ...]   # #150 — a list: customer_taxpayer is many-to-many
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
  postal_code: SatCatalogResponse | null   # expanded per FR-039 — {id, description}
  regime: SatCatalogResponse | null        # expanded per FR-039 — {id, description}
```

Both are **sent as codes and returned as objects**, which is why a recipient embedded elsewhere —
`CustomerResponse.taxpayers` — carries the same expansion rather than the raw codes.

---

## 6. Suppliers

**Prefix**: `/api/v1/suppliers`

Filters: `search` (code, name, zone).

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

Filters: `search` (first name, last name, nickname), `status` (EntityStatus),
`sales_person` (bool).

```
EmployeeCreate / EmployeeUpdate:
  first_name: str
  last_name: str
  nickname: str
  gender: int
  birthday: date
  taxpayer_id: str | null
  sales_person: bool
  status: EntityStatus
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
  personal_id: str | null
  start_job_date: date
  enroll_number: int | null
  comment: str | null
  status: EntityStatus
```

---

## 8. Warehouses

**Prefix**: `/api/v1/warehouses`

**Privileges**: `WAREHOUSES (4)`, access right matching the operation; `403` without it.

Filters: `search` (code, name), `facility` (int), `status` (EntityStatus).

```
WarehouseCreate / WarehouseUpdate:
  facility: int
  code: str
  name: str
  comment: str | null
  status: EntityStatus

WarehouseResponse:
  warehouse_id: int
  facility: FacilitySummary        # expanded per FR-039
  code: str
  name: str
  comment: str | null
  status: EntityStatus
```

---

## 9. Points of Sale

**Prefix**: `/api/v1/points-of-sale`

**Privileges**: `POINTS_OF_SALE (9)`, access right matching the operation; `403` without it.

Filters: `search` (code, name), `facility` (int), `warehouse` (int), `status` (EntityStatus).

```
PointSaleCreate / PointSaleUpdate:
  facility: int
  code: str
  name: str
  warehouse: int
  comment: str | null
  status: EntityStatus

PointSaleResponse:
  point_sale_id: int
  facility: FacilitySummary        # expanded per FR-039
  code: str
  name: str
  warehouse: WarehouseSummary      # expanded per FR-039
  comment: str | null
  status: EntityStatus
```

---

## 10. Cash Drawers

**Prefix**: `/api/v1/cash-drawers`

**Privileges**: `CASH_DRAWERS (10)`, access right matching the operation; `403` without it.

Filters: `search` (code, name), `facility` (int), `status` (EntityStatus).

```
CashDrawerCreate / CashDrawerUpdate:
  facility: int
  code: str
  name: str
  comment: str | null
  status: EntityStatus

CashDrawerResponse:
  cash_drawer_id: int
  facility: FacilitySummary        # expanded per FR-039
  code: str
  name: str
  comment: str | null
  status: EntityStatus
```

---

## 11. Facilities

**Prefix**: `/api/v1/facilities`

**Privileges**: `FACILITIES (29)`, access right matching the operation; `403` without it. Logo
upload answers to `UPDATE`.

Filters: `search` (code, name), `status` (EntityStatus).

### `POST /api/v1/facilities/{facility_id}/logo`

Multipart `image` upload; returns the updated `FacilityResponse` with `logo` set to the stored
filename. Deleting a facility also retires its in-transit warehouse and writes an `incidence` row
(spec 013).

```
FacilityCreate / FacilityUpdate:
  code: str
  name: str
  type: FacilityType     # 0 = store | 1 = production_site; defaults to 0
  location: str          # FK sat_postal_code
  address: int           # FK address
  taxpayer: str          # FK taxpayer_issuer
  logo: str
  receipt_message: str | null
  default_batch: str | null
  status: EntityStatus

FacilityResponse:
  facility_id: int
  code: str
  name: str
  type: FacilityType
  location: SatCatalogResponse     # expanded per FR-039 — the postal code
  address: AddressResponse         # expanded per FR-039
  taxpayer: str                    # RFC of the issuer, not expanded
  logo: str
  receipt_message: str | null
  default_batch: str | null
  status: EntityStatus
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

Filters: `search` (expense name).

```
ExpenseCreate / ExpenseUpdate:
  name: str       # maps to expense.expense column
  comment: str | null

ExpenseResponse:
  expense_id: int
  expense: str   # the column's own name; `name` is the *request* field
  comment: str | null
```

---

## 14. Payment Method Options

**Prefix**: `/api/v1/payment-method-options`

Filters: `facility` (int), `status` (EntityStatus).

```
PaymentMethodOptionCreate / PaymentMethodOptionUpdate:
  facility: int
  warehouse: int | null
  name: str
  number_of_payments: int
  display_on_ticket: bool
  payment_method: int
  commission: Decimal
  status: EntityStatus

PaymentMethodOptionResponse:
  payment_method_option_id: int
  facility: FacilitySummary            # expanded per FR-039
  warehouse: WarehouseSummary | null   # expanded per FR-039
  name: str
  number_of_payments: int
  display_on_ticket: bool
  payment_method: int
  commission: Decimal
  status: EntityStatus
  requires_reference: bool    # derived, never stored (#137)
```

`requires_reference` says whether this tender needs a reference or authorization number before it
can be recorded. It is **derived from the SAT `payment_method` code, not stored**, so it cannot drift
from the catalog — a client enforcing "card, transfer and cheque need one, cash does not" reads it
here instead of keeping its own copy of a mapping this API owns. A code the `PaymentMethod` enum does
not name reports `false`: the permissive default is deliberate, since an unclassified SAT code must
not stop a cashier taking money until someone classifies it. **Nothing is enforced on write** —
recording a card payment with no reference still succeeds. A stored per-facility override was
considered and deferred; see #137.

---

## 15. Vehicles

**Prefix**: `/api/v1/vehicles`

Filters: `search` (license plate, name, nickname), `status` (EntityStatus).

```
VehicleCreate / VehicleUpdate:
  license_plate: str   # unique
  name: str
  nickname: str
  tons_capacity: int
  status: EntityStatus

VehicleResponse:
  vehicle_id: int
  license_plate: str
  name: str
  nickname: str
  tons_capacity: int
  status: EntityStatus
```

---

## 16. Vehicle Operators

**Prefix**: `/api/v1/vehicle-operators`

Filters: `search` (licence number, issuing location), `employee` (int — filters by
`VehicleOperator.driver`), `status` (EntityStatus).

```
VehicleOperatorCreate / VehicleOperatorUpdate:
  driver: int
  license_type: str
  driver_license_number: str
  issue_date: date
  expiration_date: date
  issuing_location: str
  status: EntityStatus

VehicleOperatorResponse:
  vehicle_operator_id: int
  driver: EmployeeResponse    # expanded per FR-039
  license_type: str
  driver_license_number: str
  issue_date: date
  expiration_date: date
  issuing_location: str
  status: EntityStatus
  days_until_expiry: int   # computed: negative = expired
  creation_time: datetime
  modification_time: datetime
  creator: EmployeeResponse   # expanded per FR-039
  updater: EmployeeResponse   # expanded per FR-039
```

---

## 17–24. SAT Catalog Reference Data (Read-Only)

**Prefix**: `/api/v1/sat`

All 8 SAT catalogs follow the same pattern — list and get-by-id only. No write operations.

| # | Path prefix | ID field | ID type |
|---|------------|----------|---------|
| 17 | `/api/v1/sat/cfdi-usages` | `sat_cfdi_usage_id` | str(4) |
| 18 | `/api/v1/sat/countries` | `sat_country_id` | str(3) |
| 19 | `/api/v1/sat/currencies` | `sat_currency_id` | str(3) |
| 20 | `/api/v1/sat/postal-codes` | `sat_postal_code_id` | str(5) |
| 21 | `/api/v1/sat/product-services` | `sat_product_service_id` | str(8) |
| 22 | `/api/v1/sat/reason-cancellations` | `sat_reason_cancellation_id` | str(2) |
| 23 | `/api/v1/sat/tax-regimes` | `sat_tax_regime_id` | str(3) |
| 24 | `/api/v1/sat/units-of-measurement` | `sat_unit_of_measurement_id` | str(3) |

**For each catalog:**

```
GET /api/v1/sat/{resource}
  Query: skip (int, default 0), limit (int, 1–100, default 20)
  Response 200: {"items": [SatCatalogResponse, ...], "total": N}
  Response 401: unauthenticated

GET /api/v1/sat/{resource}/{id}
  Response 200: SatCatalogResponse
  Response 404: {"detail": "Not found"}
  Response 401: unauthenticated

SatCatalogResponse:            # one shape for all 8 catalogs
  id: str            # the PK value (e.g. "H87", "MXN", "G01")
  description: str | null
```

`SatCatalogResponse` is also the shape every expanded SAT foreign key takes elsewhere in this
document — `ProductResponse.key`, `TaxpayerRecipientResponse.postal_code` and `.regime`. The one
exception is a product's unit of measurement, which embeds the fuller
`SatUnitOfMeasurementResponse` (`{id, name, description, symbol}`).

**Write operations**: POST, PUT, DELETE are not registered. FastAPI returns 405 Method Not Allowed automatically.
