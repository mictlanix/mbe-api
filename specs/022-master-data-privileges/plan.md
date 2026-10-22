# Implementation Plan: Master Data Access Privileges

**Branch**: `022-master-data-privileges` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/022-master-data-privileges/spec.md`

## Summary

All 68 routes on the 11 master data routers change from `get_current_user` to a privilege guard.

- **Writes and sub-resources** use `require_privilege` with the module's own SystemObject. A nested route takes its parent's object.
- **Lookups.** The list and get-by-id routes of 7 modules use a new `require_any_privilege`. It also accepts Read on the screens that look those modules up (FR-005), so the pilot's POS, checkout, product form and pricing grid keep working.
- **`PUT /customers/{id}`** resolves its privilege from the body. A link-only body (`addresses` / `contacts`) also accepts SalesOrders or SalesQuotes Update (FR-006).

There is no schema or data change. The guards are listed route by route in [contracts/route-privileges.md](contracts/route-privileges.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 (no new dependency)

**Storage**: MariaDB `mbe_dev`; reads `access_privilege` only, no migration

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`

**Target Platform**: Linux server (xolotl, test deployment at test.api.mbe.mictlanix.com)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: One extra indexed query per request on the guarded routes (`access_privilege_user_object_uq`), the same cost products already pay

**Constraints**: No UI change. mbe-ui's existing calls must keep working for users who use them today. Administrators keep bypassing the checks.

**Scale/Scope**: 11 routers, 68 routes, 36 active users in mbe_dev

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | The only new abstraction is `require_any_privilege` and its helper `has_any_privilege`, which FR-005 and FR-006 need. No configuration, and the host table is written inline per router. | PASS |
| II. Think Before Coding | The interpretations were surfaced in the spec (strict vs host grants, the link exception) and the user chose. The non-CRUD rights are recorded in research R3. | PASS |
| III. Surgical Changes | Only the 11 routers, `deps.py` (one new factory, a helper, and a `grants` attribute on `require_privilege`) and new tests change. Existing tests are untouched (R6). | PASS |
| IV. Goal-Driven Execution | The success criteria are the route-table test, the mbe_dev re-measurement and the manual checks in quickstart.md. | PASS |
| V. Reuse Over Rebuild | Reuses `require_privilege`, `AccessPrivilege`, the 403 response and the existing test fakes. No new model, schema or service. | PASS |
| VI. Async-First | The new dependency and helper are `async` and use `AsyncSession`. | PASS |
| VII. Security by Default | Tightens authorization. Every route still requires `get_current_user` underneath, since both factories depend on it. | PASS |
| VIII. Ruff Compliance | Gate in quickstart.md. | PASS |
| Testing | Tests are written first: unit tests for the dependency and API tests for the route table and behavior (R5). | PASS |
| Changelog | A `CHANGELOG.md` `[Unreleased]` → Changed entry is planned. | PASS |

Re-check after Phase 1 design: unchanged, all PASS.

## Project Structure

### Documentation (this feature)

```text
specs/022-master-data-privileges/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── route-privileges.md
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── core/
│   └── deps.py                    # + has_any_privilege, require_any_privilege; grants attribute
└── api/v1/endpoints/
    ├── customers.py               # incl. PUT link exception, discounts → CUSTOMERS
    ├── employees.py
    ├── exchange_rates.py
    ├── expenses.py
    ├── labels.py
    ├── payment_method_options.py
    ├── price_lists.py             # incl. delete/preview → Read
    ├── suppliers.py               # incl. agreements → SUPPLIERS
    ├── taxpayer_recipients.py
    ├── vehicle_operators.py       # incl. license-photo → Update/Read
    └── vehicles.py

tests/
├── unit/test_deps_privileges.py           # new
└── api/test_master_data_privileges.py     # new: route table + behavior + link exception

docs/specs/12-users.md             # §5 Authorization: note the host grants and the link exception
CHANGELOG.md                       # [Unreleased] → Changed
```

**Structure Decision**: Single web-service project. Each router declares its guards as module-level constants (`_READ`, `_CREATE`, …), the way `sales_orders.py` and `cash_sessions.py` do. Own-module guards are `_READ`, `_CREATE`, `_UPDATE` and `_DELETE`. The 7 modules whose list and get-by-id routes also accept other screens' privileges add `_LOOKUP = require_any_privilege(...)`, with the module's own Read first.

## Complexity Tracking

No violations.
