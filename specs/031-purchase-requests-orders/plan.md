# Implementation Plan: Purchase Requests, Purchase Orders and Average Cost

**Branch**: `031-purchase-requests-orders` | **Date**: 2026-10-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/031-purchase-requests-orders/spec.md`

## Summary

- **Requests** (#68): `/purchase-requests`, 14 routes, on PurchaseRequest (86) and PurchaseRequestApproval (96).
  - The states come from the existing flags (research R1).
  - The serial is set on first submit, under the facility's transit-warehouse lock, the same numbering lock spec 029 uses (R2).
  - Approve stores the approver and the time (migration 028). Reject records a source-4 incidence and returns the request to draft. Marking lines needs PurchasesOrders (20) update.
  - Non-administrators may edit, submit and cancel only their own facility's requests.
- **Purchase orders** (#70): `/purchase-orders`, 15 routes, on 20.
  - Edits are allowed on drafts only. Lines are added by hand, from one request, or as a to-purchase group per supplier and facility (R8).
  - Request-line rows are locked so a line is on at most one live PO (R3).
  - Confirm locks the PO and creates one draft receipt per destination warehouse with the pending quantities, exactly once (R4).
  - Cancel is for drafts only. It keeps the lines and frees the request lines.
- **Average cost** (#70): spec 029's receipt confirm and cancel gain a cost step for receipts with a PO.
  - The step locks the list-0 rows of the products before any ordinary read. It reads company-wide on hand with one grouped query, averages each line in turn and records each change in `product_cost_change` (R5).
  - Cancel restores the cost when the receipt's change is still the latest, and warns otherwise (R6).
  - Cost is rounded to 4 decimals, the column's precision. This amends FR-039 at plan time.
- **PDF and email**: a `purchase_order.html` template on spec 019's rendering, and email through spec 024's mailer, with a source-5 incidence written after each send (R9).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, and the existing rendering and mail modules. No new dependency.

**Storage**: MariaDB `mbe_dev`, REPEATABLE READ. Migration 028 (R12):
- `purchase_request` gains `approver` and `approval_time`;
- `purchase_order_detail.purchase_request_detail` gets an index and an FK (8,369 linked rows, no orphans);
- a new table, `product_cost_change`.

**Testing**: pytest with httpx `ASGITransport`.
- **Unit:** state derivation, pending-line rules, grouping, pending quantity at confirm, the cost formula (Q < 0, U = 0, repeated product, missing row, rounding), the restore decision, and PDF totals.
- **Integration:** the request → PO → receipt → cost flows on SQLite.
- **API:** every route with 401, 403, 404, 409 and 422; the facility rule; 502 and 503 for mail.
- **Concurrency** (SC-001, SC-002, SC-003, US4 scenario 4) on the test deployment, after confirmation (quickstart).

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**:
- A 212-line PO confirm, and a 212-line receipt confirm with its cost updates, each under 5 s (SC-005).
- The company-wide on-hand query for 212 products measured under 1 s on mbe_dev.

**Constraints**:
- Cost is written only by receipt confirm and cancel (FR-038).
- Lock order: document → warehouses (ascending) → cost rows (ascending). Request-line rows are locked ascending, after the PO row.
- Single cutover: no design for two writers.

**Scale/Scope**:
- **Volume:** about 95 requests and 160 POs a month, with about 6 and 4.6 lines each. The largest PO has 212 lines.
- **Routes:** 29.
- **New services:**
  - `purchase_request_service.py`;
  - `purchase_order_service.py`, which includes to-purchase, confirm and email;
  - `purchase_cost_service.py`.
- **Extended:**
  - `inventory_documents.py`: the cost hooks in `confirm_simple` and `cancel`;
  - `inventory_receipt_service.py`: warnings, and the warehouse refusal for receipts with a PO;
  - `print_contexts.py`: the PO context.
- **Also:**
  - 1 migration;
  - the models in `purchases.py` (new columns, `ProductCostChange`);
  - the schema module `schemas/purchases.py`;
  - 2 endpoint modules;
  - 1 template.
  - Docs: `05-purchases.md` §1–4, the data dictionary and the CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | States come from the existing flags. Locking reuses spec 029's helpers. The cost step is two `if`s in the shared confirm and cancel, not a hook registry. There is no PO approval, currency, discount or invoice editing, no repricing, and no request PDF. | PASS |
| II. Think Before Coding | Seven clarifications are recorded. The data was measured before deciding: grouping, approvers, no orphans, query timing, column precision. The FR-039 precision conflict is surfaced and resolved in research R5 and in the spec. | PASS |
| III. Surgical Changes | Spec 029 code changes in three places: confirm and cancel call the cost step only for receipts with a PO; the receipt update refuses a warehouse move only for receipts with a PO; `attach_derived` merges warnings. Behaviour of other kinds is unchanged. | PASS |
| IV. Goal-Driven | SC-001 to SC-007 map to tests or to recorded runs on the test deployment (quickstart). | PASS |
| V. Reuse Over Rebuild | Reused: `begin_locked` pieces (`lock_warehouses`, `_transit_map`, `busy_as_conflict`, `assert_facility`), receipt models and `attach_derived`, `incidences.record`, `rendering`, `mail`, `QuoteEmailRequest`, `header_context`, `derive_status`, `require_privilege`. New services and the table are justified below. | PASS, see Complexity Tracking |
| VI. Async-First | Every route and service is async. | PASS |
| VII. Security by Default | Every route is gated on 86, 96 or 20 (FR-011, FR-029). The facility rule applies to request edit, submit and cancel. | PASS |
| VIII. Ruff | The gate runs before each commit. | PASS |
| Testing | Tests are written first for every service branch and route. | PASS |
| Changelog | `[Unreleased]` gets Added (requests, POs, to-purchase, average cost, PO PDF and email, migration 028) and Changed (receipt confirm and cancel update cost; receipts with a PO keep their warehouse). | PASS |

Post-design: no new violations.

## Project Structure

### Documentation (this feature)

```text
specs/031-purchase-requests-orders/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/purchasing.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 028_purchasing.sql                            # NEW (confirm before applying)
└── 028_purchasing_rollback.sql                   # NEW
app/
├── api/v1/router.py                              # + 2 routers
├── api/v1/endpoints/purchase_requests.py         # NEW
├── api/v1/endpoints/purchase_orders.py           # NEW
├── models/purchases.py                           # + approver, approval_time; ProductCostChange
├── schemas/purchases.py                          # NEW
├── rendering/templates/purchase_order.html       # NEW
└── services/
    ├── purchase_request_service.py               # NEW: drafts, submit/serial, approve, reject, mark, cancel, lists
    ├── purchase_order_service.py                 # NEW: drafts, request lines, to-purchase, confirm, cancel, email, lists
    ├── purchase_cost_service.py                  # NEW: lock, average, restore, history
    ├── inventory_documents.py                    # confirm_simple / cancel call the cost step for receipts with a PO
    ├── inventory_receipt_service.py              # warnings merge; warehouse refusal for receipts with a PO
    └── print_contexts.py                         # + purchase_order_context
