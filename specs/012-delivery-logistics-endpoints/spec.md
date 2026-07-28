# Feature Specification: Delivery & Logistics Endpoints

**Feature Branch**: `012-delivery-logistics-endpoints`

**Created**: 2026-07-26

**Status**: Draft

**Input**: User description: "implement endpoints for @docs/specs/06-logistics.md, use the new delivery flow @docs/specs/06a-delivery-flow-v2.md, the current state of this repo takes precedence over docs"

## Context

Two source documents describe this area and they disagree with each other by design.

`docs/specs/06-logistics.md` is a reverse-engineered description of the legacy ASP.NET MVC
delivery module: delivery orders raised from sales orders, a supervisor approval queue, a
date-grouped "For Delivery" view, and delivery itineraries that load lines onto a truck. Its
lifecycle is three loose booleans — `confirmed`, `delivered`, `picked_up` — layered over the
`completed` / `cancelled` pair every legacy document carries.

`docs/specs/06a-delivery-flow-v2.md` replaces that lifecycle with an explicit status state
machine, separates the five quantities a delivery line actually has, requires proof of delivery at
every handover, splits partial deliveries into child orders, and moves inventory in two steps so
on-hand stays honest while goods are on the road. **Where the two documents disagree, v2 wins** —
that is the point of this feature. Where either document disagrees with the repository, the
repository wins.

Today this API has no delivery capability at all. The four logistics tables are mapped in
`app/models/logistics.py` and nothing reads or writes them; there is no schema, no service and no
router entry. The adjacent capabilities this feature depends on already exist: sales orders and
their lines, the inventory ledger, facilities, warehouses, vehicles, vehicle operators, employees,
addresses, contacts, the incidence audit log, and the privilege system with its logistics
`SystemObject` values already enumerated.

### Source document coverage

| Source section | Disposition |
|---|---|
| 06 §1 Delivery Orders | In scope — User Stories 1 and 7 |
| 06 §1 Print / Ticket | **Out of scope** — mini-printer templates, PDF templates and the `only_current_warehouse` print filter are presentation concerns, consistent with how spec 011 treated the legacy print surface |
| 06 §2 Delivery Order Approval | In scope — User Story 2 |
| 06 §3 For Delivery | In scope — User Story 3, restated as a filter on `IN_PREPARATION` per v2 §2 |
| 06 §4 Delivery Itineraries | In scope — User Stories 3, 4 and 5 |
| 06a §1 Fulfillment type | In scope — User Stories 1 and 6 |
| 06a §2 State machine | In scope — all stories |
| 06a §3 Quantities per line | In scope — User Stories 3, 4 and 5 |
| 06a §4 Itinerary and stops | In scope — User Stories 3, 4 and 5 |
| 06a §5 Counter pickup proof | In scope — User Story 6 |
| 06a §6 Audit trail | In scope — User Story 8 |

## Clarifications

### Session 2026-07-26

- Q: The repository already consumes stock at sales-order confirmation, while v2 wants
  warehouse → `IN_TRANSIT` → consumed at delivery. Both would double-decrement. Which wins? →
  A: The full v2 two-step move. Sales-order confirmation stops writing an outbound ledger entry
  and records a reservation instead; goods leave the warehouse at itinerary departure and are
  consumed on delivery.
- Q: v2 §4 gives the itinerary per-stop structure, but the existing schema links an itinerary
  straight to a delivery-order line with no stop entity. How should stops be modelled? →
  A: Introduce a stop record between the itinerary and its lines, carrying the stop sequence, the
  arrival outcome and the proof of delivery, so one signature can cover several delivery orders
  dropped at the same place.
- Q: How far does proof of delivery go in this feature? → A: All the way — the structured fields
  *and* a captured signature or photo, stored the same way product images already are.
- Q: What happens to the 26,763 existing delivery orders and the 178,045 confirmed sales orders
  whose stock is already consumed? → A: Historical rows settle and the new invariant applies
  forward only. Legacy orders map to terminal statuses — delivered, picked up and cancelled ones
  to their equivalents, everything else to cancelled as abandoned — existing itinerary lines are
  recorded as fully sent and delivered, no reservations are backfilled, and the already-posted
  outbound entries stand. The pending-deliveries queue starts empty.
- Q: Should completing a delivery write back to the sales order, given `sales_order.delivered` is
  currently dead state? → A: Both — expose delivery coverage as a derived figure on the sales
  order using the existing derived-figures mechanism, *and* set `sales_order.delivered` to true
  once every deliverable line is fully delivered.
- Q: How are proof-of-delivery images retrieved, given product images are served by an
  unauthenticated static mount? → A: Through an authenticated endpoint gated on the same privilege
  as the delivery order, with the images stored outside the public static mount. A signature is
  personal data, and a content-addressed filename is obscurity rather than access control.
- Q: v2 §2 says rejection notifies the creator, but no notification infrastructure exists. Build
  it? → A: No. Rejections are made discoverable instead — the reason is stored on the order and in
  the audit trail, and delivery-order listing gains a "created by me" filter so an author finds
  their rejected drafts in one query. The v2 notification is deliberately deferred.
- Q: What reason codes accompany a shortfall? → A: One shared set — customer refused, nobody
  present, wrong address, damaged goods, other — used for both per-line shortfalls and whole-stop
  failures, a whole-stop failure being the case where every line carries the same reason.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Raise a delivery order from a sales order (Priority: P1)

As a salesperson or dispatcher, I need to turn a confirmed sales order into a delivery order that
lists exactly what is still owed to the customer, so the warehouse has a picking document and the
customer's outstanding balance of goods is explicit.

**Why this priority**: Nothing else in this feature exists without a delivery order. Every other
story reads, advances or closes one.

**Independent Test**: Can be fully tested by confirming a sales order, raising a delivery order
from its folio, and observing that the new order carries one line per
undelivered sales-order line, opens in `DRAFT`, and is refused a second time once every line has
been covered.

**Acceptance Scenarios**:

1. **Given** a completed, uncancelled sales order, **When** a delivery order is raised from it,
   **Then** an order is created in `DRAFT` carrying the sales order's customer, ship-to address,
   contact and facility, with one line per sales-order line whose ordered quantity is the part not
   yet covered by an existing delivery order, and a snapshot of each product's code and name.
2. **Given** a sales order that is not completed, or is cancelled, **When** a delivery order is
   raised from it, **Then** the request is refused and the reason names which condition failed.
3. **Given** a sales order every line of which is already fully covered by existing
   delivery orders, **When** a delivery order is raised from it, **Then** the request is refused
   as already fully delivered.
4. **Given** the configured rule that a delivery order requires a paid or credit-terms sales
   order, **When** a delivery order is raised from an unpaid immediate-terms order, **Then** the
   request is refused.
5. **Given** a `DRAFT` delivery order, **When** its scheduled date, priority, ship-to address,
   contact, notes or line quantities are edited, **Then** the change is persisted, and a line
   quantity above the sales order's remaining deliverable quantity is refused.
6. **Given** a `DRAFT` delivery order whose scheduled date is sooner than the configured minimum
   lead time, **When** it is confirmed, **Then** the request is refused unless the caller is an
   administrator.
7. **Given** a `DRAFT` delivery order with at least one line, **When** it is confirmed and
   approval is configured as required, **Then** it is assigned the next folio for its facility and
   moves to `PENDING_APPROVAL`.
8. **Given** the same order, **When** it is confirmed and approval is configured as not required,
   **Then** it is assigned its folio and branches straight past approval — to `IN_PREPARATION` if
   it is a delivery order, or to `APPROVED` if it is a counter pickup.
9. **Given** a delivery order in any status other than `DRAFT`, **When** an edit to its lines or
   header is attempted, **Then** the request is refused.

---

### User Story 2 - Approve or reject a delivery order (Priority: P1)

As a supervisor, I need a queue of delivery orders awaiting my decision, so nothing leaves the
warehouse without review and a rejected order goes back to its author with a stated reason rather
than sitting in limbo.

**Why this priority**: When approval is configured on, this is the only path from a confirmed
order to a loadable one. The dispatch flow is blocked without it.

**Independent Test**: Can be fully tested by confirming a delivery order under a configuration
requiring approval, listing the queue, approving one order and rejecting another, and observing
the first become loadable and the second return to `DRAFT` carrying its rejection reason.

**Acceptance Scenarios**:

1. **Given** delivery orders in various statuses, **When** the approval queue is listed, **Then**
   only orders in `PENDING_APPROVAL` are returned.
2. **Given** an order in `PENDING_APPROVAL` for delivery, **When** it is approved, **Then** it
   moves directly to `IN_PREPARATION` in a single recorded transition and appears in the
   pending-deliveries view; a counter-pickup order approved the same way rests at `APPROVED`.
3. **Given** an order in `PENDING_APPROVAL`, **When** it is rejected with a reason, **Then** it
   returns to `DRAFT`, the reason is stored on the order and recorded in the audit trail against
   the rejecting employee, and the order becomes editable again.
4. **Given** an order rejected back to `DRAFT`, **When** its creator lists the delivery orders
   they created, filtered to drafts, **Then** the order appears carrying its rejection reason —
   no notification is sent, and this listing is how the author learns of the rejection.
5. **Given** a rejection submitted without a reason or with a blank one, **When** it is
   submitted, **Then** it is refused.
6. **Given** an order that is not in `PENDING_APPROVAL`, **When** approval or rejection is
   attempted, **Then** the request is refused as an invalid transition.
7. **Given** approval configured as not required, **When** the queue is listed, **Then** it is
   empty, because confirmation branched every order past approval on its fulfilment type.

---

### User Story 3 - See what is pending and load it onto an itinerary (Priority: P1)

As warehouse staff, I need a date-grouped view of every delivery line still waiting to go out, and
the ability to commit those lines to a truck's itinerary, so a day's route is planned without two
dispatchers loading the same goods twice.

**Why this priority**: This is the planning step that turns approved paperwork into a physical
trip. It is also where the concurrency guard that protects the whole feature lives.

**Independent Test**: Can be fully tested by approving two delivery orders, listing the pending
view for today, creating an itinerary, committing lines to it, and observing that a second
itinerary cannot commit the same open quantity.

**Acceptance Scenarios**:

1. **Given** delivery orders in `IN_PREPARATION` for a dispatch warehouse, **When** the pending
   view is requested, **Then** it returns their lines grouped into a sliding window of buckets —
   earlier than yesterday, yesterday, today, tomorrow, the day after, and later — with each line's
   open quantity, sorted within a bucket by the sales order's priority, highest first.
2. **Given** an order that is not in `IN_PREPARATION`, **When** the pending view is requested,
   **Then** none of its lines appear.
3. **Given** an itinerary is created, **When** no vehicle, operator or date is supplied, **Then**
   it opens with today's date and the dispatch warehouse taken from the caller's point-of-sale
   setting, and stops and lines may be added.
4. **Given** an open itinerary, **When** a stop is added naming a delivery order, **Then** it
   receives the next sequence number in the trip, and delivery-order lines may be committed to it.
5. **Given** a line with open quantity 10, **When** a committed quantity of 4 is requested,
   **Then** the commitment is accepted and the line's open quantity becomes 6.
6. **Given** the same line, **When** a committed quantity above its open quantity is requested,
   **Then** the request is refused and the available open quantity is stated.
7. **Given** the same line already committed to an open itinerary, **When** a second itinerary
   requests the remainder concurrently, **Then** exactly one commitment succeeds and the other is
   refused — the open quantity is never committed twice.
8. **Given** a vehicle that already has an open itinerary, **When** a second itinerary is opened
   for it, **Then** the request is refused.
9. **Given** an operator whose licence has expired, **When** they are assigned to an itinerary,
   **Then** the assignment is accepted and the expiry is reported as an advisory warning.
