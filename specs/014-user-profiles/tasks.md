---

description: "Task list for user profiles as permission templates"
---

# Tasks: User Profiles as Permission Templates

**Input**: Design documents from `/specs/014-user-profiles/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/README.md](./contracts/README.md),
[quickstart.md](./quickstart.md)

**Tests**: **REQUIRED, not optional.** Constitution v1.2.0 removed the carve-out the upstream
template still describes — services with branching logic need `tests/unit/` coverage of those
branches, endpoints need `tests/api/` coverage, and tests are written first and confirmed failing.
This feature also has a third obligation: `tests/integration/` is the only layer that catches the
SQLite/MariaDB collation divergence (research R4) and the real 107-row write. No exemption is claimed.

**Organization**: Grouped by user story. Phase 2 blocks everything.

> **⚠️ Phase order is dependency order, not priority order.** US2 (P2, the catalog) comes before US1
> (P1, provisioning) because there is nothing to apply until a profile can be created through the API.
> The alternative — hand-seeding a profile row with SQL to test US1 first — tests a path no client
> uses. [013's tasks.md](../013-facility-transit-warehouses/tasks.md) made the same departure for the
> same reason. Priorities are labelled on each phase so the tradeoff stays visible.

> **T003 was a blocking decision and is now resolved** — see the task for the answer and
> [research.md](./research.md) R9 for the evidence. T003a re-verifies its basis before Phase 4, because
> that decision is what authorises deleting 24 granted permission rows.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US3 from [spec.md](./spec.md)

---

## Phase 1: Setup

**Purpose**: Establish the baseline and re-verify the one decision that authorises deleting data. No
scaffolding — the project layout is unchanged.

- [X] T001 Confirm a green baseline: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q` both clean (expect ~1543 tests). Record the test count — Phase 7 asserts no pre-existing test was modified
- [X] T002 Re-run the Gate 0 audit queries from [quickstart.md](./quickstart.md) against the target database and confirm the measured counts still hold: **0** duplicate `(user, object)` pairs, **31** users, `min(rows per user) = 106`, **88** rows on objects 70/104/105/107 of which **28** carry a grant. **If any has moved, stop** — [research.md](./research.md) R2 and R3 both rest on these
- [X] T003 **DECISION GATE — [research.md](./research.md) R9: RESOLVED 2026-08-12.** The four unknown object values were named from `../mbe/Model/Constants/SystemObjects.cs`: **70, 104, 105 are commented out** there (retired features), **107 `ProductionSites` is active** and the enum omits it behind a wrong `# 107 absent` comment. **Decision 1**: add `PRODUCTION_SITES = 107` (T004a) — matrix becomes **107 wide**. **Decision 2**: FR-013 is read literally — T024 is a blanket `user.privileges.clear()` and T027 asserts the retired rows are **gone**. Exporting them first was offered and declined
- [X] T003a Re-verify the basis of T003 before Phase 4: `grep -cE '^\s*[A-Za-z]\w*\s*=\s*[0-9]+\s*,' ../mbe/Model/Constants/SystemObjects.cs` returns **107**, and a diff of that file's active values against `SystemObject` shows **exactly one** missing (107). **Any other result means the legacy catalog moved and R9 needs re-deciding** — it is the sole justification for deleting 24 granted rows

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Two tables, one column, one enum value, one migration. Nothing else can start.

**⚠️ BLOCKS ALL USER STORIES**

