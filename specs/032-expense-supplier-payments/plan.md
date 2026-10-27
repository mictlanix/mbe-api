# Implementation Plan: Expense Vouchers and Supplier Payments

**Branch**: `032-expense-supplier-payments` | **Date**: 2026-10-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/032-expense-supplier-payments/spec.md`

## Summary

- **Expense vouchers** (#72): `/expense-vouchers`, 11 routes, on ExpenseTicket (82). The existing tables are used unchanged.
  - States come from `completed` and `cancelled`; a void is `completed` plus `cancelled` (research R1).
  - Only the creator edits, confirms and cancels the draft. Confirm moves the voucher into the caller's then-open session and dates it (R3).
  - A void needs delete on 82 and a reason, recorded as a source-14 incidence.
  - Confirm and void lock the session row, and `close_session` now locks it too, so a closed cut never changes (R2).
  - A ticket prints on spec 019's layout (R10).
- **Supplier payments** (#74): `/supplier-payments`, 7 routes, plus `GET /purchase-orders/{id}/payments`, on SupplierPayment (21).
  - A payment is numbered per facility with `assign_folio`.
  - It is applied to confirmed POs of its supplier that were confirmed on or after a cutoff fixed by migration (R6). Each application is capped by the payment's credit and the PO's balance.
  - Every write follows one lock order: session → payment → POs ascending → facility (R4).
  - Reverse and cancel need delete on 21 and a reason, recorded as source-15 incidences.
  - A cash payment needs the caller's open session, and the cut subtracts it as a separate figure (R8).
- **Balances** are computed, never stored (R7): per PO, and a per-supplier summary for accounts payable (#79).
- **Migration 029** adds columns to `supplier_payment`, plus the `purchase_order_payment` and `supplier_balance_cutoff` tables (R11).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, and the existing rendering module. No new dependency.

**Storage**: MariaDB `mbe_dev`, REPEATABLE READ. Migration 029 (R11):
- `supplier_payment` gains facility, serial (with `UNIQUE (facility, serial)`), cash_session, cancelled, creation_time, updater and modification_time, and the 5 legacy rows are backfilled;
- `purchase_order_payment` is new;
- `supplier_balance_cutoff` is new, with one row and a NULL cutoff.

**Testing**: pytest with httpx `ASGITransport`.
- **Unit:** the voucher state and its refusals, the creator rule, the payment and application refusals, credit and balance arithmetic, the cutoff rule, the cut figures with supplier payments, and the ticket context.
- **Integration (SQLite):** the voucher flow through the cut, and the payment flow through applications, reversal, cancel and balances.
- **API:** every route with 401, 403, 404, 409 and 422.
- **Concurrency** (SC-002, SC-003, SC-004) on the deployment, after confirmation (quickstart).

**Target Platform**: Linux server (xolotl)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: a voucher step under 1 s (SC-006). A supplier summary over a year of one large supplier's POs (about 350) under 2 s.

**Constraints**:
- **Lock orders:**
  - session row first for anything that touches a drawer;
  - payment → PO rows ascending → facility row last;
  - ordinary reads only after the last lock.
- Single cutover: no design for two writers.

**Scale/Scope**:
- **Volume:** about 13 vouchers a day across facilities. Supplier payments are new, so their volume is unknown, but well under 1,000 a month.
- **Routes:** 19.
- **New:**
  - `expense_voucher_service.py`;
  - `supplier_payment_service.py`, covering payments, applications and balances;
  - 2 endpoint modules;
  - `schemas/expense_vouchers.py` and `schemas/supplier_payments.py`;
  - 1 template.
- **Extended:**
  - `cash_session_service.py`: the close lock, and the cut figure;
  - `print_contexts.py`: the voucher context and the cut row;
  - `cash_cut.html`: the new row;
  - `purchase_orders.py` endpoint: `/payments`;
  - `enums.py`: incidence sources 14 and 15;
  - `models/supplier.py`: the new `SupplierPayment` columns;
  - `models/purchases.py`: `PurchaseOrderPayment` and `SupplierBalanceCutoff`.
- **Docs:** `05-purchases.md` §5–6, the data dictionary and the CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | Voucher states come from the existing flags. Balances are computed. One cutoff row. Not built: set cashier, voucher serials, bank accounts, supplier returns, a payment receipt, AP reports. | PASS |
| II. Think Before Coding | Six clarifications are recorded. The data was measured before deciding: legacy payments, bank accounts, the $335M of unpaid POs, voucher sessions. | PASS |
| III. Surgical Changes | Existing code changes in three places: `close_session` re-reads its row under a lock; `cut_figures` adds one figure, and the cut ticket one row; the PO router gains one read route. | PASS |
| IV. Goal-Driven | SC-001 to SC-006 map to tests or to recorded runs on the deployment (quickstart). | PASS |
| V. Reuse Over Rebuild | Reused: `open_session_for_cashier`, `assign_folio`, `busy_as_conflict`, `incidences.record`, `order_totals`, `derive_status`, `require_privilege`, `header_context`, `_ticket_layout.html`, and the existing voucher and category models. The new services and tables are justified below. | PASS, see Complexity Tracking |
| VI. Async-First | Every route and service is async. | PASS |
| VII. Security by Default | Every route is gated on 82 or 21. The voucher facility rule and creator rule apply. | PASS |
| VIII. Ruff | The gate runs before each commit. | PASS |
| Testing | Tests are written first for every service branch and route. | PASS |
| Changelog | `[Unreleased]`: Added (vouchers, supplier payments, balances, migration 029); Changed (the cash cut subtracts cash supplier payments; a session's close waits for in-flight vouchers and payments). | PASS |

Post-design: no new violations.

## Project Structure

### Documentation (this feature)

```text
specs/032-expense-supplier-payments/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/payments.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 029_supplier_payments.sql                     # NEW (confirm before applying)
└── 029_supplier_payments_rollback.sql            # NEW
app/
├── enums.py                                      # + SourceType 14, 15
├── api/v1/router.py                              # + 2 routers
├── api/v1/endpoints/expense_vouchers.py          # NEW
├── api/v1/endpoints/supplier_payments.py         # NEW
├── api/v1/endpoints/purchase_orders.py           # + GET /{id}/payments
├── models/supplier.py                            # SupplierPayment: + facility, serial, cash_session, cancelled, audit
├── models/purchases.py                           # + PurchaseOrderPayment, SupplierBalanceCutoff
├── schemas/expense_vouchers.py                   # NEW
├── schemas/supplier_payments.py                  # NEW
├── rendering/templates/expense_voucher_ticket.html # NEW
├── rendering/templates/cash_cut.html             # + supplier payments row
└── services/
    ├── expense_voucher_service.py                # NEW: drafts, lines, confirm, cancel, void, list
    ├── supplier_payment_service.py               # NEW: create, apply, reverse, cancel, balances, list
    ├── cash_session_service.py                   # close locks its row; + lock_open_session; cut_figures + supplier_payments_total
    └── print_contexts.py                         # + expense_voucher_context; cut row
