# Feature Specification: One In-Transit Location per Facility

**Feature Branch**: `013-facility-transit-warehouses`

**Created**: 2026-07-28

**Status**: Draft

**Input**: User description: "one in-transit warehouse per facility, every facility must have it and don't be editable by nobody, cascade delete by facility"

## Background

Spec 012 introduced a single, system-wide in-transit location so that goods riding on a truck stop
counting as warehouse stock. Every warehouse belongs to exactly one facility, so that single
location had to be parented on an arbitrary facility — whichever one happened to exist first.

The result is that goods dispatched from **any** facility accumulate on **one** facility's books.
The organisational chart says stock belongs to the facility that holds it; the in-transit location
contradicts that for every facility but one. Inventory documents already derive their facility from
the warehouse they touch, and facility-filtered inventory reporting is planned, so the
contradiction is a defect waiting to surface as wrong numbers rather than a visible error.

This feature replaces the single shared location with one per facility, makes that location
something the system owns rather than something people maintain, and removes the operational
trap where a facility cannot be deleted because of a location nobody asked for.

## Clarifications

### Session 2026-07-28

- Q: How is a facility recovered if it was created outside this system and has no in-transit location? → A: Keep the refusal at dispatch; repair is a migration or direct database fix only. No runtime self-heal and no repair endpoint.
- Q: What does the system answer when someone addresses an in-transit location by identifier? → A: **Forbidden** — the location exists and may not be touched. Not "not found".
- Q: Should removing a facility leave an audit trace of the in-transit location that went with it? → A: Yes. A facility audit entry is recorded, which requires a new facility audit type.
- Q: Who is an audit entry attributed to when the acting user has no employee record? → A: The case does not arise — every authenticated user must have an employee record. Absence is a broken state, not a path to design for.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - In-transit stock stays on its own facility's books (Priority: P1)

An inventory manager at the Norte facility dispatches goods on a delivery run. While those goods
are on the road they are no longer counted in any Norte warehouse, but they are still Norte's
stock — and they must show as Norte's, not as some other facility's. The same is true, at the same
time, for a dispatch out of the Sur facility.

**Why this priority**: This is the defect being fixed. Without it, every facility except one is
under-counted while one facility is over-counted, and no amount of downstream reporting can repair
it because the attribution was lost at the moment of dispatch.

**Independent Test**: Dispatch stock from a warehouse in facility A and from a warehouse in
facility B on the same day. Inspect in-transit holdings per facility: A's goods appear under A only
and B's under B only. Complete both runs and confirm both facilities return to zero in-transit.

**Acceptance Scenarios**:

1. **Given** a delivery line dispatching from a warehouse owned by facility A, **When** the trip
   departs, **Then** the goods are recorded as in transit for facility A and no other facility's
   in-transit holdings change.
2. **Given** goods recorded in transit for facility A, **When** the customer accepts them, **Then**
   they are consumed from facility A's in-transit holdings and facility A's in-transit balance for
   that product returns to what it was before departure.
3. **Given** goods recorded in transit for facility A, **When** the customer refuses them or the
   stop fails, **Then** the goods leave facility A's in-transit holdings and return to the delivery
   line's own dispatch warehouse, exactly as they do today.
4. **Given** one trip carrying lines dispatched from facility A and lines dispatched from facility
   B, **When** the trip departs, **Then** each line is recorded against its own facility's
   in-transit holdings and the trip is not refused for spanning facilities.
5. **Given** a delivery order raised by facility B whose line dispatches from a warehouse owned by
   facility A, **When** the trip departs, **Then** the goods are recorded as in transit for
   facility A — the facility the goods physically left.
6. **Given** the changeover from the previous single shared location, **When** it completes,
   **Then** the total quantity in transit per product is unchanged and each portion of it now sits
   under the facility whose warehouse it left.

---

### User Story 2 - A new facility can dispatch on day one (Priority: P2)

An administrator opens a new facility. Nobody has to remember to create an in-transit location for
it, ask an engineer to record its identifier somewhere, or discover the omission when the first
delivery run departs.

**Why this priority**: Today the in-transit location exists only because a one-off database script
created it and a person then copied its identifier into deployment configuration. That step cannot
be repeated per facility by hand without eventually being forgotten, and forgetting it misfiles
stock instead of raising an error.

**Independent Test**: Create a facility through the ordinary administration flow, then immediately
dispatch a delivery from one of its warehouses. It succeeds with no setup step in between and no
configuration change.

**Acceptance Scenarios**:

1. **Given** an administrator creating a facility, **When** the facility is created, **Then** its
   in-transit location exists immediately and is ready to receive dispatched goods.
2. **Given** the in-transit location cannot be created for some reason, **When** facility creation
   is attempted, **Then** the facility is not created either — the system never leaves a facility
   without its in-transit location.
3. **Given** facilities that existed before this feature, **When** the changeover completes,
   **Then** each of them has its own in-transit location without anyone creating one by hand.
4. **Given** a facility that somehow has no in-transit location, **When** a dispatch from one of
   its warehouses is attempted, **Then** the dispatch is refused and names the facility, rather
   than recording the goods somewhere else.

---

### User Story 3 - The in-transit location cannot be tampered with (Priority: P3)

A warehouse administrator browsing the warehouse catalogue never sees the in-transit locations,
cannot pick one when choosing where to dispatch from or sell from, and cannot rename, re-parent,
deactivate or delete one — not even with full administrator rights.

**Why this priority**: The in-transit location is bookkeeping the system owns, not a place anyone
picks from. Renaming or re-parenting one would silently move another facility's stock; deleting one
would break the trips already in flight through it; choosing one on an order would file real stock
into a virtual place.

**Independent Test**: As an administrator, attempt to list, retrieve, rename, re-parent, deactivate
and delete an in-transit location, and attempt to select one as a dispatch or sales warehouse.
Every attempt is refused as forbidden, or the location is simply absent from the choices.

**Acceptance Scenarios**:

1. **Given** any user, including an administrator, **When** they list warehouses, **Then** no
   in-transit location appears.
2. **Given** any user, including an administrator, **When** they retrieve a warehouse by the
   identifier of an in-transit location, **Then** the request is **forbidden** and the location is
   not returned. The refusal says the location is system-managed, so a developer reading it is not
   left hunting for a row that does exist.
3. **Given** any user, including an administrator, **When** they attempt to change an in-transit
   location's name, code, owning facility, status or comment, **Then** the change is **forbidden**.
4. **Given** any user, including an administrator, **When** they attempt to delete an in-transit
   location directly, **Then** the deletion is **forbidden**.
5. **Given** a user choosing a warehouse on a sales order, a delivery line, or any stock lookup,
   **When** the choices are offered, **Then** no in-transit location is among them.
6. **Given** an identifier that names no warehouse at all, **When** it is retrieved, **Then** the
   answer is still "not found" — the two conditions stay distinguishable.

---

### User Story 4 - Removing a facility removes its in-transit location (Priority: P4)

An administrator closes a facility that was opened by mistake and never traded. The facility goes
away cleanly. The administrator is not blocked by a location the system created on their behalf and
will not let them delete.

**Why this priority**: Without this, guaranteeing an in-transit location per facility would make
every facility permanently undeletable — the guarantee would create the deadlock. It is last
because it only bites facilities that are being removed.

**Independent Test**: Create a facility, then delete it without doing anything else. It deletes.
Repeat with a facility that has dispatched goods in the past and confirm the deletion is refused
for that reason.

**Acceptance Scenarios**:

1. **Given** a facility with nothing referencing it but its own in-transit location, **When** the
   facility is deleted, **Then** the facility and its in-transit location are both removed, no
   manual clean-up was required, and an audit entry records who removed the facility and that its
   in-transit location went with it.
2. **Given** a facility whose in-transit location has carried goods in the past, **When** the
   facility is deleted, **Then** the deletion is refused and the inventory history is named as the
   blocker — the same answer any other warehouse with history gives.
3. **Given** a facility that is blocked from deletion for any other reason (its own warehouses,
   orders, points of sale), **When** the facility is deleted, **Then** the answer is unchanged from
   today.

---

### Edge Cases

- **A trip spans facilities.** One vehicle carries orders dispatched from two facilities. Each line
  is recorded against its own facility's in-transit holdings; the trip is not refused. Acceptance
  and refusal each settle against the facility the line left.
- **The order's facility differs from the dispatch warehouse's facility.** The warehouse wins: the
  goods left that warehouse, so its facility carries them in transit.
- **A trip is in flight during the changeover.** Goods already recorded in the previous shared
  location are re-attributed to the facility whose warehouse they left, so the trip settles
  correctly when it returns. Per-product totals in transit do not change.
- **A facility is deleted while one of its trips is in flight.** The in-transit location carries
  the goods, so the deletion is refused on the inventory-history blocker rather than stranding
  stock.