tests/
├── unit/test_purchase_schemas.py                 # NEW
├── unit/test_purchase_states.py                  # NEW
├── unit/test_purchase_requests.py                # NEW
├── unit/test_purchase_orders.py                  # NEW
├── unit/test_purchase_cost.py                    # NEW
├── unit/test_purchase_order_print.py             # NEW
├── integration/inventory_seed.py                 # + request, supplier and cost seeds
├── integration/test_purchase_request_flow.py     # NEW
├── integration/test_purchase_order_lines_flow.py # NEW
├── integration/test_purchase_order_confirm_flow.py # NEW
├── integration/test_purchase_cost_flow.py        # NEW
├── integration/test_purchase_order_document_flow.py # NEW
├── api/test_purchase_requests.py                 # NEW
├── api/test_purchase_orders.py                   # NEW
└── api/test_inventory_receipts.py                # + cost on confirm and cancel, warehouse refusal
docs/specs/05-purchases.md
docs/data-dictionary.md
CHANGELOG.md
```

**Structure Decision**: the existing layout, with one endpoint module per resource. The cost history is served under `/purchase-orders/cost-changes`, gated on 20 Read.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Migration 028 | FR-005 needs the approver and the time; FR-036 needs a cost history; FR-026 lookups need the request-line index. | An incidence per approval can't be filtered or shown on the request cheaply; there is nowhere else to record a cost change. |
| `product_cost_change` table | Traceable cost changes and an exact restore on cancel (FR-036, FR-037). | Incidences hold free text, and restoring would mean parsing it. |
| `purchase_request_service.py`, `purchase_order_service.py` | Two documents with their own states, rights and line rules; nothing serves them today. | Spec 029's `Kind` assumes a facility column, a ledger posting and receipt-shaped lines, none of which requests or POs have. |
| `purchase_cost_service.py` | Averaging, restore and history are shared by receipt confirm and cancel, and tested on their own. | Inlining it in `inventory_documents` would add purchasing rules to the shared inventory module. |
