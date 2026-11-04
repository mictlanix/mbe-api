# Contract changes: Localizable Error Codes

Every route is under `/api/v1`. No route is added or removed and no request shape changes. Every status code and every `detail` text stays as it is (spec FR-005). What changes:
- **Error bodies:** every error the app raises is a Problem Details body (`application/problem+json`) with a `code`, plus `params` where the code defines them. The codes, statuses and params are in [error-codes.md](error-codes.md).
- **OpenAPI:** `/openapi.json` declares `Problem` as every v1 route's `default` response, and exports the `ErrorCode` enum.
- **Validation:** request-validation 422s keep FastAPI's shape and `application/json`. Custom validators report a specific `type`.
- **Two response models gain fields:** `BulkApproveResult` (`code`, `params`) and the inventory count batch's 422.

## Error body

```
Problem:
  status: int
  code: ErrorCode
  detail: str
  params: dict | null
  errors: [ProblemPart, ...] | null

ProblemPart:
  code: ErrorCode
  params: dict | null
```

- `detail` is a string, except for four codes that keep today's dict (see below).
- `params` and `errors` are omitted when empty.

A missing customer:

```json
{
  "status": 404,
  "code": "resource.not_found",
  "detail": "Customer not found",
  "params": {"resource": "customer", "id": 999999}
}
```

A credit hold, on `PUT /sales-orders/{id}` with credit terms. The status is still 422, and the text is unchanged:

```json
{
  "status": 422,
  "code": "customer.credit_hold",
  "detail": "Customer is on credit hold: 4 overdue credit order(s) must be settled before a new order can be raised",
  "params": {"customer_id": 812, "overdue_count": 4, "action": "raise"}
}
```

A delete still referenced. Public names, never table names:

```json
{
  "status": 409,
  "code": "resource.still_referenced",
  "detail": "Still referenced by sales_order (12), inventory_transfer (3) — remove those records first",
  "params": {"references": [{"resource": "sales_order", "count": 12}, {"resource": "inventory_transfer", "count": 3}], "more": 0}
}
```

## Unchanged shapes that mbe-ui reads

| Response | Kept exactly | Added |
|---|---|---|
| 403 on any protected route while a password change is pending | `detail: "password_change_required"` | `status`, `code: auth.password_change_required` |
| 422 from sales order create, update, confirm and quote convert | string `detail` | `status`, `code`, `params` |
| 409 `POST /sales-orders/{id}/confirm`, zero-priced lines / insufficient stock / missing delivery details | `detail: {"message", "lines"}` or `{"message", "missing"}` | `status`, `code` |
| 422 `POST /delivery-itineraries/{id}/depart`, lines over-committed | `detail: {"message", "lines"}` | `status`, `code` |

Phase 3, a later spec, moves the dict details to a string `detail` plus `errors[]`. That change is breaking.

## `PUT /inventory-counts/{id}/lines` — 422 batch refusal

Today the route hand-builds `{detail, errors: [{index, product | product_code, reason}]}`. Now:

```json
{
  "status": 422,
  "code": "inventory_count.batch_invalid",
  "detail": "2 invalid entries; nothing was applied",
  "errors": [
    {"index": 0, "product_code": "X-1", "reason": "unknown product", "code": "inventory_count_line.product_unknown"},
    {"index": 3, "product": 77, "reason": "not stockable", "code": "inventory_count_line.not_stockable"}
  ]
}
```

Every existing key stays; each item gains `code`.

## `POST /purchase-requests/approve`

**200** `BulkApproveResponse`. Each result gains `code` and `params`:

```
BulkApproveResult:
  purchase_request_id: int
  approved: bool
  reason: str | null
  code: ErrorCode | null
  params: dict | null
```

`code` and `params` are null when the request was approved. `reason` keeps its English text.

## Request validation (422, unchanged shape)

FastAPI's list stays: `[{type, loc, msg, input, ctx?}]`, served as `application/json`. Custom validators now report their own `type` instead of `value_error`, with raw values in `ctx` (types in [error-codes.md § Validation types](error-codes.md#validation-types)). For example, a supplier payment dated tomorrow:

```json
{"detail": [{"type": "supplier_payment_date_in_future", "loc": ["body", "date"], "msg": "The payment date cannot be in the future", "input": "2026-10-29T10:00:00", "ctx": {"date": "2026-10-29T10:00:00"}}]}
```

`msg` loses Pydantic's "Value error, " prefix (research R6).

## Framework errors

| Situation | Status | Code |
|---|---|---|
| No or invalid bearer token | 401 | `auth.not_authenticated` (`WWW-Authenticate: Bearer` kept) |
| Unknown route | 404 | `request.route_not_found` |
| Method not allowed | 405 | `request.method_not_allowed` |
| Unparseable body | 400 | `request.malformed` |
| Unexpected failure | 500 | `server.error`, `detail: "Internal server error"` |
| A constraint the services didn't check | 409 | `resource.conflict`, `detail: "The request conflicts with existing data"` |
