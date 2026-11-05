# Feature Specification: Operations analytics (inventory, receivables, commissions, fiscal, suppliers) with saved views and scheduled reports

**Feature Branch**: `038-analytics-operations`

**Created**: 2026-11-05

**Status**: Clarified

**Input**: User description: "The rest of the analytics on the foundation of the previous spec (same metrics layer, summary tables, dashboard tiles, drill-down and XLSX/PDF/CSV exports): inventory, receivables and collection, commissions, fiscal documents and supplier analytics, plus saved views and scheduled reports by email." Roadmap: mictlanix/mbe-roadmap#94, mictlanix/mbe-roadmap#96, mictlanix/mbe-roadmap#100, mictlanix/mbe-roadmap#102 (epic mictlanix/mbe-roadmap#9).

## Overview

Spec 037 built the analytics foundation for sales, margin and payments:
- a catalog of measures and dimensions, each defined once;
- one query with comparisons, shares and top N;
- a dashboard in one call;
- drill-down from every number to its documents;
- exports to XLSX, PDF and CSV that match the screen.

This spec adds the remaining business areas on that same foundation, plus two ways to reuse a question:

- **Inventory**: stock now and as of a past date, movements, kardex with value, stock-outs, cover, dead and slow stock, restock suggestions.
- **Receivables and collection**: aging today and over time, DSO, collection effectiveness, credit utilization, a collections forecast, and a customer 360. Also the supplier side (payables), symmetric.
- **Commissions**: what each salesperson earns, document by document, read from spec 025's existing computation.
- **Fiscal documents and suppliers**: invoiced against sold, invoices by status, credit notes and payment receipts, VAT per period, purchases by supplier and product, and purchase price history.
- **Saved views and scheduled reports**: save a query with a relative period, share it, and have it emailed on a schedule.

Each new number comes from a catalog measure. It follows the same privilege, facility-scope, freshness, drill-down and export rules as spec 037. Spec 037 has no summary tables (its clarification and research R1, R9). This spec adds only one stored history, the month-end receivables snapshots, because a past balance can't be read back cheaply.

### What mbe_dev shows (measured 2026-11-05, read only)

mbe_dev's copy of production ends on 2026-09-29; later rows are test data.

| Area | Fact |
|---|---|
| Stock ledger | 670,388 entries since 2013; 123k in 2025. On hand is the sum of the ledger (spec 030); there is no stored balance. |
| Ledger reset | On 2025-02-06 a mass adjustment (8,753 entries, about +1.0M units) cleared 10,805 negative product × warehouse balances; 15,918 of 43,919 pairs had gone negative at some point before it. Stock before 2025-02-07 is not trustworthy. |
| Stock today | 12,780 products with stock in 18,338 product × warehouse pairs, across 34 warehouses in 15 facilities (15 of them transit warehouses); 51 pairs are negative. |
| Stock-as-of speed | All pairs as of any date: about 1.2 s; five year-end snapshots in one pass: 1.9 s. |
| Stock value at current cost | 288.4M, of which 87.5M comes from 20 pairs with a placeholder cost of 99,999 and 158M from one cement product received but never issued through the ledger; about 41.7M without them. 630 positive pairs have cost 0. |
| Cost history | None usable: `product_cost_change` holds only test rows. Average cost changes only when a receipt of a purchase order is confirmed (spec 031). |
| Minimum stock | No minimum or maximum stock setting exists. |
| Lots and serials | Unused: no lot numbers (only an adjustment marker), no serial numbers, no expiry dates. |
| Dead stock (no movement, as of 2026-09-30) | 90 days: 10,489 products, 28.5M; 180 days: 8,785, 24.8M; 365 days: 5,651, 8.1M (outliers excluded). |
| Receivables today | 606 open credit orders, 136 customers, $6.86M, matching spec 033's figure exactly. |
| Receivables history | Payment applications, refunds, credit notes and confirmations are dated, so a balance as of a past date can be rebuilt. Exceptions: orders closed by the legacy "paid" flag without a date (2025: 277 orders, $2.79M; earlier years worse). From 2026-01 the flag matches the computed balance exactly. |
| Credit | 1,842 customers have a credit limit; credit orders: 6,195 in 2025. Limits have no history. |
| Payables | Spec 033's supplier cutoff isn't set, so every payable is 0 until go-live. Legacy purchase orders exist (1,999 confirmed in 2025, 97 suppliers, $123.7M before tax). |
| Commissions | Spec 025's rule: label rate × participation share on each sale line (price after discount, VAT included), earned when the order is fully paid, net of refunds, monthly. Its period tables are empty (no month closed yet); 6 agents, all on the "fully paid" scheme. The `customer_payment` commission fields of mictlanix/mbe#53 are card surcharges, not salesperson commissions. |
| Invoices | 5,453 live invoices in 2025 (187 cancelled after stamping); 1 credit note, 13 payment receipts, 25 advance applications. Invoice lines link to sales order lines. |
| Invoiced against sold | 2025: 19% of orders and 57% of sales without VAT are invoiced (by sale date), lag averages 15 days and reaches 312. $130.6M of 2025's $287M invoiced has no order link (standalone invoices). No real global invoices exist. |
| Purchase prices | 4,226 products bought in 2025, 1,110 of them at more than one price, 492 from more than one supplier. There is no dated supplier price list; purchase order lines are the price history. |
| Cost basis | The cost list equals the latest purchase price as typed for 86% of products. Spec 031 clarified that cost and purchase prices are both without VAT. |
| Mail and jobs | Spec 024's mailer exists; mail isn't configured on dev or test. The existing jobs (`app/jobs/`) are command-line modules that nothing runs on a schedule today. |

