# Quickstart: Delivery & Logistics Endpoints

End-to-end validation. Each scenario proves one user story against a running API. Endpoint shapes
are in [contracts/README.md](./contracts/README.md); field meanings in
[data-model.md](./data-model.md).

## Prerequisites

```bash
uv sync
uv run ruff check app/ migrations/ tests/     # must be clean before and after
uv run pytest                                  # baseline green
```

Database: MariaDB 10.11 reachable at `DATABASE_URL`. Migration `008` applied (see
[Migration](#migration)). A user with privileges on system objects 71, 87, 91 and 94, linked to an
employee, with a point of sale whose warehouse holds stock.

```bash
uv run uvicorn app.main:app --reload
export TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -d 'username=<user>&password=<pass>' | jq -r .access_token)
alias api='curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"'
```

## Migration

> **Do not run `008` before the [R12 audit](./research.md#r12--pre-migration-data-audit) is
> complete.** Three checks were outstanding when the database went offline during planning: folio
> placeholders and duplicates on `delivery_order`, `lot_serial_rqmt` occupancy, and delivery orders
> without a ship-to. Each gates a migration step.
>
> `008` is destructive: it drops seven columns (five on `delivery_order`, two on
> `deliveries_itinerary`) and settles all 26,763 existing delivery orders into
> terminal statuses. Any delivery genuinely in flight at cutover must be re-raised from its sales
> order. Take a backup and schedule it for a quiet period.

```bash
mysql mbe_dev < migrations/008_delivery_flow_v2.sql
# rollback: mysql mbe_dev < migrations/008_delivery_flow_v2_rollback.sql
```

Verify:

```bash
# every legacy order is terminal; the pending queue starts empty
api localhost:8000/api/v1/delivery-itineraries/deliveries | jq '[.buckets[].total] | add'   # 0
# the in-transit warehouse exists and matches the configured id
```

---

## Scenario 1 — Raise and confirm a delivery order (US1)

```bash
api -X POST localhost:8000/api/v1/delivery-orders -d '{"sales_order": <SO_ID>}'
```

**Expect**: 201, `status = "DRAFT"`, one line per deliverable sales-order line with
`quantity` = the uncovered remainder, `open_quantity` equal to it, and the customer, ship-to,
contact and facility copied from the sales order.

```bash
api -X POST localhost:8000/api/v1/delivery-orders/<DO>/confirm
```

**Expect**: `serial` assigned; `status = "PENDING_APPROVAL"` when
`delivery_order_approval_required` is on. When it is off, confirmation branches by fulfilment type:
`"IN_PREPARATION"` for a delivery order, `"APPROVED"` for a counter pickup (FR-020).

**Negative**: raising from an uncompleted or cancelled sales order → 409. Raising a second order
once every line is covered → 409 "already fully delivered". Editing after confirmation → 409.

---

## Scenario 2 — Approve and reject (US2)

```bash
api localhost:8000/api/v1/delivery-orders/approval
api -X POST localhost:8000/api/v1/delivery-orders/approval/<DO>/reject -d '{"reason": "Wrong address"}'
api localhost:8000/api/v1/delivery-orders?mine=true\&status=DRAFT
```

**Expect**: the queue contains only `PENDING_APPROVAL` orders; rejection returns the order to
`DRAFT` with `rejection_reason` set; the creator finds it via `mine=true` — **no notification is
sent**, this listing is the discovery path.

**Negative**: reject with `{"reason": ""}` → 422. Approve an order not in `PENDING_APPROVAL` → 409.

---

## Scenario 3 — Load a truck, and prove the guard holds (US3)

```bash
api localhost:8000/api/v1/delivery-itineraries/deliveries
api -X POST localhost:8000/api/v1/delivery-itineraries -d '{"vehicle": <V>, "vehicle_operator": <OP>}'
api -X POST localhost:8000/api/v1/delivery-itineraries/<IT>/stops -d '{"delivery_order": <DO>}'
api -X POST localhost:8000/api/v1/delivery-itineraries/<IT>/stops/<ST>/lines \
    -d '{"delivery_order_detail": <L>, "quantity": 4}'
```

**Expect**: six buckets with `today` selected by callers; the itinerary defaults to today and the
point-of-sale warehouse; committing 4 of 10 leaves `open_quantity = 6`.

**The guard (SC-004)** — the assertion that matters most:

```bash
# two concurrent commitments of the remaining 6 on the same line
api -X POST .../stops/<ST_A>/lines -d '{"delivery_order_detail": <L>, "quantity": 6}' &
api -X POST .../stops/<ST_B>/lines -d '{"delivery_order_detail": <L>, "quantity": 6}' &
wait
```

**Expect**: exactly one 201 and one 422 stating the available open quantity. Never two 201s. Run
this several times — a guard that passes once may still be racy.

**Negative**: a second open itinerary for the same vehicle → 409. An expired operator licence →
201 with a `warnings[]` entry, never a refusal.

---

## Scenario 4 — Depart, and watch stock tell the truth (US4)

Record on-hand for a stocked product before anything, then:

```bash
api -X POST localhost:8000/api/v1/sales-orders/<SO>/confirm     # on-hand UNCHANGED
api -X POST localhost:8000/api/v1/delivery-itineraries/<IT>/depart
```

**Expect (SC-005)** — the invariant this feature exists to protect:

| Point | Warehouse on-hand | In-transit |
|---|---|---|
| Before | `N` | 0 |
| After sales-order confirm | `N` — *unchanged* | 0 |
| After departure | `N − sent` | `sent` |
| After delivery | `N − sent` | 0 |

**Also confirm**: availability (on-hand minus reservations) *did* drop at confirmation, so
confirming ten orders against one item is refused on the second. This is the check that makes the
change safe — see [R5](./research.md#r5--available-to-promise-replaces-on-hand-at-sales-order-confirmation).

**Negative**: departing an itinerary with nothing committed → 409. Cancelling after departure →
409.

---

## Scenario 5 — Close a stop with proof (US5)

Two-line order, one accepted in full, one partly refused:

```bash
curl -H "Authorization: Bearer $TOKEN" -X POST \
  localhost:8000/api/v1/delivery-itineraries/<IT>/stops/<ST>/close \
  -F 'receiver_name=Juan Pérez' -F 'receiver_id_shown=INE 1234' \
  -F 'image=@signature.png' \
  -F 'lines=[{"line_id":<A>,"delivered_quantity":5},
             {"line_id":<B>,"delivered_quantity":2,"reason_code":"DAMAGED_GOODS"}]'
```

**Expect**: order → `PARTIALLY_DELIVERED`; a child order at `IN_PREPARATION` naming its parent and
carrying the remainder; accepted quantity consumed from in-transit; refused quantity back in the
dispatch warehouse; proof linked to both the stop and the order.

**Proof is authenticated (SC-006a)** — the security assertion:

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/v1/delivery-orders/<DO>/proof/image   # 401
api -o /dev/null -w '%{http_code}\n' localhost:8000/api/v1/delivery-orders/<DO>/proof/image       # 200
```

Also confirm the image is **not** reachable under `/images/` — that mount is unauthenticated and
POD files must not live there.

**Negative**: closing without `image` → 422. `delivered_quantity` above sent → 422. A shortfall
with no `reason_code` → 422. Re-closing a resolved stop → 409.

**Independent stops**: with two stops, fail one and deliver the other — the second closes normally
and the itinerary stamps `return_time` only when both are resolved.

---

## Scenario 6 — Counter pickup (US6)

Raise an order whose sales order ships to a facility address.

```bash
api localhost:8000/api/v1/delivery-orders/<DO>       # fulfillment_type = PICKUP
api -X POST localhost:8000/api/v1/delivery-orders/<DO>/ready-for-pickup
curl -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/delivery-orders/<DO>/pickup \
  -F 'receiver_name=Ana Ruiz' -F 'receiver_id_shown=INE 5678' -F 'image=@signature.png'
```

**Expect**: `PICKED_UP`; stock consumed directly from the store warehouse with **no** in-transit
entry; proof stored to the same standard as a delivery.

**Negative**: the order never appears in the pending-deliveries view or on an itinerary.
`ready-for-pickup` on a `DELIVERY`-type order → 409. Pickup without an image → 422.

---

## Scenario 7 — Retry and cancel (US7)

```bash
api -X POST localhost:8000/api/v1/delivery-orders/<DO>/requeue
api -X POST localhost:8000/api/v1/delivery-orders/<DO2>/cancel -d '{"reason": "Customer cancelled"}'
```

**Expect**: `FAILED` → `IN_PREPARATION`, order reappears in the pending view with open quantity
reflecting the returned goods. Cancellation records the reason and releases commitments.

**Negative**: cancel with a blank reason → 422. Cancel from `IN_TRANSIT` → 409. Cancel a terminal
order → 409.

---

## Scenario 8 — Audit trail (US8)

```bash
api localhost:8000/api/v1/delivery-orders/<DO>/events
```

**Expect (SC-008)**: one entry per transition in order, the first with `from_status = null`
recording creation into `DRAFT`, each naming employee and timestamp, and every rejection, failure
and cancellation carrying its reason.

**Completeness check**: drive one order the full length of the flow and assert the event count
equals the number of transitions taken. No status change may go unrecorded.

---

## Invariant sweep

After the scenarios, assert across every delivery-order line (SC-003):

```text
quantity = delivered_quantity + returned_quantity + committed_quantity + open_quantity
```

and per facility (SC-009): no two delivery orders share a `serial`. The unique index added by `008`
enforces the second; the first is the service tests' primary target.

## Exit criteria

- [ ] All eight scenarios pass, including every negative case
- [ ] The concurrency race in Scenario 3 produces exactly one success, repeatedly
- [ ] The stock table in Scenario 4 holds at every point
- [ ] POD images return 401 unauthenticated and are absent from `/images/`
- [ ] `uv run pytest` green, including the reworked sales-order tests
- [ ] `uv run ruff check app/ migrations/ tests/` clean
- [ ] `CHANGELOG.md` `[Unreleased]` updated
