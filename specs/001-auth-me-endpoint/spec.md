# Feature Specification: Self-Service Profile Endpoint (`/auth/me`)

**Feature Branch**: `001-auth-me-endpoint`

**Created**: 2026-06-14

**Status**: Implemented

**Input**: User description: "Add a GET /api/v1/auth/me endpoint so non-admin users can fetch their own profile/privileges. Gated only by get_current_user (not require_admin) — any authenticated, non-disabled user with a valid session_version can fetch their own record. Response shape identical to UserResponse (user_id, email, employee_id, administrator, disabled, session_version, settings, privileges). Resolves mictlanix/mbe-api#1 and is a prerequisite for mbe-ui's 001-user-authentication feature, which currently relies on decoding the JWT and calling GET /users/{user_id} (admin-only) to bootstrap session/RBAC state."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Authenticated user bootstraps their own session (Priority: P1)

After logging in, any user (administrator or not) needs to retrieve their own profile — including their settings (default store/point-sale/cash-drawer) and privileges — so the client application can populate its role-based UI state.

**Why this priority**: This is the core problem described in the issue. Without it, non-admin users cannot complete session bootstrap at all, and the `001-user-authentication` feature in mbe-ui is blocked.

**Independent Test**: Log in as a non-admin user, take the issued access token, call the new endpoint, and verify a 200 response containing that user's own profile (matching the existing `UserResponse` shape) with no admin privilege required.

**Acceptance Scenarios**:

1. **Given** a valid, non-disabled, non-administrator user with a valid session, **When** they request their own profile, **Then** the system returns their `UserResponse` (user_id, email, employee_id, administrator, disabled, session_version, settings, privileges) with a 200 status.
2. **Given** a valid administrator user with a valid session, **When** they request their own profile, **Then** the system returns the same response shape (with `administrator: true`) with a 200 status.
3. **Given** an authenticated user whose `settings` are not configured, **When** they request their own profile, **Then** `settings` is returned as `null` and the request still succeeds.

---

### User Story 2 - Unauthenticated or invalidated session is rejected (Priority: P2)

A request without a valid, active session must not return any profile data.

**Why this priority**: Ensures the new low-friction endpoint doesn't become a way to bypass existing authentication guarantees.

**Independent Test**: Call the endpoint with no token, an expired/invalid token, a token for a disabled user, or a token whose session version no longer matches the stored user, and verify each case is rejected with no profile data returned.

**Acceptance Scenarios**:

1. **Given** no credentials are supplied, **When** the endpoint is requested, **Then** the system returns 401 Unauthorized.
2. **Given** an expired or malformed token, **When** the endpoint is requested, **Then** the system returns 401 Unauthorized.
3. **Given** a token belonging to a user that has since been disabled, **When** the endpoint is requested, **Then** the system returns 401 Unauthorized.
4. **Given** a token whose session version no longer matches the user's current session version (e.g. after a password change or forced logout), **When** the endpoint is requested, **Then** the system returns 401 Unauthorized.

---

### Edge Cases

- A token is structurally valid but references a user that no longer exists (deleted after the token was issued): treated the same as an invalid session (401), consistent with existing authenticated endpoints.
- A user has zero privilege entries: the `privileges` list is returned empty, not omitted or null.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide an endpoint that returns the authenticated caller's own user profile.
- **FR-002**: The endpoint MUST be accessible to any authenticated, non-disabled user with a currently valid session — administrator privilege MUST NOT be required.
- **FR-003**: The response MUST contain the same information as the existing user profile representation: user_id, email, employee_id, administrator flag, disabled flag, session_version, settings (store/point-sale/cash-drawer defaults), and the full list of privileges.
- **FR-004**: The endpoint MUST reject requests with missing, invalid, expired, or session-version-mismatched credentials using the same rules as other authenticated endpoints (401 Unauthorized).
- **FR-005**: The endpoint MUST only ever return the caller's own profile, determined from their authenticated identity — never another user's data, and never via a path or query parameter.

### Key Entities

- **User Profile**: The caller's own account record — identity (user_id, email, employee_id), account flags (administrator, disabled), session_version, default operating context (settings: store/point-sale/cash-drawer), and access privileges.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A non-administrator user can retrieve their own profile and privileges immediately after logging in, with zero "forbidden" responses.
- **SC-002**: Every request lacking a valid, active session receives an unauthorized response — no profile data is ever returned without valid credentials.
- **SC-003**: The login → fetch-own-profile session bootstrap sequence succeeds for both administrator and non-administrator accounts.

## Assumptions

- The response shape is identical to the existing user profile representation (`UserResponse`) used elsewhere in the API — no new fields are introduced.
- "Own profile" is determined entirely from the authenticated session's identity, not from any caller-supplied identifier.
- This feature is additive: existing admin-only user-management endpoints are unchanged.
