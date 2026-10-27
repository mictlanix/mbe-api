# Research: Expense Vouchers and Supplier Payments

All measurements are from mbe_dev (see spec, Findings). "Locking pattern" means spec 029's:
- plain pre-reads;
- a commit that ends the request's opening transaction;
- locking reads only;
- then ordinary reads, whose REPEATABLE READ snapshot opens after the last lock and so sees what earlier lock holders committed.

## R1. Voucher states on the existing flags

**Decision**: derive the state from `completed` and `cancelled`, with NULL counted as false, and store nothing new.

| State | `completed` | `cancelled` |
|---|---|---|
| `draft` | 0 | 0 |
| `completed` (confirmed) | 1 | 0 |
| `cancelled` (a cancelled draft, or a voided voucher) | any | 1 |

The wire values are `draft`, `completed` and `cancelled`, as for receipts and POs (`derive_status`). A void keeps `completed = 1` and sets `cancelled = 1`, so a voided voucher is told apart from a cancelled draft by `completed`, and the void's incidence holds the reason.

**Rationale**: no row has both flags set today; `cut_figures` already reads `completed AND NOT cancelled`, so it needs no change for vouchers.

**Alternatives considered**: a status column. It needs a backfill, and it duplicates flags the cut already reads.

## R2. Which session, and serializing with close

**Decision**:
- **The session** is the caller's open session: `cash_session_service.open_session_for_cashier`, the newest with `end IS NULL`. A stale session (opened on an earlier day) counts as open, as for customer refunds.
- **Every drawer operation locks the session row** `FOR UPDATE` and re-reads it (`populate_existing`) before anything else is locked, then checks `end IS NULL`:
  - voucher confirm and void;
  - cash supplier payment create and cancel.
- **`close_session` locks the same row** and re-checks `end` under the lock before writing the counts. Today it checks `end` on the row the endpoint read without a lock; that change is the only one to spec 011's close.

Whichever runs first wins:
- an operation that holds the session lock commits, and the close then counts with it;
- a close that holds the lock sets `end`, and the operation re-reads it under the lock and answers 409 "The cash session is closed".

So a closed session's figures never change (FR-005, SC-002). The lock is the session row only. Nothing else locks it today, so it adds no deadlock with sales.

**Alternatives considered**:
- Checking `end` without a lock leaves a window between the check and the commit, during which a close can complete.
- Locking the drawer row instead works as well, but the session is the row whose `end` decides.

## R3. Voucher confirm, void and edits

