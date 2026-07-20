# Tasks: Unified Entity Status

**Input**: Design documents from `specs/005-unified-entity-status/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/entity-status.md, quickstart.md

**Tests**: REQUIRED — endpoint contracts change materially; constitution mandates endpoint test
coverage in the same task as the endpoint change. Existing test files are updated (factories,
assertions) rather than duplicated; new tests cover `?status` filters, login gate, and 422s.

**Organization**: Foundational phase carries the enum + models + migration (every story needs
them). Stories then layer schemas/services/endpoints (US1), the employee collapse (US2), the
login gate (US4), and filters (US3).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

*No setup tasks — no new dependencies, structure, or tooling. Existing `uv` + ruff + pytest
stack applies unchanged.*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared enum, all model columns, and the migration — every user story reads or
writes `status`, so this must land first. Note: after this phase and until US1 completes, the
tree is intentionally mid-refactor (schemas/services still reference dropped model attributes);
phases 2–3 land together before the suite can run green.

- [X] T001 Add `EntityStatus(IntEnum)` (ACTIVE=0, INACTIVE=1, ARCHIVED=2) with short docstring to app/enums.py
- [X] T002 [P] Replace `User.disabled` with `status` column (pattern: `Mapped[EntityStatus] = mapped_column(Integer, default=EntityStatus.ACTIVE, server_default='0')`) in app/models/user.py
- [X] T003 [P] Replace `Customer.disabled` with `status` in app/models/customer.py
- [X] T004 [P] Replace `Product.deactivated` with `status` in app/models/product.py
- [X] T005 [P] Replace `TaxpayerCertificate.active` with `status` in app/models/fiscal.py
- [X] T006 Replace in app/models/core.py: `Address.disabled`, `Employee.active`+`Employee.disabled` (both → one `status`), `Facility.disabled`, `Warehouse.disabled` (SmallInteger), `PointSale.disabled`, `CashDrawer.disabled`, `PaymentMethodOption.enabled`, `Vehicle.active`, `VehicleOperator.active` → `status`
- [X] T007 Create first Alembic migration in migrations/versions/ (`down_revision = None`): per data-model.md tables — add `status` SMALLINT NOT NULL server_default '0', backfill via `UPDATE ... CASE` per polarity mapping, drop legacy column(s); reversible downgrade restoring legacy columns/polarity

**Checkpoint**: Models compile (`uv run python -c "import app.models"`); migration file passes ruff.

---

## Phase 3: User Story 1 — Uniform status field on every entity (Priority: P1) 🎯 MVP

**Goal**: Every API-exposed entity reads/writes lifecycle state via one `status` field; legacy
fields are gone from all schemas.

**Independent Test**: Create/fetch each entity type; responses carry `status` (0/1/2), no
legacy field names anywhere; create defaults to ACTIVE; invalid status → 422.

### Implementation for User Story 1

- [X] T008 [P] [US1] Replace `disabled` with `status: EntityStatus` fields (Create default ACTIVE, Update optional-None, Response/ListItem required) in UserCreate/UserUpdate/UserListItem/UserResponse in app/schemas/user.py
- [X] T009 [P] [US1] Replace `disabled` with `status` in CustomerCreate/CustomerListItem/CustomerResponse in app/schemas/customer.py
- [X] T010 [P] [US1] Replace `deactivated` with `status` in ProductUpdate/ProductListItem/ProductResponse (NOT ProductCreate) in app/schemas/product.py
- [X] T011 [US1] Replace legacy fields with `status` in Facility/Warehouse/PointSale/CashDrawer/PaymentMethodOption/Vehicle/VehicleOperator Create/Update/Summary/Response schemas in app/schemas/core.py (Employee handled in US2/T015)
- [X] T012 [P] [US1] Update set/copy logic from legacy flags to `status` in app/services/user_service.py, customer_service.py (create default → `EntityStatus.ACTIVE`), product_service.py (`deactivated=False` → `status=EntityStatus.ACTIVE`)
- [X] T013 [P] [US1] Update set/copy logic to `status` (drop `int()` casts in warehouse) in app/services/facility_service.py, warehouse_service.py, point_sale_service.py, cash_drawer_service.py, payment_method_option_service.py, vehicle_service.py, vehicle_operator_service.py; check app/services/fk_expansion.py for legacy field references
- [X] T014 [US1] Update existing tests' factories/assertions from legacy fields to `status` in tests/api/test_products.py, test_customers.py, test_stores.py, test_fleet.py, test_financial.py, test_product_image.py, tests/unit/test_product_service.py; add per-entity assertions: `status` present, legacy field absent, create-defaults-to-ACTIVE, and one 422 for `status: 5`

**Checkpoint**: Full suite green except employee/auth/filter areas (US2/US4/US3).

---

## Phase 4: User Story 2 — Employee's duplicate flags collapse into one (Priority: P2)

**Goal**: Employee exposes exactly one lifecycle field; issue #80 resolved.

**Independent Test**: Employee responses carry only `status`; migration maps
`active`/`disabled` combinations per data-model.md.

### Implementation for User Story 2

- [X] T015 [US2] Replace `active` + `disabled` with single `status` in EmployeeCreate/EmployeeUpdate/EmployeeResponse in app/schemas/core.py
- [X] T016 [US2] Update app/services/employee_service.py: create sets `status` (drop `disabled=False`), update copies `status` only
- [X] T017 [US2] Update employee factories/assertions to single `status` field in tests/api/test_suppliers_employees.py; assert neither `active` nor `disabled` appears in responses

**Checkpoint**: Employee endpoints green with a single lifecycle field.

---

## Phase 5: User Story 4 — Non-active users cannot sign in (Priority: P2)

**Goal**: Login rejected unless user status is ACTIVE (preserves disabled-user rejection).

**Independent Test**: Login succeeds for ACTIVE user, rejected for INACTIVE and ARCHIVED with
the same error as today's disabled user.

### Implementation for User Story 4

- [X] T018 [US4] Change `authenticate_user` gate from `user.disabled` to `user.status != EntityStatus.ACTIVE` in app/services/user_service.py
- [X] T019 [US4] Update tests/api/test_auth.py: `_make_user` factory `disabled` → `status`, existing disabled-login test → status=INACTIVE, add ARCHIVED rejection case, `/auth/me` body asserts `status`

**Checkpoint**: Auth suite green; INACTIVE and ARCHIVED both blocked.

---

## Phase 6: User Story 3 — Filter any entity list by status (Priority: P3)

**Goal**: Identical `?status` filter on all 11 exposed list endpoints; legacy filter params gone.

**Independent Test**: `?status=0` / `?status=1` return only matching entities (totals reflect
filter); omitted param returns all; `?status=9` → 422.

### Implementation for User Story 3

- [X] T020 [P] [US3] Replace `?deactivated`/`?disabled`/`?active` with `status: EntityStatus | None = Query(None)` in app/api/v1/endpoints/products.py (both list variants), customers.py, employees.py and thread through app/services/product_service.py, customer_service.py, employee_service.py (filter on page + count queries)
- [X] T021 [P] [US3] Add `status: EntityStatus | None = Query(None)` filter to list endpoints in app/api/v1/endpoints/users.py, facilities.py, warehouses.py, points_of_sale.py, cash_drawers.py, payment_method_options.py, vehicles.py, vehicle_operators.py and their services (mirror customer_service filter pattern)
- [X] T022 [US3] Add filter tests (match, no-param, 422) per endpoint in tests/api/test_products.py, test_customers.py, test_suppliers_employees.py, test_auth.py (users list if covered there), test_stores.py, test_fleet.py, test_financial.py

**Checkpoint**: All list endpoints filter uniformly; full suite green.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T023 Run quickstart.md static gates: ruff zero violations; grep gate shows no legacy lifecycle fields in app/; OpenAPI contains `status` and none of the legacy field names
- [X] T024 Update CHANGELOG.md `[Unreleased]`: Changed (unified status field, filter params), Removed (legacy fields), Added (EntityStatus, migration) — note breaking change and issues #80/#81

---

## Dependencies & Execution Order

- **Phase 2 first** (T001 → T002–T006 [P] → T007). T006 before T011/T015 (same entities).
- **US1 (Phase 3)** unblocks everything client-visible; T008–T010 and T012–T013 parallelize
  (different files); T011 before T014's facility/warehouse assertions. The suite is only fully
  runnable after Phases 2+3 land (mid-refactor tree in between).
- **US2, US4** independent of each other; both need Phase 2 (+T008 for US4's factory overlap in
  test_auth.py; +T011's schema shape for employee response embedding — verify at T015).
- **US3 last** — filters need final schemas/services from US1/US2.
- **Polish** after all stories.

### Parallel opportunities

- T002–T005 (distinct model files); T008–T010 (distinct schema files); T012 vs T013 (distinct
  service files); T020 vs T021 (distinct endpoint files).

## Implementation Strategy

Phases 2+3 form the MVP and must land together (the tree doesn't run mid-refactor: schemas
reference model attributes directly). Then US2 → US4 → US3 in order, validating each
checkpoint, then polish. Single-developer flow; parallel markers guide agent fan-out, not team
assignment.