- [X] T004 [P] Write a failing test in `tests/unit/test_migrate.py` asserting `014_user_profiles` is discovered by the runner, ordered after `013`, and that `014_user_profiles_rollback` is **not** auto-applied — following the existing `008`–`011` test pattern in that file
- [X] T004a Add `PRODUCTION_SITES = 107` to `SystemObject` in `app/enums.py`, **deleting the `# 107 absent` comment on line 286 — it is factually wrong** (research R1, R9). Leave the other absence comments alone: 31, 70, 76–78, 104 and 105 mirror the legacy catalog's commented-out set exactly and are correct. Add the matching row to `docs/constants.md`, whose table also skips 107 (`| 107 | ProductionSites | ... |`, between 106 and 108)
- [X] T004b [P] Write a test in `tests/unit/` asserting `len(list(SystemObject)) == 107` and that `SystemObject(107).name == 'PRODUCTION_SITES'`. This is the guard that the matrix width every other test asserts is not silently changed again
- [X] T005 Add `UserProfile` to `app/models/user.py` per [data-model.md](./data-model.md): `user_profile_id` PK, `name` `String(100)`, `description` `String(250) | None`, `status` mapped to `EntityStatus` with `default=EntityStatus.ACTIVE, server_default='0'`, and a `privileges` relationship with `cascade='all, delete-orphan', lazy='selectin'`. Multi-line trailing-comma style on long `mapped_column` calls (Constitution VIII)
- [X] T006 Add `UserProfilePrivilege` to `app/models/user.py`: `user_profile_privilege_id` PK, `user_profile_id` FK via `mapped_column('user_profile', ...)`, `system_object` via `mapped_column('object', Integer)` — **aliased exactly as `AccessPrivilege` does, because `object` is a Python builtin** — `privileges` `Integer` default 0, plus the same four `allow_*` computed properties `AccessPrivilege` exposes
- [X] T007 Add `profile_id: Mapped[int | None] = mapped_column('profile', Integer, ForeignKey('user_profile.user_profile_id'))` to `User` in `app/models/user.py`. **Nullable is load-bearing** — every existing account has no origin (FR-017). No `ON DELETE SET NULL`: the plain FK is what makes `assert_not_referenced` refuse a referenced delete for free (research R5)
- [X] T008 Write `migrations/014_user_profiles.sql` per [data-model.md](./data-model.md): `CREATE TABLE IF NOT EXISTS user_profile` with `UNIQUE KEY` on `name` and explicit `COLLATE utf8mb3_unicode_ci`; `CREATE TABLE IF NOT EXISTS user_profile_privilege` with the FK to `user_profile`; `ALTER TABLE user ADD COLUMN IF NOT EXISTS profile INT(11) NULL` plus its FK. **Seed zero rows** (FR-017 — no account is migrated). Header comment states the measured audit from T002, including the 88 unknown-object rows and why they are untouched, as migration 012's header states its own
- [X] T009 Write `migrations/014_user_profiles_rollback.sql`: drop `user.profile` (the FK first), then `user_profile_privilege`, then `user_profile` — **FK order matters**. Header must state that rolling back **discards every profile and every recorded origin while leaving all `access_privilege` rows intact**, so no user loses a permission — permissions are values, origins are pointers
- [X] T010 **DONE 2026-08-12, on `mbe_dev` rather than a copy.** A copy was impossible: `mbe-dev` holds `ALL PRIVILEGES` on `mbe_dev` and only `USAGE` on `*.*`, so it cannot `CREATE DATABASE`, and DDL is non-transactional in MariaDB so apply-and-abort was not a substitute either. Run with the owner's go-ahead as: apply → verify → re-run all 5 statements (no-ops, nothing duplicated) → rollback (schema fully reverted) → re-apply, ending applied. **Measured throughout: 31 users and 3,355 `access_privilege` rows unchanged at every step, including the 59 retired-object rows and the 29 object-107 rows — the DDL touches no existing row in either direction.** Structure confirmed: `user_profile.name` UNIQUE under `utf8mb3_unicode_ci`, `user_profile_privilege` FK to it, `user.profile` `int(11)` nullable with FK `user_profile_user`, 0 profiles seeded, 0 accounts given an origin. `migrate status` reports 0 pending. Suite still 1844 passed / 10 skipped
- [X] T011 Run `uv run pytest tests/unit/test_model_schema.py -q` and confirm it passes **with no edit to that file** — the new tables should be accepted via `CREATED_BY_MIGRATION` and `user.profile` via `ADDED_BY_MIGRATION` (research R6). **If it fails, research R6 was wrong** and the parsing needs re-examining before proceeding

