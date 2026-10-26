# Implementation Plan: Physical Inventory Count, Stock and Kardex

**Branch**: `030-physical-count-kardex` | **Date**: 2026-10-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/030-physical-count-kardex/spec.md`

## Summary

- **Physical count** (#64): a new count document (`inventory_count` and `inventory_count_detail`, migration 027), built on spec 029's shared module.
  - Drafts take counted quantities one at a time or as batches of up to 2,000. Entering a product again replaces its quantity.
  - Drafts are blind (clarification Q4).
  - Confirm locks the document, then the warehouse and the facility's transit warehouse (the numbering lock), as spec 029 does. It then reads on hand for every counted product in one grouped query and posts each non-zero difference as the new type **12 (physical count)**, inserted in bulk.
  - Confirm can add uncounted products as zero. It warns about products whose available stock goes negative, and about products another count corrected since this one was created (FR-010a).
  - A confirmed count cannot be cancelled.
- **Stock** (#66):
  - `GET /stock`: one warehouse, every product with on hand, reserved, available and incoming;
  - `GET /stock/products/{id}`: one product across warehouses, plus the facilities' in-transit balances.
  - Gated on WarehouseStockReport (67).
- **Kardex** (#66): `GET /kardex` for a product and warehouse over at most 366 days. It returns the opening balance, every entry with its resolved document and running balance, and the closing balance. Gated on Kardex (32).
- **Type 13 (delivery pickup):** counter pickups post type 13 instead of 10 (FR-034), a one-line change in `delivery_order_service`.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2. No new dependency.

**Storage**: MariaDB `mbe_dev`, REPEATABLE READ.
- Migration 027 adds `inventory_count` and `inventory_count_detail`.
- The ledger (`lot_serial_tracking`, 639,954 entries) and reservations (`lot_serial_rqmt`) are unchanged.
- No new index: measured on mbe_dev, the largest warehouse's grouped sum (283,391 entries, 10,709 non-zero products) takes under 1 s, and a kardex opening balance for the busiest product about 0.4 s, connection included.

**Testing**: pytest with httpx `ASGITransport`.
- **Unit:** differences, blind responses, batch validation, uncounted-as-zero, the overlap warning, kardex balances and document resolution.
- **Integration:** count, stock and kardex flows on SQLite.
- **API:** every route with 401, 403, 404, 409 and 422.
- **On the test deployment, after confirmation:**
  - a 15,000-line count for SC-003, in a test warehouse;
  - a double confirm for SC-002;
  - stock figures checked against the ledger for SC-005.

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**:
- **Counts:** about 31 a month, about 30 lines each. The opening count reaches 15,000 lines, which must confirm in under 60 s (SC-003).
- **Stock and kardex:** a few seconds at most.

**Constraints**:
- **Ledger:** append-only; lot, serial and expiry stay NULL.
- **Counts:** confirm is all or nothing, and a count can be confirmed only once.
- **Legacy data:** type 8 stays the current system's. Stale reservation rows are ignored, since only type 11 counts as reserved.

**Scale/Scope**:
- **Routes:** 9 for counts, 2 for stock, 1 for the kardex.
- **New services:** `inventory_count_service.py`, `stock_query_service.py` and `kardex_service.py`.
- **Extended:**
  - `inventory_documents.py`: a `COUNT` kind, and a hook so cancel refuses confirmed counts;
  - `enums.py`: types 12 and 13;
  - `delivery_order_service.py`: pickup posts type 13.
- **Also:** 1 migration and a schema module. Docs: `04-inventory.md` §4–6, the data dictionary and the CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | The count reuses spec 029's draft, lock, serial, list and cancel mechanics. Stock and kardex are read-only queries over the existing ledger. There are no lots, costs, labels or file upload. One migration. | PASS |
| II. Think Before Coding | Four clarifications are recorded. Performance was measured on mbe_dev before deciding against an index. The bulk-insert and chunking choices are argued in research R2. | PASS |
| III. Surgical Changes | Spec 012's pickup changes one constant (FR-034, clarified). `inventory_documents` gains a kind and a refusal hook, and its existing behaviour is unchanged. `stock_ledger` is unchanged. | PASS |
| IV. Goal-Driven | SC-001 to SC-006 map to tests or to recorded runs on the test deployment (quickstart). | PASS |
| V. Reuse Over Rebuild | Reused: `inventory_documents` (begin_locked, next_serial, busy_as_conflict, list_documents, cancel), `stock_ledger` batched sums, and the transfer service's outstanding figures. New services are justified below. | PASS, see Complexity Tracking |
| VI. Async-First | Every route and service is async. | PASS |
| VII. Security by Default | Counts are gated on 74, stock on 67 (Read), the kardex on 32 (Read). The facility rule applies on count confirm and cancel. | PASS |
| VIII. Ruff | The gate runs before each commit. | PASS |
| Testing | Tests are written first for every service branch and route. | PASS |
| Changelog | `[Unreleased]` gets Added (counts, stock, kardex, migration 027) and Changed (pickups post type 13). | PASS |

Post-design: no new violations.

## Project Structure

### Documentation (this feature)

```text
specs/030-physical-count-kardex/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/count-stock-kardex.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 027_inventory_count.sql                       # NEW (confirm before applying)
└── 027_inventory_count_rollback.sql              # NEW
app/
├── enums.py                                      # TransactionType 12, 13
├── api/v1/router.py                              # + 3 routers
├── api/v1/endpoints/inventory_counts.py          # NEW
├── api/v1/endpoints/stock.py                     # NEW
├── api/v1/endpoints/kardex.py                    # NEW
├── models/inventory.py                           # + InventoryCount, InventoryCountDetail
├── schemas/inventory.py                          # + count, stock, kardex schemas
└── services/
    ├── inventory_documents.py                    # + COUNT kind; cancel refuses confirmed counts
    ├── inventory_count_service.py                # NEW: lines, batches, confirm, warnings
    ├── stock_query_service.py                    # NEW: warehouse and product stock
    ├── kardex_service.py                         # NEW: balances, rows, document resolution
    └── delivery_order_service.py                 # pickup posts type 13
tests/
├── unit/test_inventory_count.py                  # NEW
├── unit/test_kardex.py                           # NEW
├── integration/test_inventory_count_flow.py      # NEW
├── integration/test_stock_queries.py             # NEW
├── integration/test_kardex_flow.py               # NEW
├── api/test_inventory_counts.py                  # NEW
├── api/test_stock.py                             # NEW
└── api/test_kardex.py                            # NEW
docs/specs/04-inventory.md
docs/data-dictionary.md
CHANGELOG.md
```

**Structure Decision**: the existing layout, with one endpoint module per resource.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Migration 027 (count tables) | A count document with lines, a serial and a state is required (#64). The receipt-based count is what is being replaced. | Reusing `inventory_receipt` would keep the current system's ambiguity: a receipt that is really a count, posting type 4. |
| `inventory_count_service.py` | Replace-not-add lines, batches, blind responses, a confirm computed from on hand, and two warnings. None of these exist in spec 029's flow. | Folding it into `inventory_documents` would add count-only branches to the shared module. |
| `stock_query_service.py`, `kardex_service.py` | Read models over the ledger that nothing provides yet. | Putting them in `stock_ledger` would mix posting with reporting, and with document resolution across nine tables. |
