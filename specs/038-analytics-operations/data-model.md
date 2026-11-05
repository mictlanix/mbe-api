# Data model: Operations analytics (spec 038)

This spec adds tables for receivable history, saved views and schedules, new facts over existing tables, catalog entries, and request and response shapes. It changes no column of an existing table. Migration 032 adds one index (`lot_serial_tracking(date)`) and the seven tables below (research R19).

## New tables (migration 032)

### `receivable_snapshot`

One row per open credit order per month-end (R8).

| Column | Type | Notes |
|---|---|---|
| `month_end` | date | PK part. The last day of the month; the balance is as of 23:59:59 that day |
| `sales_order` | int | PK part. FK to `sales_order` |
| `customer` | int | FK to `customer` |
| `facility` | int | The order's facility |
| `salesperson` | int, null | The order's |
| `sales_agent` | int, null | The customer's agent at write time |
| `due_date` | datetime, null | The order's |
| `currency` | int | |
| `exchange_rate` | decimal(18,4) | |
| `total` | decimal(18,2) | Order total (spec 033) |
| `balance` | decimal(18,2) | Spec 033's balance as of the month-end (R7), in the order's currency |
| `credit_limit` | decimal(18,2), null | The customer's limit when written |
| `limit_historical` | tinyint(1) | 1 when written at that month-end; 0 for backfilled months |
| `written_at` | datetime | |

Indexes: `(month_end, customer)` and `(month_end, facility)`. A month-end with any row counts as written. A month with no open orders gets one marker row in `receivable_snapshot_month (month_end PK, written_at, orders)`, so the job doesn't redo it.

### `analytics_view`

| Column | Type | Notes |
|---|---|---|
| `analytics_view_id` | int | PK |
| `user` | varchar(20) | Owner, FK to `user` |
| `name` | varchar(100) | Unique per owner |
| `kind` | tinyint | 0 query, 1 dashboard, 2 detail, 3 customer 360 |
| `definition` | text | JSON of the request without `start`/`end` |
| `period` | text | JSON `{key, days?, start?, end?}` (R14) |
| `creation_time` | datetime | |
| `modification_time` | datetime | |

### `analytics_view_share`

PK `(analytics_view, user)`. Both columns are FKs; rows are deleted with the view.

### `analytics_schedule`

| Column | Type | Notes |
|---|---|---|
| `analytics_schedule_id` | int | PK |
| `analytics_view` | int | FK. The owner is the view's |
| `cadence` | tinyint | 0 daily, 1 weekly, 2 monthly |
| `weekdays` | tinyint | Bitmask, Monday = 1 … Sunday = 64; weekly only |
| `month_day` | tinyint, null | 1–31, monthly only; past the month's end it means the last day |
| `time_of_day` | time | Business time |
| `format` | tinyint | spec 037's `ExportFormat` (xlsx, pdf, csv) |
| `subject` | varchar(150) | |
| `enabled` | tinyint(1) | |
| `next_run_time` | datetime | The next occurrence, in business time. The claim compares it (R15) |
| `creation_time`, `modification_time` | datetime | |

Index: `(enabled, next_run_time)`.

### `analytics_schedule_recipient`

PK `(analytics_schedule, user)`. At most 10 per schedule.

### `analytics_send`

| Column | Type | Notes |
|---|---|---|
| `analytics_send_id` | int | PK |
| `analytics_schedule` | int | FK |
| `occurrence` | datetime | The scheduled moment this send is for |
| `started_at` | datetime | |
| `finished_at` | datetime, null | Null while running; a row left running after 10 minutes reads as failed (`server.error`) |
| `outcome` | tinyint | 0 running, 1 sent (the mail server accepted the message), 2 failed, 3 skipped. A `MailError` from spec 024's mailer is failed with `email.send_failed` |
| `code` | varchar(64), null | Spec 036 code of a failure or skip |
| `recipients` | text | JSON list of user ids actually sent to, plus those dropped and why |
| `format` | tinyint | |
| `size` | int, null | Attachment bytes |
| `row_count` | int, null | |

Index: `(analytics_schedule, occurrence)`.

## New facts

Each fact is one grouped SQL statement (spec 037 R3), except commissions (R11).

