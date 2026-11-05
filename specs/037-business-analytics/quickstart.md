# Quickstart: validating business analytics (spec 037)

How to prove the feature works. Shapes are in [contracts/api.md](contracts/api.md), definitions in [data-model.md](data-model.md), decisions in [research.md](research.md).

## 1. Local

```bash
cd /Users/eddy/Projects/mictlanix/mbe-api-037
uv sync
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

Expected: everything passes, including:
- **`tests/unit/test_analytics_facts.py`**: the SQL line cents with residuals equal `totals.document_totals` for fixture orders (with and without VAT, discounts, a 0% line, several lines rounding the same way). The MariaDB compile of every fact query has no dialect-only construct missing from SQLite's compile.
- **`tests/unit/test_analytics_catalog.py`**:
  - every measure and dimension in the catalog has a definition;
  - sensitive measures are exactly the cost ones;
  - every `MeasureKey` × `DimensionKey` pair is either supported or refused;
  - `test_contract_docs` passes with the new blocks.
- **`tests/integration/test_analytics_query.py`** (SQLite):
  - rows add up to totals for every additive measure, with top N and "others";
  - comparisons and % change are correct, with null against zero;
  - a refund nets on its own date and facility;
  - a cancelled-after-confirmation order disappears;
  - lines without a cost are left out of margin and lower coverage;
  - label rows report `overlapping`;
  - a payment measure grouped by product is refused.
- **`tests/api/test_analytics.py`**:
  - 401 without a token;
  - 403 `auth.forbidden` without a family;
  - 403 for sensitive measures without 37–39;
  - `facility.other_facility` without 101;
  - no `cost` or `margin` key anywhere in any response for a caller without the cost privilege (SC-005);
  - 422 for each `analytics.*` code.
- **`tests/api/test_analytics_exports.py`**:
  - XLSX cells are typed (read back with openpyxl in the test only), with a frozen header and autofilter;
  - CSV starts with the BOM;
  - PDF renders;
  - every value equals the JSON response (SC-004);
  - more than 10,000 rows gives 202, then ready, then a download; another user gets 404; after expiry, 410.

## 2. mbe_dev (read only until migration 031 is applied)

Before applying, record the EXPLAIN of each fact query for 2025 (expected `type ALL` on `sales_order` and `customer_payment`).

Apply the migration on mbe_dev only after asking: `uv run python -m app.db.migrate`, which needs the user's confirmation since it writes. Then check:

| Check | Expected |
|---|---|
| `SELECT COUNT(*) FROM sales_order WHERE completed = 1 AND confirmation_time IS NULL` | 0 |
| EXPLAIN of each fact query for October 2025 | `range` on the new date indexes |
| Confirm a draft on dev | its `confirmation_time` is within seconds of now, and it shows in `POST /analytics/query` within 60 s (SC-006) |

## 3. Reconciliation on mbe_dev (SC-001)

The branch is deployed to dev :8765. Sample 10 business days across 2025–2026, including a day with refunds and a day with a cancellation after confirmation:

- **Net sales:** `POST /analytics/query` with `net_sales`, `gross_sales`, `refunds` by `date` (day), `vat=included`. Compare with the sum of `total` from `GET /sales-orders?status=completed` for the orders confirmed that day (paged), minus that day's confirmed refunds' totals. Expected: equal to the cent.
- **Collected:** for each cash session closed on those days, `POST /analytics/query` with `collected` and `refunds_paid_out` + `store_credit_issued` by `payment_method`, filtered by `cash_session`. Compare with the session's cut (`cut_figures`, as shown by `GET /cash-sessions/{id}` and its ticket). Expected: `collected` = `sales_by_method`, and refunds paid out + store credit issued = `refunds_by_method`, per method, to the cent.
- **Drill-down (SC-007):** for 10 rows of a query by product and by customer, `POST /analytics/detail` with the row's `drill`. Expected: for a base measure (gross sales → `lines`, refunds → `refunds`, collected → `payments`) `sums.amount` equals the row's value; for net sales, `lines` − `refunds` − `credit_notes` equals it.

## 4. Performance on mbe_dev (SC-003)

Run each 30 times warm and take the p95:

| Request | Target |
|---|---|
| Dashboard, one month, all facilities | < 500 ms |
| Dashboard, 2025, all facilities | < 500 ms |
| Query, 12 months, all facilities, by product × month, every non-sensitive measure | < 2 s |
| CSV and XLSX export of 100,000 detail lines (2024–2026) | < 30 s each, as a background job |
| The overdue alert's `open_credit_orders` alone, all facilities | recorded (part of the dashboard's 500 ms) |

Record the numbers in research.md (R9). If the year dashboard misses its target, that is R1's trigger for summary tables; stop and report before building them.

## 5. Legacy questions (SC-002)

For each row of the spec's legacy table marked Q, D or Detail, run the stated request on dev and confirm that it answers the question. Record the request and a one-line result per row in the verifications.

## 6. Hand-off to mbe-ui

After merge, open an mbe-ui issue that links `contracts/api.md`. It lists the measure and dimension keys for the Spanish labels, the tiles and charts, and the new `analytics.*` codes for the Spanish error catalog (mictlanix/mbe-ui#232).
