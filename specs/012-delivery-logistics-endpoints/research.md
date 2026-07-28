# Phase 0 Research: Delivery & Logistics Endpoints

Twelve decisions, three of them revised after the post-tasks review (R2, R3, R9). Each records what was chosen, why, and what was rejected. Findings marked
**[data]** were checked against the `mbe_demo` database on 2026-07-26.

> **Audit interrupted.** The database went offline part-way through this audit (the
> `/tmp/mysql-xolotl.sock` socket disappeared). The figures below are from queries that completed;
> the three checks that did not are listed in [R12](#r12--pre-migration-data-audit) and must run
> before the migration is authorised.

---

## R1 — Lifecycle as a status column, not five booleans

**Decision.** Add `delivery_order.status SMALLINT NOT NULL` backed by a `DeliveryOrderStatus`
IntEnum, and drop `completed`, `cancelled`, `confirmed`, `delivered`, `picked_up`. Add
`fulfillment_type SMALLINT NOT NULL` backed by a `FulfillmentType` IntEnum.

**Rationale.** Migration 005 set the precedent exactly: it added `status` and dropped each legacy
flag rather than keeping both in sync, across eleven tables. Five independent booleans admit 32
combinations for 11 legal states, and **[data]** the production rows prove that gap is not
theoretical — 14 distinct combinations exist across 26,763 rows, including one order that is
simultaneously `cancelled=1, completed=1, picked_up=1`. A single column makes the illegal states
unrepresentable.

**Alternatives rejected.**

- *Keep the booleans and derive status* — the derivation is ambiguous for the 12,339 rows at
  `completed=1, confirmed=0, delivered=0, picked_up=0`, which could mean "awaiting approval" or
  "approval not configured, ready to load".
- *Keep both and dual-write* — two sources of truth, and the constitution's Simplicity principle
  rejects redundant state. Migration 005 already declined this.
- *String status column* — every other status in this codebase is an `IntEnum` over a small int.

---

## R2 — The double-assignment guard

**Decision.** `committed_quantity` lives on `delivery_order_detail`. Every commitment runs inside a
transaction that first takes `SELECT ... FOR UPDATE` on the `delivery_order_detail` row, re-reads
`open_quantity = ordered − delivered − returned − committed`, and refuses when the request exceeds
it.

**Rationale.** This is v2 design decision D4, and it matches the folio-assignment pattern already
proven in `app/services/documents.py:assign_folio`, which locks the facility row to serialise
concurrent numbering. Locking the line — not the order, not the itinerary — is the narrowest lock
that still makes two dispatchers queue rather than race, because the line is the resource being
consumed.

**Alternatives rejected.**

- *Optimistic version column with retry* — adds a column and a retry loop to solve a problem a
  row lock solves outright; SQLAlchemy's `with_for_update()` is already used in this codebase.
- *Unique constraint* — cannot express "sum of commitments ≤ ordered"; a check that spans rows is
  not a constraint MariaDB can enforce.
- *Application-level mutex* — does not survive multiple workers.

**Note.** FR-028 is about *taking* a commitment. Departure (FR-057) needs no additional lock: the
quantities were already serialised when committed, which is why the spec's edge case says two
itineraries can depart concurrently.

---

## R3 — The in-transit location

**Decision.** One virtual warehouse row, seeded by the migration, identified by a new setting
`in_transit_warehouse_id`. Ledger entries against it use the existing `post_movement` unchanged.
Warehouse listing and lookup endpoints exclude that id.

**Rationale.** `stock_ledger.on_hand` already sums by `(product, warehouse)`, so a warehouse row
gives the in-transit balance for free with no new mechanism — this is the "reuse over rebuild"
outcome. A single global row is sufficient because returns go back to the *line's own* dispatch
warehouse, which is snapshotted on the delivery-order line (FR-025a), not inferred from where the
goods sat in transit.

**Alternatives rejected.**

- *A `virtual` boolean column on `warehouse`* — the spec's wording ("flagged as virtual") suggests
  it, but the only thing the flag would drive is exclusion from pickers, and the config setting
  already identifies the row. A column change for one comparison is not warranted.
- *One transit warehouse per facility* — multiplies rows to answer a question nobody asked; the
  ledger's `reference` column already ties an entry to its itinerary.
- *A nullable `in_transit` flag on the ledger row* — would fork `on_hand` into two code paths.

---

## R4 — Reservations reuse `lot_serial_rqmt`

**Decision.** A confirmed sales order writes one `lot_serial_rqmt` row per stocked line —
`source = TransactionType.SALES_ORDER`, `reference = sales_order_id`, plus warehouse, product and
quantity. Releasing a reservation deletes its rows.

**Rationale.** The table exists, is mapped in `app/models/inventory.py`, and its five columns are
precisely a reservation: source, reference, warehouse, product, quantity. The name is literally
"requirement". Inventing a `stock_reservation` table alongside it would violate Reuse Over Rebuild
for no gain.

**Alternatives rejected.**

- *A new `stock_reservation` table* — duplicates an existing shape.
- *Deriving reservations from sales orders on the fly* — every availability check would join
  `sales_order`, `sales_order_detail` and `delivery_order_detail` to work out what is still
  reserved; a materialised row is read directly.
- *Marking the ledger* — reservations are not movements; putting them in an append-only movement
  log would corrupt `on_hand`.

**Open.** Whether the legacy application also writes this table — see [R12](#r12--pre-migration-data-audit).
If it does, reservations must be namespaced by `source` rather than assumed to be ours alone. The
design already keys on `source`, so this changes the migration's cleanup step, not the model.

---

## R5 — Available-to-promise replaces on-hand at sales-order confirmation

**Decision.** `sales_order_service.confirm_order` stops calling `post_movement` and instead
(a) computes availability as `on_hand(product, warehouse) − reserved(product, warehouse)`,
(b) refuses on shortfall exactly as today, (c) writes `lot_serial_rqmt` rows. `cancel_order` deletes
those rows instead of posting compensating entries.

**Rationale.** This is the load-bearing consequence of the clarified inventory decision, and it is
the one place this feature reaches into spec 011's code. Without subtracting reservations, the
stock check would pass repeatedly for the same physical unit: on-hand no longer drops at
confirmation, so ten orders could each confirm against one item.

**Alternatives rejected.**

- *Leave the stock check on raw on-hand* — silently oversells. This is the failure mode that makes
  the whole change unsafe if missed, which is why it is called out as its own decision.
- *Move the stock check to departure only* — a salesperson would learn at the truck that the order
  cannot be filled, which is later than the customer is standing there.

**Blast radius.** `app/services/sales_order_service.py` (`confirm_order`, `cancel_order`,
`stock_shortfalls`, `attach_derived`), `tests/unit/test_sales_order_service.py`,
`tests/api/test_sales_orders.py`. Spec 011's US1 scenarios 4 and 6 are superseded — recorded in
this feature's spec under Divergences.

---

## R6 — Proof-of-delivery image storage

**Decision.** A new private directory (setting `pod_dir`, default `pod`), **not** under the
`/images` static mount. Filenames are a UUID, not a content digest. Retrieval is an authenticated
route that streams the file after the privilege check. `image_service` is refactored minimally:
the PNG-normalising step is extracted so both callers share it, and the filename strategy becomes
the caller's choice.

**Rationale.** Two distinct hazards, one decision each. *Access*: `app/main.py:45` mounts
`images/` with `StaticFiles` and no authentication — correct for product photos, wrong for a
customer's signature (FR-044a, Constitution VII). *Aliasing*: `image_service` names files
`sha256(content).png` and skips the write when the path exists, so two identical captures become
one file; deleting one order's proof would silently remove another's evidence. A UUID makes each
capture its own file (FR-044b).

**Alternatives rejected.**

- *Reuse the static mount* — rejected in clarification; obscurity is not access control.
- *Keep content-addressing in a private directory* — solves access but not aliasing. Signatures
  are low-entropy images; a hurried "just a squiggle" capture colliding across two customers is
  plausible, and that is the exact case where evidence matters.
- *Store bytes in a BLOB column* — bloats every delivery-order query and abandons the existing
  image pipeline.

---

## R7 — Transitions are written explicitly, not by an ORM event listener

**Decision.** `delivery_order_event` rows are written by an explicit helper called from each
transition in the service layer. **This overrides v2 §6**, which specifies a SQLAlchemy event
listener.

**Rationale.** A listener cannot see what the audit trail most needs. `after_update` knows the old
and new status but not *who* acted or *why* — the employee and the reason live in the request
scope, and reaching them from a mapper event needs ambient/thread-local state, which is unsafe
under async. Worse, a listener fires on flush, so a transition rolled back later would still have
been recorded, or not, depending on flush timing. Explicit calls also let the trail record the
creation event (FR-065), which no update listener sees.

**Alternatives rejected.**

- *`after_update` mapper listener as written in v2* — see above. The doc's own framing ("costs
  almost nothing") is about the table, and the table is kept; only the write mechanism differs.
- *Reuse `incidences.record`* — considered and rejected in the spec's Assumptions: `incidence` has
  no from/to status columns, so transitions could not be queried. `incidence` remains for
  unstructured annotations.

**Consequence.** "No status change goes unrecorded" (SC-008, FR-063) becomes a discipline enforced
by routing *every* transition through one `transition()` helper that writes the row and moves the
status together. A test asserts each service transition produces exactly one event.

---

## R8 — The status column subsumes the editability guard

**Decision.** Delivery orders do not use `documents.assert_editable`. A new
`delivery_order_service.assert_editable` refuses anything not in `DRAFT`.
`documents.assign_folio` **is** reused unchanged.

**Rationale.** `assert_editable` reads `.cancelled` and `.completed` via `getattr`, both of which
this migration drops from `delivery_order`. Its `getattr(document, 'cancelled', False)` default
means it would silently pass everything rather than fail loudly — a guard that always says yes.
`assign_folio` is unaffected: it takes the model class and reads `.serial` and `.facility`, which
both survive.

**Alternatives rejected.**

- *Teach `assert_editable` about status* — it is called by three sales services whose documents
  have no status column; adding a branch there couples two lifecycles that are deliberately
  different.
- *Keep `completed`/`cancelled` on `delivery_order` just to satisfy the shared guard* — keeping
  redundant columns to avoid writing four lines is the wrong trade.

---

## R9 — One itinerary open per vehicle

**Decision.** Enforced in the service layer by a query under a lock on the `vehicle` row, mirroring
`assign_folio`'s facility lock. No partial unique index.

**Rationale.** MariaDB has no filtered/partial unique index, so "at most one row where
`status = OPEN` per vehicle" is not expressible as a constraint. The row lock makes concurrent
opens queue.

**Revised after review.** The itinerary now carries a stored `status` (FR-033a) rather than the
derived `cancelled`/`completed`/`departure_time` combination this decision originally assumed. The
lock is unchanged; the predicate becomes a single indexed column, and the `(status, date)` index
serves both this check and the FR-068 filter.

**This makes the migration load-bearing.** Legacy itineraries that map to `OPEN` would each
permanently block their vehicle from receiving another itinerary. R11 therefore settles them to
`CLOSED`.

**Alternatives rejected.**

- *Generated column plus unique index* (`vehicle` when open, `NULL` otherwise) — expressible, but
  it makes a subtle invariant depend on a generated-column expression that is easy to break and
  hard to read. Noted as a hardening option if the lock ever proves insufficient.
- *No enforcement* — FR-034 requires it.

---

## R10 — Folio uniqueness for delivery orders

**Decision.** Extend the migration-007 pattern to `delivery_order`: normalise `serial = 0` to
`NULL`, renumber genuine duplicates keeping the earliest, then add `UNIQUE (facility, serial)`.

**Rationale.** SC-009 demands it, and `documents.assign_folio` is `MAX(serial) + 1` under a lock —
migration 007's rationale applies verbatim: a lock is only as good as every future code path
remembering it, and the index is the backstop. Migration 007 covered `sales_order`, `sales_quote`
and `customer_refund` but **not** `delivery_order`.

**Blocked on data.** The duplicate and placeholder counts are the audit queries that did not
complete — see [R12](#r12--pre-migration-data-audit). Migration 007 found 4,240 placeholder rows
and 34 genuine duplicates across three tables; a similar order of magnitude should be expected
here, and the migration must state the real numbers before it is run, as 007 did.

**Note.** `delivery_order.serial` is currently `NOT NULL`. Normalising placeholders to `NULL`
requires relaxing it — the same shape as 007, which relied on MySQL unique indexes permitting
repeated `NULL`s.

---

## R11 — Legacy rows settle; the new model starts clean

**Decision.** Per the clarified answer, the migration maps every existing delivery order to a
terminal status and backfills no reservations.

| Legacy state **[data]** | Rows | New status |
|---|---|---|
| `cancelled = 1` (any combination) | 1,059 | `CANCELLED` |
| `delivered = 1` | 3,769 | `DELIVERED` |
| `picked_up = 1`, not cancelled/delivered | 4,160 | `PICKED_UP` |
| everything else | 17,775 | `CANCELLED` (abandoned) |

Existing `deliveries_itinerary_detail` rows (9,957) get `sent = delivered = quantity`,
`returned = 0`. Existing itineraries get a synthetic single stop so the new foreign key is
satisfiable. The 178,045 confirmed, undelivered sales orders keep their posted outbound entries and
receive no reservation.

**Rationale.** The alternative — treating 17,775 completed-but-undelivered rows as live work —
would open the pending-deliveries queue with years of stale paperwork and make User Story 3
untestable in practice. **[data]** The legacy application demonstrably did not maintain `delivered`:
only 3,769 of 26,763 orders carry it, against 4,160 marked picked up.

**Consequence, stated plainly.** Any delivery genuinely in flight at cutover is cancelled and must
be re-raised from its sales order. The sales orders remain, their coverage is derived from delivery
orders (FR-070), and cancelled delivery orders do not count as coverage — so re-raising produces
the right lines. This is the accepted cost of a clean start and should be scheduled for a quiet
period.

---

## R12 — Pre-migration data audit  ✅ COMPLETE (2026-07-27)

All four checks ran against `mbe_demo`. **Three of them overturned a design decision** — this is
what the audit was for.

| Check | Result | Consequence |
|---|---|---|
| `delivery_order.serial = 0` placeholders | **1,219** | Normalise to `NULL`, as migration 007 did |
| `delivery_order.serial IS NULL` | **0** | Column is currently `NOT NULL`; must be relaxed |
| Duplicate `(facility, serial)` groups | **0** (0 rows affected) | **No renumbering step needed** — simpler than 007 |
| `lot_serial_rqmt` total rows | **3,214** | Table is live, not empty — see A2 |
| `lot_serial_rqmt` by source | 1:**2,609** · 4:221 · 5:189 · 2:150 · 3:28 · 6:17 | Legacy writes `source = 1` (SalesOrder) — collides with R4 |
| `lot_serial_tracking` rows at `source = 5` | **38,411** | Value 5 is taken — see A1 |
| `lot_serial_tracking` sources in use | 1, 2, 3, 4, 5, 6, 8 | |
| `delivery_order.ship_to IS NULL` | **6,693** (807 picked up, 5,886 not) | Fulfilment-type backfill needs a rule — see A3 |

### A1 — `TransactionType.DELIVERY_ORDER = 5` is wrong; **no value in 1–9 is free**

`docs/constants.md:330-340` enumerates the legacy `TransactionType` space in full:

| 1 SalesOrder | 2 CustomerRefund | 3 InventoryIssue | 4 InventoryReceipt | 5 **InventoryTransfer** |
|---|---|---|---|---|
| 6 PurchaseOrder | 7 SupplierReturn | 8 InventoryAdjustment | 9 ProductConversion | |

`app/enums.py` models only 1–4, which made 5 *look* free. It is not: 38,411 ledger rows are
inventory transfers. Claiming 5 would conflate delivery movements with transfers in every kardex
and stock report.

**Resolution: `TransactionType.DELIVERY_ORDER = 10`** — the first value beyond the documented
legacy range. The four unmodelled legacy values (5–9) should be added to the enum at the same time
so this trap cannot be re-entered.

### A2 — `lot_serial_rqmt` is live, and the legacy app owns `source = 1`

R4 planned to key reservations as `(source = SALES_ORDER(1), reference = sales_order_id)`. That key
is already occupied: **2,609 rows at source 1**, of which **2,559 point at completed, uncancelled
sales orders**. Sampled quantities are **negative** (`-1.0000`, `-10.0000`), so the legacy sign
convention also differs from the one `reserve()` would use.

Two concrete failures if R4 ships as written:

- `reserved(product, warehouse)` would count 2,559 legacy rows as our reservations, understating
  availability and refusing confirmations that should succeed (FR-055a).
- `release_reservations(sales_order_id)` would **delete legacy rows** belonging to the other
  application.

**Resolution: namespace our reservations under their own source value** rather than reusing
`SALES_ORDER`. R4's reuse of the table stands; only the key changes. The new-table alternative R4
rejected stays rejected.

**Why namespacing rather than purging the stale rows.** Writing to this table stopped around
2025-01-02 (newest source-1 row ties to a sales order created then, against sales orders still
being created in July 2026), so the 2,609 rows are dead data from a retired writer and deleting
them would be defensible. Namespacing is still preferred: it destroys nothing, needs no migration
step, and cannot be got wrong at 3 a.m. `reserved()` filters on our value alone, so the stale rows
are simply invisible to it.

### A3 — 6,693 delivery orders have no ship-to address

Fulfilment type is `NOT NULL` and is detected by matching `ship_to` against facility addresses; 25%
of rows have no `ship_to` to match. Since every legacy row settles to a terminal status, the
backfill rule is low-risk but must be stated: **`picked_up = 1` → `COUNTER_PICKUP` (807 rows),
otherwise `DELIVERY` (5,886 rows)**. For new orders FR-005 already covers it — a null ship-to
matches no facility, so the type is `DELIVERY`.

---

## Decisions deliberately deferred

- **Performance targets.** The spec sets none and no prior feature did either. The operative
  constraint is avoiding N+1 queries on the pending-deliveries and itinerary list endpoints;
  `app/services/fk_expansion.py` already exists for this.
- **Notification on rejection.** Resolved in clarification as out of scope; a future feature can
  read `delivery_order_event` without changing anything here.
- **Print and ticket rendering.** Out of scope per the spec.
