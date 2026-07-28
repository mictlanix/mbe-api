---

description: "Task list for one in-transit location per facility"
---

# Tasks: One In-Transit Location per Facility

**Input**: Design documents from `/specs/013-facility-transit-warehouses/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/README.md](./contracts/README.md),
[quickstart.md](./quickstart.md)

**Tests**: **REQUIRED, not optional.** Constitution v1.2.0 removed the carve-out the upstream
template still describes — services with branching logic need `tests/unit/` coverage of those
branches, endpoints need `tests/api/` coverage, and tests are written first and confirmed failing.
No exemption is claimed for this feature.

**Organization**: Grouped by user story. Phase 2 is a hard prerequisite for every story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 from [spec.md](./spec.md)

---

## Phase 1: Setup

**Purpose**: Confirm the baseline before changing anything. No project scaffolding is needed —
this feature adds no new file under `app/`.

- [X] T001 Confirm a green baseline: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q` both clean, recorded in the task notes
- [X] T002 Re-run the six read-only audit queries from [research.md](./research.md) R6 against the deployment database and confirm the counts still hold — 14 facilities, 19 warehouse rows, **0** `lot_serial_tracking` rows against warehouse 20, **0** `DEPARTED` itineraries. **If any count has moved, stop**: R6's conclusion that FR-017 has nothing to redistribute is what T005's guard-only design rests on

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The column and the fourteen rows. Nothing in any user story works until every facility
has an in-transit location and the code can recognise one.

**⚠️ BLOCKS ALL USER STORIES**

- [X] T003 [P] Write a failing test in `tests/unit/test_migrate.py` asserting migration `011_facility_transit_warehouses` is discovered by the runner alongside its rollback
- [X] T004 Add `in_transit: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0')` to `Warehouse` in `app/models/core.py`, placed after `status` per [data-model.md](./data-model.md)
- [X] T005 Write `migrations/011_facility_transit_warehouses.sql` per [data-model.md](./data-model.md): (1) **guard** — fail when the existing `IN-TRANSIT` row holds a nonzero `lot_serial_tracking` balance; (2) `ALTER TABLE warehouse ADD COLUMN in_transit TINYINT(1) NOT NULL DEFAULT 0`; (3) convert row 20 to `in_transit = 1`, `code = 'IN-TRANSIT-1'` (FR-016); (4) backfill one row per facility lacking one, guarded by `NOT EXISTS` so re-applying inserts nothing; (5) assert every facility has exactly one (FR-001) and that every warehouse code is still unique (FR-018). State the measured counts in the header comment, as migration 007 did
- [X] T006 Write `migrations/011_facility_transit_warehouses_rollback.sql`: delete the 13 inserted rows, restore row 20 to code `IN-TRANSIT`, drop the column. **The header must state that `IN_TRANSIT_WAREHOUSE_ID=20` has to go back into the environment**, because the code this rolls back to reads that setting
- [X] T007 Apply `011` to a **copy** of the database and verify against the [quickstart.md](./quickstart.md) migration table: 32 warehouse rows, 14 with `in_transit = 1`, 0 facilities without one (FR-001, FR-008), row 20 converted not duplicated (FR-016), every code still unique (FR-018). Then apply it twice to prove idempotence, then apply the rollback and confirm the schema returns to its prior shape
- [X] T008 Add `get_transit_warehouse(db, facility_id)` and `transit_warehouses_for(db, dispatch_warehouse_ids)` to `app/services/warehouse_service.py`. The second runs the **single self-join** from [data-model.md](./data-model.md) returning `{dispatch_warehouse_id: transit_warehouse_id}` — one query for the whole trip, never one per line (research R2)
- [X] T009 [P] Write failing unit tests in `tests/unit/test_warehouse_service.py` (**new file**) for both helpers: a facility with a location resolves; a facility without one is absent from the map rather than raising; a map request for several dispatch warehouses across two facilities issues **one** query

**Checkpoint**: every facility has an in-transit location and the code can find it. User stories may now proceed.

---

## Phase 3: User Story 3 — The in-transit location cannot be tampered with (Priority: P3)

**Goal**: In-transit locations become unaddressable and unselectable, for every role including
administrator.

