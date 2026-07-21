---

description: "Task list for Constraint Violation Handling"
---

# Tasks: Constraint Violation Handling

**Input**: Design documents from `/specs/006-constraint-violation-handling/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. The feature *is* error behaviour, so it cannot be verified by inspection.

**Organization**: Grouped by user story so each is independently implementable and testable.

**Status**: All tasks complete — delivered in PR #108, commit `12a0078`. Checked boxes record
what shipped, not a plan awaiting execution.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story the task serves (US1, US2, US3)

## Path Conventions

Single project at repository root: `app/`, `tests/`.

---

## Phase 1: Setup

**Purpose**: Establish the ground truth this feature is built on.

- [x] T001 Audit inbound foreign keys and unique constraints against the live database, recording per-endpoint exposure in `specs/006-constraint-violation-handling/research.md`
- [x] T002 Confirm the exposure with row counts on real data (facilities, warehouses, employees, suppliers, addresses) in `specs/006-constraint-violation-handling/research.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared guard every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until T003–T005 are complete.

**Policy this implements** (spec FR-013 to FR-015, decided by the product owner): deletes are
**hard deletes** — a refused delete changes nothing, and the guard must never fall back to a
soft delete, a status change, or a cascade. Archiving stays an explicit operator action on
`status`. If a future task appears to need "just archive it instead", that is a product
decision to reopen, not an implementation shortcut to take.

- [x] T003 Create `app/services/references.py` with `find_blocking_references(db, instance, *, exempt)` deriving referencing tables from `Base.metadata` and counting them in one `UNION ALL` query
- [x] T004 Import `app.models` in `app/services/references.py` so every table is registered before metadata is scanned, regardless of import order
- [x] T005 Add `assert_not_referenced(db, instance, *, exempt)` to `app/services/references.py`, raising 409 with blockers ordered by count and truncated past five

---

## Phase 3: User Story 1 — Deleting a record that is still in use (P1) 🎯 MVP

**Goal**: A delete that cannot proceed says so, and says what is in the way.

**Independent test**: Delete any referenced record; confirm 409, the blocking relationships
and counts in the message, and the record still present.

### Tests for User Story 1

- [x] T006 [P] [US1] Cover ordering, zero-count filtering and truncation in `tests/unit/test_references.py`
- [x] T007 [P] [US1] Cover the `table.column` labelling of a table that references the same target twice in `tests/unit/test_references.py`
- [x] T008 [P] [US1] Cover exempt tables and the unsaved-instance short circuit in `tests/unit/test_references.py`
- [x] T009 [P] [US1] Cover a referenced delete returning 409 with named blockers in `tests/api/test_constraint_errors.py`

### Implementation for User Story 1

- [x] T010 [US1] Label blockers `table.column` rather than by table in `app/services/references.py`, so a table referencing the same target twice is unambiguous
- [x] T011 [P] [US1] Guard the delete in `app/services/employee_service.py`
- [x] T012 [P] [US1] Guard the delete in `app/services/facility_service.py`
- [x] T013 [P] [US1] Guard the delete in `app/services/warehouse_service.py`
- [x] T014 [P] [US1] Guard the delete in `app/services/address_service.py`
- [x] T015 [P] [US1] Guard the delete in `app/services/supplier_service.py`
- [x] T016 [P] [US1] Guard the delete in `app/services/point_sale_service.py`
- [x] T017 [P] [US1] Guard the delete in `app/services/cash_drawer_service.py`
- [x] T018 [P] [US1] Guard the delete in `app/services/vehicle_service.py`
- [x] T019 [P] [US1] Guard the delete in `app/services/label_service.py`
- [x] T020 [P] [US1] Guard the delete in `app/services/vehicle_operator_service.py`
- [x] T021 [P] [US1] Guard the delete in `app/services/taxpayer_recipient_service.py`
- [x] T022 [P] [US1] Guard the delete in `app/services/payment_method_option_service.py`
- [x] T023 [US1] Guard the delete in `app/services/customer_service.py`, keeping the existing default-customer rule ahead of it
- [x] T024 [US1] Guard the delete in `app/services/product_service.py`, exempting `product_price` per the closed cascade list
- [x] T025 [US1] Guard the delete in `app/services/user_service.py`, exempting `user_settings` and `access_privilege`
- [x] T026 [US1] Replace the bespoke guard in `app/services/price_list_service.py`, which checked only `customer` and missed `product_price`
- [x] T027 [US1] Replace the hand-written four-table guard in `app/services/taxpayer_issuer_service.py` with the shared one
- [x] T028 [US1] Rewrite `tests/unit/test_taxpayer_issuer_service.py` to pin the wiring rather than the removed counting implementation

**Checkpoint**: Referenced deletes are refused with actionable messages. Shippable alone.

---