tests/
├── unit/test_expense_vouchers.py                 # NEW
├── unit/test_supplier_payments.py                # NEW
├── unit/test_cash_cut.py                         # + supplier payments in the figures
├── integration/test_expense_voucher_flow.py      # NEW
├── integration/test_supplier_payment_flow.py     # NEW
├── api/test_expense_vouchers.py                  # NEW
├── api/test_supplier_payments.py                 # NEW
└── api/test_cash_sessions.py                     # + close and cut with vouchers and payments
docs/specs/05-purchases.md
docs/data-dictionary.md
CHANGELOG.md
```

**Structure Decision**: the existing layout, with one endpoint module per resource. The supplier summary sits under `/supplier-payments/suppliers/{id}/balance`, and the PO balance under `/purchase-orders/{id}/payments`; both are gated on 21 Read.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Migration 029 | FR-013 needs a serial and a facility; FR-012 a session link; FR-016 a cancel flag; FR-014 and FR-015 need applications that are kept; FR-020 a cutoff. | The legacy table has none of these. An incidence can't hold an application that has to be summed. |
| `purchase_order_payment` table | Partial applications of one payment to many POs, reversible and summed for balances (FR-014, FR-015, FR-018). | A PO column for the paid amount would lose which payment paid what, and couldn't be reversed per application. |
| `supplier_balance_cutoff` table | The cutoff is fixed by migration and read by every balance (clarification Q3). | An environment variable could drift between the instances that share mbe_dev; a code constant needs a deploy to change. |
| `expense_voucher_service.py`, `supplier_payment_service.py` | Two documents with their own states, rights and locks; nothing serves them today. | `customer_payment_service` has no locks, no cancel and no balance checks (spec, Findings), and changing it is out of scope. |
| `schemas/expense_vouchers.py`, `schemas/supplier_payments.py` | Request validation (amounts with 2 decimals, distinct POs, reasons) and response shapes for two new resources (contracts/payments.md). | No existing schema describes vouchers, supplier payments, applications or balances; `schemas/customer_payment.py` has a different shape (no serial per facility, no applications kept, no cancel). |
