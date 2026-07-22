---

description: "Task list for Facility Catalog Gaps"
---

# Tasks: Facility Catalog Gaps

**Input**: Design documents from `/specs/007-facility-catalog-gaps/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — new endpoints are introduced, so the constitution's mandatory-test rule
binds.

**Organization**: Grouped by user story. The three stories are wholly independent; they shipped
together only because they were reported together.

**Status**: All tasks complete — delivered in PR #103, commits `1276c71` and `212a0e4`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 taxpayer entities, US2 address expansion, US3 pairing rule

## Path Conventions

Single project at repository root: `app/`, `tests/`.

---

## Phase 1: Setup

- [x] T001 Confirm with the product owner whether a cross-facility warehouse is ever legitimate, and how far the address expansion should go; record both in `specs/007-facility-catalog-gaps/spec.md` Assumptions

---

## Phase 2: Foundational

**Purpose**: Nothing blocks all three stories. Each story carries its own groundwork.

*(No shared prerequisites — the stories touch disjoint files.)*

---

## Phase 3: User Story 1 — Choosing a facility's taxpayer (P1) 🎯 MVP

**Goal**: A facility's taxpayer becomes selectable, searchable and displayable.

**Independent test**: Search the catalog, select an entry, and render an existing facility's
taxpayer as text.

### Tests for User Story 1

- [x] T002 [P] [US1] Cover list, search pass-through, get, and 404 in `tests/api/test_taxpayer_issuers.py`
- [x] T003 [P] [US1] Cover create, update, and rejection of a short identifier and an unknown provider in `tests/api/test_taxpayer_issuers.py`
- [x] T004 [P] [US1] Cover the delete guard refusing while referenced in `tests/unit/test_taxpayer_issuer_service.py`

### Implementation for User Story 1

- [x] T005 [US1] Port `FiscalCertificationProvider` from the legacy constants into `app/enums.py`
- [x] T006 [US1] Type `TaxpayerIssuer.provider` with the enum in `app/models/fiscal.py`, without adding a server default the database does not have
- [x] T007 [US1] Create `app/schemas/fiscal.py` with create, update and response shapes, expanding `regime` and `postal_code` under separate detail keys
- [x] T008 [US1] Create `app/services/taxpayer_issuer_service.py` with list/search, get, create, update and a reference-guarded delete
- [x] T009 [US1] Create `app/api/v1/endpoints/taxpayer_issuers.py`, gating every route on `SystemObject.TAXPAYERS`
- [x] T010 [US1] Register the router at `/taxpayer-issuers` in `app/api/v1/router.py`
- [x] T011 [US1] Note in `docs/constants.md` that `FiscalCertificationProvider` now lives in `app/enums.py`
- [x] T012 [US1] Record the permission-granting prerequisite in `CHANGELOG.md`, since the permission previously governed nothing

**Checkpoint**: Taxpayer entities are listable, searchable, editable and safely deletable.

---

## Phase 4: User Story 2 — Seeing a facility's address inline (P2)

**Goal**: A facility carries its full address wherever the facility is the subject.

**Independent test**: Request the facility list; render addresses with no further requests.

### Tests for User Story 2

- [x] T013 [P] [US2] Update the facility fixtures to carry an expanded address in `tests/api/test_facilities.py`
- [x] T014 [P] [US2] Assert the expansion leaves the stored address key intact in `tests/unit/test_fk_expansion_isolation.py`

### Implementation for User Story 2

- [x] T015 [US2] Batch-fetch addresses into `address_detail` in the existing relation pass in `app/services/facility_service.py`
- [x] T016 [US2] Point `FacilityResponse.address` at the address shape via an alias in `app/schemas/core.py`, leaving `FacilitySummary.address` a bare key
- [x] T017 [US2] Record the breaking field-shape change in `CHANGELOG.md`

**Checkpoint**: Facility screens need no address round trips.

---

## Phase 5: User Story 3 — Preventing a mismatched point of sale (P3)

**Goal**: The facility/warehouse rule is enforced for every client, not just the one that
chose to.

**Independent test**: Save a mismatched pair (refused); change only the facility on a valid
point of sale (also refused).

### Tests for User Story 3

- [x] T018 [P] [US3] Cover creation refusal, unknown warehouse, amendment refusal, facility-only amendment, and the no-op skip in `tests/unit/test_point_sale_service.py`

### Implementation for User Story 3

- [x] T019 [US3] Move the relation expansion to `facility_detail` / `warehouse_detail` in `app/services/point_sale_service.py`, so the raw keys the rule compares survive the fetch
- [x] T020 [US3] Read the expansion through aliases on `PointSaleResponse` in `app/schemas/core.py`, leaving the response shape unchanged
- [x] T021 [US3] Add the pairing check and call it from create and amend in `app/services/point_sale_service.py`, validating the resulting pair and distinguishing a missing warehouse from a mismatched one
- [x] T022 [US3] Record the new rule in `CHANGELOG.md`

**Checkpoint**: The rule holds at the interface, and the client can relax its own guard.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T023 [P] Verify real records still serialize for facilities, warehouses, points of sale and taxpayer entities (quickstart step 3)
- [x] T024 [P] Verify the pairing rule end to end inside a rolled-back transaction (quickstart step 4)
- [x] T025 [P] Confirm the new resource and the address `$ref` appear in the published contract (quickstart step 2)
- [x] T026 [P] Pass ruff and hold mypy at its baseline
- [x] T027 File the wider identity-map audit uncovered by T019 as a separate issue rather than widening this change

---

## Dependencies & Execution Order

- **Setup (T001)**: blocks US2 and US3 — both encode a decision the product owner had to make
- **US1 (T002–T012)**: no dependency on the other stories. T005 → T006 → T007 → T008 → T009 → T010 is a chain; the tests are parallel
- **US2 (T013–T017)**: independent. T015 and T016 must land together or responses break
- **US3 (T018–T022)**: T019 **must** precede T021 — the rule cannot read raw keys the expansion was overwriting (research R3)
- **Polish (T023–T027)**: after all stories

### Story independence

Fully independent — different files, different endpoints. Any one could have shipped alone.

### Parallel opportunities

- The three stories could run concurrently in their entirety
- T002–T004, T013–T014, T023–T026 are parallel within their phases

---

## Implementation Strategy

**MVP**: US1. It is the only story that unblocks an operator mid-task; the other two have
working client-side workarounds.

**Then**: US3 (closes a rule no client can be trusted to hold), then US2 (deletes a workaround).

**Delivered as**: one PR (#103), because the three issues were reported together against one
client screen and share a release note.
