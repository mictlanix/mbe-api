# Implementation Plan: Business analytics (sales, margin and payments)

**Branch**: `037-business-analytics` | **Date**: 2026-11-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/037-business-analytics/spec.md`

## Summary

One metrics catalog (19 measures, 15 dimensions) drives a query endpoint, a one-call dashboard, a drill-down endpoint and XLSX/PDF/CSV exports, all under `/api/v1/analytics`. Every number is computed the same way everywhere.

The approach (research R1–R15):
- **No summary tables.** Analytics read the documents directly. A year of lines aggregates in about 0.25 s on mbe_dev, and a daily summary would be 96% the size of the line table (R1).
- **Migration 031:**
  - adds `sales_order.confirmation_time`, filled from `date` for every confirmed order and set by the one confirm path (R2);
  - adds the date indexes every query lacks today (R10);
  - creates the `analytics_export` table for background exports (R12).
- **Four facts** (sale lines, refund lines, CFDI credit notes, payments) are each one grouped SQL statement. Money is in cents, with each document's rounding residual placed on one line, so every grouping adds up and a day reconciles with the sales order screens (R3, R8).
- **Python merges the facts** and computes net and ratio measures, shares, comparisons and top N with "others".
- **Cost includes VAT**, like price (median ratio 1.16 over 4,175 products), so margin is computed without VAT on lines that have a cost (R4).
- **Payments are classified exactly as the cash cut does**, so collected reconciles per session and method (R6).
- **The dashboard runs eight fact queries concurrently** (R9).
- **Exports:** XlsxWriter (new dependency), the existing WeasyPrint printing with inline SVG charts, and the standard library CSV. Over 10,000 rows, an export runs as a background job tracked in the new table (R12).
- **Caching:** a 60-second per-process cache plus ETag (R13).
- **Privileges:** the existing report privileges gate each family. The gross-profit ones (37–39) gate cost and margin (R11).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- FastAPI 0.136.3, Pydantic 2.13.4 and async SQLAlchemy 2.0 (window functions, `@compiles`).
- WeasyPrint and Jinja2 (spec 019).
- **New:** `XlsxWriter` (BSD, pure Python, the current 3.2.x release) for XLSX in constant-memory mode.
- **New, dev only:** `openpyxl`, to read exports back in tests.

**Storage**: MariaDB 10.11 (mbe_dev). Migration `031_business_analytics.sql` (+ rollback) adds:
- one column, filled from the order date;
- four indexes;
- one table.

Export files go in `settings.exports_dir` (`exports/`, not mounted).

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`. Integration tests on SQLite; MariaDB SQL is checked by compiling with `sqlalchemy.dialects.mysql` (the `test_cash_session_service.py` pattern). Reconciliation and performance are checked on mbe_dev (quickstart §3–§4).

**Target Platform**: Linux server (xolotl): dev :8765 (tmux), test :8766 (systemd). Production runs several uvicorn workers, so the cache is per process and export state lives in the database.

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals** (SC-003):
- the dashboard p95 under 500 ms for a month and for a year across all facilities;
- a 12-month query under 2 s;
- a 100,000-row export under 30 s.

**Constraints**:
- **Money:** decimal strings, cents exact; rows add up to totals.
- **Cost and margin:** never reach a caller without 37–39.
- **Facility scope:** as specs 033–035, via 101.
- **Errors:** spec 036 Problems; 8 new `analytics.*` codes.
- **Dialects:** no MariaDB-only SQL in a path the SQLite tests run without its SQLite compile.

**Scale/Scope**:
- **Data:** about 35k orders, 71k lines, 32k payments and 1.7k refunds a year (2025); 5 facilities, 24 salespeople, 1,781 customers and 7,105 products sold.
- **Code:** 7 routes, 1 new table, 1 new column, 4 indexes, about 20 response components.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | No summary tables, refresh jobs or scheduler: the measurements showed them unnecessary (R1). One catalog and one engine serve the query, dashboard, drill-down and exports. Charts are four Jinja SVG macros, not a library. The background export uses an in-process task plus one table, not a queue. | Pass |
| II. Think Before Coding | Every premise of the request was measured before deciding, and four didn't hold: no confirmation date, no summary-table need, cost with VAT, the cash cut measures money not sales. Each departure is in the spec's clarifications or in research (R2, R1, R4, R6). The refund-rate alert threshold is backtested before it ships (R14). | Pass |
| III. Surgical Changes | Outside the new package, the edits are: one line in `_confirm_locked`, one model column, one response field, one `RESOURCE_NAMES` entry, one setting, and new error codes in spec 036's catalog. The duplicate unique key on `sales_order` is noted, not touched (R10). | Pass |
| IV. Goal-Driven Execution | SC-001 (reconciliation), SC-003 (timing) and SC-007 (drill-down sums) are checked on mbe_dev with recorded numbers (quickstart §3–§4). SC-004 and SC-005 are tests. | Pass |
| V. Reuse Over Rebuild | Reused: `totals.line_amounts` formulas (mirrored in SQL and tested against the Python), the `cut_figures` classification, `open_credit_orders` for the overdue alert, `is_credit_customer`, `require_any_privilege`/`has_any_privilege`/`can_see_every_facility`, the rendering pipeline and its filters, `ListResponse` paging conventions, the `app/jobs` CLI pattern. The new dependency and new modules are justified below. | Pass, see Complexity Tracking |
| VI. Async-First | All routes are `async def`. Dashboard queries run concurrently on separate `AsyncSession`s. XLSX writing and PDF rendering run in a worker thread (as `render_pdf` does). | Pass |
| VII. Security by Default | Every route is authenticated and gated by family. Sensitive measures are filtered at the catalog, the engine and every serializer (SC-005 test over all routes). Facility scope is enforced in the engine, not the client. An export is visible only to its owner, and its files are never mounted. | Pass |
| VIII. Ruff Compliance | `uv run ruff check app/ migrations/ tests/` gates each commit. | Pass |
| Testing | Tests are written first per task: unit tests for facts, catalog and merge math; integration on SQLite; API tests including 401/403/404/422 and the privilege sweep. | Pass |
| Changelog | `[Unreleased]` gains Added (analytics routes, `confirmation_time`, `analytics.*` codes, XlsxWriter) and Docs. | Pass |