10. **Given** an open itinerary with commitments, **When** it is cancelled, **Then** every
    commitment is released back to its line's open quantity and the delivery orders remain in
    `IN_PREPARATION`.

---

### User Story 4 - Dispatch the truck and track goods in transit (Priority: P1)

As a dispatcher, I need to declare an itinerary departed, so the quantities on board are frozen,
the goods stop counting as available stock in the warehouse, and everyone can see what is on the
road rather than on the shelf.

**Why this priority**: Departure is the point where the physical world and the ledger have to
agree. It is also the change that makes warehouse on-hand mean "actually here".

**Independent Test**: Can be fully tested by confirming a sales order and observing on-hand
unchanged, committing its delivery lines to an itinerary, declaring departure, and observing
on-hand fall by exactly the departed quantity while the same quantity appears in transit.

**Acceptance Scenarios**:

1. **Given** a sales order with stocked lines, **When** it is confirmed, **Then** the ordered
   quantity is reserved against its warehouse and no outbound ledger entry is written, so
   warehouse on-hand is unchanged.
2. **Given** a confirmed sales order whose stock is reserved, **When** the order is cancelled,
   **Then** the reservation is released and, because no outbound entry was written, no
   compensating entry is needed.
3. **Given** an open itinerary with committed lines, **When** departure is declared, **Then** the
   departure timestamp is recorded, each line's sent quantity is fixed at its committed quantity,
   every delivery order on the trip moves to `IN_TRANSIT`, and the itinerary accepts no further
   stops or commitments.
4. **Given** the same departure, **When** the ledger is inspected, **Then** each stocked line has
   an outbound entry against its dispatch warehouse and a matching inbound entry against the
   in-transit location, and its sales-order reservation is released.
5. **Given** an itinerary with no committed quantity anywhere on it, **When** departure is
   declared, **Then** the request is refused.
6. **Given** a line whose committed quantity exceeds its open quantity at the moment of
   departure, **When** departure is declared, **Then** the request is refused and the offending
   lines are named.
7. **Given** a departed itinerary, **When** cancellation is attempted, **Then** the request is
   refused — goods already on the road are resolved stop by stop, not by cancelling the trip.

---

### User Story 5 - Close a stop with proof of delivery (Priority: P1)

As a driver, I need to record what the customer actually accepted at each stop, backed by their
name and signature, so a disputed delivery is settled by evidence and any remainder is
automatically re-queued rather than quietly lost.

**Why this priority**: This is the outcome the whole flow exists to produce. Without it the goods
stay in transit forever and no delivery is ever provable.

**Independent Test**: Can be fully tested by departing an itinerary with a two-line delivery
order, closing its stop with one line accepted in full and one partly rejected, and observing the
order settle as partially delivered with a child order carrying the remainder and the rejected
goods back in the warehouse.

**Acceptance Scenarios**:

1. **Given** a stop on a departed itinerary, **When** it is closed with a delivered quantity equal
   to the sent quantity on every line, plus a receiver name, the identification shown, and a
   captured signature or photo, **Then** each delivery order at that stop moves to `DELIVERED`,
   the proof is archived against it, and the in-transit quantity is consumed.
2. **Given** a stop where some lines are accepted in full and others only in part or not at all,
   **When** it is closed with a reason code against every shortfall, **Then** the order moves to
   `PARTIALLY_DELIVERED`, the accepted quantity is consumed from transit, the returned quantity
   moves from transit back to the dispatch warehouse, and a child delivery order is created in
   `IN_PREPARATION` carrying the remainder and naming its parent.
3. **Given** a stop where nothing is accepted, **When** it is closed with a failure reason,
   **Then** the order moves to `FAILED`, the entire sent quantity moves from transit back to the
   dispatch warehouse, and no child order is created.
4. **Given** a stop closure that omits the receiver name, the signature or photo, or a reason code
   for a shortfall, **When** it is submitted, **Then** it is refused and the missing evidence is
   named.
5. **Given** a stop closure with a delivered quantity greater than the sent quantity on that line,
   **When** it is submitted, **Then** it is refused.
6. **Given** a trip with several stops, **When** one stop is closed as failed, **Then** the
   remaining stops can still be closed independently and the itinerary closes once every stop is
   resolved, recording its return timestamp.
7. **Given** a delivery order already in a terminal status, **When** its stop closure is
   resubmitted, **Then** the request is refused.

---

### User Story 6 - Hand over a counter pickup (Priority: P2)

As counter staff, I need to hand goods directly to a customer collecting them in store and capture
the same proof a driver would, so an in-store handover is as defensible as a delivered one.

**Why this priority**: A real and common path, but the delivery route is the majority of volume
and this branch reuses the proof mechanism built in User Story 5.

**Independent Test**: Can be fully tested by raising a delivery order whose ship-to address is a
facility address, approving it, marking it ready, confirming the pickup with proof, and observing
it settle as picked up with stock consumed from the store warehouse.

**Acceptance Scenarios**:

1. **Given** a sales order whose ship-to address matches a facility's address, **When** a delivery
   order is raised from it, **Then** its fulfilment type is set to counter pickup, and that type
   cannot be changed afterwards.
2. **Given** a counter-pickup order, **When** it is confirmed under a configuration requiring
   approval, **Then** it passes through `PENDING_APPROVAL` like any other order.
3. **Given** an approved counter-pickup order, **When** it is marked ready, **Then** it moves to
   `READY_FOR_PICKUP` and never appears in the pending-deliveries view or on any itinerary.
4. **Given** an order in `READY_FOR_PICKUP`, **When** the pickup is confirmed with receiver name,
   identification shown and a captured signature or photo, **Then** it moves to `PICKED_UP`, the
   proof is archived against it, and the stock is consumed directly from the store warehouse with
   no in-transit step.
5. **Given** a pickup confirmation missing any element of the proof, **When** it is submitted,
   **Then** it is refused.
