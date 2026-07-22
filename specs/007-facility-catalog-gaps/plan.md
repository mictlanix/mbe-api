# Implementation Plan: Facility Catalog Gaps

**Branch**: `010-open-issues-fixes` | **Date**: 2026-07-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-facility-catalog-gaps/spec.md`

## Summary

Three unrelated fixes delivered together because they were reported together against one
client screen:

1. `/api/v1/taxpayer-issuers` — a full CRUD resource over `taxpayer_issuer`, which had a model
   but no endpoint, gated by `SystemObject.TAXPAYERS` (24). `regime` and `postal_code` expand
   to SAT catalog objects; `provider` is exposed through a newly ported
   `FiscalCertificationProvider` enum.
2. `FacilityResponse.address` expands to the full address object, batch-fetched in the pass
   that already expands `location`. `FacilitySummary` stays flat.
3. `create_point_sale` / `update_point_sale` reject a warehouse belonging to another facility.

Fixes GitHub issues #100, #101, #102.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 — no new dependencies

**Storage**: MariaDB via aiomysql. No schema change: `taxpayer_issuer` already exists and is
already modelled; only its API surface is new.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — one new endpoint module plus service/schema changes

**Performance Goals**: The address expansion must not add a query per facility — it joins the
existing `batch_fetch` pass, so a list of any length costs one extra query, not N.

**Constraints**:
- `FacilityResponse.address` is a breaking field-shape change; no transitional form.
- `FacilitySummary` must stay flat, or every warehouse/point-of-sale/cash-drawer listing pays
  for an address lookup it does not use.
- The point-of-sale rule must read raw foreign keys, which the existing expansion was
  overwriting — see research R3.

**Scale/Scope**: 1 new endpoint module, 1 new service, 1 new schema module, 1 new enum; 3
existing services changed; 4 test files added or updated.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ | The address expansion reuses the existing `batch_fetch` + `_attach_relations` pass rather than adding a mechanism. The point-of-sale rule is one helper called from two places. |
| II. Think Before Coding | ✅ | Two questions were put to the product owner before implementing: whether a cross-facility warehouse is ever legitimate, and how far the address expansion should go. Both answers are recorded in spec Assumptions; the second reversed the reporter's stated request. |
| III. Surgical Changes | ✅ | The identity-map fix in `point_sale_service` is the one change not traceable to a reported issue — it is a prerequisite for FR-011, documented in research R3. |
| IV. Goal-Driven Execution | ✅ | Success = quickstart scenarios: suite green, OpenAPI carries the new resource, address expanded, mismatched pairing refused. |
| V. Reuse Over Rebuild | ✅ | `taxpayer_issuer_service` follows `taxpayer_recipient_service`; the endpoint follows the existing CRUD shape; expansion reuses `fk_expansion.batch_fetch`. New schema module `app/schemas/fiscal.py` mirrors the existing one-module-per-model-file convention. |
| VI. Async-First | ✅ | All new handlers and services are `async def` over `AsyncSession`. |
| VII. Security by Default | ✅ | Every new route is gated by `require_privilege(SystemObject.TAXPAYERS, …)` — see research R2 for why this is stricter than the sibling resource it was modelled on. |
| VIII. Ruff Compliance | ✅ | `uv run ruff check app/ tests/` gates completion. |

**Testing rule**: New endpoints are introduced, so the constitution's mandatory-test rule
binds. `tests/api/test_taxpayer_issuers.py` is added alongside them.

## Project Structure

### Documentation (this feature)

```text
specs/007-facility-catalog-gaps/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
├── contracts/
│   ├── taxpayer-issuers.md
│   └── facility-and-point-of-sale.md
└── tasks.md
```

### Source Code (repository root)

```text
app/
├── enums.py                          # ADD FiscalCertificationProvider
├── models/fiscal.py                  # TaxpayerIssuer.provider typed with the enum
├── schemas/
│   ├── fiscal.py                     # NEW: TaxpayerIssuerCreate/Update/Response
│   └── core.py                       # FacilityResponse.address → AddressResponse;
│                                     #   PointSaleResponse reads *_detail aliases
├── services/
│   ├── taxpayer_issuer_service.py    # NEW: list/get/create/update/delete
│   ├── facility_service.py           # + address batch-fetch into address_detail
│   └── point_sale_service.py         # + facility/warehouse rule; expansion → *_detail
└── api/v1/
    ├── endpoints/taxpayer_issuers.py # NEW
    └── router.py                     # register /taxpayer-issuers

tests/
├── api/test_taxpayer_issuers.py      # NEW
├── api/test_facilities.py            # address now expanded in fixtures
├── unit/test_point_sale_service.py   # NEW: the pairing rule
└── unit/test_fk_expansion_isolation.py  # address expansion leaves the raw key intact
```

**Structure Decision**: Existing single-service layout. `app/schemas/fiscal.py` is new because
`TaxpayerIssuer` lives in `app/models/fiscal.py` and the project pairs schema modules to model
modules.

## Complexity Tracking

*No constitution violations. The new service, schema module and endpoint module are the
feature's stated purpose, and each mirrors an existing counterpart rather than introducing a
pattern.*
