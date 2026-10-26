# Implementation Plan: Warehouse Receipts, Issues and Transfers

**Branch**: `029-inventory-documents` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/029-inventory-documents/spec.md`

## Summary

The three inventory documents get services and routes on the existing tables:

- **Receipts** (#58): draft, lines, confirm (+qty in the warehouse), cancel.
  - Lines linked to a purchase order show ordered, received elsewhere and this receipt's quantity; over-receipt is a warning.
  - The complementary receipt returns the open draft for the order and warehouse, or creates one with the remaining quantities.
- **Issues** (#60): draft, lines, confirm (−qty, checked against available stock), cancel. The supplier-return link is shown and never written.
- **Transfers** (#62), in two steps through the origin facility's transit warehouse:
  - dispatch (origin → transit, numbered in the origin facility);
  - receive, in full or per line, any number of times (transit → destination);
  - close the remainder (transit → origin) and cancel (reverses everything posted);
  - an in-transit view per facility;
  - adding a sales order's stockable lines, skipping products already on the transfer.

Shared mechanics:
- **Serial:** the facility's transit warehouse row, locked with the other warehouses, queues every numbering step of the facility; `MAX(serial)` is then an ordinary read (R1).
- **Locks:** every step that changes a document ends the request's opening transaction, then locks the document and the warehouses it touches, including the facility's transit warehouse (by primary key, in id order), before any ordinary read (R1, R2). The facility row is never locked. A repeated or concurrent request therefore cannot post twice, and every read after the locks is current. A deadlock or lock timeout answers 409 "busy, retry".
- **Stock checks:** available = on hand − reservations, summed per product, with `stock_ledger` unchanged (R2).
- **Cancel:** posts the negation of every ledger entry the document wrote (R3).
- **Rights and lists:** gated per FR-010 to FR-012 (R5, R6).

There is one migration, 026. It adds `inventory_transfer.in_transit` and the nullable `inventory_transfer_detail.quantity_received` (R4). Legacy rows read as received without a backfill. The migration is DDL, so it is confirmed before it runs.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2. No new dependency.

**Storage**: MariaDB `mbe_dev`, REPEATABLE READ.
- The tables already exist: `inventory_receipt`, `inventory_issue`, `inventory_transfer` and their details, `lot_serial_tracking` (ledger) and `lot_serial_rqmt` (reservations).
- Migration 026 adds two columns.

**Testing**: pytest with httpx `ASGITransport`.
- **Unit:** state derivation, line merging, outstanding quantities, reversal netting and over-receipt warnings.
- **Integration:** the three flows, the complementary receipt and the transit cycle, on the SQLite schema.
- **API:** routes with 401, 403, 404, 409 and 422.
- **Concurrency:** SQLite ignores row locks, so SC-002 and SC-003 are verified on the test deployment against mbe_dev, after confirmation (quickstart).

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: about 460 receipts, 100 issues and 140 transfers a month, with 4 lines on average. A confirm locks the document and 2 to 3 warehouse rows, and reads one sum per product.

**Constraints**:
- **Ledger:** append-only (spec 012 FR-062); lot, serial and expiry stay NULL.
- **Serials:** no duplicates under concurrent confirms, and no number consumed on failure.
- **Legacy rows:** documents written by the current system read correctly, including one-step transfers.
- **Out of scope:** `documents.assign_folio` is not changed.

**Scale/Scope**:
- **Routes:** 10 for receipts, 9 for issues, 13 for transfers.
- **New services:** `inventory_documents.py` (shared), `inventory_receipt_service.py`, `inventory_transfer_service.py`.
- **Also:** 1 migration, 1 schema module and 3 endpoint modules.
- **Docs:** `docs/specs/04-inventory.md`, `docs/data-dictionary.md` and the CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | One shared module drives the three documents. The issue has no service of its own because it is the shared flow plus an outbound check. There are no supplier returns, lots or FIFO. Cancel is one generic negation of what was posted. The transfer state needs one flag and one line column. | PASS |
| II. Think Before Coding | Four clarifications are recorded. The decisions on locking, state and reversal are argued in research R1–R4. The remaining sales-order race is named, not hidden (R2). | PASS |
| III. Surgical Changes | `stock_ledger.py` and `documents.assign_folio` are not touched. The models gain the two migrated columns. | PASS |
| IV. Goal-Driven | SC-001 to SC-007 map to tests or to recorded runs on the test deployment (quickstart). | PASS |
| V. Reuse Over Rebuild | Reused: `stock_ledger` (unchanged), `documents.assert_editable`, `DocumentStatus`, `require_privilege`, `has_any_privilege` and `ListResponse`. The new modules are justified below. | PASS, see Complexity Tracking |
| VI. Async-First | Every route and service is `async`, on `AsyncSession`. | PASS |
| VII. Security by Default | Every route has `require_privilege` on object 15, 16 or 17. The facility rule (FR-011) and the list scope (FR-012) are enforced in services. | PASS |
| VIII. Ruff | The gate runs before each commit. | PASS |
| Testing | API tests for every route; unit tests for every branch of state, merging, warnings and reversal. | PASS |
| Changelog | `[Unreleased]` gets Added (the three documents and the transit cycle). | PASS |

Post-design: no new violations. Migration 026 is the only schema change, and the clarified partial receipt requires it (R4).

## Project Structure

### Documentation (this feature)

```text
specs/029-inventory-documents/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/inventory-documents.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 026_inventory_transfer_transit.sql            # NEW (confirm before applying)
└── 026_inventory_transfer_transit_rollback.sql   # NEW
app/
├── api/v1/router.py                              # + 3 routers
├── api/v1/endpoints/inventory_receipts.py        # NEW
├── api/v1/endpoints/inventory_issues.py          # NEW
├── api/v1/endpoints/inventory_transfers.py       # NEW
├── models/inventory.py                           # + in_transit, quantity_received
├── schemas/inventory.py                          # NEW
└── services/
    ├── inventory_documents.py                    # NEW: kinds, drafts, lines, locks and serial,
    │                                             #   confirm, stock check, reversal, list scope
    ├── inventory_receipt_service.py              # NEW: PO view, warnings, complementary receipt
    └── inventory_transfer_service.py             # NEW: dispatch, receive, close, in transit,
                                                  #   sales order items, transfer state
