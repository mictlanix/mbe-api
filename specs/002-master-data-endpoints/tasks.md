# Tasks: Master Data REST Endpoints

**Input**: Design documents from `specs/002-master-data-endpoints/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/api.md ✅

**Tests**: Not explicitly requested — no test tasks generated.

**Organization**: Phase 1–2 are foundation; Phases 3–8 implement resources in domain batches
(each batch is independently testable via quickstart.md scenarios); Phase 9 is polish.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[US#]**: User story from spec.md (US1=List/Search, US2=Create, US3=Get, US4=Update, US5=Delete, US6=Merge)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project config additions and shared utilities required by every resource.

- [X] T001 Add `default_vat`, `is_tax_included`, `default_price_type`, `default_photo_file`, `default_customer_id` fields to `Settings` class in `app/core/config.py`
- [X] T002 Add `require_privilege(system_object: SystemObject, access_right: AccessRight)` dependency function to `app/core/deps.py` — reads caller's `AccessPrivilege` row and raises `403` if bit not set
- [X] T003 [P] Add `ListResponse` generic Pydantic model (`items: list[T], total: int`) to `app/schemas/__init__.py`

**Checkpoint**: Config and shared utils ready — all subsequent phases can proceed.

---

## Phase 2: Schema Definitions (Foundation — blocks all resource phases)

**Purpose**: Define all Pydantic request/response schemas. All four files can be written in parallel.

- [X] T004 [P] Create `app/schemas/product.py` — `ProductCreate`, `ProductUpdate`, `ProductListItem`, `ProductPriceResponse`, `ProductResponse`, `PriceListCreate`, `PriceListUpdate`, `PriceListResponse`, `ProductMergeRequest`
- [X] T005 [P] Create `app/schemas/customer.py` — `CustomerCreate`, `CustomerUpdate`, `CustomerListItem`, `CustomerResponse`, `TaxpayerRecipientCreate`, `TaxpayerRecipientUpdate`, `TaxpayerRecipientResponse`
- [X] T006 [P] Create `app/schemas/supplier.py` — `SupplierCreate`, `SupplierUpdate`, `SupplierResponse`
- [X] T007 [P] Create `app/schemas/core.py` — schemas for `Label`, `Employee`, `Store`, `Warehouse`, `PointSale`, `CashDrawer`, `ExchangeRate`, `Expense`, `PaymentMethodOption`, `Vehicle`, `VehicleOperator` (include computed `days_until_expiry` via `@model_validator`), `ProductionSite` — each as `*Create`, `*Update`, `*Response` (and `*ListItem` where list columns differ from full response)

**Checkpoint**: All schemas defined — resource services and endpoints can now be implemented in parallel.

---

## Phase 3: Products & Price Lists (US1–US6 — Priority P1)

**Goal**: Products and price lists are the most complex resources (auto-defaults, merge, cross-FK constraint). Completing this batch proves the hardest part works.

**Independent Test**: `quickstart.md` Scenarios 2–5 and 8 (price list CRUD, product create with defaults, duplicate code 409, barcode 422, product merge).

### Products (US1–US6)

- [X] T008 [US1] Create `app/services/product_service.py` — implement `list_products(db, search, label, deactivated, stockable, salable, purchasable, skip, limit)` returning `(Sequence[Product], int)`
- [X] T009 [US2] Add `create_product(db, data, settings)` to `app/services/product_service.py` — applies auto-defaults (`min_order_qty=1`, `stock_verification=True`, VAT, tax_included, price_type, photo from settings), then creates one `ProductPrice` row per existing `PriceList` in the same transaction
- [X] T010 [US3] Add `get_product(db, product_id)` to `app/services/product_service.py` — returns `Product | None` with associated `ProductPrice` rows loaded via explicit select
- [X] T011 [US4] Add `update_product(db, product, data)` to `app/services/product_service.py`
- [X] T012 [US5] Add `delete_product(db, product)` to `app/services/product_service.py` — deletes all `ProductPrice` rows first, then deletes the product
- [X] T013 [US6] Add `merge_products(db, product_id, duplicate_id)` to `app/services/product_service.py` — issues ORM `update()` per table (`sales_order_detail`, `purchase_order_detail`, `inventory_receipt_detail`, `inventory_issue_detail`, `inventory_transfer_detail`, `product_price`, `lot_serial_tracking`, `product_label`) to remap FK from duplicate → canonical, then deletes duplicate
- [X] T014 [US1] Create `app/api/v1/endpoints/products.py` — wire `GET /` (list), `POST /` (create), `GET /{product_id}` (get), `PUT /{product_id}` (update), `DELETE /{product_id}` (delete), `POST /merge` (merge — uses `require_privilege(SystemObject.PRODUCTS_MERGE, AccessRight.CREATE)`)
- [X] T015 [US1] Add `products` router to `app/api/v1/router.py` — `include_router(products.router, prefix="/products", tags=["products"])`

### Price Lists (US1–US5)

- [X] T016 [P] [US1] Create `app/services/price_list_service.py` — `list_price_lists`, `get_price_list`, `create_price_list`, `update_price_list`, `delete_price_list` (delete raises `409` if any `Customer.price_list` references it)
- [X] T017 [P] [US1] Create `app/api/v1/endpoints/price_lists.py` — full CRUD handlers
- [X] T018 [P] [US1] Register `price_lists` router in `app/api/v1/router.py`

**Checkpoint**: Products and price lists fully functional. Run quickstart.md Scenarios 2–5 and 8.

---

## Phase 4: Customers, Labels & Taxpayer Recipients (US1–US5 — Priority P1)

**Goal**: Customer-domain resources. Labels and taxpayer recipients are simple CRUD; customers add a protected-delete rule.

**Independent Test**: `quickstart.md` Scenario 6 (label CRUD round-trip) and Scenario 7 (protected customer delete 409).

### Customers (US1–US5)

- [X] T019 [US1] Create `app/services/customer_service.py` — `list_customers(search, disabled, skip, limit)`, `get_customer`, `create_customer`, `update_customer`, `delete_customer` (raises `409` if `customer_id == settings.default_customer_id`)
- [X] T020 [US1] Create `app/api/v1/endpoints/customers.py` — full CRUD handlers, search by `code`/`name`/`zone`, filter by `disabled`
- [X] T021 [US1] Register `customers` router in `app/api/v1/router.py`

### Labels (US1–US5)

- [X] T022 [P] [US1] Create `app/services/label_service.py` — `list_labels`, `get_label`, `create_label`, `update_label`, `delete_label`
- [X] T023 [P] [US1] Create `app/api/v1/endpoints/labels.py` — full CRUD handlers
- [X] T024 [P] [US1] Register `labels` router in `app/api/v1/router.py`

### Taxpayer Recipients (US1–US5)

- [X] T025 [P] [US1] Create `app/services/taxpayer_recipient_service.py` — `list_taxpayer_recipients(search, skip, limit)`, `get_taxpayer_recipient(rfc)`, `create_taxpayer_recipient`, `update_taxpayer_recipient`, `delete_taxpayer_recipient`; enforce RFC length 12–13 chars at service or schema level
- [X] T026 [P] [US1] Create `app/api/v1/endpoints/taxpayer_recipients.py` — full CRUD handlers; PK path param is RFC string
- [X] T027 [P] [US1] Register `taxpayer_recipients` router in `app/api/v1/router.py`

**Checkpoint**: Customer domain done. Run quickstart.md Scenarios 6 and 7.

---

## Phase 5: Suppliers & Employees (US1–US5 — Priority P1)

**Goal**: Two independent simple-CRUD resources with search support.

**Independent Test**: `GET /api/v1/suppliers` and `GET /api/v1/employees` return paginated lists.

### Suppliers (US1–US5)

- [X] T028 [P] [US1] Create `app/services/supplier_service.py` — `list_suppliers(search, skip, limit)`, `get_supplier`, `create_supplier`, `update_supplier`, `delete_supplier`
- [X] T029 [P] [US1] Create `app/api/v1/endpoints/suppliers.py` — full CRUD; search by `code`/`name`/`zone`
- [X] T030 [P] [US1] Register `suppliers` router in `app/api/v1/router.py`

### Employees (US1–US5)

- [X] T031 [P] [US1] Create `app/services/employee_service.py` — `list_employees(search, active, sales_person, skip, limit)`, `get_employee`, `create_employee`, `update_employee`, `delete_employee`
- [X] T032 [P] [US1] Create `app/api/v1/endpoints/employees.py` — full CRUD; search by name/nickname; filter by `active`, `sales_person`
- [X] T033 [P] [US1] Register `employees` router in `app/api/v1/router.py`

**Checkpoint**: Supplier and employee endpoints live.

---

## Phase 6: Stores, Warehouses, Points of Sale & Cash Drawers (US1–US5 — Priority P1)

**Goal**: Store-domain hierarchy resources. All four can be implemented in parallel once schemas exist.

**Independent Test**: `GET /api/v1/stores`, `/warehouses`, `/points-of-sale`, `/cash-drawers` each return paginated lists.

### Stores (US1–US5)

- [X] T034 [P] [US1] Create `app/services/store_service.py` — `list_stores`, `get_store`, `create_store`, `update_store`, `delete_store`
- [X] T035 [P] [US1] Create `app/api/v1/endpoints/stores.py` — full CRUD
- [X] T036 [P] [US1] Register `stores` router in `app/api/v1/router.py`

### Warehouses (US1–US5)

- [X] T037 [P] [US1] Create `app/services/warehouse_service.py` — `list_warehouses(store, skip, limit)`, `get_warehouse`, `create_warehouse`, `update_warehouse`, `delete_warehouse`
- [X] T038 [P] [US1] Create `app/api/v1/endpoints/warehouses.py` — full CRUD; optional `store` filter
- [X] T039 [P] [US1] Register `warehouses` router in `app/api/v1/router.py`

### Points of Sale (US1–US5)

- [X] T040 [P] [US1] Create `app/services/point_sale_service.py` — `list_point_sales`, `get_point_sale`, `create_point_sale`, `update_point_sale`, `delete_point_sale`
- [X] T041 [P] [US1] Create `app/api/v1/endpoints/points_of_sale.py` — full CRUD
- [X] T042 [P] [US1] Register `points_of_sale` router in `app/api/v1/router.py` with prefix `/points-of-sale`

### Cash Drawers (US1–US5)

- [X] T043 [P] [US1] Create `app/services/cash_drawer_service.py` — `list_cash_drawers`, `get_cash_drawer`, `create_cash_drawer`, `update_cash_drawer`, `delete_cash_drawer`
- [X] T044 [P] [US1] Create `app/api/v1/endpoints/cash_drawers.py` — full CRUD
- [X] T045 [P] [US1] Register `cash_drawers` router in `app/api/v1/router.py`

**Checkpoint**: Store-domain hierarchy fully accessible.

---

## Phase 7: Exchange Rates, Expenses & Payment Method Options (US1–US5 — Priority P1)

**Goal**: Financial catalog resources. Exchange rates have a uniqueness constraint on `(date, base, target)`.

**Independent Test**: `GET /api/v1/exchange-rates`, `/expenses`, `/payment-method-options` return paginated lists; `POST /exchange-rates` with duplicate `(date, base, target)` returns 409.

### Exchange Rates (US1–US5)

- [X] T046 [P] [US1] Create `app/services/exchange_rate_service.py` — `list_exchange_rates(date_from, date_to, base, target, skip, limit)`, `get_exchange_rate`, `create_exchange_rate` (raises `409` on duplicate `(date, base, target)`), `update_exchange_rate`, `delete_exchange_rate`
- [X] T047 [P] [US1] Create `app/api/v1/endpoints/exchange_rates.py` — full CRUD; filter by `date_from`, `date_to`, `base`, `target`
- [X] T048 [P] [US1] Register `exchange_rates` router in `app/api/v1/router.py`

### Expenses (US1–US5)

- [X] T049 [P] [US1] Create `app/services/expense_service.py` — `list_expenses`, `get_expense`, `create_expense`, `update_expense`, `delete_expense`; map `Expense.expense` column to `name` in service layer
- [X] T050 [P] [US1] Create `app/api/v1/endpoints/expenses.py` — full CRUD
- [X] T051 [P] [US1] Register `expenses` router in `app/api/v1/router.py`

### Payment Method Options (US1–US5)

- [X] T052 [P] [US1] Create `app/services/payment_method_option_service.py` — `list_payment_method_options(store, skip, limit)`, `get_payment_method_option`, `create_payment_method_option`, `update_payment_method_option`, `delete_payment_method_option`
- [X] T053 [P] [US1] Create `app/api/v1/endpoints/payment_method_options.py` — full CRUD; optional `store` filter
- [X] T054 [P] [US1] Register `payment_method_options` router in `app/api/v1/router.py` with prefix `/payment-method-options`

**Checkpoint**: Financial catalog resources live.

---

## Phase 8: Vehicles, Vehicle Operators & Production Sites (US1–US5 — Priority P1/P3)

**Goal**: Fleet and manufacturing resources. Vehicle operators require the computed `days_until_expiry` field.

**Independent Test**: `quickstart.md` Scenario 9 (vehicle operator with past expiry returns negative `days_until_expiry`).

### Vehicles (US1–US5)

- [X] T055 [P] [US1] Create `app/services/vehicle_service.py` — `list_vehicles`, `get_vehicle`, `create_vehicle`, `update_vehicle`, `delete_vehicle`
- [X] T056 [P] [US1] Create `app/api/v1/endpoints/vehicles.py` — full CRUD
- [X] T057 [P] [US1] Register `vehicles` router in `app/api/v1/router.py`

### Vehicle Operators (US1–US5)

- [X] T058 [P] [US1] Create `app/services/vehicle_operator_service.py` — `list_vehicle_operators`, `get_vehicle_operator`, `create_vehicle_operator`, `update_vehicle_operator`, `delete_vehicle_operator`; `creation_time` and `modification_time` set to `datetime.utcnow()` on create/update
- [X] T059 [P] [US1] Create `app/api/v1/endpoints/vehicle_operators.py` — full CRUD; response includes computed `days_until_expiry` (from `VehicleOperatorResponse` schema validator)
- [X] T060 [P] [US1] Register `vehicle_operators` router in `app/api/v1/router.py` with prefix `/vehicle-operators`

### Production Sites (US1–US5)

- [X] T061 [P] [US1] Create `app/services/production_site_service.py` — `list_production_sites(store, skip, limit)`, `get_production_site`, `create_production_site`, `update_production_site`, `delete_production_site`
- [X] T062 [P] [US1] Create `app/api/v1/endpoints/production_sites.py` — full CRUD; optional `store` filter
- [X] T063 [P] [US1] Register `production_sites` router in `app/api/v1/router.py` with prefix `/production-sites`

**Checkpoint**: All 17 resources implemented. Run quickstart.md Scenario 9.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T064 Run `uv run ruff check app/` and fix all violations (E, F, I, UP rules; 100-char line limit)
- [X] T065 Update `CHANGELOG.md` `[Unreleased]` section — add `Added` entries for all 17 new resource endpoints
- [X] T066 Run quickstart.md Scenarios 1–10 end-to-end and confirm all expected HTTP codes match

---

## Phase 10: FK Filters on Existing List Endpoints (US1 — Refinement)

**Goal**: Add the missing FK query-parameter filters to 5 already-implemented list endpoints. Each task edits both the service function signature and the endpoint handler — they touch different files so T069–T071 are parallelisable with each other but must follow T067/T068 only if sharing the same files (they do not).

**Independent Test**: `quickstart.md` Scenarios 11–12 (products by supplier, customers by price list).

- [X] T067 [US1] Add `supplier: int | None = Query(None)` param to `list_products` in `app/services/product_service.py` and expose it in `GET /products` handler in `app/api/v1/endpoints/products.py` — append `where(Product.supplier == supplier)` when not None
- [X] T068 [US1] Add `price_list: int | None` and `salesperson: int | None` params to `list_customers` in `app/services/customer_service.py` and expose them in `GET /customers` handler in `app/api/v1/endpoints/customers.py`
- [X] T069 [P] [US1] Add `store: int | None` and `warehouse: int | None` params to `list_point_sales` in `app/services/point_sale_service.py` and expose them in `GET /points-of-sale` handler in `app/api/v1/endpoints/points_of_sale.py`
- [X] T070 [P] [US1] Add `store: int | None` param to `list_cash_drawers` in `app/services/cash_drawer_service.py` and expose it in `GET /cash-drawers` handler in `app/api/v1/endpoints/cash_drawers.py`
- [X] T071 [P] [US1] Add `employee: int | None` param (filters `VehicleOperator.driver`) to `list_vehicle_operators` in `app/services/vehicle_operator_service.py` and expose it in `GET /vehicle-operators` handler in `app/api/v1/endpoints/vehicle_operators.py`
- [X] T072 [US1] Write or extend test coverage for FK filter params — update `tests/api/test_products.py` (supplier filter), `tests/api/test_customers.py` (price_list + salesperson), and add `tests/api/test_stores.py` coverage for POS store/warehouse filter and cash-drawer store filter, and `tests/api/test_fleet.py` for employee filter on vehicle-operators; each test must cover: filter param passed (results narrowed via mock), filter omitted (all results returned)

**Checkpoint**: All 5 FK filters live; Scenarios 11–12 pass.

---

## Phase 11: SAT Catalog Read-Only Endpoints (US7 — Refinement)

**Goal**: Expose all 8 SAT reference catalogs as paginated read-only endpoints under `/api/v1/sat/`. Constitution v1.1.0 requires tests for new API endpoints.

**Independent Test**: `quickstart.md` Scenarios 13–14 (list, get-by-id 200/404, write attempt 405).

- [X] T073 [US7] Create `app/schemas/sat_catalog.py` — define `SatCatalogResponse(id: str)` and one `Annotated` alias per catalog mapping the correct PK field name to `id` (e.g. `SatCfdiUsageResponse`, `SatCountryResponse`, …, `SatUnitOfMeasurementResponse`)
- [X] T074 [US7] Create `app/services/sat_catalog_service.py` — implement `list_sat(db, model, pk_attr, skip, limit) → tuple[Sequence, int]` and `get_sat(db, model, pk_attr, id) → row | None`; define a `SAT_CATALOG_MAP: dict[str, tuple[type, InstrumentedAttribute]]` mapping URL slug → (model class, PK column) for all 8 catalogs
- [X] T075 [US7] Create `app/api/v1/endpoints/sat_catalogs.py` — for each of the 8 catalogs register `GET /{slug}` (paginated list, returns `ListResponse[SatCatalogResponse]`) and `GET /{slug}/{id}` (single record or 404); all handlers call `list_sat` / `get_sat` from the service; all require `get_current_user`
- [X] T076 [US7] Register `sat_catalogs` router in `app/api/v1/router.py` with prefix `/sat` and tag `sat-catalogs`
- [X] T077 [US7] Write `tests/api/test_sat_catalogs.py` — mock `sat_catalog_service.list_sat` and `get_sat`; cover: list 200 (returns items+total), get-by-id 200 (known code), get-by-id 404 (unknown code), list 401 (no auth), at least one catalog path (e.g. `units-of-measurement`)

**Checkpoint**: All SAT catalog endpoints live; Scenarios 13–14 pass; test suite green.

---

## Phase 12: Refinement Polish

- [X] T078 Run `uv run ruff check app/ tests/` and fix any new violations introduced by Phases 10–11
- [X] T079 Update `CHANGELOG.md` `[Unreleased]` section — add `Added` entries for FK filters (5 endpoints enhanced) and SAT catalog endpoints (8 new read-only resources under `/api/v1/sat/`)

---

## Phase 13: Multi-Label Product Search (US1 — Refinement)

**Goal**: Allow `GET /api/v1/products` to filter by more than one label at once, matching only
products that carry every requested label (AND/intersection semantics — see research.md Decision 12).

**Independent Test**: `GET /api/v1/products?label=2&label=5` returns only products carrying both
labels; `GET /api/v1/products?label=2` (single value) behaves exactly as before.

- [X] T080 [US1] Change the `label` param in `list_products` (`app/services/product_service.py`) from `int | None` to `list[int] | None`; when set, build the `product_label` subquery grouped by product with `HAVING COUNT(DISTINCT label) == len(labels)` instead of a plain equality filter
- [X] T081 [US1] Change `label: int | None = Query(None)` to `label: list[int] | None = Query(None)` in `GET /api/v1/products` handler (`app/api/v1/endpoints/products.py`)
- [X] T082 [US1] Extend `tests/api/test_products.py` label-filter coverage (mock-based, consistent with the existing `supplier` filter tests): single `?label=2` passed through as `[2]`, repeated `?label=2&label=5` passed through as `[2, 5]`, no `label` param passed through as `None`
- [X] T083 Update `CHANGELOG.md` `[Unreleased]` section — add an `Added`/`Changed` entry noting `label` on `GET /api/v1/products` now accepts multiple values with AND semantics

**Checkpoint**: Multi-label filter live; existing single-label behavior unchanged; test suite green.

---

## Phase 14: Product Label Facets (US1 — Refinement, GH #78)

**Goal**: Expose which labels co-occur with the products matching the current `GET /api/v1/products`
filters, so mbe-ui's faceted label filter drawer can grey out labels that would narrow the result
set to zero, without paging through every matching product client-side.

**Independent Test**: `GET /api/v1/products/labels/facets` (no filters) returns every label present
across the catalog with an accurate product count; `?label=2` restricts the response to label 2
(count = size of that result set) plus every label that still co-occurs with it.

- [X] T084 [US1] Extract the `search`/`label`/`deactivated`/`stockable`/`salable`/`purchasable`/`supplier` predicate-building block out of `list_products` into `_apply_product_filters(query, *, ...)` in `app/services/product_service.py`, reused by `list_products` unchanged
- [X] T085 [US1] Add `get_label_facets(db, *, search, label, deactivated, stockable, salable, purchasable, supplier)` to `app/services/product_service.py` — applies `_apply_product_filters` to a `select(Product.product_id)` subquery, then groups `product_label` rows for those products by `label` to return `(label_id, count)` rows
- [X] T086 [US1] Add `ProductLabelFacet` (`label_id: int, count: int`) to `app/schemas/product.py`; add `GET /products/labels/facets` handler to `app/api/v1/endpoints/products.py` accepting the same filter query params as `GET /products` (no `skip`/`limit`), gated by the same `PRODUCTS`/`READ` privilege, `response_model=list[ProductLabelFacet]`
- [X] T087 [US1] Add coverage in `tests/api/test_products.py` (200 response shape, filter pass-through, 401 without auth) and `tests/unit/test_product_service.py` (filter-builder SQL, facet query grouping/filtering)
- [X] T088 Update `CHANGELOG.md` `[Unreleased]` section — add an `Added` entry for `GET /api/v1/products/labels/facets`

**Checkpoint**: Facets endpoint live; `list_products` behavior unchanged (pure extraction); test
suite green.

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)
  └─► Phase 2 (Schemas) — depends on T001 (config), T002 (deps), T003 (ListResponse)
        └─► Phase 3 (Products + PriceLists) — schemas must exist first
        └─► Phase 4 (Customers + Labels + TaxpayerRecipients) — schemas must exist first
        └─► Phase 5 (Suppliers + Employees) — schemas must exist first
        └─► Phase 6 (Stores + Warehouses + POS + CashDrawers) — schemas must exist first
        └─► Phase 7 (ExchangeRates + Expenses + PMO) — schemas must exist first
        └─► Phase 8 (Vehicles + VehicleOperators + ProductionSites) — schemas must exist first
              └─► Phase 9 (Polish) — all resources must be implemented
```

