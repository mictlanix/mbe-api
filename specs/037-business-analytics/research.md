# Research: Business analytics (spec 037)

All mbe_dev figures were measured read only on 2026-11-04 (MariaDB 10.11.13). "Confirmed" means `completed = 1 AND cancelled = 0`.

## R1. Query documents directly; no summary tables

**Decision**: Every measure is computed from the documents at request time. New indexes remove the full scans. A cache of at most 60 seconds absorbs repeated requests. Summary tables are added only if SC-003 is missed on mbe_dev, and the measurement that shows it is recorded here.

**Rationale**:
- Volume is small: 70,683 confirmed lines and 34,163 orders in 2025, growing about 15% a year.
- Speed: a full year of raw lines aggregates in 0.22–0.25 s, even though every date filter today is a full scan of `sales_order` (342k rows) or `customer_payment` (295k rows), because no date column has an index.
- Compression: a daily summary at (day, facility, product, customer, salesperson) would hold 67,837 rows for 2025, which is 96% of the line table. Only month × facility × product compresses (2.3×), and that grain can't answer day, customer or salesperson questions.
- Freshness: reading the documents is always current, so FR-020's five minutes holds trivially. `as_of` is the read time.

**Alternatives considered**:
- Daily summary tables maintained on confirm/cancel: rejected. They need a write hook in every document flow (confirm, cancel, refund confirm, payment, application) plus a rebuild command, which is more code to keep correct for no measured gain.
- A watermark job: rejected. It adds staleness and a scheduler the repo doesn't have.

## R2. A confirmation moment on sales orders (migration 031)

**Decision**: Add `sales_order.confirmation_time DATETIME NULL`. The migration fills it with `date` for every order with `completed = 1`, including those cancelled later, and indexes it. `_confirm_locked` (`sales_order_service.py:1529`, the only place that sets `completed = True`) sets it to `local_now()`. Analytics date sales by `confirmation_time` alone, with no fallback.

**Rationale**:
- No confirmation timestamp exists today. `date` equals `creation_time` on all 34,163 confirmed 2025 orders, and confirming sets only `modification_time`, which later edits overwrite.
- One code path confirms orders: POS and back office both call `POST /sales-orders/{id}/confirm`, and quote conversion creates a draft. Nothing sets `completed` back to False.
- Sales orders are retired from the legacy system, so every future confirmation goes through `_confirm_locked`.
- The column stays nullable because drafts have none. A test asserts that every confirmed order has one.

**Alternatives considered**:
- Order date as the sale date: rejected by the user.
- Delivery date: 8,018 of 2025's sales have only delivery orders the legacy system left unfinished, so there is no delivery date for most history.
- `modification_time`: later edits overwrite it.

## R3. Money: line cents with document residuals, so every grouping reconciles

**Decision**: Each fact line carries its amounts in cents:
- without VAT (`subtotal`), VAT (`tax`) and discount, using `totals.line_amounts`'s formulas;
- each rounded to cents (ROUND_HALF_UP, which is MariaDB's and SQLite's `ROUND` for these values);
- the document residual (the document's rounded total minus the sum of its rounded lines) added to one line per document, the one with the largest amount, lowest id on ties.

This is done in SQL with window functions (`SUM() OVER (PARTITION BY document)`, `ROW_NUMBER()`), which both MariaDB 10.11 and SQLite 3.25+ support. They are verified on mbe_dev.

**Rationale**:
- `document_totals` rounds once per document. Summing unrounded lines per product would differ from the sales order screens by cents, and rounding per group would make rows disagree with totals (FR-012).
- With residual-adjusted line cents every measure is a plain integer sum. Any grouping adds up to its total, and the sales by day equal the sum of the order totals the screens show (SC-001).
- No SQL in the repo aggregates money today, so this establishes the pattern. It mirrors `totals.py` exactly, and a unit test compares the SQL facts with `document_totals` for a set of fixture orders.

**Alternatives considered**:
- Rounding in Python after fetching lines: about 70k rows a year would be fetched for every year-wide request.
- Rounding per (document × requested dimensions): rows don't agree across groupings.

## R4. Cost includes VAT; margin is computed without VAT on costed lines

**Decision**:
- A line's cost without VAT = quantity × cost ÷ (1 + the line's VAT rate) when the line's price includes VAT, and quantity × cost otherwise. Cost is on the same basis as price.
- Margin = costed net sales without VAT − cost of sales without VAT.
- A line has a cost when `cost > 0` (clarified). Refund lines take the cost of the sale line they return (`customer_refund_detail.sales_order_detail` → `sales_order_detail.cost`, scaled by the refunded quantity), since refunds carry no cost.

