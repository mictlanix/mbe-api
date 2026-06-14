---

description: "Task list for GET /api/v1/auth/me"
---

# Tasks: Self-Service Profile Endpoint (`/auth/me`)

**Input**: Design documents from `/specs/001-auth-me-endpoint/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/auth-me.md](./contracts/auth-me.md), [quickstart.md](./quickstart.md)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Path Conventions

Single project — `app/` and `tests/` at repository root (see [plan.md](./plan.md) Project Structure).

---

## Phase 1: Setup

**No setup tasks required.** This feature reuses the existing FastAPI app, dependencies, schemas, and tooling as-is — no new packages, config, or infrastructure.

---

## Phase 2: Foundational

**No foundational tasks required.** `get_current_user` (`app/core/deps.py`), `UserResponse` (`app/schemas/user.py`), and `user_service.get_user` (`app/services/user_service.py`) already exist and are unchanged.

---

## Phase 3: User Story 1 - Authenticated user bootstraps their own session (Priority: P1) 🎯 MVP

**Goal**: Any authenticated, non-disabled user (admin or not) can call `GET /api/v1/auth/me` and receive their own `UserResponse`.

**Independent Test**: Log in as a non-admin user, call `GET /api/v1/auth/me` with the issued token, and verify a 200 response containing that user's `UserResponse` (user_id, email, employee_id, administrator, disabled, session_version, settings, privileges).

### Implementation for User Story 1

- [X] T001 [US1] Add `GET /me` handler to `app/api/v1/endpoints/auth.py`: depend on `get_current_user`, look up the caller via `user_service.get_user(db, current_user.user_id)`, return `UserResponse.model_validate(user)` (404 if somehow not found, matching existing endpoint conventions)

### Tests for User Story 1

- [X] T002 [US1] In `tests/api/test_auth.py`, add `test_auth_me_returns_own_profile_for_non_admin` and `test_auth_me_returns_own_profile_for_admin`: override `get_current_user`/`get_db` (no real DB — see project decision below) to provide a transient user, call `GET /api/v1/auth/me` with a bearer token, assert `200` and that the response matches `UserResponse` shape (depends on T001)

**Checkpoint**: Non-admin and admin users can both retrieve their own profile via `/api/v1/auth/me` — mbe-ui session bootstrap unblocked.

---

## Phase 4: User Story 2 - Unauthenticated or invalidated session is rejected (Priority: P2)

**Goal**: Requests without a valid, active session never receive profile data from `/auth/me`.

**Independent Test**: Call `GET /api/v1/auth/me` with no token, a token for a disabled user, and a token with a stale `session_version`; verify each returns `401` with no profile data.

### Tests for User Story 2

- [X] T003 [US2] In `tests/api/test_auth.py`, add `test_auth_me_requires_authentication`: call `GET /api/v1/auth/me` with no `Authorization` header, assert `401` (depends on T001)
- [X] T004 [US2] In `tests/api/test_auth.py`, add `test_auth_me_rejects_stale_session_version`: issue a real token via `create_access_token` with `session_version=1`, override `get_db` to return a stored user with `session_version=2`, call `GET /api/v1/auth/me`, assert `401` (depends on T001)
- [X] T005 [US2] In `tests/api/test_auth.py`, add `test_auth_me_rejects_disabled_user`: issue a real token for a user, override `get_db` to return a stored user with `disabled=True`, call `GET /api/v1/auth/me`, assert `401` (depends on T001)

**Checkpoint**: `/auth/me` enforces the same authentication guarantees (`get_current_user`) as every other authenticated endpoint — no new information-leak surface.

---

## Final Phase: Polish & Cross-Cutting Concerns

- [X] T006 [P] Run `uv run ruff check .` and `uv run mypy app` and fix any violations introduced by T001 — clean; pre-existing mypy errors in `inventory.py`/`security.py`/`deps.py` are unrelated to this change
- [ ] T007 Run [quickstart.md](./quickstart.md) manual validation steps end-to-end against a local server — **not run**: requires a live server with a seeded MySQL DB, not available in this environment. Automated tests (T002–T005) cover the same scenarios against a mocked DB.

## Project Decisions

- **Testing approach (T002–T005)**: No DB-backed test fixtures exist in this project (only `tests/api/test_health.py`, which doesn't touch the DB). Per user decision, tests use FastAPI `dependency_overrides` for `get_current_user`/`get_db` with a transient `User` instance and a minimal fake async session — no real database required. This establishes a lightweight pattern reusable for future authenticated-endpoint tests.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup / Foundational**: None — skipped, nothing to do
- **User Story 1 (P1)**: T001 → T002. No dependency on other stories.
- **User Story 2 (P2)**: T003, T004, T005 depend on T001 (the route must exist) but not on T002
- **Polish**: Depends on T001–T005 being complete

### Within Each User Story

- T002, T003, T004, T005 all edit `tests/api/test_auth.py` — complete sequentially, not in parallel
- T001 must land before any test task (route must exist to be tested)

### Parallel Opportunities

- T001 (`app/api/v1/endpoints/auth.py`) and T006 (lint/type-check, after T001) touch different files but T006 depends on T001's content, so effectively sequential
- All test tasks (T002–T005) share `tests/api/test_auth.py`, so run sequentially

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. T001: implement the `/auth/me` handler
2. T002: prove non-admin and admin users can both fetch their own profile
3. **STOP and VALIDATE**: run `uv run pytest tests/api/test_auth.py -v`
4. This alone resolves the issue's blocking problem (non-admins can bootstrap their session)

### Incremental Delivery

1. Phase 3 (US1) → MVP: endpoint works for legitimate users
2. Phase 4 (US2) → confirms no regression in auth guarantees on the new route
3. Final Phase → lint/type-check + full quickstart validation