## Phase 4: User Story 2 — Reusing a code that already exists (P2)

**Goal**: A duplicate code reads as a field-level conflict, not a server fault.

**Independent test**: Create a record reusing a code (expect a named 409); re-save an existing
record unchanged (expect success).

### Tests for User Story 2

- [x] T029 [P] [US2] Cover a duplicate code returning 409 with the field named in `tests/api/test_constraint_errors.py`

### Implementation for User Story 2

- [x] T030 [US2] Add `assert_unique(db, model, column, value, *, exclude_pk, label)` to `app/services/references.py`, excluding the row being updated by primary key
- [x] T031 [P] [US2] Check `code` on create and update in `app/services/warehouse_service.py`
- [x] T032 [P] [US2] Check `code` on create and update in `app/services/point_sale_service.py`
- [x] T033 [P] [US2] Check `code` on create and update in `app/services/cash_drawer_service.py`
- [x] T034 [P] [US2] Check `license_plate` on create and update in `app/services/vehicle_service.py`
- [x] T035 [US2] Update the mocked session in `tests/unit/test_point_sale_service.py` so the new pre-check finds nothing and the cross-FK assertions still hold

**Checkpoint**: The four unguarded unique constraints now produce named conflicts.

---

## Phase 5: User Story 3 — No conflict appears as a server fault (P3)

**Goal**: Whatever is not checked up front still never reaches the client as a 500.

**Independent test**: Raise a constraint no service checks; confirm a generic 409 carrying no
internal detail.

### Tests for User Story 3

- [x] T036 [P] [US3] Cover an unchecked `IntegrityError` becoming a 409 in `tests/api/test_constraint_errors.py`
- [x] T037 [P] [US3] Assert the driver message, index name and error number never appear in the response body in `tests/api/test_constraint_errors.py`

### Implementation for User Story 3

- [x] T038 [US3] Register an `IntegrityError` exception handler in `app/main.py` returning a generic 409
- [x] T039 [US3] Log the driver message with method and path in `app/main.py` instead of returning it

**Checkpoint**: The guarantee is total, including for endpoints added later.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T040 [P] Verify the guard against live data: blockers found per entity, and counts cross-checked against raw SQL (quickstart step 4)
- [x] T041 [P] Confirm the generated OpenAPI document is byte-identical to `main` (SC-007, quickstart step 2)
- [x] T042 [P] Hold mypy at its pre-existing baseline with no errors in `app/services/references.py` or `app/main.py`
- [x] T043 [P] Pass `ruff check` and `ruff format --check` over `app/` and `tests/`
- [x] T044 Record the behaviour and the closed cascade exemption in `CHANGELOG.md`

---

## Requirements with no task, by design

Two requirements are satisfied by *not* building something, so they have no task and their
coverage cannot be shown by pointing at one. Recorded here so the gap reads as deliberate
rather than forgotten:

| Requirement | Why no task | How it stays true |
|-------------|-------------|-------------------|
| **FR-013** — a successful delete removes the record outright, never substituting a hidden state change | Nothing was built to change state on delete | The absence is load-bearing. Any future task adding a status write to a delete path breaks it |
| **FR-015** — archiving is an explicit user action, never a side effect of a delete | `status` already exists (feature 005) and is untouched here | Guaranteed by the guard raising and returning, with no fallback branch |

Both are restated at the head of Phase 2 so they are visible to anyone editing the guard.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001–T002)**: no dependencies — but T001 is what sized every later phase
- **Foundational (T003–T005)**: depends on Setup. **Blocks every user story**
- **US1 (T006–T028)**: depends on Foundational. Independent of US2 and US3
- **US2 (T029–T035)**: depends on Foundational only. T030 extends `references.py` but touches no US1 behaviour
- **US3 (T036–T039)**: depends on nothing but Setup — `app/main.py` is untouched by the other stories
- **Polish (T040–T044)**: depends on all stories

### Story independence

All three stories are independently deliverable. US3 is the smallest (two files) and could
ship first as a pure safety improvement; it is P3 rather than P1 only because its message
carries no information a client can act on.

### Parallel opportunities

- T011–T022 are twelve different service files with no shared state — fully parallel
- T031–T034 likewise, once T030 exists
- T006–T009 are independent test files/cases
- T040–T043 are independent verification passes

---

## Implementation Strategy

**MVP**: Phase 1 + Phase 2 + Phase 3 (US1). That alone converts the destructive,
highest-consequence failure into an actionable message.

**Increment 2**: Phase 4 (US2) — highest frequency, lowest severity.

**Increment 3**: Phase 5 (US3) — completes the guarantee so it holds for code not yet written.

**Delivered as**: one PR (#108), because the three layers are meaningless individually as a
policy statement, and the shared guard would otherwise be introduced and then immediately
extended.