**Rationale**:
- Over 4,175 products, the median ratio of the cost list price to the latest 2025 purchase price without VAT is exactly 1.1600, and 3,160 products fall within 1.13–1.19. Cost is therefore recorded with VAT, like price: all 9,421 purchase lines of 2025 and all 70,683 sales lines have tax included.
- Subtracting a cost with VAT from a price without VAT would understate margin by 16% of cost.

**Correction to the spec**: its assumption "line cost is without VAT" is reversed. Updated in the spec.

## R5. CFDI credit notes stand alone; refunds are customer_refund

**Decision**:
- **Refunds** come only from `customer_refund` and its lines (confirmed, not cancelled, dated by `customer_refund.date`, which is set at confirmation).
- **Credit notes** are CFDI credit notes (`fiscal_document.type = 100`, stamped, not cancelled), dated by their issue date and valued from their lines with the same line rule.
- Credit notes carry no product, order or salesperson. They group under "none" for those dimensions and by the recipient customer and issuing facility otherwise.

**Rationale**:
- Of 126 CFDI credit notes ever, only 2 have any `order_detail`, and none reaches a refund line. No column links a fiscal document to a refund.
- Their contents are rebates ("BONIFICACION"), adjustments and one advance application. They reduce sales, but aren't returns.
- Volume is tiny: 2–3 a year since 2024. Confirming a refund writes no fiscal document (`_pay_customer_back`), so no double count is possible.
- The store-credit `credit_note` rows written by refunds are the payout of a refund, not a separate reduction.

## R6. Payments: collected, refunds paid out, store credit issued

**Decision**: Three payment measures. They classify each `customer_payment` exactly as the cash cut does (`cash_session_service.cut_figures`, :242–251):

| Measure | Payments | Amount |
|---|---|---|
| Collected | `payment_type != CREDIT_NOTE` and `amount > 0` | amount − the `amount_change` of its live applications |
| Refunds paid out | `amount < 0` (any type) | abs(amount) |
| Store credit issued | `payment_type = CREDIT_NOTE` and `amount > 0` | amount |

The cut's refunds by method equal refunds paid out plus store credit issued, the latter always under method N/A.

**Rationale**:
- The cut is the reference users trust. Collected by method for a session equals `cut_figures(...).sales_by_method`, and that is SC-001's payments half.
- Store credit is one positive credit-note-type payment per refund, created when the refund is confirmed. Spending it is an application of that same payment to another order (359 in 2025, 754 in 2026) and creates no new payment. So collected never double-counts it.
- Every negative payment is a refund paid in money. All 223 in 2025 are of the credit-note type, and later cash payouts are IMMEDIATE/CASH.
- Payments group by date, facility, cash session, customer and method. The salesperson comes from the cash session's employee; payments have no salesperson of their own.

**Not used**: `payments_by_method` (the gross sum per method) overstates the drawer by the change given.

## R7. Date grains, weekday and hour on both dialects

**Decision**: A small set of SQLAlchemy `FunctionElement`s compiled per dialect with `@compiles`:
- `period_start(grain, col)`: day, week (Monday), month, quarter and year starts;
- `weekday(col)` (Monday = 1);
- `hour_of(col)`.

MariaDB uses `DATE`, `DATE_FORMAT`, `WEEKDAY`, `QUARTER` and `HOUR`; SQLite uses `date(col, modifiers)` and `strftime`. Period filters compare the raw column with Python-computed bounds (`>= start`, `< end + 1 day`), the house pattern (`cash_session_service._status_clause`), so the indexes are used.

**Rationale**:
- Integration tests run on SQLite (`tests/integration/conftest.py`), and MariaDB constructs are checked by compiling with `sqlalchemy.dialects.mysql` in unit tests, as `test_cash_session_service.py` does.
- Stored datetimes are naive business time (`app/core/clock.py`), so no time-zone conversion happens in SQL.

## R8. Query engine: one fact per source, merged in Python

**Decision**: Measures belong to one of four facts:
- sale lines (gross sales, discounts, cost, units, orders);
- refund lines;
- credit notes;
- payments.

A request runs one grouped aggregate per fact it needs. The results are merged in Python on the dimension tuple, which emulates a full outer join. Python then computes:
- net and derived measures, and ratios from their parts (margin %, cost coverage, average ticket);
- shares, comparisons and top N with "others".

