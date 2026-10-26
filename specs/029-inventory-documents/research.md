# Research: Warehouse Receipts, Issues and Transfers

All measurements are from mbe_dev on 2026-10-25, over the last 24 months unless stated (see spec, Findings).

## R1. Serial per facility under concurrent confirms

**Decision**: after R2's locks, the confirm (or dispatch) reads `SELECT MAX(serial) … WHERE facility = :f FOR UPDATE` on the document's own table, and takes max + 1.
- This is the last lock the step takes. It locks the top of that facility's range in the `(facility, serial)` unique index (`inventory_receipt_facility_serial_idx` and the same on issue and transfer; checked on mbe_dev). Two confirms of the same document type in one facility therefore queue on it, and the second reads the serial the first committed.
- Only inventory code locks these rows. A wait here can never be part of a cycle with sales, delivery or refund code, which is why the facility row is **not** locked (R2).
- The serial is assigned to the document just before the commit, after every read, so autoflush cannot write it early. Any refusal rolls back, so no number is consumed.
- If the unique index still fired, the API's global `IntegrityError` handler answers 409. No inventory-specific handling is added.

**Rationale**: under REPEATABLE READ, a `MAX` from a snapshot older than the lock misses the serial the previous holder committed (mictlanix/mbe#45, spec 027). A locking read always reads the latest committed rows.

**Alternatives considered**:
- **The current system's unlocked `MAX+1`:** this is the race itself.
- **Locking the facility row, as `documents.assign_folio` and spec 027 do:** refund confirmation and delivery stop closing take warehouse share locks (ledger inserts, through the foreign key) before they lock the facility. An inventory step locking the facility and then warehouses could deadlock with them.
- **`documents.assign_folio`:** besides the facility lock, its `MAX` is not a locking read, so it has the mbe#45 race. It stays out of scope; its fix is a follow-up.

## R2. Locking: every lock before any ordinary read

**Decision**: every step that changes a document runs one transaction in this order (`inventory_documents.begin_locked`):

1. **Pre-reads, before ending the request's opening transaction.** These are plain reads of data that cannot change underneath the step:
   - the facility → transit warehouse map (15 rows). Spec 013 makes transit warehouses fixed: they cannot be edited or deleted;
   - for a cancel, the distinct warehouses of the document's own ledger entries. The ledger is append-only, and any entry added later for this document goes to its current header warehouses or their transit, which point 3 covers. 785 legacy transfers and 813 legacy receipts have entries outside their current header (946 and 1,407 entries), because the current system allowed header edits after confirm.
2. **End the opening transaction** (`await db.commit()`; nothing is pending). `require_privilege` and the route's 404 lookup have already read, so without this the snapshot would predate the locks.
3. **Lock the document's header row** `FOR UPDATE`, with `populate_existing`, and re-check its state. Everything after this is worked out from the locked row, never from the copy loaded before the commit.
4. **For steps that move stock, lock the warehouses** with one `SELECT … WHERE warehouse_id IN (:ids) ORDER BY warehouse_id FOR UPDATE`. That is a primary-key range, locked in ascending order. The set is:
   - receipt and issue: the warehouse;
   - transfer: the origin, the destination, and the transit warehouse of the document's facility (from the map);
   - cancel: also the warehouses from the pre-read.
5. **For confirm and dispatch, take the serial lock** (R1), last.
6. **Only then make ordinary reads**: lines, products, `quantity_received`, ledger sums (`stock_ledger.available`, unchanged) and the ledger entries to reverse. The snapshot opens at the first of these, after every lock, so it includes everything the previous holders committed.

**Which locks each step takes**:
- confirm, dispatch, receive, close the remainder and cancel: document and warehouses; confirm and dispatch also take the serial lock;
- header and line edits, and the sales-order items: the document only.

**No cycles among inventory steps**: each step locks one document, then warehouses in ascending id order, then one serial range that nothing else locks.

**Stock checks**:
- issue confirm: the issue's warehouse;
- transfer dispatch: the origin;
- cancel: every warehouse whose net reversal is outbound (R3);
- receive and close the remainder: none, because they move only what is already in transit.

**Rationale**:
- **The document lock** stops two requests on one document from both acting: two confirms, two partial receives that each see 10 outstanding, a cancel that waited behind a receive, or two line additions.
- **The warehouse locks** queue every issue, transfer and cancel that spends the same warehouse, including from another facility (14% of 2026 sales lines sell from another facility's warehouse).
- **Taking locks before reads** makes ordinary reads current. The ledger therefore needs no locking reads, which matters on mbe_dev: `SUM` over `(warehouse, product)` is planned as a merge of the single-column indexes, and a share lock there would lock every warehouse's entries for that product. `stock_ledger.py` is not changed.
- **Primary-key locking only.** A lock by `facility = :f AND in_transit` is planned on mbe_dev as an index merge with a filesort. It locks every warehouse of the facility, in scan order rather than id order, which can deadlock two transfers dispatched in opposite directions.

**Known limits**:
- **Other code's ledger inserts.** Every ledger and reservation insert takes a share lock on its warehouse row through the foreign key. A delivery departure or a refund that inserts into two warehouses in the opposite order to an inventory step's locks can deadlock with it. InnoDB then rolls one back.
  - The inventory steps map MariaDB errors 1213 (deadlock) and 1205 (lock wait timeout) to 409 "The warehouse is busy, retry".
  - The other side's handling is unchanged, and out of scope. The window is the few milliseconds of an inventory step; T033 races a refund against an issue.
- **Sales-order confirmation** (spec 012) checks availability without a warehouse lock. An issue and a sales order confirmed at the same instant can still promise the same unit. This is a follow-up.

**Alternatives considered**:
- **Locking only the document:** reads after it still come from the snapshot opened by the privilege check.
- **Locking reads on the ledger sums:** wide share locks and deadlocks, as above.
- **Share locks on the warehouse rows,** which would be compatible with the foreign-key locks: they would not serialise two inventory steps spending one warehouse.
- **A stock-balance table with a row lock:** there is none (spec 012 R4), and adding one is a redesign.

## R3. Cancelling a confirmed document

**Decision**: after R2's locks, which include every warehouse of the document's entries, read every ledger entry with `source = <document type>` and `reference = <document id>`. Post one entry per row with the opposite sign, through `stock_ledger.post_movement`.
- Before posting, net the reversal per (warehouse, product). Wherever it is outbound, require `available − outbound ≥ 0`, or answer 409 naming the product and warehouse.
- A draft is cancelled with no ledger reads or writes, but under the same document lock and facility rule (FR-011).
- A cancelled document answers 409.
- Cancelling a transfer also clears `in_transit`, in the same transaction. Its lines keep `quantity_received` as it was before the cancel, so the response shows what had been received; the reversal is in the ledger.

**Rationale**:
- "The opposite of each entry it posted" (FR-008), read from what was actually posted. It is exact for receipts, issues, partly received transfers, closed remainders and legacy one-step transfers, including the 241 whose entries don't net to zero and those with entries outside their current header.
- Types 3, 4 and 5 are written only by these three documents (deliveries use 10), so a reversal cannot pick up another document's entries.
- One function serves all three documents.

**Cost**: `lot_serial_tracking` (639,854 rows) has no index on `(source, reference)`, so the two reads (the pre-read and the entries) each scan it. At about 10 cancels a month this is acceptable, and both are plain reads with no locks; an index is a later optimisation.

**Alternatives considered**:
- **Recomputing from the lines:** it is wrong for partial receipts and for legacy transfers whose entries differ from their lines.
- **Deleting entries:** this breaks append-only (spec 012 FR-062).

## R4. Transfer state and partial receipt

**Decision**: migration 026.
- `inventory_transfer.in_transit TINYINT(1) NOT NULL DEFAULT 0`.
- `inventory_transfer_detail.quantity_received DECIMAL(18,4) NULL`.
- The transfer's state is derived:
  - `cancelled`: `cancelled`;
  - `draft`: not `completed`;
  - `in_transit`: `completed` and `in_transit`;
  - otherwise `received`.
- **Dispatch** sets `completed = 1`, `in_transit = 1` and every `quantity_received = 0`.
- **Receive** adds to `quantity_received`, which may not exceed `quantity`. It clears `in_transit` when no line is outstanding.
- **Close the remainder** posts the outstanding quantities from transit to the origin and clears `in_transit`.
- A NULL `quantity_received` (legacy) reads as `quantity` once the transfer is completed.
- The transit warehouse is that of the transfer's own `facility` (set from the origin when the draft was made, and fixed once dispatched), taken from R2's pre-read map and locked by primary key. It is never derived from the origin warehouse's *current* facility, which a warehouse edit could change. Spec 013 forbids editing or deleting transit warehouses, so dispatch, receive, close and cancel all see the same one.

**Rationale**:
- Legacy rows need no backfill: 3,318 confirmed transfers default to `in_transit = 0` with NULL received quantities, and read as received in full (FR-044a).
- The current system never writes either column, so a transfer it confirms reads the same way.

**Alternatives considered**:
- **A `status` column:** it needs a backfill, and the other documents derive state from flags too.
- **`quantity_received NOT NULL DEFAULT 0` with a backfill:** it would misreport any transfer the current system confirms after the migration.
- **Storing the transit warehouse on the transfer:** it is not needed while spec 013 keeps one fixed transit per facility.

## R5. List scope and the wider view

**Decision**:
- **Wider view:** the caller is an administrator, or `has_any_privilege(db, user, [(object, DELETE)])` holds for the document's object. They see all documents, optionally filtered by `warehouse`.
- **Default:** `warehouse == pos_warehouse OR creator == current.employee_id`. For transfers, the warehouse condition is `warehouse == pos OR warehouse_to == pos`. The point-of-sale warehouse comes from `PointSale.warehouse` for `current.point_sale_id`. Without a point of sale, only the caller's own documents are listed.
- **Filters:** `status`, `warehouse`, `skip` and `limit`, newest first.
  - `status` accepts only that document's values: `draft`, `completed` or `cancelled` for receipts and issues (not `DocumentStatus.PAID`, which is refused with 422), and `draft`, `in_transit`, `received` or `cancelled` for transfers.
  - `warehouse` matches the document's warehouse; for transfers it matches the origin or the destination. In the default scope it narrows the scope further and never widens it.

**Rationale**: clarification Q3, and the current system's scope (spec Findings). The numeric search that ignored scope is not carried over.

## R6. Facility rule on confirming steps

**Decision**: a non-administrator's `current.facility_id` must equal:
- the document's `facility` for confirm and for every cancel, draft or confirmed (origin facility for transfers);
- the destination warehouse's facility for receive;
- either one for close the remainder: the transfer's own `facility`, or the destination warehouse's facility.

Otherwise the answer is 403 "Document belongs to another facility". Header and line edits are not facility-gated, matching the current system.

**Rationale**: FR-011, restoring the current system's commented-out check, and clarification Q1.

## R7. Purchase-order view and over-receipt warning

**Decision**:
- For each line with `purchase_order_detail`, the response carries:
  - `quantity_ordered`: the purchase-order line's own `quantity`, read live. The stored column is not used for this, because the current system's complementary receipts stored the *remaining* quantity in it;
  - `quantity_received_elsewhere`: the sum of linked lines on other receipts that are completed and not cancelled.
- Per purchase-order line, sum this receipt's linked lines (legacy receipts may link one order line twice). If `quantity_received_elsewhere + that sum > ordered`, add one message to `warnings`, naming the product code and the excess.
- Warnings are computed on every read, line save and confirm. They never block.
- Lines added by hand store `quantity_ordered = 0` (the column is NOT NULL) and no link.

**Rationale**: FR-021 and FR-022. Computing warnings on read keeps them true after other receipts change.

## R8. Complementary receipt

**Decision**: `POST /inventory-receipts/{id}/complementary`, with the create right.
- **Locking:** following R2, the request ends its opening transaction, then locks the `purchase_order` row `FOR UPDATE` before any ordinary read. The source receipt and the returned draft are re-read with `populate_existing`, so the response is never stale. Two simultaneous requests for the same order therefore queue, and the second finds the draft the first created.
- **The source** is any non-cancelled receipt linked to the order, confirmed or draft. If the source is itself the open draft for that order and warehouse, it is the one returned.
- The source receipt must have a purchase order that is completed and not cancelled (otherwise 409, with the reason; a receipt with no purchase order is also 409, while an unknown receipt is 404).
- Remaining per order line of that warehouse = `PurchaseOrderDetail.quantity` − linked quantities on completed, non-cancelled receipts. Lines with nothing remaining are skipped; if none remain, 409 "fully received".
- If an open draft exists for the same order and warehouse (oldest first), answer 200 with it.
- Otherwise create a draft (201) for the order and warehouse, with the source's facility. Lines are linked, with `quantity = remaining`, `quantity_ordered = PurchaseOrderDetail.quantity`, and code and name snapshots from the product.

**Rationale**: FR-023. Legacy stored the *remaining* quantity as ordered; storing the true ordered quantity keeps R7's warning correct.

## R9. Lines and the sales-order items

**Decision**:
- `POST /{id}/lines {product, quantity}` adds to the existing line of that product, or creates one with snapshots. When a legacy draft repeats the product, it adds to the oldest of those lines (the lowest id).
  - The product must exist (404) and be stockable (422), and the quantity must be > 0.
  - The product's status is not checked, when adding or at confirm. A product discontinued after it went on a draft still confirms (spec Edge Cases), and adding a discontinued product is not refused either: the warehouse may still hold or receive it.
- `PUT /{id}/lines/{line_id} {quantity > 0}` and `DELETE` work on drafts only.
- **Warehouse status** (active, inactive, archived) is not checked when a warehouse is set on a document, matching every other service in the API, sales included. Only in-transit warehouses are refused (FR-009).
- `POST /inventory-transfers/{id}/sales-order-items {sales_order}` requires an order that is completed and not cancelled (otherwise 409). It sums the order's stockable lines per product, skips products already on the transfer, and adds the rest.

**Rationale**: FR-003 and FR-046, with clarification Q2. The 469 legacy receipts with repeated products show why lines merge.

## R10. In-transit view

**Decision**: `GET /inventory-transfers/in-transit?facility=` returns every transfer in transit whose origin or destination warehouse belongs to the facility. The facility defaults to the caller's.
- Each outstanding line shows the product, transfer id and serial, origin, destination and outstanding quantity.
- Per product, it gives the total outstanding.
- It needs the read right on InventoryTransfers.

**Rationale**: FR-045. The transit warehouse also holds delivery stock (spec 012), so the view reads the transfers, not the warehouse balance.
