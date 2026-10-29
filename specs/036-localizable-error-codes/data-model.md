# Data Model: Localizable Error Codes

No database change. The entities below are the response contract and the in-code registries behind it.

## Problem (response body)

| Field | Type | Required | Rule |
|---|---|---|---|
| `status` | int | yes | Equals the HTTP status, which equals the code's status. The one exception is a framework error with a status outside the map (R3), where it equals the HTTP status. |
| `code` | `ErrorCode` | yes | From the catalog |
| `detail` | string, or a dict for 4 codes | yes | Today's English text, unchanged (FR-005). The dict form appears only for `sales_order.missing_delivery_details`, `sales_order.zero_priced_lines`, `sales_order.insufficient_stock` and `itinerary.lines_overcommitted`. |
| `params` | object | no | Omitted when empty. Raw values only (FR-003); keys per code in `contracts/error-codes.md` |
| `errors` | list of `ProblemPart` | no | Only `inventory_count.batch_invalid` fills it in this spec (R11) |

Served as `application/problem+json`, with response headers kept (`WWW-Authenticate` on 401).

## ProblemPart (an item of `errors`)

| Field | Type | Rule |
|---|---|---|
| `code` | `ErrorCode` | The item's own situation (`inventory_count_line.*`) |
| `params` | object, optional | Raw values |
| other keys | as today | The inventory count items keep `index`, `product` or `product_code`, and `reason` |

## ErrorCode (the catalog)

- **Shape:** a StrEnum member whose value is `<domain>.<condition>` (lower snake case) and which carries `.status`, its one HTTP status.
- **Lifecycle:** active, or deprecated (kept, never removed, never reused). No member is deprecated at launch.
- **Size at launch:** 240 members (research R10).
- **Invariants:** they're tested.
  - The value matches the pattern.
  - The status is 400–599.
  - Every member is in `contracts/error-codes.md` with the same status, and vice versa.
  - Every member is asserted by at least one test.

## DomainError (the exception)

| Attribute | Type | From |
|---|---|---|
| `status_code` | int | `code.status` |
| `code` | `ErrorCode` | Constructor |
| `detail` | str, or dict (4 codes) | Constructor; today's text |
| `params` | dict | Constructor, normalized: Decimal → str with scale, date/datetime → ISO, Enum → value, `None` dropped |
| `errors` | list[dict] or None | Constructor |
| `headers` | dict or None | Constructor (`WWW-Authenticate`) |

It is a subclass of FastAPI's `HTTPException`, so `except HTTPException` and `exc.detail` keep working (`order_expiry`).

## Public resource name

- **`RESOURCE_NAMES`:** table name → public name, covering all 94 mapped tables (research R7 lists the renames).
- **`EXTRA_RESOURCES`:** names for things that aren't tables: `proof_of_delivery_image`, `license_photo`, `receivable`, `product_image`.
- **Where they're used:** `resource.not_found.resource`, `resource.already_exists.resource`, `resource.still_referenced.references[].resource`, `facility.other_facility.resource`, and the `detail` sentence of `still_referenced`.
- **Invariant:** no error body contains a table name that has a different public name (SC-006).

## Validation type (not an ErrorCode)

The `type` of an entry in FastAPI's 422 validation list, raised as `PydanticCustomError(type, message, ctx)`.
- 21 types replace plain `value_error`; `password_policy` exists already.
- Each type is `<domain>_<condition>`.
- `ctx` holds raw values.
- `msg` is today's sentence, without the "Value error, " prefix.

The types are listed in `contracts/error-codes.md` § Validation types.

## BulkApproveResult (changed response model)

| Field | Type | Change |
|---|---|---|
| `purchase_request_id` | int | — |
| `approved` | bool | — |
| `reason` | str or None | — (English, kept) |
| `code` | `ErrorCode` or None | **Added**: the refusal's code; null when approved |
| `params` | object or None | **Added**: the refusal's params |
