---
description: "Task list for purchase requests, purchase orders and average cost"
---

# Tasks: Purchase Requests, Purchase Orders and Average Cost

**Input**: design documents in `specs/031-purchase-requests-orders/`: plan.md, spec.md, research.md (R1–R12), data-model.md, contracts/purchasing.md and quickstart.md.

**Tests**: required. Constitution v1.2.0 makes them mandatory and test-first; every test task comes before its implementation and must be run and seen to fail first.

**Working tree**: `/Users/eddy/Projects/mictlanix/mbe-api-031`. It has a copy of `.env`.
- Before each commit, check `git branch --show-current` (it must be `031-purchase-requests-orders`) and run `uv run ruff check app/ migrations/ tests/`.
- No commit carries a Co-Authored-By line. Delegated work goes to agents with absolute paths and no commits.

**Reference code**:
- `app/services/inventory_documents.py`: `begin_locked`, `lock_document`, `lock_warehouses`, `_transit_map`, `busy_as_conflict`, `assert_facility`, `confirm_simple`, `cancel`, `_touch`;
- `app/services/inventory_receipt_service.py`: `attach_derived`, `_received_elsewhere`, `_create_complementary` (the receipt line shape);
- `app/services/sales_quote_service.py`: `email_quote`, `quote_email_message`, `_recipients_context`;
- `app/api/v1/endpoints/sales_quotes.py`: the `/document` and `/email` routes, and the 503 when mail is not configured;
- `app/services/print_contexts.py`: `header_context`, `sales_quote_context`;
- `app/rendering/templates/sales_quote.html`;
- `app/services/incidences.py`;
- `app/services/price_list_service.py` (the cost list is read-only through the API);
- `migrations/027_inventory_count.sql` (DDL style);
- `tests/integration/inventory_seed.py`, which includes `purchase_order(...)`.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Write `migrations/028_purchasing.sql` and `migrations/028_purchasing_rollback.sql` per data-model.md and research R12. Follow the header comment and idempotent style of `migrations/027_inventory_count.sql`.
  1. **`purchase_request`:** `ADD COLUMN IF NOT EXISTS approver INT NULL` with FK `purchase_request_approver_fk` to employee, and `approval_time DATETIME NULL`.
  2. **`purchase_order_detail`:** `KEY purchase_order_detail_request_detail_idx (purchase_request_detail)` and FK `purchase_order_detail_request_detail_fk`.
  3. **`CREATE TABLE IF NOT EXISTS product_cost_change`:**
     - columns per data-model.md;
     - keys `(product, product_cost_change_id)` and `(receipt)`;
     - FKs to product, inventory_receipt, purchase_order_detail, employee and itself (`reverted_change`).

  The rollback reverses all three, the table first.

  **Do not apply it to mbe_dev**; T033 does, after confirmation.
