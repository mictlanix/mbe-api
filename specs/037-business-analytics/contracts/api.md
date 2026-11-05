# API contract: Business analytics (spec 037)

All routes are under `/api/v1/analytics`. All require authentication. Errors are spec 036 Problems.

Conventions:
- Money values are decimal strings with two decimals, quantities with up to four, ratios with four as fractions (`"0.2530"` = 25.3%).
- Dates are ISO `YYYY-MM-DD`; datetimes are naive business time (`LocalDateTime`).
- Every response carries `as_of`, the moment its data was read.

The pseudo-schemas below are checked against `/openapi.json` by `tests/unit/test_contract_docs.py`.

## Routes

| Method | Path | Gate (any one of) | Purpose |
|---|---|---|---|
| GET | `/analytics/catalog` | sales or payments family | The measures and dimensions this caller may use (FR-001) |
| POST | `/analytics/query` | the family of every requested measure | Rows, totals, comparison, top N (FR-010–FR-015) |
| GET | `/analytics/dashboard` | sales or payments family | Every tile the caller may see (FR-031–FR-033) |
| POST | `/analytics/detail` | family + read on the document kind (orders and lines 7, payments 8, refunds 22, credit notes 23) | The documents behind an aggregate (FR-040–FR-042) |
| POST | `/analytics/exports` | as the source's route | Export a query, dashboard or detail. 200 with the file, or 202 with a job (FR-050–FR-055) |
| GET | `/analytics/exports/{analytics_export_id}` | owner | Job status |
| GET | `/analytics/exports/{analytics_export_id}/file` | owner | Download a ready export |

**The families** (FR-060), checked as `require_any_privilege(..., READ)`:
- **Sales**: 34, 35, 36, 40, 41, 47, 51, 52, 53, 55, 61, 62, 63.
- **Cost** (sensitive measures): 37, 38, 39.
- **Payments**: 33, 109.
- **Bulk detail export** (detail kinds `lines` and `payments` over more than one day without other filters): 113.

**Cost visibility:**
- Callers without the cost privilege never see the sensitive measures. The catalog leaves them out, and a query naming one is refused with `auth.forbidden` (its `required` lists 37, 38, 39).
- The dashboard, detail and exports leave out `cost`, `margin` and every sensitive measure.

**Facility scope** (FR-061):
- Callers who are administrators, or who hold 101, may pass `facility` (one facility) or omit it (every facility).
- Everyone else always gets their own facility. Passing another one is refused with `facility.other_facility` (`required` lists 101).

## Catalog

```
AnalyticsCatalogResponse:
  as_of: datetime
  measures: [AnalyticsMeasure, ...]
  dimensions: [AnalyticsDimension, ...]

AnalyticsMeasure:
  key: MeasureKey          # e.g. net_sales
  label: str
  unit: str                # money | quantity | count | ratio | days
  family: str              # spec 038: the measure's family
  kind: str                # spec 038: flow | balance
  sensitive: bool
  vat: bool                # true when vat=included changes it
  additive: bool           # false for ratios
  dimensions: [DimensionKey, ...]  # the dimensions it can be grouped or filtered by
  definition: str          # spec 038: the written definition

AnalyticsDimension:
  key: DimensionKey        # e.g. product
  label: str
  grains: [Grain, ...]     # date only: day, week, month, quarter, year
```

## Query