**Checkpoint**: schema and models exist, nothing reads them yet.

---

## Phase 3: User Story 2 — Maintain the profile catalog (Priority: P2)

**Goal**: An administrator can create, list, inspect, edit, retire and delete permission templates.

**Independent test**: Create several profiles, list them, retrieve one and confirm its sparse entry
set reads back, edit name and masks and confirm both persist, then delete an unapplied one.

- [X] T012 [P] [US2] Add the profile schemas to `app/schemas/user.py` per [contracts/README.md](./contracts/README.md): `ProfilePrivilegeResponse`, `ProfilePrivilegeUpdate` (`privileges` `Field(ge=0, le=15)`), `UserProfileCreate`, `UserProfileUpdate`, `UserProfileResponse`, `UserProfileListItem` (**no `privileges` field** — a catalog page must not fetch masks it will not render), `UserProfileListResponse`
- [X] T013 [P] [US2] Add two Pydantic validators to the profile schemas in `app/schemas/user.py`: reject a `system_object` outside `SystemObject` (FR-010 → 422), and reject a payload naming the same object twice (FR-002 → 422)
- [X] T014 [P] [US2] Write failing unit tests in `tests/unit/test_user_profile_service.py` (**new file**) for each branch: a zero-mask entry is dropped rather than stored (FR-003); a profile with no entries is valid; an unknown `system_object` is refused; a duplicate object in one payload is refused; `PUT` with `privileges` replaces the whole set while omitting the key leaves entries untouched; `assert_unique` is called against a **lowercased** comparison (FR-004)
- [X] T015 [P] [US2] Write failing API tests in `tests/api/test_user_profiles.py` (**new file**) for the five CRUD routes: `201`/`200`/`204` happy paths, `401` unauthenticated, `403` non-administrator, `404` unknown id, `409` duplicate name, `422` bad mask and unknown object. Follow the `dependency_overrides` pattern in `tests/api/test_facilities.py`
- [X] T016 [US2] Create `app/services/user_profile_service.py` with `get_profile`, `list_profiles` (search on name + `status` filter + `skip`/`limit`, mirroring `warehouse_service.list_warehouses`), `create_profile`, `update_profile`, `delete_profile`
- [X] T017 [US2] In `user_profile_service`, enforce name uniqueness with `assert_unique(db, UserProfile, func.lower(UserProfile.name), name.lower(), exclude_pk=..., label='Profile name')`. **`func.lower` is not redundant** — MariaDB's `utf8mb3_unicode_ci` would handle it but SQLite's `=` on TEXT is case-sensitive, so without this the integration layer and production disagree about what a conflict is (research R4). **Do not modify `app/services/references.py`** — its `column` parameter already accepts a SQL expression
- [X] T018 [US2] In `user_profile_service.delete_profile`, call `assert_not_referenced(db, profile)` and nothing else. It derives the blocking tables from FK metadata, so FR-008's "refused, naming how many users reference it" needs no new code (research R5)
- [X] T019 [US2] In `user_profile_service`, drop zero-mask entries on create and update so "no entry" is the single representation of "denied" and a round-trip is stable (FR-003)
- [X] T020 [US2] Create `app/api/v1/endpoints/user_profiles.py` with the five CRUD routes from [contracts/README.md](./contracts/README.md), every one `async def` and gated by `Depends(require_admin)` (FR-028). Follow the shape of `app/api/v1/endpoints/price_lists.py`
- [X] T021 [US2] Register the module in `app/api/v1/router.py`: `include_router(user_profiles.router, prefix='/user-profiles', tags=['user-profiles'])`, and add `user_profiles` to the import block in sorted position (ruff I001)
- [X] T022 [P] [US2] Write an integration test in `tests/integration/test_user_profiles_flow.py` (**new file**) driving create → list → get → update → delete against real SQL, and asserting **`409` for `"cashier"` against a stored `"Cashier"`**. This assertion is the reason T017 exists and it only fails if `func.lower` was skipped

