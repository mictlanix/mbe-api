# Research: Accounts Receivable and Accounts Payable

All figures are from mbe_dev, read-only, on 2026-10-27.

## R1. One balance function per side

**Decision:**
- **Customer side:** one function, `sales_order_service.order_balances(db, orders)`. It returns each order's total, refunded, credited, applied and balance, in batched queries. Every customer-side reader goes through it:
  - `attach_summary_totals` (order lists) and `attach_derived` (one order). Both compute balance inline today, at lines 310 and 426.
  - `_customer_credit_debt` and `_overdue_credit_orders` (the credit check).
  - `customer_payment_service.search_outstanding`, which today computes the balance itself, one order at a time.
  - `_sync_paid_flag`, which today compares applications with the total, ignoring refunds.
  - The new AR view and the statement.
- **Supplier side:** keep spec 032's `supplier_payment_service` functions (`_po_totals`, `_balances`, `po_counts`) as the one rule. Supplier returns enter as a deduction term that is always 0.

**Rationale:** the arithmetic already agrees in these places, but it lives in four copies, and the copies drift: `_sync_paid_flag` doesn't know about refunds. One function is what FR-001 asks for. On the supplier side spec 032 already has one function, so nothing needs to change there.

**Alternatives considered:** a stored balance column, kept in sync by every writer. Rejected: there are many writers (applications, refunds, credit notes, and CFDI cancellation later), and each would be a place to forget. The balance stays computed, as spec 032's does.

## R2. Credit notes against an order's invoices

**Decision:** for each order, `credited` is the sum over the live credit notes (type 100, `fiscal_document_service.live`) related to any invoice that has a line of that order. For each note:
- a note line whose `order_detail` belongs to the order counts in full for that order;
- the rest of the note's total (the XML total, as `invoice_settlements` reads it, less the traced lines) is shared out to the invoice's orders in proportion to each order's part of the invoice's line total, rounded to cents;
- the last order takes the rounding remainder, so the shares add up to the note.

**Rationale:**
- Only 1 of the 115 live note lines keeps an order line, so prorating is what applies in practice. Tracing still wins when it's available.
- 6,961 invoices cover more than one order.
- Only 5 credit orders ever had a note, all of them flagged paid, so the before/after figures move by $0 (SC-002). The rule matters going forward.

**Alternatives considered:**
- Trace lines only: rejected, because 114 of 115 lines would be lost.
- Store the share on the relation (`fiscal_document_relation.amount`, today 0 for notes): rejected, because it would need a write path in spec 028's note flow for a figure that can be computed.

## R3. Keeping `paid` consistent (FR-011, clarified)

**Decision:** two helpers in `customer_payment_service`, called under the order's row lock (R4):
- `settle(order, balance)` runs after an event that lowers the balance: an application, the completion of a refund, or the confirmation of a credit note (it locks orders on its invoices).
  - If the balance is 0.01 or less, it sets `paid`.
  - It stamps `balance_zeroed_time` if it's unset or holds the sentinel; it is never cleared, as today.
  - It never clears `paid`.
