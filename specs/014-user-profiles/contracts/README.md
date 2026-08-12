# Phase 1 — Contracts: User Profiles as Permission Templates

**Six new endpoints on one new resource, three existing endpoints changed.** No existing response
field is removed or retyped; every change to `/users` is additive.

Everything below is administrator-gated via the existing `require_admin` dependency — **no new
`SystemObject` value gates these endpoints** (FR-028, and spec Assumptions: gating them on a system
object would mean extending the permission vocabulary this feature must otherwise leave alone).
`PRODUCTION_SITES = 107` is added to that enum (research R9), but as a *catalog correction* — it gates
nothing here and this API exposes no production-sites endpoint. Unauthenticated requests answer `401`
from `get_current_user` as they do everywhere (FR-029).

---

## Profiles — `/api/v1/user-profiles` (new)

Route prefix follows the existing kebab-case convention (`/price-lists`, `/taxpayer-issuers`).
Registered in `app/api/v1/router.py` with `tags=['user-profiles']`.

| Method | Path | Purpose | Success | Failures |
|---|---|---|---|---|
| GET | `` | List, `search` + `status` + `skip`/`limit` | `200` `UserProfileListResponse` | `401`, `403` |
| POST | `` | Create | `201` `UserProfileResponse` | `401`, `403`, `409` name taken, `422` |
| GET | `/{id}` | Retrieve with its entries | `200` `UserProfileResponse` | `401`, `403`, `404` |
| PUT | `/{id}` | Update name, description, status, entries | `200` `UserProfileResponse` | `401`, `403`, `404`, `409`, `422` |
| DELETE | `/{id}` | Delete | `204` | `401`, `403`, `404`, `409` referenced |
| POST | `/{id}/apply/{user_id}` | Apply to a user | `200` `UserResponse` | `401`, `403`, `404`, `409` inactive |

### Why the apply lives here

`POST /user-profiles/{id}/apply/{user_id}` rather than `POST /users/{user_id}/apply-profile`. The
action's subject is the profile — it is the thing being copied, the thing that can refuse the request
by being inactive, and the thing an administrator has open on screen when they invoke it. The `404`
for a missing profile therefore comes from the same path resolution as every other
`/user-profiles/{id}` route, and only the user id needs its own check.

Returns `200` with the **full updated `UserResponse`**, matching `PUT /users/{id}`. A caller that has
just replaced 107 permission rows wants to see them; a `204` would force a second request to render
the result of the action just taken.

### Sparse entries, both directions (FR-003)

`privileges` on both request and response carries **only** the objects the profile grants. Absence
means denied. A request entry with `privileges: 0` is accepted and dropped, so a round-trip is
stable — what you read back is what a subsequent write would produce.

```json
// POST /api/v1/user-profiles
{
  "name": "Cashier",
  "description": "Till operator — sell and take payment, no catalog edits",
  "status": 0,
  "privileges": [
    { "system_object": 0,  "privileges": 2 },
    { "system_object": 7,  "privileges": 3 },
    { "system_object": 44, "privileges": 3 }
  ]
}
```

```json
// 201 — three entries in, three entries out. The other 104 objects are simply absent.
{
  "user_profile_id": 1,
  "name": "Cashier",
  "description": "Till operator — sell and take payment, no catalog edits",
  "status": 0,
  "privileges": [
    { "system_object": 0,  "privileges": 2, "allow_create": false, "allow_read": true,  "allow_update": false, "allow_delete": false },
    { "system_object": 7,  "privileges": 3, "allow_create": true,  "allow_read": true,  "allow_update": false, "allow_delete": false },
    { "system_object": 44, "privileges": 3, "allow_create": true,  "allow_read": true,  "allow_update": false, "allow_delete": false }
  ]
}
```

The four booleans are computed properties, exactly as `PrivilegeResponse` already exposes them for a
user. `ProfilePrivilegeResponse` is a separate schema from `PrivilegeResponse` only because it reads
off a different model; the field set is identical by design, so a client can render either with one
component.

### `PUT` replaces the entry set

Sending `privileges` replaces the profile's whole entry set — objects absent from the payload are
removed from the profile. Omitting the `privileges` key entirely leaves the entries untouched, so
renaming a profile does not require resending its masks.

This is the *profile's* edit semantics and is unrelated to FR-026's asymmetry, which is about the two
ways of writing a **user's** permissions.

### `409` bodies

