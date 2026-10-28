# Implementation Plan: Supplier Sub-Panels by Privilege, Race-Safe Serials and Stock

**Branch**: `035-serials-stock-bank-access` | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/035-serials-stock-bank-access/spec.md`

## Summary

- **Supplier sub-panels** (mictlanix/mbe-roadmap#152):
  - the supplier detail keeps its lookup gate;
  - each panel is returned only to a caller with that panel's read privilege: bank accounts 13, contacts 12, addresses 11, agreements 3;
  - on create and update, an unreadable panel's link list is ignored (R1).
- **One numbering rule** (mictlanix/mbe-roadmap#153), spec 029's commit-then-lock extended with the facility row:
  - every numbering step commits away the request's opening snapshot;
  - it then locks cash session → own documents → lines `FOR SHARE` → facility rows (ascending) → warehouses (ascending);
  - only then does it read, so `MAX(serial)` is current;
  - `documents.assign_folio` keeps its signature and gains a `lock_facility` half;
  - spec 027's locking `MAX` is rejected (R2, R3).
- **Stock:** sales order confirmation locks its stocked lines' warehouses before reading availability, so it queues with other confirmations and with spec 029's inventory steps. Locking the order row makes a double confirm confirm once. Header and line edits re-check the order under its lock, so an edit that waited behind a confirmation is refused (R3, R4).
- **Customer payments** are numbered per facility, including a refund's payouts. A numeric list search matches the serial (R6).
- **Migration 030:**
  - backfills the 20 API payments numbered 0;
  - adds `customer_payment (facility, serial)`;
  - adds `lot_serial_tracking (source, reference)` and `lot_serial_rqmt (source, reference)`;
  - runs right after the deploy (R7).
- **Supplier payments** were already safe and are unchanged. Purchase requests and invoices are unchanged too (R3).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2. No new dependency.

**Storage**: MariaDB `mbe_dev`. Migration 030 (three indexes and a 20-row backfill), with a rollback file.

**Testing**: pytest with httpx `ASGITransport` (R8).
- **Unit:** lock order per rewritten step against a fake session compiled for MariaDB; `lock_facility` and `assign_folio`; the supplier panel rule.
- **API:** supplier detail and writes by privilege; numeric payment search.
- **Integration (SQLite):** sequential numbering per type, payment numbering, stock refusal after a reservation, double confirm.
- **MariaDB races** in the quickstart, before and after, with cleanup.
- **Migration 030:** `test_migrate.py` pins its discovery and order. The SQL is exempt from an automated test, because SQLite cannot run it; it is verified on mbe_dev (quickstart step 3).

**Target Platform**: Linux server (xolotl)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**:
- A payment's `MAX(serial)` reads through the new index instead of a filesort over about 114k rows.
- The ledger lookup reads tens of rows instead of 670k.
- A confirmation adds one warehouse-lock statement and one locking read of its lines.

**Constraints**:
- No ordinary read before the facility lock on any numbering step.
- One lock order across all paths (R3).
- Refusals consume no serial.
- No renumbering of the current system's rows.

**Scale/Scope**:
- **Six numbering steps rewritten:** sales order confirm, quote confirm, delivery order confirm, refund confirm, itinerary stop close, customer payment create.
- **Four sales order edits** re-check under the order lock: header update, add, update and remove line.
- **Three supplier routes** filtered; one payment search parameter.

## Constitution Check

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | No new table, column, privilege or setting. One small helper, `lock_facility`, split out of `assign_folio`. Locks reuse spec 029's `lock_warehouses` and `busy_as_conflict` | PASS |
| II. Think Before Coding | Four clarifications. Measured duplicates, API rows, serial-0 payments, privilege holders and plans. Caller lock orders mapped before choosing. Locking `MAX`, a second connection and a counter rejected with reasons (R2) | PASS |
| III. Surgical Changes | The six numbering steps reorder their locks and keep their checks in place. The four order edits gain the same commit-and-lock prelude, nothing else. Supplier service and three routes; one search branch; one migration. Supplier payments, purchase requests and invoices untouched | PASS |
| IV. Goal-Driven | SC-001 to SC-008 map to tests and to the quickstart's before/after runs | PASS |
| V. Reuse Over Rebuild | Reuses `inventory_documents.lock_warehouses`, `busy_as_conflict`, `cash_session_service.lock_open_session`, `documents.assign_folio`, `has_any_privilege`, `is_barcode_or_id`, the fake-session lock tests of spec 029 | PASS |
| VI. Async-First | All routes and services stay async | PASS |
| VII. Security by Default | Each supplier sub-panel follows its own route's read privilege. Every check is server-side; administrators short-circuit as everywhere | PASS |
| VIII. Ruff | Gate before each commit | PASS |
| Testing | Tests first for each rewritten step's lock order, the panel rule and the search branch; concurrency proven on MariaDB, since SQLite cannot | PASS |
| Changelog | `[Unreleased]`, in the constitution's groups only: Fixed (supplier panels by privilege, in neutral wording; duplicate-serial refusals, overselling, double confirm, stale `MAX`); Changed (API payments numbered; numeric payment search; migration 030) | PASS |

Post-design: no new violations. An adversarial review of the first draft's lock order found a deadlock between warehouses-before-facility and draft creation, stale reads in close stop, the wrong transit warehouses, and unlocked order edits. All four are fixed in R2–R5. Two limits are left as they are and documented in R3: unordered ledger inserts on paths that don't number, and a same-cashier expense voucher. `lock_facility` has two callers (`assign_folio` and close stop's multi-facility lock), so it is not a single-use abstraction.

## Project Structure

### Documentation (this feature)

```text
specs/035-serials-stock-bank-access/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/api.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── api/v1/endpoints/
│   ├── suppliers.py                      # pass the caller to get/create/update
│   └── customer_payments.py              # search description (digits: id or serial)
└── services/
    ├── documents.py                      # + lock_facility; assign_folio precondition; module docstring (R2)
    ├── supplier_service.py               # readable panels; filtered _attach_links; unreadable lists → None
    ├── sales_order_service.py            # confirm_order: commit, order, lines FOR SHARE, facility, warehouses; edits lock the order
    ├── sales_quote_service.py            # confirm_quote: commit, quote, facility
    ├── delivery_order_service.py         # confirm: commit, delivery order, facility
    ├── customer_refund_service.py        # confirm_refund: commit, session, refund, order, lines, facility, warehouses; payouts numbered
    ├── delivery_itinerary_service.py     # close_stop: pre-read, commit, itinerary, stop, facilities, warehouses
    └── customer_payment_service.py       # create_payment numbered under session + facility; numeric search
migrations/
├── 030_serials_ledger_index.sql
└── 030_serials_ledger_index_rollback.sql
tests/
├── unit/test_documents.py                # lock_facility; assign_folio
├── unit/test_numbering_locks.py          # NEW: lock order of the six steps and the four edits (fake MariaDB session)
├── unit/test_supplier_panels.py          # NEW: the panel rule
├── api/test_supplier_sub_panels.py       # detail and writes by privilege
├── api/test_customer_payments.py         # numeric search passes through
└── integration/test_numbering_flow.py    # NEW: per-type sequences, payment serials, stock refusal, double confirm
docs/specs/                               # numbering and the supplier panels, where documented
CHANGELOG.md
```

**Structure Decision**: the existing single-project layout. No new module beyond tests.

## Complexity Tracking

No violations to justify.
