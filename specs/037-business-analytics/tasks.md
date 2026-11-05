---
description: "Task list for spec 037, business analytics"
---

# Tasks: Business analytics (sales, margin and payments)

**Input**: Design documents from `specs/037-business-analytics/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R15), data-model.md, contracts/api.md, quickstart.md

**Tests**: Requested. The plan says tests come first in each task group (constitution, Testing), and quickstart §1 names the test files. Each test task is written to fail before the code task that follows it.

**Organization**: Tasks are grouped by user story, so each story can be built and tested on its own.

**Working copy**: the worktree `/Users/eddy/Projects/mictlanix/mbe-api-037`, on branch `037-business-analytics`. Every path below is relative to it.

**Gates for every task**: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q` both pass before the task is checked off. A test task is committed together with the code task that makes it pass (constitution, Testing).

**One known red test**: `tests/unit/test_contract_docs.py::test_a_documented_component_exists` fails for each 037 component in contracts/api.md until a route that uses its schema exists, since only then is it in OpenAPI (9 failures before T020; the last ones clear at T051, the export routes). Likewise `tests/unit/test_error_codes.py::test_every_code_is_asserted_by_some_test` fails from T008 until every new `analytics.*` code is asserted by a test (T023 covers four, T043 the other four). Those named cases are the only failures allowed at a gate, and T057 confirms none remain.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1–US6)

---

## Phase 1: Setup

**Purpose**: Dependencies and configuration.

- [X] T001 Add `XlsxWriter` (current 3.2.x) to `[project].dependencies` and `openpyxl` to the dev dependency group in `pyproject.toml`, then run `uv sync` so `uv.lock` updates.
- [X] T002 [P] Add `exports_dir: str = 'exports'` to `Settings` in `app/core/config.py`, next to the other path settings. Add `exports/` to `.gitignore`.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The confirmation moment, the indexes, the error codes, the request enums and the dialect functions that every story uses.

**⚠️ No user story work can begin until this phase is complete.**

### Tests

- [X] T003 [P] Write `tests/integration/test_sales_order_confirmation_time.py`:
  - confirming a draft through `POST /sales-orders/{id}/confirm` sets `confirmation_time` to the frozen `local_now()`;
  - cancelling it afterwards leaves `confirmation_time` unchanged;
  - a draft has `confirmation_time` null;
  - `GET /sales-orders/{id}` returns `confirmation_time`.

  Follow the fixtures of the existing sales order integration tests.
- [X] T004 [P] Write `tests/unit/test_analytics_sql.py`:
  - `period_start` for day, week (Monday), month, quarter and year; `weekday` (Monday = 1); `hour_of`. Evaluate each on an in-memory SQLite engine for fixed datetimes, including a Sunday, the 1st of a quarter and 23:59.
  - Compile each with `sqlalchemy.dialects.mysql.dialect()` and assert the MariaDB SQL (`DATE`, `DATE_FORMAT`, `WEEKDAY`, `QUARTER`, `HOUR`), as `tests/unit/test_cash_session_service.py` does.

### Implementation

