# Contract: the permission matrix narrows from 107 to 103

This feature adds no endpoint, changes no route, and alters no request or response *shape*. It has
exactly one observable effect on an API consumer, recorded here because it is a change in the
**content** of an existing response that no schema change announces — the kind that is noticed late,
by a client that hard-coded a length.

## What changes

Endpoints that project an account's privileges return **103** entries where they returned 107:

- `GET /api/v1/auth/me`
- `GET /api/v1/users/{user_id}`
- `POST /api/v1/users` (the created account's projection)
- `POST /api/v1/user-profiles/{id}/apply/{user_id}`

Four entries disappear from every such list, identified by `system_object`:

| `system_object` | name |
|---|---|
| `58` | `TECHNICAL_SERVICE_REPORTS` |
| `64` | `TECHNICAL_SERVICE_REQUESTS` |
| `65` | `TECHNICAL_SERVICE_RECEIPTS` |
| `90` | `VEHICLE_SERVICE_ORDERS` |

Each was a denial (mask `0`) on every account in `mbe_dev` — the screens behind them were deleted
along with the tables — so no consumer loses a grant it was acting on. What it loses is four
always-denied entries it should never have been reading.

## What does not change

- No surviving `system_object` identifier moves. `88`, `89` and `91` — the live neighbours around
  the retired `90` — keep their numbers and their masks.
- The four retired numbers are never reused for a different meaning.
- `PRODUCTION_SITES` remains `107`. The identifier `107` and the former matrix width `107` were
  never the same fact (research R4); only the width changes.
- Profile requests are unaffected in shape. A profile is sparse and names only what it grants.

## What a consumer must not do

`POST` / `PUT /api/v1/user-profiles` with a `privileges` entry naming `58`, `64`, `65` or `90` is
refused with a validation error, as it already is for any object outside the catalog
(`Unknown system object: <n>`). This is existing behaviour applying to four more numbers, not a new
rule.

Measured before writing this: zero stored profiles grant any of the four, so no existing profile
becomes unwritable.
