# Contract: Datetime Fields

Applies to every `format: date-time` value in `/api/v1`, in request bodies, responses and query parameters.

## Wire format

- **Responses**: `YYYY-MM-DDTHH:MM:SS[.ffffff]`, no offset, no `Z`. Business-timezone wall-clock time. Unchanged by this feature.
- **Requests**: any ISO 8601 datetime.
  - Without an offset: taken as business-timezone time.
  - With an offset (`Z`, `+00:00`, `-05:00`, …): converted to business-timezone time.

## Examples (business timezone `America/Mexico_City`, UTC-06:00)

| Sent | Stored and returned |
|------|---------------------|
| `2026-09-27T12:00:00` | `2026-09-27T12:00:00` |
| `2026-09-27T18:00:00Z` | `2026-09-27T12:00:00` |
| `2026-09-27T12:00:00-05:00` | `2026-09-27T11:00:00` |
| `2026-09-27T00:00:00.000Z` | `2026-09-26T18:00:00` (see note) |

**Note for mbe-ui**: the last row is today's date-picker workaround (a midnight marked UTC). Once this ships it stores the previous evening. Send the naive local midnight instead (`2026-09-27T00:00:00`). Tracked in mictlanix/mbe-ui#176.

## Schema text

Every date-time node in the OpenAPI document carries this description, with the zone filled in from the setting:

> Local wall-clock time in America/Mexico_City, with no UTC offset. A value sent with an offset is converted to America/Mexico_City; a value without one is taken as already local.

`format: date-time` is kept, so generated clients still type these fields as datetimes. A client must treat the parsed value as local wall-clock time and must not convert it to or from UTC.

## Exception: token expiry

`expires_at` on the admin password-recovery response (`POST /api/v1/users/{user_id}/recover-password`) is a plain string holding an absolute UTC instant **with** an offset (`…+00:00`). It is a token lifetime, not a business timestamp, and is never stored (FR-008). It is not declared `format: date-time`, so the convention above does not describe it.
