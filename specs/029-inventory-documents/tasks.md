---
description: "Task list for warehouse receipts, issues and inventory transfers"
---

# Tasks: Warehouse Receipts, Issues and Transfers

**Input**: design documents in `specs/029-inventory-documents/`: plan.md, spec.md, research.md (R1–R10), data-model.md, contracts/inventory-documents.md and quickstart.md.

**Tests**: required. Constitution v1.2.0 makes them mandatory and test-first, overriding the template's "optional" line. Every test task comes before its implementation and must fail first.

**Working tree**: `/Users/eddy/Projects/mictlanix/mbe-api-029`. Before each commit:
- check `git branch --show-current` (expect `029-inventory-documents`);
- run `uv run ruff check app/ migrations/ tests/`.

No commit carries a Co-Authored-By line. Delegated work goes to agents with absolute paths and no commits.

**Reference patterns**:
- draft/confirm/cancel services: `app/services/customer_refund_service.py`;
- routes and module-level `require_privilege` constants: `app/api/v1/endpoints/customer_refunds.py`;
- the locking `MAX` read: `app/services/invoice_service.py` `_next_serial` (its locking read only; inventory takes no parent-row lock, research R1);
- transit posting: `app/services/delivery_itinerary_service.py` `depart`;
- seeds: `tests/integration/seed.py` (facility 1, warehouse 1, transit warehouse 2) and `tests/integration/delivery_seed.py` (`warehouse(db, facility=…)`).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Write `migrations/026_inventory_transfer_transit.sql` and `migrations/026_inventory_transfer_transit_rollback.sql`, using the SQL in data-model.md and the comment header, idempotence and backtick style of `migrations/025_fiscal_document_customer_payment.sql`. **Do not apply them to mbe_dev**; T030 does that, after confirmation.
- [ ] T002 After T001 (its schema tests read the migration files), in `app/models/inventory.py`:
  - add `InventoryTransfer.in_transit: Mapped[bool]` (`Boolean`, `default=False`, `server_default='0'`) after `cancelled`;
  - add `InventoryTransferDetail.quantity_received: Mapped[Decimal | None]` (`Numeric(18, 4)`) after `quantity`.

  Add both columns to `docs/data-dictionary.md`, under `inventory_transfer` and `inventory_transfer_detail`. Then run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py tests/unit/test_migrate.py -q`.
- [ ] T003 [P] Create `app/schemas/inventory.py` per contracts/inventory-documents.md:
  - **status:** `TransferStatus` (StrEnum `draft`, `in_transit`, `received`, `cancelled`) and its `derive_transfer_status(*, completed, cancelled, in_transit)`. Receipts and issues reuse `DocumentStatus`/`derive_status` from `app/schemas/sales_order.py`.
  - **requests:** `ReceiptCreate {warehouse, comment?}`, `ReceiptUpdate {warehouse?, comment?}`, `IssueCreate`/`IssueUpdate` (the same fields), `TransferCreate {warehouse, warehouse_to, comment?}`, `TransferUpdate {warehouse?, warehouse_to?, comment?}`, `LineAdd {product, quantity > 0}`, `LineUpdate {quantity > 0}`, `SalesOrderItemsAdd {sales_order}`, `TransferReceive {lines: list[{line_id, quantity > 0}] | None}`.
    - `comment` is at most 500 characters and `quantity` has at most 4 decimal places.
  - **responses:** the receipt, issue and transfer responses and summaries, line responses, `warnings: list[str] = []` on the receipt, and `InTransitResponse` with its product and line items.

  Write the static-rule tests first in `tests/unit/test_inventory_schemas.py`, one rejected value per rule, and one test per transfer-status branch.

---

## Phase 2: Foundational (blocks every story)

- [ ] T004 [P] Write `tests/unit/test_inventory_locks.py` first, so it fails. It covers T006's lock helpers without a database, through a fake session that records each executed statement compiled with `sqlalchemy.dialects.mysql`:
  - `lock_document` issues `SELECT … FOR UPDATE` on the header row's primary key;
  - `lock_warehouses(ids)` issues one select `WHERE warehouse_id IN (…) ORDER BY warehouse_id FOR UPDATE` with the ids de-duplicated and sorted, and no `facility` condition;
  - `next_serial(model, facility)` issues a plain `SELECT max(serial) … WHERE facility = :f` with no locking clause, and is only called after `begin_locked` (R1);
  - `begin_locked(...)` makes its pre-reads, commits, then runs `lock_document` and (when asked) `lock_warehouses` in that order, with no other statement after the commit and before the last lock (R2). The warehouse ids are taken from the locked row, not from the object passed in: a fake that returns a changed row after the lock makes the warehouse lock follow the changed row;
  - `busy_as_conflict` turns a DBAPI error with code 1213 or 1205 into 409 "The warehouse is busy, retry", after a rollback, and re-raises anything else.
- [ ] T005 [P] Write `tests/unit/test_inventory_documents.py` first, so it fails. It covers the pure helpers of T006:
  - `merge_line_quantity` (existing line + added quantity);
  - `per_product_totals(lines)`, which sums repeated products;
  - `shortages(required: dict[product, qty], available: dict[product, qty]) -> list`, which is empty when everything fits;
  - `reversal_entries(entries)`, which negates each entry, and `net_outbound(entries) -> {(warehouse, product): qty}` over the reversal;
  - `warehouses_to_lock(kind, document, transit_map, ledger_warehouses)`: the transit of the document's own facility (every kind; it is also the numbering lock, R1), the header warehouses and the ledger warehouses, de-duplicated and sorted. A facility missing from the map gives 422 "Facility {id} has no in-transit location".
- [ ] T006 Create `app/services/inventory_documents.py`, the shared mechanics (FR-001 to FR-013). It contains:
  - **The pure helpers of T005.**
  - **`Kind`:** a frozen dataclass:
    - header model, line model, line FK attribute name and id attribute names;
    - the names of the header's warehouse attributes (`('warehouse',)`, or `('warehouse', 'warehouse_to')` for a transfer) and `transit: bool` (true for transfers: the `in_transit` flag and transit postings);
    - `TransactionType`, `SystemObject`, and the label used in messages.

    Constants `RECEIPT`, `ISSUE` and `TRANSFER`.
  - **Lookups:** `get_document(db, kind, id)` and `get_line(db, kind, document, line_id)`; each returns `None` when not found.
  - **Locks (R1, R2), tested by T004:**
    - `lock_document(db, kind, document_id)`: `SELECT` the header row `FOR UPDATE` with `populate_existing`, returning the locked row (or 404);
    - `lock_warehouses(db, ids)`: one `SELECT … WHERE warehouse_id IN (:ids) ORDER BY warehouse_id FOR UPDATE`, primary key only;
    - `begin_locked(db, kind, document_id, *, stock=False, ledger=False) -> document`:
      1. pre-reads: the facility → transit map when `stock`, and the distinct warehouses of the document's ledger entries when `ledger`;
      2. `await db.commit()`;
      3. `lock_document`;
      4. when `stock`, `lock_warehouses(warehouses_to_lock(...))`, worked out from the locked row. This includes the facility's transit warehouse, the numbering lock (R1);
      5. returns the locked row.
    - `next_serial(db, model, facility) -> int`: an ordinary `SELECT MAX(serial) … WHERE facility = :f` on the document's own table, returning max + 1. It is called only after `begin_locked(stock=True)`, whose transit lock queues every numbering step of the facility. The docstring cites mbe#45 and research R1, and says why there is no locking read and no facility row lock;
    - `busy_as_conflict()`: an async context manager around each locking step. MariaDB errors 1213 and 1205 roll back and become 409 "The warehouse is busy, retry" (R2's known limit).

    **Every state-changing function below runs inside `busy_as_conflict`, starts with `begin_locked`, and makes no ordinary read before it.** That covers confirm, cancel, header and line edits, and in T019 dispatch, receive, close and sales-order items.
    - Edits and sales-order items lock the document only.
    - Receive and close the remainder also lock the warehouses (`stock=True`).
    - Confirm and dispatch then call `next_serial`; cancel adds the ledger pre-read.

    Ordinary reads after the locks are current (R2), so `stock_ledger` is used unchanged. The facility row is never locked.
  - **`create_draft(db, kind, *, warehouse, warehouse_to=None, comment, current)`:**
    - validate each warehouse: unknown → 404; `in_transit` → 422; origin equal to destination → 422; warehouse status is not checked (R9);
    - facility = the warehouse's (the origin's for a transfer);
    - set the audit fields (`local_now()`, `current.employee_id`) and `completed = cancelled = False`;
    - commit and refresh.
  - **`update_header(...)`:** `begin_locked` (document only), `documents.assert_editable`, then the same checks. Changing the warehouse updates `facility`.
  - **Lines:**
    - `add_line(...)`: the product must exist (404) and be stockable (422); its status is not checked (R9). Add to the existing line of that product (the lowest id if a legacy draft repeats it), or create one with `product_code` and `product_name` snapshots. Receipt lines get `quantity_ordered = 0`.
    - `update_line(...)` and `remove_line(...)`: drafts only.
    - All three start with `begin_locked` (document only) and bump `updater` and `modification_time`.
  - **`assert_facility(current, *facilities)`:** non-administrators whose `facility_id` is not among the facilities get 403 "Document belongs to another facility" (R6).
  - **`assert_available(db, required: dict[product, qty], warehouse)`:** uses `stock_ledger.available` per product. 409 names the product code and the warehouse code: "Not enough stock of {code} in {warehouse}: {available} available, {required} required".
  - **`confirm_simple(db, kind, document_id, *, current)`** (receipt and issue):
    1. `begin_locked(stock=True)`;
    2. `assert_editable`; no lines → 409; any line with quantity ≤ 0 or a non-stockable product → 422;
    3. `assert_facility(document.facility)`;
    4. `next_serial`; for an issue, `assert_available`;
    5. post one entry per line (inbound for a receipt, outbound for an issue);
    6. set `serial` (last, after every read, so autoflush cannot write it early), `completed`, `updater` and `modification_time`, then commit.

    Every failure rolls back, so no serial is consumed. A unique-index violation is left to the global `IntegrityError` handler (409).
  - **`cancel(db, kind, document_id, *, current)`:**
    1. `begin_locked(stock=True, ledger=True)`, which locks the facility's transit, every header warehouse, and every warehouse of the document's own entries (R2, R3). The ledger pre-read runs for drafts too, because a draft can be confirmed before the lock;
    2. cancelled → 409; `assert_facility(document.facility)` for every cancel, draft or confirmed (FR-011);
    3. draft → set `cancelled` and commit, with no ledger work;
    4. confirmed → read the entries with this source and reference, compute `net_outbound` of the reversal, `assert_available` per warehouse, post the reversal, set `cancelled`, and when `kind.transit` clear `in_transit`. Lines keep `quantity_received`. Then commit (R3).
  - **`list_documents(db, kind, *, current, status, warehouse, skip, limit, status_clause)`:** per R5.
    - The default scope or the wider view (administrator or `has_any_privilege(db, current.user_id, [(kind.system_object, AccessRight.DELETE)])`).
    - The `status` values allowed per document (anything else is 422).
    - `warehouse` matches the origin or the destination for transfers, and narrows the default scope without widening it.
    - Newest first, returning `(items, total)`.
    - The POS warehouse is looked up from `PointSale.warehouse`. Reuse `delivery_itinerary_service._pos_warehouse` by importing it, or move it to `point_sale_service` if importing a private helper across services is against the codebase's habit.
- [ ] T007 Write `tests/integration/inventory_seed.py`, the helpers shared by the story tests:
  - two facilities, each with a regular warehouse and its transit warehouse;
  - a stockable product and a non-stockable one;
  - `stock(db, product, warehouse, qty)`, which posts a ledger entry;
  - `reserve(db, product, warehouse, qty)`, through `stock_ledger.reserve` against a dummy sales order;
  - a purchase order factory (order + lines per warehouse);
  - a sales order factory (completed or draft, with lines).

  Follow `tests/integration/delivery_seed.py`.

**Checkpoint:** the shared helpers and the lock order pass their unit tests; `stock_ledger.py` is unchanged.

---

## Phase 3: User Story 1 — The warehouse receives goods (P1) 🎯 MVP

**Goal**: receipts from draft to confirmed to cancelled, posting inbound entries.

**Independent Test**: spec US1. Create, add two lines, confirm: the serial is the next one, stock rises, and the receipt is read-only. Cancel restores the stock, and is refused once the goods are issued.

- [ ] T008 [P] [US1] Write `tests/integration/test_inventory_receipt_flow.py` first. It covers US1 scenarios 1–6:
  - **Confirm:** it assigns serial = max + 1 per facility; there is one +qty entry per line with source 4, reference = receipt id, and NULL lot, serial and expiry (SC-001).
  - **Lines:** adding the same product twice merges; adding a non-stockable product → 422; editing or adding after confirm → 409.
  - **Refusals:** confirm with no lines → 409, and no serial is consumed (the next confirm gets the number).
  - **Cancel:** cancelling a confirmed receipt posts the −qty reversal and the balance returns. Cancelling after the stock was taken by an outbound entry → 409 naming the product and warehouse. Cancelling a draft writes no entries; cancelling twice → 409.
  - **Facility:** a non-administrator in another facility confirming → 403, and cancelling → 403 (FR-011). Creating the receipt sets its facility from the warehouse, and changing a draft's warehouse to one of another facility moves the receipt to that facility (FR-001).
  - **In-transit warehouse:** creating a receipt, or changing a draft's warehouse, to an in-transit warehouse → 422 (FR-009).
  - **Legacy lines:** a legacy draft holding a line with quantity 0 or a non-stockable product (seeded directly) cannot be confirmed: 422 naming the line (FR-004).
  - **Repeats:** confirming the same receipt a second time → 409 with no new entries and no serial consumed, and the same for cancelling twice. This proves the state is re-read after `begin_locked`; the concurrent pair runs in T033.
  - **Product status:** a product discontinued after it was added still confirms; adding a discontinued but stockable product is accepted (R9).
  - **Legacy entries outside the header:** a confirmed receipt whose ledger entries are in a warehouse other than its current header warehouse (seeded directly, as the current system's post-confirm edits left 813 of them) is reversed in that warehouse too, and its stock check covers it.
- [ ] T009 [P] [US1] Write `tests/api/test_inventory_receipts.py` first, covering the routes of contracts/inventory-documents.md except `/complementary`:
  - status codes 201 and 200;
  - 401 without a token, and 403 without the right: Create, Update, Delete and Read on object 15;
  - 404 for an unknown receipt and line, and 422 for quantity 0.

  Patch services where the existing API tests do.
- [ ] T010 [US1] Create `app/services/inventory_receipt_service.py` with:
  - `attach_derived(db, receipt)`: the status, the line list, and empty warnings for now (US4 fills them);
  - thin wrappers over `inventory_documents`: `create`, `update`, `add_line`, `update_line`, `remove_line`, `confirm` and `cancel`.
- [ ] T011 [US1] Create `app/api/v1/endpoints/inventory_receipts.py` with the routes and rights per the contract: `_READ`, `_CREATE`, `_UPDATE` and `_DELETE` on `SystemObject.INVENTORY_RECEIPTS`; 404 via `_receipt_or_404`. Register it in `app/api/v1/router.py` under `/inventory-receipts`, tag `inventory-receipts`. Make T008 and T009 pass.

**Checkpoint:** receipts work end to end, and stock can be brought in.

---

## Phase 4: User Story 2 — The warehouse writes stock off (P1)

**Goal**: issues with an available-stock check, and their reversal.

**Independent Test**: spec US2. With 10 on hand and 4 reserved, issuing 7 is refused and issuing 6 succeeds.

- [ ] T012 [P] [US2] Write `tests/integration/test_inventory_issue_flow.py` first. It covers US2 scenarios 1–4:
  - **Available stock:** an issue over available (on hand − reserved) → 409 naming the product and warehouse.
  - **In-transit warehouse:** an issue on an in-transit warehouse → 422 (FR-009).
  - **Repeated products:** two lines of the same product together exceeding stock → 409. Lines added through the service merge, so seed this case with two detail rows written directly, as legacy drafts do.
  - **Confirm:** one −qty entry per line with source 3.
  - **Cancel:** posts +qty.
  - **Unknown id:** confirm and cancel → 404 at the route; at the service, `get_document` returns None.
  - **Supplier return:** a legacy issue with `supplier_return` set shows it, and no request field can change it (FR-031).
- [ ] T013 [P] [US2] Write `tests/api/test_inventory_issues.py` first: the same coverage as T009, on object 16.
- [ ] T014 [US2] Add `attach_issue(document)` (status and lines) to `app/services/inventory_documents.py`. There is no `inventory_issue_service.py`: the issue routes call `inventory_documents` with `ISSUE` directly (plan structure).
- [ ] T015 [US2] Create `app/api/v1/endpoints/inventory_issues.py` and register it under `/inventory-issues`. The response includes `supplier_return`. Make T012 and T013 pass.

**Checkpoint:** stock can be written off and never goes below what sales orders hold.

---

## Phase 5: User Story 3 — Stock moves between facilities through transit (P1)

**Goal**: two-step transfers with partial receipt, closing the remainder, cancel, the in-transit view, and sales-order items.

**Independent Test**: spec US3. Dispatch from facility A to facility B: stock leaves A and appears in transit, and both see the transfer. Receive 8 of 10: 2 stay in transit. Close the remainder: the 2 return to A.

- [ ] T016 [P] [US3] Write `tests/unit/test_inventory_transfer_state.py` first:
  - `transfer_line_figures(quantity, quantity_received, completed, in_transit) -> (received, outstanding)` for new and legacy (NULL) lines, in each state. The status branches are tested once, in T003's schema tests;
  - `apply_receipt(lines, requested)`: a line id not on this transfer → 404; the same line id twice → 422; a quantity over the outstanding one, or on a line with nothing outstanding → 409;
  - `sales_order_additions(order_lines, existing_products)`, which sums per product, skips existing products, and drops non-stockable products.
- [ ] T017 [P] [US3] Write `tests/integration/test_inventory_transfer_flow.py` first. It covers US3 scenarios 1–11:
  - **Origin and destination:** the same warehouse → 422; an in-transit warehouse as either → 422.
  - **Dispatch:**
    - over available at the origin → 409;
    - success gives the origin facility's serial, −qty at the origin and +qty at the origin's transit (source 5), status `in_transit`, and `quantity_received = 0`;
    - with the origin facility's transit warehouse removed → 422, nothing posted, and no serial consumed.
  - **Receive:**
    - a full receive (no body) posts transit −qty and destination +qty, status `received`, and the entries net to zero (SC-001);
    - a partial receive of 8 of 10 stays `in_transit` with 2 outstanding, and a second receive of 2 completes it;
    - receiving 3 when 2 are outstanding → 409;
    - receiving the remaining 2 a second time after it completed → 409, with no new entries (the state is re-read after `begin_locked`).
  - **Transit warehouse:** after dispatch, moving the origin warehouse to another facility does not change which transit receive and cancel use (research R4).
  - **Close the remainder:** 2 return to the origin, status `received`, and the line shows 8 of 10.
  - **Cancel:**
    - in transit → transit back to the origin;
    - received → destination back to the origin;
    - received, with destination stock already used → 409.
  - **Facility rule:** dispatch by a non-administrator of the destination facility → 403; receive by one of the origin facility → 403; close by either facility → allowed.
  - **Lists:** a transfer in transit appears in the list of a user whose POS warehouse is the origin, and of one whose POS warehouse is the destination (SC-005). `/in-transit` for either facility shows the outstanding quantities per product and per line.
  - **Sales-order items:**
    - a completed order adds its stockable lines summed per product, skipping products already on the transfer; repeating changes nothing;
    - a draft or cancelled order → 409.
  - **Legacy:** a legacy completed transfer (`in_transit` 0, `quantity_received` NULL) reads `received` with outstanding 0. Cancelling it reverses its recorded entries, including entries in a warehouse that is no longer its origin or destination (785 such transfers on mbe_dev).
  - **Cancelled transfer:** after cancel, the lines still show what had been received before it, and the status is `cancelled`.
- [ ] T018 [P] [US3] Write `tests/api/test_inventory_transfers.py` first: all 13 routes of the contract, with 401, 403 (each right on object 17, including Delete for `/close` and `/cancel`), 404 and 422.
- [ ] T019 [US3] Create `app/services/inventory_transfer_service.py`:
  - the pure helpers of T016;
  - `attach_derived`;
  - **Every step below runs inside `busy_as_conflict` and starts with `inventory_documents.begin_locked`, with no ordinary read before it** (R2).
    - Dispatch, receive and close pass `stock=True`; dispatch then calls `next_serial`. The warehouses (origin, destination, and the transit of the transfer's own facility) are worked out from the locked row.
    - The transit warehouse comes from the pre-read map (research R4).
    - Sales-order items lock the document only.
  - **`dispatch`:**
    1. `begin_locked(stock=True)`. A facility with no transit warehouse → 422 "Facility {id} has no in-transit location", with nothing posted and no serial consumed;
    2. `assert_editable`, lines, stockable and `assert_facility(document.facility)`;
    3. `next_serial`; `assert_available` at the origin;
    4. post out at the origin and in at transit;
    5. set `serial` last, then `completed`, `in_transit` and `quantity_received = 0` on each line, then commit.
  - **`receive(document, requested | None, current)`:**
    1. `begin_locked(stock=True)`; `in_transit` is required (409);
    2. `assert_facility` for the destination warehouse's facility;
    3. `apply_receipt` (404, 422 or 409 per T016);
    4. post transit out and destination in per received quantity;
    5. update `quantity_received`, and clear `in_transit` when nothing is outstanding.
  - **`close_remainder`:** `begin_locked(stock=True)`; `in_transit` required (409); `assert_facility(document.facility, destination warehouse's facility)`; post transit out and origin in for the outstanding quantities; clear `in_transit`.
  - **`cancel`:** `inventory_documents.cancel(db, TRANSFER, …)`, which locks the origin, destination, transit and ledger warehouses, nets the reversal across all of them, and clears `in_transit` in the same transaction (T006).
  - **`add_sales_order_items`:** `begin_locked` (document only), `assert_editable`; the order must exist (404) and be completed and not cancelled (409).
  - **`in_transit_view(db, facility)`:** per R10.
- [ ] T020 [US3] Create `app/api/v1/endpoints/inventory_transfers.py` and register it under `/inventory-transfers`.
  - `/in-transit` is declared **before** `/{id}`.
  - The status filter uses `TransferStatus` through `list_documents`' `status_clause`.
  - Make T016, T017 and T018 pass.

**Checkpoint:** inter-facility transfers move through transit, and both ends can see them.

---

## Phase 6: User Story 4 — The warehouse completes a purchase order's delivery (P2)

**Goal**: the ordered and received view, the over-receipt warning, and the complementary receipt.

**Independent Test**: spec US4. A short-received completed purchase order gives a complementary draft holding only the remaining line; asking again returns the same draft.

- [ ] T021 [P] [US4] Write the US4 tests first.

  `tests/unit/test_inventory_receipt_service.py` (constitution: branching services get unit tests) covers the pure helpers:
  - `over_receipt_warnings(lines, ordered_by_po_line, received_elsewhere)`: sums this receipt's lines per order line, compares against the order line's own quantity, and gives one message per order line over it;
  - `remaining_lines(order_lines, received)`: skips lines with nothing remaining;
  - `complementary_refusal(order, remaining)`: no order, not completed, cancelled, fully received, each with its reason.

  Then extend `tests/integration/test_inventory_receipt_flow.py` for US4 scenarios 1–5:
  - **Received elsewhere:** `quantity_received_elsewhere` counts only other completed, non-cancelled receipts.
  - **Legacy complementary receipts:** a draft whose stored `quantity_ordered` holds the remaining quantity gets no false warning, because the check reads the order line (research R7).
  - **Warnings:** an over-receipt warning appears on save, read and confirm, and confirm still succeeds (FR-022). A hand-added line on a linked receipt has no link and no warning (FR-024).
  - **Complementary receipt, the success cases:**
    - it creates a draft (201) with only the under-received lines of that warehouse, `quantity = remaining` and `quantity_ordered` = the order line's quantity;
    - a second call returns the same draft (200);
    - called on that open draft itself, it returns the draft (200).
  - **Complementary receipt, the refusals (409 with the reason):** the order is not completed, cancelled, or fully received, or the receipt has no purchase order.
- [ ] T022 [P] [US4] Extend `tests/api/test_inventory_receipts.py` for `/complementary`: 200, 201, 403 without Create, 404 and 409.
- [ ] T023 [US4] In `app/services/inventory_receipt_service.py`, add:
  - the pure helpers of T021;
  - `_received_elsewhere(db, purchase_order_details, exclude_receipt)`, one grouped query;
  - warnings in `attach_derived`, per R7, against the purchase-order lines' own quantities;
  - `complementary_receipt(db, receipt, *, current) -> (receipt, created: bool)`, per R8. It ends the opening transaction and locks the `purchase_order` row `FOR UPDATE` before any ordinary read. It re-reads the source and the returned draft with `populate_existing`, so the response is never stale.

  Add the route to `inventory_receipts.py`, setting 200 or 201 from `created`. Make T021 and T022 pass.

**Checkpoint:** short purchase-order deliveries can be completed without retyping.

---

## Phase 7: User Story 5 — Each user sees their warehouse's documents (P2)

**Goal**: the default list scope, the wider view, and the facility rule, verified across all three documents.

**Independent Test**: spec US5. A clerk lists only their warehouse's documents and their own; a user with Delete sees all; a user with no rights gets 403 everywhere.

- [ ] T024 [P] [US5] Write `tests/integration/test_inventory_scope.py` first, parametrised over receipts, issues and transfers:
  - **Default scope:** the caller sees their POS warehouse's documents plus their own, and nothing else. For transfers the POS warehouse may be the origin or the destination. A caller without a point of sale sees only their own.
  - **Wider view:** with Delete on the object, or as an administrator, they see all, and the `warehouse` filter applies.
  - **Filters:** the `status` filter works, and the order is newest first.
  - **Facility rule:** confirm and cancel by a non-administrator of another facility → 403 for all three documents (FR-011).
- [ ] T025 [US5] Fix whatever T024 exposes in `app/services/inventory_documents.py` `list_documents` or `assert_facility`. If it passes first time, record that in the commit message: the scope was built in T006.

**Checkpoint:** every story is done.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T026 [P] Update `docs/specs/04-inventory.md` §1–3 to match the spec:
  - the corrections (state guards, cancel reversal, available stock, serial safety, the facility rule);
  - the two-step transfer with partial receipt and closing the remainder, replacing "same facility only" and "use Issue + Receipt between facilities";
  - the complementary receipt's true ordered quantity;
  - supplier returns not built;
  - lots, serials and FIFO out of scope.
- [ ] T027 [P] Update `CHANGELOG.md` `[Unreleased]`, under **Added**: receipts, issues and transfers (with the transit cycle), the in-transit view, the complementary receipt, and migration 026.
- [ ] T028 [P] Add a "Follow-ups" section to `specs/029-inventory-documents/quickstart.md`, for the PR description (no code):
  - `documents.assign_folio`'s non-locking `MAX` (R1);
  - sales-order confirmation's unlocked availability check (R2).
- [ ] T029 Run `uv run ruff check app/ migrations/ tests/`, `uv run ruff format --check app/ tests/` and `uv run pytest -q`; everything must be green. Include `tests/integration/test_no_endpoint_returns_500.py` for the new routes, extending its route list if it enumerates them.
- [ ] T030 **Ask the user first; DDL.** Apply migration 026 to mbe_dev (quickstart step 1). Check the columns, and that legacy transfers read as `received`. Record the result in spec.md under "Verification".
- [ ] T031 **Ask the user first.** Deploy the branch to the test instance as mictlanix@, restarting `mbe-api-test` as root. **Only after T030**, because the model maps the new columns.
- [ ] T032 **Ask the user first; writes to mbe_dev.** Run SC-007 (quickstart step 3): a receipt, an issue and an inter-facility transfer through dispatch, partial receive, close and cancel. Check the ledger sums per (warehouse, product) before and after, and record the ids and results.
- [ ] T033 **Ask the user first; writes to mbe_dev.** Run SC-002 and SC-003 (quickstart step 4):
  - 20 concurrent confirms must give distinct, consecutive serials;
  - the same draft confirmed by two requests at once posts once: one succeeds and the other gets 409;
  - of two concurrent over-subscribing issues, exactly one may succeed;
  - two concurrent partial receives of 8 on a line of 10: one succeeds and the other gets 409, with no more than 10 received;
  - a receive racing a cancel on one transfer: the ledger nets to zero per (warehouse, product) whichever wins;
  - two concurrent complementary requests for one order give a single draft;
  - issues confirmed at once in two facilities on the same product end without a deadlock or a 500;
  - two receipts of one facility, on different warehouses, confirmed at once: both succeed with consecutive serials, and neither gets 409 (research R1);
  - a transfer dispatched while the destination receives the facility's previous transfer: both succeed;
  - two transfers dispatched at once in opposite directions between the same two facilities both succeed, or one gets 409 "busy", never a 500;
  - a customer refund confirmed at the same moment as an issue from the same warehouse: the issue never answers 500 (R2's known limit). Record what the refund side answered.

  Record the results. List any cleanup and get it approved before it runs. Remove scratch scripts from xolotl.

---

## Dependencies & Execution Order

- **Setup:** T001 → T002 (its schema tests read the migration files); T003 in parallel with both.
- **Foundational:** T004 ∥ T005 → T006 (it needs both sets of tests) → T007. Phase 2 blocks every story.
- **US1** (T008–T011) needs Phase 2.
- **US2** (T012–T015) needs Phase 2, and is independent of US1 (separate endpoint files).
- **US3** (T016–T020) needs Phase 2, and is independent of US1 and US2.
- **US4** (T021–T023) needs US1, because it extends the receipt service, routes and tests.
- **US5** (T024–T025) needs US1, US2 and US3 (all three routers).
- **Polish:** T026–T029 after all stories. T030 → T031 → T032 → T033 in order, each gated on the user.

### Parallel opportunities

- T001 ∥ T003, then T002
- T004 ∥ T005
- T008 ∥ T009; T012 ∥ T013; T016 ∥ T017 ∥ T018; T021 ∥ T022
- After Phase 2: US1, US2 and US3 in parallel. They touch separate service and endpoint files and share only `router.py` (one line each, so serialise those edits).
- T026 ∥ T027 ∥ T028

---

## Implementation Strategy

1. **MVP:** Setup, Foundational and US1 (receipts). Stock can come in through the new MBE.
2. **Then:** US2 (issues) and US3 (transfers), the other P1s, then US4 (purchase-order receipts) and US5 (scope verification).
3. **Polish:** docs, the CHANGELOG and the full suite. Then, each after confirmation: the migration, the deploy, the full cycle and the concurrency checks.
4. **PR:** it must close mictlanix/mbe-roadmap#58, #60 and #62.
