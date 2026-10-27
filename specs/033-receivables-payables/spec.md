# Feature Specification: Accounts Receivable and Accounts Payable

**Feature Branch**: `033-receivables-payables`

**Created**: 2026-10-27

**Status**: Draft

**Input**: Implements two items of the administration epic (mictlanix/mbe-roadmap#8), within §1–2 of `docs/specs/07-administration.md`, with the corrections below:
- mictlanix/mbe-roadmap#77, accounts receivable;
- mictlanix/mbe-roadmap#79, accounts payable.

Both sides are one design: customers and suppliers get the same balance rules, the same views (summary, aging, drill-down), the same actions (apply a payment, a statement as PDF and email) and the same kind of scoping. It builds on:
- customer payments (spec 011), refunds, and fiscal credit notes (spec 028) on the customer side;
- spec 032's supplier payments, applications and balances on the supplier side;
- spec 019's printing and spec 024's mailer for statements.

Out of scope: the current system's debt reports (Reports epic).

## Findings that shape this spec

### The current system (`mictlanix/mbe`, `origin/casa-maestra-2026`)

- **No server-side rights.** Both controllers only require a login. AccountsReceivable (18) and AccountsPayable (19) only hide menus and links.
- **Accounts receivable** lists credit orders (`payment_terms` NetD), one row each, with an "apply payment" shortcut.
  - **Facility scope:** none. The SearchCreditsFromAllStores (101) check is commented out, so everyone sees every facility's orders. The privilege only shows a Store column.
  - **Which orders:** completed, not cancelled, total minus refunds above 0, and either unpaid or paid but modified in the last 60 days.
  - **Balance:** total − refunds − applications.
    - Refunds count in any state, draft and cancelled included.
    - Credit notes are ignored.
  - **Apply payment:**
    - **It never checks that the payment belongs to the order's customer or currency.** Only the dropdown is filtered.
    - It clamps the amount to the payment and the order balance.
    - It sets `paid` and `balance_zeroed_time` when the balance reaches 0.01 or less.
  - **Missing:** aging, credit-limit utilization and statements. Print is a stub, and `AccountStatement.cshtml` (customer, limit, available credit, a running ledger) is never rendered.
- **Accounts payable:** one row per supplier, purchases, payments, returns and balance, where balance = payments + returns − completed POs.
  - Cancelled POs and returns are not excluded.
  - Currencies are summed without conversion.
  - Its only drill-down is a running ledger ("account statement") with no date filter. There's no remittance print.
- **Supplier credit limits and days** are only displayed; nothing enforces them.
- **The audit's reference balance:** `SalesOrder.Balance` = total − completed, non-cancelled refunds − all applications + store-credit notes born from the order's refunds. The audit (`docs/audits/2026-10-adeudos-clientes/README.md`) found four other formulas that disagree with it.
- **Who sets `paid`:** refunds and credit notes only ever set it. Only the payment-correction tool clears it.

### This API today

- **There's no AR or AP endpoint.** 18, 19 and 101 are defined but used nowhere.
- **The customer balance is computed in three places with the same arithmetic:**
  - order lists (`attach_summary_totals`);
  - the credit check (`_customer_credit_debt`, `_overdue_credit_orders`);
  - `GET /customer-payments/outstanding-orders`.

  The arithmetic is total − completed, non-cancelled refunds − live applications, floored at 0. None of the three counts fiscal credit notes.
- **The credit hold counts by flag.** It counts orders that are unpaid by flag and past due, whatever their balance. The limit check sums the balance of orders that are unpaid by flag.
- **Stale docstring:** `_customer_credit_debt` says it "does not net off refunds", but it does (#223).
- **The outstanding-orders list** selects unpaid-by-flag orders of the caller's facility, zero-balance ones included.
- **Applying a customer payment** already refuses another customer's payment, another currency, and more than the payment's unapplied amount. **It doesn't check the order's balance, and nothing is locked.**
- **Writers of `paid`:**
  - Only the customer payment service writes `paid`, from applications alone: `paid` = applications cover the total.
  - `balance_zeroed_time` is stamped once and never cleared. It dates the commission month (spec 025).
  - A refund leaves the flag alone. A refund requires a paid order (FR-060), so in this API a refund never lowers an unpaid order's balance.
- **Fiscal credit notes (spec 028):**
  - A note relates to one invoice. It doesn't move money and doesn't touch `paid`.
  - Its lines keep the order line only when copied from the invoice. Lines added by hand don't.
  - One invoice often covers several orders.
- **Supplier balances (spec 032):**
  - A PO counts when it's confirmed, not cancelled, and confirmed on or after `supplier_balance_cutoff`, which is NULL, so nothing counts yet.
  - PO balance = total − live applications.
  - Per supplier: owed, credit (unapplied amounts of non-cancelled, non-legacy payments) and net.
  - Applications are checked against the PO balance and the payment's credit, under locks.
- **Purchase orders have no facility** (spec 031). Receipts and supplier payments do.
- **Email:**
  - Quotes and CFDIs are emailed to 1–10 addresses the caller types, with a PDF attached. Each send records an incidence.
  - Recipients are never taken from contacts.

### Measured on mbe_dev (2026-10-27, read-only)

- **Credit orders:** 30,247 credit orders are completed and not cancelled. Balances use the API's current arithmetic, with no credit notes:

  | Flag | Balance | Orders | Amount | Customers |
  |---|---|---|---|---|
  | unpaid | > 0.01 | 606 | $6,861,866 | 136 |
  | unpaid | ≤ 0.01 | 257 | $0 | 103 |
  | paid | > 0.01 | 16,405 | $120,189,108 | 523 |
  | paid | ≤ 0.01 | 12,979 | $0 | 1,054 |

  - **Unpaid set:** the request quoted $6.81M for the first row; the plan reconciles the two figures.
  - **Paid orders with a balance:** these are the legacy "paid without applications" orders:
    - 2012–2016: until 2016 a credit sale was marked paid when it was completed, and its payments were parked on order 1;
    - 2017–2024: orders marked paid without a payment;
    - 2025: 297 orders, $2.84M, which the audit is reconciling (its phase 4).
- **Holds:**
  - 199 customers are on credit hold by the flag, but only 130 owe an overdue balance.
  - If overdue ignored the flag on every order, 615 customers would be on hold.
- **The 257 stale unpaid flags:** 3 orders from 2024, 219 from 2025 and 35 from 2026.
- **Walk-in customer:** 127 credit orders with a balance, $205,543.
- **Customer credit:**
  - 1,750 customers hold positive unapplied payments, $24.87M in total.
  - 63 of them also owe on an open order, and hold $4.50M of that credit.
  - 74 payments have a negative unapplied amount, −$516k: cash refunds.
- **Fiscal credit notes:**
  - 113 live credit notes (type 100), from 2019 to 2026-03.
  - 114 of their 115 lines were added by hand, with no order line.
  - Only 5 credit orders ever had a credit note against their invoice, all of them flagged paid.
  - 6,961 live invoices cover more than one order.
- **Suppliers:**
  - 265 suppliers. 150 have a credit limit, and the limits sum to $10.03B, $10.0B of which is one placeholder. 115 have none.
  - 149 have credit days.
  - No supplier return has ever been recorded.
  - Of 1,750 POs confirmed in the last 12 months, 311 have no confirmed receipt.
  - 112 suppliers have a contact with an email address. 2,951 customers do.
- **Privileges:**
  - 23 users can read 18; 11 of them can also read 101, 3 of those 11 being administrators.
  - 13 users can read 19; 4 of them can't read 101.

## The shared model

Each concept exists once and is read from the side's own data. A view, action or statement is written once, with the side as its variable.

| Concept | Customer side (AR) | Supplier side (AP) |
|---|---|---|
| Party | Customer | Supplier |
| Privilege for the view | AccountsReceivable (18) | AccountsPayable (19) |
| Open document | Credit sales order (`payment_terms` NetD), completed, not cancelled, not flagged paid, balance > 0.01 | Confirmed, non-cancelled PO counting under the balance cutoff, balance > 0.01 |
| Document total | Order total (spec 011 line rules) | PO total with tax (spec 031) |
| Deductions | Completed, non-cancelled refunds, plus live fiscal credit notes against the order's invoices | Supplier returns: not modelled, always 0, with a place on the same footing as refunds |
| Applied payments | Live applications of customer payments | Live applications of supplier payments (spec 032) |
| Document balance | total − deductions − applied, floored at 0 | total − deductions − applied, floored at 0 |
| Due date | The order's due date | Date of the PO's first confirmed receipt + the supplier's credit days; none until a receipt is confirmed |
| Overdue | Due date before today and balance > 0.01 | Same |
| Owed | Sum of the open documents' balances, in base currency | Same |
| Credit | Positive unapplied amounts of the customer's payments | Credit of the supplier's payments (spec 032) |
| Net | Owed − credit | Same |
| Credit limit | `customer.credit_limit`, enforced on sales (credit check) | `supplier.credit_limit`, shown only |
| Linked documents | Invoices (CFDI batch and serial) | Confirmed receipts |
| Facility scope | The order's facility; all facilities with SearchCreditsFromAllStores (101) | Company-wide (POs have no facility) |
| History | No cutoff: all history counts | From the go-live cutoff (`supplier_balance_cutoff`) |
| Settled marker | `paid` and `balance_zeroed_time`, kept consistent with the balance | None: a PO has no paid flag |
| Party-only column | Sales agent (`customer.salesperson`) | None |
| Statement recipients | The customer's contacts, or addresses typed in | The supplier's contacts, or addresses typed in |

## Clarifications

### Session 2026-10-27

- Q: Does a `paid` flag close an order whatever its computed balance? (16,405 legacy paid orders compute $120.2M.) → A: Yes. The flag closes an order. Among orders not flagged paid, the balance alone decides what's open and overdue, and from now on the flag follows the balance (FR-007, FR-011).
- Q: When may the `paid` flag be cleared? → A: Only when an event moves the balance from 0.01 or less to above 0.01. A paid order whose balance was already above 0.01 stays paid, so a legacy paid order never reopens (FR-011).
- Q: What does a customer statement cover for a user scoped to one facility? → A: Every facility's open orders of the customer, always. Read on 18 is enough to print or email it; the view stays scoped (FR-023, FR-025).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One customer balance everywhere (Priority: P1)

A credit manager, a salesperson and a cashier each look at the same customer: on the AR view, when a credit order is refused for the limit or for a hold, and on the list of orders a payment can be applied to. Today these places compute the debt differently, and a customer can be held by an order whose balance is zero. With one balance rule, every place shows the same figure, and only an overdue balance puts a customer on hold.

**Why this priority**: It's the core of the spec. The views, statements and actions all read this balance, and the credit check blocks sales on it every day.

**Independent Test**: For every customer in mbe_dev, compare the owed figure from the AR view, the credit check's debt and the sum of the outstanding-orders balances; they must match to the cent. Then count customers on hold before and after.

**Acceptance Scenarios**:

1. **Given** a credit order whose total is covered by applications and completed refunds, but whose `paid` flag is still unset and whose due date has passed, **When** the customer places a new credit order, **Then** they aren't on hold for that order.
2. **Given** an order with a live fiscal credit note against its invoice, **When** its balance is read anywhere, **Then** the credit note's share is deducted.
3. **Given** an order whose balance a payment application brings to 0.01 or less, **When** the application is saved, **Then** the order is flagged paid and its zero time is stamped. **And** when that application is reversed and the balance rises above 0.01 again, the flag is cleared.
4. **Given** a completed refund that brings an unpaid order's balance to zero, **When** the refund completes, **Then** the order is flagged paid.

---

### User Story 2 - Receivables view with aging and credit utilization (Priority: P1)

A credit manager opens accounts receivable and sees one row per customer who owes, with what they owe, how much is overdue, the aging, the credit limit and how much of it is used. They open a customer to see each open order with its invoices, deductions, payments and balance.

**Why this priority**: It's #77's main deliverable, and the collection team works from it.

**Independent Test**: Open the view as a user with 18 and as a user with 18 and 101; check the rows, the facility scope and that the buckets sum to the owed amount.

**Acceptance Scenarios**:

1. **Given** a user with read on 18 but not on 101, **When** they open the view, **Then** they see only orders of their own facility, and each customer's figures add up those orders only.
2. **Given** a user with read on 101 as well, **When** they open the view, **Then** they see every facility and can narrow it to one.
3. **Given** a customer with a credit limit of 50,000 and 40,000 owed across all facilities, **When** their row is shown, **Then** utilization is 80% and available credit is 10,000, whatever facility the user is scoped to.
4. **Given** a customer with no credit limit, **When** their row is shown, **Then** utilization and available credit are blank and the row says "no limit".
5. **Given** a fully paid order, **When** the drill-down is opened, **Then** the order isn't listed.
6. **Given** a user without read on 18, **When** they open the view, **Then** they're refused.

---

### User Story 3 - Payables view with the same shape (Priority: P2)

An administrator opens accounts payable and sees one row per supplier the company owes, with the same columns as receivables (owed, overdue, credit, net, aging, limit and utilization), and drills into each open PO with its receipts and applied payments.

**Why this priority**: It's #79's deliverable. Until go-live sets the cutoff it shows nothing, so it's verified with a test cutoff.

**Independent Test**: Set a test cutoff in mbe_dev, open the view, compare each supplier's figures with spec 032's supplier balance, then reset the cutoff to NULL.

**Acceptance Scenarios**:

1. **Given** a confirmed PO under the cutoff with a confirmed receipt on 1 October and a supplier with 30 credit days, **When** viewed on 15 November, **Then** it's due 31 October, 15 days past due, and in the 1–30 bucket.
2. **Given** a confirmed PO with no confirmed receipt, **When** viewed, **Then** it counts in owed, has no due date and sits in the "current" bucket.
3. **Given** the cutoff is NULL, **When** the view is opened, **Then** it lists no supplier and says that balances start at go-live.
4. **Given** a user with read on 19, whatever their facility, **When** they open the view, **Then** they see every supplier.

---

### User Story 4 - Apply a payment from the view (Priority: P2)

From a party's drill-down, the user picks one of that party's payments with an unapplied amount and applies it to one or more open documents, partially or in full.

**Why this priority**: It replaces the current AR shortcut. Without it, a collector has to leave the view to settle what the view shows.

**Independent Test**: Apply a payment to two open orders, then try another customer's payment and an amount above an order's balance.

**Acceptance Scenarios**:

1. **Given** a customer payment with 1,000 unapplied and two open orders of that customer owing 600 and 700, **When** the user applies 600 and 400, **Then** the first order is paid, the second owes 300, and the payment has 0 left.
2. **Given** a payment from another customer, **When** it's applied to this customer's order, **Then** it's refused and nothing changes.
3. **Given** an order owing 300, **When** an application of 350 is attempted, **Then** it's refused.
4. **Given** two users applying to the same order at once, **When** both applications together exceed its balance, **Then** one is refused, and the order's balance never goes below zero.
5. **Given** the supplier side, **When** the same steps are taken with a supplier payment and that supplier's POs, **Then** the outcomes are the same (spec 032's rules).

---

### User Story 5 - Statement as PDF and by email (Priority: P2)

The user prints a party's account statement: open documents with aging, payments received in the last 90 days, credit limit and available credit, and owed, credit and net. They email it to the party's contacts or to addresses they type. Each send is recorded.

**Why this priority**: It's new. The current system has no equivalent, and collection uses it to tell a customer what they owe.

**Independent Test**: Render the statement for a customer and a supplier, compare its totals with the view, email it, and check the incidence.

**Acceptance Scenarios**:

1. **Given** a customer with open orders, **When** the statement is printed, **Then** its owed, overdue, buckets, credit and net equal the view's figures across all facilities, even when the user who prints it is scoped to one facility.
2. **Given** the statement is emailed to two of the customer's contacts and one typed address, **When** the send succeeds, **Then** one incidence records the party, the sender and the three recipients.
3. **Given** no mailer is configured, **When** the user emails a statement, **Then** it's refused, as quotes and CFDIs are today, and nothing is recorded.
4. **Given** a supplier, **When** the statement is printed, **Then** it uses the same template with supplier wording, receipts in place of invoices, and no sales agent.

---

### User Story 6 - Correct the stale paid flags once (Priority: P3)

The orders that are paid off but still flagged unpaid are corrected once. Each gets an incidence, and commissions are protected.

**Why this priority**: Once overdue reads the balance, these flags no longer hold anyone. Correcting them keeps the flag, the commission month and the order lists truthful.

**Independent Test**: Run the correction on mbe_dev, then count unpaid-flagged orders with a balance of 0.01 or less (expect 0) and pre-2026 orders given a zero time (expect 0).

**Acceptance Scenarios**:

1. **Given** a credit order dated 2025 that is flagged unpaid with balance 0, **When** the correction runs, **Then** it's flagged paid, its zero time stays unset, and an incidence records the change.
2. **Given** such an order dated 2026, **When** the correction runs, **Then** its zero time is the moment its balance reached zero.
3. **Given** the correction has run, **When** it runs again, **Then** nothing changes.

### Edge Cases

- **Overpaid order** (applications plus deductions above the total): its balance is 0, not negative. The excess isn't owed back through the order.
- **Credit note on an invoice that covers several orders:**
  - lines that keep an order line count for that order;
  - the rest of the note is shared out by each order's share of the invoice's total.
- **Cancelled credit note, cancelled refund or reversed application:** the order's balance rises again. The paid flag is cleared only if the balance was 0.01 or less before and is above 0.01 after.
- **Legacy paid order that still computes a balance:** a refund or credit note on it that is later cancelled leaves it paid and out of AR.
- **Orders in another currency:** owed, buckets and the party's figures are in base currency at each order's exchange rate. The drill-down also shows each document in its own currency.
- **Walk-in customer:** listed like any customer. It has no credit limit, and it stays exempt from the hold and the limit check, as today.
- **Customer whose only open orders are in other facilities:** absent from the view of a user without 101. The credit check still counts those orders.
- **Party with credit but nothing owed:** not listed in the view. Their statement still prints and shows the credit.
- **Supplier PO with several receipts:** the first confirmed one dates it. A later cancellation of that receipt moves the date to the next confirmed one, or removes it.
- **Supplier with no credit days:** a PO is due on the date of its first confirmed receipt.
- **Email to a contact with no address:** the contact isn't offered. A typed address that isn't valid is refused before sending.

## Requirements *(mandatory)*

### Functional Requirements

#### One balance

- **FR-001**: Each side MUST have exactly one balance rule for a document and one for a party. Every place on that side that needs a balance MUST use them:
  - **Customer side:**
    - the AR view and the statement;
    - the credit-limit check and the credit hold;
    - the outstanding-orders list;
    - keeping `paid` consistent.
  - **Supplier side:**
    - the AP view and the statement;
    - spec 032's PO and supplier balances.
- **FR-002**: A sales order's balance MUST be its total, less completed, non-cancelled refunds, less live payment applications, less its share of live fiscal credit notes against its invoices, floored at 0. A credit note is live when it's issued and not cancelled.
- **FR-003**: A credit note's amount MUST be shared out to the orders on its invoice. A line that keeps an order line goes to that order. The rest goes to each order in proportion to that order's part of the invoice's total.
- **FR-004**: A PO's balance MUST be its total with tax, less supplier returns (always 0 until returns are modelled), less live applications, floored at 0. This is spec 032's rule, unchanged.
- **FR-005**: A party's figures MUST be:
  - **owed**: the sum of its open documents' balances, in base currency;
  - **credit**: the sum of its payments' positive unapplied amounts, using spec 032's rule on the supplier side;
  - **net**: owed − credit.

  On the supplier side, they MUST equal spec 032's supplier balance.
- **FR-006**: Overdue MUST mean a due date before today and a balance above 0.01, on both sides; the `paid` flag MUST NOT decide it.
  - A sales order's due date is its own.
  - A PO's due date is the date its first confirmed receipt was confirmed plus the supplier's credit days. A PO without one isn't due.
- **FR-007**: The customer side's open documents MUST be the credit sales orders (`payment_terms` NetD) that are completed, not cancelled, not flagged paid, and have a balance above 0.01.
  - The flag closes an order whatever its computed balance. On mbe_dev, 16,405 legacy orders flagged paid still compute $120.2M; they stay closed, and reconciling them is the audit's job.
  - Among the orders not flagged paid, the balance alone decides what's open and overdue (FR-006). From now on the flag follows the balance (FR-011).
- **FR-008**: The supplier side's open documents MUST be the confirmed, non-cancelled POs that count under the balance cutoff (spec 032) and have a balance above 0.01. With the cutoff NULL there are none.
- **FR-009**: The credit check MUST use these figures:
  - **Hold:** at least one overdue open credit order.
  - **Limit:** the customer's owed, plus the order in hand at confirmation, against their limit.
  - **Walk-in customer:** exempt from both, as today.
- **FR-010**: The outstanding-orders list MUST list only open orders, each with the shared balance. Its facility scope and search are unchanged.

#### Keeping `paid` consistent

- **FR-011**: After any event that changes an order's balance, the order's `paid` flag MUST be updated. The events are an application or its reversal, a refund's completion or cancellation, and a credit note's issue or cancellation.
  - **Set:** when the balance is 0.01 or less. The zero time is stamped the first time and never cleared, as today.
  - **Clear:** only when an event moves the balance from 0.01 or less to above 0.01. A paid order whose balance was already above 0.01 before the event stays paid, so a legacy paid order never reopens. An event that lowers the balance never clears it.
- **FR-012**: The orders that are open by FR-007, flagged unpaid and have a balance of 0.01 or less (257 on mbe_dev) MUST be corrected once:
  - flagged paid, with one incidence per order;
  - the zero time set to the moment the balance reached zero for orders dated 2026 or later;
  - the zero time left unset for earlier orders, so they never produce a commission (the audit's decision 4).

  The correction MUST be safe to run again, so it can run once more at cutover.

#### Views

- **FR-013**: The AR view MUST require read on AccountsReceivable (18), and the AP view read on AccountsPayable (19).
- **FR-014**: The summary MUST list one row per party with at least one open document in the caller's scope. Each row shows:
  - owed and overdue;
  - aging buckets by days past due: current (not yet due, or no due date), 1–30, 31–60, 61–90, over 90;
  - the oldest open document's date;
  - credit and net;
  - credit limit, utilization and available credit;
  - on the customer side, the sales agent.

  The buckets MUST add up to owed.
- **FR-015**: Utilization (owed ÷ limit) and available credit (limit − owed) MUST be computed on the party's owed across the whole company, whatever the caller's scope.
  - They're shown only when the limit is above 0.
  - A limit of 0 or empty means "no limit": utilization and available credit stay blank.
  - Available credit below 0 is shown as an over-limit amount.
- **FR-016**: The summary MUST be filterable by party, by overdue only, by facility (when the caller sees more than one) and, on the customer side, by sales agent. It MUST be sortable by owed, overdue and oldest document.
- **FR-017**: The drill-down MUST list the party's open documents in scope. Each row shows:
  - date, serial or id, due date and days past due;
  - facility;
  - linked documents: invoice batch and serial for orders, receipt serials for POs;
  - on the customer side, the salesperson on the order;
  - total, deductions, applied payments and balance.

  Fully paid documents MUST NOT be listed.
- **FR-018**: Customer-side scope MUST be the caller's facility, or every facility when the caller can read SearchCreditsFromAllStores (101). Administrators see every facility.
- **FR-019**: The supplier side MUST be company-wide for anyone who can read 19. POs have no facility, and what the company owes a supplier isn't one facility's debt, so 101 doesn't apply there.

#### Actions

- **FR-020**: From a drill-down, a user MUST be able to apply one of the party's payments to one or more of its open documents. The application goes through the customer payment service or spec 032's supplier payment service, under their existing privileges.
- **FR-021**: An application MUST be refused when:
  - the payment belongs to another party;
  - it's in another currency;
  - it's above the payment's unapplied amount;
  - it's above the document's balance;
  - the document isn't open;
  - the document is outside the caller's scope.
- **FR-022**: Concurrent applications MUST NOT take a document's balance or a payment's unapplied amount below zero, on either side. The customer side gets the locking spec 032 gave the supplier side.
- **FR-023**: A party's statement MUST render as a PDF with spec 019's printing, from one template with the side as its variable. It shows:
  - the party and the date;
  - the credit limit, credit days and available credit;
  - all the party's open documents, in every facility, with due date, days past due and balance;
  - the aging buckets;
  - the payments received in the last 90 days (date, serial, method, amount, applied, unapplied);
  - owed, credit and net.

  A statement goes to the party, so it MUST be complete whatever the caller's facility scope. Its figures MUST equal the view's for the same party seen across all facilities.
- **FR-024**: A statement MUST be emailable through spec 024's mailer, with the PDF attached. Recipients are any of the party's contacts that have an email address and up to 10 typed addresses, at most 10 in total.
  - Each successful send MUST record one incidence on the party with the recipients.
  - With no mailer configured, the send is refused and nothing is recorded.
- **FR-025**: Printing and emailing a statement MUST require read on the side's privilege (18 or 19). SearchCreditsFromAllStores (101) isn't needed, even though the statement covers every facility.

### Key Entities

- **Party**: a customer or a supplier, with credit limit, credit days and contacts. Customers also have a sales agent.
- **Open document**: a credit sales order or a counting PO with a balance; its total, deductions, applied payments, balance and due date.
- **Deduction**: on orders, a completed refund or the order's share of a live fiscal credit note. On POs, a supplier return, which isn't modelled yet.
- **Payment and application**: existing customer and supplier payments and their applications. Unapplied amounts make up the party's credit.
- **Party balance**: owed, overdue, aging buckets, credit, net, utilization and available credit; computed, never stored.
- **Statement send**: an incidence on the party naming the sender and the recipients.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every customer in mbe_dev, the AR view's owed, the credit check's debt and the sum of the outstanding-orders balances are equal to the cent. 0 mismatches.
- **SC-002**: Before and after on mbe_dev:
  - **Customer owed:** reported against today's $6.86M over 606 orders and 136 customers, every difference explained by credit notes.
  - **Holds:** customers on hold fall from 199 to the number owing an overdue balance, 130 today.
  - **Current AR screen:** it differs for 3 customers (about $6.4k, rounding and refunds that weren't completed) and for no other.
- **SC-003**: With a test cutoff on mbe_dev, every supplier's owed, credit and net in the AP view equal spec 032's supplier balance to the cent. The cutoff is NULL again afterwards.
- **SC-004**: For every party on both sides, the aging buckets add up to owed, with 0 cent difference.
- **SC-005**: 100% of applications with another party's payment, or above a document's balance, are refused. 20 simultaneous applications against one order never apply more than its balance.
- **SC-006**: For a sample of 20 customers and 5 suppliers, the statement's totals equal the view's figures across all facilities, including statements printed by a user scoped to one facility. Each email send leaves exactly one incidence.
- **SC-007**: After the one-time correction:
  - 0 open orders flagged unpaid have a balance of 0.01 or less;
  - 0 orders dated before 2026 gain a zero time;
  - running it again changes 0 rows.
- **SC-008**: On mbe_dev's data, the summary for all customers answers within 2 seconds, and a drill-down or a statement within 1 second.

## Assumptions

- **Base currency:** a party's figures are in base currency at each document's exchange rate, as the credit check does. All open credit orders on mbe_dev are in MXN.
- **No customer cutoff:** customer history counts in full (decided 27 Oct 2026). The audit's 2025-01-01 floor applies only to the current system's reports.
- **Customer credit** is every positive unapplied amount, with no cutoff. That includes legacy payments, $24.87M across 1,750 customers. Cleaning it up is the audit's phase 2, not this spec. Credit is shown, but utilization and the credit check read owed, not net.
- **Supplier side at go-live:**
  - Nothing is migrated, and go-live sets `supplier_balance_cutoff` to the go-live date.
  - Verification uses a test cutoff and leaves it NULL.
  - Supplier credit limits are placeholders on mbe_dev. Utilization works the same way, but is never enforced.
- **Facility scope:** 12 of the 23 users who can read 18 today can't read 101. They see every facility in the current system and will see only their own here. Granting 101 is a user-administration decision.

  The 13 users who can read 19 see every supplier, now and after.
- **Applying from the view:** it's the existing customer payment and supplier payment services, not a new path. FR-021's balance check and FR-022's locking apply to the customer service as a whole, since one balance rule means one guard.
- **Refunds:** a refund still requires a paid order (FR-060), unchanged.
- **Store-credit notes:** the store-credit `credit_note` record created when a refund pays out is customer credit (an unapplied payment), not a deduction from the order.
- **Recent payments** on a statement are those received in the 90 days before its date.
- **Incidence sources:** sends are recorded under the party's source. The customer side has one (7). The plan adds one for suppliers.
- **Planning notes:**
  - The plan fixes the stale docstrings of `_customer_credit_debt` and `_overdue_credit_orders`.
  - The plan defines where a receipt's confirmation date comes from.
  - The plan reconciles the request's $6.81M with the $6.86M measured.

## Out of Scope

- The current system's debt reports (Reports epic).
- Supplier returns: not modelled. The balance rule keeps a place for them.
- Cleaning up legacy data beyond FR-012: paid orders with a computed balance, legacy unapplied payments, and payments parked on order 1 (the audit's phases 2–7).
- Enforcing supplier credit limits.
- Remittance advices for supplier payments.
- Changing who can refund, invoice or issue credit notes.
