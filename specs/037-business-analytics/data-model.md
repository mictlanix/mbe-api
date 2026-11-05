# Data model: Business analytics (spec 037)

There are three kinds of change: one column on an existing table, one new table, and the in-memory catalog that defines every measure and dimension. Everything else is read from existing documents.

## Schema changes (migration `031_business_analytics.sql`, with a rollback file)

### `sales_order.confirmation_time` (new column)

| Column | Type | Rule |
|---|---|---|
| `confirmation_time` | `DATETIME NULL` | Business time (naive local). Set by `_confirm_locked` to `local_now()` when an order is confirmed. Never changed afterwards, including by cancellation. NULL only while the order is a draft. |

- **Backfill:** `UPDATE sales_order SET confirmation_time = date WHERE completed = 1` (R2). This covers about 300k orders, cancelled-after-confirmation ones included.
- **Index:** `sales_order_confirmation_time_idx (confirmation_time)`.
- **Model:** `SalesOrder.confirmation_time: Mapped[datetime | None]`. `SalesOrderResponse` gains `confirmation_time: LocalDateTime | None`. This is additive, so mbe-ui can show it.

### New indexes (R10)

- `customer_payment_date_idx (date)`
- `customer_refund_date_idx (date)`
- `fiscal_document_type_issued_idx (type, issued)`

### `analytics_export` (new table)

One background export (FR-054).

| Column | Type | Rule |
|---|---|---|
| `analytics_export_id` | `INT AUTO_INCREMENT PK` | |
| `user` | `VARCHAR(20)` FK `user.user_id` | The owner. Only the owner can read or download it. |
| `source` | `TINYINT` | `ExportSource`: QUERY 0, DASHBOARD 1, DETAIL 2 |
| `format` | `TINYINT` | `ExportFormat`: XLSX 0, PDF 1, CSV 2 |
| `definition` | `TEXT` | The normalized request as JSON. Re-running it gives the same numbers (FR-050). |
| `status` | `TINYINT` | `ExportStatus`: PENDING 0, RUNNING 1, READY 2, FAILED 3 |
| `file_name` | `VARCHAR(100) NULL` | Under `settings.exports_dir`. Set when READY. |
| `row_count` | `INT NULL` | |
| `creation_time` | `DATETIME` | |
| `completion_time` | `DATETIME NULL` | |
| `expiry_time` | `DATETIME NULL` | `completion_time` + 24 h. After it, the file is deleted and a download answers `analytics.export_expired`. |

- **Stale runs:** a RUNNING row older than 10 minutes is shown as FAILED (its worker died).
- **Registry:** the public resource name `analytics_export` is added to spec 036's `RESOURCE_NAMES`.
- **Setting:** `settings.exports_dir = 'exports'`, which is never mounted.

## Facts (SQL, read only)

Each fact is one grouped `SELECT` built from the request. Money comes out in cents, with the document's rounding residual placed on one line (R3).

| Fact | Rows | Date | Facility | Lives in |
|---|---|---|---|---|
| `sale_line` | lines of confirmed, non-cancelled orders | `sales_order.confirmation_time` | the order's | `sales_order_detail ⋈ sales_order` |
| `refund_line` | lines of confirmed, non-cancelled refunds | `customer_refund.date` | the refund's | `customer_refund_detail ⋈ customer_refund` (cost from the linked `sales_order_detail`) |
| `credit_note` | stamped, non-cancelled CFDI credit notes (type 100) | `fiscal_document.issued` | the document's | `fiscal_document(_detail)` |
| `payment` | customer payments, classified per R6 | `customer_payment.date` | the payment's | `customer_payment` (+ live `sales_order_payment.amount_change`, + `cash_session.cashier`) |

Per-line amounts, as in `totals.line_amounts`:
- **gross** = qty × price × (1 − discount_rate);
- **subtotal** = gross ÷ (1 + tax_rate) if the price includes tax, else gross;
- **tax** = gross − subtotal if the price includes tax, else gross × tax_rate;
- **discount** = qty × price × discount_rate, on the same VAT basis;
- **cost without VAT** = qty × cost: the snapshot is recorded without VAT (R4, corrected 2026-11-05). **Below cost** compares the unit price after discount and without VAT with the cost.

Refund lines use `discount` (an amount), not `discount_rate`; their gross follows the refund line rule in `customer_refund_service`.

## Catalog (code, `app/services/analytics/catalog.py`)

### Measure

| Field | Meaning |
|---|---|
| `key` | stable snake_case, e.g. `net_sales` |
| `label` | English label (mbe-ui shows its own Spanish one) |
| `unit` | `money`, `quantity`, `count`, `ratio` |
| `facts` | the facts it reads |
| `sensitive` | needs the cost privilege |
| `vat` | whether `vat=included` changes it |
| `additive` | false for ratios, which are recomputed from their parts at every level |

