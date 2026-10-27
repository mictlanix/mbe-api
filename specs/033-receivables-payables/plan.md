# Implementation Plan: Accounts Receivable and Accounts Payable

**Branch**: `033-receivables-payables` | **Date**: 2026-10-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/033-receivables-payables/spec.md`

## Summary

- **One customer balance** (#77, the core).
  - What changes:
    - `sales_order_service.order_balances` becomes the only customer-side balance rule: total − completed refunds − the order's share of live fiscal credit notes − live applications (R1, R2).
    - Order lists, the order detail, the credit check, the outstanding-orders list, `paid` maintenance, the AR view and statements all read it.
    - The credit hold counts orders that are overdue *by balance*. Orders flagged paid stay closed (R5).
  - **`paid` maintenance:**
    - set when the balance reaches 0.01 or less;
    - cleared only when the balance crosses back above it (R3).
  - Applying a customer payment now locks payment → order, and refuses an amount above the order's balance or an order already settled (R4).
  - A payment's unapplied amount subtracts the change handed back, as the ticket (#275) and the current system do. Customer credit, payment responses, the apply check and the ticket all read that one figure (R14).
- **Shared views** (#77, #79):
  - `/accounts-receivable` (18) and `/accounts-payable` (19): summary, drill-down, statement PDF and statement email, 4 routes each.
  - Both are built from one side-agnostic service. The side supplies its open documents, credit and party data (R5–R8).
  - **Scope:** AR follows the caller's facility, or all facilities with SearchCreditsFromAllStores (101); AP is company-wide.
  - **Supplier side:** reuses spec 032's balances. It shows nothing until go-live sets the cutoff. One change: `supplier_balance` reads the shared open-PO list, so a PO with exactly 0.01 left no longer appears (spec 032 listed anything above 0).
- **Statements:** one template for both sides, always covering all facilities. They're emailed through spec 024's mailer, with an incidence on the party: source 7, or the new source 16 for suppliers (R10).
- **Stale flags:** a one-time job, `settle_paid_flags`, sets `paid` on the 257 settled-but-unflagged orders. Only orders from 2026 on get a zero time (R11).
- **No schema change** (R12).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, and the existing rendering (WeasyPrint) and mail modules. No new dependency.

**Storage**: MariaDB `mbe_dev`, REPEATABLE READ. No migration. Data written only by the R11 job, after confirmation.

**Testing**: pytest with httpx `ASGITransport`.
- **Unit:**
  - `order_balances` with refunds, credit notes (traced, prorated and rounding) and the floor;
  - the crossing rule of `settle` and `reopen`;
  - aging and buckets;
  - the party figures (owed, overdue, credit, net, utilization, "no limit");
  - due dates from receipts;
  - the statement context;
  - email recipients;
  - the job's selection and zero time.
- **Integration (SQLite, through the `client` fixture where a route is involved):** `order_balances`; apply → paid → reverse → reopen; a legacy paid order not reopening; a credit note settling an order; the credit check reading balances; outstanding orders; the AR and AP views' figures and scope; statements and email; the job.
- **API (service mocked, as `tests/api` does):** the route contract of the 8 new routes (401, 403, 404, 422, 502, 503), and the scope and parameters each route passes, with and without 101.
- **On the deployment:** concurrency (SC-005) and the before/after measurements (SC-001 to SC-003), after confirmation (quickstart).

**Target Platform**: Linux server (xolotl)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: the AR summary of every customer in under 2 s; a drill-down or statement in under 1 s (SC-008). The candidate set is the 863 credit orders not flagged paid, not the 30,247 credit orders.

**Constraints**:
- **Lock orders:**
  - payment → order, for customer applications;
  - invoices → orders ascending, for a credit note's confirm;
  - spec 032's order on the supplier side, unchanged;
  - reads only after the last lock.
- One-time data change by job only, never by migration (R11).
- Single cutover: no design for two writers.

**Scale/Scope**:
- **Routes:** 8 new. 3 write routes change: customer apply, reverse, and credit-note confirm. 4 read shapes change: outstanding orders, customer payment responses, the payment ticket, and order `balance`.
- **New:**
  - `services/accounts_service.py`: the shared views and statements;
  - `services/account_email.py`;
  - `api/v1/endpoints/accounts_receivable.py` and `accounts_payable.py`;
  - `schemas/accounts.py`;
  - `rendering/templates/account_statement.html`;
  - `jobs/settle_paid_flags.py`.
- **Extended:**
  - `sales_order_service.py`: `order_balances`, plus the credit check reading it;
  - `customer_payment_service.py`: `unapplied` subtracts change; locks, guards, `settle` and `reopen`; `search_outstanding`;
  - `customer_refund_service.py`: settle on confirm;
  - `invoice_service.py`: settle on a credit note's confirm;
  - `supplier_payment_service.py`: batch open POs, due dates;
  - `print_contexts.py`: statement context; the payment ticket reads `unapplied_amount` (#275, merged from main);
  - `enums.py`: `SourceType.SUPPLIER`.
- **Docs:** `07-administration.md` §1–2, and the CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | Balances are computed, not stored. One service serves both sides. Existing apply routes are reused. No migration. **Not built:** supplier returns, remittances, enforcement of supplier limits, legacy cleanup beyond FR-012, debt reports. | PASS |
| II. Think Before Coding | Four clarifications are recorded. Measured before deciding: the $120.2M on paid orders, change on 38,523 applications ($18.77M of credit, not $24.87M), the 257 stale flags, credit-note line tracing (1 of 115 lines), cross-facility applications (14%), privilege holders, and POs without receipts. | PASS |
| III. Surgical Changes | The changes to existing code are what FR-001 requires: four balance copies become one call; `_sync_paid_flag` becomes `settle`/`reopen`; the apply path gains locks and two guards; refund and credit-note confirms each gain one `settle` call; the stale docstrings of the credit check are rewritten. | PASS |
| IV. Goal-Driven | SC-001 to SC-008 map to tests or to recorded runs (quickstart). | PASS |
| V. Reuse Over Rebuild | **Reused:** `totals.remaining`, `refunded_by_order`, `fiscal_document_service.live`, `invoice_settlements`' note totals, spec 032's `po_counts`/`_po_totals`/`payment_credit`, `require_privilege`, `has_any_privilege`, `busy_as_conflict`, `incidences.record`, `render_pdf`, `header_context`, `_print_layout.html`, `get_mailer`, and quotes' email flow and recipient validation. The new modules are justified below. | PASS, see Complexity Tracking |
| VI. Async-First | Every route, service and the job are async. | PASS |
| VII. Security by Default | Every new route is gated on 18 or 19. AR scope is enforced server-side; the current system has no server-side check at all. Statements need read on the side, and the clarified scope is all facilities. | PASS |
| VIII. Ruff | The gate runs before each commit. | PASS |
| Testing | Tests are written first for every service branch and route. | PASS |
| Changelog | `[Unreleased]`: Added (AR and AP views, statements, `settle_paid_flags`); Fixed (a payment's `unapplied` subtracts change handed back); Changed (the credit hold and limit read the shared balance; balances deduct credit notes; applications are locked and capped at the order balance; `paid` is cleared only on a crossing). | PASS |

Post-design: no new violations.

## Project Structure

### Documentation (this feature)

```text
specs/033-receivables-payables/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/accounts.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── enums.py                                      # + SourceType.SUPPLIER = 16
├── api/v1/router.py                              # + 2 routers
├── api/v1/endpoints/accounts_receivable.py       # NEW: summary, detail, statement, email (18; 101 scope)
├── api/v1/endpoints/accounts_payable.py          # NEW: the same 4 routes (19, company-wide)
├── schemas/accounts.py                           # NEW: PartySummary, OpenDocument, PartyDetail, StatementEmailRequest
├── rendering/templates/account_statement.html    # NEW: one template, side as variable
├── jobs/settle_paid_flags.py                     # NEW: one-time correction, dry run by default
└── services/
    ├── accounts_service.py                       # NEW: side adapters, aging, party figures, scope, statement data
    ├── account_email.py                          # NEW: render, send, record
    ├── sales_order_service.py                    # + credit_note_shares, order_balances, open_credit_orders; attach_* and credit check read them; docstrings
    ├── customer_payment_service.py               # unapplied − change; apply/reverse lock + guards; settle/reopen; search_outstanding;
    │                                             #   customer_payments_with_credit; settle_stale_flags (the job's logic)
    ├── customer_refund_service.py                # confirm_refund calls settle
    ├── invoice_service.py                        # credit note confirm locks orders, calls settle
    ├── supplier_payment_service.py               # + open_purchase_orders (batch, due dates, receipts), credit_by_supplier
    └── print_contexts.py                         # + account_statement_context
