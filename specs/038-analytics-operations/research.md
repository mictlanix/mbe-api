# Research: Operations analytics (spec 038)

Measurements were taken read-only on mbe_dev on 2026-11-05. Its copy of production ends on 2026-09-29; later rows are test data, which is excluded from every figure below. The spec's "What mbe_dev shows" table summarizes them.

## R1. Two kinds of facts: flows and balances

**Decision**: spec 037's facts are **flows**: sums over a period, such as sales, refunds or payments. This spec adds **balance** facts: a state at a moment, such as stock on hand, a receivable, or what is due. The engine gains a fact kind:

| | Flow (spec 037) | Balance (this spec) |
|---|---|---|
| Value of a period | Sum over the period | Value at the end of the period |
| With a date dimension | Sum per bucket | Value at each bucket's end |
| Across other dimensions | Additive | Additive |
| Across dates | Additive | Not additive. The totals row is the value at the period's end, and shares are taken within that moment |
| Comparison | The comparison period's sum | The value at the comparison period's end |

A query may mix flows and balances, for example stock value next to cost of sales. Each measure states its kind in the catalog, so clients don't add balances across months.

**Rationale**: the spec asks for stock as of any date and aging over time. Both are balances, and summing them across a year is meaningless. The engine already merges facts in Python, so the kind only changes how date buckets combine.

**Alternatives considered**: treating stock as a flow of movements and letting clients add them up is wrong at every bucket but the last. Separate endpoints per balance would rebuild the per-report model spec 037 replaced.

## R2. Stock as of a moment comes from the ledger, in two queries

**Decision**: on hand at the end of day D is `SUM(lot_serial_tracking.quantity)` over entries dated before D + 1. This is the same sum spec 030's `stock_ledger.on_hand` and kardex opening balance use, so the two can't diverge.

With a date dimension, two queries replace one query per bucket end:
1. the **opening** balance at the period start, grouped by the requested dimensions;
2. the period's **movements**, grouped by the dimensions and the bucket.

Python then adds the movements cumulatively onto the opening.

Measured: every product × warehouse pair as of a date takes about 1.2 s, and five year-end snapshots in one pass take 1.9 s.

**Incremental moments.** The facts are computed per pair (R5), then grouped. A later moment updates only the pairs that moved in its bucket: each one is taken out of its groups at its old figures and put back at its new ones, so the rules that are not linear in on hand still hold exactly (negative stock and uncosted products add no value; stocked, costed and uncosted pairs are counts). Without `stock_options` a pair's figures depend only on its on hand, so this is exact. With them (velocity, cover, dead and slow stock) the windows move with the moment, so every pair is assessed at each moment asked; the dashboard and the detail ask for one.

**Daily checkpoint.** Each process keeps, per database, every pair's ledger sum and last entry over the entries dated up to the end of yesterday (business time) with an id up to the highest one it read, built on the first read of the day. A read at the end of day D then sums only:
- for D after the checkpoint's day, the entries dated after it up to D;
- for D before it, minus the covered entries dated after D (a range read on the date index);
- in both cases, the entries with a higher id dated on or before the checkpoint's day or D.