**Independent test**: [quickstart.md](./quickstart.md) Scenario 6 — every listed request answers
`403` or omits the row, an id naming no warehouse still answers `404`, and the automatic dispatch
fallback never yields one.

**Sequenced first despite its P3 priority.** It has no dependency on the posting change, and it
closes the `_fallback_warehouse` defect that can hand a delivery line the virtual warehouse *today*.
Shipping the guards before the posting change means the hole is closed for the whole window rather
than only at the end.

- [X] T010 [P] [US3] Write failing unit tests in `tests/unit/test_warehouse_service.py` for `list_warehouses` excluding every `in_transit` row (not just one id). **Also assert `get_warehouse` still returns in-transit rows unchanged** — the guard lives at the endpoint, and a service that silently pretends a row does not exist is a trap for future callers (research R4)
- [X] T011 [P] [US3] Write failing unit tests in `tests/unit/test_delivery_order_service.py` asserting `_fallback_warehouse` never returns an in-transit warehouse, **using a facility whose in-transit row has the lowest `warehouse_id`** — that is the case the current `MIN(warehouse_id)` gets wrong. This **inverts the existing assertion at `tests/unit/test_delivery_order_service.py:181`**, which asserts the function does *not* mention the setting
- [X] T012 [US3] Replace the configured-id exclusion in `list_warehouses` with `Warehouse.in_transit.is_(False)` in `app/services/warehouse_service.py`, and update the comment above it — it currently explains a setting that is being retired
- [X] T013 [US3] Add a shared `_addressable(db, warehouse_id)` helper to `app/api/v1/endpoints/warehouses.py` raising `404 Warehouse not found` when the row is missing and `403 In-transit locations are managed by the system` when it is in-transit, then route GET, PUT and DELETE through it. This **replaces** the `if warehouse is None: raise 404` block repeated in all three, so the module gets shorter. Leave `warehouse_service.get_warehouse` unchanged (FR-010, FR-011, FR-013, research R4)
- [X] T014 [US3] Add `Warehouse.in_transit.is_(False)` to `_fallback_warehouse` in `app/services/delivery_order_service.py` (FR-012)
- [X] T015 [P] [US3] Replace the configured-id comparison with the flag in the product lookup at `app/services/sales_order_service.py:927`, and update the comment that references the retired setting
- [X] T016 [US3] Update the source-inspection assertion at `tests/unit/test_sales_order_service.py:334` — it asserts `'in_transit_warehouse_id' in source`, which is now false. Assert the flag-based exclusion instead
- [X] T017 [US3] Add API tests to `tests/api/test_facilities.py` (where the warehouse endpoints are tested) covering `403` on GET, PUT and DELETE of an in-transit warehouse **and `404` for an id that names no warehouse at all**. The `404` case is the one that matters: a guard answering `403` for everything would pass every other assertion while destroying the distinction FR-013a exists for. Assert the `403` body text too, since FR-013a requires the refusal to explain itself

**Checkpoint**: US3 is independently verifiable via quickstart Scenario 6.

---

## Phase 4: User Story 1 — In-transit stock stays on its own facility's books (Priority: P1)

**Goal**: Departure, acceptance and return post against the in-transit location of the facility that
owns the line's dispatch warehouse.

**Independent test**: [quickstart.md](./quickstart.md) Scenarios 1–4 — two facilities dispatch with
zero cross-attribution; a cross-facility trip departs; a refusal returns to the line's own
warehouse; the warehouse's facility wins when it disagrees with the order's.

