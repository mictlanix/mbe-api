# Feature Specification: Business analytics (sales, margin and payments)

**Feature Branch**: `037-business-analytics`

**Created**: 2026-11-04

**Status**: Ready for implementation

**Input**: User description: "Business analytics for the new MBE, designed from current best practice rather than the current system's reports: one metrics layer with defined measures and dimensions, fast pre-aggregated data, a single dashboard endpoint for a modern visual dashboard, drill-down from every number to its documents, and exports to Excel (XLSX), PDF and CSV that match the screen exactly. This spec builds the foundation and the sales, margin and payments analytics." Roadmap: mictlanix/mbe-roadmap#88, mictlanix/mbe-roadmap#90, mictlanix/mbe-roadmap#92, mictlanix/mbe-roadmap#98, mictlanix/mbe-roadmap#104 (epic mictlanix/mbe-roadmap#9).

## Overview

The current system answers each business question with its own report: 38 screens, each with fixed columns, a CSV download and a printable page (`docs/specs/11-reports.md`). This spec replaces that model for sales, product sales, gross profit, best sellers, received payments and the CSV downloads with four things:

- **A metrics catalog**: named measures and dimensions, each defined once and used by every screen, export and module.
- **One query** that combines any measures with up to three dimensions, filters, a time grain and a comparison period.
- **One dashboard** that returns every tile for a period in a single call.
- **Drill-down and exports** built from the same query, so a number on screen, in Excel, in a PDF and in a CSV is always the same number.

The mbe-ui dashboard and screens are a separate mbe-ui spec; this spec defines the contract they consume. Stock and kardex, receivables and collection, commissions, fiscal documents and supplier analytics come in the next spec, on the same foundation.

### What mbe_dev shows (measured 2026-11-04, read only)

These measurements drive the decisions below.

| Fact | Value |
|---|---|
| Confirmed sales orders per year | 16,299 (2021) → 34,163 (2025); 26,818 in 2026 to date |
| Confirmed order lines per year | 55k–71k (2025: 70,683) |
| Customer payments per year | 12,780 (2021) → 31,652 (2025) |
| Confirmed refunds per year | 359 (2021) → 1,671 (2025) |
| 2025 distinct facilities / points of sale / salespeople / customers / products sold | 5 / 9 / 24 / 1,781 / 7,105 |
| Raw aggregation of all 2025 lines, by month or top 20 products | 0.23–0.25 s, with a full scan of `sales_order` (no index on its date) |
| Distinct (day, facility, product, customer, salesperson) in 2025 | 67,837, which is 96% of the line count |
| Lines whose price includes VAT | 70,683 of 70,683 (VAT 16% on all but 56) |
| Lines with cost 0 | 12,397 (17.5%), including the top-selling ready-mix concrete |
| Lines priced below cost | 4,112 |
| Non-MXN documents | 0 |
| Sales order confirmation timestamp | None: `date` equals the creation time on every 2025 order; confirming sets no date |
| Order times | Real times: 8:00–18:00, peaking 9:00–12:00; Monday–Saturday |

## Clarifications

### Session 2026-11-04

- Q: A sale's date: the request asks for the confirmation date, but no confirmation timestamp exists. → A: Record one. From now on, confirming a sales order stores the moment of confirmation. The migration that adds it fills it for every order already confirmed, with the order's date. Sales orders are retired from the legacy system, so every confirmed order has one and no fallback is needed. A delivery date was considered and set aside: 8,018 of 2025's sales have only delivery orders the legacy system left unfinished, so most history has no delivery date.
- Q: Lines with no cost (17.5% of 2025 lines) make margins look like 80%. How should margin treat them? → A: Cost, margin and margin % cover only lines that have a cost; a cost coverage measure shows the share of net sales they represent, and an alert lists the lines without a cost.
- Q: Measurements show a full year of raw lines aggregates in a quarter of a second, and a daily summary would barely compress. Build pre-aggregated summary tables anyway? → A: No. Query the documents directly, with new indexes and a short cache. Summary tables are built only if a performance target (SC-003) is missed, and the plan records the measurement that shows it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One trusted definition of every sales number (Priority: P1)

