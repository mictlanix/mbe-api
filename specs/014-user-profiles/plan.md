# Implementation Plan: User Profiles as Permission Templates

**Branch**: `014-user-profiles` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-user-profiles/spec.md`

## Summary

A named permission template that an administrator maintains once and copies onto users. Two new
tables, one nullable column on `user`, one new endpoint module, one migration. The permission scheme
the authorization path reads is untouched: `access_privilege` gains no column, and `deps.py` is not
edited.

Five things shape the approach, all five found by reading the code and auditing the database rather
than from the spec:

1. **`SystemObject` is missing exactly one live value, and this feature adds it.** The database holds
   rows for four objects the enum omits — 70, 104, 105, 107, across **88 rows, 28 carrying a grant, on
   13 of 31 accounts** (measured). The legacy source names them: 70, 104 and 105 are **commented out**
   there — retired features whose privilege rows outlived them — while **`ProductionSites = 107` is
   active** and the enum omits it behind a `# 107 absent` comment that is wrong. So the enum gains
   `PRODUCTION_SITES = 107` here, and the matrix is **107 wide**. Research R9, resolved.

2. **An apply deletes every row for the user, FR-013 read literally.** The 59 rows on the three retired
   objects (24 of them grants) go as accounts are provisioned. Defensible because nothing in either
   application reads them — legacy's own permission UI cannot render an object its enum has commented
   out. **This reversed twice** and research R3 keeps both superseded versions visible: a scoped delete
   was correct while those objects were unidentified, and identifying them removed the reason.

3. **The matrix is 107 wide, not "roughly eighty".** The spec said eighty in two places and both prior
   completion reports said "~82" — all wrong; the spec is corrected. The audit also withdrew a
   justification: 31 users, 3,355 rows, **zero duplicates**, **every user holding the complete matrix**,
   so R3's original "heals duplicates and gaps" described nothing that exists.

4. **Case-insensitive uniqueness has to be written, not inherited.** The collation is confirmed
   `utf8mb3_unicode_ci`, so MariaDB gives FR-004 for free — but `tests/integration/` runs the real
   services against SQLite, where `=` on `TEXT` is case-sensitive. A plain comparison would pass in
   production and fail the integration test, or pass both while the environments disagree.
   `func.lower()` makes the stated rule the enforced rule in both (research R4).

5. **Two shared guards carry more of this than expected.** `assert_not_referenced` derives blocking
   tables from FK metadata, so FR-008's "refused, naming how many users reference it" needs **zero
   new code** the moment `User.profile_id` is a mapped FK. And `assert_unique`'s `column` parameter
   is typed `Any` and used in a `==` expression, so it accepts `func.lower(name)` without
   modification. `references.py` is not edited.

`access_privilege` also still has **no unique index on `(user, object)`** while `deps.py:90` uses
`scalar_one_or_none()`, which *raises* on two matching rows — a duplicate would 500 every request gated
on that object. Reachable in principle, measured absent in practice, out of scope here (research R2).

The sparse/dense asymmetry is the design's centre of gravity and the likeliest place to get it wrong:
a **profile** stores only what it grants; a **user** keeps the full 107-row matrix. The apply is the
translation between the two.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI (ASGI), SQLAlchemy 2.0 async (`Mapped`/`mapped_column`), Pydantic
v2, aiomysql. **No new dependency.**