- [X] T018 [P] [US1] Write failing unit tests in `tests/unit/test_delivery_itinerary_service.py` for departure: goods from facility A's warehouse post inbound to **A's** transit location and nothing posts to any other facility's; a trip carrying lines from two facilities departs successfully and posts to both (FR-005); an order whose `facility` differs from its dispatch warehouse's facility posts to the **warehouse's** facility (FR-002)
- [X] T019 [P] [US1] Write failing unit tests in `tests/unit/test_delivery_itinerary_service.py` for stop closure: acceptance consumes from the same facility's transit location it was posted into; a refusal moves goods out of it and back to `order_line.warehouse`, reclaiming the reservation exactly as today (FR-003, FR-004)
- [X] T020 [P] [US1] Write a failing unit test in `tests/unit/test_delivery_itinerary_service.py` asserting that when a facility on the trip has no in-transit location, departure raises `422 Facility {id} has no in-transit location` **and no `post_movement` call is made for any line** — the check runs before the first ledger write, so departure is all-or-nothing (FR-009). **Also assert no `Warehouse` row is created** — FR-009a forbids repairing the facility at run time, and without a test that negative silently invites a future contributor to "fix" the 422 by self-healing
- [X] T021 [US1] In `depart()` in `app/services/delivery_itinerary_service.py`, resolve the transit map once for every dispatch warehouse on the trip via `warehouse_service.transit_warehouses_for`, raise the `422` for any dispatch warehouse missing from it **before the posting loop begins**, then post the inbound half to the mapped id instead of `settings.in_transit_warehouse_id` (line ~608)
- [X] T022 [US1] Apply the same resolution to the stop-closure path in `app/services/delivery_itinerary_service.py` (line ~746): outbound from the mapped transit id, inbound to `order_line.warehouse` unchanged
- [X] T023 [US1] Update the source-inspection assertion at `tests/unit/test_delivery_itinerary_service.py:158` — it asserts `'in_transit_warehouse_id' in source`, now false

**Checkpoint**: US1 is independently verifiable via quickstart Scenarios 1–4. **The feature's core
value is delivered here.**

---

## Phase 5: Retire the setting (FR-006)

**Purpose**: Remove the configuration and startup check that only existed because spec 012's
in-transit id could not be defaulted (research R7).

**⚠️ MUST NOT start before Phase 4 completes.** Removing `in_transit_warehouse_id` while
`delivery_itinerary_service` still reads it is an `AttributeError` on every departure, and no type
check catches it.

