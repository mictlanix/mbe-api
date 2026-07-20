# Contract: Unified `status` field and filter

## Field contract (all status-bearing, API-exposed entities)

Entities: users, customers, products, employees, facilities, warehouses, points of sale,
cash drawers, payment method options, vehicles, vehicle operators.

### Responses (detail, list items, summaries)

```json
{ "...": "...", "status": 0 }
```

- `status` is always present, integer, one of: `0` = ACTIVE, `1` = INACTIVE, `2` = ARCHIVED.
- The legacy fields `active`, `disabled`, `deactivated`, `enabled` no longer appear anywhere.

### Create requests

- `status` optional; omitted → `0` (ACTIVE).
- Exception: `POST /api/v1/products` does not accept `status` (unchanged contract shape —
  it never accepted `deactivated` either); new products are ACTIVE.
- Invalid values (anything but 0/1/2) → `422`.

### Update requests (PUT, partial-update semantics)

- `status` optional; omitted/null → unchanged; `0|1|2` → set.
- Invalid values → `422`.

## List filter contract

All list endpoints for the entities above:

```text
GET /api/v1/<entities>?status=<0|1|2>
```

- Omitted → all statuses returned.
- Provided → only exact matches returned; paginated `total` reflects the filter.
- Invalid values → `422`.

### Removed parameters (were the only pre-existing lifecycle filters)

| Endpoint | Removed param | Replacement |
|---|---|---|
| `GET /api/v1/products` (both list variants) | `?deactivated=<bool>` | `?status=` |
| `GET /api/v1/customers` | `?disabled=<bool>` | `?status=` |
| `GET /api/v1/employees` | `?active=<bool>` | `?status=` |

Sending a removed parameter has no effect (FastAPI ignores unknown query params).

## Behavioral contract

- `POST /api/v1/auth/login` (and any credential authentication): rejected unless the user's
  `status` is `0` (ACTIVE). INACTIVE and ARCHIVED users receive the same error the legacy
  `disabled` user received.
- DELETE endpoints: unchanged hard-delete semantics. No endpoint sets ARCHIVED automatically.

## OpenAPI

- `status` renders as an integer enum (`0|1|2`) with a description naming the states in every
  affected schema and query parameter.