**Storage**: MariaDB 10.11. Two tables created and one nullable column added by
`migrations/014_user_profiles.sql` (+ rollback). Zero rows seeded — profiles are authored, not
inferred. **No data migration either**: the 59 rows on retired objects 70/104/105 are removed lazily,
by the first apply that touches each account, never by DDL. No existing column changes type or
nullability. See [data-model.md](./data-model.md).

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`. Three layers, all of which this feature
must satisfy: `tests/api/` (dependency overrides, service patched out), `tests/unit/` (mocked
session), `tests/integration/` (real services, real SQL, SQLite schema built from model metadata).
The integration layer is why R4 matters.

**Target Platform**: Linux server, ASGI (uvicorn)

**Project Type**: Web service — REST API under `/api/v1/`

**Performance Goals**: None set, none needed. An apply is 1 DELETE + 107 INSERTs, an administrator
action measured in dozens per year. The one rule to hold is that `profile_name` on a user list page
costs **one** query for the page, not one per row, asserted by statement count rather than by
inspection. **Implementation note, after measuring**: `fk_expansion.batch_fetch` was the planned
mechanism and turned out to be the wrong one — it loads mapped entities, and `UserProfile.privileges`
is `lazy='selectin'`, so it fired a second query loading every mask of every profile on the page to
render a name. A two-column projection is one query. Both return identical JSON, which is why the
test counts statements.

**Constraints**: Every route `async def`; all DB access through `AsyncSession`; ruff clean at 100
columns; `app/core/deps.py` not edited; `app/services/references.py` not edited.

**Scale/Scope**: 3 user stories, 29 functional requirements, **6 new endpoints**, 2 new tables, 1 new
column, 1 new enum value, 1 new service, 1 new endpoint module, 1 migration. 3 existing endpoints
changed additively. **Measured** (`mbe_dev`, 2026-08-12): 31 users, 3,355 `access_privilege` rows, 0
duplicates, 110 distinct object values in use, 88 rows on the 4 objects the enum omitted. Matrix width
after this feature: **107**.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Simplicity First | ✅ Pass | A unique index on `access_privilege(user, object)` was drafted and dropped — correct sequence is measure, repair, constrain, across separate changes (R2). Drift detection was declined at spec time. Bulk apply declined. A `SystemObject` catalog endpoint declined (spec Assumptions) — clients already read the catalog off a user's matrix. The privilege-writing loop is one private helper shared by create and apply, replacing the loop `create_user` already has rather than adding a layer (R8). |
| II. Think Before Coding | ✅ Pass | Nine research decisions, each with rejected alternatives. The database was queried rather than assumed — eleven read-only audit queries on 2026-08-12, tabulated in R0. Four code-derived corrections contradicting the spec or my own earlier reports: the object count (R1), the duplicate-row 500 (R2), the SQLite collation divergence (R4), and the unknown-object rows (R9). **R3 was rewritten because the audit falsified its stated justification**, and the superseded reasoning is kept visible rather than quietly replaced. **R9 was raised as a question rather than decided**, then resolved by reading the legacy source an earlier draft had assumed unavailable — which also falsified R1's "the enum is stale" claim. R3 records all three of its versions rather than presenting the last as if it were the first. |
| III. Surgical Changes | ⚠️ Justified | **Six** existing app files edited: `models/user.py`, `schemas/user.py`, `services/user_service.py`, `api/v1/endpoints/users.py`, `api/v1/router.py`, and `enums.py`. The first five trace to FR-011 (profile at create) or FR-019–FR-021 (origin exposed and filterable); `enums.py` gains the one live value the enum omitted, without which no profile could express `ProductionSites` (research R9). `deps.py` and `references.py` are deliberately untouched. See Complexity Tracking. |
| IV. Goal-Driven Execution | ✅ Pass | Each story is an independently testable slice; [quickstart.md](./quickstart.md) gives 9 scenarios plus Gate 0 and a rollback check. Scenario 3 pins the atomicity FR-011 requires; Scenario 6 pins the dialect trap. |
| V. Reuse Over Rebuild | ⚠️ Justified | `assert_not_referenced`, `assert_unique`, `EntityStatus`, `SystemObject`, `AccessRight`, `require_admin` and the `AccessPrivilege` model all carry this unchanged — two of them (R4, R5) after checking that a SQL expression and a new FK need no modification. `batch_fetch` was planned and **rejected after measuring**: it loads entities and so triggers a selectin load of every profile's masks to render a name. **New**: 2 tables, 2 models, 1 service, 1 endpoint module, 7 schemas. Justified in Complexity Tracking — there is no existing template concept to extend. |
| VI. Async-First | ✅ Pass | Every new route `async def` over `AsyncSession`. The apply's delete is `user.privileges.clear()` under the existing `delete-orphan` cascade, flushed within the awaited commit. The profile-name lookup is a plain awaited `select`. |
| VII. Security by Default | ✅ Pass | All six new endpoints gated by `require_admin` (FR-028); `401` from `get_current_user` (FR-029). No new `SystemObject` value, so the permission vocabulary is unchanged. An apply bumps `session_version` (FR-015) exactly as `update_user` does. `PUT /users/{id}` deliberately does **not** accept `profile_id` — the origin is writable only by the operation that earns it, so it cannot record a claim that was never true. Enforcement is not edited. |
| VIII. Ruff Compliance | ✅ Pass | Rule set E, F, I, UP at 100 columns. New `mapped_column` calls use trailing-comma multi-line style per the constitution's E501 note. Verified by `uv run ruff check app/ migrations/ tests/`. |

**Testing gate (Constitution v1.2.0)**: tests are **REQUIRED**, no exemption claimed. The new
`user_profile_service` carries branching logic (sparse-entry dropping, status refusal, uniqueness,
mask validation) and needs `tests/unit/` coverage of each branch directly. `user_service` gains
branches (profile at create, atomic refusal) and its unit file is extended. `tests/api/` covers all
six new endpoints for happy path, 401, 403, 404, 409 and 422. `tests/integration/` is the layer that
catches the R4 dialect trap and the 107-row write, so it gets the create-with-profile and apply flows
end to end. Tests are written first and confirmed failing.

**Gate result**: PASS with two justified deviations, recorded in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/014-user-profiles/
├── plan.md              # This file
├── research.md          # Phase 0 — 8 decisions, incl. the audit that could not run (R0)
├── data-model.md        # Phase 1 — 2 tables, 1 column, the apply as a data operation
├── quickstart.md        # Phase 1 — Gate 0 (pre-migration audit) + 9 scenarios
├── contracts/
│   └── README.md        # Phase 1 — 6 new endpoints, 3 changed additively
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
migrations/
├── 014_user_profiles.sql                 # NEW: 2 CREATE TABLE; ADD COLUMN user.profile;
│                                         #   0 rows seeded; idempotent
└── 014_user_profiles_rollback.sql        # NEW: drop column, then privilege table, then profile

app/
├── enums.py                              # EDIT: + PRODUCTION_SITES = 107, replacing the wrong
│                                         #   `# 107 absent` comment (research R9)
├── models/user.py                        # EDIT: + UserProfile, + UserProfilePrivilege,
│                                         #   + User.profile_id (nullable FK)
├── schemas/user.py                       # EDIT: + 7 schemas; UserCreate/UserResponse/
│                                         #   UserListItem gain profile fields
├── services/
│   ├── user_profile_service.py           # NEW: CRUD + apply_to_user; sparse-entry handling;
│   │                                     #   assert_unique on func.lower (R4);
│   │                                     #   assert_not_referenced on delete (R5)
│   └── user_service.py                   # EDIT: create_user takes an optional profile and
│                                         #   commits once (R8); _write_privileges_from helper
│                                         #   replaces the existing zero-seeding loop;
│                                         #   list_users gains a profile filter + name lookup
├── api/v1/endpoints/
│   ├── user_profiles.py                  # NEW: 6 routes, all require_admin
│   └── users.py                           # EDIT: profile_id query param on list; create passes
│                                         #   it through. PUT untouched
└── api/v1/router.py                      # EDIT: + include_router(user_profiles, '/user-profiles')

