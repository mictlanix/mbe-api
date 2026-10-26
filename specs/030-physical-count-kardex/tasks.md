---
description: "Task list for the physical inventory count, stock queries and kardex"
---

# Tasks: Physical Inventory Count, Stock and Kardex

**Input**: design documents in `specs/030-physical-count-kardex/`: plan.md, spec.md, research.md (R1–R8), data-model.md, contracts/count-stock-kardex.md and quickstart.md.

**Tests**: required. Constitution v1.2.0 makes them mandatory and test-first; every test task comes before its implementation and must be run and seen to fail first.

**Working tree**: `/Users/eddy/Projects/mictlanix/mbe-api-030`.
- Set `BUSINESS_TIMEZONE=America/Mexico_City` for pytest and python, since there is no `.env`.
- Before each commit, check `git branch --show-current` and run `uv run ruff check app/ migrations/ tests/`.
- No commit carries a Co-Authored-By line. Delegated work goes to agents with absolute paths and no commits.

**Reference code**:
- `app/services/inventory_documents.py` (spec 029: Kind, begin_locked, next_serial, busy_as_conflict, list_documents, cancel, `_lines`, `_touch`, `attach`);
- `app/services/inventory_transfer_service.py` (outstanding figures);
- `app/services/stock_ledger.py` (`on_hand_by_warehouse`, `reserved_by_warehouse`);
- `migrations/023_commission_periods.sql` (CREATE TABLE style);
- `tests/integration/inventory_seed.py`.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Write `migrations/027_inventory_count.sql` and `migrations/027_inventory_count_rollback.sql` per data-model.md and research R8:
  - the header comment block and idempotent style of `migrations/023_commission_periods.sql`;
  - named keys, including UNIQUE `(facility, serial)` and UNIQUE `(count, product)`, and FKs to facility, warehouse, employee, product and the header;
  - the rollback drops the detail table first.

  **Do not apply it to mbe_dev**; T024 does, after confirmation.