6. **Given** a delivery-type order, **When** it is marked ready for pickup, **Then** the request
   is refused as an invalid transition.

---

### User Story 7 - Retry a failed delivery or cancel with a reason (Priority: P2)

As a dispatcher, I need to put a failed delivery back into the loading queue or retire it with a
stated reason, so returned goods are either sent out again or the order stops consuming attention.

**Why this priority**: Failure is the exception path, not the main flow, but leaving failed orders
unresolvable would strand both goods and paperwork.

**Independent Test**: Can be fully tested by failing a delivery, re-queuing it, observing it
available for loading again, and separately cancelling another failed order with a reason.

**Acceptance Scenarios**:

1. **Given** an order in `FAILED`, **When** it is re-queued, **Then** it moves to
   `IN_PREPARATION`, its lines' open quantity reflects the returned goods, and it reappears in the
   pending-deliveries view.
2. **Given** an order in `FAILED`, **When** it is cancelled with a reason, **Then** it moves to
   `CANCELLED` and the reason is recorded.
3. **Given** an order in any non-terminal status, **When** it is cancelled with a reason, **Then**
   it moves to `CANCELLED`, any commitment it holds on an open itinerary is released, and the
   reason is recorded.
4. **Given** an order in a terminal status, **When** cancellation is attempted, **Then** the
   request is refused.
5. **Given** a cancellation submitted without a reason or with a blank one, **When** it is
   submitted, **Then** it is refused.
6. **Given** an order in `IN_TRANSIT`, **When** cancellation is attempted, **Then** the request is
   refused — goods on the road are resolved at the stop, not by cancelling.

---

### User Story 8 - Read the audit trail (Priority: P3)

As a supervisor investigating a complaint, I need the full status history of a delivery order —
who moved it, when, and why — so "¿quién lo mandó?" has an answer.

**Why this priority**: Valuable for dispute resolution and compliance, but the flow works without
anyone reading it. The trail is written by the earlier stories regardless.

**Independent Test**: Can be fully tested by driving one order from draft to delivered and reading
back a complete, ordered history of its transitions.

**Acceptance Scenarios**:

1. **Given** a delivery order that has moved through several statuses, **When** its history is
   requested, **Then** every transition is returned in order, each naming the status left, the
   status entered, the employee responsible, the timestamp and any reason or note supplied.
2. **Given** a transition that required a reason — rejection, failure, cancellation — **When** the
   history is read, **Then** that reason appears against the transition.
3. **Given** an order that has only just been created, **When** its history is requested, **Then**
   it contains a single entry recording its creation into `DRAFT`.
4. **Given** any transition performed by any story in this feature, **When** the history is read,
   **Then** that transition is present — no status change goes unrecorded.

---

### Edge Cases

- A sales order whose lines are split across several delivery orders: each new order's lines are
  sized by what remains uncovered, and the last one to exhaust the order is followed by a refusal
  for any further attempt.
- A delivery order raised from a sales order whose delivery mode is pickup: refused, because that
  order is collected at the counter without a delivery document.
- A partial delivery whose child order is itself partially delivered: the child splits again,
  each generation naming its own parent, so the chain remains traceable.
- A stop containing several delivery orders where one is fully accepted and another entirely
  refused: each order settles into its own terminal status from the single stop closure.
- An itinerary departing with a line whose product is not stocked: no ledger movement is written
  for that line, and its quantities are still tracked.
- A delivery order whose lines dispatch from more than one warehouse: the in-transit and return
  movements are posted against each line's own warehouse, not the itinerary's.
- Concurrent departure of two itineraries holding commitments against the same line: both succeed,
  because the commitments were already serialised when they were taken.
- Re-queuing a failed order that was created as a partial-delivery child: it re-enters loading
  like any other order, and its parent remains terminal.
- The configured minimum lead time set to zero: confirmation is never blocked on the scheduled
  date.
- A counter-pickup order whose ship-to address is later edited to a non-facility address while in
  `DRAFT`: the fulfilment type is fixed at creation and does not change, so the order continues as
  a counter pickup.

## Requirements *(mandatory)*

### Functional Requirements

#### Delivery order lifecycle

- **FR-001**: System MUST give every delivery order an explicit status drawn from `DRAFT`,
  `PENDING_APPROVAL`, `APPROVED`, `READY_FOR_PICKUP`, `PICKED_UP`, `IN_PREPARATION`, `IN_TRANSIT`,
  `DELIVERED`, `PARTIALLY_DELIVERED`, `FAILED`, `CANCELLED`, replacing the legacy `confirmed`,
  `delivered` and `picked_up` booleans.
- **FR-002**: System MUST reject any status transition not permitted by the v2 state machine, and
  MUST say which transition was attempted.
- **FR-003**: System MUST treat `PICKED_UP`, `DELIVERED`, `PARTIALLY_DELIVERED` and `CANCELLED` as
  terminal, refusing every further transition out of them.
- **FR-004**: System MUST give every delivery order an immutable fulfilment type of either
  delivery or counter pickup, assigned at creation and never editable.
- **FR-005**: System MUST default the fulfilment type to counter pickup when the originating sales
  order's ship-to address matches the address of any facility, and to delivery otherwise.
- **FR-005a**: Users MUST be able to state the fulfilment type when raising a delivery order,
  overriding the default. **One sales order can split across both kinds** — the customer collects
  part of it at the counter and has the rest shipped — so the type is a property of the delivery
  order, not of the sale. Splitting is: raise one order, drop the lines that belong to the other
  kind while it is still a draft, then raise a second order for what is left, stating its type.
  Coverage keeps the two from overlapping.
- **FR-006**: System MUST allow header and line edits only while an order is in `DRAFT`.
- **FR-007**: System MUST allow cancellation with a mandatory non-blank reason from any
  non-terminal status except `IN_TRANSIT`, releasing any commitment the order holds.

#### Creation from a sales order

- **FR-008**: Users MUST be able to raise a delivery order from a sales order identified by its
  folio or id.