- `reopen(order, before, after)` runs after an event that raises the balance: the reversal of an application, and later the cancellation of a credit note (mictlanix/mbe-roadmap#51).
  - It clears `paid` only when `before <= 0.01 < after`.
  - `before` is read under the lock, before the change.

**Events in this API today:**
- **Applications and reversals:** both exist.
- **Refunds:** a completed refund can't be cancelled (FR-066), and a refund requires a paid order (FR-060). So completion only ever lowers the balance of an order that's already paid, and `settle` is a no-op there. It's called anyway, so the rule doesn't depend on FR-060 staying as it is.
- **Credit notes:** confirming one lowers the balance of every order on its invoice. CFDI cancellation isn't built (spec 028 leaves it to #51). The task list adds a note to #51 that its cancel path must call `reopen`.

**Rationale:** the crossing rule is the clarified answer. With it, a legacy paid order without applications stays paid whatever happens on it. Today, an application to such an order runs `covers_total(applied, total)` and *clears* `paid`, which is the same reopening bug in another form.

## R4. Locking for customer applications (FR-022)

**Decision:** `apply_payment` and `reverse_application` lock in a fixed order:
1. the payment row (`SELECT … FOR UPDATE`, `populate_existing`);
2. then the order row.

After both locks they read `unapplied_amount` and `order_balances` and check them. The credit note's `confirm` already locks its invoices (spec 028, R2); it then locks the related orders in ascending id, before `settle`. Deadlocks map to 409 through `inv.busy_as_conflict`, as in spec 032. The apply check also refuses an order that is flagged paid or has a balance of 0.01 or less (FR-021).

**Rationale:** this is spec 032's pattern: payment → documents in ascending order, with reads only after the last lock. The two paths that lock orders take them after their own parent row (payment, or invoices), and neither locks the other's parent. So they can't form a cycle.

**Alternatives considered:**
- An optimistic re-check after the insert: rejected, because under REPEATABLE READ it doesn't see a concurrent insert.
- A per-customer advisory lock: rejected as a second mechanism next to spec 032's row locks.

## R5. What "open" means on each side

**Customer side:** an order is open when it's a credit order (`payment_terms` NET_D), completed, not cancelled, not flagged paid (clarified), and its balance is above 0.01.
- The candidate query filters on the first four. 863 rows today: 606 with a balance and 257 without.
- `order_balances` then drops those at 0.01 or less.

**Supplier side:** spec 032's `po_counts` (confirmed, not cancelled, `modification_time >= cutoff`) with a balance above 0.01. Nothing counts while the cutoff is NULL.

**Rationale:** the flag filter keeps the 16,405 legacy paid orders, $120.2M, out. It also makes the candidate set small, so the views compute from a few hundred orders instead of 30,000.

## R6. Due dates and aging

- **Customer:** `sales_order.due_date`.
- **Supplier:** the earliest `modification_time` among the PO's receipts that are completed and not cancelled, plus `supplier.credit_days` days (0 when null). A receipt's confirmation stamps `modification_time` (`inventory_documents.confirm`, through `_touch`), and a confirmed receipt isn't edited afterwards. If that receipt is cancelled, the next confirmed one dates the PO.
- **POs without a confirmed receipt:** no due date. 311 of the 1,750 POs confirmed in the last 12 months have none.
- **Days past due** = `today − due_date` in local dates. With no due date, or a due date of today or later, the document is "current". The buckets are 1–30, 31–60, 61–90 and >90.
- The buckets add up to owed by construction: each document lands in exactly one.

## R7. Party figures and credit

- **Owed and overdue:** sums of open documents, in base currency at each document's `exchange_rate`. Supplier POs carry no rate and are all MXN, so their rate is 1.
- **Customer credit:** the sum, over the customer's payments, of the unapplied amount (R14), `amount − Σ(applied + change)` over live applications, where it's above 0.01. It's one grouped query per page of customers.
  - 316 customers hold $18.77M today. Without subtracting change it would be 1,750 customers and $24.87M.
  - 39 of the 316 owe on an open order, and hold $4.37M of that credit.
  - The 74 negative ones (cash refunds, −$516k) are left out by the positive filter.
- **Supplier credit:** spec 032's `payment_credit`.
- **Net** = owed − credit.
- **Utilization and available credit** use the party's owed across the whole company, even when the caller is scoped (FR-015). They're shown only when the limit is above 0.
- **Walk-in customer:** listed (127 orders, $205,543), with limit 0, so it shows "no limit".

## R8. Facility scope

- **Customer side:** filter the open orders by `sales_order.facility == current.facility_id`, unless the caller is an administrator or holds read on SEARCH_CREDITS_FROM_ALL_STORES (101). That second check is inline, using the existing pattern (`has_any_privilege`, as `fiscal_documents.py:249`). A caller with 101 may pass `facility=` to narrow the view.
- **Supplier side:** no facility filter, and 101 isn't read (FR-019).
- **Statements:** always all facilities (clarified).
- **Effect on current users:** 12 of the 23 users who can read 18 can't read 101.

## R9. Applying from the view (FR-020, FR-021)

**Decision:** no new apply route. The view calls the existing `POST /customer-payments/{id}/applications` and `POST /supplier-payments/{id}/applications`, under their own privileges (CustomerPayments create; SupplierPayment update). The customer route gains the balance check (R4) and the refusal of a settled order.

The drill-down lists the payments that can be applied: those with credit above 0.01, of the same party and in the same currency.
- **Which facilities' payments the list shows:** #271's rule. That's the caller's facility, unless they're an administrator or hold PaymentsEditor read (100), the same rule as `GET /customer-payments`. Showing other facilities' payments in the drill-down would undo #271.
- **The party's credit figure** stays company-wide: it's one number, and the statement shows it in full.
- **The apply route** itself still takes any facility's payment by id (`_payment_or_404`, not `_visible_payment_or_404`), as #271 left it.

**Rationale:**
- One path to an application means one place for the guards.
- 816 of the 5,646 credit-order applications in 2026 (14%) used another facility's payment, so the payment route can't refuse cross-facility orders. The spec's FR-021 was narrowed accordingly.

## R10. Statement (FR-023 to FR-025)

**Decision:**
- One template, `account_statement.html`, on `_print_layout.html`, rendered from `print_contexts.account_statement_context(db, side, party)`.
- The context is built from the same party function the view uses, called with all facilities.
- Recent payments are those dated within the 90 days before the statement date, with:
  - for a customer: serial, method, amount, applied and unapplied;
  - for a supplier: serial, method and amount, plus applied and credit from spec 032.
- Wording comes from the side ("Cliente" or "Proveedor", "Facturas" or "Recepciones"); the sales agent appears only for customers.

**Email:**
- `account_email.email_statement(db, side, party, to, contact_ids, mailer, current)` follows `email_quote`'s order: render, send, then record.
- Recipients are the chosen contacts' email addresses plus typed ones, deduplicated, 1–10 in total. A chosen contact must belong to the party and have an address, or the request is refused (422).
- **Incidence:** source CUSTOMER (7) or a new **SUPPLIER (16)**, `instance_id` = the party, reason "Account statement emailed", context "To: …".
- With no mailer configured: 503, nothing recorded.

**Subject and body:** "{company} Estado de cuenta {party}". The body lists owed and overdue, and says the PDF is attached.

## R11. One-time correction of stale flags (FR-012)

**Decision:** a job, `app/jobs/settle_paid_flags.py` (`python -m app.jobs.settle_paid_flags [--apply]`).
- **Default is a dry run** that prints the orders it would change.
- **Selection:** credit orders, completed, not cancelled, not flagged paid, with an `order_balances` balance of 0.01 or less (257 today: 3 from 2024, 219 from 2025, 35 from 2026).
- **For each order:**
  - set `paid`;
  - if the order is dated 2026 or later, set `balance_zeroed_time` to the latest of its live applications' `date`, its completed refunds' `date`, and its credit notes' `issued`;
  - otherwise leave `balance_zeroed_time` unset;
  - record one SALES_ORDER (3) incidence, reason "Paid flag corrected: balance is settled (spec 033)".
- **Rerunnable:** a second run selects nothing.
- It runs on mbe_dev only after confirmation, and once more at cutover.

**Rationale:** a SQL migration (spec 009) can't reproduce the balance: line rounding, prorated credit notes, and note totals stored in XML. Migrations stay for schema. `expire_orders.py` is the precedent for a job module. The audit's decision 4 keeps pre-2026 orders out of commissions.

**Commission effect:** for the 35 orders from 2026, `_candidates` will pick up their lines in the next computed month, through the chain rule. That's intended: they were settled in 2026 and owe commission there.

## R12. No schema change

`SourceType.SUPPLIER = 16` is an enum value: `incidence.source` is a plain int, as spec 032's 14 and 15 were. The views, statements and job read existing tables, so there's no migration in this spec.

## R13. Before/after measurement (SC-002)

- **Before:** 606 open orders, $6,861,866.40, 136 customers, by today's arithmetic (quickstart step 1).
- **The request's $6.81M** came from an earlier reading. The difference, about $50k, is within normal day-to-day movement of a live copy. The quickstart re-measures both figures on the same day.
- **After:** the same query through `order_balances`. With credit notes affecting no unpaid order, owed must equal the before figure to the cent.
- **Holds:** 199 by flag, 130 by balance.
- **The current AR screen** is re-read with its own SQL (`AccountsReceivablesController.cs:80-121`). It's expected to differ for 3 customers, about $6.4k (rounding, and refunds that weren't completed).