```json
{ "detail": "Profile name already exists" }
```

```json
{ "detail": "Cannot delete: referenced by 3 rows in user" }
```

The delete conflict is produced by `assert_not_referenced` with no new code (research R5), so its
wording is whatever that shared guard already emits for every other referenced delete — not a message
this feature composes.

```json
{ "detail": "Profile is not active" }
```

Returned by the apply when the profile's status is anything but `ACTIVE` (FR-017). `409` rather than
`422`: the request is well-formed and the profile exists — it is the resource's state that refuses,
which is what `409` means elsewhere in this API.

---

## Users — `/api/v1/users` (changed, additively)

| Method | Path | Change |
|---|---|---|
| POST | `` | **`UserCreate` gains optional `profile_id`.** When present, the account is created with that profile's permissions and its origin recorded, in one transaction (FR-011). When absent, behaviour is byte-for-byte what it is today |
| GET | `` | **`UserListItem` gains `profile_id` and `profile_name`**, both nullable. **New `profile_id` query filter** (FR-020, FR-021) |
| GET | `/{id}` | **`UserResponse` gains `profile_id` and `profile_name`**, both nullable (FR-020) |
| PUT | `/{id}` | **Unchanged.** Its `privileges` field stays a partial upsert (FR-026). It does **not** accept `profile_id` — changing an account's profile is an apply, not a field edit |
| DELETE | `/{id}` | Unchanged |
| POST | `/{id}/recover-password` | Unchanged |

### `POST /users` with a profile

```json
{
  "user_id": "mlopez",
  "password": "...",
  "email": "mlopez@example.com",
  "employee_id": 42,
  "profile_id": 1
}
```

`404 Profile not found` if the id names nothing; `409 Profile is not active` if it is not active.
**In both cases no user row is written** — the profile is resolved and validated before anything is
staged, and the whole create is one commit (FR-011, research R8). This ordering is the direct lesson
of #154, where `_attach_links` ran after `commit()` and a 500 meant "already created".

### Why `PUT /users/{id}` does not accept `profile_id`

Setting the origin without copying the permissions would record a provenance claim that was never
true — precisely the drift the spec declines to detect (FR-022, spec Assumptions). The origin is
writable only by the operation that earns it.

### `profile_name` is denormalized into the response

Both `UserResponse` and `UserListItem` carry `profile_name` beside `profile_id` so a list renders
without a second request per row (FR-020). It is resolved with **one** query for the whole page — a
two-column projection in `user_service.profile_names_for`, not `fk_expansion.batch_fetch`. The helper
was the plan's choice and measured worse: it loads entities, so `UserProfile.privileges`
(`lazy='selectin'`) fired a second query loading every mask of every profile on the page to render a
name. Never one query per row either way; the integration test asserts the statement count, because
both versions return identical JSON.

---

## Response schemas added to `app/schemas/user.py`

| Schema | Shape |
|---|---|
| `ProfilePrivilegeResponse` | `system_object`, `privileges`, `allow_create`, `allow_read`, `allow_update`, `allow_delete` |
| `ProfilePrivilegeUpdate` | `system_object`, `privileges` (`ge=0, le=15`) |
| `UserProfileCreate` | `name`, `description?`, `status?`, `privileges?` |
| `UserProfileUpdate` | all fields optional; `privileges` present ⇒ replace the set |
| `UserProfileResponse` | `user_profile_id`, `name`, `description`, `status`, `privileges[]` |
| `UserProfileListItem` | `user_profile_id`, `name`, `description`, `status` — no entries |
| `UserProfileListResponse` | `items[]`, `total` |

`UserProfileListItem` omits `privileges` deliberately: a catalog page showing twenty profiles does
not need sixty-plus masks it will not render, and `lazy='selectin'` would fetch them all. The detail
endpoint is where entries are read.

Modified: `UserCreate` (+`profile_id`), `UserResponse` (+`profile_id`, +`profile_name`),
`UserListItem` (+`profile_id`, +`profile_name`). `UserUpdate`, `PrivilegeResponse`,
`PrivilegeUpdate`, `UserSettings*` and `ChangePasswordRequest` are untouched.

---

## What no client has to change

An existing caller that never sends `profile_id` and ignores unknown response fields sees no
difference: `POST /users` behaves as before, `PUT /users/{id}` behaves as before, and the two new
user fields are `null` on every account that predates the feature (FR-024, FR-027).
