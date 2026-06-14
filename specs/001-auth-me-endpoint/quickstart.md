# Quickstart: Validate `GET /api/v1/auth/me`

## Prerequisites

- App running locally (see [README.md](../../README.md)) with a seeded non-admin user (`USER_ID`/`PASSWORD`) and an admin user.

## 1. Automated tests

```bash
uv run pytest tests/api/test_auth.py -v
```

Expected: all cases pass, covering non-admin success, admin success, missing token (401), and stale `session_version` (401) — see [contracts/auth-me.md](./contracts/auth-me.md).

## 2. Manual end-to-end check (non-admin user)

```bash
# 1. Log in as a non-admin user
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=USER_ID&password=PASSWORD" | jq -r .access_token)

# 2. Fetch own profile — should succeed (200), previously would 403
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me | jq
```

**Expected**: HTTP 200 with a `UserResponse` body containing `user_id`, `email`, `employee_id`, `administrator: false`, `disabled: false`, `session_version`, `settings`, and `privileges`.

## 3. Regression check — admin user

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=ADMIN_ID&password=ADMIN_PASSWORD" | jq -r .access_token)

curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me | jq
```

**Expected**: HTTP 200, same shape, `administrator: true`.

## 4. Negative check — no token

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/auth/me
```

**Expected**: `401`.

## Success criteria mapping

- SC-001 → Step 2 succeeds without a 403.
- SC-002 → Step 4 (and stale-`session_version`/disabled-user cases in `test_auth.py`) returns 401.
- SC-003 → Steps 2 and 3 both succeed with the same response shape, confirming the bootstrap sequence works for both account types.
