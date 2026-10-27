# Administration Specs

Covers receivables and payables tracking — the financial position view of outstanding customer balances and supplier obligations.

---

## 1. Accounts Receivable

**Route**: `/api/v1/accounts-receivable`
**SystemObject**: `AccountsReceivable` (18): Read for the view and the statement. `SearchCreditsFromAllStores` (101) widens the view to every facility
**Spec**: `specs/033-receivables-payables` (mictlanix/mbe-roadmap#77)

### Purpose
What customers owe on credit, aged, against their credit limits; and their account statement. Accounts payable (§2) is the same design seen from the supplier side: the aging, the party figures and the statement are written once.

### Open Orders
- **Open:** a credit order (`payment_terms = NetD`), completed, not cancelled, not flagged `paid`, with a balance above 0.01. The flag closes an order whatever it computes: the legacy paid orders (16,405 in mbe_dev computing $120.2M) stay out.
- **Order balance:** total, less completed non-cancelled refunds, less the order's share of live fiscal credit notes against its invoices, less live payment applications, floored at 0. One rule (`order_balances`) read by order lists and detail, the credit check, outstanding orders, this view and statements.
- **Credit notes:** a note line copied from an order's line counts in full for that order. The rest of the note's stamped total is shared by each order's part of the invoice, the last order taking the rounding remainder.
- **Due and overdue:** the order's `due_date`. Overdue means past due with a balance above 0.01, never the `paid` flag alone.

### Summary (`GET /accounts-receivable`)
- One row per customer with an open order in scope: owed, overdue, aging buckets (current, 1–30, 31–60, 61–90, over 90; they add up to owed), oldest order, credit, net, credit limit and days, utilization and available credit, sales agent (`customer.salesperson`).
- **Credit:** the customer's payments' unapplied amounts above 0.01: amount less applied amounts and the change handed back. **Net:** owed less credit.
- **Utilization and available credit** read what the customer owes across every facility, whatever the caller's scope. A limit of 0 or none is "no limit": both stay null.
- Filters `party`, `overdue`, `facility`, `sales_agent`; sort `owed`, `overdue` or `oldest`; `skip`/`limit` (up to 200). Base currency at each order's exchange rate.

### Drill-down (`GET /accounts-receivable/{customer_id}`)
- The open orders in scope, oldest due first: date, serial, due date, days past due, bucket, facility, salesperson, live invoices (batch and serial), total, deductions (refunds and credit notes), applied, balance.
- The payments that can be applied, with their unapplied amount. Another facility's payments only for an administrator or a PaymentsEditor (100) reader (#271).
- **Applying** goes through `POST /customer-payments/{id}/applications`, under its own privilege.

### Facility Scope
- The caller's facility; every facility for an administrator or a holder of 101, who may narrow it with `facility`. A scoped caller naming another facility gets 403.
- The current system checks nothing server-side and shows every facility to everyone: 12 of the 23 users who can read 18 can't read 101 and now see their own facility only.

### Statement
- `GET /{customer_id}/statement` (PDF, `estado-de-cuenta-{id}.pdf`), always covering every facility: open orders with aging, payments of the last 90 days (amount, applied, still to apply), limit, credit days and available credit, owed, overdue, credit and net.
- `POST /{customer_id}/statement/email` with `contacts` (the customer's, with an address) and `to` (typed), 1 to 10 recipients after duplicates. Through spec 024's mailer: 503 without one, 502 when the server refuses, and nothing recorded either way. Each send records one incidence on the customer (source 7) with the recipients.
- Read on 18 is enough; 101 isn't needed.

### Paid Flag
- **Set** when an application, a refund's completion or a credit note's confirmation brings the balance to 0.01 or less; `balance_zeroed_time` is stamped then, once (it dates the commission month). An order already flagged paid is left alone.
- **Cleared** only when an application's reversal or a credit note going back to draft takes the balance from 0.01 or less to above it. A legacy paid order never reopens.
- **Applications** lock the payment, then the order. One above the order's balance is refused (422), and one on an order flagged paid or owing 0.01 or less (409). An application's change counts against what the payment holds.
- **One-time correction:** `python -m app.jobs.settle_paid_flags [--apply]` flags paid the credit orders that owe nothing but are flagged unpaid (257 in mbe_dev), one incidence each (source 3). Only orders from 2026 on get a zero time.

### Current System
- A list of open credit orders, one row each, with an "apply payment" shortcut that never checked the payment's customer or currency. No aging, no utilization, and no statement (Print was a stub).
- Its balance counted refunds in any state and ignored credit notes; its credit hold counted orders flagged unpaid, whatever they owed.

---

## 2. Accounts Payable

**Route**: `/api/v1/accounts-payable`
**SystemObject**: `AccountsPayable` (19): Read for the view and the statement
**Spec**: `specs/033-receivables-payables` (mictlanix/mbe-roadmap#79)

### Purpose
What the company owes its suppliers, in the same shape as receivables, built on spec 032's supplier payments and balances (§6 of 05-purchases).

### Open Purchase Orders
- **Open:** a confirmed, non-cancelled PO counting under the balance cutoff (`supplier_balance_cutoff`), with more than 0.01 left. Nothing counts while the cutoff is NULL; go-live sets it, and the view is empty until then.
- **PO balance:** total with tax, less supplier returns (not modelled; always 0), less live applications.
- **Due:** the first confirmed receipt's confirmation plus the supplier's credit days. A PO without a confirmed receipt isn't due yet and sits in "current".

### Views
- `GET /accounts-payable` and `GET /accounts-payable/{supplier_id}`: the receivables summary and drill-down, without sales agent, facility or salesperson. Linked documents are confirmed receipts (facility code and serial); the summary adds `cutoff`.
- **Credit** is spec 032's payment credit; owed, credit and net equal `GET /supplier-payments/suppliers/{id}/balance`.
- **Company-wide** for anyone who can read 19: a PO has no facility, and what the company owes a supplier isn't one facility's debt.
- **Applying** goes through `POST /supplier-payments/{id}/applications`.

### Statement
- The receivables statement with supplier wording ("Proveedor", "Recepciones"), at `GET /{supplier_id}/statement` and `POST /{supplier_id}/statement/email`, to the supplier's contacts. Each send records one incidence on the supplier (source 16).

### Current System
- One row per supplier: payments plus returns less completed POs, without excluding cancelled POs or returns, summing currencies unconverted. Its only drill-down was a running ledger. Supplier credit limits and days were only displayed; they still aren't enforced.

---

## 3. Expenses

**Route**: `GET /expenses`  
**Controller**: `ExpensesController`  
**SystemObject**: `Expenses` (81)

### Purpose
Expense category catalog used in expense vouchers (petty cash). This is the catalog management screen for expense types, not the voucher entry itself (see Purchases spec for voucher entry).

### Form Fields

| Field | Column | Notes |
|-------|--------|-------|
| Name | `expenses.expense` | Category label, required |
| Notes | `expenses.comment` | |

---

## 4. Cash Session Management

Cash sessions are managed within the Payments flow (`PaymentsController`), not as a standalone admin screen, but they are a critical administrative concept.

### Cash Session Lifecycle

| Step | Action | Details |
|------|--------|---------|
| 1 | Open session | Select cash drawer, enter opening cash amount → creates `cash_session` with `Start = now` |
| 2 | Accept payments | All POS and customer payments in the session are linked via `cash_session_id` |
| 3 | Session date check | If session was opened on a prior calendar day → forced close before new sales |
| 4 | Close session | Enter denomination counts (`cash_count`), confirm totals → sets `End = now` |

### Key Table: `cash_session`

| Field | Notes |
|-------|-------|
| `cash_session_id` | PK |
| `cash_drawer` | FK → `cash_drawer` |
| `cashier` | FK → `employee` — who opened the session |
| `start` | Session open time |
| `end` | Session close time (NULL = still open) |
| `initial_amount` | Opening cash declared |
| `comment` | Notes at close |

### SystemObject
- `CashSessionClose` (111): gates who can close a cash session

---

## 5. Credit Customer Configuration

**SystemObject**: `CreditCustomerConfiguration` (98)

### Purpose
Administrative screen to configure credit terms and limits for credit customers. Controls which customers are eligible for `NetD` payment terms.

### Key Fields (on `customer`)
- `credit_limit`: maximum outstanding balance allowed
- `credit_days`: days until payment is due
- `has_credit`: computed from whether `credit_limit > 0`

### Business Rules
- `HasCredit = credit_limit > 0`
- Customers without credit (`HasCredit = false`) cannot use `NetD` payment terms on sales orders.
- Expired credits = `credit_days` past due date; blocks new credit orders until cleared.
