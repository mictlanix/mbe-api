# Feature Specification: Master Data REST Endpoints

**Feature Branch**: `002-master-data-endpoints`

**Created**: 2026-06-14

**Status**: Draft

**Input**: User description: "implement the endpoints covered by @docs/specs/01-master-data.md, reuse the existing models and update accordingly"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Browse and Search Catalog Entities (Priority: P1)

An authenticated API consumer needs to retrieve lists of master data records (products, customers, suppliers, etc.) with optional filtering and search to power lookups, dropdowns, and data-grid screens.

**Why this priority**: Every transactional screen (sales orders, inventory, POS) depends on being able to query master data. Without list endpoints nothing else works.

**Independent Test**: Can be fully tested by calling `GET /api/v1/products` (and other list endpoints) with search/filter parameters and verifying paginated results are returned.

**Acceptance Scenarios**:

1. **Given** a valid authenticated session, **When** `GET /api/v1/products` is called with no parameters, **Then** a paginated list of all active products is returned with at least `id`, `code`, `name`, `brand`, `model`, `unit_of_measurement`, `tax_rate`, and `deactivated`.
2. **Given** products exist, **When** `GET /api/v1/products?search=acero` is called, **Then** only products whose `code`, `name`, `model`, `sku`, or `brand` match the term are returned.
3. **Given** an unauthenticated caller, **When** any list endpoint is called, **Then** `401 Unauthorized` is returned.
4. **Given** a valid session, **When** `GET /api/v1/customers?search=juan` is called, **Then** results are filtered by `code`, `name`, or `zone`.

---

### User Story 2 - Create Master Data Records (Priority: P1)

An authenticated user with appropriate privileges creates new records in any master data catalog.

**Why this priority**: Without create endpoints there is no way to populate data. P1 alongside list because they are the minimal working pair.

**Independent Test**: Can be fully tested by posting a valid payload to `POST /api/v1/products` and verifying the returned record matches the submitted data plus system-computed defaults.

**Acceptance Scenarios**:

1. **Given** a valid authenticated session, **When** `POST /api/v1/products` is called with a valid payload, **Then** a new product is created and returned with `201 Created`; all system defaults are applied automatically (`min_order_qty = 1`, `stock_required = true`, default VAT, price type, photo, and one `product_price` row per existing price list).
2. **Given** a product with `code = "ABC"` already exists, **When** `POST /api/v1/products` is called with `code = "ABC"`, **Then** `409 Conflict` is returned.
3. **Given** a product payload with `bar_code = "123"` (less than 13 digits), **When** `POST /api/v1/products` is called, **Then** `422 Unprocessable Entity` is returned.
4. **Given** a valid session, **When** `POST /api/v1/price-lists` is called with a unique name, **Then** a new price list is created and returned with `201 Created`.

---

### User Story 3 - Retrieve a Single Master Data Record (Priority: P2)

An authenticated user fetches the full detail of a specific master data record by its primary key.

**Why this priority**: Required to populate edit forms and display detail screens, but list + create alone form a working MVP.

**Independent Test**: Can be fully tested by calling `GET /api/v1/products/{id}` for an existing product and verifying all fields are returned.

**Acceptance Scenarios**:

1. **Given** a product with `id = 5` exists, **When** `GET /api/v1/products/5` is called, **Then** all product fields are returned including sub-panel data (prices and labels).
2. **Given** no product with `id = 999` exists, **When** `GET /api/v1/products/999` is called, **Then** `404 Not Found` is returned.

---

### User Story 4 - Update Master Data Records (Priority: P2)

An authenticated user with appropriate privileges updates an existing master data record.

**Why this priority**: Data correction is important but secondary to read flows.

**Independent Test**: Can be fully tested by calling `PUT /api/v1/products/{id}` with changed field values and verifying the returned record reflects the update.

**Acceptance Scenarios**:

1. **Given** a product exists, **When** `PUT /api/v1/products/{id}` is called with updated fields, **Then** the record is updated and the full updated record is returned.
2. **Given** a product exists and `code` is changed to a value already used by another product, **When** `PUT /api/v1/products/{id}` is called, **Then** `409 Conflict` is returned.
3. **Given** a product does not exist, **When** `PUT /api/v1/products/{id}` is called, **Then** `404 Not Found` is returned.

---

### User Story 5 - Delete Master Data Records (Priority: P3)

An authenticated user with appropriate privileges removes a master data record.

**Why this priority**: Deletion is lower-risk to defer than other operations; records can be deactivated/disabled in the meantime.

**Independent Test**: Can be fully tested by calling `DELETE /api/v1/products/{id}` and verifying `204 No Content` and that subsequent GET returns 404.

**Acceptance Scenarios**:

1. **Given** a product exists, **When** `DELETE /api/v1/products/{id}` is called, **Then** the product and all its `product_price` rows are removed and `204 No Content` is returned.
2. **Given** the system default customer is targeted, **When** `DELETE /api/v1/customers/{id}` is called, **Then** `409 Conflict` is returned with an explanation.
3. **Given** a price list is assigned to one or more customers, **When** `DELETE /api/v1/price-lists/{id}` is called, **Then** `409 Conflict` is returned.

---

### User Story 6 - Merge Duplicate Products (Priority: P3)

An administrator merges a duplicate product into a canonical product to clean up data-entry errors.

**Why this priority**: Utility endpoint; important for data hygiene but not on the critical path for day-to-day operations.

**Independent Test**: Can be fully tested by calling `POST /api/v1/products/merge` with a valid `{product_id, duplicate_id}` payload and verifying the duplicate is gone and FK references now point to the canonical product.

**Acceptance Scenarios**:

1. **Given** two products A (canonical) and B (duplicate), **When** `POST /api/v1/products/merge` is called with `{product_id: A.id, duplicate_id: B.id}`, **Then** all FK references in transactional tables are remapped from B to A, B is deleted, and `204 No Content` is returned.
2. **Given** the caller lacks the `ProductsMerge (73)` privilege with `AllowCreate`, **When** the merge endpoint is called, **Then** `403 Forbidden` is returned.

---

### Edge Cases

- What happens when a search returns no results? → Empty list `{items: [], total: 0}` with `200 OK`.
- How does the system handle creating a product with `unit_of_measurement` that does not exist in the SAT catalog? → `422 Unprocessable Entity` (FK validation fails).
- What happens when merging a product with itself (`product_id == duplicate_id`)? → `400 Bad Request`.
- What happens when a vehicle operator's license expiry date is in the past? → The record is saved but flagged (response includes `active` status); clients should not assign expired operators to itineraries.
- What happens when deleting a label still assigned to products? → Allow delete (junction rows are removed); no conflict check required by the spec.

## Requirements *(mandatory)*

### Functional Requirements

**Products (`/api/v1/products`)**

- **FR-001**: System MUST expose `GET /api/v1/products` returning a paginated list searchable by `code`, `name`, `model`, `sku`, and `brand`; filterable by `deactivated`, `stockable`, `salable`, `purchasable`, and `label`.
- **FR-002**: System MUST expose `POST /api/v1/products` to create a product; on creation the system MUST auto-set: `min_order_qty = 1`, `stock_required = true` (model field `stock_verification`), `tax_rate` from config default, `tax_included` from config default, `price_type` from config default, `photo` from config default, and one `product_price` row per existing price list (price = 0).
- **FR-003**: System MUST expose `GET /api/v1/products/{id}` returning full product detail including prices and labels.
- **FR-004**: System MUST expose `PUT /api/v1/products/{id}` to update a product.
- **FR-005**: System MUST expose `DELETE /api/v1/products/{id}` to hard-delete a product and all its `product_price` rows.
- **FR-006**: System MUST expose `POST /api/v1/products/merge` to merge a duplicate product into a canonical product; requires the caller to hold `AllowCreate` on `SystemObject 73 (ProductsMerge)`.
- **FR-007**: `code` MUST be unique (1–25 chars, no whitespace). `bar_code` MUST be either empty or exactly 13 digits. `name` MUST be 4–250 chars.

**Price Lists (`/api/v1/price-lists`)**

- **FR-008**: System MUST expose `GET`, `POST`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}` for price lists.
- **FR-009**: A price list MUST NOT be deleted if assigned to any customer; the delete endpoint MUST return `409 Conflict` in that case.

**Customers (`/api/v1/customers`)**

- **FR-010**: System MUST expose `GET /api/v1/customers` returning a paginated list searchable by `code`, `name`, `zone`; filterable by `disabled`.
- **FR-011**: System MUST expose `POST`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}` for customers.
- **FR-012**: The system-default customer (id from config) MUST NOT be deletable; the delete endpoint MUST return `409 Conflict`.

**Labels (`/api/v1/labels`)**

- **FR-013**: System MUST expose full CRUD for labels (`/api/v1/labels`).

**Taxpayer Recipients (`/api/v1/taxpayer-recipients`)**

- **FR-014**: System MUST expose full CRUD for taxpayer recipients. RFC (`taxpayer_recipient_id`) MUST be 12–13 characters.

**Suppliers (`/api/v1/suppliers`)**

- **FR-015**: System MUST expose `GET /api/v1/suppliers` searchable by `code`, `name`, `zone`; plus `POST`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}`.

**Employees (`/api/v1/employees`)**

- **FR-016**: System MUST expose `GET /api/v1/employees` searchable by `first_name`/`last_name`/`nickname`; filterable by `active`, `sales_person`; plus `POST`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}`.

**Warehouses (`/api/v1/warehouses`)**

- **FR-017**: System MUST expose full CRUD for warehouses.

**Points of Sale (`/api/v1/points-of-sale`)**