- **FR-009**: System MUST refuse creation unless the sales order is completed and not cancelled.
- **FR-010**: System MUST refuse creation when the configured rule requiring a paid or
  credit-terms sales order is on and the sales order is neither.
- **FR-011**: *(struck — see Divergences.)* A pickup delivery mode on the sales order MUST NOT
  block creation; it is precisely the case that produces a counter-pickup delivery order.
- **FR-012**: System MUST create one delivery line per sales-order line whose quantity is not yet
  fully covered by existing non-cancelled delivery orders, and MUST set the line's ordered quantity
  to the uncovered remainder. Every line is deliverable — see the Divergences note on the
  per-line delivery flag.
- **FR-013**: System MUST refuse creation when no deliverable quantity remains, reporting the
  order as already fully delivered.
- **FR-014**: System MUST copy the customer, ship-to address, contact and facility from the sales
  order, and MUST snapshot each line's product code and name.
- **FR-015**: System MUST record the originating sales-order line against each delivery line for
  traceability.
- **FR-016**: System MUST refuse a line quantity that would take the sales-order line's total
  covered quantity above its ordered quantity.

#### Confirmation and approval

- **FR-017**: System MUST assign the next folio for the order's facility at confirmation, and MUST
  serialise concurrent confirmations so no two orders in a facility share a folio.
- **FR-018**: System MUST refuse confirmation of an order with no lines.
- **FR-019**: System MUST refuse confirmation when the scheduled date is nearer than the
  configured minimum lead time, unless the caller is an administrator.
- **FR-020**: System MUST move a confirmed order to `PENDING_APPROVAL` when approval is configured
  as required. When it is not, the order MUST branch immediately by fulfilment type — a delivery
  order to `IN_PREPARATION`, a counter-pickup order to `APPROVED`.
- **FR-021**: System MUST expose a queue returning exactly the orders in `PENDING_APPROVAL`.
- **FR-022**: Users MUST be able to approve an order in `PENDING_APPROVAL`, moving it to
  `APPROVED`.
- **FR-023**: Users MUST be able to reject an order in `PENDING_APPROVAL` with a mandatory
  non-blank reason, returning it to `DRAFT` with the reason stored on the order and in the audit
  trail.
- **FR-024**: System MUST branch on fulfilment type at the moment of approval, writing exactly one
  transition: a delivery order moves to `IN_PREPARATION`, a counter-pickup order to `APPROVED`,
  from which it MUST then be markable as `READY_FOR_PICKUP`. `APPROVED` is therefore a resting
  state for counter pickups only, and is never written as a transient step for deliveries.

#### Quantities

- **FR-025**: System MUST track four quantities per delivery line: ordered, committed, delivered
  and returned. Sent quantity is recorded per trip on the itinerary line, not on the delivery-order
  line: no invariant references it there, and from departure to closure it would merely duplicate
  committed quantity.
- **FR-025a**: System MUST record the dispatch warehouse on each delivery line, snapshotted at
  creation from the originating sales-order line and falling back to the creating facility's
  warehouse, so that every inventory movement has a defined warehouse without traversing a
  nullable link.
- **FR-026**: System MUST compute a line's open quantity as ordered minus delivered minus returned
  minus committed. Returned quantity is subtracted because goods that came back are accounted for
  elsewhere — either by the child order a partial delivery creates, or, on a failure, by the
  requeue in FR-051a that moves them back into the open pool.
- **FR-027**: System MUST refuse any commitment that would take a line's committed quantity above
  its open quantity.
- **FR-028**: System MUST serialise concurrent commitments against the same delivery line so that
  the same open quantity can never be committed to two itineraries.
- **FR-029**: System MUST fix each itinerary line's sent quantity to its committed quantity at
  departure, after which sent quantity is immutable.
- **FR-029a**: System MUST retain a line's committed quantity through `IN_TRANSIT`, releasing it
  only when the stop closes. Releasing it at departure would return goods still on the truck to the
  open pool, allowing a second itinerary to commit them.

#### Pending deliveries view

- **FR-030**: System MUST expose a view of delivery lines awaiting loading, containing exactly the
  lines of orders in `IN_PREPARATION` whose facility is active.
- **FR-031**: System MUST group that view into buckets by scheduled date — before yesterday,
  yesterday, today, tomorrow, the day after tomorrow, and later — and MUST sort lines within a
  bucket by the originating sales order's priority, highest first.
- **FR-032**: System MUST report each line's open quantity in that view.

#### Itineraries and stops

- **FR-033**: Users MUST be able to open an itinerary carrying a date, an optional vehicle, an
  optional operator, a dispatch warehouse and notes, defaulting the date to today and the
  warehouse to the warehouse of the caller's point of sale.
- **FR-033a**: System MUST give every itinerary an explicit status of open, departed, closed or
  cancelled, stored rather than derived, so the lifecycle is filterable and has one source of
  truth.
- **FR-034**: System MUST refuse opening an itinerary for a vehicle that already has one open.
- **FR-035**: System MUST warn, without refusing, when an assigned operator's licence is expired
  or inactive.
- **FR-036**: Users MUST be able to add stops to an open itinerary, each carrying a sequence
  number within the trip and naming one or more delivery orders.
- **FR-037**: Users MUST be able to commit a quantity of a delivery line to a stop, defaulting to
  the line's full open quantity and reducible for a partial load.
- **FR-038**: Users MUST be able to commit every open line of a delivery order to a stop in one
  action.
- **FR-039**: System MUST refuse departure of an itinerary carrying no committed quantity.
- **FR-040**: System MUST record departure and return timestamps on the itinerary, and MUST refuse
  any change to its stops or commitments after departure.
- **FR-041**: System MUST allow cancellation of an itinerary only before departure, releasing
  every commitment it holds.
- **FR-042**: System MUST close an itinerary once all of its stops are resolved.

#### Proof of delivery and outcomes

- **FR-043**: System MUST require, at every terminal handover, a receiver name, the identification
  shown, a capture timestamp, the capturing employee, and a signature or photo.