## Clarifications

### Session 2026-11-05

- Q: The request says to stop and ask if the business has no clear commission rule. → A: It has one, already specified and approved in spec 025: label rate × participation share on sales lines (after discount, VAT included), earned when the order is fully paid, net of refunds, by month, with a per-agent setting for the partial-payment scheme. This spec measures that computation; it defines no second rule.
- Q: There is no minimum-stock setting. Where does the reorder point come from? → A: Derived only: velocity × (lead time + target cover days), with lead time and cover as settings; no minimum or maximum stock setting is added (FR-110).
- Q: From which month-end is the receivables history rebuilt? → A: From 2025-01, with 2025 month-ends flagged as understating by up to $2.79M (legacy orders closed by the paid flag); exact from 2026-01 (FR-121).
- Q: Who may receive a scheduled report? → A: Only active users who themselves hold the view's privileges, checked when scheduling and at each send; no free-typed addresses (FR-160).
- Q: Margin and stock value need cost without VAT, but spec 037 (research R4) divided the cost snapshot by 1.16. → A: Spec 031's clarification governs: cost and purchase prices are without VAT. Spec 037 is corrected on its own fix branch (`fix/analytics-cost-without-vat`), which this spec depends on; its cost-based measures use cost as recorded.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stock position, now and on any past date (Priority: P1)

A warehouse manager wants to know what is on hand, reserved and available per product and warehouse, what it is worth, and what it was at the end of last month. An auditor asks for the stock of a product on a given date and the kardex that led to it, with values.

**Why this priority**: Inventory is the largest asset after receivables and has no analytics today beyond the stock list and the kardex screens.

**Independent Test**: For a sample of products and warehouses, compare on hand from the query with spec 030's stock screens today, and stock as of a past date with spec 030's kardex balance at that date.

**Acceptance Scenarios**:

1. **Given** today's stock, **When** on hand, reserved and available are queried by product and warehouse, **Then** each equals spec 030's stock screen for that product and warehouse.
2. **Given** a past date on or after the reliable-history date (FR-102), **When** stock is queried as of that date, **Then** it equals the kardex running balance at the end of that date.
3. **Given** a date before the reliable-history date, **When** stock as of it is requested, **Then** it is refused with a coded error naming the earliest date allowed.
4. **Given** a product and warehouse, **When** the kardex is requested from any date, **Then** it returns the opening balance, every movement with its type, document, quantity and running balance, and the value at the current cost.
5. **Given** a caller without the cost privilege, **Then** no stock value, cost or turnover appears in any response.

---

### User Story 2 - Receivables and collection, today and over time (Priority: P1)

A credit manager sees how much customers owe, how much is overdue by bucket, how that has moved month by month, who the largest debtors are, how fast the company collects (DSO), how well (collection effectiveness), how close customers are to their limits, and what is due in the coming weeks.

**Why this priority**: Overdue receivables are the most urgent cash question; spec 037's dashboard shows only today's overdue count.

**Independent Test**: Today's receivables by customer and bucket equal spec 033's receivables screen; a month-end aging figure equals a rebuild of that month-end through the same balance rule.

**Acceptance Scenarios**:

1. **Given** today, **When** receivables are queried by customer and aging bucket, **Then** the totals equal spec 033's receivables summary to the cent, in every bucket.
2. **Given** month-end dates within the stored history, **When** aging is queried over time, **Then** each month shows the balance and buckets as they stood at that month-end, computed by spec 033's rule as of that date.
3. **Given** a period, **When** DSO, collection effectiveness and collected-against-due are queried, **Then** each follows its written definition (FR-122) and drills down to the orders and applications behind it.
4. **Given** today, **When** the collections forecast is requested, **Then** open balances are grouped by due date (overdue, next 7, 30 and 60 days) and add up to the total receivable.
5. **Given** the supplier side, **When** payables are queried, **Then** they mirror receivables from spec 033's supplier balances, and are empty, not an error, until the go-live cutoff is set.

---

### User Story 3 - Customer 360 (Priority: P2)

Before calling a customer, a salesperson opens one view with what the customer bought, what we earn on them, what they paid, what they owe and how late, when they last bought, how often, their top products and their refunds.