tests/
├── api/
│   └── test_user_profiles.py             # NEW: 6 endpoints × (200/201/204, 401, 403, 404,
│                                         #   409 name, 409 referenced, 409 inactive, 422)
├── unit/
│   ├── test_user_profile_service.py      # NEW: sparse drop, zero-mask equivalence, unknown
│   │                                     #   object, status refusal, uniqueness incl. case
│   ├── test_user_service.py              # NEW or EDIT: create-with-profile atomicity, the
│   │                                     #   107-row write, origin recorded, filter
│   └── test_migrate.py                   # EDIT: + 014 discovery, ordered after 013,
│                                         #   rollback not auto-applied
└── integration/
    └── test_user_profiles_flow.py        # NEW: real SQL — create profile, create user from it,
                                          #   apply, full-replace, case-insensitive 409,
                                          #   referenced-delete 409, one-query profile_name
```

> Checked rather than assumed: **there is no `tests/unit/test_user_service.py`** and no
> `tests/api/test_users.py` — user endpoints are covered inside `tests/api/test_auth.py`. So the
> unit file is NEW, and whether the API tests extend `test_auth.py` or start a dedicated file is a
> `/speckit-tasks` decision; this plan does not pre-empt it. `tests/unit/test_model_schema.py` needs
> **no** edit, because its existing migration parsing already accepts tables from a `CREATE TABLE` and
> columns from an `ADD COLUMN` (research R6) — a first draft of this tree wrongly listed it.

**Structure Decision**: The existing single-project layout is kept. One new service module and one
new endpoint module, both following the naming and shape of their neighbours (`price_list_service.py`
/ `price_lists.py`). The two new models live in `models/user.py` beside `User` and `AccessPrivilege`
rather than in `core.py`, because they are only reachable through a user or their own endpoints.

## Delivery Phases

The spec's three stories, ordered so each lands as a working, testable slice. **Phase 0 is a hard
prerequisite for everything after it.**

| Phase | Delivers | Stories | Verify |
|---|---|---|---|
| **0 — Schema & models** | `014` + rollback; `UserProfile`, `UserProfilePrivilege`, `User.profile_id` | — | Gate 0's three audit queries answered; applies and rolls back on a copy; idempotent on re-apply; `test_model_schema` and `test_migrate` green |
| **1 — Profile catalog** | `user_profile_service`, `user_profiles.py`, 5 CRUD routes, 7 schemas | US2 | Quickstart Scenarios 6 and 7 — case-insensitive `409` **in the SQLite layer**, referenced-delete `409` naming the blocker, unreferenced `204` |
| **2 — The apply** | `PRODUCTION_SITES = 107`; `apply_to_user`; `_write_privileges_from`; the 107-row full replace; `session_version` bump | US1 (FR-010, FR-013–FR-018) | Quickstart Scenario 2 — a hand-granted permission the profile omits comes back `0`; Scenario 8 — inert on an administrator |
| **3 — Provision at creation** | `UserCreate.profile_id`; one-commit create; `404`/`409` leaving no user | US1 (FR-011) | Quickstart Scenarios 1 and 3 — 107 entries on the response, and **no account left behind** by either refusal |
| **4 — Origin visible & filterable** | `profile_id`/`profile_name` on `UserResponse` and `UserListItem`; the list filter; one-query name lookup | US3 | Quickstart Scenario 5 — filtered list returns exactly those accounts, each row naming its profile, at **one** query for the page |

Phase 2 must land before Phase 3: creation-with-profile reuses the apply's privilege-writing helper,
and building it first would mean writing that loop twice. Phase 4 is independent of 2 and 3 and could
ship in either order, but is last because it is the least valuable alone.

**These phase numbers are not tasks.md's.** This table is a delivery narrative; `tasks.md` numbers its
own phases from 1. The mapping:

| This plan | tasks.md |
|---|---|
| — | 1 — Setup |
| 0 — Schema & models | 2 — Foundational |
| 1 — Profile catalog | 3 — US2 |
| 2 — The apply | 4 — US1 (apply) |
| 3 — Provision at creation | 5 — US1 (create) |
| 4 — Origin visible & filterable | 6 — US3 |
| — | 7 — Polish |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| **Six existing app files edited** (Principle III) | FR-011 puts a profile on user creation, which reaches `schemas/user.py`, `services/user_service.py` and `api/v1/endpoints/users.py`. FR-019–FR-021 expose and filter the origin, reaching the same three plus `models/user.py`. `router.py` registers the new module — one line. `enums.py` gains `PRODUCTION_SITES = 107` | Confining the feature to new files was drafted: profiles would be a standalone catalog with an apply endpoint and nothing on the user side. It fails FR-011 (atomic create), FR-020 (origin readable) and FR-021 (filterable) — three clarified decisions. Deferring the `enums.py` line to a follow-up was offered and declined: without it no profile can express `ProductionSites`, so full replace would have to carve out an object indefinitely (research R9). `deps.py` and `references.py`, the two files where an edit would be genuinely risky, are untouched |
| **2 new tables, 2 models, 1 service, 1 endpoint module, 7 schemas** (Principle V) | There is no existing template, role or grouping concept in this codebase to extend — `grep -ril profile app/` returns one unrelated hit in `auth.py`. A permission template is a new noun and needs somewhere to live | Reusing `access_privilege` with a nullable `user` and a new `profile` column was drafted and rejected: it makes a `NOT NULL` FK nullable on the one table the authorization path reads, and every existing query would need a `profile IS NULL` filter it does not currently have — maximum blast radius on the most sensitive table, to save one table (research R6). Serializing masks into a column on `user_profile` was rejected because they are edited and validated per object, which `access_privilege` already models |

## Known-open items carried forward

**No blockers.** R9 was the one, and it is resolved (research R9).

1. **`SystemObject` gains `PRODUCTION_SITES = 107`** in this feature, replacing the incorrect
   `# 107 absent` comment, with the matching row added to `docs/constants.md` — whose table skips 107
   too. The enum's other absences (31, 70, 76–78, 104, 105) mirror legacy's commented-out set exactly
   and are correct as they stand.
2. **An apply removes the 59 rows on retired objects 70/104/105**, 24 of them grants, lazily per
   account. Accepted and irreversible through the API; the evidence that these features are retired is
   the legacy source, not an inference (research R9).
3. **`access_privilege` has no unique index on `(user, object)`** while `deps.py:90` raises on
   duplicates. Zero duplicates measured, so a low-risk follow-up rather than a prerequisite
   (research R2). Its own issue.
4. **Two pre-existing model/schema disagreements**, neither touched here: `session_version` defaults to
   `1` in the database and `0` in the model (research R7, no impact — FR-015 uses a relative
   increment), and `user.password` is `varchar(40)` in `mbe_dev` while the model declares `String(255)`
   for a bcrypt migration that has not been applied. A bcrypt hash would be truncated today. Its own
   issue.
