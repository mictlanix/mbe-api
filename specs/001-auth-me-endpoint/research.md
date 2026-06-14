# Research: Self-Service Profile Endpoint (`/auth/me`)

No `[NEEDS CLARIFICATION]` markers remain in the spec, and the technology stack is fully fixed by the existing codebase. The decisions below confirm reuse of existing components over introducing new ones.

## Decision: Route placement

- **Decision**: Add `GET /me` to `app/api/v1/endpoints/auth.py` (mounted at `/api/v1/auth/me` via the existing `/auth` prefix in `app/api/v1/router.py`).
- **Rationale**: Matches the issue's stated rationale — avoids route-ordering ambiguity with `/users/{user_id}` in `users.py`, and groups with `/auth/login` as part of the session-bootstrap flow.
- **Alternatives considered**: `/users/me` in `users.py` — rejected because it would require careful route ordering relative to `/users/{user_id}` and mixes self-service with admin-only user management.

## Decision: Authorization dependency

- **Decision**: Gate the endpoint with `get_current_user` (existing dependency in `app/core/deps.py`), not `require_admin`.
- **Rationale**: `get_current_user` already enforces token validity, non-disabled status, and `session_version` matching — exactly the rules in FR-002 and FR-004. No new auth logic needed.
- **Alternatives considered**: A new "self or admin" dependency — unnecessary; the endpoint never takes a `user_id` parameter, so "self" is the only possible target.

## Decision: Response data and lookup

- **Decision**: Reuse `UserResponse` (`app/schemas/user.py`) and `user_service.get_user(db, user_id)` (`app/services/user_service.py`), called with `current_user.user_id` from the `CurrentUser` dependency result.
- **Rationale**: Identical shape/data to `GET /users/{user_id}`, satisfying FR-003. `get_user` already eager-loads `settings` and `privileges` (used by the existing admin endpoint), so no new query logic is required.
- **Alternatives considered**: A dedicated "me" schema — rejected as unnecessary duplication; the issue explicitly requires the existing `UserResponse` shape.

## Decision: Testing approach

- **Decision**: New `tests/api/test_auth.py` using the existing `httpx.ASGITransport` + `AsyncClient` pattern from `tests/api/test_health.py`, covering: non-admin success, admin success, missing token (401), and session-version mismatch (401).
- **Rationale**: Matches established project test conventions; no new test infrastructure needed.
- **Alternatives considered**: None — pattern is already established.

**Output**: All technical unknowns resolved by direct reuse of existing, already-tested components.
