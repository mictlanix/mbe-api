# Phase 1 Contracts: Delivery & Logistics Endpoints

Every route sits under `/api/v1/`, requires an authenticated session, and is gated by
`require_privilege(<SystemObject>, <AccessRight>)` (FR-066). Two routers, four privilege surfaces.

Conventions inherited unchanged from the existing API:

- List responses are `ListResponse[T]` — `{items: [...], total: n}` — with `skip` / `limit`
  (`limit` ≤ 100).
- Foreign keys expand onto a `<column>_detail` key via `app/services/fk_expansion.py`. Never
  overwrite the mapped FK column.
- Lists apply no implicit scoping; narrowing is by explicit parameter.
- Documents are never deleted; there is no `DELETE` on any document root. Cancellation is a
  transition with a reason.

Shared error semantics:

| Status | Meaning here |
|---|---|
| 401 | No or invalid session |
| 403 | Authenticated but lacking the governing privilege |
| 404 | Delivery order, itinerary, stop, line or POD image not found |
| 409 | Lifecycle conflict — a transition outside the state machine, editing outside `DRAFT`, a second open itinerary for a vehicle, departing an empty itinerary, cancelling in `IN_TRANSIT` |
| 422 | Validation — commitment above open quantity, delivered above sent, missing reason, missing proof, scheduled date inside the lead time |

Three error shapes carry detail rather than a bare message:

- An invalid transition names the attempted `from` and `to` status (FR-002).
- A commitment above open quantity states the available open quantity (US3 scenario 6).
- A departure blocked by over-commitment names the offending lines (FR-057, US4 scenario 6).

---