- [X] T005 Write `migrations/031_business_analytics.sql` (data-model.md "Schema changes"), in the style of `migrations/029_supplier_payments.sql`:
  - `ALTER TABLE sales_order ADD COLUMN confirmation_time DATETIME NULL` after `date`;
  - `UPDATE sales_order SET confirmation_time = date WHERE completed = 1` (no fallback, R2);
  - indexes `sales_order_confirmation_time_idx`, `customer_payment_date_idx`, `customer_refund_date_idx`, `fiscal_document_type_issued_idx (type, issued)`;
  - `CREATE TABLE analytics_export` with the columns, FK to `user(user_id)` and engine/charset of the other tables.

  Write `migrations/031_business_analytics_rollback.sql`, which drops the table, the four indexes and the column. **Do not apply it to mbe_dev** (that is T061 and needs the user's confirmation).
- [X] T006 Add `confirmation_time: Mapped[datetime | None]` to `SalesOrder` in `app/models/sales.py`, and `confirmation_time: LocalDateTime | None = None` to the sales order response in `app/schemas/sales_order.py`.
- [X] T007 In `_confirm_locked` (`app/services/sales_order_service.py`, where `order.completed = True` is set), set `order.confirmation_time = local_now()`. Change nothing else in the function. T003 passes.
- [X] T008 [P] Add the eight codes of contracts/api.md "Error codes" to `ErrorCode` in `app/core/error_codes.py`, with their statuses and params. Add `ExportStatus` to the enum registry used for `enum:` params, following how existing enum params are registered. Add the same rows under a new `## analytics` section of `specs/036-localizable-error-codes/contracts/error-codes.md`. Raise the pinned count from 243 to 251 in both places it is pinned: `tests/unit/test_contract_docs.py` (`len(_documented_codes())`) and `tests/api/test_openapi_problem.py` (`len(schemas['ErrorCode']['enum'])`). `tests/unit/test_error_codes.py`, `tests/api/test_openapi_problem.py` and the error-code cases of `tests/unit/test_contract_docs.py` pass.
- [X] T009 [P] Add `'analytics_export': 'analytics_export'` to `RESOURCE_NAMES` in `app/core/errors.py`.
- [X] T010 [P] Create `app/services/analytics/__init__.py` (empty) and `app/services/analytics/sql.py` with the `FunctionElement`s `period_start(grain, col)`, `weekday(col)` and `hour_of(col)`, each with a default (SQLite) and a `mysql` `@compiles` (R7). Add a helper `period_bounds(start, end) -> (datetime, datetime)` giving `>= start 00:00` and `< end + 1 day`. T004 passes.
- [X] T011 Create `app/schemas/analytics.py` with the enums used by every story: `MeasureKey` (the 19 keys of data-model.md), `DimensionKey` (`date`, `weekday`, `hour`, `facility`, `point_of_sale`, `salesperson`, `cash_session`, `customer`, `customer_type`, `product`, `brand`, `model`, `label`, `payment_terms`, `payment_method`), `Grain`, `Comparison`, `VatTreatment`, `DetailKind`, `DetailFlag` (`overdue`, `below_cost`, `without_cost`, `collected`, `paid_out`, `store_credit`), `ExportSource`, `ExportFormat`, `ExportStatus`. All are `StrEnum`. Request and response models are added by the stories.

**Checkpoint**: migration files exist, confirming records the moment, the codes are in the catalog, and the dialect functions work on both dialects.

---

## Phase 3: User Story 1 - One trusted definition of every sales number (Priority: P1) 🎯 MVP

**Goal**: The catalog of measures and dimensions, the four facts that compute them to the cent, and the catalog route plus a query route that answers a measure for a period (no grouping yet beyond date and facility).

**Independent test**: `GET /analytics/catalog`, then `POST /analytics/query` for net sales, refunds and collected on one day. They equal the sales order totals and cash cut for that day (SQLite fixtures here; mbe_dev in T062).

### Tests

- [X] T012 [P] [US1] Write `tests/unit/test_analytics_catalog.py`:
  - every `MeasureKey` and `DimensionKey` has a catalog entry with label, unit and facts;
  - the sensitive measures are exactly `costed_net_sales`, `cost_coverage`, `cost_of_sales`, `gross_margin`, `margin_pct`;
  - the non-additive ones are exactly `cost_coverage`, `margin_pct`, `average_ticket`;
  - every measure × dimension pair is either supported or refused per data-model.md's dimension table (payment measures refuse `product`, `brand`, `model`, `label`, `point_of_sale`, `payment_terms`; sale measures refuse `cash_session` and `payment_method`);
  - `visible_measures(cost=False)` leaves out every sensitive measure.
- [X] T013 [P] [US1] Write `tests/unit/test_analytics_facts.py`:
  - for fixture orders (prices with and without VAT, line discounts, a 0% VAT line, three lines whose half-cents round the same way), the sale-line fact's cents per order equal `app.services.totals.document_totals` for that order (subtotal, tax, total), and their sum over orders equals the sum of the totals;
  - the residual lands on the largest line, lowest id on ties;
  - cost without VAT is `qty × cost ÷ (1 + rate)` for tax-included lines (R4), and lines with `cost = 0` are flagged uncosted;
  - the refund-line fact takes cost from the linked sale line, scaled by the refunded quantity;
  - every fact query compiled with `sqlalchemy.dialects.mysql` contains the window functions and no SQLite-only construct.
- [X] T014 [P] [US1] Write `tests/integration/test_analytics_query.py` (US1 part), on SQLite with fixture documents:
  - net sales for a day = gross sales − refunds − credit notes, with and without VAT;
  - an order cancelled after confirmation contributes nothing;
  - a refund nets on its own date and facility, not the sale's;
  - a CFDI credit note (type 100, stamped, not cancelled) reduces net sales; a cancelled one doesn't;
  - lines without cost are left out of cost and margin and lower `cost_coverage`; with no costed line, `margin_pct` is null;
  - collected for a cash session equals `cut_figures(...).sales_by_method` for that session, and refunds paid out + store credit issued equal `refunds_by_method`;
  - a store-credit payment applied to another order is never counted in collected;
  - an empty period returns zero totals, no rows and no error.
- [X] T015 [P] [US1] Write `tests/api/test_analytics.py` (US1 part):
  - 401 without a token on both routes (T031, T037 and T043 add the same case for each route they introduce, so every route of this spec has one, constitution Testing);
  - 403 `auth.forbidden` for a user with neither the sales nor the payments family;
  - the catalog for a user without 37–39 lists no sensitive measure;
  - a query naming `gross_margin` without 37–39 is refused with `auth.forbidden` whose `required` lists 37, 38, 39;
  - a payments-only user can query `collected` but is refused `net_sales`;
  - every response has `as_of`.

### Implementation

- [X] T016 [US1] Write `app/services/analytics/catalog.py`: `Measure` and `Dimension` frozen dataclasses with the fields of data-model.md "Catalog"; the 19 measures and 15 dimensions; each dimension's column per fact (or "none", or refused); `FAMILY_PRIVILEGES` (sales, cost, payments, bulk) from contracts/api.md; helpers `visible_measures(cost: bool, sales: bool, payments: bool)`, `applicable(measure, dimension)`, `facts_for(measures)`. Every dimension, `model` included, can be grouped and filtered. T012 passes.
- [X] T017 [US1] Write `app/services/analytics/facts.py`, the four fact queries (data-model.md "Facts", R3–R6). Each is a function `(dimensions, filters, period, scope) -> Select` returning one grouped `SELECT` of integer-cent sums plus counts:
  - `sale_line`: lines of `sales_order` with `completed = 1 AND cancelled = 0`, dated by `confirmation_time`. Per-line subtotal, tax and discount cents mirror `totals.line_amounts`, rounded with `ROUND(..., 2) * 100`. The order residual is added to the line with `ROW_NUMBER() OVER (PARTITION BY sales_order ORDER BY amount DESC, id) = 1`. Also cost-without-VAT cents, costed-subtotal cents, quantity, `COUNT(DISTINCT sales_order)`, and counts of uncosted and below-cost lines.
  - `refund_line`: `customer_refund_detail ⋈ customer_refund` (confirmed, not cancelled, dated by `customer_refund.date`), with the same residual rule per refund. Cost comes from `sales_order_detail` through `customer_refund_detail.sales_order_detail`; the salesperson is the refund's `sales_person`.
  - `credit_note`: `fiscal_document(_detail)` with `type = 100`, stamped, not cancelled, dated by `issued`.
  - `payment`: `customer_payment` dated by `date`, classified as R6: collected = `amount − SUM(live sales_order_payment.amount_change)` for `payment_type != CREDIT_NOTE AND amount > 0`; refunds paid out = `-amount` for `amount < 0`; store credit = `amount` for `payment_type = CREDIT_NOTE AND amount > 0`. Payments count only collected ones. Read `cut_figures` in `app/services/cash_session_service.py` and mirror its predicates exactly.

  Dimensions map to columns through the catalog. Missing values group as `NULL` ("none"). Period filters use `sql.period_bounds`. Facility scope applies to each fact's facility column. T013 passes.
- [X] T018 [US1] Write `app/services/analytics/engine.py`, part 1:
  - `resolve_scope(db, current, facility)`: administrators and holders of 101 (`can_see_every_facility(db, current, 101)`) get the requested facility or all; anyone else gets their own, and another facility is refused with `facility.other_facility` (as specs 033–035 do).
  - `check_privileges(db, current, measures)`: `require_any_privilege` for each measure's family; a sensitive measure without 37–39 raises `auth.forbidden` with `required` [37, 38, 39].
  - `run(db, definition, current) -> QueryResult`: run the facts the measures need, merge them on the dimension tuple in Python (full outer join), convert cents to `Decimal` with two places, compute the derived measures (net sales, costed net sales, cost of sales, gross margin, units, and the ratios from their parts, null on zero denominators), and build the totals from an undimensioned run of the same facts (not by summing rows), so label overlap can't inflate them.
- [X] T019 [US1] In `app/schemas/analytics.py`, add `AnalyticsCatalogResponse`, `AnalyticsMeasure`, `AnalyticsDimension`, `AnalyticsQueryRequest`, `AnalyticsFilter`, `AnalyticsQueryResponse`, `AnalyticsRow`, `AnalyticsKey` exactly as contracts/api.md's blocks (money as `Decimal` serialized as strings, ratios with four places).
- [X] T020 [US1] Create `app/api/v1/endpoints/analytics.py` with `GET /analytics/catalog` (sales or payments family; filters by `has_any_privilege` for the cost family) and `POST /analytics/query` (calls `engine.run`). Include the router in `app/api/v1/router.py` under the `analytics` prefix and tag. T014 and T015 pass. `test_contract_docs` passes for `AnalyticsCatalogResponse`, `AnalyticsQueryRequest` and `AnalyticsQueryResponse` (and the blocks they reference).

**Checkpoint**: one definition of every measure answers by day and facility, reconciled on fixtures. This is the MVP.

---

## Phase 4: User Story 2 - Ask any sales question without a dedicated report (Priority: P1)

**Goal**: Every dimension, filters, comparisons, shares, sort, top N with "others", validation codes, and the cache.

**Independent test**: run the query for rows 2–21 and 31 of the legacy table on fixtures and get the stated answer; mbe_dev in T065.

### Tests

- [X] T021 [P] [US2] Write `tests/unit/test_analytics_engine.py`, on in-memory fact results (no database):
  - the merge of facts with disjoint keys;
  - ratios recomputed at every level, never averaged;
  - shares sum to 1 per additive measure;
  - top N plus "others" adds up to the totals, with ties broken by key;
  - comparison, change and `change_pct` (null against 0);
  - date rows align by position against the comparison period;
  - the comparison period for `previous_period` (same length, ending the day before start) and `same_period_last_year` (Feb 29 → Feb 28).
- [X] T022 [P] [US2] Extend `tests/integration/test_analytics_query.py`:
  - rows add up to totals for every additive measure, by product, customer, salesperson, brand, model, point of sale, payment terms and customer type; by salesperson × model (legacy row 20);
  - date at each grain; weekday × hour;
  - filters (in) on each entity dimension; brand groups case-insensitively and trimmed;
  - label rows report `overlapping: true` and the totals are the true totals;
  - "none" rows for a product without brand and an order without salesperson;
  - credit notes fall under "none" when grouped by product;
  - top 3 + others; sort by a measure descending and by a dimension.
- [X] T023 [P] [US2] Extend `tests/api/test_analytics.py`, one test per code:
  - `analytics.too_many_dimensions` (4 dimensions);
  - `analytics.dimension_not_applicable` (`collected` by `product`);
  - `analytics.period_invalid` (end before start; over five years);
  - `analytics.sort_invalid` (sort on an unrequested key);
  - an unknown measure key gives FastAPI's 422 with type `enum`;
  - `facility.other_facility` for a caller without 101 who names another facility, either as the `facility` parameter or as a `facility` filter value; the own facility for one who omits it;
  - with the cost privilege, `margin_pct` is returned; without it, the response has no key containing `cost` or `margin` anywhere.
- [X] T024 [P] [US2] Write `tests/unit/test_analytics_cache.py`:
  - a hit within 3 minutes; a miss after (frozen clock);
  - LRU eviction past 256 entries;
  - different scope or cost visibility never share an entry;
  - the ETag is stable for the same body, and `If-None-Match` gives 304 at the route.

### Implementation

- [X] T025 [US2] In `app/services/analytics/facts.py`, map every remaining dimension per data-model.md's table:
  - `point_of_sale` and `payment_terms` from the order (for refunds, through the refunded order);
  - `customer_type` with the `is_credit_customer` rule (`credit_limit > 0` and not the default customer) as a SQL `CASE`;
  - `brand` and `model` as `TRIM` values (the collation merges case);
  - `label` through `product_label` (sale and refund lines only);
  - `weekday` and `hour` through `sql.weekday` and `sql.hour_of`;
  - `date` through `sql.period_start(grain, ...)`.
- [X] T026 [US2] In `app/services/analytics/engine.py`, add:
  - validation that raises the four `analytics.*` request codes with their params (too many dimensions, a measure not applicable to a dimension or filter, period, sort);
  - filters (`in` on each dimension; `values` parsed per dimension type). A `facility` filter naming a facility outside the caller's scope raises `facility.other_facility`, like the `facility` parameter (FR-061);
  - the comparison run over the comparison period;
  - share, change and `change_pct` per row and on the totals;
  - sort (default: first dimension, date ascending; with `top`, first measure descending);
  - top N with "others" computed as totals minus the top rows for additive measures, ratios from their parts;
  - each row's `drill` (the filters that reproduce it);
  - `definition` normalized in the response (defaults filled, facility resolved);
  - `overlapping` when `label` is grouped.

  T021–T023 pass.
- [X] T027 [US2] Write `app/services/analytics/cache.py`: a TTL (3 minutes; 60 s until T063) LRU (256) keyed by a SHA-256 of the normalized definition JSON plus the resolved facility scope and the cost and family visibility; `etag(body_bytes)`. Use it in the query route, which sets `ETag` and `Cache-Control: private, max-age` of the TTL and answers 304 on a matching `If-None-Match`. T024 passes.

**Checkpoint**: any legacy sales question is one query.

---

## Phase 5: User Story 3 - A glanceable dashboard in one call (Priority: P1)

**Goal**: `GET /analytics/dashboard` with KPIs, sparklines, series and alerts, privilege-trimmed.

**Independent test**: each KPI equals the query's answer for the same measure, period and scope, and a caller without the cost privilege gets no margin tile.

### Tests

- [X] T028 [P] [US3] Write `tests/integration/test_analytics_dashboard.py`:
  - each KPI value equals `engine.run` for that measure, period and scope;
  - the sparkline grain is day up to 62 days, week up to a year, then month;
  - `sales_over_time` carries the comparison series;
  - top products and customers are top 10 + others;
  - the heatmap is orders by weekday × hour;
  - `collected_today` uses today's date even when the period is longer.
- [X] T029 [P] [US3] Write the alert tests in `tests/integration/test_analytics_dashboard.py`:
  - overdue credit orders equals `open_credit_orders`'s overdue set in scope;
  - lines below cost and lines without cost count the fixture lines and appear only with the cost privilege;
  - the refund-rate alert is one alert per facility that fires, with `facility` set and `count` and `amount` the facility's refunds in the period; it fires only when the period's rate is above 2× the facility's trailing 12-month median **and** above 20% **and** refunds ≥ $20,000, with a fixture month on each side of each threshold.
- [X] T030 [P] [US3] Write `tests/unit/test_analytics_dashboard.py`, on in-memory figures (no database), for the dashboard's own rules (constitution, Testing):
  - `sparkline_grain(start, end)`: day at 62 days, week at 63 days and at 366, month at 367;
  - `refund_rate_alert(period_rate, refunds, monthly_rates)`: the median of the 12 calendar months before the period's start; fires only when the rate is above 2× that median **and** above 20% **and** refunds are at least $20,000; one case just under each threshold; fewer than 12 prior months use the months available; no prior month means no alert;
  - KPI change and `change_pct` against a zero comparison (null).
- [X] T031 [P] [US3] Extend `tests/api/test_analytics.py`:
  - 401 without a token on `GET /analytics/dashboard`;
  - a sales-only caller gets no payment tiles;
  - a payments-only caller gets no sales tiles;
  - a caller without 37–39 gets no `margin_pct`, `cost_coverage` or cost alerts (the keys are absent, not zero);
  - the dashboard and an export of it share the request shape.

### Implementation

- [X] T032 [US3] Add `AnalyticsDashboardRequest`, `AnalyticsDashboardResponse`, `AnalyticsKpi`, `AnalyticsPoint`, `AnalyticsSeries` and `AnalyticsAlert` to `app/schemas/analytics.py`, as contracts/api.md.
- [X] T033 [US3] Write `app/services/analytics/dashboard.py`:
  - run R9's eight fact queries concurrently with `asyncio.gather`, skipping the ones the caller can't see. Each runs on its own session from `async_sessionmaker(db.bind, expire_on_commit=False)`, built from the request's session, **not** `AsyncSessionLocal`: tests swap the database only by overriding `get_db` (`tests/integration/conftest.py`), so a session from the global factory would miss the SQLite test database. On SQLite, where one connection can't serve concurrent queries, fall back to running them in sequence on `db`;
  - run the comparison period's day × facility queries the same way;
  - derive every KPI, sparkline and series from those results through the engine's merge and derive functions, so the numbers equal `engine.run` (FR-031). Put the pure rules (`sparkline_grain`, `refund_rate_alert`) in module-level functions that T030 tests directly;
  - each KPI's `drill` is the `AnalyticsQueryRequest` that reproduces it, with `vat` set explicitly: `excluded` for every tile, `included` for average ticket (contracts/api.md "Tiles").
- [X] T034 [US3] Add the alerts to `app/services/analytics/dashboard.py` (R14):
  - overdue credit orders through `open_credit_orders(db, facility=..., due_before=local_now())` in `app/services/sales_order_service.py` (spec 033), with count and the sum of balances. It is as of today, whatever the dashboard period;
  - lines below cost and lines without cost from the sale-line fact's counts (cost privilege only), each with `amount` = the sum of those lines' sales amounts without VAT, which is `sums.amount` of its drill-down;
  - refund rate per facility: the period's refunds ÷ gross sales, against the median of the monthly rates of the 12 calendar months before the period's start, from one grouped query by month × facility (FR-032). The $20,000 floor applies to the period's refunds whatever its length.

  Each alert's `detail` is the `AnalyticsDetailRequest` that lists exactly its documents, with `vat=excluded` set explicitly: `kind=orders, flag=overdue` for overdue orders; `kind=lines, flag=below_cost` and `kind=lines, flag=without_cost` for the cost alerts; `kind=refunds` filtered by the facility for the refund rate, which yields one alert per facility that fires, with `facility` set and `count` and `amount` the facility's refunds in the period (contracts/api.md "Tiles").
- [X] T035 [US3] Add `GET /analytics/dashboard` to `app/api/v1/endpoints/analytics.py` (sales or payments family; `comparison` defaults to `previous_period`), cached and ETagged like the query. T028–T031 pass.

**Checkpoint**: the dashboard is one call, and its numbers equal the query's.

---

## Phase 6: User Story 4 - From any number to the documents behind it (Priority: P2)

**Goal**: `POST /analytics/detail` returns the documents behind an aggregate, paged, with sums equal to the aggregate.

**Independent test**: follow a row's `drill` to the detail; the figure that contracts/api.md's table names for the measure equals the row's value.

### Tests

- [X] T036 [P] [US4] Write `tests/integration/test_analytics_detail.py`:
  - for each kind (orders, lines, refunds, credit_notes, payments), `sums` equals the matching query value for the same filters, across pages;
  - lines carry the residual, so line amounts sum to the order totals;
  - sorting by date, amount and document, both directions; paging with `skip` and `limit`;
  - each item's `document_id` identifies the order, refund, fiscal document or payment;
  - `flag=overdue` on orders lists exactly `open_credit_orders`'s past-due set in scope, with `amount` = balance, ignoring the period; `flag=below_cost` and `flag=without_cost` on lines list exactly the lines the sale-line fact counts;
  - for every row of contracts/api.md's "Which detail figure equals which measure" table, the stated figure equals the query's value for the same filters; net sales equal `lines` − `refunds` − `credit_notes` on `sums.amount`, and units, cost of sales and gross margin equal `lines` − `refunds` on their sums (SC-007);
  - every dashboard alert's `detail` returns exactly `count` items, and `sums.amount` equals the alert's `amount` (US3 scenario 3).
- [X] T037 [P] [US4] Extend `tests/api/test_analytics.py`:
  - 401 without a token on `POST /analytics/detail`;
  - detail needs the family plus read on the kind (7 sales orders for orders and lines, 8 customer payments, 22 refunds, 23 fiscal documents for credit notes);
  - without 37–39, items and `sums` have no `cost` or `margin` key, while with 37–39 an uncosted line has `cost: null` (the key is present);
  - `flag=below_cost` or `without_cost` without 37–39 is refused with `auth.forbidden`;
  - a flag on a kind it doesn't apply to (`overdue` on lines, `below_cost` on payments, `collected` on orders) gives 422;
  - another facility without 101 is refused;
  - `limit` above 100 gives 422.
- [X] T038 [P] [US4] Write `tests/unit/test_analytics_detail.py`, without a database (constitution, Testing):
  - sort parsing for `date`, `amount`, `document` and their `-` forms, and the default `-date`;
  - `serialize_items(items, cost_visible)` drops the `cost` and `margin` keys when not visible and keeps a null when visible;
  - the `below_cost` and `without_cost` predicates compiled for MariaDB match the fact's own counting expressions.

### Implementation

- [X] T039 [US4] Add `AnalyticsDetailRequest` (with `flag: DetailFlag | None` and a model validator that accepts `overdue` only on `orders`, `below_cost`/`without_cost` only on `lines`, and `collected`/`paid_out`/`store_credit` only on `payments`), `AnalyticsDetailResponse` and `AnalyticsDetailItem` to `app/schemas/analytics.py`, as contracts/api.md.
- [X] T040 [US4] Write `app/services/analytics/detail.py`:
  - per kind, the fact's row-level `SELECT` (the same line cents and residual expressions as `facts.py`, imported, not copied), with the same filters, scope and VAT treatment;
  - a count query, and sums over all pages (`amount`, `quantity`, `discount`, and `cost`/`margin` for holders) from one aggregate of the same select; every item `amount` is positive, and payments carry their R6 class, and `flag=collected`, `paid_out` or `store_credit` keeps one class with the payment fact's own predicates (imported from `facts.py`);
  - the page itself joined to customer, employee and product names;
  - `flag=below_cost` and `without_cost` add the sale-line fact's own predicates (import them from `facts.py`) and need the cost privilege;
  - `flag=overdue` takes the ids and balances from `open_credit_orders(db, facility=..., due_before=local_now())`, ignores the period, and pages and sums them in Python (the balance rule can't be expressed in SQL, R9);
  - `serialize_items(items, cost_visible)`: callers without the cost privilege get items and `sums` dumped with `exclude={'cost', 'margin'}`, so the keys are absent. Holders keep `cost: null` on uncosted lines. Never use `exclude_none`, which would also drop meaningful nulls.
- [X] T041 [US4] Add `POST /analytics/detail` to `app/api/v1/endpoints/analytics.py`, returning the serialized dict from `serialize_items`, not the model, so the excluded keys stay out. T036, T037 and T038 pass.

**Checkpoint**: every number has its documents.

---

## Phase 7: User Story 5 - Exports that match the screen (Priority: P2)

**Goal**: XLSX, PDF and CSV of a query, the dashboard or a detail, direct up to 10,000 rows, in the background above.

**Independent test**: export one query in all three formats; every value equals the JSON response.

### Tests

- [X] T042 [P] [US5] Write `tests/api/test_analytics_exports.py`, direct exports:
  - parametrized over 20 fixed query definitions (SC-004) that cover every visible measure, each dimension at least once, a comparison, top N with others, a label grouping and both VAT treatments:
    - XLSX read back with openpyxl: a "Resumen" sheet with title, period, comparison, filters, VAT, `as_of`, user; a "Datos" sheet with numeric money cells formatted `#,##0.00`, date cells as dates, ratios as `0.0%`, a frozen first row, an autofilter and a totals row; every value equals the query JSON;
    - CSV starts with `﻿`, uses ISO dates and dot decimals, has a header of keys and a `TOTAL` row; every value equals the query JSON;
    - PDF starts with `%PDF`, and its text extracted with `pypdf` (already a dependency) contains every row's and the totals' formatted values;
  - an empty result exports the cover sheet, header and zero totals;
  - without 37–39, no export has a cost or margin column.
- [X] T043 [P] [US5] Extend `tests/api/test_analytics_exports.py`, refusals and jobs:
  - `analytics.export_source_invalid` for zero or two sources, and for a dashboard in XLSX;
  - `analytics.pdf_too_large` for a PDF over 2,000 rows (monkeypatch the cap low);
  - over the threshold (monkeypatched low) gives 202 with `status: pending`, then `ready`, then the file downloads;
  - 401 without a token on each of the three export routes;
  - an export id that doesn't exist, and another user's, both give `resource.not_found` (404);
  - a running one gives `analytics.export_not_ready` (409);
  - past `expiry_time`, `analytics.export_expired` (410) and the file is gone;
  - a RUNNING row older than 10 minutes reads as `failed`;
  - a bulk detail export (`lines` or `payments` over more than one day with no other filter) is refused with `auth.forbidden` (`required` lists 113) for a caller without 113, and passes for a holder of 113 and for an administrator; a one-day or filtered detail export doesn't need 113.
- [X] T044 [P] [US5] Write `tests/unit/test_analytics_exports.py`, without a database or route (constitution, Testing):
  - `export_mode(format, rows)`: inline at 10,000 rows, background at 10,001 for XLSX and CSV; a PDF is always inline and refused above 2,000;
  - `column_widths`: the longest formatted value per column, capped at 60;
  - the XLSX cell format per unit (money `#,##0.00`, quantity, count, ratio `0.0%`, date);
  - CSV value formatting: ISO dates, dot decimals, raw ratios, empty for null;
  - `is_bulk_detail(request)` per FR-055.

### Implementation

- [X] T045 [US5] Create `app/models/analytics.py` with `AnalyticsExport` (data-model.md table) and register it where the other models are imported, so SQLite test schemas include it.
- [X] T046 [US5] Add `AnalyticsExportRequest` and `AnalyticsExportResponse` to `app/schemas/analytics.py`, as contracts/api.md.
- [X] T047 [P] [US5] Write the CSV and XLSX writers in `app/services/analytics/exports.py`: they take the columns, rows and totals of a query or detail result and write them per contracts/api.md "File layout". XLSX uses `xlsxwriter.Workbook(path_or_buffer, {'constant_memory': True})`, writes "Resumen" first, sizes columns from the longest formatted value (capped at 60), and runs in a worker thread (`anyio.to_thread.run_sync`).
- [X] T048 [P] [US5] Write `app/rendering/templates/_charts.html`: Jinja macros `line_chart`, `bar_chart`, `stacked_bar_chart` and `heatmap` that draw inline `<svg>` from a series' points, with fixed sizes, axis labels and money formatting through the existing filters in `app/rendering/formatting.py`.
- [X] T049 [US5] Write `app/rendering/templates/analytics_report.html`, extending `_print_layout.html`: company header, title, period, filters, then KPIs and charts (dashboard) or the table (query, detail). Set a named `@page analytics` that is landscape when there are more than 8 columns, with `Página n de m` counters, and `thead { display: table-header-group }`. Add the PDF context builder to `app/services/analytics/exports.py`, which calls `render_pdf` from `app/rendering/__init__.py`. Refuse over 2,000 rows with `analytics.pdf_too_large`.
- [X] T050 [US5] Add the background path to `app/services/analytics/exports.py`:
  - count rows first; at most 10,000 (and every PDF) runs inline and returns the file;
  - otherwise insert an `analytics_export` row (PENDING) and start an `asyncio` task. The task opens its own session from `async_sessionmaker(db.bind, expire_on_commit=False)`, built from the request's session, not `AsyncSessionLocal`, so tests reach the SQLite database (see T033). It sets RUNNING, writes the file to `settings.exports_dir/<id>.<ext>`, then sets READY with `row_count`, `completion_time` and `expiry_time` = completion + 24 h (FAILED on error);
  - **both** writers (XLSX and CSV) and every file delete run in `anyio.to_thread.run_sync`, never in the event loop (constitution VI);
  - keep the task in a module-level set and expose `await exports.drain()` so tests can wait for it;
  - put `export_mode`, `column_widths`, the cell formats and `is_bulk_detail` in module-level functions that T044 tests directly;
  - `read_export(db, id, current)` returns `resource.not_found` for another user's row, shows RUNNING older than 10 minutes as FAILED, deletes the file and answers `analytics.export_expired` after expiry.
- [X] T051 [US5] Add `POST /analytics/exports`, `GET /analytics/exports/{analytics_export_id}` and `GET /analytics/exports/{analytics_export_id}/file` to `app/api/v1/endpoints/analytics.py`. The POST checks the source's own privileges (and 113 for bulk detail kinds, FR-055/FR-060) and answers 200 with `Content-Disposition: attachment` or 202 with the job. T042, T043 and T044 pass.
- [X] T052 [P] [US5] First write `tests/integration/test_purge_exports.py`: the job removes an expired row and its file and keeps an unexpired one; it fails. Then write `app/jobs/purge_exports.py`, following `app/jobs/expire_orders.py`: delete the files and rows of exports past `expiry_time`, and print the count. The test passes.

**Checkpoint**: every view exports in three formats, with the same numbers.

---

## Phase 8: User Story 6 - Payments received (Priority: P2)

**Goal**: Payment measures by method, cash session, facility and cashier for a period, reconciled with the cash cuts.

**Independent test**: collected by method and facility for a period equals the sum of that period's cash cuts.

### Tests

- [X] T053 [P] [US6] Extend `tests/integration/test_analytics_query.py`:
  - for two closed sessions in a period, collected by `payment_method` equals the sum of `cut_figures(...).sales_by_method`;
  - refunds paid out + store credit issued equal `refunds_by_method`, with store credit under method N/A;
  - a refund paid in cash shows in refunds paid out, never as negative collected;
  - grouped by `salesperson`, payments use the cash session's cashier;
  - `cash_session` as a filter limits to one session.
- [X] T054 [P] [US6] Extend `tests/api/test_analytics.py`: a holder of only 109 or only 33 can query payment measures; `collected` by `label` is refused with `analytics.dimension_not_applicable`.

### Implementation

- [X] T055 [US6] In `app/services/analytics/facts.py`, map the payment-only dimensions: `cash_session`, `payment_method`, and `salesperson` as the session's employee (`cash_session.cashier`). A payment without a session groups under "none". T053 and T054 pass.

**Checkpoint**: the received-payments reports are answered by the query.

---

## Phase 9: Polish and cross-cutting concerns

- [X] T056 [P] Write the SC-005 sweep in `tests/api/test_analytics.py`: for a caller with every family except 37–39, call every route (catalog, query with every visible measure, dashboard, each detail kind, each export format) and assert that no JSON key, CSV header or XLSX header contains `cost`, `margin`, `costed` or `coverage`.
- [X] T057 [P] Confirm `tests/unit/test_contract_docs.py` passes with every block of `specs/037-business-analytics/contracts/api.md`. Fix the doc or the schema where they differ, never by weakening the test.
- [X] T058 [P] Add the spec to `CHANGELOG.md` under `[Unreleased]`: Added (the analytics routes, `confirmation_time` on sales orders, the eight `analytics.*` codes, XlsxWriter), and Docs (spec 037).
- [X] T059 Run `uv run ruff check app/ migrations/ tests/` and the full `uv run pytest -q`. Fix any failure.

### On mbe_dev (quickstart §2–§5)

Deploying the branch to dev :8765 is pre-authorized. Applying migration 031 writes to mbe_dev, so **T061 needs the user's confirmation before it runs**.

- [X] T060 Before the migration, on mbe_dev (read only):
  - record the EXPLAIN of each fact query for 2025 in research.md R10 (expected: `type ALL` on `sales_order` and `customer_payment`);
  - count the documents whose `currency` isn't MXN in `sales_order`, `customer_refund`, `customer_payment` and `fiscal_document`, and record the counts (expected 0, FR-003). If any exist, stop and report.
- [X] T061 **Ask the user**, then apply migration 031 on mbe_dev with `uv run python -m app.db.migrate`. Check that no confirmed order lacks `confirmation_time`, that EXPLAIN now shows `range` on the new indexes, and that confirming a draft sets the moment. Record the results in the verifications.
- [X] T062 Deploy the branch to dev :8765 (as mictlanix@, tmux). Run quickstart §3, reconciliation (SC-001 and SC-007): 10 business days (one with refunds, one with a cancellation after confirmation), the cash sessions of those days, and 10 drill-downs (base measures against their kind, net sales as `lines` − `refunds` − `credit_notes`). Also confirm a draft and check it shows in the query within five minutes (SC-006). Record each comparison in the verifications.
- [X] T063 Run quickstart §4, performance (SC-003): 30 warm runs each of the month and year dashboards, the 12-month product × month query, and the 100,000-line CSV and XLSX exports. Also time the overdue alert's `open_credit_orders` call alone, across all facilities, because its Python balance computation wasn't in R9's measurements. Record the p95s in research.md R9. If the year dashboard misses 500 ms, **stop and report** before building summary tables (R1). If the overdue alert is the cause, report that too: it can be cached on its own, since it doesn't depend on the period. The dashboard's concurrent sessions run only on MariaDB (SQLite tests use the sequential fallback), so also check the dev log shows no session or connection errors over the 30 runs.
- [X] T064 Backtest the refund-rate alert over 2024–2026 on mbe_dev (read only): count the facility-months it fires on with the R14 thresholds, against the target of at most 1 in 20. Record the count, and any tuned threshold, in research.md R14.
- [ ] T065 Run quickstart §5 (SC-002): for each legacy row marked Q, D or Detail, run the stated request on dev and record the request and a one-line result in the verifications.
- [ ] T066 Open the PR from `037-business-analytics`. Under "Roadmap": `Closes mictlanix/mbe-roadmap#88`, `#90`, `#92`, `#98`, `#104`, and `Part of mictlanix/mbe-roadmap#9`. End the body with "🤖 Generated with [Claude Code](https://claude.com/claude-code)".
- [ ] T067 After merge, open the mbe-ui issue of quickstart §6 (linking contracts/api.md, the measure and dimension keys, the tiles and the eight codes). Then record the verifications, mark the spec complete, commit `.spec-context.json` to main, delete the branch locally and remotely, and remove the worktree.

---

## Dependencies and execution order

### Phases

- **Setup (1)**: none.
- **Foundational (2)**: after Setup. Blocks every story.
- **US1 (3)**: after Foundational. It builds the catalog, facts, engine core and the first two routes, so every later story depends on it.
- **US2 (4)**: after US1.
- **US3 (5)**: after US2 (the dashboard reuses dimensions, comparison and top N).
- **US4 (6)**: after US2. Its alert tests (T036) also need US3's alerts (T035).
- **US5 (7)**: after US2 for query exports, US3 for dashboard PDFs and US4 for detail exports. The writers (T047, T048) can start right after US1.
- **US6 (8)**: after US1. Independent of US2–US5, so it can run alongside them.
- **Polish (9)**: after every story. T061 waits for the user.

### Inside each story

Tests first, and they fail; then schemas, then services, then routes. `facts.py`, `engine.py` and `app/schemas/analytics.py` are shared files, so tasks that touch them run one at a time.

### Parallel opportunities

- **Phase 2**: T003, T004 together; then T008, T009, T010 together after T005–T007.
- **US1**: T012–T015 (four test files) together.
- **US2**: T021–T024 together.
- **US3**: T028–T031 together (T028 and T029 share a file: write them in one sitting).
- **US4**: T036–T038 together.
- **US5**: T042–T044 together (T042 and T043 share a file: write them in one sitting); T047, T048, T052 together.
- **US6** can run alongside US2–US5 except where it touches `facts.py` (T055 after T025).
- **Polish**: T056, T057, T058 together.

### Example: US1 tests in parallel

```text
Task: "Write tests/unit/test_analytics_catalog.py (T012)"
Task: "Write tests/unit/test_analytics_facts.py (T013)"
Task: "Write tests/integration/test_analytics_query.py, US1 part (T014)"
Task: "Write tests/api/test_analytics.py, US1 part (T015)"
```

---

## Implementation strategy

### MVP (US1)

1. Phases 1 and 2.
2. US1: catalog, facts, engine core, catalog and query routes.
3. **Stop and validate**: net sales and collected for a day reconcile on fixtures. The cent-exact facts are the riskiest part of the spec, so they are proven before anything is built on them.

### Incremental delivery

1. US1, the trusted definitions (MVP).
2. US2, any question as a query: the legacy sales reports are answerable.
3. US3, the dashboard: the visible deliverable for mbe-ui.
4. US6, payments: small once the payment fact exists (it can come earlier).
5. US4, drill-down; then US5, exports, which reuse every earlier piece.
6. Polish and the mbe_dev checks, which decide whether R1's summary tables are ever needed.
