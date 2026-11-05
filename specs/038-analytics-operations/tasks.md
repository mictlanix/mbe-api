---
description: "Task list for spec 038, operations analytics"
---

# Tasks: Operations analytics (inventory, receivables, commissions, fiscal, suppliers) with saved views and scheduled reports

**Input**: Design documents from `specs/038-analytics-operations/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R20), data-model.md, contracts/api.md, quickstart.md. Spec 037's cost fix (#296) is on main.

**Tests**: Requested. The plan says tests come first in each task group (constitution, Testing), and quickstart §1 names the cases. Each test task is written to fail before the code task that follows it.

**Organization**: Tasks are grouped by user story, so each story can be built and tested on its own. Phases follow priority: P1 (US1, US2), then P2 (US3, US4, US5, US7), then P3 (US6, US8).

**Working copy**: the worktree `/Users/eddy/Projects/mictlanix/mbe-api-038`, on branch `038-analytics-operations`. Every path below is relative to it.

**Gates for every task**: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q` both pass before the task is checked off. A test task is committed together with the code task that makes it pass.

**Known red tests** (the only failures allowed at a gate):
- `tests/unit/test_contract_docs.py::test_a_documented_component_exists` fails for each 038 block of contracts/api.md until a route using that schema exists. The last ones clear at T064, the schedule routes.
- `tests/unit/test_contract_docs.py::test_documented_fields_match_the_component` fails for the blocks that extend an existing schema, until the schema gains the fields:
  - `AnalyticsMeasure` (both specs' blocks) and `AnalyticsQueryRequest` clear at T008;
  - `AnalyticsDetailItem` (both specs' blocks) clears at T020.

  Measured on the branch before implementation: 14 failures in `test_contract_docs`, all of them these.
- `tests/unit/test_error_codes.py::test_every_code_is_asserted_by_some_test` fails from T006 until every new `analytics.*` code is asserted by a test. The last one, `analytics.attachment_too_large`, is asserted at T060.

T077 confirms that none remain.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story the task belongs to (US1–US8)

---

## Phase 1: Setup

**Purpose**: Settings and the migration files.

- [X] T001 [P] Add these settings to `Settings` in `app/core/config.py`, next to `exports_dir` (R3, R4, R5, R15):
  - `stock_history_start: date = date(2025, 2, 7)`;
  - `placeholder_cost: Decimal = Decimal('99999')`;
  - `restock_lead_days: int = 7`;
  - `restock_cover_days: int = 30`;
  - `scheduled_reports_enabled: bool = False`;
  - `report_attachment_max_bytes: int = 10 * 1024 * 1024`.

  Document `SCHEDULED_REPORTS_ENABLED` in `.env.example` if that file lists settings.
- [X] T002 [P] Write `migrations/032_operations_analytics.sql` (data-model.md "New tables", R19), in the style of `migrations/031_business_analytics.sql`:
  - the index `lot_serial_tracking_date_idx` on `lot_serial_tracking(date)`;
  - the tables `receivable_snapshot` (PK `(month_end, sales_order)`, indexes `(month_end, customer)` and `(month_end, facility)`) and `receivable_snapshot_month`;
  - `analytics_view`, with a unique `(user, name)`;
  - `analytics_view_share`;
  - `analytics_schedule`, with index `(enabled, next_run_time)`;
  - `analytics_schedule_recipient`;
  - `analytics_send`, with index `(analytics_schedule, occurrence)`.

  Use the FKs, `ON DELETE CASCADE` for shares, recipients, schedules of a view and sends of a schedule, and the engine and charset of the other tables. Write `migrations/032_operations_analytics_rollback.sql`, which drops the tables in dependency order and then the index. **Do not apply it to mbe_dev**: that is T080, and it needs the user's confirmation.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Models, the six error codes, the shared enums, the balance kind in the engine and the per-family privileges, which every story uses.

**⚠️ No user story work can begin until this phase is complete.**

### Tests

- [X] T003 [P] Extend `tests/unit/test_analytics_engine.py` with the balance kind (R1), using a fake balance fact that returns fixed values per moment:
  - with a month dimension, each bucket holds the value at that bucket's end, not a sum;
  - the totals row is the value at the period's end;
  - shares are taken within the end moment;
  - a comparison reads the value at the comparison period's end;
  - a query mixing a flow and a balance measure merges both on the dimension tuple;
  - every existing engine test still passes unchanged.
- [X] T004 [P] Extend `tests/unit/test_analytics_catalog.py`:
  - every new `MeasureKey` and `DimensionKey` of data-model.md has an entry with label, unit, family, `kind` (flow or balance) and facts;
  - the sensitive set now also holds `stock_value`, `stock_cost_coverage`, `dead_stock_value`, `slow_stock_value`, `turnover`, `days_of_inventory`, `purchase_sale_spread` and `purchase_sale_spread_pct`;
  - the families and their privileges are exactly contracts/api.md's (R17);
  - each new dimension applies only to the facts named in data-model.md "New dimensions".

### Implementation

- [X] T005 Add the models of data-model.md to `app/models/analytics.py`: `ReceivableSnapshot`, `ReceivableSnapshotMonth`, `AnalyticsView`, `AnalyticsViewShare`, `AnalyticsSchedule`, `AnalyticsScheduleRecipient` and `AnalyticsSend`. Columns and types must match migration 032 exactly. Register them wherever `app/models/__init__.py` registers spec 037's `AnalyticsExport`. `tests/unit/test_model_schema.py` passes, or is extended where it lists tables.
- [X] T006 [P] Add the six codes of contracts/api.md "Error codes" to `ErrorCode` in `app/core/error_codes.py`, with their statuses and params. Add the same rows to the `## analytics` section of `specs/036-localizable-error-codes/contracts/error-codes.md`. Raise the pinned count from 251 to 257 in `tests/unit/test_contract_docs.py` (`len(_documented_codes())`) and in `tests/api/test_openapi_problem.py` (`len(schemas['ErrorCode']['enum'])`).
- [X] T007 [P] Add `'analytics_view': 'analytics_view'` and `'analytics_schedule': 'analytics_schedule'` to `RESOURCE_NAMES` in `app/core/errors.py`.
- [X] T008 In `app/schemas/analytics.py`:
  - extend `MeasureKey`, `DimensionKey`, `DetailKind` and `DetailFlag` with the keys of data-model.md "Catalog additions" and "Request shapes";
  - extend `ExportSource` with `kardex` and `customer`;
  - add `MeasureKind` (`flow`, `balance`), `RelativePeriod`, `ViewKind` (`query`, `dashboard`, `detail`, `customer`), `Cadence`, `SendOutcome` and `StockOptions` (with the ranges of R5 as validators; out of range raises `analytics.stock_options_invalid`);
  - add `stock_options` and `price_change_pct` to `AnalyticsQueryRequest`;
  - add `family`, `kind` and `definition` to `AnalyticsMeasure` (contracts/api.md "Catalog"; spec 037's block already shows them, and T069 checks it).

  All enums are `StrEnum`.
- [X] T009 In `app/services/analytics/catalog.py`:
  - add the `kind` field to `Measure` (existing measures are `flow`);
  - add the families `inventory`, `receivables`, `payables`, `commissions`, `fiscal` and `purchases` to `FAMILY_PRIVILEGES` with R17's SystemObjects;
  - add every new measure and dimension of data-model.md with its family, unit, kind, sensitivity, facts and definition text (state "at current cost" on value, turnover and days of inventory, R4; state on every receivable balance measure that 2025 month-ends understate the receivable by up to $2.79M because of legacy orders closed by the paid flag, and are exact from 2026-01, FR-121).

  Add a `scope_column` per fact for facility scope (R17). The new facts are only names at this point, and each story adds its SQL. T004 passes.
- [X] T010 In `app/services/analytics/engine.py`, add the balance kind (R1):
  - `wanted` asks a balance fact for the moments it needs: the period's end, each bucket's end with a date dimension, and the comparison period's end;
  - `assemble` places each moment's values in its bucket instead of summing, and takes totals and shares from the end moment;
  - `fact_key` includes the moments, so the dashboard still deduplicates.

  Flow facts behave exactly as before. T003 passes.
- [X] T011 In `app/services/analytics/engine.py`, extend `check_privileges` and `families` to the new families. Add `history_guard(measures, start, end)`, which raises `analytics.before_history` with `history`, `requested` and `earliest` (R3, R8), and `month_end_guard(measures, start, end)`, which raises `analytics.period_not_month_end` (R9). Each story calls the guard for its measures.

**Checkpoint**: migration files and models exist, the codes are in the catalog, and the engine runs balance measures over fake facts.

---

## Phase 3: User Story 1 - Stock position, now and on any past date (Priority: P1) 🎯 MVP

**Goal**: stock now and as of any date since 2025-02-07, movements, value at current cost, velocity, cover, stock-outs, dead and slow stock, restock suggestions, the paged kardex with value, and the inventory tiles.

**Independent test**: on fixtures, on hand by product and warehouse equals `stock_ledger.on_hand`, and stock as of a past date equals the kardex balance at that date (on mbe_dev in T081).

### Tests

- [X] T012 [P] [US1] Write `tests/unit/test_analytics_balances.py` (stock part), on SQLite with fixture ledger entries:
  - on hand as of a date equals `stock_ledger.on_hand` over the same entries, per pair;
  - with a month dimension, each bucket equals the as-of sum at its end (opening plus cumulative movements, R2);
  - transit stock goes to the destination facility (`split_transit`), never twice;
  - reserved and available equal `reserved_by_warehouse` today and are null at a past date;
  - value uses today's list-0 cost without VAT; cost 0 and cost 99,999 pairs are uncosted, with no value, lower `stock_cost_coverage`, and add their on hand to `uncosted_quantity` (visible without the cost privilege);
  - negative on hand counts as a stock-out and adds no value.
- [X] T013 [P] [US1] Extend `tests/unit/test_analytics_facts.py` with the `movement` fact: units in and out by `movement_type`, distinct documents, transfers netting to zero company-wide, and the MariaDB compile check (no SQLite-only construct).
- [X] T014 [P] [US1] Write `tests/integration/test_analytics_inventory.py`, with fixture pairs on each side of every threshold of R5:
  - velocity over 30, 90 and 365 days, with refunds netted;
  - days of cover is null when velocity is 0;
  - stock-out, below reorder and suggested quantity (rounded up for whole-unit products, not for a fraction-selling one);
  - dead at 90, 180 and 365 days, and slow above `slow_cover_days`;
  - turnover and days of inventory from cost of sales and the average of start and end value;
  - a period starting before 2025-02-07 gives `analytics.before_history` with history `stock`;
  - `velocity_days: 20` gives `analytics.stock_options_invalid`;
  - the `stock` and `movements` detail kinds with each flag, whose sums equal the query.
- [X] T015 [P] [US1] Write `tests/unit/test_analytics_kardex.py`:
  - paging over a range longer than 366 days continues the running balance exactly (page 2's first balance = page 1's last);
  - `opening` is the balance before `start`, and `closing` the balance at `end`;
  - `unit_cost` and `value` appear only with the cost privilege;
  - `start` before 2025-02-07 gives `analytics.before_history`;
  - `limit` above 500 gives a 422.
- [X] T016 [P] [US1] Write `tests/api/test_analytics_operations.py` (US1 part):
  - 401 without a token on `GET /analytics/kardex`;
  - 403 `auth.forbidden` for inventory measures without 67, 32 or 99;
  - `stock_value` without 37–39 is refused with `required` [37, 38, 39];
  - facility scope uses the warehouse's facility;
  - the dashboard has the inventory tiles and alerts only for an inventory reader, and `stock_value_by_facility` and `dead_stock` only with the cost privilege.

### Implementation

- [X] T017 [US1] Write `app/services/analytics/balances.py`, stock part (R2, R4):
  - `stock(db, dimensions, filters, moments, scope)`: one grouped opening query (Σ `lot_serial_tracking.quantity` dated before the first moment + 1 day) and one movements query bucketed by moment, with the cumulative sum taken in Python;
  - `stock_today(db, ...)`: reserved and available from `stock_ledger.reserved_by_warehouse`, plus transit through `stock_query_service.split_transit`;
  - value from `product_price` list 0 (`COST_PRICE_LIST_ID`), with `settings.placeholder_cost` and ≤ 0 as uncosted.

  The facility comes from the warehouse. The supplier dimension is the product's. T012 passes.
- [X] T018 [US1] Add the `movement` fact to `app/services/analytics/facts.py`: units in, units out, entries and distinct `(source, reference)` documents by `movement_type`, warehouse, product and date bucket, over `lot_serial_tracking` in the period. T013 passes.
- [X] T019 [US1] Add the velocity, cover, stock-out, reorder, dead, slow and turnover measures to `app/services/analytics/balances.py`. They are pair-level, computed in Python from `stock`, `stock_today` and the sale and refund movements in the `velocity_days` before the moment (R5), with the defaults from settings and `StockOptions`. Turnover uses spec 037's cost of sales. Call `engine.history_guard` for stock measures. T014 passes for queries.
- [X] T020 [US1] Add the `stock` and `movements` kinds to `app/services/analytics/detail.py`, with the flags `stock_out`, `below_reorder`, `dead`, `slow` and `uncosted`, and `figures.values` per contracts/api.md. In `app/schemas/analytics.py`, add `warehouse` and `figures` (`AnalyticsDetailFigures`) to `AnalyticsDetailItem` and make `document_id`, `date`, `facility` and `amount` optional. Leave out cost and value without the cost privilege. `movements` also needs read on 32. T014 passes.
- [X] T021 [US1] Write `app/services/analytics/kardex.py` (R6): reuse `kardex_service.kardex_rows`, `_documents` and `source_name`. Compute the opening at `start` and add the skipped entries for each page's first balance. `limit` is at most 500, with no range cap. Value is the running balance × the current cost. Add `AnalyticsKardexResponse` and `AnalyticsKardexRow` to `app/schemas/analytics.py`.
- [X] T022 [US1] Add `GET /analytics/kardex` to `app/api/v1/endpoints/analytics.py` (inventory family; facility scope by the warehouse). Add the `kardex` source to `app/services/analytics/exports.py` (`check` and `build_table`). T015 passes, and `test_contract_docs` passes for the kardex blocks.
- [X] T023 [US1] Add the inventory KPI `stock_value`, the series `stock_value_by_facility` and the alerts `stock_outs`, `below_reorder`, `dead_stock` (180 days) and `uncosted_stock` to `app/services/analytics/dashboard.py`. Join them to the shared fact list (`_fact_job`), so one stock query serves every tile (R18). T016 passes.

**Checkpoint**: stock and movements answer through query, detail, kardex, dashboard and export, reconciled on fixtures. This is the MVP.

---

## Phase 4: User Story 2 - Receivables and collection, today and over time (Priority: P1)

**Goal**: spec 033's balance rule with `as_of`, month-end snapshots with their job, the receivable measures (aging, DSO, CEI, collected against due, utilization, forecast), payables today, and their tiles.

**Independent test**: today's receivables by customer and bucket equal `accounts_service.summary` on fixtures; a month-end equals a rebuild through the same rule.

### Tests

- [X] T024 [P] [US2] Write `tests/unit/test_sales_order_as_of.py`, on fixtures:
  - `open_credit_orders(as_of=None)` equals `as_of=local_now()`, and both equal spec 033's figures;
  - at a past moment T: an application dated after T doesn't count, and a 0001-dated one counts at its payment's date;
  - a refund after T doesn't count, and one without a date uses `modification_time`;
  - a credit note issued before T and cancelled after T counts;
  - an order confirmed after T isn't a candidate;
  - an order with `balance_zeroed_time` after T is still open at T;
  - an order flagged paid without a zero time is closed at every T.
- [X] T025 [P] [US2] Write `tests/integration/test_snapshot_receivables.py`:
  - the job writes one row per open credit order per month-end, plus a marker for a month with none;
  - a second run writes nothing;
  - `--rebuild` of an unchanged month gives identical rows in one transaction;
  - `--from 2024-12` is refused with `analytics.before_history` (history `receivables`);
  - backfilled rows have `limit_historical = 0`.
- [X] T026 [P] [US2] Write `tests/integration/test_analytics_receivables.py`:
  - receivable by customer and bucket today equals `accounts_service.summary` to the cent;
  - grouped by `payment_terms` (the order's terms), the rows add up to the total (FR-123);
  - the catalog definition of `receivable` states the 2025 caveat (FR-121);
  - a past date reads the stored month-end, and a mid-month date rounds down to the month-end before it;
  - a query for a month-end before 2025-01-31 gives `analytics.before_history` (history `receivables`);
  - the `credit_flow` fact compiles on MariaDB (added to `tests/unit/test_analytics_facts.py`, as T013 does for `movement`);
  - DSO and CEI from fixture month-ends match hand-computed values;
  - DSO over a period not starting on a 1st gives `analytics.period_not_month_end`;
  - collected on credit, due in period and due collected follow R9;
  - utilization is null without a limit and computed from parts across customers;
  - the forecast's buckets add up to the total receivable;
  - the payables measures are zero while `supplier_balance_cutoff` is null, and follow `po_counts` once it is set;
  - the `receivables` detail kind with flag `overdue` sums to the query.
- [X] T027 [P] [US2] Extend `tests/api/test_analytics_operations.py` (US2 part): 403 without 18 or 46 for receivables and without 19 for payables; facility scope uses the order's facility; the receivables tiles appear only for a receivables reader; and the payables tile is absent while the cutoff is null.

### Implementation

- [X] T028 [US2] In `app/services/sales_order_service.py`, add the keyword `as_of: datetime | None = None` to `order_balances`, `refunded_by_order`, `_credit_candidates` and `open_credit_orders` (R7). With `None` every query is unchanged. Change nothing else. T024 passes, and every spec 033 test passes unchanged.
- [X] T029 [US2] Write `app/jobs/snapshot_receivables.py` in the style of `app/jobs/purge_exports.py`, with no arguments, `--from YYYY-MM`, and `--month YYYY-MM --rebuild` (R8). Each month-end is one `open_credit_orders(as_of=<month-end 23:59:59>)` call, written in one transaction. It stores the customer's agent and current credit limit. T025 passes.
- [X] T030 [US2] Add to `app/services/analytics/balances.py`:
  - `receivable(db, ...)`, which reads `receivable_snapshot` for month-ends and `open_credit_orders()` for now, in base currency, with `accounts_service.days_past_due` and `bucket`;
  - `payable(db, ...)`, today only, from spec 033's supplier balances and `po_due_date` (R10).

  Add the aging and due bucket and sales agent dimensions.
- [X] T031 [US2] Add the `credit_flow` fact to `app/services/analytics/facts.py`: credit sales (spec 037's gross sales of credit-terms orders), collected on credit by application date, due in period from the start balance, and due collected (R9). Compute DSO and CEI from their parts in `engine.assemble`, calling `engine.month_end_guard`. T026 passes for queries.
- [X] T032 [US2] Add the `receivables` detail kind (read on 7, flag `overdue`) to `app/services/analytics/detail.py`. T026 passes.
- [X] T033 [US2] Add to `app/services/analytics/dashboard.py`:
  - the KPIs `receivable_overdue` and `dso`;
  - the series `receivable_overdue_trend` (12 month-ends and now), `top_debtors` (top 10 with others) and `collected_vs_due`;
  - the alert `payables_overdue`, only once the cutoff is set.

  Spec 037's `overdue_credit_orders` alert stays unchanged. T027 passes.

**Checkpoint**: receivables answer today and over time on fixtures; the snapshot job is idempotent.

---

## Phase 5: User Story 3 - Customer 360 (Priority: P2)

**Goal**: one response per customer and period, built from engine sections.

**Independent test**: for a fixture customer, each section equals the same query filtered by that customer, and the balance equals spec 033's.

### Tests

- [X] T034 [P] [US3] Write `tests/integration/test_analytics_customer.py`:
  - `sales` (net sales, refunds, payments as `collected`, margin), `receivable` and `top_products` each equal `POST /analytics/query` with that customer's filter;
  - payments need the payments family; without it, `collected` is absent from `sales` and the rest of the section stays;
  - margin appears only with 37–39;
  - `last_purchase` and `orders_per_month` cover the 12 months before `end`;
  - credit limit and utilization match spec 033's;
  - a section is null when the caller lacks its family;
  - an explicit other facility is refused with `facility.other_facility`;
  - 404 for an unknown customer.
- [X] T035 [P] [US3] Extend `tests/api/test_analytics_operations.py`: 401 on `GET /analytics/customers/{id}`; and the `customer` export source gives the same figures as the response.

### Implementation

- [X] T036 [US3] Write `app/services/analytics/customer.py`: run the three sections through `engine.run` with a forced customer filter, then add last purchase, frequency, limit and utilization. Add `AnalyticsCustomerResponse` to `app/schemas/analytics.py`.
- [X] T037 [US3] Add `GET /analytics/customers/{customer_id}` to `app/api/v1/endpoints/analytics.py`, and the `customer` source to `app/services/analytics/exports.py`. T034 and T035 pass, and `test_contract_docs` passes for `AnalyticsCustomerResponse`.

**Checkpoint**: a customer 360 that reconciles with its queries.

---

## Phase 6: User Story 4 - Commissions a salesperson can verify (Priority: P2)

**Goal**: commissions read from spec 025's `read_period`, by agent, label, participation, customer and month, with lines a salesperson can check.

**Independent test**: for a fixture agent and month, the total equals `commission_service.compute`, and the lines add up to it.

### Tests

- [X] T038 [P] [US4] Write `tests/unit/test_analytics_commissions.py`: lines grouped by agent, label, participation, customer and month equal the sum of the lines; `commission_base` is price × quantity; and refund lines are negative in their own month.
- [X] T039 [P] [US4] Write `tests/integration/test_analytics_commissions.py`:
  - a closed month reads the stored lines, `closed`;
  - an open month equals `commission_service.compute`, `provisional`;
  - the `commission_lines` detail sums to the query;
  - an agent without 112 sees only their own lines, even when filtering another agent;
  - a non-agent without 112 gets `auth.forbidden`;
  - with a date dimension, commissions use month grain;
  - facility scope uses the facility of the agent's user record (R17).

### Implementation

- [X] T040 [US4] Write `app/services/analytics/commissions.py` (R11): for each month in the period, call `commission_service.read_period(db, year, month, ...)` and group the lines in Python. It is registered in the engine as a Python fact, since it has no SQL. In `engine.check_privileges`, force the agent filter for an agent without 112 (`commission_agent.employee = current.employee_id`). T038 passes.
- [X] T041 [US4] Add the `commission_lines` detail kind to `app/services/analytics/detail.py`, with every field of contracts/api.md in `figures.values`. T039 passes.

**Checkpoint**: commissions match spec 025 on fixtures, and agents see only their own.

---

## Phase 7: User Story 5 - Invoiced against sold, and the fiscal picture (Priority: P2)

**Goal**: the invoice and coverage facts, status, output VAT, standalone invoices, the fiscal tiles.

**Independent test**: for a fixture month, invoiced + not invoiced = sold to the cent, and the uninvoiced detail lists exactly the lines without a live invoice line.

### Tests

- [X] T042 [P] [US5] Extend `tests/unit/test_analytics_facts.py` with `invoice_line` and `invoice_coverage`:
  - the invoiced quantity is capped at the line quantity;
  - invoiced + not invoiced cents = sale cents per line;
  - the lag is weighted by invoiced cents;
  - stamped, cancelled and pending follow R12, and a draft cancelled before stamping is excluded;
  - output VAT = invoice VAT − credit note VAT;
  - both compile on MariaDB.
- [X] T043 [P] [US5] Write `tests/integration/test_analytics_fiscal.py`:
  - coverage by facility, customer and salesperson;
  - standalone invoices appear in `standalone_invoiced` and not in coverage;
  - an invoice issued after the period raises that period's coverage;
  - `payment_receipts` is listed, not added to VAT;
  - the `invoices` and `uninvoiced_lines` detail kinds sum to the query;
  - 403 without 23 or 48;
  - facility scope uses the issuing facility: a caller limited to one facility sees none of another facility's invoices (FR-171);
  - the dashboard has `uninvoiced_sales` and the `invoice_coverage` series only for a fiscal reader, and each equals the query for the same period and scope (FR-180).

### Implementation

- [X] T044 [US5] Add the `invoice_line` and `invoice_coverage` facts to `app/services/analytics/facts.py` (R12). Reuse spec 037's line rule and `sale_lines`. Add the issuer, document status and document type dimensions. T042 passes.
- [X] T045 [US5] Add the `invoices` (read on 23) and `uninvoiced_lines` (read on 7) detail kinds to `app/services/analytics/detail.py`. T043 passes.
- [X] T046 [US5] Add the KPI `uninvoiced_sales` and the series `invoice_coverage` by facility to `app/services/analytics/dashboard.py`, for fiscal readers only. T043 passes.

**Checkpoint**: the fiscal questions answer and reconcile on fixtures.

---

## Phase 8: User Story 7 - Saved views (Priority: P2)

**Goal**: save, list, share, open, change and delete views with relative periods, run as the opener.

**Independent test**: a view saved with `last_month`, run on two frozen dates, resolves to each date's month and equals the equivalent query.

### Tests

- [X] T047 [P] [US7] Write `tests/unit/test_analytics_periods.py`: every `RelativePeriod` on edge dates (31 March, 29 February 2028, 1 January, a Sunday, ISO week 53 of 2026); `last_n_days` from 1 to 3,650; and `fixed` with `start` after `end` refused.
- [X] T048 [P] [US7] Write `tests/integration/test_analytics_views.py`:
  - create, list (own and shared), get, rename, change and delete;
  - names are unique per owner (`resource.already_exists`);
  - run resolves the period against today and equals the matching route's response;
  - a shared user runs it with their own privileges and scope, and is refused a cost measure without 37–39;
  - a view whose definition names a removed key is refused with `analytics.view_invalid`, both at save and at run;
  - only the owner may change or delete it (`auth.forbidden`);
  - `shared_with` is empty for shared users;
  - deleting the owner removes their views.
- [X] T049 [P] [US7] Write `tests/api/test_analytics_views.py`: 401 on every view route; 404 for another user's unshared view; 422 on an invalid period.

### Implementation

- [X] T050 [US7] Write `app/services/analytics/periods.py` (R14): `resolve(period, today) -> (start, end)` with ISO weeks in business time. T047 passes.
- [X] T051 [US7] Add `AnalyticsPeriod`, `AnalyticsViewCreate`, `AnalyticsViewUpdate` and `AnalyticsViewResponse` to `app/schemas/analytics.py`.
- [X] T052 [US7] Write `app/services/analytics/views.py`:
  - CRUD with owner checks;
  - validation by parsing the definition into the kind's request model, then `engine.check_privileges`, with errors mapped to `analytics.view_invalid`;
  - `run(db, view, current)`, which dispatches to the query, dashboard, detail or customer service;
  - sharing to active users only.

  Extend `exports.delete_for_user` in `app/services/analytics/exports.py` to delete the user's views and schedules.
- [X] T053 [US7] Create `app/api/v1/endpoints/analytics_views.py` with the six view routes of contracts/api.md (run keeps the caching and `If-None-Match` of the route it dispatches to). Include it in `app/api/v1/router.py` under the `analytics` prefix. T048 and T049 pass, and `test_contract_docs` passes for the view blocks.

**Checkpoint**: views save, share and run with rolled-forward periods.

---

## Phase 9: User Story 6 - Purchases and supplier prices (Priority: P3)

**Goal**: the purchase-line fact, price history with change flags, and purchase against sale.

**Independent test**: purchases by supplier for a fixture month equal the confirmed PO lines; a product's history lists every line with its previous price.

### Tests

- [ ] T054 [P] [US6] Extend `tests/unit/test_analytics_facts.py` with `purchase_line`: the amount is quantity × price × (1 − discount) with the tax-included flag ignored; cancelled and draft POs are excluded; received quantity comes from confirmed receipts; it compiles on MariaDB.
- [ ] T055 [P] [US6] Write `tests/integration/test_analytics_purchases.py`:
  - purchases by supplier, product and warehouse;
  - the `purchases` detail has the previous price and change % through the window;
  - price-0 lines are excluded from the history;
  - `price_change` flags |change %| above `price_change_pct` (default 10);
  - the spread and spread % appear only with 37–39; average purchase price shows for any purchases reader (FR-145);
  - 403 without 20 or 75;
  - facility scope uses the PO's facility.

### Implementation

- [ ] T056 [US6] Add the `purchase_line` fact to `app/services/analytics/facts.py` (R13), dated by the PO's `modification_time`, with the supplier dimension. Derive average purchase and sale prices and the spread in `engine.assemble`. T054 passes.
- [ ] T057 [US6] Add the `purchases` detail kind (read on 20, flag `price_change`) to `app/services/analytics/detail.py`, with `LAG` over (product, date, line id). T055 passes.

**Checkpoint**: purchases and price history reconcile on fixtures.

---

## Phase 10: User Story 8 - Scheduled reports by email (Priority: P3)

**Goal**: schedules for saved views, sent exactly once by a CLI job as the owner at send time, with every attempt recorded.

**Independent test**: with a frozen clock, a due schedule sends once with the attachment; two claims of the same read value send once.

**Depends on**: US7 (views).

### Tests

- [ ] T058 [P] [US8] Write `tests/unit/test_analytics_schedules.py`, for `next_occurrence`:
  - daily;
  - weekly with several weekdays;
  - monthly on day 31 in February, April and a leap year;
  - a time already passed today;
  - a run missed for 3 days gives one occurrence (the latest), then the next future one.
- [ ] T059 [P] [US8] Write `tests/integration/test_analytics_schedules.py`, for the routes:
  - create, list (administrators see all), get, change, disable and delete;
  - only the view's owner may schedule it;
  - weekly needs a weekday, and monthly a day 1–31;
  - more than 10 recipients gives a 422;
  - an inactive recipient, or one lacking a family (or the cost privilege for a sensitive measure), gives `analytics.recipient_ineligible` with `user` and `family`;
  - deleting a view deletes its schedules;
  - the send history is newest first and paged.
- [ ] T060 [P] [US8] Write `tests/integration/test_send_scheduled_reports.py`, for the job with a fake mailer and a frozen clock:
  - nothing runs while `scheduled_reports_enabled` is false;
  - a due schedule sends one email per run with the attachment, recording outcome `sent`, recipients, size and row count;
  - two claims of the same `next_run_time` send once (SC-008), as do 30 days of occurrences run twice each;
  - an owner who lost a family, and an owner whose status is not active, each fail with `auth.forbidden`;
  - a view that no longer validates fails with `analytics.view_invalid` and sends nothing (FR-161);
  - a recipient who lost a family is dropped and recorded;
  - an attachment over the cap is skipped with `analytics.attachment_too_large`;
  - `get_mailer()` returning None records `skipped` with `email.not_configured`;
  - a mailer raising `MailError` records `failed` with `email.send_failed`, and `sent` is recorded only after the mailer returns;
  - a row left running for over 10 minutes reads as failed.
- [ ] T061 [P] [US8] Extend `tests/api/test_analytics_views.py`: 401 on every schedule route; 404 for another user's schedule.

### Implementation

- [ ] T062 [US8] Add `AnalyticsScheduleCreate`, `AnalyticsScheduleUpdate`, `AnalyticsScheduleResponse` and `AnalyticsSendResponse` to `app/schemas/analytics.py`.
- [ ] T063 [US8] Write `app/services/analytics/schedules.py`, part 1:
  - `next_occurrence(schedule, after)` in business time (R15);
  - recipient eligibility through `engine.families` (R16);
  - CRUD with owner checks;
  - sends listing.

  T058 passes.
- [ ] T064 [US8] Add the five schedule routes and the sends route to `app/api/v1/endpoints/analytics_views.py`. T059 and T061 pass, and `test_contract_docs` passes for the schedule blocks.
- [ ] T065 [US8] Write `app/services/analytics/schedules.py`, part 2:
  - `claim(db, schedule_id, read_next_run_time, now)`, the conditional `UPDATE` of R15, returning whether one row changed;
  - `rebuild_owner(db, user_id) -> CurrentUser` (R16);
  - `send(db, schedule, occurrence, now)`, which inserts the running row, resolves the period, builds the file through `exports.check`, `build_table` and `file_bytes` or `render_pdf`, applies the size cap, drops ineligible recipients, sends through `mail.get_mailer()` off the event loop as spec 024 does, and finishes the row.
- [ ] T066 [US8] Write `app/jobs/send_scheduled_reports.py` in the style of `app/jobs/purge_exports.py`: exit unless enabled, select the due schedules, claim each, and send. Add a docstring giving the cron entry (`*/5 * * * *` as the instance's user). T060 passes.

**Checkpoint**: schedules send exactly once on fixtures, and every attempt is recorded.

---

## Phase 11: Polish and cross-cutting concerns

- [ ] T067 [P] Extend the SC-006 sweep in `tests/api/test_analytics_operations.py`: for a caller with every family except 37–39, call every new route and kind (query with every visible new measure, dashboard, each new detail kind, kardex, customer 360, view run, and each export format of `kardex` and `customer`). Assert that no JSON key, CSV header or XLSX header is a sensitive key. The sensitive keys are:
  - every measure the catalog marks sensitive (spec 037's and T004's set), read from the catalog, so a new sensitive measure is swept without editing the test;
  - the cost fields `unit_cost`, `cost` and `value` of kardex rows and of the `stock` kind's `figures.values`.

  Match whole key names, not substrings, so non-sensitive keys stay allowed: `uncosted_quantity`, the `uncosted_stock` alert, the `uncosted` flag and the `figures.values` container itself. If a key can't be classified this way, rename it in the contract instead of weakening the sweep.
- [ ] T068 Add a family sweep to `tests/api/test_analytics_operations.py`: for each family, a caller without it gets no tile, row or alert of it on the dashboard (SC-006).
- [ ] T069 [P] Check that spec 037's `AnalyticsMeasure` and `AnalyticsDetailItem` blocks in `specs/037-business-analytics/contracts/api.md` match the schemas as built: `family`, `kind` and `definition` on the measure; on the item, `document_id`, `date`, `facility` and `amount` optional, plus `warehouse` and `figures`. The blocks were updated during /speckit-analyze; fix the doc or the schema where they differ.
- [ ] T070 [P] Add the spec to `CHANGELOG.md` under `[Unreleased]`:
  - **Added**: the analytics families, kardex, customer 360, views and schedules routes, migration 032, the six `analytics.*` codes, the two jobs and the settings;
  - **Docs**: spec 038.
- [ ] T071 Confirm `tests/unit/test_contract_docs.py` passes with every block of `specs/038-analytics-operations/contracts/api.md`. Also implement and test the contract's "Receivable history" rule that the response's `definition` shows the month-end used when a receivable date is rounded down (US2 left it open). Fix the doc or the schema where they differ, never by weakening the test.
- [ ] T072 Map every legacy row of the spec's table (23–38) to a test or a quickstart §6 request, and add any missing integration case to the story's test file.
- [ ] T073 Run `uv run ruff check app/ migrations/ tests/` and the full `uv run pytest -q`. Fix any failure.
- [ ] T074 Read the diff (constitution III). Every changed file must be in plan.md's Source Code tree or named by a task: `app/services/analytics/*`, `sales_order_service.py` (only the `as_of` keyword), `config.py`, `error_codes.py`, `errors.py`, `app/models/analytics.py` and `app/models/__init__.py`, `app/schemas/analytics.py`, `app/api/v1/endpoints/analytics.py` and `analytics_views.py`, `app/api/v1/router.py`, the two jobs, migration 032 and its rollback, the tests, `.env.example`, `CHANGELOG.md`, spec 036's error-code doc, spec 037's contract, and this spec's documents. Revert anything else.
- [ ] T075 [P] Check that SQLite and MariaDB both compile every new fact (the compile tests of T013, T026, T042 and T054), plus the ledger queries of `balances.py` (opening sum and CASE-bucketed movements), which US1 tested only on SQLite. No fact may have a SQLite-only construct.
- [ ] T076 [P] Write the operations notes in `specs/038-analytics-operations/quickstart.md` §5, if the implementation changed them: the cron line, `SCHEDULED_REPORTS_ENABLED` on one instance, and the job's output.
- [ ] T077 Confirm that neither known red test still fails.

### On mbe_dev (quickstart §2–§6)

Deploying the branch to dev :8765 is pre-authorized. Applying migration 032, running the receivables backfill and any real email write to mbe_dev or send mail, so **T080, T082 and T086 need the user's confirmation before they run**.

- [ ] T078 Read-only on mbe_dev: record in research.md R19 the EXPLAIN of the movement query for 2025, before the migration (expected `ALL` on `lot_serial_tracking`).
- [ ] T079 Read-only on mbe_dev: time one `open_credit_orders(as_of=...)` for 2025-06-30 and 2026-06-30, and one live `commission_service.compute` month. Record the times in research.md R8 and R11, to size the backfill and the commissions target before asking.
- [ ] T080 **Ask the user**, then apply migration 032 on mbe_dev with `uv run python -m app.db.migrate`. Check that the EXPLAIN now shows `range` on `lot_serial_tracking_date_idx`, and that the seven tables exist and are empty. Record the results in the verifications.
- [ ] T081 Deploy the branch to dev :8765 (as mictlanix@, tmux). Run quickstart §3, stock: on hand for every pair against `GET /stock` (zero differences), and 20 pairs × 10 dates against the spec 030 kardex balance (SC-001). Record the results.
- [ ] T082 **Ask the user**, then run `uv run python -m app.jobs.snapshot_receivables --from 2025-01` on mbe_dev. Record the time and the rows per month. Then rebuild 3 month-ends with `--rebuild` and check that the rows are identical (SC-002).
- [ ] T083 Run the rest of quickstart §3: receivables today against `GET /accounts-receivable` by customer and bucket (SC-002); commissions for 3 agents × 2 months against `GET /commissions/periods/{y}/{m}` (SC-003); coverage for 3 months (SC-004); and purchases for one month by supplier against the PO lines. Record each comparison.
- [ ] T084 Run quickstart §4, performance (SC-009): 30 warm runs of each request, cache cleared, p95 recorded in research.md R18. If a target is missed, **stop and report** before redesigning.
- [ ] T085 Run quickstart §5 without mail: with `SCHEDULED_REPORTS_ENABLED=true` for one shell only, run the job against a due schedule. Expect an `analytics_send` row with outcome `skipped` and code `email.not_configured`. Then run two jobs at once against one due schedule, and expect exactly one send row (SC-008). Record both.
- [ ] T086 Only if the user provides SMTP settings and agrees: one real send, with the attachment, to the user. Otherwise record it as not run. Installing the cron entry on any server is a deployment step outside this spec unless the user asks.
- [ ] T087 Run quickstart §6 (SC-005): for each legacy row marked Q, D, Detail or V, run the stated request on dev and record the request and a one-line result in the verifications.
- [ ] T088 Open the PR from `038-analytics-operations`. Under "Roadmap": `Closes mictlanix/mbe-roadmap#94`, `#96`, `#100`, `#102`, and `Part of mictlanix/mbe-roadmap#9`. End the body with "🤖 Generated with [Claude Code](https://claude.com/claude-code)".
- [ ] T089 After the merge:
  - open the mbe-ui issue of quickstart §7;
  - record the verifications, mark the spec complete and commit `.spec-context.json` to main;
  - delete the branch locally and remotely, and remove the worktree.

---

## Dependencies and execution order

### Phases

- **Setup (1)**: none.
- **Foundational (2)**: after Setup. Blocks every story.
- **US1 (3)**: after Foundational. The MVP.
- **US2 (4)**: after Foundational. Independent of US1, except that both edit `balances.py`, `facts.py`, `detail.py` and `dashboard.py`, so their tasks on those files run one at a time.
- **US3 (5)**: after US2. It needs the receivable measures, plus spec 037's sales.
- **US4 (6)**: after Foundational. Independent of the other stories.
- **US5 (7)**: after Foundational. Independent of the other stories.
- **US7 (8)**: after Foundational. Its `run` dispatches to customer 360 only once US3 is done; until then, the `customer` kind is tested last.
- **US6 (9)**: after Foundational. Independent of the other stories.
- **US8 (10)**: after US7.
- **Polish (11)**: after every story. T080, T082 and T086 wait for the user.

### Inside each story

Tests come first and fail; then schemas, then services, then routes. These files are shared, so tasks that touch them run one at a time:
- `facts.py`, `balances.py` and `engine.py`;
- `detail.py` and `dashboard.py`;
- `app/schemas/analytics.py`;
- `tests/unit/test_analytics_facts.py` (T013, T026, T042 and T054) and `tests/api/test_analytics_operations.py` (T016, T027, T035, T067 and T068).

### Parallel opportunities

- **Setup**: T001 and T002.
- **Foundational**: T003 and T004; then T006 and T007 alongside T005.
- **US1**: T012–T016 (five test files).
- **US2**: T024–T027.
- **US3**: T034 and T035.
- **US4**: T038 and T039.
- **US5**: T042 and T043.
- **US7**: T047–T049.
- **US6**: T054 and T055.
- **US8**: T058–T061.
- **Across stories**: once Foundational is done, the test tasks of US1, US2, US4, US5, US6 and US7 can be written together, except those on the two shared test files above, which run one at a time. Their implementation tasks queue on the shared files.
- **Polish**: T067, T069, T070, T075 and T076; T068 after T067 (same test file).

### Example: US1 tests in parallel

```text
Task: "Write tests/unit/test_analytics_balances.py, stock part (T012)"
Task: "Extend tests/unit/test_analytics_facts.py with the movement fact (T013)"
Task: "Write tests/integration/test_analytics_inventory.py (T014)"
Task: "Write tests/unit/test_analytics_kardex.py (T015)"
Task: "Write tests/api/test_analytics_operations.py, US1 part (T016)"
```

---

## Implementation strategy

### MVP (US1)

1. Phases 1 and 2.
2. US1: stock now and as of a date, movements, velocity and restock, kardex, inventory tiles.
3. **Stop and validate**: stock reconciles with the ledger and the kardex on fixtures. The balance kind is the new idea in the engine, so it is proven on the simplest balance before receivables are built on it.

### Incremental delivery

1. **US1**, inventory (MVP).
2. **US2**, receivables with history: the `as_of` change to spec 033 is the riskiest edit outside analytics, so it lands early.
3. **US3**, customer 360, on top of US2.
4. **US4** and **US5**, commissions and fiscal, in either order.
5. **US7**, saved views; then **US6**, purchases.
6. **US8**, schedules, which reuse views and exports.
7. Polish, then the mbe_dev checks, including the migration, the backfill and the timings that decide whether anything needs redesign.
