# Delivery Flow v2

Status state machine replacing the `IsConfirmed` / `IsDelivered` / `IsPickedUpInStore`
booleans, with explicit partial-delivery, failure, proof-of-delivery, and inventory
semantics.

---

## 1. Fulfillment type

A **type**, not a status. Set at DO creation from the SO folio and immutable
afterwards. Both types share the lifecycle up to `APPROVED`, then diverge.

| `DeliveryOrder.FulfillmentType` | Behaviour |
|---|---|
| `DELIVERY` | Full route: preparation → itinerary → transit → outcome |
| `PICKUP` | Short-circuits after approval, no itinerary |

---

## 2. State machine

Every transition writes a row to `delivery_order_events` (§6). Cancellation is
allowed from any non-terminal status with a required reason.

```
Sales Order (completed, not cancelled)
    │
    ▼ [create DO from SO folio]
┌───────┐
│ DRAFT │──────────────────[cancel + reason]──────────────► CANCELLED (terminal)
└───────┘
    │ [confirm]
    ▼
┌──────────────────┐         skipped when
│ PENDING_APPROVAL │  ─ ─ ─  WebConfig.DeliveryOrderApprovalRequired = false  ─ ─ ┐
└──────────────────┘                                                              │
    │ [approve]        [reject] ──► back to DRAFT                                 │
    │                              + RejectionReason, notify creator              │
    ▼                                                                             ▼
┌──────────┐
│ APPROVED │◄─────────────────────────────────────────────────────────────────────┘
└──────────┘
    │
    ├─ FulfillmentType = PICKUP
    │      │
    │      ▼
    │  ┌───────────────────┐  [confirm pickup]   ┌───────────┐
    │  │ READY_FOR_PICKUP  │───────────────────► │ PICKED_UP │ (terminal)
    │  └───────────────────┘                     └───────────┘
    │      POD: receiver name, ID shown, timestamp, signature
    │      INV: consumed directly from the store warehouse
    │
    ▼ FulfillmentType = DELIVERY
┌────────────────┐
│ IN_PREPARATION │   "For Delivery" view = filter on this status
└────────────────┘   warehouse picks & loads
    │                GUARD: open quantity cannot be committed to two active itineraries
    │
    ▼ [confirm itinerary departure]
┌────────────┐
│ IN_TRANSIT │   SentQuantity fixed per line
└────────────┘   INV: Warehouse ──► IN_TRANSIT virtual location
    │
    ▼ [confirm stop — capture POD, DeliveredQuantity + reason code per line]
    │
    ├─ all lines Delivered = Sent ──────────► DELIVERED           (terminal)
    │                                          INV: IN_TRANSIT ──► consumed
    │
    ├─ some accepted, some not ─────────────► PARTIALLY_DELIVERED (terminal)
    │                                          INV: accepted ──► consumed
    │                                          INV: rejected ──► warehouse
    │                                          remainder ──► child DO @ APPROVED
    │
    └─ nothing delivered ───────────────────► FAILED
                                               reason: nobody home / wrong address /
                                                       rejected
                                               INV: IN_TRANSIT ──► warehouse
                                               re-queue ──► IN_PREPARATION
                                               or ──► CANCELLED + reason
```

### Statuses

| Status | Kind | Notes |
|---|---|---|
| `DRAFT` | flow | Lines copied from SO. Quantities and address editable. |
| `PENDING_APPROVAL` | flow | Only when approval is configured. Reject returns to `DRAFT` with a reason — no DO sits in limbo. |
| `APPROVED` | branch | Diverges on `FulfillmentType`. |
| `READY_FOR_PICKUP` | flow | Counter pickup only. |
| `PICKED_UP` | terminal | Requires POD. |
| `IN_PREPARATION` | flow | Assignable to an itinerary. |
| `IN_TRANSIT` | flow | `SentQuantity` fixed at departure. |
| `DELIVERED` | terminal | POD archived on the DO. |
| `PARTIALLY_DELIVERED` | terminal | Remainder split into a child DO. |
| `FAILED` | flow/terminal | Retry via `IN_PREPARATION`, or cancel with reason. |
| `CANCELLED` | terminal | Reachable from any non-terminal status; reason required. |

