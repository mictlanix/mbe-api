# API contract: Operations analytics (spec 038)

This spec extends spec 037's contract ([`specs/037-business-analytics/contracts/api.md`](../../037-business-analytics/contracts/api.md)). All routes are under `/api/v1/analytics`; every one of them requires authentication, and errors are spec 036 Problems. Money, quantity, ratio, date and `as_of` conventions are spec 037's. **Days** are decimal strings with one decimal.

The pseudo-schemas below are checked against `/openapi.json` by `tests/unit/test_contract_docs.py`.

## Routes

| Method | Path | Gate | Purpose |
|---|---|---|---|
| GET | `/analytics/catalog` | any family | Now also lists this spec's measures and dimensions, each with its `kind` (flow or balance) |
| POST | `/analytics/query` | the family of every measure | Unchanged shape; new measures, dimensions and `stock_options` |
| GET | `/analytics/dashboard` | any family | New tiles and alerts (FR-180) |
| POST | `/analytics/detail` | family + read on the kind | New kinds and flags |
| POST | `/analytics/exports` | as the source | Sources now include `kardex` and `customer` |
| GET | `/analytics/kardex` | inventory | Kardex with value, any range from the history start, paged (FR-104) |
| GET | `/analytics/customers/{customer_id}` | sales or receivables; each section its family | Customer 360 (FR-125) |
| GET | `/analytics/views` | authenticated | The caller's views and those shared with them |
| POST | `/analytics/views` | authenticated; the definition's families | Save a view |
| GET | `/analytics/views/{analytics_view_id}` | owner or shared | One view |
| PATCH | `/analytics/views/{analytics_view_id}` | owner | Rename, change definition, period or sharing |
| DELETE | `/analytics/views/{analytics_view_id}` | owner | Delete, with its schedules |
| POST | `/analytics/views/{analytics_view_id}/run` | owner or shared; the definition's families | Run with the period resolved today, as the caller |
| GET | `/analytics/schedules` | authenticated | The caller's schedules (administrators: all) |
| POST | `/analytics/schedules` | the view's owner | Schedule a view |
| GET | `/analytics/schedules/{analytics_schedule_id}` | owner, administrator | One schedule |
| PATCH | `/analytics/schedules/{analytics_schedule_id}` | owner | Change or enable/disable |
| DELETE | `/analytics/schedules/{analytics_schedule_id}` | owner | Delete (its send history is deleted with it) |
| GET | `/analytics/schedules/{analytics_schedule_id}/sends` | owner, administrator | Send history, newest first, paged |