```
AnalyticsQueryRequest:
  measures: [MeasureKey, ...]      # 1-12
  dimensions: [DimensionKey, ...]  # 0-3
  grain: Grain             # day (default) | week | month | quarter | year; used when date is a dimension
  start: date
  end: date                # inclusive; end - start < 5 years
  facility: int | None
  filters: [AnalyticsFilter, ...]
  comparison: Comparison | None    # previous_period | same_period_last_year
  vat: VatTreatment        # excluded (default) | included
  sort: str | None         # a requested measure or dimension key; "-" prefix = descending
  top: int | None          # 1-100; the rest folds into others
  stock_options: StockOptions | None  # spec 038: inventory windows and targets
  price_change_pct: int | None        # spec 038: purchase price change threshold, whole percent

AnalyticsFilter:
  dimension: DimensionKey
  values: [str, ...]       # ids for entity dimensions, the value for brand/model/customer_type/payment_terms/weekday/hour, ISO date for date

AnalyticsQueryResponse:
  as_of: datetime
  definition: AnalyticsQueryRequest   # normalized: defaults filled, facility resolved
  rows: [AnalyticsRow, ...]
  others: AnalyticsRow | None
  totals: AnalyticsRow
  overlapping: bool                   # true when grouped by label: a product with two labels counts in both rows

AnalyticsRow:
  keys: [AnalyticsKey, ...]           # one per requested dimension, in order; empty on totals
  values: dict                        # measure key -> decimal string or null
  share: dict                         # additive measure key -> ratio of the totals
  comparison: dict | None             # measure key -> value over the comparison period
  change: dict | None                 # measure key -> value - comparison
  change_pct: dict | None             # measure key -> change / comparison; null when comparison is 0
  drill: [AnalyticsFilter, ...]       # filters that reproduce this row (period and facility are the request's)

AnalyticsKey:
  dimension: DimensionKey
  value: str | None                   # id or value; null = "none"
  label: str                          # display text: "PT-001 Cemento gris 50 kg", "Monterrey", "2026-10"
```

**Rules:**
- Rows add up to `totals` for every additive measure, `others` included (FR-012).
- Ratios on every row and on the totals are computed from their parts.
- Without `sort`, rows are ordered by the first dimension; a date dimension sorts ascending.
- With `top`, the sort defaults to the first measure descending.
- With a date dimension and a comparison, rows align by position: day n of the period against day n of the comparison.

## Dashboard

`GET /analytics/dashboard?start=&end=&facility=&comparison=` (comparison defaults to `previous_period`).

```
AnalyticsDashboardRequest:
  start: date
  end: date
  facility: int | None
  comparison: Comparison | None   # previous_period (default) | same_period_last_year

AnalyticsDashboardResponse:
  as_of: datetime
  definition: AnalyticsDashboardRequest
  comparison_start: date
  comparison_end: date
  kpis: [AnalyticsKpi, ...]
  series: [AnalyticsSeries, ...]
  alerts: [AnalyticsAlert, ...]

AnalyticsKpi:
  key: str                 # net_sales | margin_pct | orders | average_ticket | collected_today | collected | refunds | cost_coverage
  value: Decimal | None
  comparison: Decimal | None
  change: Decimal | None
  change_pct: Decimal | None
  sparkline: [AnalyticsPoint, ...]
  drill: AnalyticsQueryRequest

AnalyticsPoint:
  date: date
  value: Decimal | None

AnalyticsSeries:
  key: str                 # sales_over_time | sales_by_facility | sales_by_label | sales_by_brand | top_products | top_customers | payment_mix | heatmap
  chart: str               # line | bar | donut | heatmap
  query: AnalyticsQueryResponse

AnalyticsAlert:
  key: str                 # overdue_credit_orders | lines_below_cost | lines_without_cost | refund_rate
  facility: int | None     # refund_rate only: one alert per facility where it fires
  count: int
  amount: Decimal | None
  detail: AnalyticsDetailRequest | None  # null only for spec 038's payables_overdue
```

**Tiles:**
- Sparklines use day grain up to 62 days, week up to 1 year, then month.
- **VAT**: the dashboard has no `vat` parameter. Every tile and alert is without VAT (`vat=excluded`, FR-003), except average ticket, which is with VAT (FR-004). Each `drill` and each alert's `detail` sets `vat` explicitly to its tile's treatment, so following it gives the same number.
- `sales_over_time` charts net sales and gross margin with the comparison line.
- `heatmap` is `orders` by weekday × hour.
- Each alert's `amount` equals `sums.amount` of its `detail`: the sum of balances for `overdue_credit_orders`, the sum of the lines' sales amounts without VAT for `lines_below_cost` and `lines_without_cost`, and the sum of refunds for `refund_rate`.
- `refund_rate` appears once per facility where it fires. Its `count` and `amount` are that facility's refunds in the period, and its `detail` is `kind=refunds` filtered by that facility.
- Tiles and alerts the caller may not see are absent (FR-033):
  - margin and cost coverage need the cost privilege;
  - payment tiles need the payments family;
  - sales tiles need the sales family.