- [X] T002 After T001, in `app/models/inventory.py`, add `InventoryCount` and `InventoryCountDetail` matching migration 027 column for column, in the style of the existing inventory models.
  - In `app/enums.py`, add `TransactionType.PHYSICAL_COUNT = 12` and `DELIVERY_PICKUP = 13`, with comments: 12 is posted by counts and replaces type 8 for new counts; 13 is a counter pickup referencing the delivery order; 10 is now the itinerary only. Update the enum docstring.
  - Add both tables to `docs/data-dictionary.md` §7 (API-only, migration 027).
  - Run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py tests/unit/test_migrate.py -q`.
- [X] T003 [P] In `app/schemas/inventory.py`, add the schemas of contracts/count-stock-kardex.md:
  - **counts:** `CountCreate`, `CountUpdate`, `CountLinesSet {lines: 1..2000 of {product?, product_code?, quantity ≥ 0}}` (exactly one of `product` and `product_code`), `CountLineUpdate {quantity ≥ 0}`, `CountConfirm {include_uncounted: bool = False}`, and `InventoryCountResponse` with lines whose `quantity_on_hand` and `difference` are nullable;
  - **confirm response:** `InventoryCountConfirmResponse` with `negative_available` and `corrected_by_other_counts`, and `InventoryCountSummary`;
  - **reads:** `StockRow`, `ProductStock`, `KardexRow`, `KardexDocument` and `Kardex`.

  Quantities have at most 4 decimals. Write the static-rule tests first in `tests/unit/test_inventory_schemas.py`, extending it with one rejected value per rule.

---

## Phase 2: Foundational

- [X] T004 Extend `app/services/inventory_documents.py`:
  - add a `cancellable_when_completed: bool = True` field to `Kind`, with RECEIPT, ISSUE and TRANSFER unchanged;
  - add a `COUNT` kind: header `InventoryCount`, line `InventoryCountDetail`, `line_fk='count_id'` (the `count` column is mapped to the attribute `count_id`; research R1), `id_attr='inventory_count_id'`, `line_id_attr='count_detail_id'`, `warehouses=('warehouse',)`, `transit=False`, `transaction_type=PHYSICAL_COUNT`, `system_object=PHYSICAL_COUNT_ADJUSTMENT`, `label='Count'`, `cancellable_when_completed=False`;
  - in `cancel`, after the facility check, a completed document whose kind is not cancellable gives 409 "A confirmed count cannot be cancelled".

  Write the tests first: in `tests/unit/test_inventory_documents.py`, the 409 for a completed COUNT; in `tests/unit/test_inventory_locks.py`, that it comes after the locks and the facility check. Spec 029's tests must stay green.
- [X] T005 Extend `tests/integration/inventory_seed.py` with `count(db, warehouse, lines)`, which writes a draft count directly, and with a product that has several labels (for SC-005).

**Checkpoint:** the count tables, the type numbers and the count kind exist. Spec 029 is unchanged.

---

## Phase 3: User Story 1 — The warehouse counts its stock and corrects it (P1) 🎯 MVP

**Goal**: count drafts, blind lines and batches, then confirm with the differences posted as type 12, plus both warnings.

**Independent Test**: spec US1. With 10 on hand, count 7 and confirm: the count gets a serial, stock is 7, there is one type-12 entry of −3, and a second confirm is refused.

- [X] T006 [P] [US1] Write `tests/unit/test_inventory_count.py` first, covering the pure helpers of T009:
  - `validate_batch(items, products_by_id, products_by_code)`: unknown, not stockable, repeated, and both or neither of id and code each produce an error with the index; a clean batch gives none;
  - `apply_batch(existing_lines, items)` replaces existing quantities and returns the new lines, idempotent when repeated;
  - `differences(lines, on_hand)` gives `(on_hand, difference)` per line, and skips zero differences for posting;
  - `entry_rows(count_id, warehouse, differences, now)` signs each row (inbound when positive, outbound when negative, never zero) with source 12;
  - `uncounted_lines(on_hand_by_product, counted_products, stockable)` lists non-zero balances not on the count, including negative ones;
  - `negative_available(lines, reserved)`;
  - `overlaps(this_products, other_counts)` keeps only other completed counts of the warehouse confirmed at or after this one's creation time (`>=`, research R2).
- [X] T007 [P] [US1] Write `tests/integration/test_inventory_count_flow.py` first, covering US1 scenarios 1–6 and these edge cases:
  - **Confirm:** gives serial = max + 1 per facility; type-12 entries only for non-zero differences, reference = the count id; lines show on hand and difference; SC-001 (on hand equals counted, and entries sum to the differences).
  - **Blind:** a draft's lines and responses carry no on-hand or difference (FR-003a).
  - **Batches:** a batch replaces quantities; a repeated batch is idempotent; a batch with a bad entry gives 422 listing every bad entry and applying nothing; quantity 0 is allowed.
  - **Uncounted as zero:** without it, other products are untouched; with it, they are added at 0 and adjusted. Negative balances are corrected up to 0.
  - **Repeats and cancel:** a second confirm gives 409 and posts nothing; cancelling a confirmed count gives 409; cancelling a draft posts nothing.
  - **Facility:** a non-administrator of another facility confirming or cancelling gets 403.
  - **Warnings:** `negative_available` when the count is below what is reserved; `corrected_by_other_counts` when another count of the warehouse confirmed at or after this one's creation time (FR-010a).
  - **Discontinued product:** a product discontinued after it went on the draft still confirms.
  - **Validation:** an in-transit warehouse gives 422, and a non-stockable product 422.
  - **Empty with uncounted:** an empty count confirmed with `include_uncounted` on a warehouse with nothing non-zero gives 409 and uses no serial.
  - **List (FR-009):** the caller's point-of-sale warehouse plus their own counts by default; everything with the delete right on 74; the state and warehouse filters; newest first.
- [X] T008 [P] [US1] Write `tests/api/test_inventory_counts.py` first: the 9 routes of the contract, with 401, 403 (each right on object 74), 404, 409 and 422, and services patched as in `tests/api/test_inventory_receipts.py`.
- [X] T009 [US1] Create `app/services/inventory_count_service.py`:
  - the pure helpers of T006 and `attach_derived` (blind for drafts);
  - `create`/`update`/`list`/`cancel` through `inventory_documents` with `COUNT`;
  - `set_lines(db, id, items, current)`: `begin_locked` (document only), `assert_editable`, one `IN` query per batch for products by id and by code, `validate_batch` (422 with `errors`), then apply;
  - `update_line` and `remove_line` with replace semantics;
  - `confirm(db, id, include_uncounted, current)` per research R2:
    1. `busy_as_conflict` and `begin_locked(stock=True)`;
    2. facility check, then editable check;
    3. `next_serial`;
    4. on hand by a grouped query, in chunks of 1,000 ids, or the whole warehouse with `include_uncounted` (adding the uncounted lines), **then** the lines check: no lines at this point gives 409 (research R2);
    5. bulk `insert(LotSerialTracking)` executemany in chunks of 1,000;
    6. bulk line update;
    7. the warnings;
    8. the serial set last, then commit.
- [X] T010 [US1] Create `app/api/v1/endpoints/inventory_counts.py` with the 9 routes and rights per the contract. Register it under `/inventory-counts` in `app/api/v1/router.py`. Make T006–T008 pass.

**Checkpoint:** counts work end to end.

---

## Phase 4: User Story 2 — The opening count loads a whole warehouse (P1)

**Goal**: a 15,000-line count is enterable in batches and confirms in one go, fast enough.

**Independent Test**: spec US2.

- [X] T011 [US2] Extend `tests/integration/test_inventory_count_flow.py` first:
  - 8 batches of 2,000 (15,000 lines in all) build the count, and a repeated batch changes nothing;
  - confirm posts one entry per differing product and leaves every product at its counted quantity;
  - a failure injected in the middle of posting (patch the bulk insert to raise on its third chunk) rolls back everything, with no entries, no serial and the count still a draft.

  Record the SQLite confirm time in the test output, without asserting it; SC-003 is measured on MariaDB in T026.
- [X] T012 [US2] Fix whatever T011 exposes in `inventory_count_service.confirm` (chunking, bulk statements, rollback). Record in the commit message if nothing needed changing.

**Checkpoint:** the opening count path is proven at size.

---

## Phase 5: User Story 3 — Anyone authorised sees stock per warehouse (P1)

**Goal**: `/stock` and `/stock/products/{id}`.

**Independent Test**: spec US3. With 10 on hand, 3 reserved and 2 incoming, the query shows 10, 3, 7 and 2.

- [X] T013 [P] [US3] Write `tests/unit/test_stock_queries.py` first (constitution: a branching service needs unit tests), for T015's pure helpers: `merge_stock(on_hand, reserved, incoming)` per product with available, `keep_row(row, include_zero)`, `split_transit(rows, transit_warehouses)` for the product view, and sorting and paging. Then write `tests/integration/test_stock_queries.py`, covering US3 scenarios 1–3:
  - **Figures:** on hand, reserved (type 11 only; stale types are ignored), available and incoming (outstanding lines of transfers in transit to the warehouse);
  - **Filters:** `q` on code and name, `include_zero`, and paging sorted by code;
  - **Product view:** every warehouse with a non-zero figure, plus `in_transit` per facility transit warehouse;
  - **Labels:** a product with several labels counts once (FR-022);
  - **Errors:** an in-transit warehouse as the `/stock` target gives 422, and an unknown warehouse or product 404.
- [X] T014 [P] [US3] Write `tests/api/test_stock.py` first: both routes, with 401, 403 (Read on 67), 404 and 422, and `limit` ≤ 500.
- [X] T015 [US3] Create `app/services/stock_query_service.py` per research R5 (the pure helpers of T013, then grouped queries merged by product, with the product view's third query for incoming), and `app/api/v1/endpoints/stock.py`. Register it under `/stock`. Make T013 and T014 pass.

**Checkpoint:** stock is visible with reservations and incoming transfers.

---

## Phase 6: User Story 4 — Anyone authorised traces a product's movements (P2)

**Goal**: `/kardex` with balances and resolved documents. Counter pickups post type 13.

**Independent Test**: spec US4.

- [X] T016 [P] [US4] Write `tests/unit/test_kardex.py` first:
  - `kardex_rows(opening, entries)`: running balance, totals in and out, and closing;
  - `range_bounds(from, to)`: the end inclusive of its whole day; over 366 days, or `to` before `from`, gives 422;
  - `resolve(entries, documents_by_type)`: every type of research R6's table, with type 8 reference 0 unresolved, and a type-2 entry whose product is not on that refund's lines (`customer_refund_detail`) unresolved.
- [X] T017 [P] [US4] Write `tests/integration/test_kardex_flow.py` first, covering US4 scenarios 1–4 and SC-004:
  - movements from a receipt, an issue, a transfer (dispatch, receive, close, cancel), a count (type 12), a customer refund (type 2) and a counter pickup (type 13) each resolve with facility and serial;
  - an itinerary entry (type 10) resolves to the itinerary id;
  - a legacy type-8 entry resolves to its receipt;
  - an empty range gives opening equal to closing;
  - the closing balance equals the ledger sum up to the range end.
- [X] T018 [P] [US4] Write `tests/api/test_kardex.py` first: 401, 403 (Read on 32), 404 for an unknown product or warehouse, and 422 for the range.
- [X] T019 [US4] Change the counter pickup posting in `app/services/delivery_order_service.py` (around line 886) to `TransactionType.DELIVERY_PICKUP` (FR-034). First add or extend a test in the existing delivery-order pickup tests asserting the pickup's ledger entry has source 13. Itinerary postings keep 10.
- [X] T020 [US4] Create `app/services/kardex_service.py` per research R6 (opening sum, ranged entries ordered by `(date, id)`, batched resolution per type), and `app/api/v1/endpoints/kardex.py`. Register it under `/kardex`. Make T016–T018 pass.

**Checkpoint:** every story is done.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T021 [P] Update `docs/specs/04-inventory.md` §4–6 to describe what the new MBE does:
  - the count document, blind, with batches, uncounted-as-zero, type 12, the warnings and no cancel after confirm;
  - stock queries;
  - the kardex;
  - types 12 and 13.

  Drop InventoryAdjustments (93), ZeroOut and the lot, serial and expiry views, which are out of scope. Keep the document's structure.
- [x] T022 [P] Update `CHANGELOG.md` `[Unreleased]` in the style of spec 029's entries:
  - **Added:** counts, stock and kardex, and migration 027;
  - **Changed:** counter pickups post type 13.
- [X] T023 Run `uv run ruff check app/ migrations/ tests/`, `uv run ruff format --check` on the changed files, and `uv run pytest -q`; everything must be green.
- [ ] T024 **Ask the user first; DDL.** Apply migration 027 to mbe_dev with `uv run --env-file ../mbe-api/.env python -m app.db.migrate`, check both tables, and record the result in spec.md under "Verification".
- [ ] T025 **Ask the user first.** Deploy to the test instance (`/var/www/mbe-api-test`, `mbe-api-test`) as mictlanix@, restarting as root. Only after T024.
- [ ] T026 **Ask the user first; writes to mbe_dev.**
  - Run quickstart steps 3–5: SC-005 stock against the ledger (read only), SC-004 kardex (read only), and a small count with a concurrent double confirm (SC-001, SC-002).
  - Run step 6, SC-003 at 15,000 lines, only on a warehouse the user names, with the restoration agreed first.

  Record the ids and results, then remove scratch scripts from xolotl.

---

## Dependencies & Execution Order

- **Setup:** T001 → T002; T003 in parallel.
- **Foundational:** T004, then T005. Phase 2 blocks the stories.
- **US1:** T006–T010.
- **US2** (T011–T012) needs US1.
- **US3** (T013–T015) needs only Phases 1–2, so it runs in parallel with US1.
- **US4** (T016–T020) needs Phases 1–2 and, for its count case, US1's count (T017 can seed a type-12 entry directly if US1 isn't done). It runs in parallel with US1 and US3.
- **Polish:** after every story. T024 → T025 → T026 are each gated on the user.

### Parallel opportunities

- T001 ∥ T003
- T006 ∥ T007 ∥ T008
- T013 ∥ T014
- T016 ∥ T017 ∥ T018
- US1, US3 and US4 in parallel after Phase 2. They touch separate files, except `router.py` (one line each, so serialise those edits) and `inventory_schemas` tests (T003 only).
- T021 ∥ T022

---

## Implementation Strategy

1. **MVP:** Setup, Foundational and US1 (counts).
2. **Then:** US2 (scale), US3 (stock) and US4 (kardex and pickups).
3. **Polish:** docs, the CHANGELOG and the suite. Then, each after confirmation: the migration, the deploy and the live checks.
4. **PR:** it must close mictlanix/mbe-roadmap#64 and #66.