### Within Phase 3 (Products — ordered)

```
T008 (list_products) → T009 (create_product) → T010 (get_product)
→ T011 (update_product) → T012 (delete_product) → T013 (merge_products)
→ T014 (products endpoint) → T015 (router)
T016 (price_list_service) can run in parallel with T008–T013
T017, T018 can run in parallel once T016 is done
```

### User Story Dependencies

- **US1+US2 (P1)**: Start after Phase 2 (Schemas) — implemented across Phases 3–8
- **US3 (P2)**: Already covered within the same service/endpoint files as US1 — no separate phase needed
- **US4 (P2)**: Already covered within the same service/endpoint files as US1 — no separate phase needed
- **US5 (P3)**: Already covered within the same service/endpoint files as US1 — no separate phase needed
- **US6 (P3)**: T013+T014 (merge) — depends on T008 (product service exists)
- **US1 refinement (FK filters)**: Phase 10 — depends on Phase 3–8 completion (services/endpoints must exist first)
- **US7 (SAT catalogs)**: Phase 11 — depends on T003 (ListResponse) and T073 (schemas); T073–T077 in order

---

## Parallel Opportunities

### After Phase 2 completes, all of these can run in parallel:

```
# Phase 3 batch:
"product_service.py list + create + get + update + delete" [T008-T012]
"price_list_service.py" [T016]

# Phase 4 batch (fully parallel with Phase 3):
"customer_service.py" [T019]
"label_service.py" [T022]
"taxpayer_recipient_service.py" [T025]

# Phase 5 batch:
"supplier_service.py" [T028]
"employee_service.py" [T031]

# Phase 6 batch:
"store_service.py" [T034]
"warehouse_service.py" [T037]
"point_sale_service.py" [T040]
"cash_drawer_service.py" [T043]

# Phase 7 batch:
"exchange_rate_service.py" [T046]
"expense_service.py" [T049]
"payment_method_option_service.py" [T052]

# Phase 8 batch:
"vehicle_service.py" [T055]
"vehicle_operator_service.py" [T058]
"production_site_service.py" [T061]
```