## R14. Unapplied amount counts change handed back (FR-005a, clarified)

**Decision:** `customer_payment_service.unapplied_amount` and `attach_summary_unapplied` subtract `amount + amount_change` of each live application, not `amount` alone. Everything that reports or checks unapplied reads them:
- `unapplied` on payment responses;
- the apply check: an application's `amount + amount_change` must not exceed the unapplied amount. Today only `amount` is compared;
- the drill-down's payments that can be applied;
- the customer credit (R7);
- the payment ticket (#275): `print_contexts` computes `allocated = Σ(amount + amount_change)` itself. It switches to `unapplied_amount`, so the ticket and the API share one figure.

`origin/main` (#270, #271, #275) was merged into the branch on 2026-10-27, during the second plan run.

**Measured:**
- 38,523 live applications carry change. 432 of them are on credit payments (type 2) and the rest are on cash sales.
- 7,602 payments that show a positive `unapplied` today are 0 or less once change is subtracted.
- Example: payment 300789 is 600, with 530 applied and 70 change. It's 0 by the ticket's rule; `unapplied` reports 70 today.

**Rationale:** change is money the payment handed back, so it can't be applied again. The current system's `CustomerPayment.Balance` and the #275 ticket both subtract it. Two figures for one payment is the divergence this spec exists to remove.

**Not changed:** the order's balance. Change isn't money the order received, so `order_balances` subtracts `amount` only, as today. The apply check compares `amount`, not `amount + change`, with the order's balance.