**Decision**:
- **Create** pre-reads the caller's open session and refuses with 409 "An open cash session is required" without one. The draft takes the session, the session's drawer's facility, `creator = updater =` the caller, and `date = creation_time = now`. A draft does not affect the cut, so create takes no lock.
- **Edits** lock the voucher row and require a draft whose creator is the caller (FR-002).
- **Confirm** follows the locking pattern:
  1. **Pre-read:** the caller's open session id. Commit.
  2. **Lock** the session row and check that it is open and the caller's, then lock the voucher row and check that it is a draft created by the caller.
  3. **Read** the lines and require a total above 0.
  4. **Set** `cash_session` and `facility` (from the session's drawer), `date = now`, `completed`, `updater` and `modification_time`.
- **Void:**
  1. **Pre-read** the voucher's `cash_session`, then commit.
  2. **Lock** that session row, then the voucher row. Re-check that the voucher is still in that session, confirmed and not cancelled, and that the session is open.
  3. **Write:** set `cancelled` and write a source-14 incidence with the reason ("Expense voucher voided").
- **Cancelling a draft** locks the voucher row only. The caller must be its creator.

**`date` is set on confirm.** The ticket and the lists then show when the cash left the drawer. 16 legacy vouchers were confirmed into a later session than the one they were created in, and showed the earlier date.

**Rationale**: the voucher's session moves on confirm (FR-003), so confirm locks the caller's session, not the draft's. Void locks the voucher's own session, which can't change once the voucher is confirmed.

**Alternatives considered**: keeping the creation date, which is what legacy does. It misdates a voucher confirmed in a later session.

## R4. Supplier payment numbering, and lock order

**Decision**: one global lock order for every path that writes supplier payments or their applications:

**session row → payment row → PO rows (ascending) → facility row (numbering)**

- **Create** (one transaction, applications optional):
  1. Pre-reads: the supplier, the caller's session for cash, and the POs named. Commit.
  2. Lock the session row (cash only) and check it is open.
  3. Lock the named PO rows, ascending.
  4. Number: `documents.assign_folio(SupplierPayment, facility=...)`, which locks the facility row and reads `MAX(serial)`.
  5. Ordinary reads: each PO's lines and live applications. Check the PO state, supplier, cutoff and balance.
  6. Insert the payment and its applications. Commit.
- **Apply:** lock the payment row, then the PO rows ascending, then do the ordinary reads (the payment's live applications, each PO's lines and live applications). Check and insert.
- **Reverse an application:** lock the payment row, then that application's PO row.
- **Cancel:**
  1. Lock the session row (cash payments only), then the payment row, then the PO rows of its live applications, ascending.
  2. Reverse every live application, set `cancelled`, and write the incidence.

**Rationale**:
- **Facility lock last.** `assign_folio`'s `MAX` is an ordinary read, so it must come after every lock. Taking the facility last keeps it the last lock.
- **No cycle.** Nothing in the codebase takes a facility row and then a PO, payment or session row. Sales orders, refunds and deliveries lock only the facility.
- **Credit and balance stay at 0 or above, even concurrently (FR-017, SC-003).** Two applications of one payment queue on the payment row, and two payments applied to one PO queue on the PO row. Spec 031 locks PO rows for edits and confirm too, so a confirm and an application on one PO also queue.
- **Errors under contention.** Deadlocks and lock-wait timeouts are mapped to 409 by `inv.busy_as_conflict`.

**Alternatives considered**: `inventory_documents.next_serial` under the transit-warehouse lock. Supplier payments touch no warehouse, and `assign_folio` is the facility-row lock spec 027 uses for documents without one.

## R5. Facility of a payment

**Decision**:
- **Cash payment:** the facility of the session's drawer.
- **Other methods:** the caller's facility (the token's `facility_id`). An administrator without one sends `facility` in the body; a body `facility` from a non-administrator is refused with 422.

**Rationale**: the serial is per facility (FR-013), and POs carry no facility to borrow.

## R6. The balance cutoff

**Decision**:
- **Storage:** a one-row table `supplier_balance_cutoff (supplier_balance_cutoff_id = 1, cutoff DATETIME NULL)`. Migration 029 creates it with `cutoff` NULL. The go-live migration (a later numbered file, written at go-live) sets the date. No route reads or writes it except through the balance rules.
- **Which POs count:** a PO counts when it is `completed`, not cancelled, and `modification_time >= cutoff`. Spec 031 refuses every write to a confirmed PO, so a confirmed PO's `modification_time` is its confirmation time.
- **NULL cutoff:** no PO counts. Applications are refused with 409 "Balances start at the cutoff, which is not set yet", and payments are all credit.

**Rationale**:
- The user chose a date fixed by migration (clarification Q3).
- A table, not an environment variable, keeps the date with the data it governs, so every instance on mbe_dev agrees.
- Legacy POs edited after confirm all predate the cutover, so their `modification_time` is before the cutoff too.

**Alternatives considered**:
- A generic key/value settings table. Nothing else needs one yet.
- Adding a `confirmation_time` column to `purchase_order`. It needs a backfill, and it duplicates `modification_time` for confirmed POs.

## R7. Balances and credit, computed

**Decision**: store no balance. Compute it.
- **PO total:** `purchase_order_service.order_totals`, the spec 031 rule: each line's net price, with tax added per line.
- **PO balance:** the total minus the sum of the PO's live applications.
- **Payment credit:** the amount minus the sum of its live applications. It is 0 for a cancelled payment, and 0 for a legacy payment (`facility IS NULL`, clarification Q1).
- **Supplier summary:**
  - the counting POs with a balance above 0;
  - the supplier's payments with credit above 0;
  - `owed` (the sum of PO balances), `credit` (the sum of payment credit), and `net = owed − credit`.

  It is two grouped queries plus one lines query over the counting POs.
