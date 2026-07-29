---

description: "Task list for Delivery & Logistics Endpoints"
---

# Tasks: Delivery & Logistics Endpoints

**Input**: Design documents from `/specs/012-delivery-logistics-endpoints/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/README.md)

**Tests**: **NOT OPTIONAL.** Constitution v1.2.0 requires tests for every change — `tests/api/` for
endpoints (happy path, 401, 403, 404, resource-specific 409/422) **and** `tests/unit/` for every
service or helper carrying branching logic, state transitions or arithmetic. Tests are written
first, confirmed failing, then implemented, and are committed with the code they cover.

> The upstream Spec Kit boilerplate in `tasks-template.md` says tests are optional. It is
> contradicted by this project's constitution and is overridden here.

**Organization**: Grouped by user story so each lands as an independently testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: Which user story the task serves (US1–US8)
- Exact file paths are given in every task

## Path Conventions

Existing single-project layout, unchanged: `app/{enums,core,models,schemas,services,api/v1/endpoints}`
and `tests/{api,unit}` at repository root. Migrations are raw SQL in `migrations/`.

---

> **Verified against a running application on 2026-07-27** (`mbe_dev`, every created row removed
> afterwards): scenarios 1, 2, 3, 4, 5, 7 and 8. The concurrency race was run three times against
> live MariaDB and yielded exactly one winner each time; the stock table held at every point.
>
> **T089 (scenario 6, counter pickup) verified 2026-07-28**, once FR-005a made the fulfilment type
> selectable — it had been blocked because no sales order in this dataset ships to a facility
> address. Confirmed: raise as counter pickup, confirm resting at `APPROVED`, absent from the
> pending view, refused from an itinerary (409), pickup without an image refused (422), handover
> with proof reaching `PICKED_UP`, stock consumed from the store warehouse with **zero in-transit
> ledger rows**, and `ready-for-pickup` on a delivery-type order refused (409).
>
> **All eight scenarios have now run end to end. Two findings came out of them — see below T105.**

## ⚠️ Read before starting

Two hazards govern the ordering below.

1. **Phase 2 changes shipped sales-order behaviour** (T016–T024). Removing the outbound ledger
   entry at sales-order confirmation without simultaneously subtracting reservations from the stock
   check fails *silently* — confirmations keep succeeding while one physical unit satisfies
   unlimited orders. **T020 and T021 are a pair; neither ships alone.**
2. **The migration is destructive and its audit is incomplete.** Phase 1 finishes the three
   outstanding checks from [research R12](./research.md#r12--pre-migration-data-audit). `008` drops
   seven columns and settles all 26,763 existing delivery orders into terminal statuses; anything
   genuinely in flight at cutover must be re-raised from its sales order.

---

## Phase 1: Setup & Data Audit

**Purpose**: Establish a known-good baseline and finish the audit the migration depends on. The
database went offline during planning with three checks outstanding.

- [X] T001 Confirm baseline is green on this branch: `uv run ruff check app/ migrations/ tests/` and `uv run pytest tests/ -q`
- [X] T002 [P] Audit delivery-order folios: count `delivery_order` rows with `serial = 0` and count `(facility, serial)` groups having `COUNT(*) > 1`, plus the total rows inside those groups — gates the renumbering step of the migration (research R10)
- [X] T003 [P] Audit `lot_serial_rqmt` occupancy: total row count grouped by `source`, to establish whether the legacy application writes reservations this feature would otherwise clobber (research R4)
- [X] T004 [P] Audit `delivery_order` rows with `ship_to IS NULL` — determines whether fulfilment-type detection needs a fallback for rows the migration must classify
- [X] T005 [P] Audit `lot_serial_tracking` for any row using `source = 5`, confirming the value is free before `TransactionType.DELIVERY_ORDER` claims it (data-model.md)
- [X] T006 Record all four audit results verbatim in the R12 table of `specs/012-delivery-logistics-endpoints/research.md`, replacing the "outstanding" note. Do not proceed to T012 until this task is complete

**Checkpoint**: Every figure the migration relies on is measured, not assumed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, enums, settings, the inventory rework, and the transition chokepoint. **No
user story can start until this phase completes.**

### Enums, settings and models

- [X] T007 [P] Add `DeliveryOrderStatus`, `FulfillmentType`, `ItineraryStatus`, `StopOutcome` and `ShortfallReason` int enums to `app/enums.py`, plus `TransactionType.DELIVERY_ORDER`, with values exactly as given in `data-model.md` (FR-001, FR-004, FR-033a, FR-045a)
- [X] T008 [P] Add `delivery_order_approval_required`, `delivery_order_requires_paid_or_credit_sales_order`, `min_span_hours_for_deliveries`, `in_transit_warehouse_id` and `pod_dir` settings to `app/core/config.py` per `data-model.md`
- [X] T009 Update `DeliveryOrder`, `DeliveryOrderDetail`, `DeliveriesItinerary` and `DeliveriesItineraryDetail` in `app/models/logistics.py` per `data-model.md`: on `delivery_order` drop the five booleans and add `status`, `fulfillment_type`, `parent_delivery_order`, `rejection_reason`, `proof_of_delivery`; on the line add `committed/delivered/returned_quantity` plus the snapshotted `warehouse`, and **no `sent_quantity`**; on the itinerary drop `cancelled`/`completed` and add `status`, `departure_time`, `return_time`; on the itinerary line add the stop FK, `sent/delivered/returned_quantity` and `reason_code`, rename `quantity` to `committed_quantity`, and **drop the direct `deliveries_itinerary` FK** (FR-001, FR-025, FR-025a, FR-033a)
- [X] T010 Add `DeliveriesItineraryStop`, `ProofOfDelivery` and `DeliveryOrderEvent` models to `app/models/logistics.py` per `data-model.md`, including the `UNIQUE (deliveries_itinerary, sequence)`, `(delivery_order, delivery_order_event_id)` and `(status, date)` indexes (FR-036, FR-043, FR-063, FR-068)

### Migration

- [X] T011 [P] Write failing unit tests in `tests/unit/test_migrate.py` (extending the existing file) asserting migration `008` is discovered and ordered after `007`
- [X] T012 Write `migrations/008_delivery_flow_v2.sql` using the real audit figures from T006, in this order: normalise `delivery_order.serial = 0` to `NULL` and relax the column to nullable; renumber genuine folio duplicates keeping the earliest; add the new columns; settle all 26,763 delivery orders into terminal statuses per the R11 mapping table; backfill `delivery_order_detail.warehouse` from `sales_order_detail.warehouse`, falling back to the facility's lowest-id warehouse where that link or column is null, before making it `NOT NULL` (FR-025a); create the three new tables; backfill existing itinerary lines as `sent = delivered = committed_quantity`; create a synthetic stop per existing itinerary; **map all 3,617 itineraries to `ItineraryStatus`, settling any `cancelled=0, completed=0` row to `CLOSED` rather than `OPEN`** — a stale `OPEN` row would permanently block its vehicle under FR-034; seed the in-transit warehouse row; add `UNIQUE (facility, serial)` on `delivery_order`; drop the legacy booleans last (five on `delivery_order`, two on `deliveries_itinerary`)
- [X] T013 Write `migrations/008_delivery_flow_v2_rollback.sql` reversing T012 in inverse order, restoring the five `delivery_order` booleans and the two `deliveries_itinerary` booleans from their status mappings
- [X] T014 Apply `008` against a **copy** of the database, verify the R11 row counts land as predicted (1,059 cancelled / 3,769 delivered / 4,160 picked up / 17,775 abandoned), confirm **no itinerary is left `OPEN`** and every `delivery_order_detail.warehouse` is populated, then apply the rollback and confirm the original shape returns
- [X] T015 Record the migration's stated row counts and its accepted consequence — deliveries in flight at cutover are cancelled and must be re-raised — in the header comment of `migrations/008_delivery_flow_v2.sql`, following the precedent of `007_document_serial_unique.sql`
- [X] T015a Capture the warehouse id `008` seeded for the in-transit location and set `in_transit_warehouse_id` in the deployment environment, documenting it in `.env.example`. The migration cannot know the id it will be assigned, so the setting cannot be defaulted correctly at T008
- [X] T015b Add a startup check in `app/main.py` refusing to serve when `settings.in_transit_warehouse_id` is `0` or names a warehouse that does not exist, with a unit test in `tests/unit/test_stock_ledger.py`. Left unset, every departure would post ledger entries against a non-existent warehouse — a silent misfiling of stock rather than a visible error (research R3)

### Inventory rework — the silent-failure pair

- [X] T016 [P] Write failing unit tests in `tests/unit/test_stock_ledger.py` for `reserve()`, `release_reservations()`, `reserved()` and `available()`: a reservation reduces availability but not on-hand, releasing restores it, and availability equals on-hand minus reservations per product and warehouse (FR-055, FR-055a, FR-056)
- [X] T017 Implement `reserve()`, `release_reservations()`, `reserved()` and `available()` in `app/services/stock_ledger.py` over the existing `lot_serial_rqmt` table (`source = TransactionType.SALES_ORDER`, `reference = sales_order_id`) per research R4, making T016 pass
- [X] T018 [P] Write failing unit tests in `tests/unit/test_sales_order_service.py` asserting that confirming a sales order writes **no** `lot_serial_tracking` row and **does** write one `lot_serial_rqmt` row per stocked line, and that cancelling deletes those rows without writing a compensating entry
- [X] T019 [P] Write a failing unit test in `tests/unit/test_sales_order_service.py` proving the oversell guard: with on-hand 1 and one unit already reserved, confirming a second order for that product is refused — this is the assertion that makes the whole change safe (FR-055a, research R5)
- [X] T020 Rework `confirm_order` in `app/services/sales_order_service.py` to call `stock_ledger.reserve()` instead of `post_movement()`, making T018 pass (FR-055)
- [X] T021 Rework the stock check in `app/services/sales_order_service.py` (`stock_shortfalls` and its `on_hand` lookup) to compare against `stock_ledger.available()` — on-hand minus outstanding reservations — rather than raw on-hand, making T019 pass (FR-055a). **T020 must not ship without this task**
- [X] T022 Rework `cancel_order` in `app/services/sales_order_service.py` to call `stock_ledger.release_reservations()` instead of posting compensating entries, removing the now-unreachable compensating branch (FR-056)
- [X] T023 Update `tests/api/test_sales_orders.py` where it asserts the superseded spec 011 behaviour — confirmation posting an outbound movement, and cancellation posting a compensating entry — replacing both with the reservation expectations
- [X] T024 **Checkpoint**: `uv run pytest tests/ -q` fully green and `uv run ruff check app/ migrations/ tests/` clean. Review this inventory change on its own before any delivery endpoint depends on it

### Transition chokepoint and shared helpers

- [X] T025 [P] Write failing unit tests in `tests/unit/test_delivery_events.py`: `transition()` writes exactly one event carrying from-status, to-status, employee and timestamp; a blank reason is refused where a reason is required; an illegal transition is refused naming both statuses; every terminal status refuses all further transitions; creation records `from_status = NULL`. **Include the five type-restricted transitions** from `data-model.md` — a `DELIVERY` order must be refused `APPROVED` and `READY_FOR_PICKUP`, a `COUNTER_PICKUP` order refused `IN_PREPARATION` (FR-002, FR-003, FR-024, FR-063, FR-065)
- [X] T026a [P] Write a failing unit test in `tests/unit/test_delivery_events.py` proving SC-002 against the services, not the transition table: parameterised over `delivery_order_approval_required` on/off and both fulfilment types, drive real service calls and assert every one of the eleven statuses is entered under at least one configuration (SC-002)
- [X] T026 Implement `app/services/delivery_events.py` — the legal-transition table from `data-model.md`, keyed on `(from, to, fulfillment_type)` so the five type-restricted transitions are refused centrally rather than in each calling service, plus `transition(order, to_status, *, employee, reason=None)` which validates, moves the status and stages the `delivery_order_event` row together — making T025 pass. Explicit calls, **not** a SQLAlchemy event listener (research R7)
- [X] T027 [P] Write failing unit tests in `tests/unit/test_image_service.py` asserting the PNG normalisation is reusable and that two identical images saved with the POD strategy produce two distinct files
- [X] T028 Refactor `app/services/image_service.py` to extract PNG normalisation from filename choice, keeping product images content-addressed and letting a caller supply a UUID filename and target directory, so one order's proof can never alias another's, making T027 pass (FR-044b, research R6)
- [X] T029 [P] Create `app/schemas/delivery_order.py` with the request and response models from `contracts/README.md`, including line quantities and derived `open_quantity`
- [X] T030 [P] Create `app/schemas/delivery_itinerary.py` with the itinerary, stop, line and stop-closure models from `contracts/README.md`

**Checkpoint**: Schema migrated, inventory semantics inverted and verified, every transition forced
through one auditable helper. User stories can begin.

---

## Phase 3: User Story 1 — Raise a delivery order from a sales order (P1)

**Goal**: A confirmed sales order becomes a delivery order listing exactly what is still owed.

**Independent test**: Raise an order from a sales order with deliverable lines, confirm it, observe
`DRAFT` → folio assigned → `PENDING_APPROVAL`/`APPROVED`, and a second attempt refused once every
line is covered.

- [X] T031 [P] [US1] Write failing unit tests in `tests/unit/test_delivery_order_service.py` for deliverable-line computation: uncovered remainder per sales-order line, cancelled delivery orders not counting as coverage, and the already-fully-delivered refusal
- [X] T032 [P] [US1] Write failing unit tests in `tests/unit/test_delivery_order_service.py` for fulfilment-type detection — counter pickup when the sales order's ship-to matches a facility address, delivery otherwise — and for its immutability after creation
- [X] T033 [P] [US1] Write failing API tests in `tests/api/test_delivery_orders.py` for creation and confirmation: 201 in `DRAFT`; 409 for an uncompleted, cancelled, pickup-mode or fully-delivered sales order; 422 when the paid-or-credit rule is unmet; 401 and 403
- [X] T034 [US1] Implement `create_from_sales_order()` in `app/services/delivery_order_service.py` — validation gates (FR-008 to FR-011), deliverable lines with uncovered remainder (FR-012, FR-013), header and product-snapshot copying including the dispatch `warehouse` snapshot with facility fallback (FR-014, FR-015, FR-025a), fulfilment-type detection (FR-004, FR-005) — and record the creation event via `delivery_events.transition()` (FR-065)
- [X] T035 [US1] Implement `assert_editable()` in `app/services/delivery_order_service.py` refusing anything outside `DRAFT` (FR-006). Do **not** reuse `documents.assert_editable`, whose `getattr` defaults would silently pass every order once the booleans are dropped (research R8)
- [X] T036 [US1] Implement header and line editing in `app/services/delivery_order_service.py`, refusing a line quantity above the sales order's remaining deliverable quantity (FR-016)
- [X] T037 [US1] Implement `confirm()` in `app/services/delivery_order_service.py`: refuse an order with no lines, refuse a scheduled date inside `min_span_hours_for_deliveries` unless the caller is an administrator, assign the folio via the existing `documents.assign_folio`, then transition to `PENDING_APPROVAL` when approval is required, else branch by fulfilment type to `IN_PREPARATION` or `APPROVED` (FR-017, FR-018, FR-019, FR-020)
- [X] T038 [US1] Create `app/api/v1/endpoints/delivery_orders.py` with the list, create, get, update, line-edit, line-delete and confirm routes from `contracts/README.md`, each gated on `require_privilege(SystemObject.DELIVERY_ORDERS, ...)`
- [X] T039 [US1] Register the delivery-orders router in `app/api/v1/router.py` under `/delivery-orders`
- [X] T040 [US1] Verify Scenario 1 of `quickstart.md` end to end, including every negative case (SC-001)

**Checkpoint**: Delivery orders can be raised, edited and confirmed. US1 is independently demoable.

---

## Phase 4: User Story 2 — Approve or reject a delivery order (P1)

**Goal**: A supervisor queue that never leaves an order in limbo.

**Independent test**: Confirm under approval-required configuration, list the queue, approve one and
reject another; the first becomes loadable, the second returns to `DRAFT` with its reason.

- [X] T041 [P] [US2] Write failing unit tests in `tests/unit/test_delivery_order_service.py` for approval: the queue contains exactly `PENDING_APPROVAL`; approval writes **exactly one** transition, branching to `IN_PREPARATION` for a delivery order and `APPROVED` for a counter pickup; rejection returns to `DRAFT` storing the reason; a blank reason is refused; both are refused from any other status (FR-021, FR-022, FR-023, FR-024)
- [X] T042 [P] [US2] Write failing API tests in `tests/api/test_delivery_orders.py` for the approval routes, including 422 on a blank reason and 409 on a wrong-status transition
- [X] T043 [US2] Implement `approve()` and `reject()` in `app/services/delivery_order_service.py`, both routed through `delivery_events.transition()`. Approval branches on fulfilment type in a **single** transition — `IN_PREPARATION` for a delivery, `APPROVED` for a pickup — never writing `APPROVED` transiently for a delivery. Rejection stores `rejection_reason` (FR-021 to FR-024)
- [X] T044 [US2] Clear `rejection_reason` when a rejected order is re-confirmed, so a stale reason cannot outlive the rejection it explains
- [X] T045 [US2] Add the approval routes to `app/api/v1/endpoints/delivery_orders.py` gated on `require_privilege(SystemObject.DELIVERY_ORDER_APPROVAL, ...)`, registering `/delivery-orders/approval` **before** `/delivery-orders/{id}` so FastAPI does not match `approval` as an id
- [X] T046 [US2] Add the `mine` filter to the delivery-order list route — the discovery path that replaces the notification v2 specifies (FR-067)
- [X] T047 [US2] Verify Scenario 2 of `quickstart.md` end to end

**Checkpoint**: The approval loop closes. Orders reach `IN_PREPARATION`.

---

## Phase 5: User Story 3 — See what is pending and load it onto an itinerary (P1)

**Goal**: A date-grouped queue, and commitments that two dispatchers cannot double-take.

**Independent test**: List the pending view, create an itinerary, commit lines, and prove a second
itinerary cannot commit the same open quantity.

- [X] T048 [P] [US3] Write failing unit tests in `tests/unit/test_delivery_itinerary_service.py` for the pending view: only `IN_PREPARATION` orders at active facilities, six date buckets, priority-descending ordering within a bucket, and counter-pickup orders absent
- [X] T049 [P] [US3] Write failing unit tests in `tests/unit/test_delivery_itinerary_service.py` for `open_quantity` arithmetic and the commitment guard: `open = ordered − delivered − returned − committed` per FR-026 — **note the `returned` term, without which a partial delivery double-counts its remainder** — a commitment within open quantity succeeds and reduces it; one above is refused stating what is available; the SC-003 invariant holds after each operation
- [X] T050 [P] [US3] Write a failing **concurrency** test in `tests/unit/test_delivery_itinerary_service.py` asserting two simultaneous commitments against the same line yield exactly one success — the SC-004 assertion, and the reason the row lock exists
- [X] T051 [P] [US3] Write failing API tests in `tests/api/test_delivery_itineraries.py` for the pending view, itinerary creation, stop creation and line commitment, including 409 on a second open itinerary per vehicle and 422 above open quantity
- [X] T052 [US3] Implement the pending-deliveries query in `app/services/delivery_itinerary_service.py` with the six-bucket grouping from `contracts/README.md`, using `fk_expansion` to avoid N+1 on customer and product (FR-030 to FR-032)
- [X] T053 [US3] Implement itinerary creation in `app/services/delivery_itinerary_service.py`: `status = OPEN`, date defaulting to today, warehouse from the caller's point of sale, and the one-`OPEN`-itinerary-per-vehicle check under a `FOR UPDATE` lock on the `vehicle` row (FR-033, FR-033a, FR-034, research R9)
- [X] T054 [US3] Implement the operator-licence advisory in `app/services/delivery_itinerary_service.py` — an expired or inactive licence returns a warning in the response and never refuses the assignment (FR-035)
- [X] T055 [US3] Implement stop creation with sequence assignment in `app/services/delivery_itinerary_service.py` (FR-036)
- [X] T056 [US3] Implement line commitment in `app/services/delivery_itinerary_service.py`: take `SELECT ... FOR UPDATE` on the `delivery_order_detail` row, re-read `open_quantity`, refuse an excess, then write the itinerary line and update the running totals in the same transaction (FR-027, FR-028, research R2). Default the quantity to the full open quantity and allow it to be reduced for a partial load (FR-037)
- [X] T057 [US3] Implement commit-all-open-lines-of-an-order in `app/services/delivery_itinerary_service.py`, reusing the single-line path so the guard cannot diverge (FR-038)
- [X] T058 [US3] Implement commitment adjustment, commitment release and stop deletion in `app/services/delivery_itinerary_service.py`, each returning quantity to the open pool
- [X] T059 [US3] Implement itinerary cancellation in `app/services/delivery_itinerary_service.py`, setting `status = CANCELLED`, releasing every commitment and refusing from any status other than `OPEN` (FR-033a, FR-041)
- [X] T060 [US3] Create `app/api/v1/endpoints/delivery_itineraries.py` with the list, create, get, update, cancel, stop and line routes, gating the pending view on `SystemObject.FOR_DELIVER` and the rest on `SystemObject.DELIVERY_ITINERARIES`. The list route implements all six FR-068 filters — `date_from`, `date_to`, `vehicle`, `vehicle_operator`, `warehouse` (the itinerary's dispatch origin) and `status` — with paging and a total count (FR-066, FR-068)
- [X] T061 [US3] Register the delivery-itineraries router in `app/api/v1/router.py` under `/delivery-itineraries`, with `/deliveries` ordered before `/{id}`
- [X] T062 [US3] Verify Scenario 3 of `quickstart.md`, running the concurrency race repeatedly — a guard that passes once may still be racy

**Checkpoint**: A day's route can be planned. The concurrency guard is proven.

---

## Phase 6: User Story 4 — Dispatch the truck and track goods in transit (P1)

**Goal**: Departure freezes what is on board and makes warehouse on-hand mean "actually here".

**Independent test**: Confirm a sales order and observe on-hand unchanged; depart and observe
on-hand fall by exactly the departed quantity while the same quantity appears in transit.

- [X] T063 [P] [US4] Write failing unit tests in `tests/unit/test_delivery_itinerary_service.py` for departure: sent quantity fixed at committed, orders moved to `IN_TRANSIT`, the itinerary locked against further stops and commitments, 409 with nothing committed, and offending lines named when over-committed. **Assert `committed_quantity` is retained across departure** — releasing it there would return in-transit goods to the open pool and let a second dispatcher commit them (data-model.md, SC-004)
- [X] T064 [P] [US4] Write failing unit tests asserting the two-step move — one outbound entry against the dispatch warehouse and one inbound against the in-transit warehouse per stocked line — that the sales-order reservation is released, and that non-stocked lines move nothing
- [X] T065 [P] [US4] Write failing API tests in `tests/api/test_delivery_itineraries.py` for `/depart`, including 409 on an empty itinerary and 409 on cancelling after departure
- [X] T066 [US4] Implement `depart()` in `app/services/delivery_itinerary_service.py`: set itinerary `status = DEPARTED` and stamp `departure_time`, fix `deliveries_itinerary_detail.sent_quantity` from its committed quantity, **leave `delivery_order_detail.committed_quantity` untouched**, transition every delivery order on the trip to `IN_TRANSIT`, and refuse when nothing is committed (FR-029, FR-029a, FR-039, FR-040)
- [X] T067 [US4] Implement the departure inventory move in `app/services/delivery_itinerary_service.py` — `post_movement` outbound against `delivery_order_detail.warehouse` and inbound against `in_transit_warehouse_id`, then release the matching reservation (FR-025a, FR-057, FR-061)
- [X] T068 [US4] Refuse itinerary cancellation after departure in `app/services/delivery_itinerary_service.py` (FR-041, US4 scenario 7)
- [X] T069 [US4] Add the `/depart` route to `app/api/v1/endpoints/delivery_itineraries.py`
- [X] T070 [US4] Verify Scenario 4 of `quickstart.md`, asserting the full stock table at every point **and** that availability dropped at sales-order confirmation (SC-005)

**Checkpoint**: Goods on the road are visible and warehouse on-hand is honest.

---

## Phase 7: User Story 5 — Close a stop with proof of delivery (P1)

**Goal**: The outcome the whole flow exists to produce, backed by evidence.

**Independent test**: Depart a two-line order, close its stop with one line accepted and one partly
rejected, and observe `PARTIALLY_DELIVERED` with a child order and the rejected goods back in stock.

- [X] T071 [P] [US5] Write failing unit tests in `tests/unit/test_delivery_order_service.py` for settlement classification: all accepted → `DELIVERED`; some accepted → `PARTIALLY_DELIVERED`; none accepted → `FAILED`; each order at a shared stop settling independently (FR-047, FR-049, FR-050)
- [X] T072 [P] [US5] Write failing unit tests for the child-order split: exactly one child at `IN_PREPARATION` — a delivery-type child never rests at `APPROVED` — carrying exactly the unaccepted quantity and naming its parent, and a child that splits again keeping the chain traceable (FR-048, SC-007)
- [X] T073 [P] [US5] Write failing unit tests for closure inventory: accepted quantity consumed from in-transit, returned quantity moved from in-transit back to `delivery_order_detail.warehouse`, and `committed_quantity` released only here (FR-029a, FR-052, FR-058, FR-059, FR-062)
- [X] T074 [P] [US5] Write failing unit tests for proof validation: missing receiver name, missing image or a shortfall without a reason code are each refused, and `delivered_quantity` above `sent_quantity` is refused (FR-043, FR-045, FR-045a, FR-046, SC-006)
- [X] T075 [P] [US5] Write failing API tests in `tests/api/test_delivery_orders.py` asserting the POD image route returns 401 unauthenticated, 403 without privilege, 200 with it, and 404 before settlement (FR-044a, SC-006a)
- [X] T076 [US5] Implement POD capture in `app/services/delivery_order_service.py` — validate the structured fields, save the image under `settings.pod_dir` with a UUID filename via the refactored `image_service`, and write the `proof_of_delivery` row (FR-043, FR-044)
- [X] T077 [US5] Implement `close_stop()` in `app/services/delivery_itinerary_service.py` performing all seven steps from `contracts/README.md` in one transaction: validate, store proof, settle each order, split children into `IN_PREPARATION`, post inventory, update sales-order coverage, and set the itinerary to `CLOSED` with its `return_time` when it was the last unresolved stop (FR-042, FR-047 to FR-050)
- [X] T078 [US5] Implement sales-order coverage write-back in `app/services/sales_order_service.py`: set `sales_order.delivered` when every deliverable line is fully delivered (FR-071)
- [X] T079 [US5] Implement the derived per-line coverage block in `sales_order_service.attach_derived` — ordered, covered, delivered, outstanding — computed from delivery orders and not stored (FR-070)
- [X] T080 [US5] Add the stop-closure route to `app/api/v1/endpoints/delivery_itineraries.py` accepting multipart proof plus the per-line JSON payload
- [X] T081 [US5] Add the POD routes to `app/api/v1/endpoints/delivery_orders.py`: structured proof and the authenticated image stream, reading from `settings.pod_dir` and never exposing a static URL (FR-044a)
- [X] T082 [US5] Confirm POD files are unreachable under the `/images` static mount in `app/main.py` — assert it in `tests/api/test_delivery_orders.py`, since this is the requirement that made the clarification go the way it did
- [X] T083 [US5] Verify Scenario 5 of `quickstart.md`, including the independent-stops case and the SC-003 invariant sweep

**Checkpoint**: Deliveries settle with evidence. The core flow is complete end to end.

---

## Phase 8: User Story 6 — Hand over a counter pickup (P2)

**Goal**: An in-store handover as defensible as a delivered one.

**Independent test**: Raise a facility-address order, mark it ready, confirm the pickup with proof,
and observe `PICKED_UP` with stock consumed from the store warehouse and no transit entry.

- [X] T084 [P] [US6] Write failing unit tests in `tests/unit/test_delivery_order_service.py`: `ready_for_pickup()` refused for a delivery-type order; pickup confirmation requiring the same proof as a delivery; stock consumed directly from the store warehouse with no in-transit entry; the reservation released
- [X] T085 [P] [US6] Write failing API tests in `tests/api/test_delivery_orders.py` for `/ready-for-pickup` and `/pickup`, including 422 without an image and 409 on the wrong fulfilment type
- [X] T086 [US6] Implement `ready_for_pickup()` and `confirm_pickup()` in `app/services/delivery_order_service.py`, reusing the POD capture from T076 and posting the single outbound movement (FR-053, FR-054, FR-060)
- [X] T087 [US6] Exclude counter-pickup orders from the pending-deliveries query and from stop creation in `app/services/delivery_itinerary_service.py` (FR-053)
- [X] T088 [US6] Add the pickup routes to `app/api/v1/endpoints/delivery_orders.py`
- [X] T089 [US6] Verify Scenario 6 of `quickstart.md`

**Checkpoint**: Both fulfilment branches settle with equal evidence.

---

## Phase 9: User Story 7 — Retry a failed delivery or cancel with a reason (P2)

**Goal**: Failed deliveries are either sent out again or retired with a stated reason.

**Independent test**: Fail a delivery, re-queue it, observe it loadable again; separately cancel
another with a reason.

- [X] T090 [P] [US7] Write failing unit tests in `tests/unit/test_delivery_order_service.py`: `FAILED` → `IN_PREPARATION` restoring open quantity from returned goods; cancellation from any non-terminal status releasing commitments; a blank reason refused; cancellation refused from `IN_TRANSIT` and from every terminal status
- [X] T091 [P] [US7] Write failing API tests in `tests/api/test_delivery_orders.py` for `/requeue` and `/cancel`
- [X] T092 [US7] Implement `requeue()` in `app/services/delivery_order_service.py`, transferring each line's `returned_quantity` back into its open quantity so the returned goods can be loaded again (FR-051, FR-051a)
- [X] T093 [US7] Implement `cancel()` in `app/services/delivery_order_service.py` with a mandatory non-blank reason, releasing commitments and refusing from `IN_TRANSIT` and terminal statuses (FR-007)
- [X] T094 [US7] Add the `/requeue` and `/cancel` routes to `app/api/v1/endpoints/delivery_orders.py`
- [X] T095 [US7] Verify Scenario 7 of `quickstart.md`, including a failed delivery re-queued and delivered on a second trip with the goods accounted for in between (SC-010)

**Checkpoint**: No order can strand. Every non-terminal state has an exit.

---

## Phase 10: User Story 8 — Read the audit trail (P3)

**Goal**: "¿Quién lo mandó?" has an answer.

**Independent test**: Drive one order from draft to delivered and read back a complete, ordered
history.

> The trail is *written* by Phase 2's `delivery_events` helper, which every preceding story already
> uses. This phase adds only the read surface and the completeness assertion.

- [X] T096 [P] [US8] Write failing API tests in `tests/api/test_delivery_orders.py` for the events route: ordered history, `from_status = null` on the creation entry, reasons present on rejection, failure and cancellation
- [X] T097 [P] [US8] Write a failing unit test in `tests/unit/test_delivery_events.py` asserting **completeness** — drive an order the full length of the flow and assert the event count equals the number of transitions taken, with **one** event per approval (FR-024), not two (SC-008, FR-063)
- [X] T098 [US8] Implement the ordered history query in `app/services/delivery_order_service.py` and add the `/events` route to `app/api/v1/endpoints/delivery_orders.py` (FR-064)
- [X] T099 [US8] Verify Scenario 8 of `quickstart.md`

**Checkpoint**: Every status change is accounted for and readable.

---

## Phase 11: Polish & Cross-Cutting Concerns

- [X] T100 [P] Exclude the in-transit warehouse id from warehouse listing and lookup responses in `app/api/v1/endpoints/warehouses.py`, so it cannot be picked as a sales-order or itinerary warehouse (research R3)
- [X] T101 [P] Add the delivery-order and itinerary list endpoints to `tests/unit/test_list_query_counts.py`, asserting no N+1 on customer, product and facility expansion
- [X] T102 [P] Verify `references.assert_not_referenced` behaves sensibly for the three new tables, and that a missing delivery order, itinerary, stop, line or POD image returns 404 while a lifecycle or quantity violation returns 409 naming the offending records — asserted in both `tests/api/` files (FR-069)
- [X] T103 [P] Update `CHANGELOG.md` `[Unreleased]` under Added, Changed and Removed, calling out explicitly that sales-order confirmation now reserves rather than consumes stock, and that the five delivery-order booleans are gone
- [X] T104 Run the full invariant sweep from `quickstart.md`: SC-003 across every delivery-order line and SC-009 folio uniqueness per facility
- [X] T105 Final gate: `uv run ruff check app/ migrations/ tests/` clean and `uv run pytest tests/ -q` fully green

---

## Dependencies

```text
Phase 1 (Setup & Audit)
    │  T006 gates the migration — no measured figures, no 008
    ▼