- **FR-044**: System MUST accept an uploaded signature or photo image, store it outside any
  publicly served location, and return a reference by which it can be retrieved.
- **FR-044a**: System MUST serve a proof-of-delivery image only to an authenticated caller holding
  the privilege required to read the delivery order it belongs to, and MUST NOT expose it at an
  unauthenticated URL.
- **FR-044b**: System MUST keep a proof-of-delivery image available for as long as the delivery
  order it evidences, and MUST NOT let the removal of one order's proof affect another's.
- **FR-045**: System MUST require a delivered quantity per line at stop closure, and a reason code
  for every line whose delivered quantity is below its sent quantity.
- **FR-045a**: System MUST restrict shortfall reason codes to a fixed set — customer refused,
  nobody present, wrong address, damaged goods, other — and MUST use that same set for whole-stop
  failures, where every line carries the reason.
- **FR-046**: System MUST refuse a delivered quantity greater than the line's sent quantity.
- **FR-047**: System MUST settle a delivery order at `DELIVERED` when every line's delivered
  quantity equals its sent quantity.
- **FR-048**: System MUST settle a delivery order at `PARTIALLY_DELIVERED` when some but not all
  of the sent quantity is accepted, and MUST create a child delivery order in `IN_PREPARATION` carrying
  the unaccepted remainder and naming its parent.
- **FR-049**: System MUST settle a delivery order at `FAILED` when none of its sent quantity is
  accepted, recording the failure reason.
- **FR-050**: System MUST allow each stop of a trip to be closed independently, so a failed stop
  does not block the others.
- **FR-051**: Users MUST be able to move an order in `FAILED` either to `IN_PREPARATION` for
  another attempt or to `CANCELLED` with a reason.
- **FR-051a**: System MUST, when re-queuing a `FAILED` order, return each line's returned quantity
  to its open quantity, so the goods that came back can be loaded onto another trip. Without this
  transfer a re-queued order has no open quantity and can never be dispatched.
- **FR-052**: System MUST record the returned quantity per line for anything not accepted.

#### Counter pickup

- **FR-053**: System MUST keep counter-pickup orders out of the pending-deliveries view and off
  every itinerary.
- **FR-054**: Users MUST be able to confirm a pickup on an order in `READY_FOR_PICKUP`, moving it
  to `PICKED_UP` under the same proof requirements as a delivery.

#### Inventory

- **FR-055**: System MUST stop writing an outbound ledger entry when a sales order is confirmed,
  and MUST record a reservation of the line's quantity against its warehouse instead.
- **FR-055a**: System MUST compute available stock as on-hand minus outstanding reservations, and
  MUST refuse to confirm a sales order whose stocked lines exceed availability. Confirmation no
  longer reduces on-hand (FR-055), so a check against on-hand alone would let one physical unit
  satisfy an unlimited number of orders — every confirmation succeeding, and the shortfall
  surfacing only when the truck is loaded. FR-055 MUST NOT be implemented without this
  requirement.
- **FR-056**: System MUST release a sales order's reservations when it is cancelled, without
  writing a compensating ledger entry.
- **FR-057**: System MUST, at itinerary departure, write for each stocked line an outbound entry
  against its dispatch warehouse and a matching inbound entry against the in-transit location, and
  release the corresponding sales-order reservation.
- **FR-058**: System MUST, when a line is accepted, write an outbound entry against the in-transit
  location, consuming the goods.
- **FR-059**: System MUST, when a line is returned or fails, write an outbound entry against the
  in-transit location and an inbound entry against its dispatch warehouse.
- **FR-060**: System MUST, on a counter pickup, write a single outbound entry against the store
  warehouse and release the reservation, with no in-transit step.
- **FR-061**: System MUST post no inventory movement for lines whose product is not stocked.
- **FR-062**: System MUST leave every ledger entry in place, expressing reversals as further
  entries rather than edits or deletions.

#### Audit trail

- **FR-063**: System MUST record every delivery-order status transition with the status left, the
  status entered, the responsible employee, a timestamp and any reason or note.
- **FR-064**: Users MUST be able to read a delivery order's transition history in order.
- **FR-065**: System MUST record the creation of an order into `DRAFT` as the first entry of its
  history.

#### Access, listing and errors

- **FR-066**: System MUST require authentication on every endpoint in this feature, and MUST gate
  each one on the privilege appropriate to its surface: delivery orders, the approval queue, the
  pending-deliveries view, and itineraries.
- **FR-067**: Users MUST be able to list delivery orders filtered by status, customer, facility,
  fulfilment type, scheduled-date range and whether the caller created them, and to search by
  folio, customer name or the originating sales order, with paging and a total count.
- **FR-068**: Users MUST be able to list itineraries filtered by date range, vehicle, operator,
  dispatch warehouse and open-or-closed state, with paging and a total count.
- **FR-069**: System MUST answer a request naming a delivery order, itinerary, stop or line that
  does not exist with a not-found result, and an invalid transition or quantity violation with a
  conflict result naming the offending records.

#### Sales order coverage

- **FR-070**: System MUST report, as a derived figure on the sales order, how much of each
  deliverable line is ordered, covered by delivery orders, delivered and still outstanding,
  computed from the delivery orders rather than stored.
- **FR-071**: System MUST mark a sales order delivered once every one of its deliverable lines has
  been fully delivered, and MUST leave it unmarked while any line is outstanding.

### Key Entities

- **Delivery Order**: A picking and shipping document raised from a sales order for one customer
  and one ship-to address. Carries a facility, a folio, a scheduled date, a priority, a status, an
  immutable fulfilment type, an optional parent order when it is the remainder of a partial
  delivery, an optional rejection reason, and its archived proof of delivery once settled.
- **Delivery Order Line**: One product on a delivery order, snapshotting the product's code, name
  and dispatch warehouse, linked back to the sales-order line it serves, and carrying the ordered,
  committed, delivered and returned quantities from which open quantity is derived. Sent quantity
  lives on the itinerary line, where each trip records its own.