| Key | Unit | Facts | Sensitive | VAT | Definition |
|---|---|---|---|---|---|
| `gross_sales` | money | sale_line | | yes | Σ line subtotal (+ tax when included) |
| `discounts` | money | sale_line | | yes | Σ line discount |
| `refunds` | money | refund_line | | yes | Σ refund line subtotal (+ tax) |
| `credit_notes` | money | credit_note | | yes | Σ credit note line subtotal (+ tax) |
| `net_sales` | money | sale_line, refund_line, credit_note | | yes | gross_sales − refunds − credit_notes |
| `costed_net_sales` | money | sale_line, refund_line | ✓ | no | net of lines with cost > 0, without VAT |
| `cost_coverage` | ratio | (parts) | ✓ | no | costed_net_sales ÷ net_sales (without VAT) |
| `cost_of_sales` | money | sale_line, refund_line | ✓ | no | Σ sale cost without VAT − Σ refunded cost without VAT, costed lines only |
| `gross_margin` | money | (parts) | ✓ | no | costed_net_sales − cost_of_sales |
| `margin_pct` | ratio | (parts) | ✓ | no | gross_margin ÷ costed_net_sales |
| `units_sold` | quantity | sale_line | | no | Σ qty |
| `units_refunded` | quantity | refund_line | | no | Σ refund qty |
| `units` | quantity | (parts) | | no | units_sold − units_refunded |
| `orders` | count | sale_line | | no | COUNT(DISTINCT order) |
| `average_ticket` | money | (parts) | | no | gross_sales with VAT ÷ orders |
| `collected` | money | payment | | no | R6 |
| `refunds_paid_out` | money | payment | | no | R6 |
| `store_credit_issued` | money | payment | | no | R6 |
| `payments` | count | payment | | no | count of payments counted in `collected` |

### Dimension

| Field | Meaning |
|---|---|
| `key` | e.g. `product` |
| `label` | English label |
| `facts` | each fact that provides it, with its column. A fact without it yields "none" for money facts that can't be split (credit notes by product). A payment fact without it is refused (FR-006). |
| `display` | how a row is labelled: id plus name (the product's code and name, the customer's name, the employee's name, the facility's code) |

| Key | sale_line | refund_line | credit_note | payment |
|---|---|---|---|---|
| `date` (grain: day, week, month, quarter, year) | confirmation_time | date | issued | date |
| `weekday`, `hour` | confirmation_time | date | issued | date |
| `facility` | order | refund | document | payment |
| `point_of_sale` | order | via order | none | refused |
| `salesperson` | order | refund `sales_person` | none | session cashier |
| `cash_session` | refused | refused | refused | payment |
| `customer`, `customer_type` | order | refund | document | payment |
| `product`, `brand`, `model`, `label` | line | line | none | refused |
| `payment_terms` | order | via order | none | refused |
| `payment_method` | refused | refused | refused | payment |

**Facility scope** is applied to every fact's facility column (FR-061).

## Request and response shapes (Pydantic, `app/schemas/analytics.py`)

These are summarized here; the full field lists are in `contracts/api.md`.

- **`QueryDefinition`**:
  - `measures: list[MeasureKey]` (1–12)
  - `dimensions: list[DimensionKey]` (0–3)
  - `grain: Grain = day`, used when `date` is a dimension
  - `start: date`, `end: date`
  - `filters: list[Filter{dimension, values}]`
  - `comparison: Comparison | None` (`previous_period`, `same_period_last_year`)
  - `vat: included | excluded = excluded`
  - `facility: int | None` (None = the caller's own, or every facility for privileged callers)
  - `sort: str | None`, `top: int | None` (1–100)
  
  `MeasureKey` and `DimensionKey` are StrEnums, so an unknown key fails request validation with FastAPI's 422 and the allowed values are in OpenAPI.
- **`QueryResult`**:
  - `as_of`, `definition` (normalized)
  - `columns` (dimension and measure metadata)
  - `rows: list[Row]`, `others: Row | None`, `totals: Row`
  - `overlapping: bool` (label)
  
  `Row` holds `keys` (dimension values with display labels), `values` (measure → Decimal string), `share` (measure → ratio), `comparison`, `change`, `change_pct`, and `drill` (the Filter list that reproduces it).
- **`Dashboard`**: `as_of`, `period`, `comparison_period`, `kpis: list[Kpi]`, `series: list[Series]`, `alerts: list[Alert]`. Absent tiles are omitted (FR-033).
- **`DetailRequest`**: `kind` (orders, lines, refunds, credit_notes, payments), `start`, `end`, `facility`, `filters`, `flag` (`overdue` on orders; `below_cost`, `without_cost` on lines; `collected`, `paid_out`, `store_credit` on payments), `vat`, `sort`, `skip`, `limit`. **`DetailPage`**: `items`, `total`, `sums` (`amount`, `quantity`, `discount`, and `cost`/`margin` for holders, over every page; which one equals which measure is the contract's table), `as_of`.
- **`ExportRequest`**: `format` and exactly one of `query`, `dashboard` or `detail`. The source is the field that is set, and is stored in `analytics_export.source`. **`AnalyticsExportResponse`** returns the table row's public fields plus `download_url`.

## State: `analytics_export.status`

```text
PENDING ──task starts──► RUNNING ──file written──► READY ──24 h──► (deleted; download → export_expired)
                              └──error / stale 10 min──► FAILED
```
