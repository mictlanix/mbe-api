# Research: Supplier Sub-Panels by Privilege, Race-Safe Serials and Stock

Measurements are from mbe_dev on 2026-10-27, read-only (see the spec's Findings). Code references are to the branch's base, main at 56de8a0.

## R1. Supplier sub-panels: filter each panel, keep the lookup gate

**Decision**:
- `GET /suppliers/{id}` keeps `_LOOKUP` (Suppliers, Products or Pricing read).
- `supplier_service` works out which panels the caller may read with one `access_privilege` query: Bank Accounts (13), Contacts (12), Addresses (11), Suppliers (3), each with the READ bit. Administrators read all four.
- `_attach_links` queries only the readable panels. The others stay empty lists, so the response shape does not change.
- `create_supplier` and `update_supplier` treat an unreadable panel's list as `None`: links unchanged on update, none written on create (FR-003, clarification Q1).
- The three routes returning the detail (`GET`, `POST`, `PUT`) pass the caller to the service.

**Rationale**:
- mbe-ui's `supplierDisplayNameProvider` reads the detail for a filter chip's code and name, for users who may hold Products or Pricing read without Suppliers read. Moving the detail to Suppliers read would break that chip, so the gate stays and the panels are filtered.
- Each panel follows its own route's gate (clarification Q2). One rule covers all four panels, and it matches how mbe-ui's supplier form already gates each panel's actions.
- The form sends all three link lists on every save. Ignoring an unreadable list keeps the form working for the 3 editors without Bank Accounts read, and stops a caller from replacing links they cannot see.
- Skipping the queries for unreadable panels also saves up to four queries for lookup callers.

**Other embeds checked**:
- The customer detail embeds addresses and contacts for the sales screens, and no bank accounts. It stays unchanged (FR-005).
- No other response embeds these records. Sales, quote and delivery documents carry ids only. Purchase orders, supplier payments and expense vouchers embed none. Invoice batch bank details are free text.
- The supplier list and `ProductResponse.supplier` carry no panels.

**Alternatives considered**:
- **Move the detail to Suppliers read:** breaks the filter chip for 25 users.
- **Return 403 for an unreadable list on a write:** breaks today's form for those editors (clarification Q1).
- **Filter only bank accounts:** leaves contacts and addresses under a weaker gate than their own routes (clarification Q2).

## R2. The numbering rule: commit, lock, then read

**Decision**: every step that numbers a document per facility runs one transaction in this order. It is spec 029's R2, extended with the facility row as the numbering lock:

1. **Pre-reads.** These are plain reads of data that cannot change under the step: the route's lookup, privileges, and the warehouse and facility sets of a stop being closed (R5). They load scalar columns, or entities that step 3 re-reads.
2. **`await db.commit()`.** This ends the transaction that the dependencies' reads opened. Nothing is pending at this point.
3. **Locks on the step's own rows, `FOR UPDATE` with `populate_existing`:**
   - the cash session, first, on paths that write a payment into one;
   - then the document row or rows.

   Everything after this works from the locked rows.
4. **A locking read (`FOR SHARE`, `populate_existing`) of the lines that decide the warehouse set,** on steps that write stock. A locking read returns current rows and opens no snapshot.
5. **The facility row or rows, ascending** (`documents.lock_facility`). This is the numbering lock.
6. **The warehouses, ascending, in one statement** (`inventory_documents.lock_warehouses`), on steps that write stock.
7. **Ordinary reads.** The snapshot opens at the first of these, after every lock, so it includes everything the previous holders committed. That covers `MAX(serial)` in `documents.assign_folio`, `stock_ledger.available`, balances and credit checks.

Further rules:
- **No stale entities.** After the commit, every entity the step uses is either re-read by a query with `populate_existing` or loaded for the first time after the locks.
  - `db.get` on an object the session already holds returns it without SQL. With `expire_on_commit=False`, that object carries its pre-commit values.
  - `expire_all` is not used, because async lazy loads raise.
- **The serial is written to the document last,** after every check. A refusal rolls back and consumes nothing (FR-010).
- **Each step runs inside `inventory_documents.busy_as_conflict`,** so a deadlock (1213) or lock-wait timeout (1205) answers 409 "busy, retry" and writes nothing (FR-009).

**Why the facility comes before the warehouses**:
- Inserting a header row checks its `facility` foreign key, which takes a share lock on the facility row. Some paths then insert lines that check `warehouse`, which takes a share lock on a warehouse:
  - opening a refund (`customer_refund_service.py:363-384`);
  - a purchase order confirmation that writes receipts for two warehouses (`purchase_order_service.py:780-792`);
  - an inventory draft or header update, where the facility key is checked first (`inventory_documents.py:418-452`; `inventory_receipt_service.py:262-273`).
- With warehouses locked first, a confirmation holding warehouse W X and waiting for facility F X would deadlock with one of those holding F S and waiting for W S. That is the normal case at one counter.
- With the facility first, no path holds a warehouse and then waits on a facility:
  - refunds and stop closing, which did so today (spec 029 R1's reason for leaving the facility row alone), are rewritten here;
  - inventory steps insert no facility-keyed row after their warehouse lock;
  - pickup and departure insert only warehouse-keyed rows.

**The helpers** (`app/services/documents.py`):
- `lock_facility(db, facility)`: `SELECT facility_id … FOR UPDATE`; 404 when the facility does not exist. This is today's first half of `assign_folio`.
- `assign_folio(db, model, *, facility)`: `lock_facility` (a no-op when the row is already held), then a plain `MAX(serial) + 1`.
  - Its signature is unchanged and it serves every caller.
  - Its docstring states the precondition: no ordinary read in the transaction before the facility lock.
  - The wrong claim that there is "no unique index" goes. Most tables have one; customer payments and purchase requests don't.
- A step that numbers several documents in one transaction (close stop's split children) calls `assign_folio` once per document. Each call sees the earlier ones' serials through autoflush, because a transaction's own writes are visible to its reads.

**Rationale**:
- InnoDB opens a REPEATABLE READ snapshot at the first consistent read. Locking reads and writes don't open it.
  - Today every `assign_folio` caller except supplier payments has run plain reads before the facility lock: the dependencies' user and privilege reads, then the document. So the `MAX` can come from before the previous holder's commit. That is mictlanix/mbe#45.
  - The unique `(facility, serial)` indexes catch the duplicate. The global `IntegrityError` handler then answers a generic 409, so a cashier is refused for no visible reason.
- Taking every lock first makes plain reads current. Neither `MAX` nor the ledger sums need a locking read. Spec 029 R2 showed that matters on mbe_dev: a share lock on the ledger sums spreads across every warehouse's entries for a product.

**Alternatives considered**:
- **Spec 027's locking `MAX … FOR UPDATE/FOR SHARE`:**
  - A backward scan of the `(facility, serial)` index locks the newest document's row and the next key. Spec 029 R1 reproduced two deadlock shapes this causes.
  - Here it would add a third. A refund of the facility's newest order holds that order X, then waits on the facility. A confirmation holding the facility would then wait on the order's row.
  - Payments applied to the order just confirmed are routine at the counter, and their `settle` updates that order's row.
- **Reading `MAX` on a second connection after taking the lock:**
  - Every request would hold one pooled connection while waiting on the facility lock and need a second one once it gets the lock. With more waiters than the pool has connections, the holder can't get its second connection and blocks until the pool timeout.
  - The second connection also cannot see the transaction's own uncommitted children (close stop numbers several).
- **A counter column or table:** the current system numbers from `MAX + 1` on the same tables, so a counter would drift from it.
- **Warehouses before the facility** (this plan's first draft, matching today's refund order): the draft-creation deadlock above.

## R3. Each numbering step, and its locks

| Step | 3. Own rows | 5. Facility | 4, 6. Warehouses | Change |
|---|---|---|---|---|
| Sales order confirm | the order | the order's | the stocked lines' warehouses, from the lines read `FOR SHARE` | rewritten (R4) |
| Sales quote confirm | the quote | the quote's | none | rewritten |
| Delivery order confirm | the delivery order | the delivery order's | none (no stock moves) | rewritten |
| Customer refund confirm | the cashier's open session, the refund, the source order | the refund's | the stocked lines' warehouses, from the refund lines read `FOR SHARE` | rewritten |
| Itinerary stop close (split children) | the itinerary, the stop | the stop's delivery orders' facilities | pre-read (R5) | rewritten (R5) |
| Customer payment create | the cashier's open session, when the payment takes one | the caller's | none | rewritten (R6) |
| Supplier payment create | cash session, purchase orders ascending | the payment's | none | **unchanged:** already commit-then-lock |

**Sales order edits** (header update, add, update and remove line) also change.
- They commit, lock the order with `populate_existing` and re-check `assert_editable` before writing.
- Today they check a copy read before the confirmation committed. They then wait on the order row behind it, and write a line onto a confirmed order: a line with no reservation, under a folio already printed.
- They take no other lock (R4).

**Supplier payments were already safe (FR-011).** `supplier_payment_service.create_payment` commits at :356. Between that and `assign_folio` at :361 it runs only `FOR UPDATE` reads (`lock_open_session`, `_lock_orders`), and nothing is pending to autoflush. Its `MAX` is therefore the first ordinary read after the locks.

**Purchase requests** (`submit`: commit, request, transit warehouse, plain `MAX`) and **invoices** (spec 027: batch row, then a locking `MAX` on `fiscal_document`) keep working as they are. Neither takes the facility row.

**Why the cash session goes first on refunds and payment creation.**
- A refund's payout and a customer payment insert into `customer_payment` with a foreign key to `cash_session`, which takes a share lock on the session row.
- Today the refund does that after its facility lock. A cash supplier payment holds the session X, then waits on the facility. With the same cashier's session on both sides, that is a cycle today.
- Taking the session first is the order supplier payments already use. Session close takes only the session; voucher confirm and void take the session, then the voucher. Neither closes a cycle with it.

**No cycle among the paths.** Every path takes its locks in the order cash session → documents → facility rows (ascending) → warehouses (ascending, one statement).
- **Payment application and reversal** (spec 033): payment, then order. They wait on nothing a confirmation holds before its order, and they never take the facility or warehouses.
- **Credit-note invoices** (spec 027): batch, fiscal documents, then orders ascending. They take no facility or warehouse lock.
- **Inventory steps** (spec 029): document, then warehouses including transit. They never take a sales document or wait on the facility after their warehouse lock.
- **Draft creation anywhere:** a header insert's share lock on the facility queues behind a numbering step, and holds no warehouse that the step wants.
- **Cross-facility lines** (14% of 2026): a confirmation in facility A may lock facility B's warehouse after A's facility row. No path holds a warehouse and then waits on a facility, so this adds no cycle.

**Known limits** (as spec 029):
- **Unordered ledger inserts on paths that don't number.** Pickup confirmation, itinerary departure and order cancel take implicit share locks on warehouses, in line order, with no explicit lock (FR-021 keeps them as they are).
  - Against a confirmation's ascending lock that can still deadlock. InnoDB then rolls one side back.
  - The confirmation's side answers 409 "busy, retry". The other side's handling is unchanged.
- **Expense voucher creation by the same cashier.** `create_voucher` inserts one row whose facility key is checked before its `cash_session` key. It can deadlock with that same cashier's refund, payment or cash supplier payment (the last exists today).
  - Two simultaneous requests from one cashier are needed for this. It is left as it is.
- **The facility lock is now held for the whole step,** a few tens of milliseconds, instead of only its tail.
  - In that window it also holds back every insert of a facility-keyed row in that facility: new orders, quotes, refunds, delivery orders, inventory documents, vouchers and invoices.
  - Confirmations, payments and refunds in one facility already queue on it today.
  - Different facilities do not wait for each other unless they share a warehouse.

## R4. Sales order confirmation: stock under the warehouse locks

**Decision**:
- `confirm_order` runs R2 in full:
  - commit;
  - lock the order (`populate_existing`) and re-check that it is editable;
  - read its lines `FOR SHARE` (`populate_existing`), and work out the warehouses of the stocked lines. `_stocked_products` is a plain read, so the set includes every line with a warehouse; locking an extra warehouse is harmless;
  - lock the facility, then those warehouses ascending.
- Then come today's checks, unchanged and in today's order: credit hold, delivery details, lines, zero prices, availability, shortfalls, credit limit or cash allowed. After them, `assign_folio`, `stock_ledger.reserve` and the commit.
- The whole step runs inside `busy_as_conflict`.
- The route keeps its 404 lookup (`_writable_order_or_404`) as the pre-read.

**Rationale**:
- Every step that lowers a warehouse's availability now takes that warehouse's row lock before reading availability: sales order confirmations, and spec 029's issues, transfer dispatches, counts and cancels. The later one sees the earlier one's reservation or movement.
  - Two orders for the last 5 units: one confirms, and the other gets today's "Insufficient stock" 409 with its lines (FR-018).
  - The same holds for an order against an inventory issue (US3, scenario 2).
- The order lock makes a concurrent second confirmation of the same order wait, then fail `assert_editable` with "Document is completed and can no longer be edited" (FR-019).
- Line and header edits write the order row, so they queue on it. With R3's re-check, an edit that waited behind a confirmation is refused the same way.
- `sales_order_service` can import `inventory_documents` without a cycle: nothing that `inventory_documents` imports reaches back to the sales services. So `lock_warehouses` and `busy_as_conflict` are reused, not moved.

**Other paths that change availability** (FR-021: unchanged):

| Path | Effect on availability | Locks today |
|---|---|---|
| Inventory issue confirm, transfer dispatch, count confirm, cancel of an inbound document (spec 029/030) | lowers | document, warehouses incl. transit |
| Delivery pickup, itinerary departure | none (on-hand and reservation fall together) | the delivery line (`_lock_line`) on commit and adjust; implicit FK share locks on ledger inserts |
| Itinerary return (`_reclaim_reservation`) | neither: goods come back on-hand and are re-reserved | none explicit; locked by R5 when it runs inside a stop close |
| Refund, sales order cancel | raises | the source order (refund); none explicit (cancel) |

**Alternatives considered**:
- **Locking reads on the ledger sums:** these are wide share locks, as spec 029 R2 found.
- **Locking only the order:** two orders still read stale availability.

## R5. Closing an itinerary stop

**Decision**: `close_stop` runs the following, with the endpoint's image save staying before the service call.

1. **Pre-read, as scalar columns:**
   - the stop's lines, and through them their delivery order lines' dispatch warehouses and their delivery orders' facilities;
   - the sales order lines' warehouses that `_reclaim_reservation` writes to;
   - the in-transit warehouse of each dispatch warehouse's facility, from `warehouse_service.transit_warehouses_for`, the same mapping the body posts to.
2. **Commit.**
3. **Lock the itinerary, then the stop,** `FOR UPDATE` with `populate_existing`. Re-check that the itinerary is `DEPARTED` and the stop is `PENDING`.
4. **Lock the facilities, ascending.**
5. **Lock the warehouses, ascending:** dispatch, transit and sales-line warehouses.
6. **Today's body.** It reads the stop's lines and the delivery order lines and orders with `populate_existing`, rather than through `db.get` on objects loaded before the commit.

The whole step runs inside `busy_as_conflict`.

**Rationale**:
- Once the itinerary has departed, a stop's lines and their warehouses are fixed. Delivery order lines are editable only in DRAFT, and only their quantity. A plain pre-read before the commit is therefore safe, as spec 029 R2 relied on for a cancel's ledger warehouses.
- Locking the stop makes a second close of the same stop wait, then see it resolved. Locking the itinerary makes two stops of one itinerary queue, so they agree on which one is last.
- The facility lock is taken whether or not the stop splits. Whether an order is partly delivered is known only after the reads, and the lock must come before them.
- Today the split child's insert takes a share lock on the facility (foreign key) before `assign_folio` asks for X. Two partial closings in one facility can deadlock on that upgrade. Taking the facility X up front removes it.
- The transit warehouse is the dispatch warehouse's facility's, not the delivery order's, and the two differ on cross-facility lines. Locking the delivery order's transit instead would leave the real one outside the lock set.
- Departure, line commit and adjust take no lock that conflicts with this order. Departure needs an OPEN itinerary; commit and adjust lock delivery order lines of open itineraries.

## R6. Customer payment numbering and search

**Decision**:
- **`customer_payment_service.create_payment`:**
  - pre-reads stay: customer, open session;
  - then commit, `cash_session_service.lock_open_session` when a session applies, and `assign_folio(db, CustomerPayment, facility=current.facility_id)`;
  - the insert carries that serial;
  - the step runs inside `busy_as_conflict`.
- **A session closed between the pre-read and the lock** answers 409 "The cash session is closed", as supplier payments do.
  - Today such a payment is recorded into the closed session, after its count.
  - Falling back to no session would silently move a cash payment out of the drawer it was taken at.
- **`customer_refund_service._pay_customer_back`:**
  - numbers the one payment it writes, the cash payout or the credit note's backing payment, with `assign_folio` for the refund's facility;
  - it already runs under the refund's facility lock (R3).
- **A refund whose session closed between the pre-read and the lock** answers the same 409, from `lock_open_session`. A refund always requires the cashier's open session.
- **Search:** `GET /customer-payments?search=<digits>` matches `customer_payment_id = n OR serial = n`, inside the list's existing facility scope.
  - Anything else keeps matching the customer's name.
  - This mirrors `search_outstanding`'s `is_barcode_or_id` rule and the current system's `Id == n || Serial == n`.
- **The tickets** already print a non-zero serial (`print_contexts`: `pad8(payment.serial)` on the payment ticket and the statement). **The response did not carry it** (corrected during implementation: only `OutstandingOrderResponse` had a `serial`), so `CustomerPaymentResponse` gains `serial` (FR-013). The field is added; no client breaks.

**Rationale**:
- The current system numbers every payment per store, credit-note payments included, and prints and searches by that number. Using the same table's `MAX + 1` per facility continues its sequence.
- The `(facility, serial)` index from R7 makes each payment's `MAX` an index lookup instead of a filesort over about 114k rows.

**Alternatives considered**:
- **A numeric search that also matches names:** customer names are not numeric, and the order search already splits on digits.
- **Searching across facilities, as the current system does:** spec 034's facility rule keeps payments scoped. `/search` (PaymentsEditor) stays the cross-facility route.

## R7. Migration 030

**Decision**: `migrations/030_serials_ledger_index.sql`, with `030_serials_ledger_index_rollback.sql`. MariaDB 10.11, idempotent.

1. **Backfill** the payments this API wrote with serial 0:
   - within each facility, in id order, they get `MAX(serial) + ROW_NUMBER()`;
   - it uses one `UPDATE … JOIN` over a derived table with a window function, as migration 007 does;
   - `customer_payment.serial` is NOT NULL, and migration 007 cleaned zeros only on sales orders, quotes and refunds. That only the API's rows carry 0 is a measurement: 20 rows on mbe_dev, all API (spec Findings). The update changes only `serial = 0` rows;
   - a second run finds no zeros and changes nothing (FR-015).
2. **`CREATE INDEX IF NOT EXISTS customer_payment_facility_serial_idx ON customer_payment (facility, serial)`.** It is non-unique, because the current system's 11 duplicate groups stay (FR-016).
3. **`CREATE INDEX IF NOT EXISTS lot_serial_tracking_source_reference_idx ON lot_serial_tracking (source, reference)`** (FR-022).
4. **`CREATE INDEX IF NOT EXISTS lot_serial_rqmt_source_reference_idx ON lot_serial_rqmt (source, reference)`.**
   - A sales order cancel's `release_reservations` deletes by `source` and `reference`. Without an index that DELETE scans all 3,252 rows and next-key-locks the whole table until the cancel commits, which stalls every confirmation's `reserve` in the meantime.
   - The departure and return paths look reservations up the same way.

The rollback drops the three indexes. It leaves the backfilled serials, because they are valid folios that tickets may already have printed. Restoring 0 would hide them.

**When it runs:** after the branch is deployed.
- The migration takes no facility lock and the payment index is not unique. Run before the deploy, a payment written by the old code in between would still be 0, and a rerun would number it.
- Run after the deploy while payments are being taken, the backfill's `MAX` could race the new code's numbering.
- On dev it runs right after the deploy, with no cashier at work. At go-live it runs in the deploy window.

**Rationale**:
- One file keeps the deploy to one migration. The index on `lot_serial_tracking` (632k rows) builds online in MariaDB 10.11.
- The current system's NHibernate mappings name their columns and ignore indexes, so neither change affects it.
- Before the migration, the ledger lookup by source and reference scans 670,388 rows by full index scan (spec Findings). The quickstart records the plan after (FR-023, SC-007).

## R8. Testing

- **Unit (lock order, fake MariaDB session):** for each rewritten step, the statements in order. That covers the six numbering steps and the four sales order edits. Following `tests/unit/test_inventory_locks.py`, the recorded statements are compiled for MariaDB, and the tests pin:
  - the commit first;
  - then the `FOR UPDATE` and `FOR SHARE` locks in R3's order (own rows, lines, facility, warehouses);
  - then the first plain `SELECT`;
  - a refusal rolls back and writes no serial;
  - errors 1213 and 1205 become 409.
- **Unit:** `documents.lock_facility` and `assign_folio` (404, scoped `MAX`); the supplier panel rule (administrator, each privilege alone, none).
- **API:**
  - supplier detail by privilege: a Products-only user gets four empty panels (SC-001), and a full holder gets all four;
  - create and update with an unreadable list leave links unchanged (SC-002);
  - numeric payment search.
- **Integration (SQLite):**
  - consecutive serials across sequential confirmations of each type;
  - refund payments and plain payments numbered per facility;
  - insufficient stock after a prior reservation;
  - a second confirm of a completed order refused.
- **MariaDB concurrency proofs** (quickstart): SQLite serialises writers, so the races run against mbe_dev through the dev instance. Each runs before deploying the branch and again after, and cleans up its rows.
  - SC-003: 20 concurrent order confirmations;
  - SC-004: 20 concurrent payments;
  - SC-005: two orders for the last 5 units, and the double confirm.