## Detail

```
AnalyticsDetailRequest:
  kind: DetailKind         # orders | lines | refunds | credit_notes | payments
  start: date
  end: date
  facility: int | None
  filters: [AnalyticsFilter, ...]
  flag: DetailFlag | None  # overdue (orders) | below_cost, without_cost (lines, cost privilege) | collected, paid_out, store_credit (payments)
  vat: VatTreatment
  sort: str | None         # date | amount | document; "-" = descending (default -date)
  skip: int
  limit: int               # 1-100, default 20
  price_change_pct: int | None  # spec 038: whole percent, default 10; purchases' price_change flag

AnalyticsDetailResponse:
  as_of: datetime
  items: [AnalyticsDetailItem, ...]
  total: int
  sums: dict               # amount, quantity, discount, cost and margin (the last two when visible) over every page: they equal the aggregate

AnalyticsDetailItem:
  kind: DetailKind
  id: int                  # sales_order_id | sales_order_detail_id | customer_refund_id | fiscal_document_id | customer_payment_id
  document_id: int | None  # spec 038: null for stock pairs; the document to open: the order for a line, the refund, the fiscal document, the payment
  folio: str | None
  date: datetime | None    # spec 038: null for stock pairs
  facility: int | None    # spec 038: null only where a kind has none
  customer: int | None
  customer_name: str | None
  salesperson: int | None
  product: int | None
  product_code: str | None
  product_name: str | None
  quantity: Decimal | None
  amount: Decimal | None   # spec 038: null where a kind has no amount; positive: a line's or order's sales amount, a refund's, a credit note's, or a payment's amount in its class (collected net of change, paid out, store credit); the balance with `flag=overdue`; with the request's VAT treatment
  cost: Decimal | None     # cost privilege only; null for uncosted lines
  margin: Decimal | None   # cost privilege only
  payment_method: int | None
  warehouse: int | None    # spec 038
  figures: AnalyticsDetailFigures | None  # spec 038: the kind's own columns
```

