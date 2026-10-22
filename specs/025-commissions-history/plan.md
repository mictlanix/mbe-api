# Implementation Plan: Commissions with Stable History

**Branch**: `025-commissions-history` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/025-commissions-history/spec.md`

## Summary

- **Scope document:** `docs/specs/02-sales.md` gains **§10 Commissions**, covering the current system and the new rules (#32).
- **Settings API:** CRUD over the five shared settings tables already mapped in `app/models/commission.py` (#33).
- **Computation:** a port of the current system's post-#58 commission query, dated by the stable `balance_zeroed_time` and refund `date`.
- **Periods:** calendar-month periods that **close** (freezing every computed line into the new `commission_period_line`), **reopen** while unpaid, and are **paid**. Reading a closed or paid month never recomputes.
- **Payments:** the payment path now stamps `balance_zeroed_time`, and migration 023 backfills recent paid orders.

The routes are specified in [contracts/commissions.md](contracts/commissions.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 (no new dependency)

**Storage**: MariaDB `mbe_dev`. **Migration 023** adds `commission_period` and `commission_period_line`, and backfills `sales_order.balance_zeroed_time` for 38 recent rows. Settings tables shared with the current system. `commissions_history` untouched.

**Testing**: pytest + pytest-asyncio + httpx; integration tests on the SQLite template schema, which must gain the two new tables through the models.

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: computing a month is one SQL statement over about 3,000 paid orders. Closing inserts about 1,000–2,000 lines in one transaction. Reading a closed month is a single indexed query.

**Constraints**:
- Parity with the current system's report (SC-002), apart from the two documented date changes.
- Closed months never change (SC-001).
- No change to the current system's tables beyond the `balance_zeroed_time` stamp and backfill.

**Scale/Scope**: 1 migration, 2 new models, 1 new service, 1 new router with about 20 routes, 1 change in `customer_payment_service`, 1 enum value, docs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | One service, `commission_service`, and one router. The computation is one ported SQL statement rather than re-derived rules. Periods are a status row plus frozen lines. No configurability. | PASS |
| II. Think Before Coding | The clarifications are recorded. Research records the corrections found while planning: a NULL customer salesperson earns nothing; the stamp began in 2025, hence the bounded backfill; refunds are dated by `date`. | PASS |
| III. Surgical Changes | Existing code changes only in `_sync_paid_flag` (the stamp) and `SourceType`. Everything else is new files, docs and the migration. | PASS |
| IV. Goal-Driven Execution | Tests per quickstart.md, the parity check SC-002 against mbe_dev, and the migration row counts. | PASS |
| V. Reuse Over Rebuild | Reuses the settings models, `require_privilege`, `incidences.record`, the IntegrityError → 409 handler, `local_now`, and the legacy query's logic. New tables, models, schemas and service are justified under Complexity Tracking. | PASS |
| VI. Async-First | All DB work through `AsyncSession`, and `text()` for the computation. | PASS |
| VII. Security by Default | Every route is gated by SystemObject 112. This is stricter than the current system's login-only report (5 of 33 non-admin users hold Read). | PASS |
| VIII. Ruff Compliance | Gate. | PASS |
| Testing | Endpoint tests for each route (happy path, 404, 401, 403, 409, 422). Integration tests for computation and period stability. A unit test for the stamp. Tests are written first. | PASS |
| Changelog | Added (commissions API) and Changed (`balance_zeroed_time` stamped on payment), with an upgrade note on migration 023. | PASS |
| Migrations | A numbered migration plus rollback; `docs/data-dictionary.md` and `docs/mbe_schema.sql` updated in the same change. Confirm with the user before applying it to mbe_dev. | PASS |

Re-check after Phase 1 design: unchanged, all PASS.

## Project Structure

### Documentation (this feature)

```text
specs/025-commissions-history/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/commissions.md
├── checklists/requirements.md
└── tasks.md              # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 023_commission_periods.sql            # tables + balance_zeroed_time backfill
└── 023_commission_periods_rollback.sql
app/
├── models/commission.py                  # + CommissionPeriod, CommissionPeriodLine
├── enums.py                              # + SourceType.COMMISSION = 12, CommissionPeriodStatus
├── schemas/commission.py                 # new: settings and period schemas
├── services/
│   ├── commission_service.py             # new: settings CRUD, compute, close/reopen/pay, read
│   └── customer_payment_service.py       # _sync_paid_flag stamps balance_zeroed_time
└── api/v1/
    ├── endpoints/commissions.py          # new router
    └── router.py                         # include /commissions

tests/
├── api/test_commission_settings.py
├── api/test_commission_periods.py
├── integration/test_commission_compute.py
├── integration/test_commission_periods.py
└── unit/test_customer_payment_service.py  # stamp cases

docs/specs/02-sales.md (§10), docs/specs/11-reports.md (§37 note), docs/data-dictionary.md,
docs/mbe_schema.sql, docs/constants.md, specs/011-sales-cycle-endpoints/spec.md (FR-065a note), CHANGELOG.md
```

**Structure Decision**: single web-service project. One service module owns all of commissions. The computation SQL lives in it as a module constant, with a comment citing the legacy source lines and the three deliberate differences.

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| Tables `commission_period`, `commission_period_line` (and models) | Stable history means frozen copies of each paid line (FR-007), and nothing in the schema holds them | `commissions_history` is stale and ruled out; computing live is the instability this feature removes |
| New service `commission_service` and router | No commissions API exists | Spreading it across the sales services would mix unrelated concerns |
| New schemas `app/schemas/commission.py` | Typed requests and responses for about 20 routes | No existing schema covers commissions |
| `SourceType.COMMISSION = 12` | SC-004 asks that settings changes record who made them, and `incidence` is the project's audit log | A per-table `updated_by` column would alter the shared legacy tables |
| Migration 023 backfill of `balance_zeroed_time` | Recent paid orders without the stamp would earn nothing (FR-008a) | Not backfilling silently drops 38 recent orders from commissions |