- **Over-payment can't happen:** every application is at most the PO's balance, and confirmed POs can't change. So a PO balance below 0 means corrupt data, not a state to handle.

**Rationale**: customer payments compute their unapplied amount the same way. A stored balance would need the same locks to stay right, and would add a column to keep in sync.

## R8. Cash supplier payments in the cut

**Decision**:
- `CashCutFigures` gains `supplier_payments_total`: the non-cancelled supplier payments of the session with `method = CASH`.
- `cash_in_drawer = starting + cash_sales − expenses − supplier_payments − cash_refunds`.
- The cut ticket adds a "Pagos a proveedores" row after "Gastos" when the figure is above 0.
- The session's `expected_cash` follows automatically, since it is read from the cut.

**Rationale**: FR-012 calls for a separate figure. Only cash payments have a session (FR-011).

## R9. Incidences

**Decision**: two new API-only incidence sources, following 10 to 13:

| Source | What | `comment` | `content` |
|---|---|---|---|
| 14 `EXPENSE_VOUCHER` | a voucher void | reason | "Expense voucher voided" |
| 15 `SUPPLIER_PAYMENT` | an application reversed | reason | "Application to purchase order {id} reversed: {amount}" |
| 15 `SUPPLIER_PAYMENT` | a payment cancelled | reason | "Supplier payment cancelled" |

All are written with `incidences.record`, in the same transaction as the change.

## R10. Voucher ticket

**Decision**:
- **Template:** `expense_voucher_ticket.html` on `_ticket_layout.html`. The context is `print_contexts.expense_voucher_context`, built from `header_context` like the cash cut.
- **Content:** the voucher number (`pad8` of the id), the cashier's name, the date, each line's category, note and amount, the total, and the "Entregó" and "Recibió" signature lines.
- **Route:** `GET /expense-vouchers/{id}/ticket`, on 82 Read, following `GET /cash-sessions/{id}/ticket`. It answers 409 for a draft or cancelled voucher.

## R11. Migration 029

**Decision**: one idempotent migration, `029_supplier_payments.sql`, with a rollback, in the style of 025 to 028.
- **`supplier_payment`** gains these columns, all nullable for the 5 legacy rows except `cancelled`:
  - `facility` (FK) and `serial`, with `UNIQUE (facility, serial)`; MariaDB allows repeated NULLs, so legacy rows don't collide;
  - `cash_session` (FK, indexed);
  - `cancelled TINYINT(1) NOT NULL DEFAULT 0`;
  - `creation_time`, `updater` (FK) and `modification_time`.
  - **Backfill:** `creation_time = modification_time = date`, `updater = creator`.
- **New table `purchase_order_payment`:**
  - `purchase_order_payment_id`;
  - `supplier_payment` (FK) and `purchase_order` (FK), both indexed;
  - `amount DECIMAL(18,2)`;
  - `applier` (FK employee) and `date`;
  - `cancelled TINYINT(1) NOT NULL DEFAULT 0`.
- **New table `supplier_balance_cutoff`:** one row, with `cutoff` NULL.
- **Expense vouchers:** no change.

**Rationale**: the table shape follows `sales_order_payment`, without `amount_change` or `confirmed`, which supplier payments don't use. `UNIQUE (facility, serial)` backs up R4's lock, as for spec 030's counts.

## R12. Amounts

**Decision**: voucher line amounts, payment amounts and application amounts are 2-decimal values above 0, with a maximum of 99,999,999.99. PO totals are already rounded to cents (`document_totals`). A payment's `amount` column is `DECIMAL(18,4)`, but the API writes 2 decimals.

**Rationale**: these are money moved by hand; cents are the unit. The largest voucher line is $50,000.

## R13. What changes in existing code

- **`cash_session_service`:**
  - `close_session` locks and re-reads its row (R2);
  - `cut_figures` adds the supplier-payment figure (R8);
  - the cut ticket gains a row.
- **`purchase_order_service`:** no change. Balances call `order_totals`.
- **`enums.SourceType`:** adds 14 and 15.
- **Models:** `SupplierPayment`, `PurchaseOrderPayment` and `SupplierBalanceCutoff` are new, in `models/purchases.py`. The voucher models exist.
- **Behaviour elsewhere is unchanged.**