**Flags** select the documents of an alert whose condition isn't a dimension, so `total` equals the alert's `count`:
- `overdue` on `orders`: open credit orders past their due date as of today (spec 033's balance rule). The period is ignored, and `amount` is the balance.
- `below_cost` and `without_cost` on `lines`: lines whose net price is below their cost, or that have no cost. They need the cost privilege.
- `collected`, `paid_out` and `store_credit` on `payments`: the payments of one R6 class. Without a flag, `payments` lists every class.
- A flag on another kind fails request validation (422).

**Which detail figure equals which measure** (same filters, period, scope and VAT):

| Measure | Kind | Detail figure |
|---|---|---|
| gross sales | `lines` | `sums.amount` |
| discounts | `lines` | `sums.discount` |
| units sold | `lines` | `sums.quantity` |
| orders | `orders` | `total` |
| refunds | `refunds` | `sums.amount` |
| units refunded | `refunds` | `sums.quantity` |
| credit notes | `credit_notes` | `sums.amount` |
| collected | `payments`, `flag=collected` | `sums.amount` |
| payments | `payments`, `flag=collected` | `total` |
| refunds paid out | `payments`, `flag=paid_out` | `sums.amount` |
| store credit issued | `payments`, `flag=store_credit` | `sums.amount` |

Derived measures reconcile as their parts: net sales = `lines` − `refunds` − `credit_notes` on `sums.amount`; units = `lines` − `refunds` on `sums.quantity`; cost of sales = `lines` − `refunds` on `sums.cost`; gross margin = `lines` − `refunds` on `sums.margin`. Costed net sales and the ratios have no drill of their own. Every `amount` is positive; the subtraction is the measure's, never the item's.

**Cost and margin keys**: for callers without the cost privilege, `cost` and `margin` are left out of every item and of `sums` (absent, not null). For holders, an uncosted line has `cost: null`.

A client opens the existing screen for `document_id`: `/sales-orders/{id}`, `/customer-refunds/{id}`, `/fiscal-documents/{id}` or `/customer-payments/{id}`.

## Exports

```
AnalyticsExportRequest:
  format: ExportFormat     # xlsx | pdf | csv
  query: AnalyticsQueryRequest | None
  dashboard: AnalyticsDashboardRequest | None
  detail: AnalyticsDetailRequest | None
  kardex: AnalyticsKardexRequest | None  # spec 038
  customer: AnalyticsCustomerRequest | None  # spec 038

AnalyticsExportResponse:
  analytics_export_id: int
  source: ExportSource     # query | dashboard | detail; spec 038: kardex, customer
  format: ExportFormat
  status: ExportStatus     # pending | running | ready | failed
  row_count: int | None
  creation_time: datetime
  completion_time: datetime | None
  expiry_time: datetime | None
  download_url: str | None
```

**Sources and formats:**
- Exactly one source per request; otherwise the request is refused with `analytics.export_source_invalid`.
- A dashboard exports to PDF only. A detail export takes every page; its `skip` and `limit` are ignored.

**Small and large exports** (FR-054):
- At most 10,000 rows: `200` with the file (`Content-Disposition: attachment`).
- More than 10,000 rows (XLSX, CSV): `202` with `AnalyticsExportResponse`. Poll `GET /analytics/exports/{id}`; when `ready`, download `GET /analytics/exports/{id}/file` until `expiry_time` (24 h).

**PDF** caps at 2,000 table rows. A larger one is refused with `analytics.pdf_too_large`.

**File layout:**
- **XLSX:** sheet "Resumen" holds the title, period, comparison, facility, filters written out, VAT treatment, `as_of`, and the generator's user and name. Sheet "Datos" holds the dimension columns then the measure columns, a frozen header row, an autofilter, typed cells (dates as dates; money as `#,##0.00`; ratios as `0.0%`), and a bold totals row.
- **CSV:** UTF-8 with a BOM, comma separated, ISO dates, dot decimals, raw ratios (0.253). The header row uses the measure and dimension keys, with a final `TOTAL` row.
- **PDF:**
  - company header, the filters, then the KPIs (dashboard) or the table (query and detail);
  - the charts as inline SVG drawn from the same rows;
  - landscape pages when there are more than 8 columns;
  - headers repeated on every page and a totals row;
  - `Página n de m` page numbers.

## Error codes (added to spec 036's catalog)

They are added to `specs/036-localizable-error-codes/contracts/error-codes.md` under a new `## analytics` section, and to `ErrorCode`.

| Code | Status | Params | Situation |
|---|---|---|---|
| `analytics.too_many_dimensions` | 422 | max: int, given: int | More than three dimensions were requested. |
| `analytics.dimension_not_applicable` | 422 | measure: str, dimension: str | A measure can't be grouped or filtered by a dimension (a payment measure by product). |
| `analytics.period_invalid` | 422 | start: date, end: date, max_days: int | The end is before the start, or the period is longer than five years. |
| `analytics.sort_invalid` | 422 | sort: str | The sort key is neither a requested measure nor a requested dimension. |
| `analytics.export_source_invalid` | 422 | — | An export request must name exactly one source, and a dashboard exports only to PDF. |
| `analytics.pdf_too_large` | 422 | rows: int, max_rows: int | A PDF would hold more than 2,000 table rows; export to XLSX or CSV instead. |
| `analytics.export_not_ready` | 409 | status: enum:ExportStatus | The export is still running or failed. |
| `analytics.export_expired` | 410 | expiry_time: datetime | The export's file was deleted after 24 hours. |

**Reused codes:**
- `auth.forbidden`: a sensitive measure without the cost privilege, or a family without its privilege.
- `facility.other_facility`: another facility without 101.
- `resource.not_found` with resource `analytics_export`: an unknown export, or another user's.
- Unknown measure, dimension, grain or format keys fail request validation (FastAPI's 422 with type `enum`), because the keys are StrEnums published in OpenAPI.

## Changed shapes elsewhere

`SalesOrderResponse` gains `confirmation_time: datetime | None`, when the order was confirmed (null on drafts). The change is additive. No contract doc carries a checked `SalesOrderResponse` block, so nothing else changes.
