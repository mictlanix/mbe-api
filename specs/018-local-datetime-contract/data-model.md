# Data Model: Local Datetime Contract

No tables, columns or migrations change. Stored datetimes are already naive Mexico City wall-clock time, written the same way by the legacy system.

## Setting: `business_timezone`

| Attribute | Value |
|-----------|-------|
| Environment variable | `BUSINESS_TIMEZONE` |
| Type | IANA timezone name, validated as `ZoneInfo` |
| Default | none: the API refuses to start without it |
| Value in `.env` and `.env.example` | `America/Mexico_City` |

Validation: the name must exist in the timezone database. A missing or unknown name stops startup with an error naming `business_timezone`.

## Value convention: local datetime

Every datetime the API stores, returns or accepts is **naive wall-clock time in the business timezone**.

| Direction | Rule |
|-----------|------|
| Stored | Naive, business timezone. Unchanged. |
| Returned | Naive ISO 8601, no offset. Unchanged (FR-007). |
| Accepted without offset | Taken as business-timezone time, unchanged (FR-005). |
| Accepted with offset | Converted to business-timezone time, offset dropped (FR-004). |
| Current time | Always read in the business timezone, never the host's (FR-002). |

Exception: JWT expiry stays an aware UTC instant. It is never stored or returned as a field (FR-008).

## Known data inconsistency (not corrected)

Up to 2 `vehicle_operator` rows in `mbe_dev` (modified 2026-03-18) may carry UTC instead of local time. Each is corrected on its next update. See the spec's Assumptions.
