# Tasks: Retire Technical Service and Vehicle Service Orders

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/permission-matrix.md](./contracts/permission-matrix.md)

**Size**: normal — 12 tasks across 3 stories

## Phase 1: Setup

**None required.** No dependency, tool or configuration changes; the branch is cut and the suite is
green at 2285 passing. A task to "run the suite to confirm green" would not be work.

## Phase 2: Foundational

**None required**, and that is a finding rather than an omission. The three stories touch disjoint
files — Story 1 the models, the dump and the dictionary; Story 2 the enum; Story 3 the constants
that state the enum's size — so nothing blocks anything across stories. The only real coupling is
*within* Story 1 and is handled inside a single task (T001).

---

## Phase 3: User Story 1 — The repository stops describing tables that no longer exist (P1)

**Goal**: Nothing in this repository claims the seven dropped tables exist — not as mapped models,
not in the dictionary, not in the schema dump the standing checks derive from.

**Independent Test**: Grep the repository for the seven table names; every remaining hit is
historical record. Ask the live-schema derivation for its table list and confirm none of the seven
appears.

### Implementation

**Wave 1 — independent (different files):**

- [ ] **T001** [P] [US1] Retire the seven tables from this repository's understanding of the schema,
  in one task because the pieces cannot be separated without a red suite: remove the seven
  `CREATE TABLE` blocks from `docs/mbe_schema.sql`, delete `app/models/technical_service.py`, drop
  its import from `app/models/__init__.py`, and remove the six now-stale
  `tech_service_request_component` waivers from `tests/unit/test_data_dictionary.py`. **The waivers
  are load-bearing here**: dropping the tables from the dump makes each waiver name a column that no
  longer exists, and `test_no_waiver_is_stale` fails on exactly that. Leave the nine sectionless
  legacy-table waivers alone — they are out of scope. · `docs/mbe_schema.sql`,
  `app/models/technical_service.py`, `app/models/__init__.py`, `tests/unit/test_data_dictionary.py`
- [ ] **T002** [P] [US1] Remove the seven `### <table>` sections, the section-11 note marking the
  module as pending removal, and the per-table *Abandoned* markers under each. Leave
  `vehicle_service_order` and `service_order_detail`'s **section-11 neighbours** — the note's
  explicit "not in scope" paragraph goes with the note, since both tables it protected are now
  themselves removed. · `docs/data-dictionary.md`

**⟶ Wait for Wave 1 to finish, then:**

- [ ] **T003** [US1] Prove the schema check is live rather than assuming it. Locally re-add the
  deleted model module (models present, tables absent from the dump), run
  `uv run pytest tests/unit/test_model_schema.py`, confirm it fails naming the seven tables' columns,
  then revert. Record the observed failure output in `research.md` under a new **R5 — mutation
  proof**. This is the evidence the loud ordering would have produced, taken without a red commit
  (plan, Ordering). · `specs/016-drop-tech-service/research.md`

**Checkpoint**: Story 1 is independently functional — the repository's description of the database
matches the database, and the check that enforces that has been shown to work.

---

## Phase 4: User Story 2 — Provisioning stops re-creating the deleted permission rows (P1)

**Goal**: Creating an account or applying a template no longer writes permission rows for the four
menu entries the monolith's migration deleted.

**Independent Test**: Provision an account with no template, count its permission rows, and confirm
none names 58, 64, 65 or 90.

### Implementation

- [ ] **T004** [US2] Remove `TECHNICAL_SERVICE_REPORTS = 58`, `TECHNICAL_SERVICE_REQUESTS = 64`,
  `TECHNICAL_SERVICE_RECEIPTS = 65` and `VEHICLE_SERVICE_ORDERS = 90`. Change no surviving
  identifier and reuse none of the four (FR-004, research R3). Write **no** permission-row cleanup —
  `user_service._write_privileges_from` already removes rows naming an object the enum does not
  define, which is the entire cleanup path (research R2); `user_service.py` is not edited by this
  task. · `app/enums.py`

**⟶ Wait for T004, then:**