## `/delivery-orders` — SystemObject `DELIVERY_ORDERS` (71)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/delivery-orders` | READ | Filters: `status`, `customer`, `facility`, `fulfillment_type`, `sales_order`, `date_from`, `date_to`, `mine`, `search` (folio, customer name, sales order). `mine` is how a rejected draft is found — no notification is sent (FR-067). `sales_order` answers "which deliveries belong to this sale?" in one call, matching through the lines and including cancelled orders (FR-067a, #147). The filter stays singular — you ask about one sale — while the field it matches on, `sales_orders`, is a list; a consolidated shipment is returned under each of its sales |
| POST | `/delivery-orders` | CREATE | Body: `{sales_order: int}` or `{sales_order_folio, facility}`, plus optional `fulfillment_type` (FR-005a), optional `lines: [{sales_order_detail, quantity}]` to claim a named subset instead of everything uncovered (FR-005b, #138), and the destination's own optional `ship_to`, `contact`, `date` and `comment`, each falling back to the sale's value (FR-005c, #146). A stated `ship_to` is what the fulfilment-type detection reads. `lines` has three cases, and the empty one is the opposite of the omitted one: **omitted** claims every quantity the sale still owes, **a named subset** claims those lines, and **an explicit `[]`** creates the destination carrying nothing, to be filled with `POST /{id}/lines` afterwards (#165). Creates in `DRAFT`. 409 when the sales order is not completed / is cancelled / is pickup mode / is already fully delivered — including for an empty create, which needs the sale to still owe something for the destination to be fillable; 422 when the paid-or-credit rule is on and unmet (FR-008 – FR-014), and when a requested line over-claims its uncovered quantity, is named twice, or belongs to another sale |
| GET | `/delivery-orders/{id}` | READ | Includes lines with their four quantities, dispatch warehouse and derived `open_quantity`. Both the response and the summary carry **`sales_orders: int[]`**, derived from the lines and ordered by id — every sale the shipment draws on, `[]` if no line links to one. A list because the relation is many-to-many: this was a scalar filled by `min()`, which named one sale of a consolidated shipment and dropped the rest with nothing in the response to say so (FR-067a, #147) |
| PUT | `/delivery-orders/{id}` | UPDATE | Header edits. 409 outside `DRAFT` (FR-006) |
| POST | `/delivery-orders/{id}/lines` | UPDATE | Body: `{sales_order_detail, quantity}`. Adds one of the sale's lines to an existing draft, so a line dropped with `DELETE` can be restored and a line left out at creation can be added afterwards (#163). The line may come from **any** of the customer's sales orders, not only the one the delivery was raised from: a delivery order and a sales order are many-to-many, and one shipment consolidating several sales is an operation the business does. Only in `DRAFT`. 409 when that sales order line already has a row here — naming it, so the caller can `PUT` instead; 422 when the line belongs to another customer, and above its own sales order's remaining deliverable quantity |
| PUT | `/delivery-orders/{id}/lines/{line_id}` | UPDATE | Quantity edit. 422 above the sales order's remaining deliverable quantity (FR-016) |
| DELETE | `/delivery-orders/{id}/lines/{line_id}` | UPDATE | Only in `DRAFT` |
| POST | `/delivery-orders/{id}/confirm` | UPDATE | Assigns folio; → `PENDING_APPROVAL` when approval is required, else branches by fulfilment type to `IN_PREPARATION` (delivery) or `APPROVED` (pickup). 409 with no lines; 422 inside the lead time unless administrator (FR-017 – FR-020) |
| POST | `/delivery-orders/{id}/cancel` | UPDATE | Body: `{reason}` — required, non-blank. Releases commitments. 409 from a terminal status or `IN_TRANSIT` (FR-007) |
| GET | `/delivery-orders/{id}/events` | READ | Ordered transition history (FR-064) |

### Counter pickup

| Method | Path | Right | Notes |
|---|---|---|---|
| POST | `/delivery-orders/{id}/ready-for-pickup` | UPDATE | `APPROVED` → `READY_FOR_PICKUP`. 409 for a `DELIVERY`-type order (FR-024, US6 scenario 6) |
| POST | `/delivery-orders/{id}/pickup` | UPDATE | Multipart: `receiver_name`, `receiver_id_shown`, `image`. → `PICKED_UP`, consumes stock from the store warehouse. 422 on any missing proof element (FR-054, FR-060) |

### Failure recovery

| Method | Path | Right | Notes |
|---|---|---|---|
| POST | `/delivery-orders/{id}/requeue` | UPDATE | `FAILED` → `IN_PREPARATION` (FR-051) |

### Proof of delivery

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/delivery-orders/{id}/proof` | READ | Structured proof — receiver, identification, timestamp, capturing employee |
| GET | `/delivery-orders/{id}/proof/image` | READ | Streams the signature or photo. **Authenticated** — never a static URL (FR-044a). 404 before settlement |

---

## `/delivery-orders/approval` — SystemObject `DELIVERY_ORDER_APPROVAL` (94)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/delivery-orders/approval` | READ | Exactly the orders in `PENDING_APPROVAL` (FR-021). Empty when approval is not configured |
| POST | `/delivery-orders/approval/{id}/approve` | UPDATE | **One** transition, branching on fulfilment type: → `IN_PREPARATION` (delivery) or → `APPROVED` (pickup). `APPROVED` is never written transiently for a delivery (FR-022, FR-024) |
| POST | `/delivery-orders/approval/{id}/reject` | UPDATE | Body: `{reason}` — required, non-blank. → `DRAFT` carrying `rejection_reason`. 422 on a blank reason (FR-023) |

> Route ordering matters: `/delivery-orders/approval` must be registered **before**
> `/delivery-orders/{id}`, or FastAPI matches `approval` as an id. The same trap the existing
> `/sales-orders/product-lookup` route avoids.

---

## `/delivery-itineraries/deliveries` — SystemObject `FOR_DELIVER` (91)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/delivery-itineraries/deliveries` | READ | Pending-deliveries view. Lines of `IN_PREPARATION` orders at active facilities, bucketed by scheduled date, sorted by sales-order priority descending, each carrying `open_quantity` (FR-030 – FR-032) |

Response shape — six buckets, always present, possibly empty:

```json
{
  "buckets": [
    {"key": "earlier",   "date": null,         "items": [...], "total": 12},
    {"key": "yesterday", "date": "2026-07-25", "items": [...], "total": 3},
    {"key": "today",     "date": "2026-07-26", "items": [...], "total": 8},
    {"key": "tomorrow",  "date": "2026-07-27", "items": [...], "total": 5},
    {"key": "day_after", "date": "2026-07-28", "items": [...], "total": 2},
    {"key": "later",     "date": null,         "items": [...], "total": 41}
  ]
}
```

`skip` / `limit` apply per bucket. Counter-pickup orders never appear (FR-053).

---

## `/delivery-itineraries` — SystemObject `DELIVERY_ITINERARIES` (87)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/delivery-itineraries` | READ | Filters: `date_from`, `date_to`, `vehicle`, `vehicle_operator`, `warehouse`, `status` (`OPEN` / `DEPARTED` / `CLOSED` / `CANCELLED`, stored per FR-033a). `warehouse` matches the itinerary's dispatch origin (FR-068) |
| POST | `/delivery-itineraries` | CREATE | All fields optional; defaults to today and the warehouse of the caller's point of sale. 409 when the vehicle already has an open itinerary (FR-033, FR-034). Response carries `warnings[]` for an expired operator licence — advisory, never a refusal (FR-035) |
| GET | `/delivery-itineraries/{id}` | READ | Stops in sequence, each with its lines and quantities |
| PUT | `/delivery-itineraries/{id}` | UPDATE | Header edits. 409 after departure (FR-040) |
| POST | `/delivery-itineraries/{id}/cancel` | UPDATE | Releases every commitment. 409 after departure (FR-041) |
| POST | `/delivery-itineraries/{id}/depart` | UPDATE | Fixes `sent_quantity`, moves orders to `IN_TRANSIT`, posts the two-step inventory move. 409 with nothing committed; 422 when a line is over-committed, naming the lines (FR-039, FR-040, FR-057) |

### Stops and commitments

| Method | Path | Right | Notes |
|---|---|---|---|
| POST | `/delivery-itineraries/{id}/stops` | UPDATE | Body: `{delivery_order}`. Assigns the next `sequence`. 409 after departure (FR-036) |
| DELETE | `/delivery-itineraries/{id}/stops/{stop_id}` | UPDATE | Releases the stop's commitments. Open itineraries only |
| POST | `/delivery-itineraries/{id}/stops/{stop_id}/lines` | UPDATE | Body: `{delivery_order_detail, quantity?}`. `quantity` defaults to the line's full open quantity. **Takes the row lock** (R2). 422 above open quantity, stating what is available (FR-037, FR-027, FR-028) |
| POST | `/delivery-itineraries/{id}/stops/{stop_id}/lines/all` | UPDATE | Body: `{delivery_order}`. Commits every open line of the order in one call (FR-038) |
| PUT | `/delivery-itineraries/{id}/stops/{stop_id}/lines/{line_id}` | UPDATE | Adjust committed quantity. 409 after departure (FR-029) |
| DELETE | `/delivery-itineraries/{id}/stops/{stop_id}/lines/{line_id}` | UPDATE | Releases the commitment |

### Closing a stop

| Method | Path | Right | Notes |
|---|---|---|---|
| POST | `/delivery-itineraries/{id}/stops/{stop_id}/close` | UPDATE | Multipart: `receiver_name`, `receiver_id_shown`, `image`, and `lines` as JSON — `[{line_id, delivered_quantity, reason_code?}]` |

Closing is the feature's most consequential call. It:

1. refuses unless every line is accounted for, `delivered ≤ sent`, and every shortfall carries a
   `reason_code` (FR-045, FR-045a, FR-046);
2. stores the proof and links it to the stop **and** to each delivery order settled there
   (FR-043, FR-044);
3. settles each order independently as `DELIVERED`, `PARTIALLY_DELIVERED` or `FAILED`
   (FR-047 – FR-049);
4. creates a child delivery order at `IN_PREPARATION` for any remainder, naming its parent (FR-048);
5. posts the transit-out and warehouse-return movements (FR-058, FR-059);
6. updates sales-order coverage and sets `sales_order.delivered` where now complete
   (FR-070, FR-071);
7. closes the itinerary and stamps `return_time` when it was the last unresolved stop (FR-042).

All seven happen in one transaction. A stop already resolved returns 409 (US5 scenario 7).

---

## Sales-order additions — SystemObject `SALES_ORDERS` (7)

No new route. `GET /sales-orders/{id}` gains a derived `delivery_coverage` block per line —
ordered, covered, delivered, outstanding — from `attach_derived` (FR-070). The existing `delivered`
field on the sales order becomes meaningful (FR-071).

---

## Not built

- No notification endpoint — deferred with rationale (spec Divergences).
- No print, ticket or PDF route — out of scope.
- No route to edit `sent_quantity` after departure — it is immutable by design (FR-029).
- No standalone delivery-order creation — sales orders are the only origin.