- **A facility is deactivated rather than deleted.** Its in-transit location is unaffected — trips
  already in flight settle normally.
- **An in-transit location is somehow missing.** Dispatch is refused naming the facility; the
  system never falls back to another facility's location or to an unspecified warehouse.
- **The automatic dispatch-warehouse fallback.** When a delivery line has no warehouse chosen and
  one is picked automatically for its facility, an in-transit location is never eligible.
- **Two facilities need distinguishable in-transit locations.** Every warehouse identifying code is
  unique system-wide today; the per-facility in-transit locations do not break that.

## Requirements *(mandatory)*

### Functional Requirements

**Per-facility attribution**

- **FR-001**: System MUST maintain exactly one in-transit location for every facility.
- **FR-002**: On departure, System MUST record the dispatched goods into the in-transit location of
  the facility that owns the delivery line's dispatch warehouse.
- **FR-003**: On acceptance, System MUST consume the goods from the same facility's in-transit
  location they were recorded into.
- **FR-004**: On refusal, failure or return, System MUST take the goods out of that facility's
  in-transit location and return them to the delivery line's own dispatch warehouse — unchanged
  from current behaviour.
- **FR-005**: System MUST allow a single trip to carry goods dispatched from more than one
  facility, recording each line against its own facility's in-transit location.
- **FR-006**: System MUST determine which in-transit location to use from the data itself, with no
  deployment configuration naming an in-transit location. The system-wide in-transit warehouse
  identifier setting introduced by spec 012 is retired.

**Guaranteed existence**

- **FR-007**: System MUST create a facility's in-transit location as part of creating the facility,
  as a single all-or-nothing operation — a facility MUST NOT exist without its in-transit location.
- **FR-008**: System MUST give every facility that existed before this feature its own in-transit
  location, without manual intervention per facility.
- **FR-009**: System MUST refuse a dispatch, naming the facility, when that facility has no
  in-transit location, rather than recording the movement anywhere else.
- **FR-009a**: System MUST NOT attempt to repair a facility missing its in-transit location at run
  time — no self-healing on dispatch and no repair operation. The refusal in FR-009 stands until the
  location is created by a migration or a direct database fix.

**System ownership**

- **FR-010**: System MUST refuse every attempt to modify an in-transit location — its name, code,
  owning facility, status or comment — from every caller, including administrators, answering
  **forbidden**.
- **FR-011**: System MUST refuse every attempt to delete an in-transit location directly, answering
  **forbidden**.
- **FR-012**: System MUST omit in-transit locations from warehouse listings and from every
  warehouse-selection surface, including dispatch-warehouse choice, sales-order warehouse choice,
  automatic dispatch-warehouse fallback, and product stock lookups.
- **FR-013**: System MUST NOT return an in-transit location when a warehouse is retrieved by
  identifier, answering **forbidden** instead.
- **FR-013a**: System MUST keep "forbidden" and "not found" distinguishable — an identifier naming
  no warehouse at all still answers not found. The refusal MUST state that the location is
  system-managed, rather than being an unexplained denial.

**Facility removal**

- **FR-014**: When a facility is deleted, System MUST remove that facility's in-transit location as
  part of the same operation, so that location alone never blocks the deletion.
- **FR-015**: System MUST still refuse a facility deletion when the facility's in-transit location
  carries inventory history, naming that history as the blocker — the same treatment any other
  warehouse receives.
- **FR-015a**: System MUST record an audit entry when a facility is deleted, identifying the acting
  user, the facility, and the in-transit location removed with it. The entry MUST be written in the
  same operation as the deletion, so a deletion cannot succeed without leaving a trace.

**Changeover**

- **FR-016**: System MUST preserve the previous shared in-transit location as the in-transit
  location of the facility it already belongs to, rather than discarding it.
- **FR-017**: System MUST move every balance held in the previous shared location that originated
  from another facility's warehouse into that facility's in-transit location, leaving the total
  quantity in transit per product unchanged.
- **FR-018**: System MUST keep every warehouse identifying code unique after the changeover.

### Key Entities

- **Facility**: An operating site. Owns warehouses, and now owns exactly one in-transit location.
  Its deletion carries that location with it.
- **In-transit location**: A holding place for goods that have left a warehouse and not yet reached
  a customer. Belongs to exactly one facility. Created and destroyed by the system, never by a
  user; never selectable, never editable.
- **Warehouse**: A real place stock sits, belonging to one facility. Unchanged, except that
  in-transit locations are no longer confusable with one.
