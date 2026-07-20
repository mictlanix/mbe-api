# Implementation Plan: Unified Entity Status

**Branch**: `005-unified-entity-status` | **Date**: 2026-07-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-unified-entity-status/spec.md`

## Summary

Replace every boolean lifecycle flag (`disabled`, `active`, `deactivated`, `enabled`) across 13
entities with one non-nullable integer `status` column backed by a shared
`EntityStatus(IntEnum)` (`ACTIVE=0, INACTIVE=1, ARCHIVED=2`) in `app/enums.py`. All request/
response schemas drop the legacy fields (hard breaking change), every API-exposed list endpoint
gains a `?status` filter, login rejects non-ACTIVE users, and a single Alembic migration (the
first in the repo) adds+backfills+drops columns per table. Fixes GitHub issues #80 and #81.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Alembic (all existing —
no new dependencies)

**Storage**: MariaDB via aiomysql — 13 tables altered by one Alembic migration (add `status`
SMALLINT NOT NULL DEFAULT 0, backfill, drop legacy columns). API exclusively owns the DB.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport` (existing pattern in `tests/api/`)

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — cross-cutting schema/model/service/endpoint change + first DB
migration

**Performance Goals**: N/A — no new hot paths; `?status` filters add one indexed-free equality
predicate to existing list queries (same cost as the boolean filters they replace)

**Constraints**:
- Breaking API change with no deprecation aliases (user-approved; mbe-ui updated separately).
- `EntityStatus` serialized as integer in JSON, matching the existing `CurrencyCode` IntEnum
  convention.
- DELETE endpoints keep hard-delete semantics; ARCHIVED is only client-settable.
- Migration must be reversible (downgrade restores legacy columns and polarity).

**Scale/Scope**: 1 new enum; 13 models (`user`, `customer`, `product`, `employee`, `facility`,
`warehouse`, `point_sale`, `cash_drawer`, `payment_method_option`, `vehicle`,
`vehicle_operator` + model-only `address`, `taxpayer_certificate`); ~34 schema fields across 4
schema files; 11 services; 11 endpoint modules; 1 migration; ~9 test files updated.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ | One shared enum, one field name, one migration. No status-transition rules, no soft-delete machinery, no per-entity enums. |
| II. Think Before Coding | ✅ | Enum values/polarity, filter scope, breaking-change policy, and DB ownership were decided with the user before spec (recorded in spec Assumptions). |
| III. Surgical Changes | ✅ | Every touched line replaces a legacy flag or adds the agreed `?status` filter. No adjacent refactors; the `int()` casts in warehouse service disappear only because the column they served is gone. |
| IV. Goal-Driven Execution | ✅ | Success = quickstart.md scenarios pass: pytest green, grep gate finds no legacy fields, `?status` filters verified per endpoint. |
| V. Reuse Over Rebuild | ✅ | Reuses `app/enums.py` IntEnum pattern (`CurrencyCode`), existing list/filter service pattern (`customer_service.list_customers`), existing test factories. New enum justified: no existing abstraction expresses lifecycle state. |
| VI. Async-First | ✅ | No sync DB access introduced; migration runs through the existing async-aware `migrations/env.py`. |
| VII. Security by Default | ✅ | No new endpoints; existing auth gates untouched. Login gate is preserved and broadened: any non-ACTIVE status is rejected. |
| VIII. Ruff Compliance | ✅ | `uv run ruff check app/ migrations/ tests/` gates completion. |

**Testing rule**: No brand-new endpoints, but endpoint contracts change materially — tests in
`tests/api/` are updated in the same tasks as their endpoints, plus new `?status` filter tests.

## Project Structure

### Documentation (this feature)

```text
specs/005-unified-entity-status/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── entity-status.md
└── tasks.md             # Phase 2 output (/speckit-tasks — not created by /speckit-plan)
```

### Source Code (repository root)

```text
app/
├── enums.py                     # ADD EntityStatus(IntEnum)
├── models/
│   ├── user.py                  # User.disabled → status
│   ├── customer.py              # Customer.disabled → status
│   ├── product.py               # Product.deactivated → status
│   ├── core.py                  # Address/Employee(2 flags)/Facility/Warehouse/PointSale/
│   │                            #   CashDrawer/PaymentMethodOption/Vehicle/VehicleOperator → status
│   └── fiscal.py                # TaxpayerCertificate.active → status
├── schemas/
│   ├── user.py                  # UserCreate/Update/ListItem/Response: disabled → status
│   ├── customer.py              # CustomerCreate/ListItem/Response: disabled → status
│   ├── product.py               # ProductUpdate/ListItem/Response: deactivated → status
│   └── core.py                  # Employee/Facility/Warehouse/PointSale/CashDrawer/
│                                #   PaymentMethodOption/Vehicle/VehicleOperator schemas → status
├── services/                    # replace flag set/copy + add status filter param:
│   ├── user_service.py          #   + authenticate_user: status != ACTIVE blocks login
│   ├── customer_service.py      #   disabled filter → status
│   ├── product_service.py       #   deactivated filter → status
│   ├── employee_service.py      #   active filter → status; drop disabled handling
│   ├── facility_service.py      # + status filter (new)
│   ├── warehouse_service.py     # + status filter (new); drop int() casts
│   ├── point_sale_service.py    # + status filter (new)
│   ├── cash_drawer_service.py   # + status filter (new)
│   ├── payment_method_option_service.py  # + status filter (new)
│   ├── vehicle_service.py       # + status filter (new)
│   └── vehicle_operator_service.py       # + status filter (new)
└── api/v1/endpoints/            # replace/add `status: EntityStatus | None = Query(None)`:
    ├── products.py customers.py employees.py       # replace legacy filter params
    └── users.py facilities.py warehouses.py points_of_sale.py cash_drawers.py
        payment_method_options.py vehicles.py vehicle_operators.py   # add filter

migrations/versions/
└── xxxx_unified_entity_status.py   # first revision: add/backfill/drop per 13 tables

tests/api/                       # update factories/assertions + new filter tests:
    test_auth.py test_products.py test_customers.py test_stores.py test_fleet.py
    test_suppliers_employees.py test_financial.py test_product_image.py
tests/unit/test_product_service.py
```

**Structure Decision**: Existing single-service layout (`app/{models,schemas,services,api}`).
No new modules — the enum joins the existing `app/enums.py`, and every other change lands in
files that already own the affected entity.

## Complexity Tracking

*No constitution violations — table not needed. The only "new abstraction" is the
`EntityStatus` enum itself, which is the feature's stated purpose.*
