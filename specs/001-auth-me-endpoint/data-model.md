# Data Model: Self-Service Profile Endpoint (`/auth/me`)

No new entities, fields, or migrations. This feature is read-only and reuses existing models/schemas.

## Entity: User Profile (response)

Maps directly to the existing `UserResponse` schema (`app/schemas/user.py`), backed by the existing `User` model and its `settings` / `privileges` relationships.

| Field             | Type                          | Notes                                              |
|-------------------|-------------------------------|----------------------------------------------------|
| `user_id`         | `str`                          | Primary key / username                              |
| `email`           | `str`                          |                                                      |
| `employee_id`     | `int \| None`                  |                                                      |
| `administrator`   | `bool`                         |                                                      |
| `disabled`        | `bool`                         | Always `false` for a caller reaching this endpoint, since `get_current_user` rejects disabled users |
| `session_version` | `int`                          | Must match the value embedded in the caller's token |
| `settings`        | `UserSettingsResponse \| None` | `store_id`, `point_sale_id`, `cash_drawer_id`       |
| `privileges`      | `list[PrivilegeResponse]`      | May be empty                                        |

## Source of truth

- **Identity**: `current_user.user_id` from `CurrentUser` (`app/core/deps.py`), derived from the JWT `sub` claim — never from a request parameter.
- **Data lookup**: `user_service.get_user(db, current_user.user_id)` — identical lookup used by `GET /users/{user_id}`.
