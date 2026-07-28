# Implementation Plan: One In-Transit Location per Facility

**Branch**: `013-facility-transit-warehouses` | **Date**: 2026-07-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-facility-transit-warehouses/spec.md`

## Summary

Spec 012 put goods riding on a truck into a single system-wide in-transit warehouse. Every warehouse
belongs to exactly one facility, so that row had to be parented on an arbitrary one —
`MIN(facility_id)`. Every facility's dispatched stock therefore accumulates on one facility's books.

This replaces it with one in-transit location per facility, owned by the system rather than
maintained by people. One column, one enum value, one migration, ten app files touched, one setting
and one startup check deleted.

Four things shape the approach:

1. **A boolean column is now the right answer, and it was not before.** Spec 012's R3 rejected a
   flag on `warehouse` because *"the config setting already identifies the row"*. This feature
   retires that setting, and three of the four questions the code must answer are set predicates
   (which warehouses may be chosen, is this one virtual, does every facility have one) that a single
   configured id could never have answered. That is precisely why spec 012 could only ever have one.
2. **The blast radius is smaller than it looks, because of where the guards land.** GET, PUT and
   DELETE on `/warehouses/{id}` all resolve the row the same way, so one shared helper answers
   `403 In-transit locations are managed by the system` for all three — three functional
   requirements, one enforcement point. The helper also absorbs the `404` block those endpoints
   currently repeat, so the module gets shorter. *(This was `404` until the clarification session of
   2026-07-28; see research R4, which keeps the superseded reasoning visible.)*
3. **The audit came back empty, and that is the finding.** The shared in-transit warehouse has
   **zero** ledger rows and there are **zero** departed itineraries. FR-017's "redistribute
   in-flight balances" has nothing to redistribute, so it becomes a guard rather than an
   `UPDATE ... JOIN` that cannot correctly split a ledger row anyway. Spec 012's plan had to work
   around an audit it could not finish; this one did not have to.
4. **The defect is worse than the spec claimed.** The shared location sits on facility **1**, which
   is `INACTIVE` — and `available_orders` filters on active facilities, so facility 1 can never
   dispatch. Every other facility's in-transit stock was accruing to the books of the one facility
   structurally incapable of shipping it.

This feature also fixes a live latent defect it did not go looking for:
`delivery_order_service._fallback_warehouse` picks `MIN(warehouse_id)` within a facility with **no
in-transit exclusion at all**, so it can already hand a delivery line the virtual warehouse as its
dispatch warehouse. FR-012 names it; the fix lands here.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI (ASGI), SQLAlchemy 2.0 async (`Mapped`/`mapped_column`), Pydantic
v2, aiomysql. **No new dependency.**

**Storage**: MariaDB 10.11. One column added to `warehouse` by
`migrations/011_facility_transit_warehouses.sql` (+ rollback); one row converted, thirteen inserted.
No new table, no new index, no new model class — see [data-model.md](./data-model.md).

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`, following `tests/api/`
(`dependency_overrides`, no live database) and `tests/unit/` for service logic.

**Target Platform**: Linux server, ASGI (uvicorn)

**Project Type**: Web service — REST API under `/api/v1/`

**Performance Goals**: None set, and none needed. `warehouse` holds 32 rows after the migration.
The one thing to hold is that the facility→transit resolution is **one query per departure or
closure, not per line** (research R2) — the N+1 rule `app/services/fk_expansion.py` exists to
enforce.

**Constraints**: Every route `async def`; all DB access through `AsyncSession`; ruff clean at 100
columns; existing privilege gates unchanged.