**Why this priority**: The legacy Customer 360 was never finished; this is the most requested single-customer view.

**Independent Test**: For one customer, every figure in the view equals the matching query filtered by that customer, and the balance equals spec 033's for that customer.

**Acceptance Scenarios**:

1. **Given** a customer and a period, **When** the customer 360 is requested, **Then** it returns sales, refunds, net sales, margin (cost privilege only), payments, balance and aging, last purchase date, purchase frequency and top 10 products, each equal to its query or spec 033 figure.
2. **Given** a caller limited to one facility, **When** they ask for another facility, **Then** the request is refused with spec 034's facility code; without a facility, the figures are scoped to the caller's own.

---

### User Story 4 - Commissions a salesperson can verify (Priority: P2)

A sales agent wants to see what they earned this month and why: every line that counted, its participation, the label rate, the agent's share and the amount. A manager compares agents and months.

**Why this priority**: Commissions are paid from these numbers; an agent must be able to check each one.

**Independent Test**: For an agent and month, the commission total equals spec 025's computation for that period, and the per-document detail adds up to it.

**Acceptance Scenarios**:

1. **Given** a closed commission period, **When** commissions are queried by agent, label and participation, **Then** they equal spec 025's stored period lines.
2. **Given** an open month, **When** commissions are queried, **Then** they equal spec 025's live computation for that month, marked as provisional.
3. **Given** a commission figure, **When** it is drilled down, **Then** each line shows the order, the product, the label, the participation, the label rate, the agent's share, the base and the amount, and the lines add up to the figure.
4. **Given** an agent who is not an administrator and lacks the commissions reading privilege, **Then** they see only their own commissions.

---

### User Story 5 - Invoiced against sold, and the fiscal picture (Priority: P2)

An accountant checks how much of last month's sales has been invoiced, which sales still lack an invoice, how many invoices were stamped, cancelled or are still pending, the credit notes and payment receipts issued, and the VAT of the period.

**Why this priority**: Unbilled sales and cancelled invoices are tax risks; the legacy fiscal report lists documents but answers none of these questions.

**Independent Test**: For a month, invoiced amount by issuer equals the sum of the live invoices' lines; the uninvoiced detail lists exactly the sales lines with no live invoice line.

**Acceptance Scenarios**:

1. **Given** a period, **When** invoice coverage is queried, **Then** it shows sold, invoiced (from the invoice lines linked to the period's sales lines) and not invoiced, by facility, customer and salesperson, and the not-invoiced figure drills down to the sales lines that make it up.
2. **Given** a period, **When** invoices are queried by status, **Then** stamped, cancelled and pending counts and amounts follow FR-142, and cancelled ones show when they were cancelled.
3. **Given** a period, **When** the VAT summary is requested, **Then** output VAT of invoices minus credit notes is shown per issuer and month.
4. **Given** standalone invoices (no linked sale), **Then** they appear separately, so coverage isn't overstated by them.

---

### User Story 6 - Purchases and supplier prices (Priority: P3)

A buyer reviews purchases by supplier and product, sees how a product's purchase price changed over time and across suppliers, and compares what we pay with what we sell for.

**Why this priority**: Purchasing moved to the new system recently (spec 031); the legacy purchase history is the only price history there is.

**Independent Test**: Purchases by supplier for a month equal the sum of the confirmed purchase orders' lines of that month; the price history of a product lists every purchase order line for it.

**Acceptance Scenarios**:

1. **Given** a period, **When** purchases are queried by supplier and product, **Then** amounts and quantities equal the confirmed purchase order lines, and drill down to them.
2. **Given** a product, **When** its price history is requested, **Then** it lists each purchase with date, supplier, unit price and the change from the previous purchase, and flags changes above a threshold.
3. **Given** a product and period, **When** purchase against sale is queried, **Then** it shows the average purchase price, the average sale price and the difference, both without VAT, for holders of the cost privilege.

---

### User Story 7 - Saved views (Priority: P2)

A manager builds "net sales by facility, month to date, against last year" once, saves it, and reopens it every morning with the dates rolled forward. They share it with the store managers.

**Why this priority**: Without saving, every question must be rebuilt by hand; saved views are also what scheduled reports send.

**Independent Test**: Save a view with a relative period, reopen it on another day, and check that the period resolved to that day's dates; open a shared view as another user and check their own privileges and scope apply.

**Acceptance Scenarios**:

1. **Given** a query, dashboard or detail definition with a relative period, **When** it is saved, **Then** it can be listed, opened, renamed and deleted by its owner, and opening it runs it with the period resolved against today.
2. **Given** a view shared with other users, **When** one of them opens it, **Then** it runs with that user's privileges and facility scope, not the owner's; measures they may not see are refused as in a query.
3. **Given** a view that uses a measure or dimension removed from the catalog, **When** it is opened, **Then** it is refused with a coded error, not run partially.

---

### User Story 8 - Scheduled reports by email (Priority: P3)

A director wants last week's sales dashboard as a PDF every Monday at 8:00, and the credit manager wants the aging as XLSX on the first of each month.

**Why this priority**: Pushes the numbers to people who don't open the app; depends on saved views.

**Independent Test**: Schedule a saved view, advance the scheduler past its time, and check one send is recorded with the attachment built under the owner's privileges at send time; run two schedulers at once and check only one send happens.

**Acceptance Scenarios**:

1. **Given** a saved view and a schedule (daily, weekly on a weekday, monthly on a day, at a time), **When** the time comes, **Then** the view runs with the owner's privileges and facility scope at that moment, is rendered as PDF or XLSX, is emailed to the recipients, and the send is recorded with its time, recipients, size and outcome.
2. **Given** the owner lost the privilege the view needs, was deactivated, or the export is too large to email, **When** the time comes, **Then** nothing is sent, the failure is recorded with its code, and the owner can see it.
3. **Given** a missed run (the scheduler was down), **When** it runs again, **Then** each schedule sends at most once for the missed time, never once per missed occurrence.
4. **Given** two schedulers running against the same database, **Then** each occurrence is sent exactly once.

### Edge Cases

- **Transit warehouses:** stock in a transit warehouse is shown as in transit for the destination facility, never twice (spec 030's rule).
- **Negative stock:** a negative balance is shown as it is; it counts as a stock-out and never adds value.
- **Uncosted stock:** stock whose current cost is 0, or a placeholder 99,999 or higher, is uncosted: it is left out of stock value, and a cost coverage measure and an alert show how much stock lacks a usable cost, as spec 037 does for margin.
- **Bulk products sold without ledger issues:** their on hand is what the ledger says; the dead-stock and uncosted alerts surface them, nothing corrects them.
- **Kardex over long ranges:** spec 030's kardex caps a range at 366 days; the analytics kardex pages through any range instead.
- **Receivables of cancelled orders:** an order cancelled after confirmation stops counting from its cancellation; since that moment has no date, it stops counting in every past month-end too (stated in the history's caveat).
- **Legacy orders closed by the paid flag:** they count as closed in every month-end (spec 033's rule today); the stored history says how much this understates before 2026.
- **Credit limit changes:** utilization uses the limit stored in each month-end snapshot. Backfilled month-ends carry the customer's limit when the backfill ran, marked as not historical (FR-121), so utilization history is real only from the first month-end written after release.
- **Commission of a refunded order after the period closed:** spec 025's rule assigns it to the refund's month.
- **Invoice issued in a later period:** coverage of a past period rises when it is invoiced; the response's `as_of` says when it was read.
- **Invoice for more than one order, or partly invoiced lines:** coverage counts each order line's invoiced quantity, capped at the line quantity.
- **Saved view whose owner is deleted:** the view and its schedules are deleted with the user, as spec 037's exports are.
- **Relative periods across year ends and short months:** "last month" on March 31 is February 1–28 (or 29); "same period last year" of February 29 ends on February 28.
- **Empty payables:** before the supplier cutoff, every payable figure is zero and the dashboard shows no payables alert.

## Requirements *(mandatory)*

### Functional Requirements

**Foundation (from spec 037)**

- **FR-001**: Every number in this spec MUST be a measure in spec 037's catalog, served by its query, dashboard, detail and export routes or by the views this spec names, with the same rules: definitions written once, rows adding up to totals, server-computed shares and comparisons, `as_of`, drill-down filters, the cost privilege, facility scope, freshness within five minutes (except the stored month-end history, FR-121), the response cache, and spec 036 error codes.
- **FR-002**: Each new family (inventory, receivables, payables, commissions, fiscal, purchases) MUST be gated on the existing privileges of the screens and reports it replaces (FR-170). New measures and dimensions are added to the catalog with the families they belong to.
- **FR-003**: Every legacy question of `docs/specs/11-reports.md` in these areas MUST be answerable; the Legacy questions table says how, and says where a legacy report answers a question nobody can answer with today's data.

**Inventory (#94)**

- **FR-100**: Stock measures MUST come from the stock ledger through spec 030's functions, never a second rule:
  - **On hand**: the sum of ledger quantities up to the moment asked (spec 030);
  - **Reserved** and **available**: spec 030's rule, today only (reservations have no history);
  - **In transit**: stock in transit warehouses, shown against the destination facility (spec 030).
- **FR-101**: Stock MUST be available as of any date from the reliable-history date (FR-102) to today, at the end of that business day, by product, warehouse, facility, brand, label and supplier (the product's).
- **FR-102**: The reliable-history date is 2025-02-07, the day after the ledger reset. Stock, movements and kardex balances as of an earlier date are refused with a coded error that names it. The date is a setting, so a later clean-up can move it.
- **FR-103**: **Movements**: units in and units out by movement type (sale, refund, receipt, issue, transfer, adjustment, count) and by period. Transfers are shown separately and net to zero across the company.
- **FR-104**: **Kardex with value**: per product and warehouse, from any date on or after FR-102 to any later date, paged: opening balance, then each movement with date, type, document, quantity in or out and running balance; and the value of the running balance at the current average cost (cost privilege only).
- **FR-105**: **Value**: stock value = on hand × the product's current average cost (spec 031), without VAT, cost privilege only. Stock whose cost is 0 or 99,999 or more is uncosted and left out of value. **Stock cost coverage** = the share of product × warehouse pairs with stock that have a usable cost, shown with every value, together with the uncosted pairs' on-hand quantity.
- **FR-106**: **Velocity and cover**: average daily units out by sale (sales minus refunds) over a trailing window (90 days by default, settable per request between 30 and 365); **days of cover** = available ÷ velocity, null when velocity is 0.
- **FR-107**: **Turnover** = cost of sales (spec 037, with its cost fix) over the period ÷ average stock value over the period at current cost; **days of inventory** = days in the period ÷ turnover. Cost privilege only. Both say in their definition that they use current cost, since there is no cost history.
- **FR-108**: **Stock-outs**: product × warehouse pairs with available ≤ 0 that sold in the trailing velocity window.
- **FR-109**: **Dead and slow stock**: dead = on hand > 0 and no movement of any type in N days (N = 90, 180 or 365, chosen per request); slow = on hand > 0 and days of cover above a threshold (180 by default). Each with its quantity and value.
- **FR-110**: **Restock suggestions**: for each product × warehouse that sold in the velocity window, a suggestion to restock when available is below the reorder point, with the suggested quantity to reach the target cover. The reorder point is derived, never stored: velocity (FR-106) × (lead time + target cover days); the suggested quantity = velocity × (lead time + target cover days) − available, rounded up to a whole unit for products sold in whole units. Lead time (7 days by default) and target cover (30 days by default) are settings, and a request may override them. No minimum or maximum stock setting is added.
- **FR-111**: Lots and serials are not reported: no product has lot or serial data (legacy reports 25, 26, 29 are retired).

**Receivables and collection (#96)**

- **FR-120**: Every receivable figure MUST come from spec 033's balance rule (`order_balances` and its candidates). To read a past date, that rule gains an "as of" moment: only events dated on or before it count (applications by their date, refunds by their date, credit notes issued and not yet cancelled, orders confirmed). At "now" it returns exactly spec 033's figures, so the two can't drift.
- **FR-121**: **Month-end history**: the receivable balance of every open credit order at each month-end is stored once that month has ended, computed by FR-120, with the customer's credit limit at that moment. It is backfilled for every month-end from 2025-01-31. The 2025 month-ends understate the receivable by up to $2.79M (277 legacy orders closed by the paid flag without a date, counted as closed); the history states this caveat for 2025 and is exact from 2026-01. Month-ends before 2025 are refused with a coded error naming the first one. The backfilled credit limit of a past month-end is the customer's limit when the backfill runs, marked as not historical. A command rebuilds any month-end; rebuilding an unchanged month gives the same figures.
- **FR-122**: Measures, all in base currency (spec 033's rule):
  - **Receivable**, **current** and **overdue** balance, and by aging bucket (current, 1–30, 31–60, 61–90, 90+ days past due), spec 033's buckets;
  - **Credit sales**: the gross sales (spec 037) of credit-terms orders;
  - **DSO** = receivable at period end ÷ credit sales in the period × days in the period;
  - **Collection effectiveness (CEI)** = (receivable at start + credit sales − receivable at end) ÷ (receivable at start + credit sales − current receivable at end);
  - **Collected on credit**: applications dated in the period to credit orders;
  - **Due in the period**: balances whose due date falls in the period, and how much of it was collected by period end;
  - **Credit utilization** = receivable ÷ credit limit per customer (spec 033's figure), null without a limit;
  - **Collections forecast**: open balances by due date: overdue, due in 1–7, 8–30, 31–60 and later.
  DSO and CEI need balances at the start and end of the period, so periods for them end on a month-end within the history or today.
- **FR-123**: Dimensions: customer, salesperson (the order's) and sales agent (the customer's, as spec 033's screen shows), facility, aging bucket, due-date bucket, payment terms, and date at month grain for the history.
- **FR-124**: **Payables**: the same measures for suppliers from spec 033's supplier balances (purchase orders confirmed from the supplier cutoff), from go-live; until the cutoff is set they are zero and their alert is omitted.
- **FR-125**: **Customer 360**: one response for a customer and period with:
  - net sales, refunds and margin (cost privilege);
  - payments;
  - balance, overdue and aging, as of now;
  - last purchase date and purchase frequency (orders per month over the trailing 12 months);
  - top 10 products;
  - credit limit and utilization.
  Each figure equals its query or spec 033 figure, carries its drill-down, and is exportable.

**Commissions (#100)**

- **FR-130**: Commissions MUST be read from spec 025's computation, never recomputed by a second rule:
  - stored period lines for closed months;
  - spec 025's live computation for months not yet closed, marked provisional. An open month in a query that also includes earlier open months shows only what closing them in order would add: spec 025 computes each open month as if the months before it were closed, so a query spanning several open months would otherwise count the earlier ones again.
  The rule is spec 025's, restated so a salesperson can check it:
  - each sale line's price after discount, with VAT, × quantity × the label's rate × the agent's share for their participation (customer's agent and order's salesperson: lifetime client; otherwise channelling for the order's salesperson and field attention for the customer's agent);
  - earned in the month the order became fully paid (or, for agents on the partial scheme, as payments are applied);
  - refunds are negative lines in their own month.
- **FR-131**: Measures: commission amount and commission base, by agent, label, participation, customer, month and period status (provisional or closed).
- **FR-132**: Drill-down lists each commission line with the order, the line, the product, the label, the participation, the label rate, the agent's share, the base and the amount; the lines add up to the figure.
- **FR-133**: An agent without the commissions reading privilege sees only their own commissions; holders and administrators see all agents in their facility scope.
- **FR-134**: The card and instalment surcharge fields on payments (mictlanix/mbe#53) are not commissions and are not reported.

**Fiscal documents and suppliers (#102)**

- **FR-140**: **Invoice facts**: invoices, credit notes and payment receipts priced from their lines with spec 037's line rule, dated by issue, by issuer (taxpayer), facility, customer, status and type.
- **FR-141**: **Invoiced against sold**: for the sales of a period (by sale date, spec 037), invoiced = the invoice lines of live invoices linked to those sales lines, each capped at the sale line's quantity and valued at the sale line's price; not invoiced = sold − invoiced; coverage = invoiced ÷ sold. Standalone invoices (no linked sale) are a separate measure, so they never inflate coverage. Not invoiced drills down to the sales lines. Invoicing lag = days from sale to first invoice, as an average weighted by invoiced amount; each line's lag shows in the invoice coverage detail.
- **FR-142**: **Status**: stamped (sealed and live), cancelled (stamped, then cancelled; with the cancellation date), and pending (drafts, and documents sealed but not stamped). Discarded drafts that were never issued aren't counted.
- **FR-143**: **VAT summary**: output VAT per issuer and month = VAT of stamped live invoices − VAT of stamped live credit notes; payment receipts are listed, not added (their VAT belongs to the invoices they pay). Input VAT is out of scope: supplier invoices aren't recorded.
- **FR-144**: **Purchases**: purchase order lines of confirmed purchase orders, before tax, by supplier (the order's), product, brand, label, warehouse and month; and received quantity and value from the receipts linked to them. The purchase's date is the order's confirmation (spec 033 uses the same moment). Legacy purchase orders count, as legacy sales do in spec 037.
- **FR-145**: **Price history**: every purchase of a product with date, supplier, unit price before tax (after discount) and change from the product's previous purchase (amount and %). Changes beyond ±10% (settable per request) are flagged. Average purchase price per product, supplier and month.
- **FR-146**: **Purchase against sale**: per product and period, the average purchase price before tax and the average sale price without VAT, and their difference and %, cost privilege only.

**Saved views (all areas)**

- **FR-150**: A user MUST be able to save a definition (query, dashboard, detail or customer 360) with a name and a relative period, list their views and the views shared with them, open one, rename it, change it and delete it.
- **FR-151**: Relative periods:
  - today and yesterday;
  - this week, this month, this quarter and this year to date;
  - last week, last month, last quarter and last year;
  - the last N days;
  - fixed dates.
  They are resolved in business time when the view runs. A comparison is kept as the comparison kind (previous period, same period last year), not as dates.
- **FR-152**: A view is shared with named users. Opening a shared view runs it with the opener's privileges and scope; only the owner can change, delete or schedule it.
- **FR-153**: A view is validated against the catalog when saved and when opened; a view that no longer validates is refused with a coded error and is not run partially.

**Scheduled reports**

- **FR-160**: The owner of a saved view MUST be able to schedule it: daily, weekly (on chosen weekdays) or monthly (on a day of the month, the last day when the month is shorter), at a time in business time, as PDF or XLSX (CSV also allowed), with a subject and recipients. Recipients are active users of the system (at most 10 per schedule), each sent to the email address on their user record. A recipient MUST hold the privileges the view needs (every family it reads, and the cost privilege if it has a cost measure); this is checked when the schedule is saved and again at each send, and a recipient who no longer qualifies is left out of that send and recorded as such. The figures are the owner's (FR-161), so the facility scope is the owner's. No free-typed address is accepted.
- **FR-161**: At send time the view runs as its owner then: their privileges and facility scope (rebuilt from the user's current record), the period resolved against that day. If the owner is inactive, lost a needed privilege, or the view no longer validates, nothing is sent and the failure is recorded with its code.
- **FR-162**: Every attempt is recorded: schedule, occurrence, time started and finished, recipients, format, size, outcome (sent, failed with code, skipped). A send is recorded as sent only once the mail server has accepted the message; a refusal is recorded as failed with spec 024's `email.send_failed`. Owners see their schedules' history; administrators see all.
- **FR-163**: Each scheduled occurrence is sent at most once, even with several schedulers against the same database or a scheduler that was down: a run claims the occurrence in the database before sending, and a missed run sends only the latest missed occurrence.
- **FR-164**: An attachment over a size cap (10 MB) is not sent; the failure is recorded and the owner is told to narrow the view. Mail is sent through spec 024's mailer; when mail isn't configured, runs are recorded as skipped with spec 024's code.
- **FR-165**: Schedules run from a command-line job started on a schedule by the server (like the existing jobs), enabled per instance by a setting so only one instance sends. The plan documents the server's schedule entry; installing it on a server is a deployment step.

**Dashboard**

- **FR-180**: The dashboard (spec 037) MUST gain these tiles and alerts, each omitted for callers without its family's privilege:
  - **Inventory**: stock value by facility (cost privilege), stock-outs, products below reorder point, dead stock value (180 days), uncosted stock;
  - **Receivables**: overdue balance with its month-end trend (12 months of history), top 10 debtors with "others", collected against due this period, DSO;
  - **Fiscal**: sales not invoiced in the period and coverage;
  - **Payables**: an overdue payables alert (only once the supplier cutoff is set).
  Each tile equals the query's answer for the same measure, period and scope.
- **FR-181**: The performance targets of spec 037 (SC-003, as relaxed) apply to the dashboard with these tiles; any new query over 12 months, and stock as of any date for all products and warehouses, in under 5 s.

**Security**

- **FR-170**: Families and their readers (any one of), reusing existing privileges; the plan maps each to its SystemObject:

  | Family | Readers |
  |---|---|
  | Inventory | The stock and kardex screens' readers (spec 030), the warehouse reports' privileges |
  | Inventory value, turnover, cost | Plus the cost privilege (spec 037) |
  | Receivables, customer 360 | The receivables screen's readers (spec 033), customer debt report |
  | Payables | The payables screen's readers (spec 033) |
  | Commissions | The commissions screen's readers (spec 025); every agent sees their own |
  | Fiscal | Fiscal documents read (23) |
  | Purchases and prices | Purchase orders read (spec 031) |
  | Saved views | Any user who can run the view's definition |
  | Scheduling | Any user, for their own views; the view's privileges are checked at send time |

  No new privilege unless the plan shows one is needed.
- **FR-171**: Facility scope: as spec 037 (FR-061). Inventory is scoped by the warehouse's facility, receivables by the order's, purchases by the purchase order's, fiscal documents by the issuing facility, commissions by the agent's facility.

### Legacy questions

How each question of the current system's inventory, receivables, commissions, fiscal and supplier reports is answered (the rest were mapped in spec 037). "Q" is the query, "D" the dashboard, "Detail" the drill-down, "V" a view of this spec.

| # | Legacy report | Question | New answer |
|---|---|---|---|
| 23 | Fiscal documents (48) | Which CFDI were issued? | Detail: invoices, filter issuer, period, status; Q: invoices, VAT, by issuer, status, type (FR-140–FR-143) |
| 24 | Warehouse stock | What is in each warehouse and what is it worth? | Q: on hand, available, value by warehouse, product, as of any date from FR-102 |
| 25 | Stock by lot | Stock per lot | **Retired**: no lot data exists (FR-111) |
| 26 | Stock by serial number | Stock per serial | **Retired**: no serial data exists (FR-111) |
| 27 | Warehouse restock | What should we restock? | Q: restock suggestions (FR-110); **legacy answered "below minimum" with no minimum set**, so it listed everything at zero |
| 28 | Kardex | Movements of a product with balance and cost | V: kardex with value, any range from FR-102 (FR-104) |
| 29 | Serial number kardex | Movements of a serial | **Retired**: no serial data exists |
| 30 | Products by supplier (75) | What does each supplier sell us? | Q: purchases by supplier, product (FR-144); the supplier's catalog itself is the product list filtered by supplier, not analytics |
| 32 | Store movements summary (99) | Movements by facility and type | Q: units in, units out by facility, movement type, month (FR-103) |
| 33 | Customer debt (46) | Who owes what, how late? | Q: receivable, overdue by customer, aging bucket; D: overdue trend, top debtors |
| 34 | Credit and collection | How well do we collect? | Q: DSO, CEI, collected against due; **the legacy report was never finished** |
| 35 | Customer 360 | Everything about a customer | V: customer 360 (FR-125); **the legacy view was never built** |
| 37 | Commissions by salesperson (112) | What did each agent earn, and from what? | Q: commission by agent, label, participation, month; Detail: commission lines (FR-130–FR-132) |
| 38 | Download CSV files (113), purchases | Purchases as CSV | Export of the purchase detail (FR-144), under the bulk export rule of spec 037 |

### Key Entities

- **Stock position**: on hand, reserved, available and in transit per product × warehouse at a moment, with value at current cost; derived from the ledger, never stored.
- **Receivable snapshot**: one row per open credit order per month-end: order, customer, facility, salesperson, agent, due date, balance, bucket, and the customer's credit limit then. The only stored history in this spec.
- **Commission line**: spec 025's line (order, line, product, label, participation, rates, base, amount, period, status), read as it is.
- **Invoice coverage**: per sales line, sold and invoiced quantity and amount, and the lag.
- **Saved view**: owner, name, kind (query, dashboard, detail, customer 360), definition with a relative period, shared users, created and changed times.
- **Schedule**: view, cadence (daily, weekly days, monthly day), time, format, recipients, subject, enabled, next occurrence.
- **Send record**: schedule, occurrence, started, finished, recipients, format, size, outcome and code.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On mbe_dev, on hand by product and warehouse equals spec 030's stock for every pair today, and stock as of 10 sampled dates since 2025-02-07 equals the kardex balance on those dates for 20 sampled pairs.
- **SC-002**: Today's receivables by customer and bucket equal spec 033's receivables summary to the cent; each stored month-end, rebuilt, gives the same figures.
- **SC-003**: For 3 agents and 2 months, commissions equal spec 025's computation for those periods to the cent, and every commission figure's lines add up to it.
- **SC-004**: For 3 sampled months, invoiced + not invoiced = sold to the cent, and the not-invoiced detail lists exactly the sales lines without a live invoice line.
- **SC-005**: Every legacy question in the table marked Q, D, Detail or V is answered on mbe_dev by the stated request.
- **SC-006**: A caller without the cost privilege finds no cost, value, margin or turnover in any response or export of this spec; a caller without a family's privilege gets no tile, row or alert of it.
- **SC-007**: A saved view opened on two different days resolves its relative period to each day's dates and returns the same figures as the equivalent query.
- **SC-008**: Over 30 days of simulated occurrences with two schedulers running at once, each occurrence is sent exactly once, and every attempt, sent or failed, is recorded.
- **SC-009**: Performance: the dashboard with the new tiles under 2 s for a month and 5 s for a year (p95, all facilities); stock as of any date for all products and warehouses, 12-month queries and the kardex of any product under 5 s.

## Assumptions

- **Cost without VAT**: cost and purchase prices are without VAT (spec 031's clarification). Spec 037's division by 1.16 is fixed separately on `fix/analytics-cost-without-vat`, which must merge before this spec is implemented, so margin, stock value and purchase prices share one basis.
- **Current cost only**: there is no cost history, so stock value, kardex value and turnover use today's average cost; this is stated in each definition, not hidden.
- **Placeholder costs**: costs of 99,999 or more are placeholders (20 stock pairs worth 87.5M) and are treated as uncosted, like cost 0.
- **Reservations today only**: reservations have no history, so reserved and available exist only for today.
- **Receivables history**: rebuilt from 2025-01 through spec 033's rule; legacy orders closed by the paid flag count as closed in every past month-end (spec 033's rule today), which understates 2025 month-ends by up to $2.79M; credit limits of backfilled month-ends are today's, so utilization history is real only from the first month-end after release.
- **Lead time**: no maintained lead time exists (the legacy `lead_time_purchase` table is a snapshot of unknown age), so lead time is a single setting with a per-request override.
- **Sales agent vs salesperson**: receivables group by both; spec 033's screen uses the customer's agent, spec 037's sales use the order's salesperson.
- **Payables are empty until go-live**: the supplier cutoff isn't set on mbe_dev, so payable figures are zero there; tests use fixtures.
- **Purchases include legacy orders** from 2012 on, like sales in spec 037; their prices are before tax (spec 031); lines at price 0 are left out of price history.
- **No supplier invoices or input VAT**: the system records no supplier CFDI.
- **Commissions rule is spec 025's** and approved there; its two clarified points (VAT-included base, fully paid) are restated, not reopened. The 302 legacy history lines zeroed by hand (2025) are history, not part of the rule.
- **Mail**: spec 024's mailer; mail isn't configured on dev or test, so end-to-end sends are verified only where SMTP settings are supplied; otherwise runs are recorded as skipped.
- **Scheduling**: no scheduler exists; the job is a command-line module like `app/jobs/*`, run every few minutes by the server, enabled on one instance (dev and test share mbe_dev).
- **Test data on mbe_dev**: warehouse 19's count entries, the transit test entries of 2026-10-25 and the cost-change test rows of 2026-10-26 are excluded from measurements, not deleted.
- **Out of scope**: the mbe-ui screens; external BI; input VAT; lot and serial reporting; editing commission rules (spec 025); the payment surcharge fields (mictlanix/mbe#53); forecasting beyond due-date grouping.
