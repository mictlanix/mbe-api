# Phase 1 Data Model: Delivery & Logistics Endpoints

Four existing tables change, three are new, and one column is added to nothing else. Every
decision here traces to [research.md](./research.md).

---

## Enums (`app/enums.py`)

```python
class DeliveryOrderStatus(IntEnum):
    DRAFT = 0
    PENDING_APPROVAL = 1
    APPROVED = 2
    READY_FOR_PICKUP = 3
    PICKED_UP = 4            # terminal
    IN_PREPARATION = 5
    IN_TRANSIT = 6
    DELIVERED = 7            # terminal
    PARTIALLY_DELIVERED = 8  # terminal
    FAILED = 9
    CANCELLED = 10           # terminal


class FulfillmentType(IntEnum):
    DELIVERY = 0
    PICKUP = 1


class ItineraryStatus(IntEnum):
    OPEN = 0
    DEPARTED = 1
    CLOSED = 2       # terminal
    CANCELLED = 3    # terminal


class StopOutcome(IntEnum):
    PENDING = 0
    DELIVERED = 1
    PARTIALLY_DELIVERED = 2
    FAILED = 3


class ShortfallReason(IntEnum):
    CUSTOMER_REFUSED = 0
    NOBODY_PRESENT = 1
    WRONG_ADDRESS = 2
    DAMAGED_GOODS = 3
    OTHER = 4
```

`TransactionType` gains `DELIVERY_ORDER = **10**` for the transit movements (FR-057 to FR-060),
together with the five legacy values (5-9) this codebase had not modelled.

