---

description: "Task list for spec 035: supplier sub-panels by privilege, race-safe serials and stock"
---

# Tasks: Supplier Sub-Panels by Privilege, Race-Safe Serials and Stock

**Input**: Design documents from `specs/035-serials-stock-bank-access/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R8), data-model.md, contracts/api.md, quickstart.md

**Tests**: Required. The constitution makes tests mandatory and test-first, and the spec asks for a Products-only test and concurrency proofs. Each story writes its tests first and sees them fail before the code changes.

**Organization**: by user story. US1, US2 and US4 are independent of each other once Phase 2 is done. US3 builds on US2's rewrite of `confirm_order`. US5 adds to US4's migration file.

**Where to work**: the worktree `/Users/eddy/Projects/mictlanix/mbe-api-035`, branch `035-serials-stock-bank-access`. Writes, DDL and cleanup on mbe_dev need confirmation; deploying to dev (:8765, as mictlanix@) does not.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: the user story the task belongs to (US1–US5)

## Lock order every numbering step follows (research R2)

1. pre-reads (scalars, or entities re-read later);
2. `await db.commit()`;
3. own rows `FOR UPDATE` with `populate_existing`: the cash session first on payment paths, then the documents;
4. lines `FOR SHARE` with `populate_existing`, on steps that write stock;
5. facility rows ascending (`documents.lock_facility`);
6. warehouses ascending, one statement (`inventory_documents.lock_warehouses`);
7. ordinary reads, including the plain `MAX` inside `assign_folio`; the serial is set last.

Each step runs inside `inventory_documents.busy_as_conflict(db, detail=…)`. No `db.get` on an object loaded before the commit, no `expire_all`.

---

## Phase 1: Setup

**Purpose**: a green baseline, and the "before" evidence while dev still runs main.

- [X] T001 Run the gates on the untouched branch in `/Users/eddy/Projects/mictlanix/mbe-api-035`: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`; record the pass count
- [X] T002 [P] Record the read-only "before" evidence of quickstart step 1 with the scratchpad `q.sh` helper (MYSQL_PWD, `--skip-ssl`): the `EXPLAIN` of the `lot_serial_tracking` lookup by `source` and `reference` for one confirmed receipt; the count of `customer_payment` rows with `serial = 0` and facility 51's `MAX(serial)`; a checksum over `(customer_payment_id, serial)` of every non-zero payment; and, through dev's API, `GET /suppliers/{id}` as a Products-only user for a supplier with bank accounts. Save the outputs under the scratchpad
- [ ] T003 [P] Write the race scripts in the scratchpad (not the repo), per quickstart: each creates its own test customer and drafts at facility 51 through dev's API with `httpx`, fires its requests concurrently with tokens minted as in spec 034, records the statuses, serials and availability, deletes every row it created in `finally`, and prints the rows left. The race user needs an open cash session at facility 51: a refund cannot be confirmed without one (`customer_refund_service.py:485`), and a payment takes the session lock only when one is open. Reuse the user's open session if there is one and record which, otherwise open one and remove it in `finally`. Refunds use a non-stockable product, so they write no ledger rows; their cleanup deletes the payout payments, any credit notes and the refunds. Scripts: `race_confirm_serials.py` (20 non-stockable orders, SC-003; in the same burst, 5 quotes and 2 refunds opened beforehand on orders the script confirmed and paid, for US2 scenario 2), `race_payments.py` (20 payments, SC-004), `race_last_units.py` (two 5-unit orders on a 5-unit warehouse, plus one order confirmed twice at once, SC-005), `race_order_vs_issue.py` (an order and an inventory issue for the last 5, US3 scenario 2)
- [ ] T004 Ask for confirmation, then run `race_confirm_serials.py` and `race_last_units.py` against dev on main (quickstart step 1, "before"). Expect at least one generic 409 "The request conflicts with existing data" and fewer than 20 serials; both 5-unit orders confirming with availability −5; record the double-confirm outcome. Check the scripts report 0 rows left

---

## Phase 2: Foundational