A manager asks "how much did we sell last month, and what did we earn on it?" Today the answer depends on which report they open: some include VAT, some net refunds, some count cancelled orders. With this spec there is one catalog of measures. Each measure has a written definition (what counts, which date, with or without VAT, how refunds and credit notes net), and every screen, export and other module that shows that number uses the catalog.

**Why this priority**: Every other story is built on these definitions. A dashboard with a wrong definition is worse than no dashboard.

**Independent Test**: Ask the catalog for its measures and dimensions; for one day on mbe_dev, compute net sales and collected payments through the query and compare them with the sales order list and the cash cuts of that day.

**Acceptance Scenarios**:

1. **Given** the catalog, **When** a client asks for it, **Then** it lists every measure and dimension with its key, label, unit (money, quantity, count, ratio), whether it is sensitive (cost and margin), and the dimensions it can be grouped by.
2. **Given** one business day on mbe_dev, **When** net sales for that day are queried, **Then** they equal the sum of the totals of that day's confirmed orders shown by the sales order list, minus that day's refunds and credit notes, to the cent.
3. **Given** one closed cash session, **When** collected payments are queried filtered by that session, **Then** they equal the cash cut's figures for that session, by payment method, to the cent.
4. **Given** an order cancelled after confirmation, **When** sales are queried for its date, **Then** it contributes nothing.

---

### User Story 2 - Ask any sales question without a dedicated report (Priority: P1)

A salesperson's supervisor wants "units and net sales by brand for Juan, this quarter against last quarter". There is no report for that combination today. With the query, they pick the measures, up to three dimensions, the filters, a time grain and a comparison, and get rows plus totals, already ranked, with "others", share of total, and change against the comparison.

**Why this priority**: This is how the 38 legacy questions are answered without 38 screens.

**Independent Test**: Run the query for each row of the legacy question table (FR-030) and check that the answer is produced.

**Acceptance Scenarios**:

1. **Given** measures, dimensions and filters from the catalog, **When** the query runs, **Then** it returns one row per combination of dimension values, a totals row, and `as_of`; the rows add up to the totals exactly.
2. **Given** a top-N limit, **When** the query runs, **Then** it returns the N largest rows by the chosen measure and one "others" row with the rest, so the totals still add up.
3. **Given** a comparison (previous period or same period last year), **When** the query runs, **Then** each row and the totals carry the comparison value, the difference and the % change, computed by the server.
4. **Given** a measure, dimension or filter not in the catalog, more than three dimensions, or an invalid period, **When** the query is sent, **Then** it is refused with a coded error (spec 036) naming the problem.
5. **Given** a caller without the cost privilege, **When** they request a cost or margin measure, **Then** it is refused; **When** they request other measures, **Then** no cost or margin field appears anywhere in the response.

---

### User Story 3 - A glanceable dashboard in one call (Priority: P1)

A store manager opens the app in the morning and sees, for today or a chosen period and their facility: net sales, margin %, cost coverage, orders, average ticket, collected, refunds, each against the comparison period with a sparkline; charts of sales over time, by facility, by category and brand, top products and customers, payment mix and a weekday × hour heatmap; and a short list of alerts that need action.

**Why this priority**: The dashboard is the first thing every manager sees and the main reason for this spec.

**Independent Test**: Call the dashboard for a period on mbe_dev; check that each KPI equals the same measure from the query for the same period and scope, and that a caller lacking a privilege gets no tile for it.

**Acceptance Scenarios**:

1. **Given** a period and facility scope, **When** the dashboard is requested, **Then** one response carries every tile the caller may see, and every number equals what the query returns for the same measure, period and scope.
2. **Given** a caller without the cost privilege, **When** the dashboard is requested, **Then** margin tiles are absent from the response, not zero.
3. **Given** an alert with a count, **When** its drill-down filter is used, **Then** the detail endpoint returns exactly that many documents.

---

### User Story 4 - From any number to the documents behind it (Priority: P2)

A number looks wrong. The manager taps it and sees the orders, lines, payments or refunds that make it up, sorted and paged, each linking to its existing screen.

**Why this priority**: Without drill-down nobody trusts the numbers; it also replaces the legacy line-level and order-listing reports.

**Independent Test**: For an aggregate from the query, follow its drill-down filter and check that the detail rows sum to the aggregate.

**Acceptance Scenarios**:

1. **Given** any row, total or tile, **When** it is returned, **Then** it carries the filter that reproduces it.
2. **Given** that filter, **When** the detail endpoint is called for orders, lines, payments, refunds or credit notes, **Then** it returns the documents, paged and sortable, and the detail figure FR-041 names for the measure equals the aggregate.
3. **Given** each detail row, **Then** it identifies its document so a client can open the existing screen for it.

---

### User Story 5 - Exports that match the screen (Priority: P2)

An accountant needs last month's sales by product in Excel, a manager wants a PDF of the dashboard for the monthly meeting, and an external tool needs a CSV of payments. Each export is built from the same query the screen used, so the numbers match exactly.

**Why this priority**: Replaces the legacy CSV downloads and printable pages (#104).

**Independent Test**: Export a query result as XLSX, PDF and CSV and compare every value and total with the query's JSON response.

**Acceptance Scenarios**:

1. **Given** a query, **When** it is exported as XLSX, **Then** the workbook has a cover sheet (title, filters, period, comparison, generated at and by whom) and a data sheet with typed cells (numbers as numbers, dates as dates, money with two decimals), a frozen header row, an autofilter, sized columns and a totals row.
2. **Given** a query or the dashboard, **When** it is exported as PDF, **Then** the document has the company header, filters, KPIs, the charts drawn the same way as on screen, tables with headers repeated on every page and totals, page numbers, and landscape pages where the table is wide.
3. **Given** a query, **When** it is exported as CSV, **Then** the file is UTF-8 with BOM, uses ISO dates and dot decimals, and carries the same rows and totals.
4. **Given** an export larger than the threshold, **When** it is requested, **Then** it runs in the background and the caller gets a link to download it that expires; smaller ones are returned directly.
5. **Given** a caller without the cost privilege, **Then** no export contains cost or margin.

---

### User Story 6 - Payments received (Priority: P2)

A cashier supervisor reviews payments received by method, facility, cash session and salesperson for a period, with refunds paid out shown separately.

**Why this priority**: Replaces the two received-payments reports (#98).

**Independent Test**: Query collected payments by method and facility for a period and compare with the cash cuts of the sessions in that period.

**Acceptance Scenarios**:

1. **Given** a period, **When** collected payments are grouped by method, **Then** the totals equal the sum of the cash cuts' figures for the sessions in that period, by method.
2. **Given** a refund paid out in cash or by transfer, **Then** it appears in refunds paid out, never as a negative payment.

### Edge Cases

- **Cancelled after confirmation:** an order cancelled after it was confirmed leaves the sales of every period, including periods already viewed or exported.
- **Refund in a later period:** a refund nets against the period in which it is confirmed, not the period of the original sale, so a closed month's net sales can only change when a document dated in it changes.
- **Cross-facility refunds:** a refund made in a different facility from the sale nets against the facility where the refund was made.
- **No sales:** a period or scope with no sales returns zero totals and empty series, not an error; percentage changes against zero are null, not infinite.
- **Lines without cost:** they count in net sales and units, and are left out of cost and margin; cost coverage shows the share they leave out. A period where no line has a cost has null margin %, not 100%.
- **Negative or zero quantities:** 63 lines in 2025 have a quantity ≤ 0. They count as they are, and drill-down shows them.
- **Products with several labels:** grouped by label, such a product counts once under each of its labels, so label rows can add up to more than the total; the response says so, and the totals row is the true total.
- **Missing dimension values:** an order with no salesperson, a product with no brand or a payment with no cash session groups under an explicit "none" value.
- **Facility scope:** a caller limited to their facility who asks for another facility is refused with spec 034's facility code; one who asks for all facilities gets only theirs.
- **Late documents:** a document dated in the past (a legacy-system write, a corrected date) changes that past period; `as_of` and the cache's freshness rule cover it.
- **Comparison periods:** a comparison period that starts before the earliest data returns zeros for the missing part.
- **Export of an empty result:** the export has the cover sheet and a header row with a zero totals row.

## Requirements *(mandatory)*

### Functional Requirements

**Metrics catalog**

- **FR-001**: The system MUST keep one catalog of measures and dimensions, served to clients, in which each entry has a stable key, a label, a unit, the dimensions it supports and whether it is sensitive. Every number this spec serves comes from a catalog measure.
- **FR-002**: **What counts as a sale.** A sale is a sales order that is confirmed and not cancelled. Its date is the moment it was confirmed, in business time (America/Mexico_City). Confirming an order records that moment. The migration that adds it fills it for every order already confirmed with the order's date, the best record of when it was confirmed (the two are the same day on nearly every order). Every confirmed order therefore has a confirmation moment; only drafts have none. Drafts, cancelled orders and quotes never count.
- **FR-003**: **Money and tax.** Every money measure is available without VAT (the default) and with VAT. Amounts follow the existing line rule: a line's gross is quantity × price × (1 − discount rate); when the price includes VAT, the amount without VAT is gross ÷ (1 + VAT rate). Amounts are in the document's currency, as the sales order screens show them (they apply no exchange rate either); every document on mbe_dev is MXN, and implementation re-checks that before release. Converting other currencies is out of scope. Order totals round once per document, exactly as the sales order screens do, so a day's sum equals the sum of the order totals those screens show.
- **FR-004**: The catalog MUST define these measures, each exactly once:

  | Measure | Definition | Date it falls on |
  |---|---|---|
  | Gross sales | Sum of the amounts of the lines of sales (FR-002), after line discounts | The sale's date |
  | Discounts | Sum of quantity × price × discount rate on those lines (the amount given away) | The sale's date |
  | Refunds | Sum of the amounts of confirmed, non-cancelled refund lines, computed with the same line rule | The refund's confirmation date |
  | Credit notes | Sum of the amounts of stamped, non-cancelled CFDI credit notes (rebates and adjustments; none documents a refund) | The credit note's issue date |
  | Net sales | Gross sales − refunds − credit notes | Each part on its own date |
  | Costed net sales | The part of net sales, without VAT, that comes from lines with a cost (cost > 0); refund lines follow the cost of the sale line they return | As its parts |
  | Cost coverage | Costed net sales ÷ net sales without VAT; null when net sales are 0 | As its parts |
  | Cost of sales | Sum of quantity × the line's cost snapshot without VAT (the cost includes VAT like the price, so it is divided by 1 + the line's VAT rate), for sales lines with a cost, minus the same for refund lines (through the refunded sale line's cost) | As its sale or refund |
  | Gross margin | Costed net sales − cost of sales | As its parts |
  | Margin % | Gross margin ÷ costed net sales; null when costed net sales are 0 | As its parts |
  | Units | Sum of line quantities of sales minus refunded quantities | As its parts |
  | Orders | Count of sales | The sale's date |
  | Average ticket | Gross sales ÷ orders (with VAT by default, as a customer pays it); null when orders are 0 | The sale's date |
  | Collected | Sum of positive payments that are not store credit, net of the change given on their applications: the cash cut's sales tender | The payment's date |
  | Payments | Count of the payments in Collected | The payment's date |
  | Refunds paid out | Sum of negative payments: refunds paid back as money | The payment's date |
  | Store credit issued | Sum of store credit created by refunds (positive credit-note payments). Spending it later is an application of that same payment, never counted again | The payment's date |

  Lines without a cost are left out of cost and margin, never counted as pure profit; cost coverage says how much of net sales the margin describes. Measures are additive across every dimension except ratios (margin %, cost coverage, average ticket), which the server recomputes from their parts at every level, never averages.
- **FR-005**: **Sensitive measures.** Costed net sales, cost coverage, cost of sales, gross margin and margin % are sensitive. Only callers with the cost privilege (FR-060) may request them; for everyone else they are absent from the catalog's response, refused in queries, and absent from the dashboard, drill-down rows and exports.
- **FR-006**: The catalog MUST define these dimensions, with their keys and the measures they apply to:
  - **date**, at day, week (ISO, Monday first), month, quarter and year grains, in business time;
  - **facility** and **point of sale**;
  - **salesperson** (the order's), and for payments the cashier's session;
  - **customer**, and **customer type** (credit or cash, from the customer's current credit limit, using the rule the sales screens use today);
  - **product**, **brand**, **model** and **label**; the label serves as the product category, since products have no other category;
  - **payment method**, and **payment terms** (immediate or credit, from the order);
  - **weekday** and **hour of day**, for the heatmap.
  Payment measures group only by date (and weekday and hour), facility, cash session, customer, customer type, salesperson (the cash session's cashier) and payment method; a request mixing a payment measure with a product dimension is refused. Credit notes carry no product, order or salesperson, so grouped by those they fall under "none". The cash cut's refunds equal refunds paid out plus store credit issued.

**Query**

- **FR-010**: A query endpoint MUST accept measures, up to three group-by dimensions, filters on any catalog dimension (equals, in), a period (start and end dates), a time grain, an optional comparison (previous period of the same length, or the same period last year), an optional sort, and an optional top-N, and return rows, a totals row and `as_of`.
- **FR-011**: The query MUST be validated against the catalog. Nothing the client sends is used as SQL, a column name or a table name. Unknown measure, dimension or grain keys fail request validation (FastAPI's 422, since the keys are published as enumerations). Measures that don't apply to a dimension, more than three dimensions, periods over five years, end dates before start dates and sorts on unrequested keys are refused with spec 036 codes (`analytics.*`) whose params name the offending key.
- **FR-012**: The rows MUST add up to the totals for every additive measure, including when top-N folds the rest into an "others" row. The "others" row and the totals are computed by the server.
- **FR-013**: For each row and the totals, the server MUST compute the share of the total for each additive measure, and with a comparison, the comparison value, the difference and the % change (null when the comparison is 0). Clients never compute these.
- **FR-014**: Money in responses is a decimal string with two decimals, quantities with up to four, ratios as fractions with four decimals (0.2530 = 25.3%), following the existing API conventions.
- **FR-015**: Every response (catalog, query, dashboard, detail, export) states `as_of`, the moment its data was read.

**Freshness and performance**

- **FR-020**: Data MUST be fresh within five minutes of a document being confirmed, cancelled or paid. Analytics read the documents directly, so they are as fresh as the documents. Summary tables are added only if a performance target is missed (see Clarifications); if they are, they are maintained incrementally, a command rebuilds them in full, and `as_of` reports their refresh time.
- **FR-021**: Identical requests within a short window (at most 60 seconds) MAY be answered from a cache, which never serves data older than FR-020 allows and never serves one caller's response to a caller with different privileges or facility scope.
- **FR-022**: The confirmation moment (FR-002) and the indexes needed to meet SC-003 are added through numbered SQL migrations (spec 009); the indexes are checked against the query plans. The confirmation column's migration fills it for every existing confirmed order; it is empty only on drafts.

**Dashboard**

- **FR-030**: Every question that the current system's sales, payments and product reports answer MUST be answerable with the query or the dashboard. The table under Legacy questions shows how, and says where a legacy report answers a question wrongly or one nobody should ask.
- **FR-031**: A dashboard endpoint MUST return, in one response, for a period and facility scope:
  - **KPIs**, each with its value, the comparison value, the difference, the % change and a sparkline series at the period's natural grain: net sales, margin %, cost coverage, orders, average ticket, collected (today and period), refunds;
  - **series**: net sales and gross margin over time with a comparison line; net sales by facility; net sales by label and by brand; top 10 products and top 10 customers with "others"; collected by payment method; orders by weekday × hour;
  - **alerts** (FR-032).
  Every number in it is without VAT except average ticket (with VAT, FR-004), and equals the query's answer for the same measure, period, scope and VAT treatment.
- **FR-032**: The dashboard MUST include these alerts, each with a count, an amount where it applies, and the drill-down filter that lists its documents:
  - **Overdue credit orders:** confirmed credit orders with a balance past their due date (spec 033's rule), in scope, as of today whatever the period.
  - **Lines sold below cost** in the period: lines whose net price is below their cost (4,112 in 2025); the amount is those lines' sales without VAT. Only for holders of the cost privilege.
  - **Lines sold without a cost** in the period (12,397 in 2025), since they distort margin; the amount is those lines' sales without VAT. Only for holders of the cost privilege.
  - **Refund rate above normal:** refunds ÷ gross sales for the period, per facility, one alert for each facility where it is raised, with the count and sum of that facility's refunds in the period. It is raised when the rate exceeds both twice the median of the facility's monthly rates over the 12 calendar months before the period starts and 20%, with at least $20,000 refunded in the period (whatever its length). With fewer than 12 prior months the available ones are used; with none, the alert isn't raised. A plain "twice the average" rule fires on 28 of 158 facility-months since 2024; the thresholds are backtested before release and fire on at most 1 in 20 (7 of 169 facility-months over 2024–2026, research R14).
  No other alerts in this spec; each must be justified by mbe_dev data when it is built (thresholds recorded in the plan).
- **FR-033**: Tiles, series and alerts a caller has no privilege for are omitted from the response, not zeroed.

**Drill-down**

- **FR-040**: Every row, total, KPI, series point and alert MUST carry the filter (measures excluded) that reproduces it as a query.
- **FR-041**: A detail endpoint MUST take such a filter and a document kind (orders, lines, payments, refunds, credit notes) and return the documents behind it, paged and sortable, each with its identifier and its contribution to the measure. The contributions of all pages add up to the aggregate. Alerts whose condition isn't a dimension use a flag on the detail request: `overdue` (orders, amount = balance, as of today), `below_cost` and `without_cost` (lines, cost privilege only), and `collected`, `paid_out` and `store_credit` (payments, one class each), so an alert's drill-down lists exactly its count. Each base measure equals one figure of one kind's detail (the contract's table: an amount, quantity or discount sum, or the document count for orders and payments; payments split by class). A derived measure reconciles as the same combination of its parts' details: net sales = lines − refunds − credit notes; units, cost of sales and gross margin = lines − refunds. Costed net sales and the ratios have no drill of their own.
- **FR-042**: Detail rows follow the same privilege and scope rules as the aggregates: no cost or margin without the cost privilege, nothing outside the caller's facility scope.

**Exports**

- **FR-050**: Exports MUST be produced from a query definition (or the dashboard request) by running it the same way as the screen does, so every value matches the JSON response for the same request and `as_of`.
- **FR-051**: XLSX exports have a cover sheet (title, period, comparison, filters in words, generated at, generated by) and a data sheet with typed cells: numbers as numbers, dates as dates, money formatted with two decimals, percentages as percentages; a frozen header row, an autofilter, column widths fitted to content, and a totals row. Large results are written as a stream, without holding the workbook in memory.
- **FR-052**: PDF exports use the existing document printing (spec 019): company header, period and filters, KPIs, the charts drawn as vector graphics by the server the same way the screen draws them, tables with headers repeated on each page and a totals row, page numbers, landscape pages when a table has more than eight columns.
- **FR-053**: CSV exports are UTF-8 with a byte order mark (so Excel reads accents), comma separated, ISO 8601 dates, dot decimals, a header row and a final totals row.
- **FR-054**: Exports up to 10,000 rows are returned directly. A PDF holds at most 2,000 table rows; a larger one is refused with a suggestion to use XLSX or CSV. Larger XLSX and CSV exports run in the background: the caller gets a job identifier, can poll it, and downloads the file from a link valid for 24 hours, after which the file is deleted. Only its creator can download it.
- **FR-055**: Exports also cover the legacy "Download CSV files" (sales with lines, and payments, for a period) as detail exports of the drill-down kinds.

**Security and scope**

- **FR-060**: Each family is gated on the existing privileges of the reports it replaces; no new privilege unless the table below says why:

  | Family | Readers (any one of) |
  |---|---|
  | Sales measures, query and dashboard | Sales by customer (34), by salesperson (35), by product (36), sales order summary (47), the product-sales reports (51, 52, 53, 55, 61, 62, 63), best sellers (40, 41) |
  | Cost and margin (the cost privilege) | Gross profits by customer (37), by salesperson (38), by product (39) |
  | Payments measures | Received payments (33), received payments summary (109) |
  | Drill-down to documents | The family's privilege, plus read on the document kind (sales orders 7 for orders and lines, customer payments 8, refunds 22, fiscal documents 23 for credit notes) |
  | Bulk detail exports (FR-055) | Download CSV files (113); no user holds it today, so administrators grant it as needed |

  No new SystemObject is needed: the gross-profit privileges are already the ones that decide who sees profit in the current system.
- **FR-061**: Facility scope: every request is limited to the caller's facility unless the caller is an administrator or holds Search credits from all stores (101), as in specs 033–035. A caller limited to their facility who filters on another facility is refused with `facility.other_facility`.
- **FR-062**: Every refusal uses spec 036's error codes. New codes are added to its catalog and documented in this spec's contract.

### Legacy questions

How each sales, payments and product question of the current system is answered. "Q" is the query, "D" the dashboard, "Detail" the drill-down. Questions of the other families (stock, kardex, debt, collection, commissions, fiscal documents, suppliers) are listed for completeness and belong to the next spec.

| # | Legacy report | Question | New answer |
|---|---|---|---|
| 1 | Received payments (33) | Which payments came in? | Detail: payments, filtered by period, facility, method, customer, salesperson, cash session |
| 2 | Sales by product (36) | How much of each product did we sell? | Q: units, net sales (+ cost, margin with the cost privilege) by product; filters brand, model, label, facility. **Legacy warehouse filter dropped**: a sale's warehouse is where stock left, not a sales question; stock by warehouse comes with the next spec. |
| 3 | Gross profits by customer (37) | What do we earn per customer? | Q: net sales, cost, margin, margin % by customer. **Legacy is misleading**: it counts lines without a cost as pure profit (17.5% of 2025 lines), which is how 2025 shows an 80% margin; the new margin also puts cost and price on the same basis, both without VAT (research R4). |
| 4 | Gross profits by salesperson (38) | What does each salesperson earn us? | Q: orders, net sales, cost, margin, margin % by salesperson. Same correction as 3. |
| 5 | Gross profits by product (39) | Which products earn most? | Q: units, net sales, cost, margin, margin % by product. Same correction as 3. |
| 6 | Best selling products by customer (40) | What does this customer buy most? | Q: units, net sales by product, filter customer, top N |
| 7 | Best selling products by salesperson (41) | What does this salesperson sell most? | Q: units, net sales by product, filter salesperson, top N |
| 8 | Customers report (66) | Customer directory | Not analytics: the existing customer list and its export. |
| 9 | Sales by customer (34) | How much did each customer buy? | Q: orders, units, net sales by customer. **"Total paid" and "balance" dropped**: they mix receivables into a sales period; balances belong to receivables (next spec). |
| 10 | Sales by salesperson (35) | How much did each salesperson sell? | Q: orders, net sales (+ cost, margin) by salesperson |
| 11 | Customer sales orders (50) | Which orders did this customer place? | Detail: orders, filter customer |
| 12 | Salesperson orders (49) | Which orders did this salesperson close? | Detail: orders, filter salesperson |
| 13 | Salesperson orders and refunds (72) | Net sales per salesperson after refunds | Q: gross sales, refunds, net sales (+ margin) by salesperson. Refunds net by the refund's date and the refund's salesperson. |
| 14 | Product sales by customer (51) | Which products did a customer buy? (described as "which customers bought a product", but filtered by customer) | Q: units, net sales by product, filter customer; and the other reading: by customer, filter product |
| 15 | Product sales by brand (53) | Sales of a brand | Q: units, net sales by brand (or by product, filter brand) |
| 16 | Product sales by model (52) | Sales of a model | Q: units, net sales by model (or by product, filter model) |
| 17 | Product sales by salesperson (55) | Which products each salesperson sold | Q: units, net sales by salesperson, product |
| 18 | … by salesperson and label (61) | Same, by label | Q: by salesperson, label |
| 19 | … by salesperson and brand (62) | Same, by brand | Q: by salesperson, brand |
| 20 | … by salesperson and model (63) | Same, by model | Q: by salesperson, model |
| 21 | Sales order summary (47) | Orders, revenue, collected, balance and refunds by day or month | Q: orders, gross sales, refunds, net sales, collected by date (day or month grain); D: KPIs and series. **"Balance" dropped** as in 9. |
| 22 | Sales details summary | Line-level detail | Detail: lines |
| 23 | Fiscal documents (48) | CFDI issued | Next spec |
| 24–29 | Stock, by lot, by serial, restock, kardex, serial kardex | Inventory | Next spec |
| 30 | Products by supplier (75) | Supplier catalog | Next spec (supplier analytics) |
| 31 | Products orders and refunds by salesperson (79) | Units sold, refunded and net per product and salesperson | Q: units sold, units refunded, units, gross sales, refunds, net sales by salesperson, product |
| 32 | Store movements summary (99) | Inventory movements by facility | Next spec |
| 33 | Customer debt (46) | Balances and aging | Next spec (receivables); D's overdue alert covers the action item |
| 34 | Credit and collection | Collection performance | Next spec |
| 35 | Customer 360 | One customer's purchases, payments and product mix | D filtered by customer, plus Q by product filtered by customer. **Payments history** comes with receivables (next spec). |
| 36 | Received payments summary (109) | Totals by method and facility | Q: collected, refunds paid out by facility, payment method |
| 37 | Commissions by salesperson (112) | Commissions | Already answered by spec 025's commission periods; out of scope |
| 38 | Download CSV files (113) | Sales, purchases and payments as CSV | Exports (FR-055) for sales and payments; purchases in the next spec |

### Key Entities

- **Measure**: a named number with one definition (FR-004): key, label, unit, sensitivity, the date it falls on, how it aggregates (sum or ratio of parts).
- **Dimension**: a named way to group or filter (FR-006): key, label, grains (for date), the measures it applies to, and how a missing value is shown.
- **Query definition**: measures, dimensions, filters, period, grain, comparison, sort, top N. The single input shared by the query, the dashboard tiles, drill-down filters and exports.
- **Result**: rows, totals, comparison figures, shares, `as_of`, and the drill-down filter of each row.
- **Dashboard**: KPIs, series and alerts for a period and scope, each built from a query definition.
- **Export job**: a background export with its owner, query definition, format, status, file and expiry.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 10 sampled business days on mbe_dev, net sales equal the totals of the orders confirmed that day as the sales order screens show them, net of that day's refunds and credit notes, and collected payments equal the sum of that day's cash cuts by method, to the cent.
- **SC-002**: Every legacy question in the table above marked Q, D or Detail is answered on mbe_dev by the stated query; none needs a new endpoint.
- **SC-003**: The dashboard responds in under 500 ms at the 95th percentile for a month and for a year across all facilities; any query over 12 months across all facilities in under 2 s; an export of 100,000 rows completes in under 30 s.
- **SC-004**: For 20 sampled queries, every value in the XLSX, PDF and CSV exports equals the JSON response for the same request.
- **SC-005**: A caller without the cost privilege finds no cost, margin or margin % in any catalog, query, dashboard, detail or export response (checked by tests over every route of this spec).
- **SC-006**: A confirmed, cancelled or paid document shows in the analytics within five minutes.
- **SC-007**: For every row, total and tile sampled, its drill-down documents add up to it exactly (for a derived measure, its parts' documents combined as FR-041 states).

## Assumptions

- **Line cost includes VAT, like price**: the cost price list matches the 2025 purchase prices with VAT (median ratio 1.16 over 4,175 products), and every 2025 line has its price with VAT. Cost of sales without VAT is therefore quantity × cost ÷ (1 + the line's VAT rate) (research R4).
- **Customer type is current, not historical**: "credit or cash" uses the customer's current credit limit. A customer whose limit changed is classified by today's value; payment terms on the order give the historical credit/immediate split.
- **Labels are the category**: products have no category field; the legacy system already calls labels categories.
- **Brand and model are free text**: they are grouped by their trimmed, case-insensitive value.
- **Refund dates**: refunds count on their confirmation date (set when confirmed); the 131 refund drafts with no date never count.
- **Credit notes**: the store-credit notes written by refunds are the payout of a refund already counted, never a second reduction. CFDI credit notes are rare (2–3 a year since 2024) and none of the 126 ever issued documents a refund (research R5), so they reduce sales on their own.
- **The cash cut measures money, not sales**: it reports tender received per session, net of change, so it reconciles with Collected, not with net sales. Net sales reconcile with the sales order screens.
- **Sales are retired from the legacy system**: a module moved to the new MBE is no longer used in the legacy system, so no order is confirmed without recording its confirmation moment.
- **One currency**: all documents on mbe_dev are MXN, and the sales screens apply no exchange rate, so analytics don't either (FR-003). Implementation counts non-MXN documents on mbe_dev before release and stops if any exist.
- **New dependencies**: an XLSX writer library (XlsxWriter) is needed, since none is installed; and openpyxl, as a development dependency only, so tests can read the workbooks back. Both are declared and reviewed in the plan (constitution V).
- **Background exports** use the existing pattern for jobs (command-line modules on an external schedule) or a minimal in-process worker; the plan decides and justifies it. No new infrastructure (queue, cache server) is assumed.
- **Out of scope**: the mbe-ui screens; a generic BI tool or external warehouse; forecasting; stock, kardex, receivables, collection, commissions, fiscal documents and supplier analytics (next spec); changing who sees cost on the existing sales order screens.