- **FR-018**: System MUST expose full CRUD for points of sale.

**Cash Drawers (`/api/v1/cash-drawers`)**

- **FR-019**: System MUST expose full CRUD for cash drawers.

**Stores (`/api/v1/stores`)**

- **FR-020**: System MUST expose full CRUD for stores.

**Exchange Rates (`/api/v1/exchange-rates`)**

- **FR-021**: System MUST expose `GET /api/v1/exchange-rates` filterable by date range and currency pair; plus `POST`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}`.

**Expenses (`/api/v1/expenses`)**

- **FR-022**: System MUST expose full CRUD for expenses (catalog categories).

**Payment Method Options (`/api/v1/payment-method-options`)**

- **FR-023**: System MUST expose full CRUD for payment method options.

**Vehicles (`/api/v1/vehicles`)**

- **FR-024**: System MUST expose full CRUD for vehicles.

**Vehicle Operators (`/api/v1/vehicle-operators`)**

- **FR-025**: System MUST expose full CRUD for vehicle operators. The response MUST include an advisory flag when the license is within 30 days of expiry or already expired.

**Production Sites (`/api/v1/production-sites`)**

- **FR-026**: System MUST expose full CRUD for production sites.

**General**

- **FR-027**: All endpoints MUST require a valid authenticated JWT (`get_current_user`).
- **FR-028**: All list endpoints MUST support `skip` and `limit` pagination parameters and return a `{items: [...], total: N}` envelope.
- **FR-029**: All write endpoints MUST return the persisted record (or `204 No Content` for deletes).

### Key Entities *(include if feature involves data)*

- **Product**: The central SKU record. Has prices per price list, label tags, a supplier, SAT catalog references, and boolean flags controlling stockability, perishability, traceability, and sales eligibility.
- **PriceList**: Named pricing tier. Products have one price per list. Assigned to customers.
- **Customer**: Buyer entity with credit terms, zone, price list assignment, and an optional salesperson.
- **Supplier**: Vendor entity with credit terms and zone.
- **Employee**: Internal staff record; subset flagged as salespersons.
- **Store**: Top-level branch; all warehouses, POS terminals, and cash drawers belong to a store.
- **Warehouse**: Physical stock location within a store.
- **PointSale**: POS terminal configuration tied to a store and warehouse.
- **CashDrawer**: Physical cash drawer hardware tied to a store.
- **Label**: Classification tag applied to products.
- **TaxpayerRecipient**: RFC record for CFDI invoice recipients.
- **ExchangeRate**: Daily currency conversion rate (base → target).
- **Expense**: Category label for petty-cash vouchers.
- **PaymentMethodOption**: Per-store payment method configuration including installments and commission.
- **Vehicle**: Fleet vehicle with load capacity.
- **VehicleOperator**: Driver license record for an employee.
- **ProductionSite**: Manufacturing line configuration within a store.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All 17 master data resources respond to their `GET` list endpoint within 500 ms under normal load.
- **SC-002**: All mandatory field validations (code uniqueness, bar code length, RFC length) return an error response before any data is persisted — zero silent data corruption.
- **SC-003**: The product merge operation leaves zero orphan FK references to the deleted duplicate across all transactional tables.
- **SC-004**: All endpoints return `401` for unauthenticated requests and never expose record data to unauthenticated callers.
- **SC-005**: The product creation auto-default logic (prices, defaults) executes atomically — either all rows are created or none are.

## Assumptions

- All SQLAlchemy models listed in `app/models/` (product.py, customer.py, core.py, supplier.py, fiscal.py) are reused without schema changes unless a field name mismatch requires a model correction (e.g., `stock_verification` vs. spec's `stock_required`).
- The field `product.stock_verification` in the ORM model corresponds to `stock_required` in the business spec; the ORM field name will be kept as-is and the API response will expose it as `stock_required`.
- Config defaults (`DefaultVAT`, `IsTaxIncluded`, `DefaultPriceType`, `DefaultPhotoFile`, `DefaultCustomer`) are available through a config/settings object rather than `WebConfig` (legacy .NET concept); implementation will use the appropriate Python config mechanism.
- Sub-panel endpoints for customers (addresses, contacts, taxpayers, discounts) and suppliers (addresses, contacts, bank accounts, agreements) are **out of scope** for this feature; only the parent resource CRUD is in scope.
- The `Incidence` logging mentioned in the product spec is deferred — it is not a hard requirement for this feature.
- Authentication and privilege enforcement use the existing `get_current_user` and `require_admin` dependency pattern; per-resource privilege checks (SystemObject-based) are out of scope unless explicitly noted (the product merge endpoint requires `ProductsMerge` privilege check).
- Tests are optional per the project constitution; the plan will include tests if the implementer decides to include them.
- Exchange rates are uniquely constrained by `(date, base, target)` pair; attempting to create a duplicate returns `409 Conflict`.