tests/
├── unit/test_order_balances.py                   # NEW: credit_note_shares
├── unit/test_accounts.py                         # NEW: aging, buckets, figures, PO due dates
├── unit/test_account_email.py                    # NEW: merge_recipients
├── unit/test_settle_paid_flags.py                # NEW: stale_zero_time
├── unit/test_customer_payment_service.py         # + settle/reopen crossing, guards
├── integration/test_order_balances.py            # NEW: order_balances, open_credit_orders
├── integration/test_outstanding_orders.py        # NEW
├── integration/test_accounts_receivable_flow.py  # NEW: scope, figures, drill-down
├── integration/test_accounts_payable_flow.py     # NEW: equals spec 032's balance
├── integration/test_account_statement.py         # NEW
├── integration/test_account_email.py             # NEW
├── integration/test_settle_paid_flags.py         # NEW
├── integration/test_payment_flow.py              # + unapplied − change, settle/reopen, refusals
├── integration/test_credit_hold.py               # + hold by balance, limit with credit notes
├── integration/test_credit_note_flow.py          # + a credit note settles an order
├── integration/test_refunded_balance.py          # + a refund settles an order
├── integration/test_supplier_payment_flow.py     # + open_purchase_orders, due dates
├── api/test_accounts_receivable.py               # NEW: route contract (mocked service)
├── api/test_accounts_payable.py                  # NEW: route contract (mocked service)
└── api/test_customer_payments.py                 # + a service 409 reaches the caller
docs/specs/07-administration.md                   # §1–2 rewritten for the API
CHANGELOG.md
```

**Structure Decision**: the existing single-project layout. The shared model is one service module, `accounts_service.py`. It holds a small `Side` description (privilege, incidence source, labels, and the functions that load open documents, credit and parties) and two instances, `CUSTOMERS` and `SUPPLIERS`. Both routers call the same functions with their side.

## Complexity Tracking

| New piece | Why needed | Simpler alternative rejected because |
|---|---|---|
| `accounts_service.py` | The views, aging, party figures and statement data are new and shared by both sides (the spec's symmetry requirement) | Putting them in `customer_payment_service` and `supplier_payment_service` would duplicate the aging, the figures and the statement data per side |
| `account_email.py` | The statement send flow for both sides | Copying `email_quote` per side would mean two copies; quotes' flow is reused through its validators and mail module |
| `jobs/settle_paid_flags.py` | A one-time data fix needing the Python balance rule | A SQL migration can't reproduce line rounding, prorated credit notes or XML note totals (R11) |
| Two endpoint modules | One per privilege and path, as every resource in the API has | One parametrized router would hide the two paths' different scope rules |