The last condition keeps a back-dated entry from being missed. They happen: on mbe_dev (2026-11-09) 16 days since 2025 hold an entry written after a later-dated one, up to 34 days back (2026-03-17 written after 2026-04-20). The last entry of a pair can't be taken back, so a read before the checkpoint's day that needs it (R5's dead stock) still sums the whole ledger.

The checkpoint relies on the ledger being append-only. This API only inserts into `lot_serial_tracking` (spec 030); a cancel posts a reversing entry. On mbe_dev, ids since 2025-02-07 have 22 gaps: one of 11,902 ids after id 1,217,074 (the 2025-02-06 reset) and 21 of 1–23 ids (85 in all), consistent with rolled-back inserts. Rows changed in place by the legacy application can't be detected from the data. Such a change would be picked up when the checkpoint is next rebuilt, the next day.

**Measured cause (spec 038 dashboard on dev, before this design):** a month took 22 s and a year 40 s at p95, against 2 s and 5 s. The `stock_value` sparkline reads the stock fact at every bucket: 31 moments for a month, about 53 for a year. Each read cost about 2.5 s for the opening (a `GROUP BY` over the whole ledger, 28,913 pairs) plus 0.8 s for the bucketed movements, then about 0.55 s of Python per moment, copying and regrouping every pair. All pairs by warehouse × product as of a date took 4.0–4.2 s.

Migration 032 adds an index on `lot_serial_tracking(date)`, which has none today. The as-of sum still reads every earlier row, but movements in a period become a range read.

**Reserved and available**: today only, from `stock_ledger.reserved_by_warehouse`, because reservations have no history. Asking for them at a past date gives null, and the catalog says so.

**In transit**: spec 030's `split_transit` rule. Stock in a transit warehouse belongs to the transfer's destination facility.

**Alternatives considered**: storing daily stock snapshots would hold 18k pairs a day, a new table and a writer on every movement path. The in-process checkpoint gives the same read without a table or a writer, at the cost of one full sum per process per day.

## R3. Reliable history starts on 2025-02-07

**Decision**: `settings.stock_history_start = 2025-02-07`. Stock, movements and kardex balances for dates before it are refused with `analytics.before_history`, with params `history`, `requested` and `earliest`.

The reason is the mass reset of 2025-02-06 (8,753 type-8 entries, about +1.0M units). It cleared 10,805 negative pairs, and 15,918 of 43,919 pairs had gone negative at some point before it.

**Alternatives considered**: serving earlier dates with a warning flag. Rejected, because a number that is known to be wrong would still end up exported.

## R4. Value at today's average cost; placeholders are uncosted

**Decision**:
- **Unit cost:** the product's current cost, `product_price` on list 0 (`COST_PRICE_LIST_ID`), without VAT (spec 031; spec 037 R4 as fixed in #296).
- **Uncosted pairs:** a pair whose cost is ≤ 0 or ≥ `settings.placeholder_cost` (99,999) is uncosted and contributes no value.
- **Stock cost coverage:** costed pairs with stock ÷ all pairs with stock.

Measured: placeholders account for 87.5M of the 288.4M total; 630 positive pairs have cost 0.

**Turnover**: cost of sales over the period (spec 037's sale-line and refund-line facts) ÷ the average of stock value at the period's start and end. **Days of inventory** = days in the period ÷ turnover. Both carry the caveat "at current cost" in the catalog's definition text. All of these are cost-privileged.

**Alternatives considered**: historical cost from sale-line cost snapshots. Rejected because it covers only products that sold, which is circular for dead stock.

## R5. Velocity, cover, stock-outs, dead and slow stock, restock

**Decision**: a query carries `stock_options`. Defaults and allowed ranges:

| Option | Default | Range |
|---|---|---|
| `velocity_days` | 90 | 30–365 |
| `dead_days` | 180 | 90, 180 or 365 |
| `slow_cover_days` | 180 | 30–730 |
| `lead_days` | `settings.restock_lead_days`, 7 | 0–120 |
| `cover_days` | `settings.restock_cover_days`, 30 | 1–365 |

The measures, all at the period's end (as of the end of day D):
- **Units out by sale**: −Σ ledger quantity of sale types (1, 10, 13) − Σ of refund type 2, over the `velocity_days` before D. **Velocity** is that total ÷ `velocity_days`.
- **Days of cover**: available (or on hand, at a past date) ÷ velocity; null when velocity is 0.
- **Stock-out**: velocity > 0 and available ≤ 0.
- **Reorder point**: velocity × (`lead_days` + `cover_days`).
- **Below reorder**: velocity > 0 and available < reorder point.
- **Suggested quantity**: reorder point − available, rounded up to a whole unit unless the product sells fractions. Fractions show on 423 pairs today. A product sells fractions when one of its sale entries (types 1, 10, 13) in the velocity window has a non-whole quantity; no product field says so.
- **Dead**: on hand > 0 and no ledger entry of any type in the `dead_days` before D.
- **Slow**: on hand > 0, not dead, and days of cover > `slow_cover_days`. A pair without sales has no cover, so it is never slow (it becomes dead once `dead_days` pass without an entry).

These are pair-level flags. Their measures are counts of pairs, quantities and values (cost-privileged), and they drill down to the `stock` detail kind with the same flag.

**Rationale**: the spec's clarification calls for derived reorder points only. Lead time has no maintained source: `lead_time_purchase` is a legacy snapshot of unknown age.

## R6. Kardex with value, any range, paged

**Decision**: analytics gets its own kardex route.
- **Reused from spec 030:** `kardex_service.kardex_rows` (running balance), `_documents` (references) and `source_name`.
- **Range:** spec 030's 366-day cap (`range_bounds`) is replaced by paging, at most 500 entries a page.
- **Each page** starts with the balance before its first entry, computed as the opening plus the entries skipped.
- **Value:** each row adds the running balance × current cost (cost privilege), and the response states which cost it used.

Spec 030's route keeps its cap untouched (surgical).

## R7. Receivables as of a moment: one rule with an `as_of`

**Decision**: `sales_order_service.order_balances`, `_credit_candidates` and `open_credit_orders` gain a keyword `as_of: datetime | None = None`. With `None` every query is exactly today's (spec 033). With a moment T:

- **Applications** count when they are live and their `date ≤ T`. The application's own date is used, not the payment's: they differ by more than a day on 783 of 2025's applications. The 9 rows dated 0001 count as at their payment's date.
- **Refunds** count when they are completed, not cancelled, and dated ≤ T (`refunded_by_order` gains the same keyword). Refunds without a date use `modification_time`.
- **Credit notes** count when issued ≤ T and either not cancelled or cancelled after T (`cancellation_date > T`).
- **Candidates** are credit-terms orders that are completed, not cancelled, confirmed (`confirmation_time`) ≤ T, and either not flagged paid or `balance_zeroed_time > T`.

Legacy orders flagged paid without a zero time count as closed, as spec 033 counts them today. That understates 2025 month-ends by up to $2.79M (277 orders). From 2026-01 the flag and the computed balance agree exactly. Orders cancelled after confirmation and reversed applications have no date, so they leave every month-end.

**Rationale**: the spec forbids a second formula. With `as_of=None` the existing tests are unchanged, and a new test asserts that `as_of=now` equals `None` on fixtures.

## R8. Month-end receivable snapshots

**Decision**: table `receivable_snapshot` holds one row per open credit order per month-end (data-model). The command `uv run python -m app.jobs.snapshot_receivables` is idempotent:
- with no arguments it snapshots every ended month that has no snapshot yet;
- `--from 2025-01` backfills;
- `--month YYYY-MM --rebuild` replaces one month.

Each month is written in one transaction (delete, then insert).

- **Credit limit:** the customer's limit at write time. A backfilled month marks it `limit_historical = 0`.
- **Earlier months:** month-ends before 2025-01-31 are refused with `analytics.before_history`.
- **Size:** about 600–900 open orders per month-end, so about 20k rows for the backfill.
- **Speed:** each month-end takes one `open_credit_orders(as_of=...)` call. Today that is 0.3–1.0 s with the candidate set of about 600 orders; past month-ends add the orders flagged paid later, so the backfill time is measured during implementation.

**Rationale**: a past balance takes spec 033's full rule over thousands of candidate orders, which is too slow per request for 22 month-ends. Stored month-ends are what make the trend fast and stable.

**Writes on mbe_dev**: running the backfill on mbe_dev writes rows, so it needs the user's confirmation, like a migration.

**Measured on mbe_dev** (T079, T082): `open_credit_orders(as_of=…)` takes 1.7–2.1 s for 2025-06-30 (389 orders), 1.1–1.3 s for 2026-06-30 (599) and 1.1–1.2 s for now (606, $6,861,865.40, spec 033's figure). The backfill from 2025-01 wrote 22 month-ends (13,770 rows) in 37 s; rebuilding 2025-06, 2025-12 and 2026-09 gave identical rows (count, balance and a checksum of every column but `written_at`).

## R9. Receivable measures

**Decision**: all amounts are in base currency (balance × exchange rate), as `accounts_service.summarize` computes them.

- **Receivable, current, overdue and the aging buckets:** spec 033's `days_past_due` and `bucket` at the moment, so its five buckets.
- **Credit sales:** spec 037's gross sales, with VAT, of orders with credit terms. Receivables include VAT, so DSO compares like with like.
- **DSO** = receivable at the period's end ÷ credit sales in the period × days in the period.
- **CEI** = (receivable at the start + credit sales − receivable at the end) ÷ (receivable at the start + credit sales − current receivable at the end).
- **DSO and CEI periods:** they need the receivable at both ends. The period must start on the 1st of a month and end on a month-end or today, with the start month-end within history. Otherwise the request is refused with `analytics.period_not_month_end`.
- **Collected on credit:** live applications dated in the period to credit-terms orders, by application date.
- **Due in the period:** the balance at the period's start of orders whose due date falls in the period. **Due collected** is the applications to those orders dated in the period.
- **Credit limit and utilization:** utilization is the receivable ÷ the limit per customer, null without a limit. Across customers it is computed only over those with a limit, from their parts.
- **Forecast:** today's open balances by due bucket (overdue, 1–7, 8–30, 31–60, over 60 days).
- **Open orders:** a count.

Two dimensions name a person:
- **Salesperson** is the order's.
- **Sales agent** is the customer's, as spec 033's screen groups.

## R10. Payables: today only, from spec 033's supplier balances

**Decision**: payable, current, overdue, the buckets, the forecast and the open purchase orders come from `supplier_payment_service` balances (PO total − live applications) and `accounts_service.po_due_date`. Only POs counted by `po_counts` against `supplier_balance_cutoff` are included. There is no history and no DPO, because payables have no data until the cutoff is set at go-live. Snapshots can be added later in the same shape. While the cutoff is null, payable figures are zero and the dashboard omits their tiles.

**Alternatives considered**: snapshotting payables too. Rejected because there is nothing to snapshot.

## R11. Commissions are read from spec 025

**Decision**: a commission fact built in Python.
- **Source:** for each month in the period, `commission_service.read_period(db, year, month, salesperson=…)`. A closed or paid period returns its stored lines; an open one is computed live and flagged `provisional`.
- **Several open months:** An open month in a query that also includes earlier open months shows only what closing them in order would add. Spec 025's live month assumes every earlier month is closed, so it picks up a fully paid sale an earlier open month already counts, and the partial scheme's live amount is cumulative. Each later open month's line therefore gives its amount less what the query's earlier open months gave for the same (sale line, agent, participation, movement, refund line), and a line with nothing left is dropped (decided by the user). The first open month of a query equals `compute`. Limit: the subtraction cannot add a line spec 025's live month leaves out; with no month ever closed, a refund in a later open month of a fully paid sale earned in an earlier open month is not listed, though closing in order would list it.
- **Lines** are grouped in Python by agent (the line's `salesperson`, a commission agent), label, participation, customer and month.
- **Measures:** commission (Σ amount), base (Σ the line's price × quantity), and lines (count).
- **Detail:** the `commission_lines` detail kind lists the lines themselves, with every field a salesperson needs to recompute one.

**Own commissions**: a caller without 112 who is a commission agent (`commission_agent.employee = current.employee_id`) gets their own lines only: the engine forces a filter on agent = self. Anyone else without 112 is refused (`auth.forbidden`).

**Performance**: a live `compute` of one month runs spec 025's candidate query. It is timed during implementation; closed months read stored rows. A 12-month query mostly reads stored months once periods are closed. None are closed on mbe_dev today, so all would be live there. Timed read-only on mbe_dev: `compute` takes about 3.5 s per open month (2.75 s in `_candidates`), so reading each month once per slice, one after another (24 calls for 12 months, 48 with a comparison), took about 85 s. So a request reads each distinct month once, before the slices run (rows, totals and the comparison share them), and reads the months concurrently, each on its own session, as the dashboard runs its facts (037 R9); the in-order deduction of open months runs afterwards, in month order. A 12-month query now makes 12 `read_period` calls, 24 with a comparison, and the `commission_lines` detail 12.

**The mbe#53 fields** (`customer_payment.commission`) are card surcharges and are not read.

## R12. Invoices, coverage, status and VAT

**Decision**: two new facts.

**invoice_line**: the lines of fiscal documents of type 0 (invoice), 200 (payment receipt) and 100 (credit note, already spec 037's credit-note fact, reused). They are priced with spec 037's line rule and dated by `issued`. Dimensions:
- issuer (taxpayer);
- facility (issuing);
- customer;
- type;
- status:
  - **stamped**: not cancelled and `stamped` set;
  - **cancelled**: cancelled and `stamped` set, carrying `cancellation_date`;
  - **pending**: not cancelled and not stamped, but issued.

Drafts cancelled without ever being stamped are excluded. **Output VAT** = invoice VAT − credit note VAT, stamped and live.

The credit notes are rows of `invoice_line` itself (document type `credit_note`), priced with the same line rule as spec 037's credit-note fact, so output VAT is one fact and groups by issuer, which spec 037's credit-note fact does not have. Without a `document_status` grouping or filter, the invoice measures count stamped documents only (`invoiced` "stamped by default"); output VAT counts stamped live documents whatever the status grouping. A payment receipt is one row carrying its `payment_amount`, never VAT.

**invoice_coverage**: the sale lines of spec 037 (by sale date), each joined to the quantity invoiced on it: Σ `fiscal_document_detail.quantity` over live stamped invoices, by `order_detail`, capped at the line quantity.
- **Invoiced cents** = the line's sale cents × invoiced ÷ quantity, rounded.
- **Not invoiced** = sale cents − invoiced cents, so the two add up to sold exactly.
- **Lag** = days from the sale to the first invoice issued on the line, as an average weighted by invoiced cents.
- **Standalone invoiced**: invoice lines without an `order_detail`, a separate measure on the invoice fact.

Measured for 2025: 57% coverage by amount; $130.6M of $287M invoiced is standalone.

**Rationale**: the line-level link exists and no line is over-invoiced. Capping protects against future data.

## R13. Purchases and price history

**Decision**: a purchase_line fact over the lines of completed, not-cancelled purchase orders.
- **Date:** the order's `modification_time`, which spec 033 uses as its confirmation moment.
- **Amount:** quantity × price × (1 − discount), before tax. The "tax included" flag is ignored, per spec 031 FR-034.
- **Supplier:** the order's.
- **Other dimensions:** product, brand, label, warehouse and facility.
- **Received:** quantity from confirmed receipt lines linked to the PO line.
- **Legacy orders count**, from 2012 on.

**Price history**: the `purchases` detail kind lists lines with the previous purchase price of the same product, via `LAG` over (product, date, line id) across the product's whole history (any supplier, facility, or date before the period). It shows the change and the change %, and flags it when |change %| > `price_change_pct` (default 10), a field of the detail request. Lines with price 0 (after discount) are excluded from the history: they are listed, being purchases the measures count, but have no previous price and are never one.

**Purchase against sale**:
- **Average purchase price** = purchase amount ÷ purchase quantity. Visible to any purchases reader (FR-145), who already sees every line's price.
- **Average sale price** = spec 037's gross sales without VAT ÷ units sold.
- **Spread** is their difference and %, by product and period, cost privilege only (FR-146).

## R14. Saved views

**Decision**: tables `analytics_view` and `analytics_view_share` (data-model).
- **Stored definition:** the JSON of a query, dashboard, detail or customer 360 request, with `start`/`end` replaced by a `period`: a `RelativePeriod` key plus `days` for "last N days", or fixed dates.
- **Validation:** every save and every open validates the definition against the catalog. A view that no longer validates is refused with `analytics.view_invalid`, with params `key` and `reason`. Validating means parsing the definition, with the resolved dates, into its kind's request model; `key` is the dotted path of the first field that fails (`measures.1`) and `reason` the parser's message. A save then makes the kind's privilege and scope checks as the owner; a run is the kind's route as the opener, so a measure the opener may not see is refused as in a query (`auth.forbidden`), not as an invalid view.
- **Run:** `POST /analytics/views/{id}/run` resolves the period with `local_today()` and dispatches to the same engine call as the matching route, as the opener.
- **Changes and visibility:** only the owner may change, delete or schedule a view. Shared users see it in their list.
- **User deletion** removes their views and schedules, extending spec 037's `delete_for_user`.

**Relative periods** (`app/services/analytics/periods.py`): `today`, `yesterday`, `this_week`, `this_month`, `this_quarter` and `this_year` (to date), `last_week`, `last_month`, `last_quarter`, `last_year`, `last_n_days` (the N days ending today) and `fixed`. Weeks are ISO weeks, in business time. A comparison stays a comparison kind.

## R15. Scheduled reports: a CLI job, claimed in the database

**Decision**: tables `analytics_schedule`, `analytics_schedule_recipient` and `analytics_send`, and a job `app/jobs/send_scheduled_reports.py`.

Each run works like this:
1. Exit unless `settings.scheduled_reports_enabled`. This is false by default and turned on for one instance, because dev and test share mbe_dev.
2. Select the schedules with `enabled = 1 AND next_run_time <= now`.
3. For each, claim it with `UPDATE … SET next_run_time = <next occurrence after now> WHERE id = ? AND next_run_time = <read value>`, and continue only when one row changed. A missed run therefore sends only once, for its latest occurrence.
4. Insert an `analytics_send` row (running), then:
   - rebuild the owner (R16);
   - resolve the period;
   - build the export with spec 037's `exports.check`, `build_table` and `file_bytes` or `render_pdf`;
   - skip if it exceeds `settings.report_attachment_max_bytes` (10 MB);
   - send through `mail.get_mailer()` to the eligible recipients.
5. Finish the row as sent, failed (with its code) or skipped.

The cron entry (every 5 minutes, as the instance's user) is documented in the quickstart. Installing it on a server is a deployment step that needs confirmation.

**Rationale**: no scheduler exists and the existing jobs are CLI modules (`app/jobs/*`), so this follows their pattern. The database claim makes the send exactly-once across processes and instances, which SC-008 tests with two concurrent runs on MariaDB semantics, emulated on SQLite by sequential claims of the same read value.

**Alternatives considered**: an in-process loop in the app's lifespan. It dies with each restart and runs once per worker and instance. APScheduler would be a new dependency for a five-minute poll.

**Mail**: mail isn't configured on dev or test, so `get_mailer()` returns None there and runs are recorded as skipped with `email.not_configured` (spec 024).

## R16. Privileges at send time

**Decision**: a scheduled run rebuilds `CurrentUser` from the owner's user row:
- `administrator`, `employee_id` and `session_version`;
- the facility from `UserSettings.facility_id`.

It stops (failed, `auth.forbidden`) if the owner's status is not active: the error catalog has no inactive-user code, and a deactivated user can't sign in either. It then calls spec 037's `exports.check` with that user, which applies families, the cost privilege, facility scope and filters exactly as for a request. Background exports in spec 037 reuse the families captured at request time; scheduled sends re-check at each send, as the spec requires.

**Recipients** must be active users who hold every family the view reads, plus the cost privilege when it has a sensitive measure. This is checked with `engine.families` per recipient, at save time (`analytics.recipient_ineligible`, with params `user` and `family`) and again at each send, where an ineligible recipient is dropped and recorded. At most 10 recipients.

## R17. Privileges per family

**Decision**: no new SystemObject. Each family reuses the existing screens' privileges:

| Family | Readers (any one of) |
|---|---|
| Inventory | Warehouse stock report (67), Kardex (32), Store movements summary (99) |
| Inventory cost (value, turnover, days of inventory, spread) | Plus the cost privilege (37, 38, 39), as spec 037 |
| Receivables, customer 360 balance | Accounts receivable (18), Customer debt report (46) |
| Payables | Accounts payable (19) |
| Commissions, all agents | Commissions by salesperson (112); without it, an agent sees their own |
| Fiscal | Fiscal documents (23), Fiscal documents report (48) |
| Purchases and prices | Purchase orders (20), Products by supplier report (75) |
| Detail of a kind | The family plus read on the document kind, as spec 037 (sales orders 7, fiscal documents 23, purchase orders 20, kardex 32 for movements) |

Facility scope:
- inventory: the warehouse's facility;
- receivables: the order's facility;
- payables and purchases: the facility of the PO lines' warehouse. `purchase_order` has neither a facility nor a warehouse column (the header warehouse is the purchase request's); a PO line carries its request line's warehouse, or the request header's when the line had none. For payables, a PO whose lines span facilities takes the lowest facility id, and one with no line warehouse is visible only to callers with no facility scope. The purchase line fact places each line by its own warehouse's facility, so a PO split across facilities counts in each for its own lines; a line with no warehouse is visible only to callers with no facility scope;
- fiscal: the issuing facility;
- commissions: the facility of the agent's user record (`user.employee` = the line's `salesperson`). An agent with no user is visible only to callers with no facility scope.

## R18. Performance

**Decision**: the targets are spec 037's, as relaxed (SC-009). Each new fact is one grouped SQL statement, except commissions, which are built in Python from spec 025's lines. The dashboard's new tiles join the shared fact list of spec 037's dashboard, which runs each distinct fact once and concurrently (037 R9).

The two heaviest reads are both timed in the quickstart, and if either misses its target that is reported before anything is redesigned:
- **Stock as of today for all pairs**, about 1.2 s. The dashboard's stock tiles share one such query.
- **Commission months computed live.**

Stock missed its target on dev: the `stock_value` sparkline cost one Python pass over every pair per bucket, on top of a full-ledger opening (R2's measured cause). R2's incremental moments make a later moment cost only its moved pairs, and its daily checkpoint makes the opening a range read near the checkpoint instead of a sum over the whole ledger. The first read of each day per process still pays one full sum.

Re-timed after that (2026-11-09; dashboard month 5.6 s and year 13.4 s at p50 on dev), the stock tiles were still the critical path, and the dashboard's cost was mostly Python CPU on the one event loop its concurrent facts share (locally, a year's dashboard spent 25 s of CPU in 36 s). The causes, and what each became:
- **Four stock reads per dashboard**: the KPI now and its comparison, the value by facility and its total (also the alerts). Each reread the warehouses, the opening, the costs and every pair. Stock slices that differ only in their dimensions and moments now share one read (`balances.stock_many`, batched by `engine.fact_jobs` for both the query and the dashboard), so there are two: without options and with them.
- **The comparison's buckets**: a dated KPI made the stock fact read every bucket of the comparison period, a second year of movements for the year's dashboard, though the KPI shows only the comparison's end. The KPI now takes its value and comparison from an undated read and its sparkline from a dated one without comparison: the same numbers, one comparison moment.
- **Costs and product descriptions** were read 500 ids at a time, 41 round trips for every pair's product. Above 500 products they are read whole, in one statement, and filtered in Python.
- **R5's last entry before the checkpoint's day** fell back to a sum over the whole ledger, which every closed month's dashboard alerts hit. Only the pairs that moved since the moment now get their last entry read again, through the product index (past 500 such products it still sums the whole ledger).
- **Python per moved pair**: a move now changes its groups once, by the difference of its figures, into groups found once per pair, and zero pairs are skipped.

Locally, through an SSH tunnel to mbe_dev, a year's dashboard went from 36 s (25 s CPU) to 11–12 s (7.9 s CPU), and a month's from 9.9–12 s to 6.5–7.7 s (4 s CPU). What remains is mostly the MySQL driver decoding rows (about 190k for the year, most of them the stock movements, one row per pair per weekly bucket): pure-Python decoding on the event loop.

Re-timed after those (dashboard p95 3.6 s for a month and 10.1 s for a year on dev, against 2 s and 5 s), every tile still ran in one Python process, so one request could not get much faster. The decision: the dashboard takes an optional `families` list, and mbe-ui should request its tile groups in parallel, one dashboard request per family (sales, payments, inventory, receivables, payables, fiscal), drawing each group as it arrives. Each request reads only its family's facts, and their union is the whole dashboard.

**Measured on dev, 2026-11-09** (p95 of 10 runs per group, response cache flushed before each, run on the xolotl box itself; a runaway probe query was still holding one MariaDB core, so these are on the high side):

| Tile group | Month alone | Year alone |
|---|---|---|
| sales (spec 037) | 1.38 s | 4.55 s |
| payments | 0.08 s | 0.65 s |
| inventory | 1.63 s | 3.14 s |
| receivables | 0.95 s | 1.89 s |
| fiscal | 0.19 s | 2.74 s |

Every group meets SC-009 on its own. Fired together at one dev instance, all five take 2.9 s for a month and 10.0 s for a year, because dev runs a single uvicorn process and the groups' Python work queues on its one event loop; they only overlap their database waits. The parallel load pays off where the API runs several worker processes (`uvicorn --workers N`), which is a deployment setting outside this spec.

Other SC-009 timings on dev (30 runs, p95): stock as of 2026-06-30 for every pair by warehouse × product 3.9 s; receivables by customer × bucket × month over 12 month-ends 2.6 s; kardex of the busiest pair (2,724 entries) 0.3 s first page, 0.2 s last page. Commissions over 12 open months by agent × label: 7.7 s, a known miss accepted by the user: spec 025's candidate query costs about 2.75 s per open month (mictlanix/mbe-api#298), and closed months read stored lines.

## R19. Migration 032

**Decision**: `migrations/032_operations_analytics.sql`, with a rollback. It adds:
- the index `lot_serial_tracking_date_idx` on `lot_serial_tracking(date)`;
- the tables `receivable_snapshot`, `receivable_snapshot_month` (one marker per written month-end, so a month with no open orders isn't rewritten), `analytics_view`, `analytics_view_share`, `analytics_schedule`, `analytics_schedule_recipient` and `analytics_send`.

There are no column changes to existing tables. Applying it on mbe_dev needs the user's confirmation, as 031 did.

**On mbe_dev** (T078, T080): before the migration, the EXPLAIN of 2025's movements was `ALL` on `lot_serial_tracking` (674k rows, no date index). Applied with the user's OK on 2026-11-09 (8 statements, about 3 s). After it, October 2025's movements read `range` on `lot_serial_tracking_date_idx` (8,219 rows); a whole year, about 18% of the ledger, still scans, which is the optimizer's choice at that selectivity. The seven tables were created empty.

## R20. Error codes

**Decision**: six new codes in spec 036's catalog, each with a section in this spec's contract:

| Code | Status | Params |
|---|---|---|
| `analytics.before_history` | 422 | history (stock, receivables), requested, earliest |
| `analytics.period_not_month_end` | 422 | measure, start, end |
| `analytics.view_invalid` | 422 | key, reason |
| `analytics.recipient_ineligible` | 422 | user, family |
| `analytics.attachment_too_large` | 422 | size, max_size. Recorded on a send, never returned by a route |
| `analytics.stock_options_invalid` | 422 | option, value, min, max |