- **Delivery Order Event**: One recorded status transition — the order, the status left, the
  status entered, the employee, the timestamp and any reason. Append-only.
- **Delivery Itinerary**: One trip: a date, a vehicle, an operator, a dispatch warehouse,
  departure and return timestamps, the stops that make up the route, and a status of open,
  departed, closed or cancelled. Open until departed, then closed once every stop is resolved.
- **Itinerary Stop**: One place the truck stops, holding its sequence in the trip, the delivery
  orders dropped there, the arrival outcome, and the proof of delivery covering them.
- **Itinerary Line**: The commitment of a quantity of one delivery-order line to one stop, holding
  the committed and sent quantities, the delivered and returned quantities recorded at closure,
  the shortfall reason code, and a note.
- **Proof of Delivery**: The evidence of a handover — receiver name, identification shown, capture
  timestamp, capturing employee, and the stored signature or photo. Attached to a stop for a
  delivery and to the order itself for a counter pickup.
- **Stock Reservation**: A claim on a quantity of a product in a warehouse held by a confirmed
  sales order between confirmation and departure, reducing what is available to promise without
  reducing what is on hand.
- **In-Transit Location**: The stock location representing goods that have left a warehouse and
  not yet reached a customer, holding them between departure and outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A dispatcher can take a confirmed sales order all the way to a proven delivery —
  raise, confirm, approve, load, depart, close with proof — without any manual data correction.
- **SC-002**: Every one of the eleven statuses is reachable under at least one supported
  configuration — `PENDING_APPROVAL` requires approval to be configured, and `APPROVED` and
  `READY_FOR_PICKUP` require a counter-pickup order — and no transition outside the state machine
  can be performed through any endpoint.
- **SC-003**: For every delivery order in the system, ordered quantity equals delivered plus
  returned plus committed plus open, on every line, at every point in the flow.
- **SC-004**: The same open quantity is never committed to two itineraries: under two dispatchers
  loading the same line at once, exactly one commitment succeeds.
- **SC-005**: Warehouse on-hand for a product never counts goods that have physically left, and
  never omits goods still on the shelf: the sum of warehouse on-hand and in-transit quantity is
  unchanged by departure and falls only on acceptance.
- **SC-006**: No delivery order reaches `DELIVERED`, `PARTIALLY_DELIVERED` or `PICKED_UP` without
  a stored receiver name and a retrievable signature or photo.
- **SC-006a**: No proof-of-delivery image is retrievable without authentication and the privilege
  to read its delivery order.
- **SC-007**: Every partial delivery leaves exactly one child order carrying exactly the
  unaccepted quantity, and the parent-child chain is traceable to the originating sales order.
- **SC-008**: Every status change of every delivery order appears in its history with the
  responsible employee and a timestamp, and every rejection, failure and cancellation carries a
  non-blank reason.
- **SC-009**: No two delivery orders in the same facility hold the same folio.
- **SC-010**: A failed delivery can be returned to the loading queue and delivered on a second
  trip, with the goods accounted for in the warehouse in between.

## Assumptions

- The legacy `confirmed`, `delivered` and `picked_up` booleans on `delivery_order` are replaced by
  the new status column and dropped, following the precedent set by the unified-entity-status
  migration, which added `status` and dropped each legacy flag rather than keeping both in sync.
  The existing `completed` and `cancelled` booleans are likewise subsumed by the status.
- Delivery configuration currently held in the legacy `WebConfig` — whether approval is required,
  whether a paid or credit-terms sales order is required, and the minimum lead time in hours —
  becomes application settings alongside the existing product and sales defaults, not database
  rows.
- The reservation introduced by FR-055 is recorded in the existing requirement table that already
  carries a source, a reference, a warehouse, a product and a quantity, rather than in a new one.
- The in-transit location is a warehouse row like any other, flagged as virtual, so ledger entries
  against it need no new mechanism and its balance is readable by the existing on-hand query.
- The audit trail is a dedicated events table rather than the general incidence log: a transition
  has structured from-status and to-status fields that the incidence log's free-text content
  cannot express or query. The incidence log continues to serve unstructured annotations.
- A stop belongs to exactly one itinerary, and a delivery order appears at exactly one stop of any
  given trip.
- Print, ticket and PDF generation for delivery orders are presentation concerns handled by the
  client and are out of scope, consistent with how spec 011 treated the legacy print surface.
- New privilege targets are not invented: the logistics `SystemObject` values already enumerated —
  delivery orders, delivery order approval, for-deliver, pending deliveries and delivery
  itineraries — cover every surface this feature exposes.
- Sales orders continue to be the only origin of a delivery order; delivery orders are not raised
  standalone.
- Existing data settles rather than migrating into the new flow. Every one of the 26,763 legacy
  delivery orders becomes terminal: cancelled ones become `CANCELLED`, delivered ones `DELIVERED`,
  picked-up ones `PICKED_UP`, and the 17,515 completed-but-undelivered rows become `CANCELLED` as
  abandoned — the legacy application did not maintain its `delivered` flag reliably, so treating
  them as live work would bury the pending queue in years of stale paperwork. The 9,957 existing
  itinerary lines are recorded as fully sent and fully delivered.
- The reservation model applies only to sales orders confirmed after this feature ships. The
  178,045 already-confirmed, undelivered sales orders keep the outbound ledger entries their
  confirmation posted; no reservation is backfilled and no entry is reversed, because those goods
  were already decremented and reconstructing the history would rewrite on-hand system-wide.

## After merge

The feature merged as PR #120 on 2026-07-28. Three things changed afterwards; each is recorded
here because a spec that stops at the merge describes a system that no longer exists.

