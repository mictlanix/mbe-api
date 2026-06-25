# Implementation Plan: Master Data REST Endpoints

**Branch**: `002-master-data-endpoints` | **Date**: 2026-06-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-master-data-endpoints/spec.md`

## Summary

Expose 17 master data catalog resources (products, price lists, customers, suppliers, employees,
stores, warehouses, points of sale, cash drawers, labels, taxpayer recipients, exchange rates,
expenses, payment method options, vehicles, vehicle operators, production sites) as authenticated
REST endpoints under `/api/v1/`. Also expose 8 SAT catalog reference tables as read-only
endpoints under `/api/v1/sat/`. FK filters are added to 5 list endpoints (products by supplier,
customers by price_list/salesperson, points-of-sale by store/warehouse, cash-drawers by store,
vehicle-operators by employee). All ORM models already exist; the work is schema definitions,
service functions, route handlers, and router registration.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI (ASGI), SQLAlchemy 2.0 async (`Mapped`/`mapped_column`),
Pydantic v2, aiomysql (MariaDB)

**Storage**: MariaDB via async SQLAlchemy sessions

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport` (optional per constitution)

**Target Platform**: Linux server

**Project Type**: REST API (web-service)

**Performance Goals**: List endpoints respond in under 500 ms under normal load (SC-001)

**Constraints**: All endpoints authenticated; product creation must be atomic (SC-005)

**Scale/Scope**: 17 resources × 5 CRUD operations + 8 SAT catalogs × 2 read-only handlers + 5 FK filter additions = ~103 new route handlers

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ PASS | One schema file per model group, one service file per resource, one endpoint file per resource — minimum viable abstraction. No generics or base-class magic. |
| II. Think Before Coding | ✅ PASS | All model discrepancies and config gaps resolved in research.md before writing code. |
| III. Surgical Changes | ✅ PASS | Models are untouched. Only `app/core/config.py` (new fields), `app/api/v1/router.py` (new includes), and new files are changed. |
| IV. Goal-Driven Execution | ✅ PASS | Each task in tasks.md has a verify step. |
| V. Reuse Over Rebuild | ✅ PASS | All 17 SQLAlchemy models reused as-is. `get_current_user` dependency reused. Existing service pattern (list/get/create/update/delete) repeated. |
| VI. Async-First | ✅ PASS | All handlers are `async def`, all DB calls use `AsyncSession` + `await`. |
| VII. Security by Default | ✅ PASS | All endpoints gated by `get_current_user`. Merge endpoint additionally checks `PRODUCTS_MERGE` privilege. |
| VIII. Ruff Compliance | ✅ PASS | All new code linted before commit. |

## Project Structure

### Documentation (this feature)

```text
specs/002-master-data-endpoints/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions and findings
├── data-model.md        # Phase 1 — entity reference
├── quickstart.md        # Phase 1 — validation guide
├── contracts/
│   └── api.md           # Phase 1 — endpoint contracts
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code

```text
app/
├── core/
│   └── config.py                        # ADD: 5 new settings fields
├── api/
│   └── v1/
│       ├── router.py                    # ADD: 17 new router.include_router() calls
│       └── endpoints/
│           ├── products.py              # NEW: Product CRUD + merge
│           ├── price_lists.py           # NEW: PriceList CRUD
│           ├── customers.py             # NEW: Customer CRUD
│           ├── labels.py                # NEW: Label CRUD
│           ├── taxpayer_recipients.py   # NEW: TaxpayerRecipient CRUD
│           ├── suppliers.py             # NEW: Supplier CRUD
│           ├── employees.py             # NEW: Employee CRUD
│           ├── warehouses.py            # NEW: Warehouse CRUD
│           ├── points_of_sale.py        # NEW: PointSale CRUD
│           ├── cash_drawers.py          # NEW: CashDrawer CRUD
│           ├── stores.py                # NEW: Store CRUD
│           ├── exchange_rates.py        # NEW: ExchangeRate CRUD
│           ├── expenses.py              # NEW: Expense CRUD
│           ├── payment_method_options.py # NEW: PaymentMethodOption CRUD
│           ├── vehicles.py              # NEW: Vehicle CRUD
│           ├── vehicle_operators.py     # NEW: VehicleOperator CRUD
│           ├── production_sites.py      # NEW: ProductionSite CRUD
│           └── sat_catalogs.py          # NEW: 8 SAT catalog read-only endpoints
├── schemas/
│   ├── product.py                       # NEW: Product + PriceList + ProductPrice schemas
│   ├── customer.py                      # NEW: Customer + TaxpayerRecipient schemas
│   ├── supplier.py                      # NEW: Supplier schemas
│   └── core.py                          # NEW: Employee, Store, Warehouse, PointSale,
│                                        #      CashDrawer, Label, ExchangeRate, Expense,
│                                        #      PaymentMethodOption, Vehicle, VehicleOperator,
│                                        #      ProductionSite schemas
└── services/
    ├── product_service.py               # NEW: Product service (includes merge + price init)
    ├── price_list_service.py            # NEW: PriceList service
    ├── customer_service.py              # NEW: Customer service
    ├── label_service.py                 # NEW: Label service
    ├── taxpayer_recipient_service.py    # NEW: TaxpayerRecipient service
    ├── supplier_service.py              # NEW: Supplier service
    ├── employee_service.py              # NEW: Employee service
    ├── warehouse_service.py             # NEW: Warehouse service
    ├── point_sale_service.py            # NEW: PointSale service
    ├── cash_drawer_service.py           # NEW: CashDrawer service
    ├── store_service.py                 # NEW: Store service
    ├── exchange_rate_service.py         # NEW: ExchangeRate service
    ├── expense_service.py               # NEW: Expense service
    ├── payment_method_option_service.py # NEW: PaymentMethodOption service
    ├── vehicle_service.py               # NEW: Vehicle service
    ├── vehicle_operator_service.py      # NEW: VehicleOperator service
    ├── production_site_service.py       # NEW: ProductionSite service
    └── sat_catalog_service.py           # NEW: SAT catalog list/get service (all 8 models)

tests/
└── api/
    └── v1/
        └── test_products.py             # OPTIONAL: product CRUD smoke tests
```

**Structure Decision**: Single-project FastAPI layout. Endpoint files 1:1 with routes, schema
files grouped by model file origin, service files 1:1 with resources. Follows the existing
`users.py` / `user_service.py` / `schemas/user.py` precedent.

## Complexity Tracking

No constitution violations.
