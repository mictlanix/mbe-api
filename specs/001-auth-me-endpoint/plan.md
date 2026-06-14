# Implementation Plan: Self-Service Profile Endpoint (`/auth/me`)

**Branch**: `001-auth-me-endpoint` | **Date**: 2026-06-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-auth-me-endpoint/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Add `GET /api/v1/auth/me`, returning the authenticated caller's own `UserResponse` (user_id, email, employee_id, administrator, disabled, session_version, settings, privileges). Gated by `get_current_user` only (no `require_admin`), so any authenticated, non-disabled user with a valid session can bootstrap their session/RBAC state — fully reusing existing schema, dependency, and service-layer lookup with no new entities or migrations.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy (async ORM), python-jose (JWT), Pydantic

**Storage**: MySQL via `aiomysql` / SQLAlchemy async session (existing `User` model, no schema changes)

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport` (existing pattern in `tests/api/`)

**Target Platform**: Linux server (FastAPI ASGI service)

**Project Type**: Single web-service project (existing `app/` FastAPI app)

**Performance Goals**: N/A — single primary-key lookup, same cost as existing `GET /users/{user_id}`

**Constraints**: Must reuse existing `UserResponse` schema, `get_current_user` dependency, and `user_service.get_user`; no new fields, models, or migrations

**Scale/Scope**: One new route handler in `app/api/v1/endpoints/auth.py`; no new modules

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the unfilled template (no project-specific gates ratified yet). Falling back to `CLAUDE.md` guidelines: minimum code to satisfy the spec, no speculative abstractions, surgical changes only.

- This feature adds one route handler and reuses existing schema/dependency/service — no new abstractions, models, or configuration. **PASS**.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
app/
├── api/v1/
│   ├── router.py              # existing — /auth prefix already registered, no change
│   └── endpoints/
│       └── auth.py            # add GET /me handler here
├── core/
│   └── deps.py                # get_current_user (existing, reused, unchanged)
├── schemas/
│   └── user.py                # UserResponse (existing, reused, unchanged)
└── services/
    └── user_service.py        # get_user (existing, reused, unchanged)

tests/
└── api/
    └── test_auth.py           # new — tests for GET /api/v1/auth/me
```

**Structure Decision**: Single existing FastAPI project (`app/`). The new endpoint is one handler added to `app/api/v1/endpoints/auth.py` under the already-registered `/auth` prefix. No new modules, schemas, services, or migrations — fully reuses `get_current_user`, `UserResponse`, and `user_service.get_user`.

## Complexity Tracking

N/A — Constitution Check passed with no violations.