**Scale/Scope**: 4 user stories, 21 functional requirements, **0 new endpoints**, 0 new tables,
0 new services, 1 migration. 14 facilities and 19 warehouse rows affected (measured).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Simplicity First | ✅ Pass | A generated column plus unique index would enforce one-per-facility in the database; rejected because neither writer to this schema can violate it (R3). An `UPDATE ... JOIN` redistributing historic balances was drafted and dropped — zero rows to move (R6). A startup check asserting the invariant was rejected as an availability failure in response to a condition FR-009 already handles (R7). The feature **removes** a setting, a startup check and ~35 lines of `main.py`. |
| II. Think Before Coding | ✅ Pass | Eight research decisions, each with rejected alternatives (R4 revised and R8 added by the clarification session; R4 keeps its superseded reasoning visible). The database was queried rather than assumed — six read-only audit queries on 2026-07-28, tabulated in R6. Where spec 012's R3 was overturned, the reason it no longer holds is stated rather than the conclusion quietly reversed. |
| III. Surgical Changes | ⚠️ Justified | **Ten** existing app files edited. Five follow directly from the spec (`models/core.py`, `services/warehouse_service.py`, `services/facility_service.py`, `services/delivery_itinerary_service.py`, `services/delivery_order_service.py`); two are the retirement of what this replaces (`core/config.py`, `main.py`); three came from the clarification session — `api/v1/endpoints/warehouses.py` (the `403` helper), `enums.py` and `api/v1/endpoints/facilities.py` (the audit entry). See Complexity Tracking. |
| IV. Goal-Driven Execution | ✅ Pass | Each user story is an independently testable slice; [quickstart.md](./quickstart.md) gives seven scenarios, including the rollback trap in Scenario 7 and the all-or-nothing departure in Scenario 5. |
| V. Reuse Over Rebuild | ⚠️ Justified | No table, no model class, no service, no endpoint, no schema field, no privilege. `stock_ledger.post_movement`, `assert_not_referenced`, `assert_unique`, `incidences.record` and the existing `warehouse` table all carry the feature unchanged. The cascade reuses delete-then-flush rather than adding a parameter to `references.py` (R5). **One new enum value** — `SourceType.FACILITY` — justified in Complexity Tracking. |
| VI. Async-First | ✅ Pass | All touched paths already `async def` over `AsyncSession`. The one new query is a self-join executed with `await db.execute`. `db.flush()` in the cascade is awaited. `incidences.record` is deliberately synchronous — it stages, the caller commits. |
| VII. Security by Default | ✅ Pass | No new endpoint and no privilege change. The feature *narrows* what an authenticated administrator can reach: in-transit locations become unaddressable (FR-010 – FR-013). `403` with a reason rather than `404` keeps "forbidden" and "not found" distinguishable (FR-013a, R4) — the clarified choice traded a little id disclosure for an answer a developer can act on. Facility deletion now leaves an audit trail (FR-015a). |
| VIII. Ruff Compliance | ✅ Pass | Rule set E, F, I, UP at 100 columns; verified by `uv run ruff check app/ migrations/ tests/`. Removing the setting orphans imports in `main.py` and `sales_order_service.py`, which Principle III requires this change to clean up. |

**Testing gate (Constitution v1.2.0)**: tests are **REQUIRED**. `facility_service`,
`warehouse_service`, `delivery_itinerary_service` and `delivery_order_service` all carry branching
logic changed here and each needs `tests/unit/` coverage of those branches. `tests/api/` files for
warehouses and facilities cover the new `403`s, the `404` control case, the cascade and the audit entry. Tests are written first and
confirmed failing. **No exemption is claimed.**

**Gate result**: PASS with one justified deviation, recorded in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/013-facility-transit-warehouses/
├── plan.md              # This file
├── research.md          # Phase 0 output — 7 decisions, incl. the measured audit (R6)
├── data-model.md        # Phase 1 output — the column, the rows, the three lookups
├── quickstart.md        # Phase 1 output — 7 validation scenarios + migration checks
├── contracts/
│   └── README.md        # Phase 1 output — 5 changed endpoints, 0 new
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
migrations/
├── 011_facility_transit_warehouses.sql           # NEW: + in_transit column; convert row 20;
│                                                 #   backfill 13; guard on nonzero balance
└── 011_facility_transit_warehouses_rollback.sql  # NEW: delete 13, restore row 20, drop column

app/
├── main.py                                  # EDIT: delete verify_in_transit_warehouse() and
│                                            #   its lifespan call (R7)
├── enums.py                                 # EDIT: + SourceType.FACILITY = 10 (R8)
├── core/config.py                           # EDIT: delete in_transit_warehouse_id (R7)
├── models/core.py                           # EDIT: + Warehouse.in_transit
├── api/v1/endpoints/
│   ├── warehouses.py                        # EDIT: one _addressable() helper — 404 missing,
│   │                                        #   403 in-transit — replacing 3 repeated blocks (R4)
│   └── facilities.py                        # EDIT: pass CurrentUser into delete_facility (R8)
└── services/
    ├── warehouse_service.py                 # EDIT: flag-based exclusion in list_warehouses;
    │                                        #   get_warehouse left unfiltered on purpose (R4);
    │                                        #   + get_transit_warehouse / transit map helper
    ├── facility_service.py                  # EDIT: create the location with the facility;
    │                                        #   cascade it on delete (R5); audit entry (R8)
    ├── delivery_itinerary_service.py        # EDIT: resolve per-facility transit at departure
    │                                        #   and stop closure; 422 when missing (R2)
    ├── delivery_order_service.py            # EDIT: _fallback_warehouse excludes in-transit
    └── sales_order_service.py               # EDIT: product lookup uses the flag, not the id