Phase 2 (Foundational)  ── T020+T021 are a pair; T024 is a hard review gate
    │
    ├─────────────┬─────────────┬──────────────┬─────────────┐
    ▼             ▼             ▼              ▼             ▼
Phase 3 (US1)   ...           ...            ...           ...
    │
    ▼
Phase 4 (US2) ── needs US1's orders to approve
    │
    ▼
Phase 5 (US3) ── needs US2 to reach IN_PREPARATION
    │
    ▼
Phase 6 (US4) ── needs US3's commitments to depart
    │
    ▼
Phase 7 (US5) ── needs US4's IN_TRANSIT orders to settle
    │
    ├──────────────────────┐
    ▼                      ▼
Phase 8 (US6)          Phase 9 (US7) ── needs US5 to produce a FAILED order
    │                      │
    └──────────┬───────────┘
               ▼
        Phase 10 (US8) ── read-only over what the others wrote
               ▼
        Phase 11 (Polish)
```

**This feature's stories are genuinely sequential**, unlike most. The state machine means US2 needs
orders from US1, US3 needs approved orders, US4 needs commitments, and US5 needs departed goods. The
honest dependency chain is drawn above rather than claiming an independence that does not exist.

US6 (counter pickup) is the one branch that could be built directly after US2, since it skips the
itinerary entirely — worth knowing if work is parallelised.

## Parallel Opportunities

Within each phase, `[P]` tasks touch different files and can run together:

- **Phase 1**: T002–T005 are four independent read-only queries
- **Phase 2**: T007/T008 (enums, settings); T016/T018/T019 (three test files); T025/T027/T029/T030
- **Phase 3**: T031/T032/T033
- **Phase 5**: T048/T049/T050/T051 — four independent test files before any implementation
- **Phase 7**: T071–T075 — five test tasks, the largest parallel block
- **Phase 11**: T100–T103

Test-writing tasks are the richest parallel opportunity throughout, since the constitution requires
them first and they touch separate files.

## Implementation Strategy

### MVP scope

**Phases 1, 2 and 3** — audit, migration, inventory rework and User Story 1. That delivers a
delivery order raised from a sales order, confirmed and numbered, with the new inventory semantics
in place. It is demoable and it is the smallest slice that proves the state machine and the schema.

Phase 2 is disproportionately large for a "foundational" phase because the migration and the
inventory inversion are both prerequisites that cannot be deferred into a story. That is a property
of this feature, not an ordering mistake.

### Incremental delivery

1. Phases 1–2 → schema and inventory ready, **reviewed on their own**
2. Phase 3 (US1) → orders exist → demo
3. Phase 4 (US2) → approval loop closes → demo
4. Phases 5–6 (US3, US4) → trucks depart with honest stock → demo
5. Phase 7 (US5) → deliveries settle with proof → **the feature is functionally complete**
6. Phases 8–10 (US6, US7, US8) → pickup, recovery, audit read
7. Phase 11 → polish and final gates

### Suggested review checkpoints

- **After T024** — the inventory rework, alone. This is the change that can fail silently
- **After T015** — the migration, before it is run anywhere that matters
- **After T083** — the full flow works end to end

---

## Notes

- `[P]` tasks touch different files and have no dependency on incomplete work
- `[Story]` maps a task to its user story for traceability
- Tests are written first and confirmed failing (Constitution v1.2.0) — no exemption applies to any
  task here, since every one has observable behaviour
- Commit after each task or logical group
- The migration (T012–T015) is destructive; take a backup and schedule it for a quiet period


---

## Findings from the end-to-end verification (2026-07-27)

**F1 — the `delivery = 1` filter made the feature unusable against real data. Fixed
(2026-07-27); the column is dropped by migration `009`.** FR-012 takes
"deliverable means the line is flagged for delivery" from `docs/specs/06-logistics.md`.
`sales_order_detail.delivery` is **0 on all 910,891 rows**, including the 54,741 lines the legacy
delivery orders were actually raised from. Every `create_from_sales_order` therefore returns 409
"already fully delivered". The scenarios above only ran because the flag was set by hand on two
fixture orders and reverted afterwards. **FR-012 needs a decision**: drop the filter, or find what
the legacy system really used.

**F2 — FR-011 was wrong and is now struck (2026-07-28).** Not implementing it was accidentally
correct.
FR-011 says refuse creation when the sales order's delivery mode is `PickUp`. The data says the
opposite: of 15,527 orders with `partial_deliveries = 1` (`DeliveryMode.PickUp`), **15,461 have a
delivery order** — those are precisely the counter pickups. Implementing FR-011 would have broken
the entire `COUNTER_PICKUP` branch. The requirement is removed from the spec; FR-005's ship-to detection and
FR-005a's explicit override are what distinguish the two fulfilment types.

---

## After merge (2026-07-28)

The 108 tasks above were complete at PR #120. What followed came out of the feature being used and
measured, not from the plan, and is logged here so the trail does not stop at the merge.

- [X] **Release reservations per line, not per order** (PR #120). `release_reservations` gave back
      an order's whole claim; an order reserves one row per line, so departing one line released
      the rest — leaving them sellable twice. Departure and counter pickup now release only what
      moved; returns re-reserve, because the sale still owes the goods.
- [X] **Expire abandoned orders** (#118, PR #121). A sweep cancels an order still neither paid nor
      delivered after `UNPAID_ORDER_EXPIRY_DAYS` (default 2) **and still holding a reservation**.
      Scoping to reservation holders is load-bearing: without it, 1,363 historical orders matched
      and not one held stock.
- [X] **Seed a system employee** (PR #122). `sales_order.updater` is an enforced FK; an automated
      cancellation needs an actor that is not a salesperson. Migration `010`, id `-1` — negative so
      employee `AUTO_INCREMENT` is untouched.
- [X] **Make it a constant, create it at startup** (PR #123). One correct value, nothing to decide,
      and a wrong one fails a sweep partway through. Moved to `app/core/constants.py`;
      `ensure_system_employee` creates the row when absent.

**Known and deliberately open**

- `lookup_products` reports raw on-hand rather than availability, so a salesperson can see stock
  that confirmation will refuse. Left because it changes a spec 011 response's meaning.
- `@app.on_event('startup')` is deprecated by FastAPI in favour of `lifespan`; two hooks in
  `app/main.py` use it. Worth its own change rather than smuggling an app-wide boot change into an
  unrelated PR.
- Production cutover for migrations `008`, `009` and `010` — all applied to `mbe_dev` only, which
  was designated a development copy.
- `mbe-ui` must stop sending and reading the removed sales-order line `delivery` field.
