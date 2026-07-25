# Contract: Product merge and merge preview

Two endpoints on `/api/v1/products`. The merge existed before this feature and its request and
response shapes are unchanged — what changed is what it does to the data. The preview is new.

## `POST /api/v1/products/merge`

```http
POST /api/v1/products/merge    { "product_id": 1, "duplicate_id": 2 }
```

`204 No Content`. Gated by `PRODUCTS_MERGE` (73) with `AllowCreate`.

| Status | When |
|--------|------|
| `204` | Merged. The duplicate no longer exists |
| `400` | `product_id == duplicate_id` — `"Cannot merge a product with itself"` |
| `403` | Caller lacks `PRODUCTS_MERGE` / `AllowCreate` |
| `404` | `"Canonical product not found"` or `"Duplicate product not found"`, naming the side |
| `409` | A reference could not be carried over, so the deletion failed. Generic — see below |

### What a `204` guarantees

- Every record that referred to the duplicate now refers to the canonical, **except** its
  prices, labels, commission assignment and per-customer discounts, which are deleted.
- Nothing anywhere still refers to the duplicate.
- The canonical's own configuration is untouched: its price, label, commission and discount
  counts are the same as before the merge.
- Stamped fiscal documents state exactly what they stated before. `fiscal_document_detail` rows
  are re-pointed at the canonical, but each keeps its own `product_code` / `product_name`
  snapshot of what was invoiced.

### What a `409` means

The merge is all-or-nothing: a conflict means **nothing was changed**. It arrives through the
generic `IntegrityError` backstop (feature 006) and so names nothing, because the only remaining
way to reach it is a reference from a table outside the modelled data set. Every modelled
reference is carried over by construction.

### Irreversible

There is no unmerge. The duplicate is deleted and its configuration rows are gone. Clients
should route this through a review step and show the preview below.

### Data a merge does not carry over

A label or per-customer discount that exists only on the duplicate is **not** moved. If it
should survive, set it on the canonical before merging. This is the price of an outcome that can
be stated without reference to which rows the two products had in common.

## `GET /api/v1/products/merge/preview`

```http
GET /api/v1/products/merge/preview?product_id=1&duplicate_id=2
```

```json
{
  "items": [
    { "category": "sales_order_detail.product", "count": 9 },
    { "category": "product_price.product", "count": 3 }
  ],
  "total": 12
}
```

Gated by `PRODUCTS_MERGE` (73) with `AllowRead` — the merge's own object at read level, so a
reviewer can see the blast radius without holding the right to perform the merge.

| Status | When |
|--------|------|
| `200` | Counted. `items` may be empty, in which case `total` is `0` |
| `400` | `product_id == duplicate_id` — the same message the merge returns |
| `401` | Unauthenticated |
| `403` | Caller lacks `PRODUCTS_MERGE` / `AllowRead` |
| `404` | Either product missing, named by side, exactly as the merge names it |
| `422` | `product_id` or `duplicate_id` missing — both are required query parameters |

### Semantics

- `category` is a `table.column` label — the same vocabulary a delete conflict uses, so one
  glossary covers both.
- A table referring to products through two columns produces one entry per column.
- Ordered by `count` descending, then by name. Relations with no rows are omitted, never
  reported as `0`.
- `total` is the sum of `count`.
- Read-only. Requesting a preview changes nothing.
- A preview that returns `200` is a preview of a merge that would be accepted: the pair is
  validated by the same code path, so the same `400` and `404` apply.

### Reading `total` correctly

`total` is what the merge **touches**, not what it reassigns. Four of the nineteen relations —
`product_price`, `product_label`, `commission_product`, `customer_discount` — are deleted rather
than moved, and they are counted. A client labelling the total "records that will be reassigned"
overstates it; "records affected" is accurate. The four are identifiable by `category`, so a
client that wants to present the split can.

### Coverage

Both the preview and the merge enumerate the referencing relations through one shared helper, so
their coverage cannot drift: the categories reported are exactly the relations acted on. A new
foreign key to `product` appears in the preview and is handled by the merge as soon as its model
exists — neither endpoint needs changing.

## Route ordering

`GET /merge/preview` is declared before `GET /{product_id}` in
[products.py](../../../app/api/v1/endpoints/products.py), so it is not swallowed by the by-id
route. A request missing the query parameters returns `422`, not a `404` from the by-id handler
— that distinction is pinned by a test.