### Rules attached to transitions

- **GUARD — double assignment.** A line's open quantity can never be committed to
  two active itineraries. Enforce `OpenQuantity >= requested` in the service layer,
  backed by `SELECT ... FOR UPDATE` on the DO line in MariaDB.
- **INV — two-step inventory move.** Warehouse → `IN_TRANSIT` virtual location at
  departure; `IN_TRANSIT` → consumed on delivery. On-hand stays honest while the
  truck is on the road, and returns are a simple reverse move.

  > **Superseded in part by spec 013.** This document describes *one* `IN_TRANSIT`
  > virtual location. There is now **one per facility**, and a line settles against
  > the location belonging to the facility that owns its dispatch warehouse. A single
  > global location had to be parented on an arbitrary facility, which put every
  > facility's in-transit stock on one facility's books. The two-step move itself is
  > unchanged — only which location the second step names.
- **POD — proof of delivery.** At every terminal handover (delivery *and* counter
  pickup): receiver name, timestamp, signature or photo, plus per-line
  `DeliveredQuantity` and a reason code for any shortfall. A status flip alone is
  not proof.

---

## 3. Quantities per DO line

What left the warehouse and what the customer accepted are not always equal — track
them separately.

| Field | Meaning |
|---|---|
| `OrderedQuantity` | Copied from the SO line at DO creation. |
| `CommittedQuantity` | Reserved on an active itinerary — the double-assignment guard. |
| `SentQuantity` | Left the warehouse (fixed at itinerary departure). |
| `DeliveredQuantity` | Accepted by the customer, backed by POD. |
| `ReturnedQuantity` | Rejected or failed — physically back in the warehouse. |

```
OpenQuantity = OrderedQuantity - DeliveredQuantity - CommittedQuantity
               // drives re-dispatch & child-DO splits
```

---

## 4. Delivery Itinerary — one record per trip

The itinerary is the unit of dispatch: it holds the truck, the driver, and every DO
line riding along. It closes stop by stop.

**Trip header**

- Vehicle — plate / internal unit number
- Operator — driver assigned for the trip
- Origin warehouse — where the load departs from
- Departure & return timestamps — `SentQuantity` is fixed at departure

One itinerary open at a time per vehicle; closing it releases the vehicle for the
next dispatch.

**Per stop**

- Sequence — stop order within the trip
- Delivery Order — one or more per stop, with their lines
- `SentQuantity` per DO line, set at departure
- Outcome — `DeliveredQuantity`, reason code, POD (§2)

Stops resolve independently: one failed stop doesn't block the rest of the trip from
closing.

---

## 5. Counter pickup is not a shortcut on proof

Same evidentiary standard as delivery: who picked it up and when. This is what
settles disputes.

---

## 6. Audit trail

```
delivery_order_events
  (delivery_order_id, from_status, to_status, user_id, timestamp, note/reason)
```

Written on **every** transition via a SQLAlchemy event listener. Costs almost
nothing; answers every "¿quién lo mandó?" later.

---

## Design decisions — override consciously

**D1 — Partial delivery splits into a child DO** (linked by `ParentDeliveryOrderId`)
rather than reopening the parent. One DO = one trip = one POD; invoicing
reconciliation stays clean.

**D2 — `FAILED` re-queues to `IN_PREPARATION`, not `APPROVED`.** The goods physically
returned and need re-loading anyway. If failed returns are staged separately, add a
`RETURNED` intermediate status.

**D3 — `PICKUP` passes through approval** when the config requires it,
branching only after `APPROVED`. If pickups shouldn't need approval, move the branch
earlier — but make that an explicit `WebConfig` choice.

**D4 — `CommittedQuantity` is the concurrency guard.** Service-layer check plus row
lock. Two dispatchers hitting the same line simultaneously is exactly the race this
exists for.