**Families** (research R17), checked as `require_any_privilege(..., READ)`:
- **Inventory**: 67, 32, 99. Value, turnover, days of inventory and the purchase-sale spread also need the cost privilege (37, 38, 39).
- **Receivables**: 18, 46. **Payables**: 19.
- **Commissions**: 112 for every agent. Without it, a commission agent gets only their own lines (a forced `salesperson` filter: the commission line's earning agent); anyone else gets `auth.forbidden`.
- **Fiscal**: 23, 48. **Purchases**: 20, 75.

Facility scope is spec 037's (FR-061), applied per family as R17 states.

## Catalog

```
AnalyticsMeasure:
  key: MeasureKey
  label: str
  unit: str              # money | quantity | count | ratio | days
  family: str            # NEW
  kind: str              # NEW: flow | balance
  sensitive: bool
  vat: bool
  additive: bool
  dimensions: [DimensionKey, ...]
  definition: str        # NEW: the written definition, with caveats such as "at current cost"
```

The three new fields are also added to spec 037's `AnalyticsMeasure` block, since `test_contract_docs` checks both documents against the same component.

`MeasureKey` and `DimensionKey` gain the keys listed in [data-model.md](../data-model.md#catalog-additions). Balance measures:
- The totals row is the value at the period's end.
- Shares are taken within that moment.
- With a date dimension, each bucket holds the value at that bucket's end.

## Query

```
AnalyticsQueryRequest:
  measures: [MeasureKey, ...]
  dimensions: [DimensionKey, ...]
  grain: Grain
  start: date
  end: date
  facility: int | None
  filters: [AnalyticsFilter, ...]
  comparison: Comparison | None
  vat: VatTreatment
  sort: str | None
  top: int | None
  stock_options: StockOptions | None

StockOptions:
  velocity_days: int     # 30-365, default 90
  dead_days: int         # 90 | 180 | 365, default 180
  slow_cover_days: int   # 30-730, default 180
  lead_days: int         # 0-120, default settings.restock_lead_days (7)
  cover_days: int        # 1-365, default settings.restock_cover_days (30)
```

Rules beyond spec 037's:
- **Stock dates:** a stock or movement measure whose period starts before `stock_history_start` (2025-02-07) gives `analytics.before_history` (history `stock`).
- **Receivable history:** a receivable balance at a past date reads the stored month-end. A date that is neither a month-end nor today rounds down to the month-end before it, and the response's `definition` shows the moment used: for a query of receivable balances only without a date dimension, `end` is the month-end read and `start` no later than it (so a comparison is the period before that month-end). With a date dimension each bucket's key is its period; a query that also sums a flow keeps its period, over which the flow is summed. A month-end before 2025-01-31 gives `analytics.before_history` (history `receivables`).
- **DSO and CEI:** they need `start` on a 1st and `end` on a month-end or today; otherwise `analytics.period_not_month_end`.
- **Today-only measures:** `reserved`, `available` and the payable measures are null at any moment but now.
- **Commissions:** a line belongs to its month, so a period starting or ending mid-month reads the whole months it touches, and a `day` or `week` grain is raised to `month` (the response's `definition` shows it). Rows carry `commission_status`. An open month in a query that also includes earlier open months shows only what closing them in order would add (research R11); the `commission_lines` detail lists the same remainders. For a commission agent without 112 the response's `definition` shows the forced `salesperson` filter, which replaces any the caller named. Being a commission agent opens the analytics routes, as a family privilege does.

## Dashboard

`AnalyticsDashboardResponse` (spec 037) gains these, omitted per family:
- **KPIs:** `stock_value` (cost), `receivable_overdue`, `dso`, `uninvoiced_sales`.
- **Series:**
  - `stock_value_by_facility` (bar, cost);
  - `receivable_overdue_trend` (line, 12 month-ends and now);
  - `top_debtors` (bar, top 10 with others);
  - `collected_vs_due` (bar: due in period, due collected, collected on credit);
  - `invoice_coverage` (bar by facility).
- **Alerts:**
  - `stock_outs` (detail `stock`, flag `stock_out`);
  - `below_reorder` (`stock`, `below_reorder`);
  - `dead_stock` (`stock`, `dead`, cost: amount is the value);
  - `uncosted_stock` (`stock`, `uncosted`);
  - `payables_overdue` (once the cutoff is set): the open POs past due now and their payable. Its `detail` is null, since there is no payables detail kind.

The `overdue_credit_orders` alert of spec 037 is unchanged.

Inventory tiles appear for an inventory reader when the period starts within the stock history (2025-02-07 on). The `stock_value` KPI's sparkline is the value at each bucket's end. The inventory alerts count the pairs as of the period's end at the default `StockOptions` (dead after 180 days), from one stock query shared with `stock_value_by_facility`'s totals (R18); each `count` is its detail's `total`. Only `dead_stock` has an `amount`, the dead pairs' value; the others' is null.

## Detail

`DetailKind` gains these kinds:

| Kind | Rows | Flags | Extra read privilege |
|---|---|---|---|
| `stock` | product × warehouse pairs at the moment: on hand, reserved, available, velocity, cover, reorder point, suggested quantity, last movement, cost and value (cost) | `stock_out`, `below_reorder`, `dead`, `slow`, `uncosted` | — |
| `movements` | ledger entries: date, type, document, warehouse, product, quantity | — | 32 |
| `receivables` | open credit orders at the moment: balance, due date, days past due, bucket | `overdue` | 7 |
| `invoices` | fiscal documents: type, status, issuer, series-folio, UUID, issued, stamped, cancelled, subtotal, VAT, total | — | 23 |
| `uninvoiced_lines` | sale lines with their not-invoiced quantity and amount | — | 7 |
| `purchases` | PO lines: date, supplier, product, quantity, unit price, previous price, change, change % | `price_change` | 20 |
| `commission_lines` | spec 025 lines: order, line, product, label, participation, label rate, agent share, base, amount, status | — | — |

`AnalyticsDetailRequest` gains `price_change_pct`, the threshold of the `price_change` flag (R13). The field is also added to spec 037's block.

```
AnalyticsDetailRequest:
  kind: DetailKind
  start: date
  end: date
  facility: int | None
  filters: [AnalyticsFilter, ...]
  flag: DetailFlag | None
  vat: VatTreatment
  sort: str | None
  skip: int
  limit: int
  price_change_pct: int | None   # NEW: whole percent, 0-1000, default 10; purchases with flag price_change only
```

```
AnalyticsDetailItem:
  kind: DetailKind
  id: int
  document_id: int | None
  folio: str | None
  date: datetime | None
  facility: int | None
  customer: int | None
  customer_name: str | None
  salesperson: int | None
  product: int | None
  product_code: str | None
  product_name: str | None
  quantity: Decimal | None
  amount: Decimal | None
  cost: Decimal | None
  margin: Decimal | None
  payment_method: int | None
  warehouse: int | None
  figures: AnalyticsDetailFigures | None

AnalyticsDetailFigures:
  values: dict
```

`figures.values` carries the kind's own columns as decimal strings or ISO dates: on hand, cover, bucket, status, rates and the like. The contract lists them per kind, as in the table above, so the shared item shape stays one schema. Sums over every page are in `sums`, as in spec 037.

**Exports** of these kinds hold what the screen shows: spec 037's detail columns, then `warehouse` (stock, movements and purchases), then each `figures.values` column in the order listed below, typed in XLSX (numbers, dates). A figure that repeats an item field (a commission line's `amount`) is not repeated. A stock pair's `value` is a column only with the cost privilege. The TOTAL line holds each column that `sums` names.

**`stock`** lists the pairs as of the end of the period (now when it reaches today), at the default `StockOptions`, which is what the dashboard's alerts count:
- `id` and `product` are the product, `warehouse` the warehouse, `facility` the warehouse's; `quantity` is on hand; `cost` the unit cost (cost privilege, null when uncosted); `document_id`, `date` and `amount` are null.
- `figures.values`: `on_hand`, `reserved` and `available` (null at a past moment), `in_transit`, `velocity`, `cover`, `reorder_point`, `suggested`, `last_movement`, and `value` (cost privilege only, absent otherwise).
- Without a flag, every pair with stock, sales or reservations. Each flag selects R5's pairs.
- `sums`: `quantity` (on hand: `on_hand`, `dead_stock_quantity`, `slow_stock_quantity` or `uncosted_quantity` with the matching flag), `suggested` (`suggested_quantity`), and `value` (cost privilege only: `stock_value`, `dead_stock_value`, …). `total` is the pair count (`stock_out_pairs`, `below_reorder_pairs`).
- `sort`: `date` is the last movement, `amount` the on hand, `document` the product code.

**`movements`** lists the ledger entries of the period:
- `id` is the entry, `document_id` and `folio` its document when it resolves (as spec 030's kardex), `quantity` signed, `amount` null.
- `figures.values`: `movement_type`, `source` (the type's name) and `reference`.
- `sums`: `quantity` (net), `units_in` and `units_out`, equal to the measures. `sort`: `amount` is the quantity, `document` the reference.

**`receivables`** lists the open credit orders the receivable fact sums at the end of the period (now when it reaches today, else the stored month-end on or before it), with flag `overdue` those past due then:
- `id` and `document_id` are the order, `folio` its serial, `date` its confirmation, `facility`, `customer` and `salesperson` the order's; `amount` the balance in base currency.
- `figures.values`: `balance` (in the order's currency), `exchange_rate`, `due_date`, `days_past_due`, `bucket` (aging) and `due_bucket` (forecast).
- `sums`: `amount` (`receivable`, or `receivable_overdue` with the flag). `sort`: `date` is the confirmation, `amount` the balance, `document` the order.

**`invoices`** lists the fiscal documents the invoice fact sums (invoices, credit notes and payment receipts), stamped only unless the request filters by `document_status`, as the query counts them:
- `id` and `document_id` are the fiscal document, `folio` its series-folio, `date` its issue, `facility` the issuing facility; `amount` the invoice's or credit note's lines (with the request's VAT treatment), or a receipt's payment amount.
- `figures.values`: `type` (invoice, credit_note, payment_receipt), `status` (stamped, cancelled, pending), `issuer`, `uuid`, `issued`, `stamped`, `cancelled` (the cancellation date, for a cancelled one), `subtotal`, `vat` and `total`.
- `sums`: `invoiced`, `output_vat` and `payment_receipts`, equal to the measures; with a `document_type` filter of `invoice`, `total` is `invoices`. `sort`: `amount` is the amount, `document` the fiscal document id.

**`uninvoiced_lines`** lists the sale lines of the period not wholly invoiced by live stamped invoices, from the coverage fact (R12):
- `id` is the sale line, `document_id` and `folio` its order; `quantity` and `amount` are what is not invoiced (the amount with the request's VAT treatment).
- `figures.values`: `sold`, `invoiced` (capped at sold), `invoiced_amount`, `first_invoiced` and `lag_days` (days from the sale to the first invoice; null when none).
- `sums`: `amount` (`uninvoiced_sales`) and `quantity`. `sort`: `amount` is the amount, `document` the order.

**`purchases`** lists the lines the purchase fact sums (R13): lines of confirmed, not cancelled purchase orders dated in the period, each placed by its own warehouse's facility:
- `id` is the purchase order line, `document_id` its purchase order (`folio` null: purchase orders have no serial), `date` the order's confirmation (`modification_time`), `facility` and `warehouse` the line's; `quantity` the line's, `amount` quantity × price × (1 − discount), before tax whatever `vat` says.
- `figures.values`: `supplier`, `supplier_name`, `unit_price` (after discount, before tax), `previous_price`, `change`, `change_pct` (a ratio: 0.1429 is 14.29%) and `received` (from confirmed receipts).
- The previous price is the unit price of the product's purchase before it, by (date, line id), from any supplier, facility or date before the period (`LAG` over the product's history), whatever the request's filters and scope. Lines at price 0 after discount are listed, since they are purchases, but are left out of the history: they have no previous price and are never one.
- Flag `price_change`: the lines whose |change| is more than `price_change_pct` percent (default 10) of the previous price.
- `sums`: `amount` (`purchases`), `quantity` (`purchase_quantity`) and `received` (`received_quantity`). `sort`: `amount` is the amount, `document` the purchase order.

**`commission_lines`** lists spec 025's lines of the months the period touches, as `read_period` gives them (stored once a month is closed, live while it is open):
- `id` is the sale line, `document_id` and `folio` its order, `date` the paid date (a refund's date for a refund line), `facility` the agent's user's, `salesperson` the earning agent, `quantity` the line's (or the refund's), `amount` the commission.
- `figures.values`: `month`, `status` (provisional, closed), `movement` (sale, refund), `scheme`, `sales_order`, `sales_order_detail`, `customer_refund_detail`, `order_date`, `paid_date`, `product`, `label`, `label_name`, `label_rate`, `participation`, `participation_name`, `participation_rate` (the agent's share), `rate`, `price`, `base` (price × quantity, negative for a refund), `share` (paid share, partial-payment scheme), `full_commission` and `amount`.
- `sums`: `amount` (`commission`) and `base` (`commission_base`); `total` is the line count (`commission_lines`). `sort`: `date` is the paid date, `amount` the commission, `document` the order.
- An agent without 112 gets their own lines only, whatever `salesperson` filter they send.

## Kardex

`GET /analytics/kardex?product=&warehouse=&start=&end=&skip=0&limit=100` (limit at most 500).

The same parameters, as `AnalyticsKardexRequest`, are the `kardex` source of `POST /analytics/exports` (spec 037's `AnalyticsExportRequest` gains the field). An export takes every entry of the range; its `skip` and `limit` are ignored.

```
AnalyticsKardexRequest:
  product: int
  warehouse: int
  start: date
  end: date
  skip: int
  limit: int             # 1-500, default 100

AnalyticsKardexResponse:
  as_of: datetime
  product: int
  warehouse: int
  start: date
  end: date
  opening: Decimal
  unit_cost: Decimal | None
  rows: [AnalyticsKardexRow, ...]
  total: int
  closing: Decimal

AnalyticsKardexRow:
  date: datetime
  movement_type: int
  source: str
  reference: int
  document_id: int | None
  quantity: Decimal
  balance: Decimal
  value: Decimal | None
```

- **Balances:** `opening` is the balance before `start`; each page's first row continues from the rows skipped.
- **Cost:** `unit_cost` and `value` are present only with the cost privilege.
- **Range:** `start` before 2025-02-07 gives `analytics.before_history`. There is no range cap. An `end` before `start` gives spec 030's `kardex.range_reversed`.
- **Scope:** a warehouse of another facility is refused with `facility.other_facility` unless the caller sees every facility.
- **Uncosted:** with the cost privilege, `unit_cost` and `value` are null when the product's cost is 0 or at least the placeholder (R4).

## Customer 360

`GET /analytics/customers/{customer_id}?start=&end=&facility=`

```
AnalyticsCustomerRequest:
  customer: int
  start: date
  end: date
  facility: int | None

AnalyticsCustomerResponse:
  as_of: datetime
  customer: int
  customer_name: str
  start: date
  end: date
  sales: AnalyticsQueryResponse | None
  receivable: AnalyticsQueryResponse | None
  top_products: AnalyticsQueryResponse | None
  last_purchase: datetime | None
  orders_per_month: Decimal | None
  credit_limit: Decimal | None
  credit_utilization: Decimal | None
```

- **Sections:** each section is the same response the query route gives for its definition, which it echoes with the customer's filter forced. `sales` holds `net_sales` and `refunds`, `collected` (spec 037's payments; payments family) and `gross_margin` and `margin_pct` (cost), with the previous period. `receivable` holds `receivable`, `receivable_current` and `receivable_overdue` now (start = end = today), by `aging_bucket`. `top_products` is `net_sales` by product, the top 10 by `-net_sales`.
- **Missing families:** a section is null when the caller lacks its family. Without the payments family `collected` is absent from `sales`; without cost the margin measures are. The route needs the sales or the receivables family (`auth.forbidden` otherwise).
- **Purchase frequency:** over the 12 months up to `end` (from the day after the same date a year earlier): `orders_per_month` is the `orders` measure ÷ 12, two decimals, and `last_purchase` the latest confirmation of an order it counts, null when none. Both need the sales family.
- **Credit:** `credit_limit` and `credit_utilization` are spec 033's summary for the customer (utilization reads what the customer owes across every facility; null without a limit). Receivables family.
- **Unknown customer:** `resource.not_found`.
- **Export:** `AnalyticsExportRequest.customer` takes `AnalyticsCustomerRequest`. The file has one line per section row and measure: `section`, `row` (the keys' labels joined by " / ", `(others)` or `TOTAL`), `measure`, `value`, `comparison`; then `customer` lines for `last_purchase`, `orders_per_month`, `credit_limit` and `credit_utilization`, as far as the families allow.
- **Facility scope:** a customer whose orders are all outside the caller's facility scope still opens, but every figure is scoped. Asking for another facility explicitly is refused as in spec 037.

## Saved views

```
AnalyticsViewCreate:
  name: str
  kind: ViewKind         # query | dashboard | detail | customer
  definition: dict
  period: AnalyticsPeriod
  shared_with: [str, ...]

AnalyticsViewUpdate:
  name: str | None
  definition: dict | None
  period: AnalyticsPeriod | None
  shared_with: [str, ...] | None

AnalyticsPeriod:
  key: RelativePeriod
  days: int | None
  start: date | None
  end: date | None

AnalyticsViewResponse:
  analytics_view_id: int
  name: str
  kind: ViewKind
  owner: str
  definition: dict
  period: AnalyticsPeriod
  shared_with: [str, ...]
  creation_time: datetime
  modification_time: datetime
```

- **Relative periods:** `RelativePeriod` is one of `today`, `yesterday`, `this_week`, `this_month`, `this_quarter`, `this_year`, `last_week`, `last_month`, `last_quarter`, `last_year`, `last_n_days` (needs `days`, 1–3,650; the N days ending today) or `fixed` (needs `start` and `end`, start not after end). `days` goes only with `last_n_days`, and `start`/`end` only with `fixed`; otherwise 422.
- **Run:** `POST /views/{id}/run` returns the same response as the view's route (query, dashboard, detail or customer). It takes the optional `If-None-Match` and caching of that route.
- **Sharing:** `shared_with` holds user ids, which must be active users (an unknown or inactive one is `resource.not_found` for `user`). The owner is left out of the list. Only the owner changes or deletes a view; a shared user is refused with `auth.forbidden` (empty `required`), anyone else gets `resource.not_found`. `shared_with` is shown only to the owner; shared users see an empty list.

## Schedules

```
AnalyticsScheduleCreate:
  analytics_view: int
  cadence: Cadence       # daily | weekly | monthly
  weekdays: [int, ...]   # 1 (Monday) to 7; weekly only
  month_day: int | None  # 1-31; monthly only
  time_of_day: time
  format: ExportFormat
  subject: str
  recipients: [str, ...]
  enabled: bool

AnalyticsScheduleUpdate:
  cadence: Cadence | None
  weekdays: [int, ...] | None
  month_day: int | None
  time_of_day: time | None
  format: ExportFormat | None
  subject: str | None
  recipients: [str, ...] | None
  enabled: bool | None

AnalyticsScheduleResponse:
  analytics_schedule_id: int
  analytics_view: int
  cadence: Cadence
  weekdays: [int, ...]
  month_day: int | None
  time_of_day: time
  format: ExportFormat
  subject: str
  recipients: [str, ...]
  enabled: bool
  next_run_time: datetime

AnalyticsSendResponse:
  analytics_send_id: int
  occurrence: datetime
  started_at: datetime
  finished_at: datetime | None
  outcome: SendOutcome   # running | sent | failed | skipped
  code: str | None
  sent_to: [str, ...]
  dropped: [str, ...]
  size: int | None
  row_count: int | None
```

- **Recipients:** user ids, at most 10, each an active user who holds the view's families (and the cost privilege when it has a sensitive measure). Otherwise `analytics.recipient_ineligible`.
- **The mail:** the email goes to the address on each user record, with the report attached and a one-line body naming the view, the period and `as_of`.
- **The run job:** `uv run python -m app.jobs.send_scheduled_reports`, every 5 minutes, on the one instance where `SCHEDULED_REPORTS_ENABLED=true`.

## Error codes (added to spec 036's catalog)

| Code | Status | Params | Situation |
|---|---|---|---|
| `analytics.before_history` | 422 | history: str, requested: date, earliest: date | Stock before 2025-02-07, or a receivable month-end before 2025-01-31 |
| `analytics.period_not_month_end` | 422 | measure: str, start: date, end: date | DSO or CEI over a period that doesn't start on a 1st and end on a month-end or today |
| `analytics.view_invalid` | 422 | key: str, reason: str | A saved view no longer validates against the catalog |
| `analytics.recipient_ineligible` | 422 | user: str, family: str | A recipient is inactive or lacks a family the view reads |
| `analytics.attachment_too_large` | 422 | size: int, max_size: int | Recorded on a send whose attachment exceeds 10 MB |
| `analytics.stock_options_invalid` | 422 | option: str, value: int, min: int, max: int | A stock option out of range |

The catalog goes from 251 to 257 codes.

## Changed shapes elsewhere

None. Spec 033's `order_balances` and `open_credit_orders` gain a keyword (`as_of`) used internally; their routes and responses don't change.