**Post-design re-check**: unchanged. Phase 1 added one model, one schema module, one endpoint module, one service package, two templates and one job. No other abstraction.

## Project Structure

### Documentation (this feature)

```text
specs/037-business-analytics/
├── spec.md
├── plan.md              # This file
├── research.md          # R1–R15
├── data-model.md        # column, table, facts, catalog, shapes
├── quickstart.md        # local, migration, reconciliation, performance, legacy questions
├── contracts/
│   └── api.md           # routes, schemas (checked by test_contract_docs), error codes
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 031_business_analytics.sql            # NEW  confirmation_time + backfill, 4 indexes, analytics_export
└── 031_business_analytics_rollback.sql   # NEW
app/
├── core/
│   ├── config.py               # + exports_dir
│   ├── error_codes.py          # + 8 analytics.* codes
│   └── errors.py               # + RESOURCE_NAMES['analytics_export']
├── models/
│   ├── sales.py                # + SalesOrder.confirmation_time
│   └── analytics.py            # NEW  AnalyticsExport
├── schemas/
│   ├── sales_order.py          # + confirmation_time
│   └── analytics.py            # NEW  requests, responses, MeasureKey/DimensionKey/Grain enums
├── services/
│   ├── sales_order_service.py  # _confirm_locked sets confirmation_time
│   └── analytics/              # NEW
│       ├── __init__.py
│       ├── catalog.py          # measures, dimensions, applicability, sensitivity
│       ├── sql.py              # period_start / weekday / hour_of, compiled per dialect
│       ├── facts.py            # the four fact queries: line cents with residuals, grouping, filters, scope
│       ├── engine.py           # validate → run facts → merge → derive → shares/comparison/top N
│       ├── dashboard.py        # tiles from concurrent facts; alerts
│       ├── detail.py           # documents behind an aggregate, paged, with sums
│       ├── exports.py          # XLSX/CSV writers, PDF context, background task, purge
│       └── cache.py            # TTL/LRU keyed by request + scope + visibility; ETag
├── api/v1/
│   ├── endpoints/analytics.py  # NEW  7 routes
│   └── router.py               # + include analytics
├── rendering/templates/
│   ├── analytics_report.html   # NEW  query/detail/dashboard PDF
│   └── _charts.html            # NEW  SVG macros: line, bar, donut, heatmap
└── jobs/
    └── purge_exports.py        # NEW  deletes expired export files and rows
tests/
├── unit/
│   ├── test_analytics_catalog.py
│   ├── test_analytics_facts.py     # SQL cents == totals.py; MariaDB compile
│   ├── test_analytics_engine.py    # merge, ratios, shares, comparison, top N, others
│   ├── test_analytics_sql.py       # grain/weekday/hour on SQLite and compiled for MariaDB
│   ├── test_analytics_cache.py
│   ├── test_analytics_dashboard.py  # sparkline grain, refund-rate rule
│   ├── test_analytics_detail.py     # sort, flags, cost-key exclusion
│   └── test_analytics_exports.py    # inline/background threshold, widths, formats
├── integration/
│   ├── test_analytics_query.py
│   ├── test_analytics_dashboard.py
│   ├── test_analytics_detail.py
│   ├── test_sales_order_confirmation_time.py
│   └── test_purge_exports.py
└── api/
    ├── test_analytics.py
    └── test_analytics_exports.py
```

**Structure Decision**: The analytics code is a package under `app/services/analytics/`, because it is one feature with several cooperating modules (catalog, facts, engine) rather than one service per document. Routes, schemas and the model follow the existing one-module-per-area layout.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| XlsxWriter dependency | FR-051 asks for typed cells, number formats, a frozen header, autofilter and streaming writes for 100k rows. | openpyxl write-only is slower and weaker on formatting; CSV renamed `.xlsx` isn't a workbook. |
| `analytics_export` table and migration | Background exports must be visible to whichever worker answers the poll (several uvicorn workers) and keep their owner and expiry. | In-memory job state breaks with several workers; a filesystem sidecar duplicates a table with less safety. |
| `app/services/analytics/` package (8 modules) | One engine shared by query, dashboard, detail and exports is what makes the numbers match (FR-050). | A service per report would rebuild the 38-report model the spec replaces. |
| Window functions in SQL | The cents-and-residual rule (R3) keeps every grouping reconciled to the cent with plain sums. | Python rounding would fetch every line per request, and rounding per group breaks FR-012. |
| Per-process cache | FR-021; the dashboard's year view is the heaviest request. | No cache: acceptable by the measurements, but repeated dashboard loads would re-run eight queries. A shared cache server is new infrastructure. |
