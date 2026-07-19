#!/usr/bin/env bash
# Creates GitHub issues for all 66 tasks in the Master Data REST Endpoints feature.
# Prerequisites: gh CLI installed and authenticated (gh auth login)
# Usage: bash specs/002-master-data-endpoints/create-issues.sh

set -euo pipefail

REPO="mictlanix/mbe-api"
FEATURE="002-master-data-endpoints"
SPEC_DIR="specs/$FEATURE"

echo "Creating issues for feature: $FEATURE in $REPO"
echo "---"

create_issue() {
  local title="$1"
  local body="$2"
  local label="$3"
  local url
  url=$(gh issue create --repo "$REPO" --title "$title" --body "$body" --label "$label" 2>/dev/null \
        || gh issue create --repo "$REPO" --title "$title" --body "$body")
  echo "Created: $url"
}

# ── Phase 1: Setup ──────────────────────────────────────────────────────────

create_issue \
  "T001 Add product-creation config defaults to Settings" \
  "**Phase**: 1 — Setup
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)
**Plan**: [$SPEC_DIR/plan.md]($SPEC_DIR/plan.md)

## Task

Add \`default_vat\`, \`is_tax_included\`, \`default_price_type\`, \`default_photo_file\`, \`default_customer_id\` fields to the \`Settings\` class.

**File**: \`app/core/config.py\`

## Acceptance

- [ ] Five new optional fields added to \`Settings\` with sensible defaults (16% VAT, tax not included, price_type=0, photo='no-image.png', customer_id=1)
- [ ] Fields are overridable via environment variables
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T002 Add require_privilege dependency to deps.py" \
  "**Phase**: 1 — Setup
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`require_privilege(system_object: SystemObject, access_right: AccessRight)\` dependency function that reads the caller's \`AccessPrivilege\` row and raises \`403 Forbidden\` if the bit is not set.

**File**: \`app/core/deps.py\`

## Acceptance

- [ ] Function signature: \`def require_privilege(system_object: SystemObject, access_right: AccessRight) -> Callable\`
- [ ] Returns a FastAPI dependency that injects \`CurrentUser\` and checks the privilege bit
- [ ] Raises \`HTTP 403\` when bit not set
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T003 Add ListResponse generic Pydantic model" \
  "**Phase**: 1 — Setup
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`ListResponse[T]\` generic Pydantic model (\`items: list[T], total: int\`) shared by all 17 list endpoints.

**File**: \`app/schemas/__init__.py\`

## Acceptance

- [ ] Generic model defined once, importable from \`app.schemas\`
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

# ── Phase 2: Schema Definitions ─────────────────────────────────────────────

create_issue \
  "T004 Create product schemas (Product, PriceList, ProductPrice)" \
  "**Phase**: 2 — Schema Definitions
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)
**Contracts**: [$SPEC_DIR/contracts/api.md]($SPEC_DIR/contracts/api.md)

## Task

Create \`app/schemas/product.py\` with:
- \`ProductCreate\`, \`ProductUpdate\`, \`ProductListItem\`, \`ProductResponse\`
- \`ProductPriceResponse\`
- \`PriceListCreate\`, \`PriceListUpdate\`, \`PriceListResponse\`
- \`ProductMergeRequest\`

**Key**: \`stock_verification\` ORM field exposed as \`stock_required\` via Pydantic alias (\`ConfigDict(populate_by_name=True)\`). Barcode validated: empty or exactly 13 digits. Code: 1–25 chars, no whitespace.

**File**: \`app/schemas/product.py\`

## Acceptance

- [ ] All schema classes defined per contracts/api.md
- [ ] \`stock_required\` alias works in both serialization and deserialization
- [ ] Bar code validator rejects anything that is not empty and not 13 digits
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T005 Create customer schemas (Customer, TaxpayerRecipient)" \
  "**Phase**: 2 — Schema Definitions
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)
**Contracts**: [$SPEC_DIR/contracts/api.md]($SPEC_DIR/contracts/api.md)

## Task

Create \`app/schemas/customer.py\` with:
- \`CustomerCreate\`, \`CustomerUpdate\`, \`CustomerListItem\`, \`CustomerResponse\`
- \`TaxpayerRecipientCreate\`, \`TaxpayerRecipientUpdate\`, \`TaxpayerRecipientResponse\`

**File**: \`app/schemas/customer.py\`

## Acceptance

- [ ] All schema classes defined per contracts/api.md
- [ ] RFC field (\`taxpayer_recipient_id\`) validated to 12–13 chars
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T006 Create supplier schemas" \
  "**Phase**: 2 — Schema Definitions
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/schemas/supplier.py\` with \`SupplierCreate\`, \`SupplierUpdate\`, \`SupplierResponse\`.

**File**: \`app/schemas/supplier.py\`

## Acceptance

- [ ] All schema classes defined per contracts/api.md
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T007 Create core schemas (Employee, Facility, Warehouse, PointSale, CashDrawer, Label, ExchangeRate, Expense, PaymentMethodOption, Vehicle, VehicleOperator)" \
  "**Phase**: 2 — Schema Definitions
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)
**Contracts**: [$SPEC_DIR/contracts/api.md]($SPEC_DIR/contracts/api.md)

## Task

Create \`app/schemas/core.py\` with Create/Update/Response schemas for all 12 remaining resources.

**Key notes**:
- \`VehicleOperatorResponse\` must include computed \`days_until_expiry: int\` via \`@model_validator(mode='after')\` (negative = expired)
- \`ExpenseCreate/Response\` must alias \`Expense.expense\` column to \`name\` field
- \`WarehouseResponse.disabled\` is \`int | None\` (SmallInteger in DB) — normalize to \`bool | None\` in schema

**File**: \`app/schemas/core.py\`

## Acceptance

- [ ] All 12 resource schema sets defined per contracts/api.md
- [ ] \`days_until_expiry\` is computed correctly (positive = days remaining, negative = already expired)
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

# ── Phase 3: Products & Price Lists ─────────────────────────────────────────

create_issue \
  "T008 [US1] Implement list_products service function" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US1 — Browse and Search Catalog Entities
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`list_products(db, *, search, label, deactivated, stockable, salable, purchasable, skip, limit)\` to \`app/services/product_service.py\` returning \`(Sequence[Product], int)\`.

**File**: \`app/services/product_service.py\`

## Acceptance

- [ ] Search filters on code, name, model, sku, brand (case-insensitive ilike)
- [ ] Boolean filters (deactivated, stockable, salable, purchasable) applied when provided
- [ ] Label filter joins through product_label junction table
- [ ] Returns total count alongside paginated results
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T009 [US2] Implement create_product service function with auto-defaults" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US2 — Create Master Data Records
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`create_product(db, data, settings)\` to \`app/services/product_service.py\`.

Auto-defaults applied on create:
- \`min_order_qty = 1\`
- \`stock_verification = True\` (alias \`stock_required\`)
- \`tax_rate = settings.default_vat\`
- \`tax_included = settings.is_tax_included\`
- \`price_type = settings.default_price_type\`
- \`photo = settings.default_photo_file\`
- Creates one \`ProductPrice\` row (price=0, low_profit=0, high_profit=0) per existing \`PriceList\` in the same transaction

**File**: \`app/services/product_service.py\`

## Acceptance

- [ ] All auto-defaults applied
- [ ] ProductPrice rows created atomically with the product
- [ ] Raises 409 if code already exists
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T010 [US3] Implement get_product service function" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US3 — Retrieve a Single Master Data Record
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`get_product(db, product_id)\` to \`app/services/product_service.py\` returning \`Product | None\` with associated \`ProductPrice\` rows loaded via explicit select.

**File**: \`app/services/product_service.py\`

## Acceptance

- [ ] Returns None when product not found
- [ ] Prices list populated in response
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T011 [US4] Implement update_product service function" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US4 — Update Master Data Records
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`update_product(db, product, data)\` to \`app/services/product_service.py\`. Applies partial updates (only non-None fields from \`ProductUpdate\`).

**File**: \`app/services/product_service.py\`

## Acceptance

- [ ] Partial updates supported (PATCH semantics via PUT)
- [ ] Code uniqueness re-checked if code changes
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T012 [US5] Implement delete_product service function" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US5 — Delete Master Data Records
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`delete_product(db, product)\` to \`app/services/product_service.py\`. Deletes all \`ProductPrice\` rows first, then deletes the product.

**File**: \`app/services/product_service.py\`

## Acceptance

- [ ] ProductPrice rows deleted before product
- [ ] Atomic transaction (both deletes or neither)
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T013 [US6] Implement merge_products service function" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US6 — Merge Duplicate Products
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`merge_products(db, product_id, duplicate_id)\` to \`app/services/product_service.py\`.

Remaps FK references from duplicate → canonical across tables:
\`sales_order_detail\`, \`purchase_order_detail\`, \`inventory_receipt_detail\`, \`inventory_issue_detail\`, \`inventory_transfer_detail\`, \`product_price\`, \`lot_serial_tracking\`, \`product_label\`

Then hard-deletes the duplicate.

**File**: \`app/services/product_service.py\`

## Acceptance

- [ ] All 8 tables remapped via ORM \`update()\` statements
- [ ] Duplicate deleted after all remaps
- [ ] Raises 400 if product_id == duplicate_id
- [ ] Entire operation is atomic
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T014 [US1] Create products endpoint (full CRUD + merge)" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US1–US6
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)
**Contracts**: [$SPEC_DIR/contracts/api.md]($SPEC_DIR/contracts/api.md)

## Task

Create \`app/api/v1/endpoints/products.py\` wiring all product handlers:
- \`GET /\` → list (T008)
- \`POST /\` → create (T009)
- \`GET /{product_id}\` → get (T010)
- \`PUT /{product_id}\` → update (T011)
- \`DELETE /{product_id}\` → delete (T012)
- \`POST /merge\` → merge (T013) — protected by \`require_privilege(SystemObject.PRODUCTS_MERGE, AccessRight.CREATE)\`

All routes require \`get_current_user\`.

**File**: \`app/api/v1/endpoints/products.py\`

## Acceptance

- [ ] All 6 routes wired and returning correct HTTP status codes
- [ ] Unauthenticated requests return 401
- [ ] Merge endpoint returns 403 without PRODUCTS_MERGE privilege
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T015 [US1] Register products router in router.py" \
  "**Phase**: 3 — Products & Price Lists
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Add \`include_router(products.router, prefix='/products', tags=['products'])\` to \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`

