# Implementation Plan: Local Datetime Contract

**Branch**: `018-local-datetime-contract` | **Date**: 2026-09-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/018-local-datetime-contract/spec.md`

## Summary

Resolve #228 with option B. Every datetime stays naive Mexico City wall-clock time, but that now comes from one required setting (`BUSINESS_TIMEZONE`) instead of the host clock. Incoming datetimes with an offset are converted at the schema boundary instead of having the offset silently dropped by the database driver. Every date-time field in the OpenAPI schema says what it means. One annotated type, `LocalDateTime`, does both the conversion and the documentation. Three helpers in `app/core/clock.py` replace every host-clock read. No data or wire format changes.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI 0.136, pydantic 2.13, pydantic-settings 2.14, `tzdata` (already present)

**Storage**: MariaDB via aiomysql, naive `DATETIME` columns shared with the legacy C# monolith. No schema change.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`

**Target Platform**: Linux server, any host timezone

**Project Type**: web service (REST API)

**Performance Goals**: none new. One timezone conversion per datetime field in a request.

**Constraints**: must stay compatible with the legacy monolith's naive Mexico City timestamps. Response wire format must not change.

**Scale/Scope**: 1 new module, 1 setting, ~58 call sites in 13 files, `datetime` annotations in 10 schema files and 4 endpoint files, 2 env files, 2 new test files.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | Pass | Three functions and one annotated type. No class, no middleware, no data migration. |
| II. Think Before Coding | Pass | Option B, the required setting and no backfill were decided by the user. The `format: date-time` trade-off is recorded in research R4. |
| III. Surgical Changes | Pass | Only host-clock reads and `datetime` annotations change. `csd_service` and `security.py` are deliberately left alone (R2). |
| IV. Goal-Driven Execution | Pass | Each FR maps to a test in research R5. |
| V. Reuse Over Rebuild | Pass | The new module is justified below. It reuses pydantic's `ZoneInfo` validation and the `astimezone().replace(tzinfo=None)` pattern from `csd_service.py`. |
| VI. Async-First | Pass | No I/O added. |
| VII. Security by Default | Pass | No endpoint or auth change. JWT expiry untouched. |
| VIII. Ruff Compliance | Pass | Gate runs before commit. |
| Testing | Pass | Tests first: clock and contract unit tests plus boundary API tests (R5). |
| Changelog | Pass | `[Unreleased]` gets a Changed entry, including the upgrade note that `BUSINESS_TIMEZONE` is now required. |

**Post-design re-check**: still passes. The design added no dependency, no model and no table.

## Project Structure

### Documentation (this feature)

```text
specs/018-local-datetime-contract/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── datetime-convention.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── core/
│   ├── config.py            # + business_timezone: ZoneInfo (required)
│   └── clock.py             # NEW: local_now, local_today, to_local
├── schemas/
│   ├── __init__.py          # + LocalDateTime annotated type
│   └── *.py (10 files)      # datetime → LocalDateTime; core.py date.today() → local_today()
├── api/v1/endpoints/
│   ├── customer_payments.py # date_from/date_to → LocalDateTime
│   ├── delivery_orders.py
│   ├── cash_sessions.py
│   └── sales_orders.py
└── services/ (12 files)     # datetime.now()/date.today()/UTC now → local_now()/local_today()

tests/unit/
├── test_clock.py            # NEW: helpers + required setting
└── test_datetime_contract.py # NEW: schema descriptions + no stray clock reads
tests/api/                   # + offset round-trip on a sales order and a list filter

.env, .env.example           # + BUSINESS_TIMEZONE=America/Mexico_City
CHANGELOG.md
```

**Structure Decision**: existing single-project layout. The clock helpers go in `app/core/` next to `config.py`, whose setting they read. `LocalDateTime` goes in `app/schemas/__init__.py`, which already holds the shared schema descriptions.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|--------------------------------------|
| `app/core/clock.py` (new module) | One place that reads the business timezone for "now", "today" and conversion | Inlining `datetime.now(tz).replace(tzinfo=None)` at 58 sites is how `vehicle_operator_service` already drifted to UTC |