Dimensions map to columns per fact. A dimension a fact can't provide (product for credit notes) yields "none" for that fact. A measure whose fact can't group by a requested dimension (a payment measure by product) is refused (FR-006).

**Rationale**:
- It keeps every SQL statement a single grouped scan over indexed date ranges.
- Ratios are never averaged.
- One code path serves the query, dashboard, drill-down totals and exports (FR-050).

**Labels** are many-to-many (2 products have 2 labels; 4,948 of 7,105 products sold have none). Grouping by label joins `product_label`, so a two-label product counts under each label. The response's `overlapping: true` flag says rows can exceed the total, which is computed without the label join.

## R9. The dashboard in a few concurrent queries

**Decision**: The dashboard runs a fixed set of fact queries concurrently, each on its own session from `AsyncSessionLocal`, and derives every tile from them:
1. sale lines by day × facility;
2. refunds by day × facility;
3. payments by day × facility × method;
4. orders by weekday × hour;
5. top products;
6. top customers;
7. sale lines by label;
8. by brand.
- **Comparison:** the comparison period reuses queries 1–3 over its own range.
- **Overdue alert:** reuses `open_credit_orders` (the spec 033 balance rule, which SQL can't reproduce).
- **Costed alerts:** the cost alerts are two counts on the sale-line fact.

**Measured**: Every query takes 0.06–0.23 s for a full year on mbe_dev without the new indexes (R10). Eight run concurrently in about the slowest one, which fits the 500 ms p95. Each concurrent query takes a session from `async_sessionmaker(db.bind)`, built from the request's session rather than the global `AsyncSessionLocal`, so the SQLite tests (which override `get_db`) reach the same database. If the year view misses SC-003 after the indexes, summary tables per R1 are the fallback.

**As built, measured (T063, 2026-11-05, mbe_dev after migration 031):** timed in-process on xolotl through the full route stack, the response cache cleared before each run, all facilities, administrator.

| Request | p50 | p95 | Target |
|---|---|---|---|
| Dashboard, October 2025 (30 runs) | 2.17 s | 2.26 s | < 500 ms: **missed** |
| Dashboard, 2025 (30 runs) | 8.51 s | 8.93 s | < 500 ms: **missed** |
| Query, 2025, product × month, the 10 non-sensitive measures, 24,423 rows (10 runs) | 4.34 s | 4.46 s | < 2 s: **missed** |
| `open_credit_orders`, all facilities (30 runs) | 0.27 s | 0.40 s | recorded |
| Detail lines export, 2024-07-01 to 2025-12-31, 104,718 rows, CSV | 8.2 s (7.4 query + 0.8 write) | — | < 30 s: met |
| The same, XLSX | 15.6 s (7.7 query + 8.0 write) | — | < 30 s: met |

**Why:** the dashboard isn't R9's eight queries. Each tile is its own `engine.execute`, which runs the facts once for its rows, once more for the totals, and again for the comparison. A 2025 dashboard issues 94 fact queries: 31 over sale lines (32.4 s of database time), 27 over refunds, 24 over credit notes and 12 over payments, about 8.7 s of wall time even when concurrent. One sale-line fact for 2025 now takes about 1 s rather than R1's 0.22–0.25 s, because of the per-document residual (`ROW_NUMBER`/`SUM OVER`) on every line. Summary tables at R1's daily grain would hold 96% of the line rows, so they wouldn't remove that cost by themselves. Per T063, this is reported before any of them is built.

**After sharing the fact queries (same day, the user chose to try this first):** the engine now lists the fact queries a definition reads (`engine.wanted`) and builds the answer from their rows (`engine.assemble`). The dashboard runs each distinct query once, concurrently with the overdue alert, and assembles every tile and alert from the shared rows, so each tile still reads exactly the SQL `POST /analytics/query` runs for it. A 2025 dashboard now issues 58 fact queries rather than 94, 18 of them over sale lines.

| Request (10 warm runs, cache cleared) | p50 | p95 |
|---|---|---|
| Dashboard, October 2025 | 1.41 s | 1.53 s |
| Dashboard, 2025 | 4.42 s | 4.59 s |

Both still miss 500 ms. A profile of one year dashboard: about 2.4 s waiting on MariaDB for the 18 concurrent sale-line scans, and the rest in Python, mostly reading 24,000 rows through aiomysql and merging them (the product and customer tiles fetch every product and customer before cutting to the top 10).

**Tried and not kept:** rounding per document with a `GROUP BY` on the order instead of the window, for groupings without a line dimension. Alone it takes 0.64 s rather than 0.99 s for 2025, but 12 concurrent copies take 2.43 s against the window's 1.74 s, so it doesn't help the dashboard.

## R10. Indexes (migration 031)

**Decision**: Add:
- `sales_order(confirmation_time)`;
- `customer_payment(date)`;
- `customer_refund(date)`;
- `fiscal_document(type, issued)`.

Each is verified with EXPLAIN on mbe_dev before and after (quickstart §2).

**Rationale**: Every date filter is a full scan today (EXPLAIN: `type ALL`). Detail-table joins already use FK indexes: `sales_order_detail.sales_order`, `customer_refund_detail.customer_refund` and `fiscal_document_detail.document`.

**Before migration 031 (measured 2026-11-04, T060, mbe_dev, the compiled fact queries for 2025):**

| Fact | Plan |
|---|---|
| sale_line | `sales_order` ALL (339,597 rows), lines by `sales_order_detail_sales_order_fk_idx` (`date` stood in for `confirmation_time`, which 031 adds) |
| refund_line | `customer_refund` ALL (9,468), lines and the sold line by their keys |
| credit_note | `fiscal_document` ALL (58,070), lines by `fiscal_document_detail_document_fk_idx` |
| payment | `customer_payment` ALL (293,738), change per payment as a lateral derived lookup on `sales_order_payment_customer_payment_idx` |

The first payment fact grouped every `sales_order_payment` row (298,958) for the change, whatever the period; it now joins the period's payments first, which is the lateral lookup above. Non-MXN documents in `sales_order`, `sales_order_detail`, `customer_refund`, `customer_payment` and `fiscal_document`: 0 each (FR-003).

**Not touched**: `sales_order` carries two identical unique keys on (facility, serial). That's unrelated, so it is mentioned and left alone (constitution III).

## R11. Privileges

**Decision**: As in FR-060, using `require_any_privilege(...)` for families and `has_any_privilege` mid-request to drop sensitive measures (the `supplier_service.readable_panels` precedent).

**Measured holders** (36 active non-admin users):
- **Sales family** (any of 34, 35, 36, 40, 41, 47, 51, 52, 53, 55, 61, 62, 63): 14 users get in. Two hold all 13. Three hold only one: 34, or 47 (two users).
- **Cost** (37, 38, 39): 3 users. One holds only 39, so they newly see margin by customer and salesperson too. That is accepted: margin is one fact, and the old per-axis split doesn't survive a free query.
- **Payments** (33, 109): 13 and 3 users.
- **Bulk exports** (113): no current holder. Admins can grant it; until then only administrators get the legacy-style bulk CSVs, which is acceptable because no one uses them today.
- **Drill-down to credit notes** needs read on fiscal documents (23), as the existing `/fiscal-documents` routes do.
- **Lost access:** nobody. Gaining users see more axes of the same data, which is the point of the design.

## R12. Exports

**Decision**:
- **XLSX:** XlsxWriter (new dependency, BSD) in `constant_memory` mode, which streams row by row and supports typed cells, number formats, freeze panes, autofilter and a cover sheet. Column widths come from the longest formatted value seen per column, capped at 60.
- **PDF:** the existing WeasyPrint pipeline. A new `analytics_report.html` template sets a named `@page analytics` with landscape when there are more than eight columns, `Página n de m` page numbers and `thead { display: table-header-group }` so headers repeat. Charts are inline `<svg>` built by Jinja macros (line, bar, stacked bar, heatmap) from the same series the JSON carries. No chart library is used.
- **CSV:** the standard library `csv` with a UTF-8 BOM, written as a `StreamingResponse`.
- **Background:** results over 10,000 rows (XLSX and CSV) run as an in-process `asyncio` task after a 202 response. PDFs always render inline; one over 2,000 rows is refused with `analytics.pdf_too_large` (FR-054). The task opens its session from the request session's bind, and the writers run in a worker thread. Their state is in a new `analytics_export` table (owner, definition, format, status, file name, row count, created, expires) and the files are in `settings.exports_dir`. Downloads check the owner. Expired files are deleted on access and by `uv run python -m app.jobs.purge_exports`, following the existing job pattern.

**Rationale**:
- XlsxWriter is the fastest pure-Python writer, about 100k rows × 10 columns in a few seconds in constant-memory mode. openpyxl's write-only mode is slower and has weaker formatting.
- WeasyPrint renders about 1 s per 200-line document (spec 019), so PDFs are capped at 2,000 rows, with a coded refusal suggesting XLSX. A larger table on paper serves nobody.
- A table instead of in-memory job state, because production runs several uvicorn workers (README: `--workers 4`) and a poll may reach a different worker. A task lost to a restart shows as `failed` after a stale timeout.

**Alternatives considered**:
- openpyxl: slower, see above.
- matplotlib or pygal for charts: a heavy dependency for four chart shapes.
- A job queue (RQ or Celery): new infrastructure, which the spec excludes.

## R13. Cache and ETag

**Decision**:
- A per-process TTL cache (60 s, LRU, 256 entries) keyed by a hash of the normalized request plus the caller's facility scope and cost visibility. It is never keyed by user. Two callers with the same scope and visibility share entries, so one caller can't see a response built for different privileges.
- Responses carry `ETag` (a hash of the body) and `Cache-Control: private, max-age=60`. A matching `If-None-Match` gets 304.
- No invalidation hook: with direct queries (R1) the TTL is the only staleness, and it is inside FR-020.

## R14. Alerts, from the data

- **Overdue credit orders:** 601 across 5 facilities today (201 due in the last 90 days). It is useful, and it is the spec 033 rule. It is as of today, whatever the period, and its drill-down uses the detail flag `overdue`, since a balance isn't a dimension. Its `open_credit_orders` cost wasn't measured in R9; implementation times it (quickstart §4).
- **Lines below cost:** 4,112 in 2025, about 6% of lines, so it would fire every period. It is shown as a count and amount for the period, not as a red alert, and it is cost-privileged.
- **Lines without a cost:** 12,397 in 2025 (17.5%). Same treatment, and it drives the cost coverage KPI.
- **Refund rate** (per facility; the baseline is the median of the monthly rates of the 12 calendar months before the period starts, whatever the period's length): a naive "2× the trailing-12-month mean" fires on 28 of 158 facility-months, 22 of them in 2024 when the baseline was ramping up. **Decision**: raise only when the period's rate exceeds both 2× the trailing-12-month median and 15%, with refunds of at least $20,000 in the period. Implementation re-runs the backtest over 2024–2026 (task) and records how many facility-months fire, with a target of at most 1 in 20. If not met, the thresholds are tuned there and recorded here.
  - **Backtest (T064, 2026-11-05, mbe_dev, `vat=excluded`, every facility-month of 2024-01 to 2026-10 with sales):** with 15% the alert fired on 11 of 169 facility-months (1 in 15), missing the target. Grid over the floor and the multiple (fired / 169): 15% ×2 → 11, ×2.5 → 9, ×3 → 7; **20% ×2 → 7**, ×2.5 → 6, ×3 → 5; 25% any → 3. **Tuned:** the floor is raised to 20%, keeping 2× the median and $20,000, the smallest change that meets 1 in 20 (7 of 169, 1 in 24). It fires on 2024-08 facility 51, 2025-06, 2025-11, 2026-04 and 2026-06 facility 47, 2025-10 and 2026-01 facility 52; facility 47's months each refunded $1.9M–$4.5M at rates of 20–67%.
- **Considered and not added**: 1,017 confirmed, unpaid orders with immediate terms exist. That is a receivables question for the next spec, noted rather than alerted.

## R15. Dimensions as measured

| Dimension | Notes |
|---|---|
| Label (the category) | 36 labels in use. Only 30% of products sold have one; the rest group under "none". |
| Brand | 555 distinct case-insensitively. The collation (`utf8mb3_unicode_ci`) already merges case and accents in GROUP BY. 1,266 products have none. |
| Model | 5,257 distinct, nearly one per product. Offered because legacy questions 16 and 20 ask for it, but the catalog marks it as a filter and a detail column rather than a useful grouping. |
| Customer type | Credit when `credit_limit > 0` and not the default customer (`is_credit_customer`). |
| Payment terms | From the order (IMMEDIATE 0, NET_D 1). |
| Salesperson | The order's for sales; the refund's `sales_person` for refunds (legacy question 13 nets refunds by the refunding salesperson); the cash session's employee for payments. |
| Facility | The document's own: the order's, the refund's (14 refunds since 2024 were taken at another facility), the payment's, the credit note's issuer facility. |
