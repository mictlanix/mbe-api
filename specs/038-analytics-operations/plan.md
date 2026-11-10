# Implementation Plan: Operations analytics (inventory, receivables, commissions, fiscal, suppliers) with saved views and scheduled reports

**Branch**: `038-analytics-operations` | **Date**: 2026-11-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/038-analytics-operations/spec.md`

## Summary

This spec extends spec 037's analytics (catalog, engine, dashboard, detail, exports) to inventory, receivables and payables, commissions, fiscal documents and purchases. It adds saved views and emailed schedules. Every new number is a catalog measure computed by the rule that already owns it:
- spec 030's ledger sum for stock;
- spec 033's balance rule for receivables and payables;
- spec 025's computation for commissions;
- spec 037's line rule for invoices.

So nothing has a second formula. The approach (research R1–R20):

- **Balance facts** next to spec 037's flow facts. A balance takes its value at a moment: the period's end, or each date bucket's end. It adds across other dimensions but not across dates (R1).
- **Stock as of any day** since 2025-02-07 comes from the ledger in two queries (opening plus movements), with value at today's cost. Placeholder and zero costs are left out as uncosted (R2–R4).
- **Velocity, cover, stock-outs, dead and slow stock, and restock** are derived from options on the request; there is no minimum-stock setting (R5).
- **Kardex** gets an analytics route with value, paged instead of capped at 366 days (R6).
- **Receivables as of a moment** reuse spec 033's functions with a new `as_of` keyword. A job stores month-end snapshots, backfilled from 2025-01, for aging over time, DSO and CEI (R7–R9). Payables are today-only (R10).
- **Commissions** are read from spec 025's `read_period`: stored lines for closed months, live for open ones. An agent sees their own without privilege 112 (R11).
- **Invoices:** an invoice-line fact and a coverage fact over spec 037's sale lines (R12).
- **Purchases:** a purchase-line fact, with price history through `LAG` (R13).
- **Saved views** with relative periods and sharing (R14).
- **Scheduled sends:** a cron-run CLI job that claims each occurrence in the database, rebuilds the owner's privileges at send time, and sends through spec 024's mailer to eligible users (R15–R16).
- **Migration 032:** one index and seven tables (R19). **Six new `analytics.*` codes** (R20).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- FastAPI, Pydantic 2 and async SQLAlchemy 2.0, as in spec 037.
- XlsxWriter and WeasyPrint, as in spec 037.
- Spec 024's `app/services/mail.py`.
- **No new dependency.** The scheduler is a CLI job run by cron, not a library.

**Storage**: MariaDB 10.11 (mbe_dev). Migration `032_operations_analytics.sql` (with rollback) adds the index `lot_serial_tracking(date)` and seven tables:
- `receivable_snapshot` and `receivable_snapshot_month`;
- `analytics_view` and `analytics_view_share`;
- `analytics_schedule`, `analytics_schedule_recipient` and `analytics_send`.

It doesn't change any column of an existing table.

**Testing**: as spec 037:
- pytest with SQLite integration tests through the conftest;
- MariaDB compile checks for every new fact;
- API tests for 401, 403, 404 and 422, plus the cost-key sweep;
- reconciliation and timing on mbe_dev (quickstart §3–§4).

**Target Platform**: xolotl. Dev on :8765 (tmux) and test on :8766 (systemd) share mbe_dev, so scheduled sends are enabled on one instance only.

**Project Type**: web service (REST under `/api/v1`)

**Performance Goals** (SC-009):
- dashboard with the new tiles: under 2 s for a month and 5 s for a year;
- stock as of a date for all pairs, 12-month queries and any kardex page: under 5 s.

**Constraints**:
- No second formula for stock, balances or commissions.
- Cost-derived figures only with 37–39.
- Facility scope per family (R17).
- Spec 036 error codes.
- No history before 2025-02-07 (stock) or 2025-01-31 (receivables).
- Exactly-once sends.

**Scale/Scope**:
- **Data:** 670k ledger entries (123k a year), 18k stocked pairs, about 600–900 open credit orders a month-end, 5.5k invoices a year, 2k purchase orders a year, 6 commission agents.
- **Code:** 14 new routes, 10 new facts, about 60 measures and 10 dimensions, 7 tables, 2 jobs.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | Only one stored history (month-end receivables), because a past balance is too slow to compute per request (R8). Stock history is read from the ledger, not snapshotted (R2). Payables stay today-only until they have data (R10). There is no minimum-stock setting, by the user's decision (R5). The scheduler is a CLI job like `app/jobs/*`, not a library or an in-process loop (R15). | Pass |
| II. Think Before Coding | Every area was measured on mbe_dev first, and several premises didn't hold. Lots and serials have no data, so those reports are retired. Stock is unreliable before the 2025-02-06 reset. There is no cost history and no minimum stock. Commissions already have an approved rule (spec 025), and mbe#53 isn't about commissions. Payables are empty. Spec 037's cost basis was wrong, now fixed in #296. Three decisions went to the user in the spec's clarifications. | Pass |
| III. Surgical Changes | Outside the analytics package, the edits are: an `as_of` keyword on three `sales_order_service` functions and `refunded_by_order` (default unchanged, so spec 033's behaviour and tests stay identical); `delete_for_user` gains views and schedules; settings; spec 036's catalog. Spec 030's kardex route keeps its cap. | Pass |
| IV. Goal-Driven Execution | SC-001 to SC-004 are reconciliations on mbe_dev with recorded results (quickstart §3). SC-006 to SC-008 are tests. SC-009 is timed (§4). | Pass |
| V. Reuse Over Rebuild | Reused: `stock_ledger` sums and `reserved_by_warehouse`, `stock_query_service.split_transit`, `kardex_service.kardex_rows`/`_documents`, `order_balances`/`open_credit_orders`, `accounts_service.days_past_due`/`bucket`/`summarize`, supplier balances, `commission_service.read_period`, spec 037's facts/engine/dashboard/detail/exports/cache, `mail.get_mailer`, `exports.check` for the send-time privilege check, and the `app/jobs` CLI pattern. New modules are justified in Complexity Tracking. | Pass, see Complexity Tracking |
| VI. Async-First | Routes are `async def`. Dashboard facts run concurrently (037 R9). Mail sends and file writing use the thread helpers already used by spec 024 and 037. | Pass |
| VII. Security by Default | Every route is authenticated and gated per family. Cost-derived measures are filtered in the catalog, engine and serializers. Views run as the opener. Schedules run as the owner, re-checked at send. Recipients must hold the view's families, and no free-typed addresses are accepted. | Pass |
| VIII. Ruff Compliance | `uv run ruff check app/ migrations/ tests/` gates every commit. | Pass |
| Testing | Tests come first for each task, as listed in quickstart §1. | Pass |
| Changelog | `[Unreleased]` gains an Added entry for the new analytics, views, schedules, migration 032, the codes and the job. | Pass |

**Post-design re-check**: unchanged.
- **Phase 1 added:** two models modules, the schema additions, two endpoint modules (views/schedules and the kardex/customer routes inside `analytics.py`), seven service modules and two jobs. `user_service.delete_user` already calls `exports.delete_for_user`, so extending that function removes views and schedules without touching `user_service.py`.
- **Contract test:** `test_contract_docs` will fail on this branch until the new schemas exist, because the contract is written first, as in spec 037. Spec 037's `AnalyticsMeasure` and `AnalyticsDetailItem` blocks already show the new fields, so they fail too until T008 and T020.

## Project Structure

### Documentation (this feature)

```text
specs/038-analytics-operations/
├── spec.md
├── plan.md              # This file
├── research.md          # R1–R20
├── data-model.md        # tables, facts, catalog additions, shapes
├── quickstart.md        # local, migration, reconciliation, performance, schedules, legacy questions
├── contracts/
│   └── api.md           # routes, schemas (checked by test_contract_docs), error codes
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 032_operations_analytics.sql            # NEW  ledger date index + 7 tables
└── 032_operations_analytics_rollback.sql   # NEW
app/
├── core/
│   ├── config.py               # + stock_history_start, placeholder_cost, restock_lead_days,
│   │                           #   restock_cover_days, scheduled_reports_enabled,
│   │                           #   report_attachment_max_bytes
│   ├── error_codes.py          # + 6 analytics.* codes
│   └── errors.py               # + RESOURCE_NAMES for views and schedules
├── models/
│   └── analytics.py            # + ReceivableSnapshot(+Month), AnalyticsView(+Share),
│                               #   AnalyticsSchedule(+Recipient), AnalyticsSend
├── schemas/
│   └── analytics.py            # + keys, StockOptions, kardex, customer, views, schedules, sends
├── services/
│   ├── sales_order_service.py  # as_of keyword on order_balances, refunded_by_order,
│   │                           #   _credit_candidates, open_credit_orders
│   └── analytics/
│       ├── catalog.py          # + families, measures, dimensions, fact kinds
│       ├── facts.py            # + movement, invoice_line, invoice_coverage, purchase_line SQL
│       ├── balances.py         # NEW  stock (opening + movements), stock_today, receivable, payable
│       ├── commissions.py      # NEW  spec 025 lines grouped in Python
│       ├── engine.py           # + balance kind: bucket ends, totals at the end, comparison at end
│       ├── dashboard.py        # + new tiles and alerts
│       ├── detail.py           # + new kinds and flags
│       ├── kardex.py           # NEW  paged kardex with value
│       ├── customer.py         # NEW  customer 360 from engine sections
│       ├── periods.py          # NEW  relative periods
│       ├── views.py            # NEW  saved views and sharing
│       ├── schedules.py        # NEW  next occurrence, claim, send, eligibility
│       └── exports.py          # + kardex and customer sources; delete_for_user extended
├── api/v1/endpoints/
│   ├── analytics.py            # + kardex, customers
│   └── analytics_views.py      # NEW  views and schedules routes
└── jobs/
    ├── snapshot_receivables.py   # NEW
    └── send_scheduled_reports.py # NEW
tests/
├── unit/       test_analytics_balances, _periods, _schedules, _commissions, _facts (new facts),
│               _engine (balance kind), test_sales_order_as_of
├── integration/ test_analytics_inventory, _receivables, _commissions, _fiscal, _purchases,
│               _views, _schedules, test_snapshot_receivables, test_send_scheduled_reports
└── api/        test_analytics_operations (routes, privileges, sweep), test_analytics_views
```

**Structure Decision**: everything stays in spec 037's `app/services/analytics/` package, because the new areas are measures of the same engine. Views and schedules get their own route module, since they are CRUD rather than analytics reads. The two jobs follow the existing `app/jobs` CLI pattern.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Balance fact kind in the engine | Stock and receivables are states at a moment; summing them across dates is wrong (R1) | Separate per-area endpoints would rebuild the per-report model spec 037 replaced |
| `receivable_snapshot` (+ month marker) | Aging over time, DSO and CEI need past balances. Spec 033's rule over thousands of candidates per month-end is too slow per request (R8) | Computing on demand: 22 month-ends × the full rule per dashboard load. Snapshots from now only: the user chose a backfill from 2025-01 |
| `as_of` on spec 033 functions | "Never a second formula" (FR-120) | A copied as-of formula would drift from spec 033's |
| Views and schedules tables | Saved views and scheduled sends must persist, be shared and be claimed exactly once across instances | Files or settings JSON can't be claimed atomically or shared |
| Two CLI jobs | Snapshots and sends need to run on a schedule; the repo's jobs are CLI modules | In-process loops die on restart and run once per worker and instance |
| `lot_serial_tracking(date)` index | Movements in a period are a full scan of 670k rows today | No index: every movement and velocity query scans the ledger |
| `analytics/balances.py` | Balance facts read a moment (opening + movements, snapshots, today's reservations), not a period, and mix SQL with Python accumulation (R2, R8, R10) | Putting them in `facts.py`, whose every function is one grouped flow `SELECT` (037 R3), would break that module's single shape |
| `analytics/commissions.py` | Commissions come from spec 025's `read_period`, grouped in Python, with no SQL of its own (R11) | A SQL fact would be the second formula FR-130 forbids |
| `analytics/kardex.py` | Paging over any range with value; spec 030's route caps at 366 days and has no value (R6) | Changing spec 030's `kardex_service` would alter its route (not surgical); it reuses `kardex_rows` and `_documents` instead |
| `analytics/customer.py` | One response assembling three engine runs plus four customer figures (FR-125) | Putting it in `engine.py` mixes a composite view into the query core; a client-side assembly would lose the single export |
| `analytics/periods.py` | Relative periods are shared by views and schedules (R14, R15) | Inline in `views.py` would force `schedules.py` to import view internals; no existing helper resolves relative periods |
| `analytics/views.py`, `analytics/schedules.py` | CRUD, validation, claim and send for the new tables (R14–R16) | Spec 037's `exports.py` handles one-off files, not persisted definitions or recurring sends |
| `endpoints/analytics_views.py` | Eleven CRUD routes for views and schedules | Adding them to `analytics.py` mixes CRUD with analytics reads in one router |
| Schemas in `app/schemas/analytics.py` | The request and response shapes of contracts/api.md | Existing spec 037 schemas are extended where they fit (query, detail, catalog); new ones only for new routes |