- [X] T024 Delete `in_transit_warehouse_id` from `app/core/config.py` (line ~53)
- [X] T025 [P] Delete the `IN_TRANSIT_WAREHOUSE_ID` entry and its explanatory comment from `.env.example` (lines ~14 and ~20)
- [X] T026 Delete `verify_in_transit_warehouse()` from `app/main.py` and its call in `lifespan`, leaving `ensure_system_employee` as the only startup check. Remove the imports the deletion orphans (Principle III)
- [X] T027 Update `tests/unit/test_startup.py` — it asserts **two** checks run before serving and that the employee is created before the warehouse is checked. Both assertions are now wrong. Keep the properties that still hold: a raising check aborts the boot, serving never begins after a failed check, the deprecated decorator is unused, the app is wired to the lifespan
- [X] T028 [P] Delete the three startup-guard tests in `tests/unit/test_stock_ledger.py` (lines ~188, ~211, ~236 — spec 012's T015b) which monkeypatch the removed setting
- [X] T029 Verify `grep -rn "in_transit_warehouse_id\|IN_TRANSIT_WAREHOUSE_ID" app/ tests/ .env.example` returns **nothing**, and that the app boots with no such variable set

---

## Phase 6: User Story 2 — A new facility can dispatch on day one (Priority: P2)

**Goal**: Creating a facility creates its in-transit location atomically; a facility never exists
without one.

**Independent test**: [quickstart.md](./quickstart.md) Scenario 5 — `POST /facilities` returns
`201` and a dispatch from one of its warehouses succeeds with no setup step and no environment
change in between.

- [X] T030 [P] [US2] Write failing unit tests in `tests/unit/test_facility_service.py` (**new file**) for `create_facility`: the in-transit location exists after creation, coded `IN-TRANSIT-{facility_id}` with `in_transit = 1`, `status = ACTIVE`, and the facility's own id as its parent
- [X] T031 [P] [US2] Write a failing unit test in `tests/unit/test_facility_service.py` asserting that when the warehouse insert fails, **neither** row is committed (FR-007) — one transaction, all or nothing
- [X] T032 [US2] Extend `create_facility` in `app/services/facility_service.py` to insert the in-transit location in the same transaction as the facility, per the field table in [data-model.md](./data-model.md). The code needs the facility id, so `flush()` the facility first and commit both together — do not commit twice
- [X] T033 [P] [US2] Add an API test to `tests/api/test_facilities.py` asserting `POST /facilities` still returns `201` with an unchanged body — the in-transit location is created but is not exposed in the response (contracts/README.md)

**Checkpoint**: US2 is independently verifiable via quickstart Scenario 5.

---

## Phase 7: User Story 4 — Removing a facility removes its in-transit location (Priority: P4)

**Goal**: The system-created location never blocks a facility delete, while real inventory history
still does.

**Independent test**: [quickstart.md](./quickstart.md) Scenario 7 — all four answers: `204` with an
audit entry, the in-transit history `409`, the unchanged `409` for other references, and the `422`
when attribution is impossible.

- [X] T034 [P] [US4] Write failing unit tests in `tests/unit/test_facility_service.py` for `delete_facility`: a facility whose only reference is its in-transit location deletes with `204` and **both** rows go (FR-014); a facility whose transit location carries `lot_serial_tracking` history raises `409` naming `lot_serial_tracking.warehouse` **first** (FR-015); a facility blocked by real warehouses raises the unchanged `409`
- [X] T035 [US4] Write a failing unit test in `tests/unit/test_facility_service.py` asserting that after a `409`, **the in-transit warehouse row still exists and no `incidence` row was written**. The delete and the audit entry are both staged before the assert, so a missing rollback would destroy the row and log a deletion that never happened, while appearing to refuse — this is the test that catches a future change to session handling
- [X] T036 [P] [US4] Write failing unit tests in `tests/unit/test_facility_service.py` for the audit entry (FR-015a): a successful delete stages exactly one `incidence` under `SourceType.FACILITY`, keyed to the facility id, attributed to the acting user's employee id, naming the removed in-transit location in its context; and a delete attempted by a user with **no** employee record raises `422` rather than inventing an attribution (research R8)
- [X] T037 [P] [US4] Add `FACILITY = 10` to `SourceType` in `app/enums.py` — the next free value after `PRODUCT = 9` (research R8)
- [X] T038 [US4] Implement the cascade in `delete_facility` in `app/services/facility_service.py` per research R5: resolve the transit location, `assert_not_referenced` on **it** first, `db.delete()` it, `await db.flush()`, then the existing `assert_not_referenced(db, facility)` and delete, unchanged. Do **not** add an `exempt` parameter — it is table-granular and would hide the facility's real warehouses
- [X] T039 [US4] **(after T037 — needs `SourceType.FACILITY`)** Add the acting user to `delete_facility`'s signature in `app/services/facility_service.py` and stage the `incidences.record` entry in the same transaction as the deletes, refusing with `422` when the employee id is absent. Update the call site in `app/api/v1/endpoints/facilities.py` to pass its `CurrentUser` through instead of discarding it (FR-015a)
- [X] T040 [P] [US4] Add an API test to `tests/api/test_facilities.py` covering `DELETE /facilities/{id}` returning `204`, the in-transit `409`, and the `422` for a user with no employee record, per the response table in [contracts/README.md](./contracts/README.md)

**Checkpoint**: all four user stories complete.

---

## Phase 8: Polish & Cross-Cutting

- [X] T041 [P] Update `CHANGELOG.md` under `[Unreleased]`: **Changed** — one in-transit location per facility, replacing the system-wide one; in-transit locations now answer `403` rather than being addressable; **Added** — an audit entry on facility deletion (`SourceType.FACILITY`); **Removed** — `IN_TRANSIT_WAREHOUSE_ID` and its startup check; **Fixed** — the automatic dispatch fallback could return the virtual warehouse. Note that migration 011 needs **no** follow-up id capture, unlike 008
- [X] T042 [P] Add a note to `docs/specs/06a-delivery-flow-v2.md` recording that the single in-transit warehouse it describes is now one per facility, so the document does not keep teaching the superseded shape
- [X] T043 Confirm against the live `user` table that every user with facility-delete privilege has an employee record. `user.employee` is nullable, so the clarified invariant is policy rather than a schema guarantee — if it does not hold, T039's `422` turns a working deletion into a failure for those users (plan.md, third risk)
- [X] T044 Run the full [quickstart.md](./quickstart.md) — all seven scenarios plus the migration and rollback checks
- [X] T045 Final gates: `uv run ruff check app/ migrations/ tests/` clean and `uv run pytest -q` green

---

## Dependencies

```text
Phase 1 (Setup) ── T002's audit gates T005's guard-only design
     ↓
Phase 2 (Foundational) ── column, migration, 14 rows, lookup helpers
     ↓
     ├─→ Phase 3 (US3) ── guards & pickers ─────────┐  independent of each other
     └─→ Phase 4 (US1) ── per-facility posting ─────┤
                                ↓                    │
                        Phase 5 (retire setting) ←───┘  HARD: after Phase 4
                                ↓
                        Phase 6 (US2) ── facility create
                                ↓
                        Phase 7 (US4) ── facility delete
                                ↓
                        Phase 8 (Polish)
```

**The one ordering that is not negotiable**: Phase 5 after Phase 4. Everything else is convenience.

Phase 6 before Phase 7 is a soft ordering — T036's cascade is easier to test against facilities
T032 creates — but each phase is independently testable if taken out of order.

**Within Phase 2**: T004 (model) and T005 (migration) must agree on the column definition; T007
cannot run until both land. T008 depends on T004.

## Parallel Opportunities

| Phase | Parallel set | Why safe |
|---|---|---|
| 2 | T003, T009 | Different test files, no shared state |
| 3 | T010, T011, T015 | Three different files; T015's service change is independent of the warehouse guards |
| 4 | T018, T019, T020 | Same file but disjoint test classes — write together, run together |
| 5 | T025, T028 | `.env.example` and a test file; neither imports the other |
| 6 | T030, T031, T033 | Two unit tests plus one API test |
| 7 | T034, T036, T037, T040 | Test writing plus the enum value, which nothing else touches |
| 8 | T041, T042 | Two documents |

**Phase 3 and Phase 4 can run in parallel as whole phases** once Phase 2 lands — they touch
disjoint services (`warehouse_service` + `delivery_order_service` vs
`delivery_itinerary_service`). T015/T016 in Phase 3 touch `sales_order_service`, which Phase 4 does
not.

## Implementation Strategy

**MVP = Phase 2 + Phase 4 (US1).** That is the defect fixed: every facility has its own location and
dispatched goods land on the right books. It is shippable without US2, US3 or US4 — new facilities
would need their location created by hand, and the locations would still be editable, but no stock
is misattributed.

**Recommended increment order** — note this is *not* the spec's priority order, and the reason is in
Phase 3's header:

1. **Phase 2** — foundational, unavoidable.
2. **Phase 3 (US3, P3)** — pulled forward because it closes a live defect (`_fallback_warehouse`)
   that exists today, independent of everything else.
3. **Phase 4 (US1, P1)** — the core value. Review this one on its own.
4. **Phase 5** — retire the setting, only once Phase 4 has landed.
5. **Phase 6 (US2, P2)** and **Phase 7 (US4, P4)** — the facility lifecycle, naturally paired.

**Two things to watch, carried from the plan's risk section.**

Phase 5 before Phase 4 breaks every dispatch. The dependency is real but invisible to tooling —
`settings.in_transit_warehouse_id` fails at attribute access, not at import.

T035 is the test most likely to be skipped and the most expensive to be wrong about. The facility
delete stages a row deletion *and* an audit entry *before* the check that might refuse, relying on
`get_db` never committing on an exception — a property of a file two directories away.

T043 is a check, not a code change, and it gates a behaviour regression rather than a bug: if any
privileged user lacks an employee record, T039 turns a working facility deletion into a `422` for
them. Do it before release, not after.

## Task Count

**45 tasks**: Setup 2, Foundational 7, US3 8, US1 6, Retire-setting 6, US2 4, US4 7, Polish 5.

**Added by the clarification session of 2026-07-28** (+4): T037 (the enum value), T039 (the audit
write and signature change), T043 (the invariant check), and the split of the old T036 into T036 +
T038. T013, T017, T034, T035 and T040 were rewritten for `403` and for the audit entry.
