# Implementation Plan: Constraint Violation Handling

**Branch**: `013-integrity-error-handling` | **Date**: 2026-07-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/006-constraint-violation-handling/spec.md`

## Summary

Three layers, cheapest to most informative, so that no database constraint can surface as a
`500`:

1. A FastAPI `IntegrityError` handler returning a generic `409` — the backstop that makes the
   guarantee total, including for endpoints added later.
2. A shared referential guard (`app/services/references.py`) that refuses a delete while any
   mapped table still references the row, naming the blocking `table.column` and row counts.
   Referencing tables come from `Base.metadata`, not a hand-written list, so new foreign keys
   are covered as soon as their model exists.
3. Duplicate-key pre-checks for the four unique constraints that had none, so the most common
   create-form mistake reads as a named field conflict rather than a generic one.

Deletes stay hard deletes; archiving remains a client-driven `status` change. Fixes GitHub
issue #107.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 — no new dependencies

**Storage**: MariaDB via aiomysql. Read-only with respect to schema: this feature adds no
column, table or index. It reads `information_schema`-equivalent knowledge from SQLAlchemy's
own `Base.metadata`, which the 209 existing foreign keys are already modelled in.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`, plus a read-only verification
pass against the populated `mbe_demo` database

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — cross-cutting service-layer change plus one application-level
exception handler

**Performance Goals**: One additional `UNION ALL` query per delete request. Deletes are rare,
administrator-initiated actions; a single round trip that produces an actionable message is a
deliberate trade against a bare failure.

**Constraints**:
- No API surface change: the generated OpenAPI document must be byte-identical before/after.
- Conflict responses must not disclose table, column or index names to the client — the
  guard's `table.column` labels are the deliberate exception, being the actionable payload.
- Hard delete only. No soft delete, no cascade beyond the two exempted owned-child cases.
- Reference counting must not require a maintained per-entity list.

**Scale/Scope**: 1 new module (`references.py`, 2 public functions); 1 exception handler; 17
delete services gain one guard call; 4 services gain create/update duplicate checks; 2 bespoke
guards replaced; 3 test files added, 2 updated.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ | One shared module replaces what would have been 17 hand-written guards. No retry logic, no partial-delete machinery, no configurable policy. The `exempt` parameter exists because two real cascades require it, not speculatively. |
| II. Think Before Coding | ✅ | Soft delete vs. hard delete, and whether the two owned-child cascades were exempt, were both put to the product owner before implementation and are recorded in spec Assumptions. The second answer reversed an earlier one; the spec records the final decision, not the first. |
| III. Surgical Changes | ✅ | Every touched line adds a guard, a pre-check, or the handler. Two bespoke guards were deleted because the shared one subsumes them — `price_list`'s also happened to be incomplete. No adjacent refactors. |
| IV. Goal-Driven Execution | ✅ | Success = quickstart.md scenarios pass: suite green, OpenAPI diff empty, guard counts cross-check against raw SQL on live data. |
| V. Reuse Over Rebuild | ⚠️ | One new module. Justified in Complexity Tracking below. |
| VI. Async-First | ✅ | Both guard functions are `async def` taking `AsyncSession`; the exception handler is `async def`. No sync DB access introduced. |
| VII. Security by Default | ✅ | No new endpoints and no auth changes. The handler tightens disclosure: the driver message, which names tables and indexes, is logged rather than returned. |
| VIII. Ruff Compliance | ✅ | `uv run ruff check app/ tests/` gates completion; mypy held at its pre-existing baseline with no errors in new code. |

**Testing rule**: No brand-new endpoints, so the constitution's mandatory-test rule for
endpoints does not bind. Tests are added anyway — the feature *is* error behaviour, so it is
untestable by inspection.

## Project Structure

### Documentation (this feature)

```text
specs/006-constraint-violation-handling/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md  # /speckit-specify output
├── contracts/
│   └── conflict-responses.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
app/
├── main.py                              # ADD IntegrityError handler → generic 409
└── services/
    ├── references.py                    # NEW: find_blocking_references, assert_not_referenced,
    │                                    #      assert_unique
    ├── employee_service.py              # + guard on delete
    ├── facility_service.py              # + guard on delete
    ├── address_service.py               # + guard on delete
    ├── supplier_service.py              # + guard on delete
    ├── label_service.py                 # + guard on delete
    ├── vehicle_operator_service.py      # + guard on delete
    ├── taxpayer_recipient_service.py    # + guard on delete
    ├── payment_method_option_service.py # + guard on delete
    ├── customer_service.py              # + guard, keeping the default-customer rule
    ├── product_service.py               # + guard, exempting product_price
    ├── user_service.py                  # + guard, exempting user_settings/access_privilege
    ├── price_list_service.py            # REPLACE bespoke guard (it missed product_price)
    ├── taxpayer_issuer_service.py       # REPLACE bespoke four-table guard
    ├── warehouse_service.py             # + guard; + code uniqueness on create/update
    ├── point_sale_service.py            # + guard; + code uniqueness on create/update
    ├── cash_drawer_service.py           # + guard; + code uniqueness on create/update
    └── vehicle_service.py               # + guard; + license_plate uniqueness on create/update

tests/
├── unit/
│   ├── test_references.py               # NEW: guard behaviour, exemptions, labelling
│   ├── test_taxpayer_issuer_service.py  # REWRITTEN: now pins wiring, not counting
│   └── test_point_sale_service.py       # UPDATED: mocks must satisfy the new pre-check
└── api/
    └── test_constraint_errors.py        # NEW: 409 for each class, and no leakage
```

**Structure Decision**: Existing single-service layout. The only new file is
`app/services/references.py`, which sits beside `fk_expansion.py` — the established home for
cross-cutting helpers shared by services rather than owned by one entity.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New module `app/services/references.py` (Principle V) | 17 delete services need identical reference-counting. No existing abstraction counts inbound references; `fk_expansion.batch_fetch` resolves FKs outward, which is the opposite direction. | Writing the guard inline in each service was the alternative — 17 near-identical hand-maintained lists, which FR-007 forbids because a new foreign key would silently go unguarded. The two bespoke guards that already existed demonstrated the failure mode: `price_list`'s checked one of its two referencing tables. |
