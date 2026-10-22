# Implementation Plan: Commissions with Stable History

**Branch**: `025-commissions-history` | **Date**: 2026-10-22 (re-planned after the scheme clarifications) | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/025-commissions-history/spec.md`

## Summary

- **Scope document:** `docs/specs/02-sales.md` gains **§10 Commissions** (#32).
- **Settings:** CRUD over the five shared settings tables (#33). Agents gain a **scheme**: 1 for fully paid orders, the current system's rule; 2 for proportional commission on partial payments.
- **Computation:** two stages.
  - **SQL facts:** candidate lines, applied and refunded amounts per order, refund lines, stored lines.
  - **Pure rules:** the participation branches; scheme 1 (full commission in the paid month); scheme 2 (the cumulative share net of refunds, minus what was stored); refunds as negative lines charged to the agent who was paid; a cap that never pays a share twice.
- **Periods:** calendar months that **close in sequence**, freezing every line into `commission_period_line`. The latest closed month can be **reopened**, and a closed month can be **paid**. Closed months never change.
- **Payments:** the payment path stamps `balance_zeroed_time`, and migration 023 backfills recent paid orders and adds the scheme column.

The routes are specified in [contracts/commissions.md](contracts/commissions.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 (no new dependency)

**Storage**: MariaDB `mbe_dev`. **Migration 023**:
- `commission_agent.scheme` (default 1);
- tables `commission_period` and `commission_period_line`;
- the `balance_zeroed_time` backfill (38 rows).

Settings tables shared with the current system. `commissions_history` untouched.

**Testing**: pytest + pytest-asyncio + httpx. Unit tests for the pure rules, including a randomized invariant test; integration tests on the SQLite schema.

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: a month is 3 fact queries plus 1 stored-line query, then in-memory rules over about 3,000 orders. A close inserts 1,000–2,000 rows in one transaction. A closed month is read with a single indexed query.

**Constraints**:
- Scheme 1 parity with the current system's report (SC-002), apart from the two date changes.
- Closed months never change (SC-001).
- No share paid twice (SC-006).
- The current system's tables change only by the defaulted `scheme` column and the `balance_zeroed_time` stamp and backfill.

**Scale/Scope**: 1 migration, 2 new models plus 1 column, 2 new service modules (`commission_rules`, which is pure, and `commission_service`), 1 router with about 22 routes, 1 change in `customer_payment_service`, 1 enum value, docs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | Stored lines are both the history and the ledger the cap reads; there is no second ledger. The rules live in one pure function. No configurability beyond the per-agent scheme the user asked for. | PASS |
| II. Think Before Coding | The clarifications are recorded, and planning corrections are written back into the spec: NULL csp earns nothing; the bounded backfill; refund dates; the sequential closing (FR-010a) found while designing scheme 2; the first close as the chain's start. | PASS |
| III. Surgical Changes | Existing code changes only in `_sync_paid_flag` and `SourceType`. Everything else is new, plus the migration and docs. | PASS |
| IV. Goal-Driven Execution | The quickstart test matrix, the SC-002 parity check on mbe_dev, the SC-006 invariant test and the migration counts. | PASS |
| V. Reuse Over Rebuild | Reuses the settings models, `require_privilege`, `incidences.record`, the IntegrityError → 409 handler and `local_now`. Scheme 1 reproduces the legacy query's rules. New additions are justified below. | PASS |
| VI. Async-First | All DB access is async. The rules are pure and synchronous over loaded data, with no I/O. | PASS |
| VII. Security by Default | SystemObject 112 on every route. This is stricter than the current system's login-only report (5 of 33 non-admin users hold Read). | PASS |
| VIII. Ruff Compliance | Gate. | PASS |
| Testing | Unit tests for every branch of the rules, plus an invariant test. Integration tests for the facts and periods. Route tests (200/401/403/404/409/422). A stamp test. Tests are written first. | PASS |
| Changelog | Added (commissions API, schemes) and Changed (`balance_zeroed_time` stamped on payment), with an upgrade note for migration 023 and the first close. | PASS |
| Migrations | Numbered, with a rollback. The data dictionary and schema dump are updated in the same change. Confirm with the user before applying to mbe_dev. | PASS |

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
├── 023_commission_periods.sql            # scheme column, 2 tables, backfill
└── 023_commission_periods_rollback.sql
app/
├── models/commission.py                  # + CommissionAgent.scheme, CommissionPeriod, CommissionPeriodLine
├── enums.py                              # + SourceType.COMMISSION = 12, CommissionScheme, CommissionPeriodStatus, CommissionMovement
├── schemas/commission.py                 # new
├── services/
│   ├── commission_rules.py               # new, pure: participation keys, scheme 1/2 amounts, refunds, cap
│   ├── commission_service.py             # new: settings CRUD, facts (SQL), compute, close/reopen/pay, read
│   └── customer_payment_service.py       # _sync_paid_flag stamps balance_zeroed_time
└── api/v1/
    ├── endpoints/commissions.py          # new router
    └── router.py                         # include /commissions

tests/
├── unit/test_commission_rules.py
├── unit/test_customer_payment_service.py   # stamp cases
├── integration/test_commission_compute.py
├── integration/test_commission_periods.py
├── api/test_commission_settings.py
└── api/test_commission_periods.py

docs/specs/02-sales.md (§10), docs/specs/11-reports.md (§37 note), docs/data-dictionary.md,
docs/mbe_schema.sql, docs/constants.md, specs/011-sales-cycle-endpoints/spec.md (FR-065a note), CHANGELOG.md
```

**Structure Decision**: single web-service project. The rules are split from the service, so the money logic, with its schemes, cap, refunds and key freeze, is a pure, exhaustively testable function. The service owns I/O and transactions.

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| Tables `commission_period`, `commission_period_line` (+ models) | Stable history means frozen copies of each line (FR-007), and the cap and key freeze must read what was paid (FR-017, FR-009a) | `commissions_history` is stale and ruled out; computing live is the instability this feature removes |
| Column `commission_agent.scheme` | The scheme is chosen per agent (clarification) | A separate table adds a join for one value; the defaulted column is invisible to the current system |
| `commission_rules.py` separate from `commission_service.py` | The cross-row rules (schemes, cap, refunds, freeze) need direct, exhaustive tests without a database | Inlining them in the service makes every rules test an integration test |
| New schemas `app/schemas/commission.py` | Typed bodies for about 22 routes | No existing schema covers commissions |
| `SourceType.COMMISSION = 12` | SC-004 asks that settings changes record who made them | Per-table audit columns would alter more shared tables |
| Migration 023 backfill | 38 recent paid orders would otherwise earn nothing (FR-008a) | Not backfilling silently drops them |