**The reservation lifecycle gained an expiry (#118, PR #121).** FR-055 reserves stock at
confirmation and FR-056 releases it on cancellation, but nothing released it for an order that was
simply abandoned — confirmed, never paid, never delivered. Stock stayed unavailable indefinitely,
visible on the shelf and missing from availability, and nothing made the leak visible because
on-hand remained correct. A scheduled sweep (`uv run python -m app.jobs.expire_orders`) now cancels
an order still neither paid nor delivered `UNPAID_ORDER_EXPIRY_DAYS` (default 2) after its date
**and still holding a reservation**, which releases the stock through FR-056's path.

> The reservation condition is not an optimisation. Reservations exist only for orders confirmed
> after this feature shipped — none were backfilled (see the R11 clarification). Without it the
> sweep matched **1,363 historical orders, none of which held a reservation**: a mass retirement of
> documents that released nothing.

**Automated actions have an actor (PR #122, #123).** Cancelling writes `sales_order.updater`, an
enforced foreign key, and attributing an automated cancellation to the salesperson would read in
the audit trail as their decision. `SYSTEM_EMPLOYEE_ID` is a **constant**, not a setting — a wrong
value is not a preference but a sweep that fails partway through — fixed at `-1` and seeded by
migration `010`, or created at API startup for a database that never ran it. Negative because
employee 0 cannot exist and a high id would push employee `AUTO_INCREMENT` past it.

**Releases became per-line (PR #120).** `release_reservations` gave back an order's whole claim,
but an order reserves one row per line, so departing one line released the reservations for all of
them — leaving the untouched lines sellable a second time. Departure and counter pickup now release
only the product, warehouse and quantity that moved, and goods that come back from a refusal or a
failed stop **re-reserve**, because the sale still owes them.

## Divergences from the source documents

- **A pickup sales order is not refused; it is exactly what produces a counter pickup.**
  `06-logistics.md` says `DeliveryMode` must not be `PickUp` because "pickup orders cannot generate
  a delivery order", and FR-011 originally said so. The data says the reverse: of the **15,527**
  sales orders carrying `DeliveryMode.PickUp`, **15,461 (99.6%) have a delivery order**. Those are
  the counter pickups — the 4,218 rows the legacy `picked_up` flag marked. Enforcing the rule would
  have refused almost every counter pickup in the system and left `FulfillmentType.COUNTER_PICKUP`
  unreachable through the only route that creates a delivery order. The requirement is struck; what
  distinguishes the two fulfilment types is the ship-to detection of FR-005 and the explicit
  override of FR-005a.

- **Every sales-order line is deliverable; the per-line `delivery` flag has been removed.**
  `06-logistics.md` defines the deliverable set as the lines flagged `delivery = true`, and this
  spec originally followed it. Measured against the database on 2026-07-27 that rule yields the
  empty set: the column was **0 on all 910,891 rows**, including the 54,741 lines the 26,763 legacy
  delivery orders were actually raised from. The same predicate appeared in three queries, so
  honouring it did not merely block delivery-order creation — it also silently disabled the
  sales-order `delivered` write-back (FR-071) and the derived coverage figures (FR-070). What the
  data shows the legacy system doing is taking the whole order: of 23,774 sales orders that
  produced a delivery order, **22,976 carried every line**, and the ~3% left out are spread evenly
  across stockable and non-stockable products. Coverage bounds a delivery order now, and migration
  `009` drops the column, because a write-only field that looks authoritative invites exactly the
  mistake this spec made. A sale that mixes shipped and collected lines is expressed by splitting
  it across two delivery orders of different fulfilment types (FR-005a), which records what
  actually happened to the goods rather than merely excluding a line from delivery.

- **Booleans replaced by a status.** `06-logistics.md` drives the flow from `IsConfirmed`,
  `IsDelivered` and `IsPickedUpInStore`. This feature implements the v2 state machine and drops
  those flags.
- **Approval rejection no longer strands the order.** The legacy flow set `IsConfirmed = false`
  and logged an incidence, leaving the order in an ambiguous state. v2 returns it to `DRAFT` with
  a stated reason so it can be corrected and resubmitted.
- **Rejection does not notify the creator.** v2 §2 calls for notifying the author on rejection.
  This repository has no notification channel of any kind — not even password recovery sends mail
  — so building one for a single event would be speculative scope. The reason is recorded on the
  order and in the audit trail, and the author finds rejected drafts through the "created by me"
  listing filter. Deferred, not dropped: a future notification feature can hang off the audit
  trail without changing anything specified here.
- **The "For Delivery" filter is a status, not a conjunction of flags.** The legacy view filtered
  on not-cancelled, completed, non-facility ship-to and enabled facility; v2 replaces all of that
  with membership of `IN_PREPARATION`.
- **Partial delivery splits rather than reopens.** The legacy flow set `IsDelivered` once all
  quantity was sent and had no representation for goods sent but refused. v2 splits the remainder
  into a child order.
- **Inventory moves in two steps and moves later.** The repository currently consumes stock when
  the *sales order* is confirmed. This feature changes that to a reservation and moves consumption
  to the delivery, via an in-transit location. **This modifies behaviour delivered by spec 011**:
  its acceptance scenario that confirmation "records an outbound inventory movement per stocked
  line", and its scenario that cancellation posts a compensating entry, are both superseded. The
  ledger remains append-only either way.
- **Itineraries gain stops.** The existing schema links an itinerary straight to a delivery-order
  line. v2 introduces the stop as the unit that closes, carrying sequence, outcome and proof.
- **Sent quantity is no longer editable after loading.** The legacy flow allowed `SentQuantity` to
  be adjusted until the itinerary was confirmed. v2 separates committed quantity, which is
  editable while the itinerary is open, from sent quantity, which is fixed at departure.
- **`DeliveryMode = PartialDeliveries` is not written back to the sales order.** The legacy flow
  mutated the sales order after the first delivery order was raised; per-line coverage is now
  derived from the delivery orders themselves (FR-070), so that flag would be redundant state. The
  sales order's `delivered` flag *is* maintained (FR-071) — it is the one fulfilment fact worth
  storing, because it is a whole-order terminal condition rather than a running total.