**Checkpoint**: US2 is independently deliverable — quickstart Scenarios 6 and 7 pass.

---

## Phase 4: User Story 1a — Applying a profile (Priority: P1)

**Goal**: An administrator can copy a profile's permissions onto an existing account.

**Independent test**: Apply a profile to a user, read the user back, confirm the granted objects match
and every other known object is denied.

**T003 resolved**: T024 is a blanket clear, T027 asserts the retired rows are gone. T003a re-verifies the legacy catalog before this phase starts.

- [X] T023 [P] [US1] Write failing unit tests in `tests/unit/test_user_service.py` (**new file — it does not exist today**) for the privilege-writing helper: **107 rows** written for a profile granting three objects; a mask the profile omits comes out `0`; a permission the user held on an omitted object is gone (FR-013); the helper stages without committing
- [X] T024 [US1] Add `_write_privileges_from(user, profile)` to `app/services/user_service.py` per [data-model.md](./data-model.md): `user.privileges.clear()`, then append one `AccessPrivilege` per `SystemObject` member with `masks.get(int(obj), 0)` — **107 rows** after T004a. The blanket clear is T003's Decision 2, and it removes the 59 rows on retired objects 70/104/105. Iterate the **enum**, not `range(0, 114)` — the remaining gaps are declared absences that mirror the legacy catalog (research R1). Stages only; the caller commits
- [X] T025 [US1] Replace the zero-seeding loop in `user_service.create_user` with a call to `_write_privileges_from(user, None)` so create and apply share one code path (research R8). This **replaces** existing code rather than adding a layer — verify the no-profile create still writes a full set of denied rows exactly as before, now **107** (FR-027)
- [X] T026 [P] [US1] Write failing unit tests in `tests/unit/test_user_profile_service.py` for `apply_to_user`: an inactive profile is refused (FR-017); a missing profile and a missing user each raise not-found (FR-016); `session_version` is incremented (FR-015); `profile_id` is recorded, replacing any previous value (FR-019); the whole thing is one commit (FR-018)
- [X] T027 [P] [US1] Write a failing test pinning T003's Decision 2: seed a user with rows on objects 70, 104 and 105, apply a profile, and assert **those rows are gone** while object 107 is present with the profile's mask (or 0). This is the single test that would catch a regression to either superseded version of research R3, so it must exist rather than be remembered
- [X] T028 [US1] Add `apply_to_user(db, profile, user)` to `app/services/user_profile_service.py`: refuse a non-`ACTIVE` profile with `409 Profile is not active` (FR-017), call `_write_privileges_from`, set `user.profile_id`, increment `user.session_version`, and `commit()` **once** (FR-018)
- [X] T029 [US1] Add `POST /{id}/apply/{user_id}` to `app/api/v1/endpoints/user_profiles.py` returning `200` with the full `UserResponse`, per [contracts/README.md](./contracts/README.md). `404` for either a missing profile or a missing user; the profile's `404` comes from the same path resolution the other `/{id}` routes use
- [X] T030 [P] [US1] Write failing API tests in `tests/api/test_user_profiles.py` for the apply route: `200`, `404` unknown profile, `404` unknown user, `409` inactive profile, `403` non-administrator, `401` unauthenticated
- [X] T031 [P] [US1] Extend `tests/integration/test_user_profiles_flow.py`: apply against real SQL, assert the response carries **107** privilege entries with the right masks, and assert a hand-granted permission on an object the profile omits comes back `0` (quickstart Scenario 2)

**Checkpoint**: US1's apply half works — quickstart Scenarios 2 and 8 pass.

---

## Phase 5: User Story 1b — Provisioning at creation (Priority: P1)

**Goal**: Creating a user can name a profile and get a fully provisioned account in one action.

