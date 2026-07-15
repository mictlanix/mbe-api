# Contract: `GET /api/v1/auth/me`

## Request

- **Method**: `GET`
- **Path**: `/api/v1/auth/me`
- **Auth**: `Authorization: Bearer <access_token>` (OAuth2 password bearer, same as all other authenticated endpoints)
- **Parameters**: none

## Responses

### 200 OK

Body: `UserResponse` (existing schema, `app/schemas/user.py`) — same shape as `GET /api/v1/users/{user_id}`.

```json
{
  "user_id": "jdoe",
  "email": "jdoe@example.com",
  "employee_id": 123,
  "administrator": false,
  "disabled": false,
  "session_version": 3,
  "settings": {
    "store_id": 1,
    "store_code": "CMZ",
    "store_name": "CASA MAESTRA ZUMPANGO",
    "point_sale_id": 2,
    "point_sale_code": "01",
    "point_sale_name": "PV ZUMPANGO",
    "cash_drawer_id": 5,
    "cash_drawer_code": "01",
    "cash_drawer_name": "CC ZUMPANGO"
  },
  "privileges": [
    {
      "system_object": 10,
      "privileges": 15,
      "allow_create": true,
      "allow_read": true,
      "allow_update": true,
      "allow_delete": true
    }
  ]
}
```

`settings` may be `null`; `privileges` may be `[]`.

The `*_code` / `*_name` fields are resolved from the referenced store, point of sale, and
cash drawer. They require no catalog-read privileges, since `/auth/me` returns the caller's
own profile. When a `*_id` is `null`, its `*_code` and `*_name` are `null` too.

### 401 Unauthorized

Returned when (identical to all other `get_current_user`-gated endpoints):

- No `Authorization` header / malformed or expired token
- Token's `sub` does not resolve to an existing user
- User is `disabled`
- Token's `session_version` does not match the user's current `session_version`

```json
{
  "detail": "Invalid or expired credentials"
}
```

Includes `WWW-Authenticate: Bearer` header.

## Authorization model

- Gated by `get_current_user` only — **no** `require_admin` check.
- Always returns the caller's own record (`current_user.user_id`); there is no path or query parameter that selects a different user.