tests/
├── api/
│   └── test_facilities.py                   # EDIT: warehouse *and* facility endpoints both live
│                                            #   here — 403 on get/put/delete of an in-transit
│                                            #   row, 404 for an id naming nothing; facility
│                                            #   create/delete cascade + audit entry
└── unit/
    ├── test_warehouse_service.py            # NEW: exclusion, lookup, transit map
    ├── test_facility_service.py             # NEW: atomic create, cascade, rollback-on-409
    ├── test_delivery_itinerary_service.py   # EDIT: per-facility posting, cross-facility trip,
    │                                        #   all-or-nothing 422 (drops the source assertion
    │                                        #   at :158)
    ├── test_delivery_order_service.py       # EDIT: fallback never returns in-transit — inverts
    │                                        #   the existing assertion at :181
    ├── test_sales_order_service.py          # EDIT: source assertion at :334
    ├── test_startup.py                      # EDIT: lifespan now runs one check, not two
    ├── test_stock_ledger.py                 # EDIT: drop the startup-guard tests (T015b of 012)
    └── test_migrate.py                      # EDIT: + 011 discovery
```

> Two corrections found by reading the test suite rather than assuming it: there is no
> `tests/api/test_warehouses.py` — warehouse endpoints are tested inside `test_facilities.py` — and
> `tests/unit/test_startup.py` asserts that **two** checks run before serving, so retiring one is a
> change it will catch. Both were wrong in the first draft of this tree.

**Structure Decision**: The existing single-project layout is kept unchanged. No new package, no new
module, no new file under `app/`. Every change is an edit to a file that already exists, which is
what "nothing new is built" means concretely.

## Delivery Phases

The spec's four stories, ordered so each lands as a working, testable slice. **Phase 0 is a hard
prerequisite for everything after it.**

| Phase | Delivers | Stories | Verify |
|---|---|---|---|
| **0 — Column & migration** | `011` + rollback; `Warehouse.in_transit`; 14 rows exist | — | Applies and rolls back on a copy; 32 warehouse rows, 14 in-transit, 0 facilities without one; idempotent on re-apply |
| **1 — System ownership** | `_addressable()` endpoint helper, `list_warehouses` flag, fallback exclusion, sales lookup | US3 | Quickstart Scenario 6 — every administrator request answers `403` **and an id naming nothing still answers `404`**; the fallback never returns an in-transit row |
| **2 — Per-facility posting** | Transit resolution at departure and closure; the `422` | US1 | Quickstart Scenarios 1–4 — two facilities dispatch with zero cross-attribution; a cross-facility trip departs; a refusal returns to the line's own warehouse |
| **3 — Retire the setting** | `config.py`, `main.py`, `.env.example`, orphaned imports and tests | US1 (FR-006) | Boot with no `IN_TRANSIT_WAREHOUSE_ID` set; the grep in quickstart's final gate returns nothing |
| **4 — Facility lifecycle** | Atomic create; delete cascade; `SourceType.FACILITY` and the deletion audit entry | US2, US4 | Quickstart Scenarios 5 and 7 — including that a `409` leaves the in-transit row **intact and unlogged** |

Phase 3 must not land before Phase 2: removing the setting while departure still reads it breaks
every dispatch. Phase 1 can land independently of both.

**These phase numbers are not tasks.md's.** This table is a delivery narrative; `tasks.md` numbers
its own phases from 1. The mapping:

| This plan | tasks.md |
|---|---|
| — | 1 — Setup |
| 0 — Column & migration | 2 — Foundational |
| 1 — System ownership | 3 — US3 |
| 2 — Per-facility posting | 4 — US1 |
| 3 — Retire the setting | 5 — Retire the setting |
| 4 — Facility lifecycle | 6 — US2, 7 — US4 |
| — | 8 — Polish |

## Complexity Tracking

> Filled because the Constitution Check flagged one justified deviation.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| **Ten existing app files edited** (Principle III) | Five are the feature: the column (`models/core.py`), the guards and lookups (`warehouse_service.py`), the lifecycle (`facility_service.py`), the posting (`delivery_itinerary_service.py`), the fallback (`delivery_order_service.py`). Two are the retirement of what this replaces: `core/config.py` and `main.py` both exist *only* because spec 012's in-transit id could not be defaulted, and leaving them would mean shipping a startup check for a setting nothing reads. Three came from clarification: `api/v1/endpoints/warehouses.py` (the `403` helper), `enums.py` (the audit type) and `api/v1/endpoints/facilities.py` (passing the actor through). | Keeping the setting as a dormant override is configurability nobody asked for (Principle I). Leaving `verify_in_transit_warehouse` in place would refuse to boot over a variable that no longer has a consumer. `sales_order_service.py` is edited for one predicate — the alternative is leaving it comparing against a deleted setting, which does not compile. |
| **`_fallback_warehouse` exclusion** (Principle III — a fix beyond the literal request) | FR-012 requires that no selection surface can yield an in-transit location; this one currently can, and did before this feature existed. It takes `MIN(warehouse_id)` inside a facility with no exclusion, so any facility whose in-transit row has the lowest id would silently dispatch *from* the virtual location. | Leaving it means FR-012 is only partly met and the feature ships a known hole. Filing it separately would leave the hole open across the release that closes every other picker — the inconsistency is what makes it dangerous. |
| **New `SourceType.FACILITY = 10`** (Principle V) — *from clarification* | FR-015a. The cascade destroys a row the operator never created and cannot see; the clarified answer is that this must not be silent. `incidences.record` already provides the who/when/why shape, but `SourceType` tops out at `PRODUCT = 9` and has no facility value. | Filing the entry under an existing `SourceType` was offered in clarification and rejected: a log filed under the wrong entity type is less trustworthy than no log. Writing no entry was the pre-clarification design and was overturned. |
| **`delete_facility` signature change** (Principle III) — *from clarification* | The audit entry needs the acting user's employee id, which the service does not currently receive. The endpoint has it and discards it. | Reading the current user inside the service would need a request-scoped dependency the service layer deliberately does not have; every other audited service in this codebase takes the actor as a parameter. |

**Deliberately not done**: no facility inventory report; no `in_transit` field on any response
schema; no new endpoint, privilege, table, model class or service; no database-level uniqueness
constraint; no snapshot of the transit warehouse on the itinerary line; no rewrite of settled ledger
history; **no runtime repair of a facility missing its location** (FR-009a — clarified as
migration-only); **no `SYSTEM_EMPLOYEE_ID` fallback for audit attribution** (R8).

## Post-Design Constitution Re-Check

Re-run after Phase 1. **Result: PASS** — no new violation. The design removed work rather than
adding it, twice, and the audit changed one requirement's shape.

| Principle | Post-design finding |
|---|---|
| I. Simplicity First | **Strengthened during design, in three places.** The audit turned FR-017 from a redistribution into an assertion (R6). The single enforcement helper collapsed three requirements into one change (R4). The cascade turned out to need nothing from `references.py` (R5). Each was a smaller answer found by looking, not by deciding to be brief. **Clarification then added scope back** — an enum value, an audit write and a signature change — which is what a clarification session is for. |
| III. Surgical Changes | Held, and the footprint is stated as a count rather than a promise: **ten** app files, seven test files, two migration files. The edits that are not literally requested — the `_fallback_warehouse` exclusion, and the three from clarification — are named in Complexity Tracking rather than absorbed, because a Constitution Check that under-reports its own blast radius is worthless. Spec 012's plan learned that the hard way and said so. |
| V. Reuse Over Rebuild | Held on everything structural — no table, model class, service, endpoint, schema or privilege. **The claim that this was the first feature here to add no new abstraction at all did not survive clarification**: `SourceType.FACILITY` is one new enum value. The claim is corrected rather than quietly dropped, because it was the more interesting thing this plan had to say and it turned out to be wrong. |
| VI. Async-First | Held. One new self-join query, awaited; `db.flush()` in the cascade, awaited. |
| VII. Security by Default | Held, narrowed and now audited. The change reduces what an authenticated administrator can address, and facility deletion stops being an untraced destructive action. |

**Two risks stated rather than buried.**

**Phase 3 before Phase 2 breaks every dispatch, silently at import time and loudly at run time.**
Removing `in_transit_warehouse_id` while `delivery_itinerary_service` still reads it is an
`AttributeError` on the departure path. The phase table orders them; the tasks must preserve that
order, and nothing in the type checker will catch it if they do not.

**The facility-delete cascade stages a delete before an assert, and a missing rollback would destroy
the row while appearing to refuse.** `get_db` never commits on an exception, so InnoDB discards the
staged delete when the session closes — but that is a property of a dependency two files away, not
of the code being written. Quickstart Scenario 7 asserts the row is **still present** after a `409`,
which is the check that would catch a future change to session handling.

**A third risk, added by clarification.** `delete_facility` now refuses when the acting user has no
employee record, on the clarified invariant that every authenticated user has one. The database does
**not** enforce that — `user.employee` is nullable — so if the invariant is not actually true in a
deployment, this feature turns a working facility deletion into a `422` for those users. That is the
correct failure (an audit attribution must not be invented) but it is a behaviour change to a
shipped endpoint, and it is worth confirming against the live `user` table before release rather
than discovering it in production.

**One thing the audit changed that is worth carrying into the migration.** The shared in-transit
warehouse belongs to facility 1, which is `INACTIVE`. Migration 011 converts that row rather than
deleting it (FR-016), so facility 1 keeps a location it can never use — correct and harmless, and
exactly what "every facility has one" means. It is called out here so nobody reads it later as a
migration bug.

**Agent context (`CLAUDE.md`) updated**: the `<!-- SPECKIT START -->` / `<!-- SPECKIT END -->` block
now points at this plan.