| Fact | Kind | Rows | Date | Facility scope |
|---|---|---|---|---|
| `stock` | balance | `lot_serial_tracking`: Σ quantity before the moment; and Σ in the period by bucket (R2) | entry `date` | warehouse's facility |
| `movement` | flow | `lot_serial_tracking` in the period: units in (quantity > 0), units out (−quantity < 0), entries, documents (distinct source, reference) | entry `date` | warehouse's facility |
| `stock_today` | balance | today's reserved and available (`reserved_by_warehouse`) | now | warehouse's facility |
| `receivable` | balance | `receivable_snapshot` for month-ends; `open_credit_orders()` for now | month-end or now | order's facility |
| `credit_flow` | flow | applications to credit orders by application date; due-in-period from the start balance (R9) | application date, due date | order's facility |
| `payable` | balance | spec 033's supplier balances, now | now | PO lines' warehouse's facility (R17) |
| `commission` | flow | spec 025 `read_period` lines, per month (Python) | the line's month | agent's facility |
| `invoice_line` | flow | `fiscal_document_detail` of invoices (0) and credit notes (100), priced like spec 037, and one row per payment receipt (200) with its `payment_amount` (R12) | `issued` | issuing facility |
| `invoice_coverage` | flow | spec 037's `sale_lines` joined to invoiced quantity per sale line (R12) | sale date | order's facility |
| `purchase_line` | flow | `purchase_order_detail` of completed, not cancelled POs; received quantity from confirmed receipts | PO `modification_time` | the line's warehouse's facility (R17) |

## Catalog additions

Units follow spec 037: money, quantity, count, ratio, days. "Cost" marks a sensitive measure (the cost privilege).

### Inventory

| Key | Unit | Kind | Cost | Definition |
|---|---|---|---|---|
| `on_hand` | quantity | balance | | Σ ledger quantity up to the moment (R2) |
| `reserved` | quantity | balance | | Today only (R2); null at a past moment |
| `available` | quantity | balance | | on hand − reserved, today only |
| `in_transit` | quantity | balance | | Stock in transit warehouses, against the destination facility |
| `stock_value` | money | balance | ✓ | on hand × current cost, costed pairs (R4) |
| `stock_cost_coverage` | ratio | balance | ✓ | costed pairs with stock ÷ pairs with stock |
| `uncosted_quantity` | quantity | balance | | On hand of pairs with stock whose cost is ≤ 0 or ≥ the placeholder (FR-105) |
| `units_in`, `units_out` | quantity | flow | | Σ positive and −Σ negative ledger quantities |
| `movement_documents` | count | flow | | Distinct (source, reference) |
| `sales_velocity` | quantity | balance | | units out by sale over `velocity_days` ÷ `velocity_days` (R5) |
| `days_of_cover` | days | balance | | available ÷ velocity |
| `stock_out_pairs` | count | balance | | Pairs with velocity > 0 and available ≤ 0 |
| `below_reorder_pairs`, `suggested_quantity` | count, quantity | balance | | R5 |
| `dead_stock_quantity`, `dead_stock_value` | quantity, money | balance | value ✓ | No movement in `dead_days` |
| `slow_stock_quantity`, `slow_stock_value` | quantity, money | balance | value ✓ | Cover > `slow_cover_days` |
| `turnover`, `days_of_inventory` | ratio, days | flow | ✓ | R4 |

### Receivables and payables

| Key | Unit | Kind | Definition |
|---|---|---|---|
| `receivable`, `receivable_current`, `receivable_overdue` | money | balance | R9 |
| `open_orders` | count | balance | Open credit orders |
| `credit_sales` | money | flow | Gross sales with VAT of credit-terms orders |
| `collected_on_credit` | money | flow | Applications to credit orders dated in the period |
| `due_in_period`, `due_collected` | money | flow | R9 |
| `dso` | days | ratio of parts | R9; needs month-end bounds |
| `cei` | ratio | ratio of parts | R9; needs month-end bounds |
| `credit_limit`, `credit_utilization` | money, ratio | balance | R9 |
| `payable`, `payable_current`, `payable_overdue`, `open_purchase_orders` | money, count | balance | R10, now only |

### Commissions

| Key | Unit | Definition |
|---|---|---|
| `commission` | money | Σ spec 025 line amounts |
| `commission_base` | money | Σ line price (after discount, with VAT) × quantity |
| `commission_lines` | count | |

### Fiscal