- [ ] **T005** [US2] Update the catalog's own test: matrix width `107` → `103`, and add an assertion
  that 58, 64, 65 and 90 are absent while the live neighbours `88`, `89` and `91` keep their
  identifiers. **Do not touch `test_production_sites_is_107`** — `PRODUCTION_SITES` is still `107`
  and that `107` is an identifier, not the count (research R4). Extend the file's docstring to say
  why four entries left the catalog and where the rows went. · `tests/unit/test_system_objects.py`
- [ ] **T006** [US2] Assert the behaviour end to end against a real schema: a provisioned account
  holds one row per defined object and none for the four retired ones. Update `OBJECT_COUNT` to
  `103` in the same pass, since the file's assertions are written against it. ·
  `tests/integration/test_user_profiles_flow.py`

**Checkpoint**: Story 2 is independently functional — the API no longer undoes the monolith's
migration on the next account it provisions.

---

## Phase 5: User Story 3 — The permission matrix width is stated once and stays true (P2)

**Goal**: Every statement of the matrix width agrees with the count the code produces.

**Independent Test**: Grep for `107` across the repository and confirm each surviving hit is the
*identifier* `PRODUCTION_SITES`, never the count.

### Implementation

**Wave 1 — independent (different files):**

- [ ] **T007** [P] [US3] `OBJECT_COUNT` `107` → `103`, and the module docstring's "all 107" → 103. ·
  `tests/unit/test_user_service.py`
- [ ] **T008** [P] [US3] Docstring "the 107-row write" → 103. · `tests/api/test_user_profiles.py`
- [ ] **T009** [P] [US3] `_write_privileges_from`'s docstring: "Every one of the 107 `SystemObject`
  values" → 103, and note that 58/64/65/90 joined 70/104/105 as objects whose grants outlived them —
  the same sentence already explains the removal loop. · `app/services/user_service.py`
- [ ] **T010** [P] [US3] `UserProfilePrivilege`'s docstring: "A user carries all 107 rows" → 103. ·
  `app/models/user.py`

**⟶ Wait for Wave 1 to finish, then:**

- [ ] **T011** [US3] Guard the R4 trap explicitly: confirm `tests/unit/test_user_profile_service.py`
  line 87 still asserts `system_object=107` **unchanged** — it is a fixture naming the surviving
  `PRODUCTION_SITES` identifier, and a find-and-replace of `107` would have silently rewritten it
  into a different assertion. No edit expected; this task is the check that none was made. ·
  `tests/unit/test_user_profile_service.py`

**Checkpoint**: Story 3 is complete — one number, stated consistently, with the identifier that
merely shared its value left alone.

---

## Phase 6: Polish

- [ ] **T012** Write `quickstart.md` giving one runnable command per success criterion (SC-001 to
  SC-009), run all of them, and record the results. Then add the `CHANGELOG.md` Unreleased entry
  under **Removed**, naming the seven tables, the four retired identifiers, the width change and the
  fact that no migration was added here. Finish with `uv run ruff check app tests`, the full suite,
  and `uv run mypy app` compared against its 170-error baseline. ·
  `specs/016-drop-tech-service/quickstart.md`, `CHANGELOG.md`

---

## Dependencies & Execution Order

**Phases**: Setup (none) → Foundational (none) → US1 → US2 → US3 → Polish. The three story phases
are genuinely independent of each other and could run in any order; they are listed in spec priority
order.

- **US1**: T001 and T002 are independent (Wave 1). T003 waits for both, since it mutates what T001
  produced.
- **US2**: T004 alone, then T005 and T006 — both assert against the enum T004 changes.
- **US3**: T007–T010 are independent (Wave 1, four different files). T011 waits for them, being the
  check that the wave did not overreach.
- **Polish**: T012 waits for everything.

**On commits and the never-red rule.** The gate decision was that no *commit* is red, not that no
intermediate task state is. T004 leaves the suite failing until T005 and T006 land, which is fine
inside a phase — commit at the checkpoints, not between tasks within a phase. T001 is the one task
that must be internally complete, which is why its four files are one task rather than four.

## Parallel Opportunities

- US1 Wave 1: T001, T002 — the dump/models/waivers versus the dictionary.
- US3 Wave 1: T007, T008, T009, T010 — four files, four one-line prose corrections, no shared state.
- The three story phases as whole units, if a host wants to fan out; only Polish joins them.