## Acceptance

- [ ] Route appears in \`GET /api/v1/openapi.json\`
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T016 [US1] Create price_list_service.py" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/price_list_service.py\` with \`list_price_lists\`, \`get_price_list\`, \`create_price_list\`, \`update_price_list\`, \`delete_price_list\`.

\`delete_price_list\` must raise \`409 Conflict\` if any \`Customer.price_list\` references this record.

**File**: \`app/services/price_list_service.py\`

## Acceptance

- [ ] All 5 CRUD functions implemented
- [ ] Delete raises 409 when price list is in use by a customer
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T017 [US1] Create price_lists endpoint" \
  "**Phase**: 3 — Products & Price Lists
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/price_lists.py\` with full CRUD for \`/price-lists\`.

**File**: \`app/api/v1/endpoints/price_lists.py\`

## Acceptance

- [ ] GET / POST / GET {id} / PUT {id} / DELETE {id} all wired
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T018 [US1] Register price_lists router in router.py" \
  "**Phase**: 3 — Products & Price Lists
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`price_lists\` router with prefix \`/price-lists\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`

## Acceptance

- [ ] Route appears in OpenAPI spec
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

# ── Phase 4: Customers, Labels, Taxpayer Recipients ──────────────────────────

create_issue \
  "T019 [US1] Create customer_service.py" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/customer_service.py\` with:
- \`list_customers(db, *, search, disabled, skip, limit)\` — search by code/name/zone
- \`get_customer\`, \`create_customer\`, \`update_customer\`
- \`delete_customer\` — raises \`409\` if \`customer_id == settings.default_customer_id\`

**File**: \`app/services/customer_service.py\`

## Acceptance

- [ ] Search filters on code, name, zone
- [ ] Delete rejects default customer with 409
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T020 [US1] Create customers endpoint" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/customers.py\` with full CRUD for \`/customers\`.

**File**: \`app/api/v1/endpoints/customers.py\`

## Acceptance

- [ ] All 5 routes wired
- [ ] \`disabled\` filter query param supported
- [ ] \`uv run ruff check app/\` passes" \
  "enhancement"

create_issue \
  "T021 [US1] Register customers router in router.py" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`customers\` router with prefix \`/customers\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T022 [US1] Create label_service.py" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/label_service.py\` with \`list_labels\`, \`get_label\`, \`create_label\`, \`update_label\`, \`delete_label\`.

**File**: \`app/services/label_service.py\`" \
  "enhancement"

create_issue \
  "T023 [US1] Create labels endpoint" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/labels.py\` with full CRUD for \`/labels\`.

**File**: \`app/api/v1/endpoints/labels.py\`" \
  "enhancement"

create_issue \
  "T024 [US1] Register labels router in router.py" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`labels\` router with prefix \`/labels\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T025 [US1] Create taxpayer_recipient_service.py" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/taxpayer_recipient_service.py\` with:
- \`list_taxpayer_recipients(db, *, search, skip, limit)\` — search by RFC and name
- \`get_taxpayer_recipient(db, rfc: str)\`
- \`create_taxpayer_recipient\`, \`update_taxpayer_recipient\`, \`delete_taxpayer_recipient\`

RFC PK is a string (12–13 chars). Uniqueness enforced by DB PK.

**File**: \`app/services/taxpayer_recipient_service.py\`" \
  "enhancement"

create_issue \
  "T026 [US1] Create taxpayer_recipients endpoint" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/taxpayer_recipients.py\` with full CRUD for \`/taxpayer-recipients\`. Path param for get/put/delete is the RFC string.

**File**: \`app/api/v1/endpoints/taxpayer_recipients.py\`" \
  "enhancement"

create_issue \
  "T027 [US1] Register taxpayer_recipients router in router.py" \
  "**Phase**: 4 — Customers, Labels & Taxpayer Recipients
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`taxpayer_recipients\` router with prefix \`/taxpayer-recipients\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

# ── Phase 5: Suppliers & Employees ──────────────────────────────────────────

create_issue \
  "T028 [US1] Create supplier_service.py" \
  "**Phase**: 5 — Suppliers & Employees
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/supplier_service.py\` with full CRUD. \`list_suppliers\` searches by code/name/zone.

**File**: \`app/services/supplier_service.py\`" \
  "enhancement"

create_issue \
  "T029 [US1] Create suppliers endpoint" \
  "**Phase**: 5 — Suppliers & Employees
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/suppliers.py\` with full CRUD for \`/suppliers\`.

**File**: \`app/api/v1/endpoints/suppliers.py\`" \
  "enhancement"

create_issue \
  "T030 [US1] Register suppliers router in router.py" \
  "**Phase**: 5 — Suppliers & Employees
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`suppliers\` router with prefix \`/suppliers\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T031 [US1] Create employee_service.py" \
  "**Phase**: 5 — Suppliers & Employees
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/employee_service.py\` with full CRUD. \`list_employees\` supports search by first_name/last_name/nickname and filters by \`active\`, \`sales_person\`.

**File**: \`app/services/employee_service.py\`" \
  "enhancement"

create_issue \
  "T032 [US1] Create employees endpoint" \
  "**Phase**: 5 — Suppliers & Employees
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/employees.py\` with full CRUD for \`/employees\`. Expose \`active\` and \`sales_person\` as boolean filter query params.

**File**: \`app/api/v1/endpoints/employees.py\`" \
  "enhancement"

create_issue \
  "T033 [US1] Register employees router in router.py" \
  "**Phase**: 5 — Suppliers & Employees
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`employees\` router with prefix \`/employees\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

# ── Phase 6: Facilities, Warehouses, POS, Cash Drawers ──────────────────────

create_issue \
  "T034 [US1] Create facility_service.py" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/facility_service.py\` with \`list_facilities\`, \`get_facility\`, \`create_facility\`, \`update_facility\`, \`delete_facility\`.

**File**: \`app/services/facility_service.py\`" \
  "enhancement"

create_issue \
  "T035 [US1] Create facilities endpoint" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/facilities.py\` with full CRUD for \`/facilities\`.

**File**: \`app/api/v1/endpoints/facilities.py\`" \
  "enhancement"

create_issue \
  "T036 [US1] Register facilities router in router.py" \
  "**Phase**: 6 — Facility Hierarchy
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`facilities\` router with prefix \`/facilities\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T037 [US1] Create warehouse_service.py" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/warehouse_service.py\` with full CRUD. \`list_warehouses\` accepts optional \`facility\` (int) filter.

**File**: \`app/services/warehouse_service.py\`" \
  "enhancement"

create_issue \
  "T038 [US1] Create warehouses endpoint" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/warehouses.py\` with full CRUD for \`/warehouses\`. Expose optional \`facility\` filter query param.

**File**: \`app/api/v1/endpoints/warehouses.py\`" \
  "enhancement"

create_issue \
  "T039 [US1] Register warehouses router in router.py" \
  "**Phase**: 6 — Facility Hierarchy
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`warehouses\` router with prefix \`/warehouses\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T040 [US1] Create point_sale_service.py" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/point_sale_service.py\` with full CRUD for PointSale records.

**File**: \`app/services/point_sale_service.py\`" \
  "enhancement"

create_issue \
  "T041 [US1] Create points_of_sale endpoint" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/points_of_sale.py\` with full CRUD for \`/points-of-sale\`.

**File**: \`app/api/v1/endpoints/points_of_sale.py\`" \
  "enhancement"

create_issue \
  "T042 [US1] Register points_of_sale router in router.py" \
  "**Phase**: 6 — Facility Hierarchy
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`points_of_sale\` router with prefix \`/points-of-sale\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T043 [US1] Create cash_drawer_service.py" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/cash_drawer_service.py\` with full CRUD for CashDrawer records.

**File**: \`app/services/cash_drawer_service.py\`" \
  "enhancement"

create_issue \
  "T044 [US1] Create cash_drawers endpoint" \
  "**Phase**: 6 — Facility Hierarchy
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/cash_drawers.py\` with full CRUD for \`/cash-drawers\`.

**File**: \`app/api/v1/endpoints/cash_drawers.py\`" \
  "enhancement"

create_issue \
  "T045 [US1] Register cash_drawers router in router.py" \
  "**Phase**: 6 — Facility Hierarchy
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`cash_drawers\` router with prefix \`/cash-drawers\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

# ── Phase 7: Exchange Rates, Expenses, Payment Method Options ───────────────

create_issue \
  "T046 [US1] Create exchange_rate_service.py" \
  "**Phase**: 7 — Financial Catalogs
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/exchange_rate_service.py\` with:
- \`list_exchange_rates(db, *, date_from, date_to, base, target, skip, limit)\`
- \`get_exchange_rate\`, \`create_exchange_rate\` (raises 409 on duplicate (date, base, target)), \`update_exchange_rate\`, \`delete_exchange_rate\`

**File**: \`app/services/exchange_rate_service.py\`" \
  "enhancement"

create_issue \
  "T047 [US1] Create exchange_rates endpoint" \
  "**Phase**: 7 — Financial Catalogs
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/exchange_rates.py\` with full CRUD for \`/exchange-rates\`. Expose \`date_from\`, \`date_to\`, \`base\`, \`target\` as optional query params on the list endpoint.

**File**: \`app/api/v1/endpoints/exchange_rates.py\`" \
  "enhancement"

create_issue \
  "T048 [US1] Register exchange_rates router in router.py" \
  "**Phase**: 7 — Financial Catalogs
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`exchange_rates\` router with prefix \`/exchange-rates\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T049 [US1] Create expense_service.py" \
  "**Phase**: 7 — Financial Catalogs
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/expense_service.py\` with full CRUD. Note: the \`Expense.expense\` column maps to \`name\` in the schema layer.

**File**: \`app/services/expense_service.py\`" \
  "enhancement"

create_issue \
  "T050 [US1] Create expenses endpoint" \
  "**Phase**: 7 — Financial Catalogs
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/expenses.py\` with full CRUD for \`/expenses\`.

**File**: \`app/api/v1/endpoints/expenses.py\`" \
  "enhancement"

create_issue \
  "T051 [US1] Register expenses router in router.py" \
  "**Phase**: 7 — Financial Catalogs
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`expenses\` router with prefix \`/expenses\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T052 [US1] Create payment_method_option_service.py" \
  "**Phase**: 7 — Financial Catalogs
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/payment_method_option_service.py\` with full CRUD. \`list_payment_method_options\` accepts optional \`facility\` (int) filter.

**File**: \`app/services/payment_method_option_service.py\`" \
  "enhancement"

create_issue \
  "T053 [US1] Create payment_method_options endpoint" \
  "**Phase**: 7 — Financial Catalogs
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/payment_method_options.py\` with full CRUD for \`/payment-method-options\`.

**File**: \`app/api/v1/endpoints/payment_method_options.py\`" \
  "enhancement"

create_issue \
  "T054 [US1] Register payment_method_options router in router.py" \
  "**Phase**: 7 — Financial Catalogs
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`payment_method_options\` router with prefix \`/payment-method-options\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

# ── Phase 8: Vehicles, Vehicle Operators ────────────────────────────────────

create_issue \
  "T055 [US1] Create vehicle_service.py" \
  "**Phase**: 8 — Fleet & Manufacturing
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/vehicle_service.py\` with full CRUD for Vehicle records.

**File**: \`app/services/vehicle_service.py\`" \
  "enhancement"

create_issue \
  "T056 [US1] Create vehicles endpoint" \
  "**Phase**: 8 — Fleet & Manufacturing
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/vehicles.py\` with full CRUD for \`/vehicles\`.

**File**: \`app/api/v1/endpoints/vehicles.py\`" \
  "enhancement"

create_issue \
  "T057 [US1] Register vehicles router in router.py" \
  "**Phase**: 8 — Fleet & Manufacturing
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`vehicles\` router with prefix \`/vehicles\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

create_issue \
  "T058 [US1] Create vehicle_operator_service.py" \
  "**Phase**: 8 — Fleet & Manufacturing
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/services/vehicle_operator_service.py\` with full CRUD. Set \`creation_time\` and \`modification_time\` to \`datetime.utcnow()\` on create; update \`modification_time\` on update.

**File**: \`app/services/vehicle_operator_service.py\`" \
  "enhancement"

create_issue \
  "T059 [US1] Create vehicle_operators endpoint" \
  "**Phase**: 8 — Fleet & Manufacturing
**Story**: US1–US5
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Create \`app/api/v1/endpoints/vehicle_operators.py\` with full CRUD for \`/vehicle-operators\`. Response includes \`days_until_expiry\` computed by \`VehicleOperatorResponse\` model validator.

**File**: \`app/api/v1/endpoints/vehicle_operators.py\`" \
  "enhancement"

create_issue \
  "T060 [US1] Register vehicle_operators router in router.py" \
  "**Phase**: 8 — Fleet & Manufacturing
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Register \`vehicle_operators\` router with prefix \`/vehicle-operators\` in \`app/api/v1/router.py\`.

**File**: \`app/api/v1/router.py\`" \
  "enhancement"

# Production sites are no longer a separate resource — they are `facility`
# rows with `type=1` (PRODUCTION_SITE). T061–T063 (production_site_service,
# production_sites endpoint, router registration) were removed.

# ── Phase 9: Polish ──────────────────────────────────────────────────────────

create_issue \
  "T064 Run ruff check and fix all violations" \
  "**Phase**: 9 — Polish
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Run \`uv run ruff check app/\` and fix all violations (E, F, I, UP rules; 100-char line limit).

\`\`\`bash
uv run ruff check app/ --fix
\`\`\`

## Acceptance

- [ ] Zero ruff violations across all new files" \
  "enhancement"

create_issue \
  "T065 Update CHANGELOG.md for all 17 new resource endpoints" \
  "**Phase**: 9 — Polish
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)

## Task

Update \`CHANGELOG.md\` \`[Unreleased]\` section with an \`Added\` entry listing all 17 new REST resource endpoints.

**File**: \`CHANGELOG.md\`

## Acceptance

- [ ] All 17 resources listed under \`Added\`
- [ ] Format follows Keep a Changelog conventions" \
  "documentation"

create_issue \
  "T066 Run quickstart.md validation scenarios end-to-end" \
  "**Phase**: 9 — Polish
**Feature**: [$FEATURE]($SPEC_DIR/spec.md)
**Quickstart**: [$SPEC_DIR/quickstart.md]($SPEC_DIR/quickstart.md)

## Task

Run all 10 scenarios from \`specs/002-master-data-endpoints/quickstart.md\` and confirm every expected HTTP status code matches.

## Acceptance

- [ ] Scenario 1: unauthenticated → 401
- [ ] Scenario 2: price list CRUD → 201, list count ≥ 1
- [ ] Scenario 3: product create → min_order_qty=1, stock_required=true, prices_count ≥ 1
- [ ] Scenario 4: duplicate code → 409
- [ ] Scenario 5: invalid barcode → 422
- [ ] Scenario 6: label round-trip → 201, 200, 200, 204, 404
- [ ] Scenario 7: default customer delete → 409
- [ ] Scenario 8: product merge → 204, duplicate → 404
- [ ] Scenario 9: expired operator → negative days_until_expiry
- [ ] Scenario 10: ruff passes" \
  "enhancement"

echo ""
echo "✅ All 66 issues created in $REPO"
