# Contract: Conflict responses

Applies to every endpoint. No request shape changes and no endpoint is added — the OpenAPI
document is byte-identical before and after this feature. What changes is which status code a
client receives when the data store rejects the request.

## Status code contract

| Condition | Before | After |
|-----------|--------|-------|
| Delete a record something still references | `500` | `409` |
| Create/update duplicating `warehouse.code`, `point_sale.code`, `cash_drawer.code`, `vehicle.license_plate` | `500` | `409` |
| Any other constraint rejection | `500` | `409` |
| Delete a record nothing references | `204` | `204` (unchanged) |
| Re-save a record without changing its code | `200` | `200` (unchanged) |

`409` is the only code this feature introduces. Nothing that previously succeeded now fails.

## Referenced-record delete

```http
DELETE /api/v1/warehouses/1
```

```json
{
  "detail": "Still referenced by point_sale.warehouse (3), inventory_receipt.warehouse (2) — remove those records first"
}
```

- Status: `409`.
- Blockers are `table.column`, ordered by count descending.
- At most five are listed; a longer list appends `, and N more`.
- The record is unchanged — a refused delete has no side effect.
- A table referencing the target through two columns yields two entries, distinguished by
  column (e.g. `inventory_transfer.warehouse_to` and `inventory_transfer.warehouse`).

The `table.column` labels are deliberate and intended for client consumption. They are the
one place internal names are disclosed, because they are the payload that makes the error
actionable.

## Duplicate code

```http
POST /api/v1/warehouses    { "facility": 1, "code": "WH1", "name": "Duplicate" }
```

```json
{ "detail": "Warehouse code already exists" }
```

- Status: `409`.
- Message names the field in operator language: `Warehouse code`, `Point of sale code`,
  `Cash drawer code`, `License plate`.
- On `PUT`, the record being edited is excluded from the search: saving a record with its own
  existing code succeeds.

## Generic conflict (backstop)

```json
{ "detail": "The request conflicts with existing data" }
```

- Status: `409`.
- Returned for any constraint no service checks up front, including constraints on tables
  this feature does not model.
- Carries **no** table, column, index or constraint names, and no driver text or error
  number. The underlying message is written to the server log instead.

## Client guidance

- `409` is never retryable as-is. The request must change, or the referenced records must be
  removed first.
- A `409` from a delete means nothing was deleted. There is no partial-delete state.
- Refusal is not an invitation to archive. Setting `status` to `ARCHIVED` remains a separate,
  explicit action the operator chooses; the API never does it on their behalf.
