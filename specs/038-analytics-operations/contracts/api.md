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
| GET | `/analytics/customers/{customer_id}` | receivables (balance parts), sales (sales parts) | Customer 360 (FR-125) |
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
  price_change_pct: int | None   # whole percent, 0-1000; purchases flag a change above it (default 10)

StockOptions:
  velocity_days: int     # 30-365, default 90
  dead_days: int         # 90 | 180 | 365, default 180
  slow_cover_days: int   # 30-730, default 180
  lead_days: int         # 0-120, default settings.restock_lead_days (7)
  cover_days: int        # 1-365, default settings.restock_cover_days (30)
```

Rules beyond spec 037's:
- **Stock dates:** a stock or movement measure whose period starts before `stock_history_start` (2025-02-07) gives `analytics.before_history` (history `stock`).
- **Receivable history:** a receivable balance at a past date reads the stored month-end. A date that is neither a month-end nor today rounds down to the month-end before it, and the response's `definition` shows the moment used. A month-end before 2025-01-31 gives `analytics.before_history` (history `receivables`).
- **DSO and CEI:** they need `start` on a 1st and `end` on a month-end or today; otherwise `analytics.period_not_month_end`.
- **Today-only measures:** `reserved`, `available` and the payable measures are null at any moment but now.
- **Commissions:** grouped by `date`, they use month grain. Rows carry `commission_status`.

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
  - `payables_overdue` (once the cutoff is set).

The `overdue_credit_orders` alert of spec 037 is unchanged.

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

## Kardex

`GET /analytics/kardex?product=&warehouse=&start=&end=&skip=0&limit=100` (limit at most 500).

```
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
- **Range:** `start` before 2025-02-07 gives `analytics.before_history`. There is no range cap.

## Customer 360

`GET /analytics/customers/{customer_id}?start=&end=`

```
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

- **Sections:** each section is the same response the query route gives for its definition. `sales` holds net sales, refunds, payments (spec 037's `collected`) and margin (cost) with comparison; `receivable` holds the balance, overdue and buckets now.
- **Missing families:** a section is null when the caller lacks its family.
- **Purchase frequency:** `orders_per_month` is taken over the 12 months before `end`.
- **Facility scope:** a customer whose orders are all outside the caller's facility scope still opens, but every figure is scoped. Asking for another facility explicitly is refused as in spec 037.

## Saved views

```
AnalyticsViewCreate:
  name: str
  kind: str              # query | dashboard | detail | customer
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
  kind: str
  owner: str
  definition: dict
  period: AnalyticsPeriod
  shared_with: [str, ...]
  creation_time: datetime
  modification_time: datetime
```

- **Relative periods:** `RelativePeriod` is one of `today`, `yesterday`, `this_week`, `this_month`, `this_quarter`, `this_year`, `last_week`, `last_month`, `last_quarter`, `last_year`, `last_n_days` (needs `days`, 1–3,650) or `fixed` (needs `start` and `end`).
- **Run:** `POST /views/{id}/run` returns the same response as the view's route (query, dashboard, detail or customer). It takes the optional `If-None-Match` and caching of that route.
- **Sharing:** `shared_with` holds user ids, which must be active users. `shared_with` is shown only to the owner; shared users see an empty list.

## Schedules

```
AnalyticsScheduleCreate:
  analytics_view: int
  cadence: str           # daily | weekly | monthly
  weekdays: [int, ...]   # 1 (Monday) to 7; weekly only
  month_day: int | None  # 1-31; monthly only
  time_of_day: time
  format: ExportFormat
  subject: str
  recipients: [str, ...]
  enabled: bool

AnalyticsScheduleUpdate:
  cadence: str | None
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
  cadence: str
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
  outcome: str           # running | sent | failed | skipped
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