- **Delivery line**: Carries the dispatch warehouse the goods leave from — the input that decides
  which facility's in-transit location receives them.
- **Trip (itinerary)**: A vehicle run carrying delivery lines, possibly from several facilities.
- **Stock movement**: The record of goods leaving one location and arriving at another; the thing
  whose facility attribution this feature corrects.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every facility has exactly one in-transit location at every point in time — count of
  facilities without one, or with more than one, is zero, including immediately after a facility is
  created.
- **SC-002**: Goods dispatched from a facility appear in that facility's in-transit holdings and in
  no other facility's — zero cross-facility attribution across a dispatch involving at least two
  facilities.
- **SC-003**: A facility's total stock, counting what it holds in warehouses plus what it has in
  transit, accounts for 100% of the goods it has dispatched and not yet settled — no facility is
  under-counted and none is over-counted.
- **SC-004**: Zero attempts to view, select, rename, re-parent, deactivate or delete an in-transit
  location succeed, across every role including administrator — and every refusal is
  distinguishable from "no such warehouse", so a developer can tell the two apart without reading
  the database.
- **SC-004a**: 100% of facility deletions leave an audit entry naming the acting user and the
  in-transit location removed — no facility is ever deleted without a trace.
- **SC-005**: Opening a new facility requires zero manual setup steps and zero deployment
  configuration changes before its first dispatch, down from one of each today.
- **SC-006**: A facility that has never traded can be deleted without the operator removing
  anything the system created on its behalf.
- **SC-007**: Immediately after the changeover, the total quantity in transit per product equals
  the total before it — zero discrepancy — and every in-flight trip settles to the same warehouse
  balances it would have settled to beforehand.

## Assumptions

- **"Not editable by nobody" includes administrators.** No role is exempt. These rows are system
  bookkeeping; if one ever has to change, it changes through a migration, not through the API.
- **The dispatch warehouse's facility decides, not the delivery order's facility.** Where the goods
  physically were is what inventory attribution means; the two coincide in normal operation, and
  when they diverge the warehouse is the truthful answer.
- **Returns continue to go to the delivery line's own dispatch warehouse**, exactly as spec 012
  established. This feature changes where goods sit *while* in transit, nothing about where they
  come back to.
- **Every facility gets an in-transit location regardless of status.** The guarantee is per facility
  record, not per active facility, so deactivating a facility cannot strand goods in flight.
- **Facility deletion is otherwise unchanged.** Only the system-created in-transit location stops
  being a blocker; warehouses, orders, points of sale and every other reference block exactly as
  they do today.
- **In-flight balances at changeover are traceable.** Each in-transit balance can be tied back
  through its trip to the warehouse it left, and therefore to a facility. If a balance cannot be
  attributed, the changeover stops rather than guessing.
- **Every authenticated user has an employee record.** Audit attribution depends on it, and the
  clarified answer is that a user without one is a broken state rather than a case to design a
  fallback for. **Worth knowing**: the database does not enforce this — `user.employee` is nullable
  — so this is a policy the deployment upholds, not a guarantee the schema provides. Facility
  deletion therefore refuses rather than inventing an attribution if it ever encounters one.
  *(Updated by #127: the policy is now a schema guarantee — `user.employee` is `NOT NULL` from
  migration 012 — and the refusal is removed, because the state it guarded cannot occur.)*
- **A facility missing its in-transit location is repaired out of band.** By migration or direct
  database fix; the system will not create one on demand (FR-009a). This keeps the invariant's only
  writer in one place, at the cost of a facility created outside this system staying undispatchable
  until someone notices.
- **Facility-level inventory reporting is out of scope.** This feature makes the underlying
  attribution correct so those reports can be built on it later; it does not build them.
- **Delivery flow semantics are unchanged**: commitments, sent quantities, reservations, the state
  machine and proof of delivery all behave exactly as spec 012 defines them.

### Dependencies

- Builds on spec 012's delivery flow and the single in-transit location it introduced; the
  changeover in FR-016 through FR-018 operates on what that migration created.
- Affects any deployment currently running spec 012, which has the system-wide in-transit warehouse
  identifier set in its environment. That setting is retired by FR-006.

### Out of Scope

- Facility-filtered or facility-summarised inventory reports.
- Cross-facility transfer documents, which remain unsupported.
- Any change to how a delivery line's dispatch warehouse is chosen, beyond excluding in-transit
  locations from the automatic fallback.
- Making other warehouse types system-owned or non-editable.
