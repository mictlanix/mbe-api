# API Contract: Sales Order Origin

**Feature**: [../spec.md](../spec.md) | **Data model**: [../data-model.md](../data-model.md)

All routes are under `/api/v1/` and require authentication, unchanged. No endpoint is added, removed
or renamed; four existing ones gain a field or a parameter.

## `POST /sales-orders`

Request body gains one optional field.

| Field | Type | Required | Notes |
|---|---|---|---|
| `origin` | integer enum: `0` point of sale, `1` back office | no | Which workflow raised the order. Omitted means **not recorded** — the API infers nothing from the register, the customer, or the fulfilment intent. |

- `origin: 1` → the created order reports `origin: 1`.
- omitted → the created order reports `origin: null`.
- `origin: 2` (or any non-member) → **422**, from schema validation, before anything is written.
- `origin: null` explicitly → the same as omitting it.

## `PUT /sales-orders/{id}`

**Unchanged.** `origin` is not part of the update contract. A request that includes it is accepted
(200) and the field is ignored — the stored origin is not changed, not cleared, and not validated.
This is the existing behaviour for any unknown field on this endpoint, not a new rule.

## `POST /sales-quotes/{id}/convert`

**Request unchanged** — this endpoint takes no body. The resulting order reports `origin: 1`
(back office), set by the service. The client supplies nothing and cannot override it.

The converted order also reports the quote it came from in `sales_quote`, as it already does. The
two are independent: `origin` says which workflow the order belongs to, `sales_quote` says what
preceded it.

## `GET /sales-orders/{id}` — `SalesOrderResponse`

Gains one field.

| Field | Type | Notes |
|---|---|---|
| `origin` | integer enum or `null` | `null` means not recorded. |

`sales_quote` is already on this response and is unchanged.

## `GET /sales-orders` — `SalesOrderSummary`

Each row in `items[]` gains two fields.

| Field | Type | Notes |
|---|---|---|
| `origin` | integer enum or `null` | Which workflow raised the order, on the row itself — no follow-up request per row. |
| `sales_quote` | integer or `null` | The quote this order was converted from, or `null`. |

The envelope (`items`, `total`) and every existing field are unchanged, and the endpoint still costs
a fixed three queries per page regardless of page size.

### New query parameters

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `origin` | integer enum | unset | Return only orders that recorded this workflow. Orders recording nothing are **excluded**. |
| `exclude_origin` | integer enum | unset | Return every order that did not record this workflow, **including** orders that recorded nothing. |

Both are optional and independent. Examples:

```
GET /api/v1/sales-orders?origin=1              # the back-office inbox
GET /api/v1/sales-orders?exclude_origin=1&point_sale=3   # register 3's own list, history intact
GET /api/v1/sales-orders?origin=1&exclude_origin=1       # 200 with an empty page
GET /api/v1/sales-orders?origin=7                        # 422
```

Existing parameters (`mine`, `customer`, `salesperson`, `status`, `date_from`, `date_to`,
`facility`, `point_sale`, `search`, `skip`, `limit`) are unchanged in name, type and behaviour,
and combine with the two new ones by conjunction, as they already do with each other.

## Compatibility

- A client that ignores the new fields sees no difference in any response.
- A client that sends no new parameters gets exactly the page it gets today.
- Every order created before this change reports `origin: null` and behaves identically in every
  other respect.
