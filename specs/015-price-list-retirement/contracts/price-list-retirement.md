# Contract: Price list retirement and its preview

Two endpoints on `/api/v1/price-lists`. The delete existed before this feature; its path, method
and success status are unchanged, and it gains one optional query parameter. The preview is new.
Both require an authenticated caller and no further privilege, like every endpoint in this router.

## `DELETE /api/v1/price-lists/{price_list_id}`

```http
DELETE /api/v1/price-lists/7
DELETE /api/v1/price-lists/7?replacement=3
```

`204 No Content`.

| Parameter | In | Type | Required | Meaning |
|-----------|----|------|----------|---------|
| `price_list_id` | path | int | yes | The list being retired |
| `replacement` | query | int | no | The list its customers are moved to |

| Status | When |
|--------|------|
| `204` | Retired. The list no longer exists |
| `400` | `replacement == price_list_id` — `"Cannot replace a price list with itself"` |
| `401` | Unauthenticated |
| `404` | `"Price list not found"`, or `"Replacement price list not found"`, naming which |
| `409` | Something other than its prices still references the list — `"Still referenced by customer.price_list (12) — remove those records first"` |

### What a `204` guarantees

- The list is gone.
- Every `product_price` row for the list is gone. No other list's prices for those products are
  affected — a product priced in five lists keeps four.
- If `replacement` was named, every customer that was on the retired list is now on the
  replacement, and no other customer's assignment changed.
- Nothing anywhere still references the retired list.

### What a `409` means

Exactly what it means today, with one relation removed from the reasons: the list's own prices no
longer block. Anything else does, named with its count, largest first. Reaching a `409` for
`customer.price_list` is the signal to retry with `replacement`.

A relation added to the data model after this feature ships will also appear here, because the
blockers are counted off the mapped foreign keys rather than a list in the code. That is deliberate:
an unfamiliar relation should refuse the retirement rather than be deleted on a guess.

### All-or-nothing

Every refusal above leaves the data exactly as it was — including the `400` and the `404` for the
replacement, which are raised after nothing has been written, and the `409`, which is raised after
the customer move but before any commit. There is no state in which customers have been moved off a
list that still exists, or in which a list has lost its prices but survives.

### Irreversible

There is no un-retire. The prices are deleted outright and the customers' previous assignment is not
recorded anywhere. Clients should route this through a review step and show the preview below.

### `replacement` when nothing is assigned

Accepted, and moves nobody. A client that always sends a replacement is not punished for it. The
value is still validated, so a typo is a `404` whether or not any customer would have moved.

## `GET /api/v1/price-lists/{price_list_id}/delete/preview`

```http
GET /api/v1/price-lists/7/delete/preview
```

```json
{
  "items": [
    { "category": "product_price.list", "count": 4312 },
    { "category": "customer.price_list", "count": 12 }
  ],
  "total": 4324
}
```

| Status | When |
|--------|------|
| `200` | The breakdown, largest count first. Empty `items` and `total: 0` for a list nothing references |
| `401` | Unauthenticated |
| `404` | `"Price list not found"` — the same refusal the delete gives, so a preview that answers describes a list that can be acted on |

### Reading `items`

`category` is `table.column`, the same label the `409` uses, so a client can match a preview line to
the blocker it will hit. Two categories exist today and each is acted on differently:

| Category | What the retirement does with it |
|----------|----------------------------------|
| `product_price.list` | Deleted |
| `customer.price_list` | Moved to `replacement`, or the retirement is refused |

`total` is the sum of every count — records the retirement **touches**, not records it deletes. A
client labelling it "will be deleted" overstates it, the same way the merge preview's total covers
both what a merge moves and what it discards.

A category not in the table above is one added since this feature shipped; it blocks the retirement.

### Read-only

Asking changes nothing. The endpoint issues counts and no writes.