**Purpose**: the numbering lock helper and the lock-order test harness every numbering story uses.

**⚠️ CRITICAL**: US2, US3 and US4 need this phase.

- [X] T005 Write failing tests in `tests/unit/test_documents.py` for `documents.lock_facility(db, facility)`: it issues `SELECT facility.facility_id … FOR UPDATE` and answers 404 when no row comes back; and for `assign_folio`: the facility lock comes before the `MAX(serial)` read, and the `MAX` is scoped to the facility (keep the existing AsyncMock style)
- [X] T006 In `app/services/documents.py`, split today's facility lock out of `assign_folio` into `async def lock_facility(db, facility: int) -> None` (FOR UPDATE, 404 when missing); `assign_folio(db, model, *, facility)` calls it, then reads the plain `MAX(serial) + 1`. Keep `assign_folio`'s signature. Rewrite its docstring and the module docstring: the precondition (no ordinary read in the transaction before the facility lock; R2), the lock order, and drop the false "no unique index" claim (most tables have one; `customer_payment` and `purchase_request` don't). T005 passes
- [X] T007 Create `tests/unit/test_numbering_locks.py` with its module docstring (spec 035, research R2/R3/R8) and the shared harness: import `FakeSession`, `FakeResult`, `COMMIT` and `ROLLBACK` from `tests.unit.test_inventory_locks`, plus helpers `index_of(log, pattern)` and `assert_order(log, *patterns)` that assert the patterns appear in that order, and `first_plain_select(log)` that finds the first `SELECT` without `FOR UPDATE`/`FOR SHARE` after the commit. Add a 1213 and a 1205 `OperationalError` factory as `test_inventory_locks.py` builds them

**Checkpoint**: `lock_facility` exists; `assign_folio` is unchanged for callers; the harness imports.

---

## Phase 3: User Story 1 - Supplier bank accounts only for those allowed to see them (Priority: P1) 🎯 MVP

**Goal**: the supplier detail keeps its lookup gate; each sub-panel goes only to a caller who may read it (bank accounts 13, contacts 12, addresses 11, agreements 3; admins all); an unreadable link list on a write is ignored (research R1).

**Independent Test**: as a Products-only user, `GET /suppliers/{id}` for a supplier with all four panels returns four empty lists; an administrator gets all four.

### Tests for User Story 1 ⚠️ write first, see them fail

- [X] T008 [P] [US1] Create `tests/unit/test_supplier_panels.py` for the panel rule in `supplier_service`: an administrator reads all four; each privilege alone (13, 12, 11, 3 with the READ bit) opens only its own panel; a privilege row without the READ bit opens nothing; no rows opens nothing; the rule costs one `access_privilege` query
- [X] T009 [P] [US1] Extend `tests/api/test_supplier_sub_panels.py` (reuse its seeding of a supplier with addresses, contacts, bank accounts and agreements, and its user/privilege helpers):
  - Products read only: `GET /suppliers/{id}` returns 200 with `code` and `name` and `[]` for `bank_accounts`, `contacts`, `addresses`, `agreements` (SC-001);
  - Suppliers + Bank Accounts + Addresses read, no Contacts: bank accounts, addresses and agreements filled, `contacts` empty (US1 scenario 2);
  - administrator: all four filled;
  - update as an editor with Suppliers update but no Bank Accounts read, sending `bank_accounts: []`: the supplier's bank account links are unchanged in the DB (SC-002); the same with a non-empty list of other ids: unchanged;
  - create as that editor with a `bank_accounts` list: the new supplier has no bank account links;
  - `POST` and `PUT` responses are filtered like `GET` (US1 scenario 5);
  - `GET /suppliers` items and a product's embedded `supplier` still carry no sub-panels (FR-004)

### Implementation for User Story 1

- [X] T010 [US1] In `app/services/supplier_service.py`, add the panel rule: a function returning the set of readable panels for a user, with one `access_privilege` query over objects 13, 12, 11, 3 checking the READ bit (`AccessRight.READ`), and all four for an administrator (reuse how `app/api/deps.py` decides admin and reads privileges). `_attach_links` takes that set, queries only the readable panels and leaves the rest as `[]`. `get_supplier`, `create_supplier` and `update_supplier` take the caller (or the readable set); on create and update, set `addresses`, `contacts` and `bank_accounts` to `None` for an unreadable panel before `_set_links`, so update leaves those links unchanged and create writes none
- [X] T011 [US1] In `app/api/v1/endpoints/suppliers.py`, pass the current user to the service from `GET /suppliers/{supplier_id}`, `POST /suppliers` and `PUT /suppliers/{supplier_id}`. Gates stay as they are (`_LOOKUP` on GET; Suppliers create/update on writes). T008 and T009 pass
- [X] T012 [P] [US1] Update the Sub-Panels section of `docs/specs/01-master-data.md` (around line 237): each panel is returned only to a caller with its read privilege (13, 12, 11, 3; administrators all), `[]` otherwise; on `POST`/`PUT` a list for an unreadable panel is ignored. Describe the rule only, not what it changed

**Checkpoint**: US1 is complete and testable on its own.

---

## Phase 4: User Story 2 - Concurrent confirmations get distinct, consecutive serials (Priority: P1)

**Goal**: every numbering step takes its locks in R2's order before any ordinary read, so `MAX(serial)` is current and no confirmation is refused by the unique index (research R2–R5).

**Independent Test**: on MariaDB, 20 concurrent confirmations at facility 51 all return 200 with serials max+1 … max+20 (quickstart step 5, SC-003). Locally, the lock-order unit tests pin the order.

### Tests for User Story 2 ⚠️ write first, see them fail

- [X] T013 [P] [US2] In `tests/unit/test_numbering_locks.py`, test `sales_order_service.confirm_order`'s order: `COMMIT`, then `sales_order … FOR UPDATE`, then `sales_order_detail … FOR SHARE`, then `facility … FOR UPDATE`, then one `warehouse … WHERE warehouse_id IN (…) … FOR UPDATE` in ascending ids (lines on warehouses 9 and 4 lock `4, 9`; a line with no warehouse adds none), then the first plain `SELECT`. Nothing wider is locked (FR-020): exactly one `facility … FOR UPDATE`, for the order's own facility, and the warehouse statement holds only the lines' warehouses, even when one of them belongs to another facility. A completed order after the lock raises 409 "Document is completed and can no longer be edited" with a `ROLLBACK` and no serial set; 1213 and 1205 raised by a statement become 409 "The warehouse is busy, retry"
- [X] T014 [US2] In `tests/unit/test_numbering_locks.py`, test `sales_quote_service.confirm_quote` (:453): `COMMIT` → `sales_quote … FOR UPDATE` → `facility … FOR UPDATE` → first plain `SELECT`, no warehouse lock, 1213 → 409 "The facility is busy, retry"; and `delivery_order_service.confirm` (:683) the same with `delivery_order`
- [X] T015 [US2] In `tests/unit/test_numbering_locks.py`, test `customer_refund_service.confirm_refund`: `COMMIT` → `cash_session … FOR UPDATE` (always: a refund requires the cashier's open session, :485) → `customer_refund … FOR UPDATE` → source `sales_order … FOR UPDATE` → `customer_refund_detail … FOR SHARE` → `facility … FOR UPDATE` → `warehouse … IN (…) FOR UPDATE` ascending → first plain `SELECT`; a session closed by the time of its lock answers 409 "The cash session is closed" and writes nothing; 1213 → 409 "The warehouse is busy, retry"
- [X] T016 [US2] In `tests/unit/test_numbering_locks.py`, test `delivery_itinerary_service.close_stop`: the pre-reads, then `COMMIT` → `deliveries_itinerary … FOR UPDATE` → `deliveries_itinerary_stop … FOR UPDATE` → `facility … FOR UPDATE` ascending (two delivery orders in facilities 8 and 3 lock `3, 8`) → one warehouse statement holding the dispatch, the transit warehouses of the dispatch warehouses' facilities (from `warehouse_service.transit_warehouses_for`) and the sales-line warehouses, ascending → the body's reads. An itinerary no longer `DEPARTED` or a stop no longer `PENDING` after the lock is refused with today's error and writes nothing
- [X] T017 [P] [US2] Create `tests/integration/test_numbering_flow.py` (reuse `tests/integration/seed.py` and the confirm flows of the existing sales, quote, delivery and refund integration tests): three sequential confirmations of each type in one facility get `max+1, max+2, max+3` per type; a refusal (zero price or insufficient stock) between two confirmations consumes no serial; a second facility's sequence is independent

### Implementation for User Story 2

- [X] T018 [US2] Rewrite `sales_order_service.confirm_order` in `app/services/sales_order_service.py` (:1364) per research R4, inside `inventory_documents.busy_as_conflict(db)`: `await db.commit()`; lock the order `FOR UPDATE` with `populate_existing` and `assert_editable`; read its lines `FOR SHARE` with `populate_existing`; the warehouses are the distinct non-null `warehouse` of those lines (a superset of the stocked ones, harmless); `documents.lock_facility(db, order.facility)`; `inventory_documents.lock_warehouses(db, warehouses)`; then today's checks unchanged and in today's order (credit hold, delivery details, lines, zero prices, availability via `_stocked_products`/`stock_ledger.available`, shortfalls, credit limit or cash allowed); then `assign_folio`, `stock_ledger.reserve`, and the commit. Import `inventory_documents` (no cycle, R4). Keep the route's `_writable_order_or_404` pre-read in `app/api/v1/endpoints/sales_orders.py`
- [X] T019 [P] [US2] Rewrite `confirm_quote` (:453) in `app/services/sales_quote_service.py`: commit; lock the quote `FOR UPDATE` with `populate_existing` and re-check its state; `lock_facility(quote.facility)`; today's checks; `assign_folio` last; inside `busy_as_conflict(db, detail='The facility is busy, retry')`
- [X] T020 [P] [US2] Rewrite `confirm` (:683) in `app/services/delivery_order_service.py` the same way as T019, for the delivery order and its facility
- [X] T021 [US2] Rewrite `customer_refund_service.confirm_refund` in `app/services/customer_refund_service.py` (:473) per research R3: pre-reads (the cashier's open session id, today's `open_session_for_cashier` at :485 and its 409 when none); commit; `cash_session_service.lock_open_session` first, always (it answers 409 "The cash session is closed" when the session closed meanwhile); lock the refund, then the source order (today's lock at :493), both with `populate_existing`; refund lines `FOR SHARE` with `populate_existing`; `lock_facility(refund.facility)`; `lock_warehouses` of the lines' warehouses (what `post_movement` writes to); then today's checks and writes, `assign_folio` for the refund last; inside `busy_as_conflict(db)`. Leave payout numbering to T031
- [X] T022 [US2] Rewrite `delivery_itinerary_service.close_stop` in `app/services/delivery_itinerary_service.py` (:677) per research R5: pre-read as scalars the stop's dispatch warehouses, its delivery orders' facilities, the sales-line warehouses `_reclaim_reservation` writes to, and `warehouse_service.transit_warehouses_for(dispatch warehouses)`; commit; lock the itinerary then the stop `FOR UPDATE` with `populate_existing` and re-check `DEPARTED`/`PENDING`; lock the facilities ascending via `lock_facility`; `lock_warehouses` of the union; then today's body, re-reading the stop lines, delivery order lines and orders with `populate_existing` queries instead of `db.get` on pre-commit objects; inside `busy_as_conflict(db)`. The endpoint's image save stays before the service call
- [X] T023 [US2] Confirm `supplier_payment_service.create_payment` (`app/services/supplier_payment_service.py`, commit :356, `assign_folio` :361) still runs only `FOR UPDATE` reads between its commit and `assign_folio` after T006; change nothing (FR-011). Run T013–T017 and the existing unit, API and integration tests of sales orders, quotes, delivery orders, refunds and itineraries; fix what the new commit-then-lock order breaks in tests that relied on pre-commit objects

**Checkpoint**: every numbering step except payment creation follows R2; the suite passes.

---

## Phase 5: User Story 3 - Two confirmations cannot sell the same units (Priority: P1)

**Goal**: sales order confirmation checks availability under its warehouses' locks (done in T018); a double confirm confirms once; an order edit that waited behind a confirmation is refused (FR-017–FR-019).

**Independent Test**: on MariaDB, two concurrent 5-unit orders on a 5-unit warehouse give one 200 and one 409 "Insufficient stock", availability 0 (SC-005). Locally, the integration tests and the edit lock-order tests.

**Depends on**: T018.

### Tests for User Story 3 ⚠️ write first, see them fail

- [X] T024 [P] [US3] In `tests/unit/test_numbering_locks.py`, test the four edits in `sales_order_service` (`update_order` :1098, `add_line` :1256, `update_line` :1309, `remove_line` :1342): each issues `COMMIT`, then `sales_order … FOR UPDATE`, before any write; an order that is completed by the time of the lock raises 409 "Document is completed and can no longer be edited" and writes nothing; `update_line` and `remove_line` then re-read their line (`sales_order_detail` by id, after the order lock), and a line deleted meanwhile answers 404 and writes nothing; no facility or warehouse lock is taken
- [X] T025 [P] [US3] In `tests/integration/test_numbering_flow.py`, add: with 5 units available, confirm a 5-unit order, then a second 5-unit order on the same warehouse gets 409 `{"message": "Insufficient stock", "lines": [...]}` and reserves nothing, and availability reads 0; confirming an already confirmed order answers 409 "Document is completed and can no longer be edited" with one serial and one set of reservations; a line add on a confirmed order answers the same 409

### Implementation for User Story 3

- [X] T026 [US3] In `app/services/sales_order_service.py`, give `update_order`, `add_line`, `update_line` and `remove_line` the same prelude: `await db.commit()`, lock the order `FOR UPDATE` with `populate_existing`, `assert_editable`, then today's body unchanged. Keep the four signatures (they take the `SalesOrder` the endpoint's `_writable_order_or_404` in `app/api/v1/endpoints/sales_orders.py` (:37) loaded): lock by `order.sales_order_id` with `populate_existing`, which refreshes that same object in place. `update_line` and `remove_line` also receive a line read before the commit; after the order lock, re-read it by id with `populate_existing` (404 if it is gone). T024 and T025 pass
- [X] T027 [US3] Re-run the sales order unit, API and integration tests (`tests/unit/test_sales_order_service.py`, `tests/api/test_sales_orders.py`, `tests/integration/test_line_warehouse.py`, `tests/integration/test_credit_hold.py`) and fix any that relied on the pre-commit order object

**Checkpoint**: confirmations serialise per warehouse; double confirm and late edits are refused.

---

## Phase 6: User Story 4 - Customer payments carry a folio (Priority: P2)

**Goal**: every customer payment the API writes gets its facility's next payment serial; a numeric list search matches the serial; the 20 serial-0 payments are backfilled (research R6, R7).

**Independent Test**: take a payment at a facility whose max payment serial is N; it is numbered N+1 and `GET /customer-payments?search=<N+1>` finds it.

### Tests for User Story 4 ⚠️ write first, see them fail

- [X] T028 [P] [US4] In `tests/unit/test_numbering_locks.py`, test `customer_payment_service.create_payment`: pre-reads, `COMMIT`, `cash_session … FOR UPDATE` when a session applies, `facility … FOR UPDATE`, then the `MAX(customer_payment.serial)` plain read; the inserted payment's `serial` is that value + 1; a session closed by the time of the lock answers 409 "The cash session is closed" and inserts nothing; 1213 → 409 "The facility is busy, retry"
- [X] T029 [P] [US4] In `tests/api/test_customer_payments.py`, test `GET /customer-payments?search=<digits>`: matches a payment by its `customer_payment_id` and by its `serial`, only within the list's facility scope (a payment with that serial in another facility is not returned to a facility-scoped caller); a non-numeric search still matches the customer's name
- [X] T030 [P] [US4] In `tests/integration/test_numbering_flow.py`, add: two payments in one facility get consecutive serials from that facility's max, and `POST /customer-payments` returns that `serial`; the payment ticket's print context shows it as 8 digits (`00018714` style, as `tests/unit/test_print_contexts.py` checks today) (FR-013); a refund paid out as a credit note and one paid in cash write payments numbered in the refund facility's payment sequence, after any plain payment's serial (US4 scenario 2)

### Implementation for User Story 4

- [X] T031 [US4] In `app/services/customer_payment_service.py`, rewrite `create_payment` (:228) per research R6: keep the pre-reads (customer, open session); `await db.commit()`; `cash_session_service.lock_open_session` when a session applies, answering 409 "The cash session is closed" when it is gone; `serial = await documents.assign_folio(db, CustomerPayment, facility=current.facility_id)`; insert with that serial instead of `0`; inside `inventory_documents.busy_as_conflict(db, detail='The facility is busy, retry')`
- [X] T032 [US4] In `app/services/customer_refund_service.py`, make `_pay_customer_back` (:595) number the one payment it writes, the cash payout or the credit note's backing payment (:619), with `assign_folio(db, CustomerPayment, facility=refund.facility)` instead of `serial=0`. It runs under T021's facility lock
- [X] T033 [US4] In `customer_payment_service.list_payments` (:281), when `search` is all digits (reuse `is_barcode_or_id`'s rule), filter `customer_payment_id = n OR serial = n` inside the existing facility scope; otherwise keep the customer-name match. Update the `search` parameter's description in `app/api/v1/endpoints/customer_payments.py` ("All digits: the payment's id or its serial; otherwise the customer's name"). T028–T030 pass
- [ ] T034 [US4] Create `migrations/030_serials_ledger_index.sql` in migration 029's header style, idempotent, MariaDB 10.11: (1) backfill `customer_payment` rows with `serial = 0`: per facility, in id order, `MAX(serial) + ROW_NUMBER()` via one `UPDATE … JOIN` over a derived table with a window function, as migration 007 does; touches only `serial = 0` rows; (2) `CREATE INDEX IF NOT EXISTS customer_payment_facility_serial_idx ON customer_payment (facility, serial)` (non-unique). Create `migrations/030_serials_ledger_index_rollback.sql` dropping the index with `DROP INDEX IF EXISTS` and keeping the serials (comment why: they are folios tickets may have printed). Test first, in `tests/unit/test_migrate.py` following its 008–016 tests: 030 is discovered and ordered after 029, and its rollback is not applied automatically. The SQL itself is exempt from an automated test (SQLite cannot run MariaDB's `UPDATE … JOIN` with a window function or `CREATE INDEX IF NOT EXISTS`); say so in the commit, and it is verified on mbe_dev by T041
- [ ] T035 [P] [US4] Update `docs/specs/02-sales.md` §5 Customer Payments: the API numbers every payment it writes per facility (`MAX + 1` under the facility lock, shared with the current system's sequence), refund payouts included; the numeric search matches id or serial within the facility scope; a payment into a session closed meanwhile answers 409 "The cash session is closed"

**Checkpoint**: API payments are numbered and searchable; migration 030 holds the backfill and the payment index.

---

## Phase 7: User Story 5 - Cancelling an inventory document does not scan the ledger (Priority: P3)

**Goal**: the ledger and reservation lookups by `(source, reference)` go through an index (FR-022, research R7).

**Independent Test**: on mbe_dev after the migration, the `EXPLAIN` of the ledger lookup uses `lot_serial_tracking_source_reference_idx` with `ref` access and reads tens of rows (SC-007).

**Depends on**: T034 (same migration file).

- [ ] T036 [US5] Append to `migrations/030_serials_ledger_index.sql`: `CREATE INDEX IF NOT EXISTS lot_serial_tracking_source_reference_idx ON lot_serial_tracking (source, reference)` and `CREATE INDEX IF NOT EXISTS lot_serial_rqmt_source_reference_idx ON lot_serial_rqmt (source, reference)`, with a header comment on why each (the cancel reversal and `_ledger_warehouses`; `release_reservations`' delete that otherwise next-key-locks all of `lot_serial_rqmt`). Add both `DROP INDEX IF EXISTS` lines to `migrations/030_serials_ledger_index_rollback.sql`. Run `tests/unit/test_migrate.py`; the plans are verified on mbe_dev by T002 and T044 (same exemption as T034)

**Checkpoint**: migration 030 is complete (backfill, three indexes, rollback).

---

## Phase 8: Polish, deploy and verification

**Purpose**: changelog, gates, dev deployment, the "after" evidence, and the record.

- [ ] T037 [P] Add to `CHANGELOG.md` `[Unreleased]`, using only the constitution's groups (Added / Changed / Removed / Fixed / Docs): **Fixed**: the supplier detail returns each sub-panel only to callers with that panel's read privilege, and a save ignores link lists for panels the caller cannot read (roadmap#152; neutral wording, no description of what was exposed); concurrent confirmations no longer refused by duplicate serials, a sales order confirmation no longer oversells against another confirmation or an inventory issue, a double confirm confirms once, an order edit that waited behind a confirmation is refused; **Changed**: the API's customer payments are numbered per facility, numeric payment search matches the serial, a payment or refund into a session closed meanwhile answers 409 "The cash session is closed", migration 030 (backfill of 20 payments, three indexes)
- [ ] T038 [P] Add the lock order to `docs/specs/02-sales.md` Confirm Action (around line 222): commit, the order, its lines, the facility, the warehouses ascending, then the checks and the serial; 409 "The warehouse is busy, retry" on a deadlock or lock timeout. In the refund Confirm Action (around line 349): the cash session is locked first, a session closed meanwhile answers 409 "The cash session is closed", and the payout payment is numbered in the facility's payment sequence; and note in `docs/specs/04-inventory.md` (line 27) that sales confirmations now take the same warehouse locks after their facility row
- [ ] T039 Run the gates: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`; all pass (SC-008)
- [ ] T040 Deploy the branch to the dev instance on xolotl (tmux :8765, as mictlanix@) and check `/health` (quickstart step 2)
- [ ] T041 Ask for confirmation, then apply `migrations/030_serials_ledger_index.sql` to mbe_dev right after the deploy and run it a second time (quickstart step 3). Check: no `serial = 0` payments; the 20 carry facility 51's next serials in id order; the checksum over every other payment's `(customer_payment_id, serial)` equals T002's; `SHOW INDEX` lists the three new indexes; the second run changes 0 rows
- [ ] T042 Check the supplier panels on dev (quickstart step 4, SC-001): a Products-only user gets four empty panels for T002's supplier; an administrator gets all four; one Suppliers-read user without Bank Accounts read gets `bank_accounts` empty and the others per their privileges. No supplier write is sent to dev
- [ ] T043 Ask for confirmation, then run the races after (quickstart step 5): `race_confirm_serials.py` (all 200, serials max+1 … max+20, SC-003; the quotes and refunds of the same burst each consecutive within their own type, US2 scenario 2), `race_payments.py` (all 201, 20 distinct consecutive serials, SC-004), `race_last_units.py` (one 200 and one 409 "Insufficient stock", availability 0; double confirm one 200 and one 409 "Document is completed …", one serial and one set of reservations, SC-005), `race_order_vs_issue.py` (one succeeds, the other is refused for insufficient stock). Each reports 0 rows left
- [ ] T044 Re-run T002's `EXPLAIN` on mbe_dev (quickstart step 6, SC-007): `lot_serial_tracking_source_reference_idx` with `ref` access and rows in the tens
- [ ] T045 Record the verifications in `specs/035-serials-stock-bank-access/.spec-context.json` with the companion writer (before/after race outcomes, migration counts, EXPLAIN plans, supplier panels by user), and put the same evidence in the PR's Verification section. The PR lists `Closes mictlanix/mbe-roadmap#152` and `Closes mictlanix/mbe-roadmap#153`, describes the supplier-panel change neutrally (the rule, not what it prevented), and its commits carry no `Co-Authored-By` lines
- [ ] T046 After the merge: delete the branch locally and on the remote, remove the worktree, mark the spec complete (`--mark-complete`) and commit `.spec-context.json` straight to main; then open the promised roadmap issue in mictlanix/mbe-roadmap for the concurrent credit-limit check (two credit orders that each fit the limit but together exceed it; needs a customer lock every payment and refund path follows)

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: none. T004 must run before T040 (dev still on main).
- **Foundational (Phase 2)**: after T001. Blocks US2, US3, US4.
- **US1 (Phase 3)**: after T001 only; it does not need Phase 2.
- **US2 (Phase 4)**: after Phase 2.
- **US3 (Phase 5)**: after T018.
- **US4 (Phase 6)**: after Phase 2. T032 edits `customer_refund_service.py` after T021.
- **US5 (Phase 7)**: after T034.
- **Polish (Phase 8)**: after every story. T040 → T041 → T042/T043/T044 → T045 → T046.

### Shared files (run their tasks in order)

- `tests/unit/test_numbering_locks.py`: T007 → T013, T014, T015, T016 → T024 → T028 (append one section each; serialise the writes even where marked [P] across stories)
- `tests/integration/test_numbering_flow.py`: T017 → T025 → T030
- `app/services/sales_order_service.py`: T018 → T026
- `app/services/customer_refund_service.py`: T021 → T032
- `migrations/030_serials_ledger_index*.sql`: T034 → T036
- `docs/specs/02-sales.md`: T035 → T038

### Within each story

Tests first and failing, then the service, then the endpoint, then the docs.

## Parallel Opportunities

- **Phase 1**: T002 and T003 together.
- **US1 alongside Phase 2**: T008, T009 (tests) while T005–T007 run; US1 touches only supplier files.
- **US2 tests**: T013–T016 are sections of one file, so only T013 carries [P]; write the four in one pass. T017 in parallel with them.
- **US2 implementation**: T019 (quotes) and T020 (delivery orders) in parallel with each other and with T018; T021 and T022 touch their own files and can run alongside too.
- **US4**: T028, T029, T030 in parallel (different files, apart from the shared-file order above); T033 and T034 in parallel.
- **Docs**: T012, T035, T037 in parallel.

### Parallel example: User Story 2

```text
Task: "T018 Rewrite confirm_order in app/services/sales_order_service.py"
Task: "T019 Rewrite quote confirm in app/services/sales_quote_service.py"
Task: "T020 Rewrite delivery order confirm in app/services/delivery_order_service.py"
Task: "T022 Rewrite close_stop in app/services/delivery_itinerary_service.py"
```

## Implementation Strategy

### MVP first (User Story 1)

1. Phase 1 (T001–T004), so the "before" evidence exists while dev runs main.
2. Phase 3 (US1): delivers the supplier-panel rule on its own. Validate with T009.

### Incremental delivery

1. US1: supplier panels.
2. Phase 2 + US2: numbering rule across the six steps.
3. US3: stock under warehouse locks, double confirm, late edits.
4. US4: payment folios, search, backfill.
5. US5: ledger and reservation indexes.
6. Phase 8: one deploy, migration 030 right after it, the "after" runs, one PR closing #152 and #153.

All stories ship in one PR, as the spec and the roadmap issues ask.

## Notes

- [P] tasks = different files, no dependency on an incomplete task.
- Commit after each task or logical group, with no `Co-Authored-By` lines.
- Every mbe_dev write (races, cleanup, migration) waits for confirmation; reads and the dev deploy don't.
- Don't renumber the current system's rows; don't add unique indexes on `customer_payment` or `purchase_request`.