**Independent test**: Create a user naming a profile; the response carries 107 entries matching the
profile and records the origin. Create one naming a bad profile; no account exists afterwards.

- [X] T032 [P] [US1] Write failing unit tests in `tests/unit/test_user_service.py` for create-with-profile: the 107 rows come from the profile; `profile_id` is recorded; a missing profile and an inactive profile each raise **before anything is staged**; creating without a profile is unchanged (FR-027)
- [X] T033 [US1] Add `profile_id: int | None = None` to `UserCreate` in `app/schemas/user.py`. **Do not add it to `UserUpdate`** — setting an origin without copying permissions would record a claim that was never true (FR-022, [contracts/README.md](./contracts/README.md))
- [X] T034 [US1] Rework `user_service.create_user` to resolve and validate the profile **before staging the user**, then write its masks via `_write_privileges_from` and set `profile_id`, all under **one** `commit()` (FR-011, research R8). **Nothing may run after the commit** — `create_customer` shipped a 500-after-commit bug of exactly this shape (CHANGELOG #154), which is what T036 asserts
- [X] T035 [P] [US1] Write failing API tests for `POST /users` with `profile_id`: `201` with 107 entries and the origin set, `404 Profile not found`, `409 Profile is not active`. Extend `tests/api/test_auth.py`, which is where user endpoints are currently covered, **or** start `tests/api/test_users.py` — either is fine, but pick one and put all of this feature's user-endpoint tests there
- [X] T036 [US1] Write a failing test asserting that after a `404` or `409` from `POST /users` with a bad profile, **`GET /users/{id}` returns 404** — no account was left behind (FR-011, quickstart Scenario 3). This is the atomicity assertion; a 4xx that created a user is the bug this task exists to prevent
- [X] T037 [P] [US1] Extend `tests/integration/test_user_profiles_flow.py` with create-from-profile end to end against real SQL, including the rollback case where a bad profile leaves no row

**Checkpoint**: US1 complete — quickstart Scenarios 1 and 3 pass. **This is the MVP.**

---

## Phase 6: User Story 3 — Find and re-provision (Priority: P3)

**Goal**: An administrator can see which profile an account came from and find every account on a
profile, so a corrected profile can be re-applied to the right people.

**Independent test**: Apply a profile to two users, filter the user list by it, see exactly those two
with the profile named on each row.

- [X] T038 [P] [US3] Add `profile_id: int | None` and `profile_name: str | None` to both `UserResponse` and `UserListItem` in `app/schemas/user.py` (FR-020)
- [X] T039 [P] [US3] Write failing unit tests in `tests/unit/test_user_service.py`: the profile filter narrows the list; `profile_name` resolves for rows that have an origin and is `None` for rows that do not; **a page of N rows costs one query for profile names, not N** — assert the query count, do not eyeball it
- [X] T040 [US3] Add a `profile_id` filter to `user_service.list_users` (FR-021), following the `status` filter pattern already in that function and applying it to both the base query and the count query
- [X] T041 [US3] Resolve `profile_name` for a page with **one** query. `batch_fetch` was the planned mechanism and measured worse — it loads entities, so `UserProfile.privileges` (`lazy='selectin'`) fires a second query loading every mask of every profile on the page to render a name. Implemented as a two-column projection in `user_service.profile_names_for`; the deviation and its measurement are recorded in the plan and contracts
- [X] T042 [US3] Add the `profile_id` query parameter to `list_users` in `app/api/v1/endpoints/users.py` and pass it through. **`PUT /users/{id}` is not touched** (FR-026, FR-023)
- [X] T043 [P] [US3] Write failing API tests for the filter and the two new response fields, in whichever file T035 chose
- [X] T044 [P] [US3] Extend `tests/integration/test_user_profiles_flow.py`: provision two users from one profile, filter by it, assert exactly those two come back with `profile_name` populated, and assert the one-query cost against real SQL

**Checkpoint**: US3 complete — quickstart Scenario 5 passes.

---

## Phase 7: Polish & Cross-Cutting

- [X] T045 Update `CHANGELOG.md` under `[Unreleased] > Added` **and `Fixed`**, in the established style: what the feature does, the sparse-profile/dense-user asymmetry, and the R9 finding with its measured numbers — `SystemObject` was missing the live `ProductionSites = 107` behind a wrong `# 107 absent` comment (Fixed), and an apply removes 59 rows on three objects the legacy catalog has commented out, 24 of them grants across 13 of 31 accounts (Added, stated as a behaviour change). A reader should learn both from the changelog rather than rediscovering them
- [X] T046 [P] Document `user_profile`, `user_profile_privilege` and `user.profile` in `docs/data-dictionary.md`, including that profile entries are sparse while `access_privilege` is dense — the asymmetry is the thing a reader will get wrong
- [X] T047 Verify `git diff` touches **neither** `app/core/deps.py` **nor** `app/services/references.py`. An edit to either means FR-024 was violated or research R4/R5 was wrong — stop and re-examine rather than accommodating it
- [X] T048 Confirm no pre-existing test was modified to accommodate this feature (SC-005). Compare against T001's recorded count: the total should have grown only by new tests. A changed existing assertion is a finding to report, not a test to fix
- [X] T049 `uv run ruff check app/ migrations/ tests/` — zero violations (Constitution VIII)
- [X] T050 `uv run pytest -q` — all green, all three layers
- [X] T051 **DONE 2026-08-12** against uvicorn on `mbe_dev`. All nine scenarios walked, **34/34 checks passed**, uvicorn log clean (zero 500s). **One deliberate substitution**: Scenario 2 says to pick one of the 13 real accounts holding a retired-object grant, but applying a profile to a real account replaces its whole 107-row matrix — and `admin` is both one of those 13 and the account the walk authenticates as, so applying to it would have bumped `session_version` and invalidated the walk's own token. A probe user was seeded with rows on objects 70/104/105 instead, which exercises the same path on data we own. All probe users and profiles torn down; `mbe_dev` verified back to its pre-walk numbers (31 users, 3,355 privilege rows, 59 retired-object rows, 29 object-107 rows, `admin.session_version` still 4, 0 accounts with an origin, 0 orphan privilege rows)
- [X] T052 [P] File the two follow-ups [plan.md](./plan.md) carries forward, each with its measured evidence: no unique index on `access_privilege(user, object)` against `scalar_one_or_none()` in `deps.py:90` (0 duplicates measured, so low-risk); and `user.password` is `varchar(40)` in `mbe_dev` while the model declares `String(255)` for an unapplied bcrypt migration, so a bcrypt hash would truncate today. The third — the missing enum value — is fixed here by T004a rather than deferred

---

## Dependencies

```
Phase 1 (T001–T003a)
      │
      ├── T003 resolved / T003a re-verifies ───┐
      ▼                                        │
Phase 2 (T004–T011)  schema + models + enum    │
      │              (T004a widens the matrix  │
      ▼               to 107)                  │
Phase 3 (T012–T022)  US2 — catalog             │
      │                                        │
      ▼                                        ▼
Phase 4 (T023–T031)  US1a — apply  ◄───── rests on T003/T003a
      │
      ▼
Phase 5 (T032–T037)  US1b — create   (T034 reuses T024's helper)
      │
      ▼
Phase 6 (T038–T044)  US3 — origin visible      (independent of 4 and 5
      │                                         except for needing Phase 2)
      ▼
Phase 7 (T045–T052)
```

**Hard orderings**:

- **Phase 2 before everything.** No model, no anything.
- **Phase 3 before Phase 4.** There is nothing to apply until a profile can be created.
- **Phase 4 before Phase 5.** T034 calls the helper T024 writes. Building creation first means
  writing that loop twice.
- **T003a before T024 and T027.** T003's decision authorises deleting 24 granted rows; T003a confirms the legacy catalog it was read from has not moved.
- **T004a before T024.** The helper iterates `SystemObject`, so the enum must already be 107 wide or the first apply writes a 106-row matrix and object 107 is silently dropped.
- **Phase 6 needs only Phase 2** — it could ship before Phase 4 or 5. It is last because it is the
  least valuable alone.

**Within each phase**: tests before the implementation they cover, and confirmed failing first
(Constitution: Development Workflow > Testing).

---

## Parallel Opportunities

Tasks marked `[P]` touch different files with no incomplete dependency.

**Phase 2**: T004 (test_migrate) and T004b (enum test) run alongside T005–T007 (models) — different files. T004a is not `[P]`: T004b asserts what it does.

**Phase 3**: T012, T013, T014, T015 all parallel — schemas, validators, unit tests and API tests are
four files. T022 parallels T016–T021 once the schemas exist.

**Phase 4**: T023, T026, T027, T030, T031 are all test files and all parallel with each other.
T024/T025 and T028/T029 are sequential — same files, and T025 depends on T024.

**Phase 5**: T032, T035, T037 parallel. T033/T034 sequential (`schemas/user.py` then
`user_service.py`).

**Phase 6**: T038, T039, T043, T044 parallel. T040/T041/T042 sequential — T040 and T041 are the same
function.

**Phase 7**: T046 and T052 are documentation and issue-filing, parallel with everything.

---

## Implementation Strategy

**MVP = Phases 1, 2, 3 and 4, 5.** That is US1 complete plus the catalog it needs — an administrator
can author a template and provision accounts from it, at creation or afterwards. US3 (Phase 6) makes
correction tractable but nothing is broken without it.

**Incremental delivery**:

1. **Phases 1–3** ship a working profile catalog. Demonstrable on its own: create, inspect, edit,
   retire, delete. Nothing consumes it yet.
2. **Phase 4** makes it useful — permissions can be copied onto existing accounts.
3. **Phase 5** removes the two-step from onboarding, which is the value claim in SC-002.
4. **Phase 6** closes the loop for corrections.

**Stop points**: after Phase 3, after Phase 4, and after Phase 5 the tree is green and shippable.
Phase 6 is additive to response shapes only.

**The likeliest implementation error**, stated once here because it recurs across three phases: a
**profile is sparse** (entries only for what it grants) and a **user is dense** (107 rows, always).
The apply is the translation. Getting this backwards passes many tests and fails FR-003 or FR-013.

---

## Task Count

| Phase | Story | Tasks | Of which tests |
|---|---|---|---|
| 1 — Setup | — | 4 | — |
| 2 — Foundational | — | 10 | 2 |
| 3 — Catalog | US2 (P2) | 11 | 3 |
| 4 — Apply | US1 (P1) | 9 | 5 |
| 5 — Create | US1 (P1) | 6 | 4 |
| 6 — Origin | US3 (P3) | 7 | 3 |
| 7 — Polish | — | 8 | — |
| **Total** | | **55** | **14** |

21 tasks are marked `[P]`.

**Files — 8 new**: `app/services/user_profile_service.py`, `app/api/v1/endpoints/user_profiles.py`,
`migrations/014_user_profiles.sql`, `migrations/014_user_profiles_rollback.sql`,
`tests/unit/test_user_profile_service.py`, `tests/unit/test_user_service.py`,
`tests/api/test_user_profiles.py`, `tests/integration/test_user_profiles_flow.py`.

**10 edited**: `app/enums.py`, `app/models/user.py`, `app/schemas/user.py`,
`app/services/user_service.py`, `app/api/v1/endpoints/users.py`, `app/api/v1/router.py`,
`tests/unit/test_migrate.py`, `CHANGELOG.md`, `docs/data-dictionary.md`, `docs/constants.md`. Possibly a ninth — `tests/api/test_auth.py` — if T035
extends it rather than starting `tests/api/test_users.py`.

**Not edited, by design**: `app/core/deps.py`, `app/services/references.py`,
`tests/unit/test_model_schema.py`. T047 and T011 verify this.