| Key | Unit | Definition |
|---|---|---|
| `invoiced` | money | Invoice lines (type 0), by status filter (default stamped) |
| `invoices` | count | Distinct invoices |
| `standalone_invoiced` | money | Invoice lines with no sale line |
| `payment_receipts` | money | Type 200 documents' `payment_amount` |
| `output_vat` | money | Invoice VAT − credit note VAT (stamped, live), both from `invoice_line`, so it groups by issuer |
| `invoiced_sales`, `uninvoiced_sales` | money | Coverage fact (R12) |
| `invoice_coverage` | ratio | invoiced_sales ÷ (invoiced + uninvoiced) |
| `invoicing_lag` | days | Weighted average days, sale to first invoice |

### Purchases

| Key | Unit | Cost | Definition |
|---|---|---|---|
| `purchases` | money | | Σ PO line amount before tax |
| `purchase_quantity`, `received_quantity` | quantity | | |
| `purchase_orders` | count | | |
| `average_purchase_price` | money | | purchases ÷ purchase quantity |
| `average_sale_price` | money | | gross sales without VAT ÷ units sold (spec 037) |
| `purchase_sale_spread` | money | ✓ | Sale − purchase average price |
| `purchase_sale_spread_pct` | ratio | ✓ | Spread ÷ average sale price |

### New dimensions

| Key | Applies to | Values |
|---|---|---|
| `warehouse` | stock, stock_today, movement, purchase_line | warehouse id |
| `movement_type` | movement | `TransactionType` |
| `supplier` | stock, stock_today, movement (the product's); purchase_line, payable (the PO's) | supplier id |
| `aging_bucket` | receivable, payable | current, 1-30, 31-60, 61-90, 90+ |
| `due_bucket` | receivable, payable | overdue, 1-7, 8-30, 31-60, 60+ |
| `sales_agent` | receivable, credit_flow | employee id (the customer's agent) |
| `payment_terms` | receivable, credit_flow (spec 037's dimension, now also on these facts) | the order's terms |
| `commission_participation`, `commission_status` | commission | participation id; provisional, closed |
| `issuer` | invoice_line | taxpayer |
| `document_status` | invoice_line | stamped, cancelled, pending |
| `document_type` | invoice_line | invoice, credit_note, payment_receipt |

Spec 037's dimensions apply where the fact has them: date, facility, product, brand, model, label, customer, salesperson.

## Request shapes

- **`AnalyticsQueryRequest`** (spec 037) gains `stock_options: StockOptions | None` and `price_change_pct: int | None`, a whole percent (R5, R13). An integer keeps the request one OpenAPI component: a `Decimal` field would split it into input and output schemas, since the response echoes the request. Measures, dimensions and flags extend through the enums.
- **`AnalyticsMeasure`** (spec 037's catalog entry) gains `family`, `kind` and `definition`.
- **`DetailKind`** gains:
  - `stock`, with flags `stock_out`, `below_reorder`, `dead`, `slow` and `uncosted`;
  - `movements`;
  - `receivables` (open orders as of, with flag `overdue`);
  - `invoices`;
  - `uninvoiced_lines`;
  - `purchases`, with flag `price_change`;
  - `commission_lines`.
- **`KardexRequest`** (query parameters): product, warehouse, start, end, skip, limit ≤ 500.
- **`AnalyticsCustomerRequest`**: customer, start, end, facility (the route's parameters; the `customer` export source).
- **`AnalyticsViewCreate`**: name, kind, definition, period, shared_with.
- **`AnalyticsScheduleCreate`**: view, cadence, weekdays, month_day, time_of_day, format, subject, recipients, enabled.

## Validation rules

- **Dates before history:** stock-history dates before `stock_history_start` and receivable month-ends before 2025-01-31 are refused with `analytics.before_history`.
- **DSO and CEI:** their periods must start on a 1st and end on a month-end or today, with the start month in history; otherwise `analytics.period_not_month_end`.
- **Stock options:** out of range gives `analytics.stock_options_invalid`.
- **Views:** names are unique per owner (`resource.already_exists`). A definition must validate (`analytics.view_invalid`).
- **Schedules:**
  - weekly needs at least one weekday, and monthly a day from 1 to 31;
  - recipients must be eligible (`analytics.recipient_ineligible`), at most 10;
  - only the view's owner may schedule it (`auth.forbidden`).

## State transitions

- **Schedule:** enabled ⇄ disabled (owner). Each claim moves `next_run_time` forward.
- **Send:** running → sent | failed | skipped. A row left running after 10 minutes reads as failed.
- **Receivable month:** absent → written. Rebuild replaces it in one transaction.