> **Audit result — the original proposal was wrong.** `DELIVERY_ORDER = 5` was planned here.
> `docs/constants.md` allocates the whole 1-9 range, and `lot_serial_tracking` carries **38,411
> rows at source 5** (inventory transfers). Value 10 is the first beyond the legacy range. The
> unmodelled legacy values are now in the enum so the range cannot be misread as free again.
> See [R12/A1](./research.md#r12--pre-migration-data-audit).

---

## `delivery_order` — changed

| Column | Change | Notes |
|---|---|---|
| `completed`, `cancelled`, `confirmed`, `delivered`, `picked_up` | **dropped** | Subsumed by `status` (R1) |
| `status` | **new** `SMALLINT NOT NULL` | `DeliveryOrderStatus` |
| `fulfillment_type` | **new** `SMALLINT NOT NULL DEFAULT 1` | `FulfillmentType` (`0` pickup, `1` delivery); immutable after creation (FR-004). Renumbered by migration 018 from `0` delivery / `1` pickup, onto the scale `sales_order.fulfillment_intent` uses, so one enum serves both (#170). `MIXED` is refused here — it describes a sale, not a shipment |
| `parent_delivery_order` | **new** `INT NULL` FK → self | Set on a partial-delivery child (FR-048) |
| `rejection_reason` | **new** `VARCHAR(500) NULL` | Cleared when the order leaves `DRAFT` again (FR-023) |
| `proof_of_delivery` | **new** `INT NULL` FK → `proof_of_delivery` | Set at settlement, for both fulfilment types (FR-043) |
| `serial` | **relaxed** to `NULL`-able, gains `UNIQUE (facility, serial)` | R10 — folio assigned at confirmation, `NULL` before |
| everything else | unchanged | `facility`, `customer`, `ship_to`, `contact`, `date`, `priority`, `comment`, audit columns |

**Uniqueness.** `UNIQUE (facility, serial)` — the backstop behind `documents.assign_folio` (SC-009).

**Validation.**

- `status` transitions only along the state machine below; enforced in one place (see
  [Transitions](#transitions)).
- `fulfillment_type` is write-once at creation, and never `MIXED`: a shipment is one kind or the other, and a mixed sale is one that produces a delivery order of each (#170).
- Editable only in `DRAFT` (FR-006), enforced by `delivery_order_service.assert_editable` — **not**
  `documents.assert_editable`, which reads columns this migration drops (R8).

---

## `delivery_order_detail` — changed

| Column | Change | Meaning |
|---|---|---|
| `quantity` | unchanged | **Ordered** quantity (FR-025) |
| `committed_quantity` | **new** `DECIMAL(18,4) NOT NULL DEFAULT 0` | Reserved on an **active** itinerary — set when committed, **retained through `IN_TRANSIT`**, cleared only at stop closure (FR-029a) |
| `delivered_quantity` | **new** `DECIMAL(18,4) NOT NULL DEFAULT 0` | Accepted, backed by POD |
| `returned_quantity` | **new** `DECIMAL(18,4) NOT NULL DEFAULT 0` | Refused or failed, back in the warehouse |
| `warehouse` | **new** `INT NOT NULL` FK → `warehouse` | Dispatch origin, snapshotted at creation (FR-025a) |

**No `sent_quantity` here.** Sent lives on `deliveries_itinerary_detail`, where each trip records
its own. On the delivery-order line it would be referenced by no invariant, would equal
`committed_quantity` for the whole window both are live, and after closure would restate what
`delivered + returned` already say.

**`warehouse` is snapshotted, not joined.** `delivery_order_detail.sales_order_detail` is nullable
and `sales_order_detail.warehouse` is nullable too, so the inherited path yields no warehouse for
some lines — and FR-057/FR-059 need one for every stocked line at departure. Snapshotting at
creation matches how this same table already captures `product_code` and `product_name`, and keeps
the departure path a single column read.

```text
open_quantity = quantity − delivered_quantity − returned_quantity − committed_quantity   (FR-026)
```

**These four columns are running totals, and the denormalisation is load-bearing.** The per-trip
truth lives on `deliveries_itinerary_detail`; these totals exist so the double-assignment guard can
read `open_quantity` as plain arithmetic on the single row it has locked (R2). Deriving them by
aggregating itinerary lines would mean the lock no longer covers the values it protects. Every
write to a total happens in the same transaction as the itinerary-line write that caused it.

**Invariant (SC-003).** `quantity = delivered + returned + committed + open`, on every line, at
every point. This is the assertion the service tests target directly, and it is the same statement
as the formula above rearranged — the two must never be allowed to drift apart.

### How the totals move

`returned` is subtracted from `open` because returned goods are always accounted for somewhere
else: by the child order a partial delivery creates, or by the requeue that puts them back in play.
Two rules make that hold.

| Moment | Totals | Why |
|---|---|---|
| Commitment taken | `committed += n` | Guard consumes open quantity (R2) |
| Departure | `committed` **unchanged**, `sent += n` | The goods are still spoken for; releasing `committed` here would return them to the open pool while they are on the truck, and a second dispatcher could commit them again — the exact race SC-004 forbids |
| Stop closed | `committed −= n`, `delivered += accepted`, `returned += refused` | The commitment resolves into its two outcomes |
| Partial delivery | parent keeps `returned`; child carries the remainder as its own `quantity` | Parent settles at `open = 0`; the remainder is counted once, on the child (FR-048, SC-007) |
| Requeue from `FAILED` | `returned −= n`, restoring `open` | FR-051a. Without this a re-queued order has `open = 0` and can never be dispatched |

Worked example — a line of 5, sent 5, 3 accepted and 2 refused:

```text
after closure   open = 5 − 3 − 2 − 0 = 0      child order carries 2      ✅ counted once
if `returned` were not subtracted:
                open = 5 − 3 − 0 = 2          child order carries 2      ❌ counted twice
```

---

## `proof_of_delivery` — new

| Column | Type | Notes |
|---|---|---|
| `proof_of_delivery_id` | `INT PK` | |
| `receiver_name` | `VARCHAR(250) NOT NULL` | Non-blank (FR-043) |
| `receiver_id_shown` | `VARCHAR(100) NOT NULL` | Identification presented |
| `captured_time` | `DATETIME NOT NULL` | |
| `captured_by` | `INT NOT NULL` FK → `employee` | |
| `image_file` | `VARCHAR(255) NOT NULL` | UUID filename under `settings.pod_dir` (R6) |

One table serving both handover kinds. A delivery's proof hangs off the stop and is *also* pointed
at by each delivery order settled there, so "the proof is archived on the order" holds uniformly
and one signature can cover several orders dropped at the same place. A counter pickup's proof is
pointed at only by its order.

**Never content-addressed** (R6): `image_file` is a UUID so two identical captures cannot alias,
satisfying FR-044b. Served only through the authenticated route (FR-044a).

---

## `delivery_order_event` — new

| Column | Type | Notes |
|---|---|---|
| `delivery_order_event_id` | `INT PK` | |
| `delivery_order` | `INT NOT NULL` FK | |
| `from_status` | `SMALLINT NULL` | `NULL` only for the creation entry (FR-065) |
| `to_status` | `SMALLINT NOT NULL` | |
| `employee` | `INT NOT NULL` FK → `employee` | |
| `event_time` | `DATETIME NOT NULL` | |
| `reason` | `VARCHAR(500) NULL` | Required for rejection, failure, cancellation |

Append-only. Written by an explicit service-layer helper, **not** an ORM event listener — see R7
for why v2 §6 is overridden here.

**Index**: `(delivery_order, delivery_order_event_id)` for ordered history reads (FR-064).

---

## `deliveries_itinerary` — changed

| Column | Change | Notes |
|---|---|---|
| `status` | **new** `SMALLINT NOT NULL` | `ItineraryStatus` (FR-033a) |
| `cancelled`, `completed` | **dropped** | Subsumed by `status` |
| `departure_time` | **new** `DATETIME NULL` | Stamped on `OPEN → DEPARTED` (FR-040) |
| `return_time` | **new** `DATETIME NULL` | Stamped on `DEPARTED → CLOSED` (FR-042) |
| everything else | unchanged | `date`, `vehicle`, `vehicle_operator`, `warehouse`, `comment`, audit columns |

**Index**: `(status, date)` — serves the FR-068 `state` filter and the one-open-per-vehicle check.

**Lifecycle** — stored, not derived. The plan originally argued the existing booleans expressed
this well enough; FR-068's `state` filter forces the derivation into a query predicate anyway, so
storing it removes a reconstruction rather than adding state. `departure_time` and `return_time`
remain, as timestamps rather than as state.

```text
OPEN ──[depart]──► DEPARTED ──[last stop resolves]──► CLOSED   (terminal)
  │
  └──[cancel]────► CANCELLED (terminal)   — only from OPEN (FR-041)
```

**Invariant (FR-034).** At most one `OPEN` itinerary per vehicle, enforced by a service-layer check
under a lock on the `vehicle` row (R9); MariaDB cannot express it as a partial unique index.

> **Migration consequence.** Legacy itineraries with `cancelled = 0, completed = 0` must **not**
> become `OPEN`. FR-034 would then permanently block every vehicle carrying a stale one from ever
> receiving a new itinerary. They settle to `CLOSED`, consistent with R11.

---

## `deliveries_itinerary_stop` — new

| Column | Type | Notes |
|---|---|---|
| `deliveries_itinerary_stop_id` | `INT PK` | |
| `deliveries_itinerary` | `INT NOT NULL` FK | |
| `sequence` | `SMALLINT NOT NULL` | Stop order within the trip (FR-036) |
| `arrival_time` | `DATETIME NULL` | Set at closure |
| `outcome` | `SMALLINT NOT NULL DEFAULT 0` | `StopOutcome` |
| `proof_of_delivery` | `INT NULL` FK | Required to close with anything accepted (FR-043) |
| `comment` | `VARCHAR(500) NULL` | |

**Uniqueness**: `UNIQUE (deliveries_itinerary, sequence)`.

A stop belongs to exactly one itinerary; a delivery order appears at exactly one stop of a trip
(spec Assumptions). Stops resolve independently (FR-050).

---

## `deliveries_itinerary_detail` — changed

| Column | Change | Meaning |
|---|---|---|
| `quantity` | **renamed** to `committed_quantity` | What this trip claims of the line |
| `deliveries_itinerary_stop` | **new** `INT NOT NULL` FK | Replaces the direct itinerary link as the grouping level |
| `sent_quantity` | **new** `DECIMAL(20,6) NOT NULL DEFAULT 0` | Fixed at departure (FR-029) |
| `delivered_quantity` | **new** `DECIMAL(20,6) NOT NULL DEFAULT 0` | Recorded at closure |
| `returned_quantity` | **new** `DECIMAL(20,6) NOT NULL DEFAULT 0` | Recorded at closure |
| `reason_code` | **new** `SMALLINT NULL` | `ShortfallReason`; required when `delivered < sent` (FR-045a) |
| `deliveries_itinerary` | **dropped** | The stop is the sole path to the trip |
| `delivery_order_detail`, `comment` | unchanged | |

**One path to the itinerary, not two.** Keeping a direct `deliveries_itinerary` FK alongside the
stop FK would save one indexed join on tables of 3,617 and 9,957 rows, at the cost of a state where
a line claims itinerary A while its stop belongs to itinerary B — with nothing declaring which
wins. Trip-scoped queries join through the stop.

The rename is safe: nothing in this API reads the table today, and the old name (`quantity`) would
be actively misleading once four quantities coexist.

---

## Transitions

Every arrow writes one `delivery_order_event`. All of them route through a single
`delivery_order_service.transition(order, to_status, *, employee, reason=None)` helper that moves
the status and writes the row together — the discipline behind SC-008 (R7).

```text
                    ┌───────┐
      (create) ────►│ DRAFT │◄──── reject + reason ────┐
                    └───┬───┘                          │
                        │ confirm (assign folio)       │
              ┌─────────┴─────────┐                    │
   approval   │                   │  no approval       │
   required   ▼                   │  required          │
     ┌──────────────────┐         │                    │
     │ PENDING_APPROVAL │         │                    │
     └────────┬─────────┘         │                    │
              │ approve           │                    │
              └── reject ─────────┼────────────────────┘
                                  │
        THE BRANCH — one transition, taken at approval (or at confirmation
        when approval is not required). APPROVED is never a transient step
        for a delivery order; it is where a counter pickup rests. (FR-024)
                                  │
          PICKUP ◄────────┴────────► DELIVERY
                 │                              │
                 ▼                              ▼
           ┌──────────┐                ┌────────────────┐
           │ APPROVED │                │ IN_PREPARATION │◄──── re-queue ──┐
           └────┬─────┘                └───────┬────────┘                 │
                │ mark ready                   │                          │
                ▼                              │                          │
        ┌───────────────────┐                  │                          │
        │ READY_FOR_PICKUP  │                  │                          │
        └─────────┬─────────┘                  │                          │
                  │ confirm pickup + POD       │ itinerary departs        │
                  ▼                            ▼                          │
           ┌───────────┐               ┌────────────┐                     │
           │ PICKED_UP │ terminal      │ IN_TRANSIT │                     │
           └───────────┘               └─────┬──────┘                     │
                                             │ stop closed + POD          │
                        ┌────────────────────┼────────────────────┐       │
                        ▼                    ▼                    ▼       │
                 ┌───────────┐  ┌─────────────────────┐     ┌────────┐    │
                 │ DELIVERED │  │ PARTIALLY_DELIVERED │     │ FAILED │────┘
                 └───────────┘  └─────────┬───────────┘     └───┬────┘
                    terminal              │ child DO            │
                                          ▼ @ IN_PREPARATION   ▼
                                       terminal              CANCELLED

  CANCELLED is reachable from any non-terminal status except IN_TRANSIT, reason required (FR-007).
```

**Terminal**: `PICKED_UP`, `DELIVERED`, `PARTIALLY_DELIVERED`, `CANCELLED`.
**`FAILED` is not terminal** — it re-queues to `IN_PREPARATION` or cancels (v2 D2, FR-051).
**`IN_TRANSIT` cannot be cancelled** — goods on the road resolve at the stop (FR-007).

### The table is keyed on `(from, to, fulfillment_type)`

Since the branch happens *at* approval (FR-024), legality is not a function of the two statuses
alone. A plain `{from: {to}}` mapping would permit a delivery order to reach `READY_FOR_PICKUP`.
These five transitions are type-restricted; every other transition is legal for both types:

| From | To | Legal for |
|---|---|---|
| `PENDING_APPROVAL` | `IN_PREPARATION` | `DELIVERY` only |
| `PENDING_APPROVAL` | `APPROVED` | `PICKUP` only |
| `DRAFT` | `IN_PREPARATION` | `DELIVERY` only — confirmation with approval disabled |
| `DRAFT` | `APPROVED` | `PICKUP` only — confirmation with approval disabled |
| `APPROVED` | `READY_FOR_PICKUP` | `PICKUP` only |

`transition()` already receives the order, so it reads `fulfillment_type` itself. Enforcing this in
the chokepoint rather than in each calling service is the point of having a chokepoint — a guard
that lives in `ready_for_pickup()` alone stops the helper being the complete authority R7 designed
it to be.

---

## Inventory movements

| Event | Ledger effect | Reservation effect |
|---|---|---|
| Sales order confirmed | *(none)* | `lot_serial_rqmt` row per stocked line (FR-055) |
| Sales order cancelled | *(none)* | rows deleted (FR-056) |
| Itinerary departs | `−qty @ dispatch warehouse`, `+qty @ in-transit` | matching rows deleted (FR-057) |
| Line accepted | `−qty @ in-transit` | — (FR-058) |
| Line returned / failed | `−qty @ in-transit`, `+qty @ dispatch warehouse` | — (FR-059) |
| Counter pickup confirmed | `−qty @ store warehouse` | rows deleted (FR-060) |

Non-stocked lines move nothing (FR-061). Entries are never edited or deleted (FR-062).

**Availability**, replacing raw on-hand in the sales-order stock check (R5):

```text
available(product, warehouse) = on_hand(product, warehouse) − reserved(product, warehouse)
```

Without the subtraction, confirmation no longer decrements anything and the same physical unit
would satisfy an unlimited number of orders. This is the single most important consequence of the
inventory decision, and it is required by **FR-055a** rather than left to the design notes.

---

## Sales-order coupling

| Requirement | Effect |
|---|---|
| FR-070 | Per-line coverage (ordered / covered / delivered / outstanding) computed from delivery orders and attached by `sales_order_service.attach_derived`; **not stored** |
| FR-071 | `sales_order.delivered` set to `1` when every deliverable line is fully delivered — the one stored fulfilment fact |
| FR-055, FR-056 | `confirm_order` and `cancel_order` reworked per R5 |

Cancelled delivery orders never count as coverage, so re-raising an order after the R11 cutover
produces the correct lines.

---

## Settings (`app/core/config.py`)

| Setting | Default | Replaces |
|---|---|---|
| `delivery_order_approval_required` | `False` | `WebConfig.DeliveryOrderApprovalRequired` |
| `delivery_order_requires_paid_or_credit_sales_order` | `False` | `WebConfig.DeliveryOrderRequiresPaidOrCreditSalesOrder` |
| `min_span_hours_for_deliveries` | `0` | `WebConfig.MinSpanHoursForDeliveries` |
| `in_transit_warehouse_id` | `0` | *(new — R3; seeded by the migration)* |
| `pod_dir` | `pod` | *(new — R6; private, outside the `/images` mount)* |