tests/
├── unit/test_inventory_schemas.py                # NEW
├── unit/test_inventory_locks.py                  # NEW
├── unit/test_inventory_documents.py              # NEW
├── unit/test_inventory_receipt_service.py        # NEW
├── unit/test_inventory_transfer_state.py         # NEW
├── integration/inventory_seed.py                 # NEW
├── integration/test_inventory_receipt_flow.py    # NEW
├── integration/test_inventory_issue_flow.py      # NEW
├── integration/test_inventory_transfer_flow.py   # NEW
├── integration/test_inventory_scope.py           # NEW
├── api/test_inventory_receipts.py                # NEW
├── api/test_inventory_issues.py                  # NEW
└── api/test_inventory_transfers.py               # NEW
docs/specs/04-inventory.md
docs/data-dictionary.md
CHANGELOG.md
```

**Structure Decision**: the existing layout. There is one endpoint module per resource, as elsewhere. The three documents share one service module because their draft, line, serial, cancel and scope rules are identical (FR-001 to FR-013).

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Migration 026 (`in_transit`, `quantity_received`) | The clarified partial receipt needs a received quantity per line. "In transit" must also stay distinct from "received with a closed remainder" (R4). | Deriving state from ledger sums cannot tell a closed remainder from an outstanding one. A `status` column would need a backfill of 3,409 legacy rows. |
| `inventory_documents.py` | It holds the shared mechanics of three documents (FR-001 to FR-013), written once. | Three copies would drift. Serial and stock locking have no database constraint behind them (R1, R2). |
| `inventory_receipt_service.py`, `inventory_transfer_service.py` | Purchase-order and transit logic is specific to each document. | Putting it all in the shared module would mix concerns that only one document has. |