---

## Implementation Strategy

### MVP First (List + Create for Products and Price Lists Only)

1. Complete Phase 1 (T001–T003)
2. Complete T004 in Phase 2 (product schemas only)
3. Complete T008–T010, T016–T018 (product + price list list/create/get)
4. **Validate**: `quickstart.md` Scenarios 2–4
5. Ship and iterate

### Full Feature Incremental

1. Phase 1 → Phase 2 (setup + schemas, ~1 day)
2. Phase 3 (products + price lists — most complex, ~1 day)
3. Phases 4–8 in parallel (remaining 15 resources, ~1–2 days)
4. Phase 9 (polish, ~1 hour)

### Parallel Team (3 developers)

After Phase 1+2 complete:
- Dev A: Phase 3 (Products + PriceLists)
- Dev B: Phases 4+5 (Customer domain + Supplier + Employee)
- Dev C: Phases 6+7+8 (Store hierarchy + Financial + Fleet)

---

## Notes

- All `[P]` tasks modify different files — safe to run in parallel
- `stock_verification` ORM field is aliased to `stock_required` in schema; use `Field(alias="stock_required")` with `ConfigDict(populate_by_name=True)`
- `Expense.expense` column aliased to `name` in schema — use `Field(alias="expense")`
- `ProductPrice.price_list` maps to DB column `list` — handled by ORM mapping already
- `VehicleOperator.days_until_expiry` is a computed `@model_validator(mode="after")` field; not stored in DB
- Each router registration (`include_router`) in T015/T018/T021/etc. is a one-liner in `app/api/v1/router.py` — batch them if doing all at once, but each resource's router task lists it separately for traceability