- [X] T002 After T001, update the models:
  - in `app/models/purchases.py`, add `approver` and `approval_time` to `PurchaseRequest`, and a new `ProductCostChange` matching migration 028 column for column, in the style of the file;
  - add `product_cost_change` and the new columns to `docs/data-dictionary.md` (API-only, migration 028);
  - run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py tests/unit/test_migrate.py -q`.
- [X] T003 [P] Create `app/schemas/purchases.py` with every request and response of contracts/purchasing.md:
  - **Requests:**
    - `PurchaseRequestCreate`, `PurchaseRequestUpdate`;
    - `RequestLineAdd` (quantity > 0 with 2 decimals, `product_name` ≤ 250);
    - `RequestLineUpdate`, `ToPurchaseSet`;
    - `RejectRequest` (reason 1–500 characters, not blank once stripped);
    - `BulkApprove` (1–100 ids) and `BulkApproveResponse`;
    - `PurchaseRequestSummary`, `PurchaseRequestResponse`, `PurchaseRequestLine`.
  - **POs:**
    - `PurchaseOrderCreate`, `PurchaseOrderUpdate` (the estimated receipt date is not before today, using `app.core.clock`);
    - `OrderLineAdd`, `OrderLineUpdate` (quantity > 0 with 4 decimals; price ≥ 0 with 7 decimals; tax rate 0 ≤ rate < 1 with 4 decimals);
    - `AddRequestLines`, `ToPurchaseCreate`;
    - `PurchaseOrderSummary`, `PurchaseOrderResponse`, `PurchaseOrderLine`, `SkippedLine`, `GeneratedReceipt`;
    - `ToPurchaseGroup` and `ToPurchaseLine`;
    - `PurchaseOrderEmailRequest`, which extends `QuoteEmailRequest` with `message: str | None` (≤ 1,000);
    - `CostChange`.

  Comments go through the shared `Comment` type. Write the static-rule tests first in `tests/unit/test_purchase_schemas.py`, one rejected value per rule.

---

## Phase 2: Foundational

- [X] T004 [P] Write `tests/unit/test_purchase_states.py` first, then add the pure state helpers:
  - in `app/services/purchase_request_service.py`: `request_status(completed, approved, cancelled)`, with NULL as false (research R1, all five flag combinations in the data, including completed + cancelled);
  - in `app/services/purchase_order_service.py`: `order_status(...)`, reusing `derive_status`;
  - in each module, a `*_status_clause(value)` for list filters; an unknown value gives 422.
- [X] T005 [P] Extend `tests/integration/inventory_seed.py` with:
  - `purchase_request(db, *, warehouse, lines, state='draft', serial=None)`, writing rows directly in any state;
  - a supplier helper, if none exists;
  - a cost helper: `cost(db, product, price)` writes the list-0 row.

  Check that the existing `purchase_order(...)` seed can take lines with a request-line link, a price and a warehouse; extend it if not. Extend `tests/integration/test_inventory_seed.py` for the new helpers.

**Checkpoint:** schema, models, states and seeds exist. Nothing is routed yet.

---

## Phase 3: User Story 1 — A store requests goods and a supervisor approves (P1) 🎯 MVP

**Goal**: request drafts, lines, submit with a serial, approve, reject, mark, cancel, lists and the approval queue.

**Independent Test**: spec US1. Create, add two lines, submit (the facility's next serial), approve (approver and time); another request is rejected with a reason and is a draft again with an incidence.

- [X] T006 [P] [US1] Write `tests/unit/test_purchase_requests.py` first, covering T009's pure helpers:
  - `next_request_serial(highest)`;
  - `submit_refusal(status, lines)`: not a draft, no lines, or a zero quantity;
  - `below_minimum_warning(code, quantity, min_order_qty)`: none when the minimum is 0 or 1, or when at or above it;
  - `default_quantity(min_order_qty)`: the minimum when it is at least 1, otherwise 1 (259 purchasable products have a minimum of 0; FR-002);
  - `facility_move_refusal(serial, old_facility, new_facility)`, for FR-002;
  - `approve_refusal(status)`, `reject_refusal(status)`, `cancel_refusal(status, lines_on_live_po)`, `mark_refusal(status, to_purchase, on_live_po)` (marking or unmarking is refused unless the request is submitted or approved, FR-008 and FR-009; unmarking a line on a live PO is refused).
- [X] T007 [P] [US1] Write `tests/integration/test_purchase_request_flow.py` first, covering US1 scenarios 1–8 and the matching edge cases:
  - **Submit:** serial = the facility's max + 1, across two warehouses of one facility; resubmitting after a reject keeps the serial; a refused submit takes no number.
  - **Facility:** after a serial, moving the header to another facility's warehouse gives 409, and to one of the same facility is allowed.
  - **Approve and reject:** approve stores the approver and time; reject writes a source-4 incidence with the reason, clears the approver, and returns the request to draft.
  - **Bulk approve:** reports each id approved or refused with its reason.
  - **Cancel:** refused when a line is on a non-cancelled PO; allowed when the PO line is on a cancelled PO.
  - **Lines:** a new line is stored with `to_purchase = 1`, `customer = settings.default_customer_id` and `Accepted = 0` (FR-008, FR-013; the column default of `to_purchase` is 0); marking a line of a draft or cancelled request gives 409; unmarking a line on a live PO is refused; FR-010's live PO fields are shown; the line warehouse defaults to the header's, the name to the product's, and the quantity to the minimum order quantity, or 1 when that minimum is 0; a non-purchasable product gives 422; an in-transit warehouse gives 422.
  - **Lists:** by default the caller's own, without cancelled; `all=true`; every filter; the queue is oldest first with the facility filter and line counts.
- [X] T008 [P] [US1] Write `tests/api/test_purchase_requests.py` first:
  - every route of the contract with 401, 403 (86, 96, or 20 for marking) and 404;
  - 409 and 422 cases;
  - 403 for a non-administrator editing, submitting or cancelling another facility's request, while approving, rejecting and marking are allowed (FR-011).
- [X] T009 [US1] Extend `app/services/purchase_request_service.py` (T004 created it with the state helpers):
  - the pure helpers of T006;
  - create (warehouse defaulting to the caller's point-of-sale warehouse via `_pos_warehouse`, 422 if none);
  - header update and line add, update and remove, all draft-only. A header update takes only the request-row lock, so after it the current and the new warehouse's facilities are read with plain reads (the facility rule and FR-002); the pre-read map is for submit and cancel, which take later locks. A new line writes `to_purchase = 1`, `customer = settings.default_customer_id` and `Accepted = 0` explicitly, never relying on the column defaults (FR-008, FR-013);
  - submit per research R2: pre-read `inv._transit_map` and the warehouse → facility map, commit, lock the request row with `populate_existing`, look up its facility in the map (no ordinary read before the next lock), `inv.lock_warehouses([transit of facility])`, then re-read the header warehouse's facility and answer 409 "retry" if it differs from the map, then the checks, then `MAX(serial)` over requests joined to the facility's warehouses;
  - approve and bulk approve (each id in its own locked transaction);
  - reject, with `incidences.record` source `PURCHASE_REQUEST`;
  - marking per research R3: pre-read the line's request before the commit, then lock the request row, then the line row `FOR UPDATE` (the same order as cancel); 404 if the line no longer belongs to that request; then refuse unless the request is submitted or approved, then run the live-PO check;
  - cancel per research R3: pre-read the request's line ids, commit, lock the request row, check the facility from the pre-read map, lock those line rows ascending, then check (no ordinary read before the line locks);
  - `attach` (status, facility, line count, lines with product code and min order quantity, live PO line);
  - lists and the queue per research R11.

  Every write runs inside `inv.busy_as_conflict` and starts like spec 029's `begin_locked`: commit, lock the request row `FOR UPDATE` with `populate_existing`, then check its state (research R3). The facility rule, `inv.assert_facility`, is checked once the row is locked, on edit, submit and cancel only. On submit and cancel the facility is looked up in the warehouse → facility map pre-read before the commit, never with a query between locks; after the last lock the header warehouse's facility is re-read, and a mismatch with the map answers 409 "retry" (research R2, R3). A header edit reads the facilities after its one lock.
- [X] T010 [US1] Create `app/api/v1/endpoints/purchase_requests.py` with the 14 routes of the contract. Declare `/approval-queue` and `/approve` before `/{id}`. Register it in `app/api/v1/router.py` under `/purchase-requests`. Make T006–T008 pass.

**Checkpoint:** requests work end to end and are usable on their own.

---

## Phase 4: User Story 2 — Purchasing turns approved requests into purchase orders (P1)

**Goal**: PO drafts and lines, adding a request's lines, to-purchase groups and creating a PO from one, with one live PO per request line.

**Independent Test**: spec US2. Suppliers A (in two facilities) and B give three groups and three draft POs priced from cost; an unmarked line is on none; creating again adds nothing.

- [X] T011 [P] [US2] Write `tests/unit/test_purchase_orders.py` first, covering T014's pure helpers:
  - `is_pending(request_status, to_purchase, live_po_line)`;
  - `group_key(product_supplier, line_warehouse_facility, header_warehouse_facility)`, using the header's facility when the line warehouse is NULL;
  - `group_lines(rows)`: line count, and the estimated total with missing cost as 0, ordered by facility then supplier name, with the no-supplier group last;
  - `line_values(product, cost_or_none, quantity, warehouse)`: price = cost or 0 with a warning, tax rate = the product's, the fixed defaults MXN, 1, 0 and `tax_included` 0 (FR-021);
  - `supplier_mismatch_warning(...)`;
  - `split_taken(requested_ids, still_pending_ids)` → `(taken, skipped)`.
- [X] T012 [P] [US2] Write `tests/integration/test_purchase_order_lines_flow.py` first, covering US2 scenarios 1, 2, 4, 5 and 6, plus:
  - PO create, update and line CRUD, all draft-only; a non-purchasable product gives 422; an in-transit warehouse gives 422; the price defaults to cost; the line warehouse defaults to the caller's point-of-sale warehouse (FR-021);
  - adding a request's lines: only its pending lines; a supplier mismatch warns; a request that is not approved gives 409;
  - to-purchase: draft, submitted and cancelled requests, unmarked lines and lines on a live PO are excluded; the no-supplier group is listed; creating for `supplier = null` gives 422; a second create for the same group gives 409 (nothing pending);
  - removing a linked PO line, or cancelling the draft PO, makes the request line pending again, and the cancelled PO keeps its lines;
  - SC-001 sequentially: a line taken by one PO is in `skipped` for the next.

  SQLite can't show row-lock races; the concurrent case is quickstart step 4 (T035).
- [X] T013 [P] [US2] Write `tests/api/test_purchase_orders.py` first, covering the create, header update, cancel, line, request-line, to-purchase, list and detail routes: 401, 403 (20), 404, 409 and 422, including 422 for a line with no warehouse when the caller has no point of sale.
- [X] T014 [US2] Extend `app/services/purchase_order_service.py` (T004 created it with the state helpers):
  - the pure helpers of T011;
  - create; header update (draft-only, refusing changes to fixed fields); line add, update and remove (draft-only), with the line warehouse defaulting to the caller's point-of-sale warehouse via `_pos_warehouse`, and 422 "A warehouse is required" when neither is given (FR-021);
  - `add_request_lines` per research R3: pre-read the request's line ids, commit, lock the PO row, lock those line rows ascending, then read the request header and add the pending lines;
  - `to_purchase_groups(facility)` and `create_from_group(supplier, facility)` per research R8: the group's line ids are read before the commit, then those rows are locked ascending, then the pending re-check runs (no ordinary read between the commit and the locks);
  - cancel: draft-only; lines are kept;
  - `attach` (status, supplier name, line counts, subtotal, tax and total, the lines' request links, `skipped` and `warnings`);
  - lists per research R11.

  Every write runs inside `inv.busy_as_conflict` and starts by committing and locking the PO row `FOR UPDATE` with `populate_existing`, then checking it is a draft, so no edit can land after a concurrent confirm (research R3, R4).
- [X] T015 [US2] Create `app/api/v1/endpoints/purchase_orders.py` with these contract routes:
  - list, create, get, update;
  - line add, update and delete;
  - `request-lines`, cancel;
  - `GET` and `POST /to-purchase`.

  Declare `/to-purchase` and `/cost-changes` before `/{id}`. Register it in `app/api/v1/router.py` under `/purchase-orders`. Make T011–T013 pass.

**Checkpoint:** POs can be built by hand and from requests. They can't be confirmed yet.

---

## Phase 5: User Story 3 — A buyer issues a purchase order and the stores receive it (P1)

**Goal**: confirm creates one draft receipt per destination warehouse with the pending quantities, once. Receipt progress appears on the PO.

**Independent Test**: spec US3. A PO with lines to W1 (2 products) and W2 (1) confirms into two linked draft receipts; a second confirm gives 409 and there are still two.

- [X] T016 [P] [US3] Extend `tests/unit/test_purchase_orders.py` first with:
  - `confirm_refusal(status, supplier, lines)`;
  - `zero_price_warnings(lines)`;
  - `receipts_to_generate(lines, stockable, held)` → `{warehouse: [(line, pending)]}`, ascending: non-stockable lines left out, warehouses with nothing pending left out, pending = ordered − held;
  - `progress(lines, received)` → received and pending, floored at 0 (FR-030).
- [X] T017 [P] [US3] Write `tests/integration/test_purchase_order_confirm_flow.py` first, covering US3 scenarios 1–6:
  - confirm creates receipts in each warehouse's facility, linked to the PO, with lines linked to the PO lines, `quantity` = pending and `quantity_ordered` = ordered;
  - a second confirm gives 409 and no new receipt;
  - after confirm, every header and line edit gives 409, and so does cancel;
  - a confirmed receipt shows in the PO detail's `quantity_received` and `quantity_pending`;
  - spec 029's complementary receipt still covers the remainder;
  - moving a PO receipt's warehouse gives 409 (research R7), and a hand-made receipt can still move;
  - a PO with only non-stockable lines confirms with no receipt;
  - a complementary receipt of a PO with a non-stockable line leaves that line out, and the PO shows no receiving progress for it.
- [X] T018 [P] [US3] Extend `tests/api/test_purchase_orders.py` first with confirm: 401, 403, 404, 409 (not a draft, no lines, a zero quantity, no supplier) and the `receipts` in the response. Extend `tests/api/test_inventory_receipts.py` with the warehouse-move 409.
- [X] T019 [US3] Implement `confirm` in `app/services/purchase_order_service.py` per research R4: commit, lock the PO row, check, read what non-cancelled receipts hold with one grouped query, create the draft receipts and lines ascending by warehouse, then set `completed`, the updater and the time.
  - Add the per-line progress to `attach`, reusing the grouped query shape of `inventory_receipt_service._received_elsewhere`; non-stockable lines get `quantity_received` and `quantity_pending` null.
  - In `inventory_receipt_service.complementary_receipt`, leave non-stockable order lines out of the remaining lines, so its draft can be confirmed (it is refused 422 otherwise; mbe_dev has 53 such PO lines).
  - In `app/services/inventory_receipt_service.update`, refuse a warehouse change on a receipt with `purchase_order` with 409 "A purchase order's receipt keeps its warehouse"; the check runs against the locked row inside `inv.update_header` or right before it.
  - Add the confirm route. Make T016–T018 pass.

**Checkpoint:** a PO flows into receipts. Cost is not yet updated.

---

## Phase 6: User Story 4 — Receiving goods updates average cost (P1)

**Goal**: averaging on receipt confirm, restore or warn on cancel, and the cost history.

**Independent Test**: spec US4. 10 on hand at 100, plus 10 received at 120, gives a cost of 110 and one history row with Q, R and U.

- [X] T020 [P] [US4] Write `tests/unit/test_purchase_cost.py` first, covering T023's pure helpers:
  - `unit_cost(price, discount)`, ignoring `tax_included`;
  - `average(q, c, r, u)`: Q < 0 counts as 0, rounding half up to 4 decimals (R > 0 always, so Q + R > 0);
  - `apply_lines(lines, on_hand, costs)`: lines in id order, two lines of one product averaged one after the other, U = 0 skipped with a warning, lines without a PO line ignored; it returns the changes and the final cost per product;
  - `restore_plan(receipt_changes, latest_change_by_product)` → `(restores, warnings)`: restore only when the latest change is this receipt's and not a restore, to the previous cost of the receipt's first change for that product.
- [X] T021 [P] [US4] Write `tests/integration/test_purchase_cost_flow.py` first, covering US4 scenarios 1–5 and SC-004:
  - the formula against seeded stock across two facilities and in transit;
  - a receipt without a PO, and a hand-added line on a PO receipt, leave cost unchanged (the hand-added product must not already be on the receipt, or `add_line` merges it into the linked line);
  - a product with no cost row gets one;
  - two lines of one product;
  - cancelling the latest receipt restores the cost and writes a restore row; cancelling an earlier one after a later receipt warns and keeps the cost;
  - every history row reproduces `new_cost` from its own Q, C, R and U;
  - `GET /purchase-orders/cost-changes?product=` lists them, newest first;
  - writes to list 0 through the price-list API are still refused (spec 004 FR-005a; `tests/integration/test_cost_price_list.py` stays green).
- [X] T022 [P] [US4] Extend `tests/api/test_inventory_receipts.py` first: confirm and cancel responses carry the cost warnings. Extend `tests/api/test_purchase_orders.py` with `cost-changes`: 401, 403, 422 without `product`, and 404 for an unknown product.
- [X] T023 [US4] Create `app/services/purchase_cost_service.py` per research R5 and R6:
  - the pure helpers of T020;
  - `lock(db, receipt_id)`: a locking read of the linked products, then the list-0 rows `FOR UPDATE` ascending;
  - `apply(db, receipt, lines, current)`: a grouped company-wide on-hand query, then the cost row update or insert, the staged `ProductCostChange` rows, and the warnings;
  - `lock_restore(db, receipt_id)` and `restore(db, receipt, current)`;
  - `history(db, product, skip, limit)`.
- [X] T024 [US4] Wire the cost step into `app/services/inventory_documents.py` per research R7:
  - in `confirm_simple`, when `kind is RECEIPT and document.purchase_order is not None`, call `purchase_cost_service.lock` right after `begin_locked`, and `apply` after the checks and before `post_movement`;
  - in `cancel`, for a completed RECEIPT with a PO, call `lock_restore` after `begin_locked` and `restore` with the reversal;
  - keep the warnings on `document.__dict__['cost_warnings']`;
  - in `app/services/inventory_receipt_service.attach_derived`, `warnings` = the over-receipt warnings plus the cost warnings;
  - add the `cost-changes` route.

  Spec 029's and 030's tests (`tests/unit/test_inventory_*.py`, `tests/integration/test_inventory_*_flow.py`) must stay green. Make T020–T022 pass.

**Checkpoint:** every P1 story is done.

---

## Phase 7: User Story 5 — The buyer prints and emails the order to the supplier (P2)

**Goal**: the PO PDF, and email with a source-5 incidence.

**Independent Test**: spec US5.

- [X] T025 [P] [US5] Write `tests/unit/test_purchase_order_print.py` first:
  - `order_totals(lines)`: subtotal, tax per line rate, and total, with tax always added;
  - `order_email_message(...)`: subject, body with the optional message, and the attachment name `orden-compra-{id:08d}.pdf`.
- [X] T026 [P] [US5] Write `tests/integration/test_purchase_order_document_flow.py` first, in the style of `tests/integration/test_quote_document_render.py` and `test_fiscal_email.py`:
  - the PDF renders for a confirmed PO and shows the supplier, lines, warehouses and totals;
  - the header facility is the lowest line's warehouse's facility;
  - email sends with the attachment and records the source-5 incidence after the send;
  - a `MailError` gives 502 with no incidence; mail not configured gives 503.
- [X] T027 [P] [US5] Extend `tests/api/test_purchase_orders.py` first: `/document` and `/email` with 401, 403 (Read and Update on 20), 404, and 409 for a draft or cancelled PO (FR-043).
- [X] T028 [US5] Implement it:
  - `app/rendering/templates/purchase_order.html`, letter size, modelled on `sales_quote.html`;
  - `print_contexts.purchase_order_context(db, order)` per research R9;
  - `purchase_order_service.email_order` mirroring `sales_quote_service.email_quote`;
  - the `/document` and `/email` routes in `app/api/v1/endpoints/purchase_orders.py`, mirroring `sales_quotes.py`, including the 503.

  Make T025–T027 pass.

**Checkpoint:** every story is done.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T029 [P] Update `docs/specs/05-purchases.md` §1–4 to describe what the new MBE does, keeping the document's structure:
  - request states, the serial on submit, approval with approver, reject as an incidence, marking, the facility rule;
  - PO states, draft-only edits, to-purchase per supplier and facility, one live PO per request line, receipts on confirm;
  - average cost with restore on cancel, and the history;
  - the PDF and email.

  Drop PO approval (95), currency, discount and invoice editing, and repricing, which are out of scope.
- [X] T030 [P] Update `CHANGELOG.md` `[Unreleased]` in the style of spec 030's entries:
  - **Added:** requests, POs, to-purchase, average cost and its history, the PO PDF and email, migration 028;
  - **Changed:** receipt confirm and cancel update cost for receipts with a PO; receipts with a PO keep their warehouse; complementary receipts leave non-stockable PO lines out.
- [X] T031 Check that `tests/integration/test_no_endpoint_returns_500.py` (and any route inventory test) covers the 29 new routes, and extend it if it lists routes by hand.
- [ ] T032 Run `uv run ruff check app/ migrations/ tests/`, `uv run ruff format --check` on the changed files, and `uv run pytest -q`. Everything must be green.
- [ ] T033 **Ask the user first; DDL.** Apply migration 028 to mbe_dev with `uv run python -m app.db.migrate`. Check the two columns, the index and FK, and the new table. Record the result in spec.md under "Verification".
- [ ] T034 **Ask the user first.** Deploy to the test instance (`/var/www/mbe-api-test`, `mbe-api-test`) as mictlanix@, restarting as root. Only after T033.
- [ ] T035 **Ask the user first; writes to mbe_dev.** Run quickstart steps 3–8:
  - concurrent submits (SC-002);
  - concurrent ordering of the same request lines (SC-001);
  - a concurrent double PO confirm (SC-003);
  - cost averaging, including two receipts of one product in two facilities at once, then restore on cancel (SC-004, US4 scenario 4);
  - the 212-line timing (SC-005);
  - PDF and email to an address the user names (SC-006).

  Agree the warehouses and the cleanup with the user first. Record the ids and results in spec.md under "Verification", then remove scratch scripts from xolotl.

---

## Dependencies & Execution Order

- **Setup:** T001 → T002; T003 in parallel.
- **Foundational:** T004 ∥ T005, after T002. Phase 2 blocks the stories.
- **US1** (T006–T010) needs only Phases 1–2.
- **US2** (T011–T015) needs US1's requests for its request-line paths. PO drafts and hand lines don't, so T011, T013 and T014's hand-line part can start with US1.
- **US3** (T016–T019) needs US2's PO service.
- **US4** (T020–T024) needs US3 to generate PO receipts. T021 can seed receipts with a PO directly, so T020–T023 can run in parallel with US3; T024 comes after T019, since both touch the receipt service.
- **US5** (T025–T028) needs US3's confirmed POs. It runs in parallel with US4.
- **Polish:** after every story. T033 → T034 → T035 are each gated on the user.

### Parallel opportunities

- T001 ∥ T003; T004 ∥ T005
- T006 ∥ T007 ∥ T008
- T011 ∥ T012 ∥ T013
- T016 ∥ T017 ∥ T018
- T020 ∥ T021 ∥ T022, and T023 alongside US3
- T025 ∥ T026 ∥ T027; US5 ∥ US4
- T029 ∥ T030
- Shared files: `router.py` (T010, T015), `purchase_order_service.py` (T014, T019, T028), `purchase_orders.py` endpoints (T015, T019, T024, T028), `tests/api/test_purchase_orders.py` (T013, T018, T022, T027), `tests/api/test_inventory_receipts.py` (T018, T022). Serialise the edits to each.

---

## Implementation Strategy

1. **MVP:** Setup, Foundational and US1 (requests and approval).
2. **Then:** US2 (POs from requests), US3 (confirm and receipts) and US4 (average cost), the P1 set that replaces purchasing at cutover.
3. **Then:** US5 (PDF and email).
4. **Polish:** docs, the CHANGELOG and the suite. Then, each after confirmation: the migration, the deploy and the live checks.
5. **PR:** it must close mictlanix/mbe-roadmap#68 and #70, and say "Part of mictlanix/mbe-roadmap#7".
