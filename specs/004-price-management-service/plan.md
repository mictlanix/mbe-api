# Implementation Plan: Price Management Service

**Branch**: `004-price-management-service` | **Date**: 2026-07-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-price-management-service/spec.md`

## Summary

Extract `ProductPrice` (per-product, per-price-list price rows) out of the product domain into
its own CRUD surface: a new schema module, a new `product_price_service.py`, and a new top-level
`/product-prices` router — mirroring the existing `/price-lists` module exactly. `product_service.py`
and `products.py` lose all knowledge of `ProductPrice`/`PriceList`: `ProductResponse` drops the
`prices` field, product creation stops auto-provisioning price rows, and product delete/merge
delegate price cleanup to the new service instead of touching the `product_price` table directly.
`PriceList` itself (`/price-lists`, `price_list_service.py`) is untouched.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 (all existing — no new
dependencies)

**Storage**: MariaDB via aiomysql — existing `product_price` table, no schema/migration change

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport` (existing pattern in `tests/api/`)

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — new endpoint module + removal of logic from an existing module

**Constraints**:
- No DB migration: `product_price` table schema is unchanged, only which module owns access to it.
- Behavior change (pre-agreed, documented in spec Assumptions/FR-010): new products no longer get
  auto-provisioned zeroed price rows.
- `PriceListResponse`/`PriceListCreate`/`PriceListUpdate` stay in `app/schemas/product.py` exactly
  where they are today — `app/schemas/customer.py` imports `PriceListResponse` from there and must
  keep working unchanged.
- New endpoints gated by `SystemObject.PRICING` (`app/enums.py:122`), which exists and is
  currently unused — reused rather than adding a new `SystemObject` member.

**Scale/Scope**: One new schema file, one new service file, one new endpoint/router file, one
router registration line, edits to `product_service.py`/`products.py`/`app/schemas/product.py` to
remove `ProductPrice` logic, tests for the new endpoints.

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ | New module is a straight copy of the existing `price_lists.py` / `price_list_service.py` pattern — no new abstractions invented. |
| II. Think Before Coding | ✅ | Scope, response-shape, and auto-creation-removal decisions were already surfaced and agreed with the user before this plan (recorded in spec Assumptions). |
| III. Surgical Changes | ✅ | Product-side edits are deletions of `ProductPrice`-specific code paths only; no unrelated refactors to `product_service.py`. |
| IV. Goal-Driven Execution | ✅ | Success = new `/product-prices` CRUD works end-to-end (quickstart.md) and product endpoints return no pricing data. |
| V. Reuse Over Rebuild | ✅ | Reuses `SystemObject.PRICING` (already defined, unused), the `price_lists.py` endpoint pattern, and `PriceListResponse` as-is. |
| VI. Async-First | ✅ | New service functions are `async def` using `AsyncSession`, matching `price_list_service.py`. |
| VII. Security by Default | ✅ | All new endpoints gated by `require_privilege(SystemObject.PRICING, ...)`, matching the `products.py` privilege pattern (stricter than `price_lists.py`, which only checks `get_current_user`). |
| VIII. Ruff Compliance | ✅ | `uv run ruff check app/ migrations/ tests/` must pass before commit. |

## Project Structure

### Documentation (this feature)

```text
specs/004-price-management-service/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── product-prices.md
└── tasks.md              # Phase 2 output (/speckit-tasks — not created by /speckit-plan)
```

### Source Code (repository root)

```text
app/
├── enums.py                              # unchanged — SystemObject.PRICING already exists
├── models/
│   └── product.py                        # unchanged — PriceList, ProductPrice models as-is
├── schemas/
│   ├── product.py                        # remove ProductPriceResponse + ProductResponse.prices field
│   └── product_price.py                  # NEW — ProductPriceCreate/Update/Response
├── services/
│   ├── product_service.py                # remove all ProductPrice/PriceList references
│   └── product_price_service.py          # NEW — CRUD + list/filter, delete_for_product()
└── api/v1/
    ├── router.py                         # register product_prices router at /product-prices
    └── endpoints/
        ├── products.py                   # unchanged behavior, response just narrower
        └── product_prices.py             # NEW — list/create/get/update/delete endpoints

tests/
└── api/
    ├── test_products.py                  # update: assert no `prices` field, no auto-created rows
    └── test_product_prices.py            # NEW — CRUD + filter + 404/409 tests
```

**Structure Decision**: Single FastAPI service, existing layout (`app/{models,schemas,services,api}`).
The new price module is added as sibling files next to the existing `price_lists.py` /
`price_list_service.py`, following that exact precedent — no new top-level structure needed.

## Complexity Tracking

*No constitution violations — table not needed.*
