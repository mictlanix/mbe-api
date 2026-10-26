# Research: Warehouse Receipts, Issues and Transfers

All measurements are from mbe_dev on 2026-10-25, over the last 24 months unless stated (see spec, Findings).

## R1. Serial per facility under concurrent confirms

**Decision**: the confirm (or dispatch) takes its locks as in R2, which includes the document's `facility` row `FOR UPDATE`. It then reads `MAX(serial)` for that model and facility and takes max + 1.
- The serial is written in the same transaction as the ledger entries. Any refusal rolls back, so no number is consumed.
- The helper is `next_serial(db, model, facility)` in `inventory_documents.py`.
- It keeps spec 027's locking read (`with_for_update(read=True)`) on the `MAX` as a second guard. Under R2's ordering a plain read would already be current, but the locking read costs nothing here, because the unique index makes it a short range read.

**Backstop**: all three tables already have `UNIQUE (facility, serial)` (`inventory_receipt_facility_serial_idx` and the same on issue and transfer; checked on mbe_dev). Legacy NULL serials are allowed by it. If it ever fires, the `IntegrityError` maps to 409 "Serial already taken, retry", never a 500.

**Rationale**: under REPEATABLE READ, the snapshot is fixed at a transaction's first ordinary read. Any `MAX` taken from a snapshot older than the lock misses the serial the previous holder committed (mictlanix/mbe#45, spec 027).

**Alternatives considered**:
- **The current system's unlocked `MAX+1`:** this is the race itself.
- **`documents.assign_folio`:** it locks the facility, but the caller's snapshot may predate the lock and its `MAX` is not a locking read, so it has the same race. The spec keeps it out of scope; its fix is a follow-up for sales orders, deliveries, quotes, refunds and itineraries.
- **Relying on the unique index alone:** concurrent confirms would fail with errors instead of queueing.

## R2. Locking: every lock before any ordinary read

**Decision**: every step that changes a document runs one transaction in this order:

1. **End the request's opening transaction** (`await db.commit()`; nothing is pending). `require_privilege` and the route's 404 lookup have already made ordinary reads, so without this the snapshot would predate the locks.
2. **Lock the document's header row** `FOR UPDATE` (`populate_existing`), and re-check its state.
3. **For steps that number or move stock:**
   - lock the `facility` row `FOR UPDATE`;
   - then lock every warehouse the step can touch, in ascending id order, with one `SELECT … FOR UPDATE`:
     - receipt and issue: the warehouse;
     - transfer: the origin, the destination, and the origin facility's transit warehouse, found by `facility = document.facility AND in_transit`.
4. **Only then make ordinary reads:** lines, products, `quantity_received`, ledger sums (`stock_ledger.available`, unchanged) and the ledger entries to reverse. The snapshot opens at the first of these, after every lock, so it includes everything the previous holders committed.

The steps are:
- confirm, dispatch, receive, close the remainder and cancel take all three levels;
- header and line edits and the sales-order items take only the document lock.

**Lock order** is document, then facility, then warehouses by id. Each step locks one document, so no cycle is possible.

**Stock checks**:
- issue confirm: the issue's warehouse;
- transfer dispatch: the origin;
- cancel: every warehouse whose net reversal is outbound (R3);
- receive and close the remainder: none, because they move only what is already in transit.

**Rationale**:
- **Locking the document** stops two requests on one document from both acting: two confirms, two partial receives that each see 10 outstanding, a cancel that waited behind a receive, or two line additions.
- **Locking the warehouses** queues every issue, transfer and cancel that spends the same warehouse, including from another facility (14% of 2026 sales lines sell from another facility's warehouse).
- **Taking locks before reads** makes ordinary reads current. The ledger therefore needs no locking reads, which matters on mbe_dev: `SUM` over `(warehouse, product)` is planned as a merge of the single-column warehouse and product indexes, and a share lock there would lock every warehouse's entries for that product and could deadlock two facilities. Reservations have only single-column indexes too. `stock_ledger.py` is not changed.

**Known limit**: sales-order confirmation (spec 012) takes no warehouse lock. An issue and a sales order confirmed at the same instant can still promise the same unit. Fixing that means changing the sales-order confirm, which is outside this spec; it is recorded as a follow-up.

**Alternatives considered**:
- **Locking only the document, as last revised:** reads after it still come from the snapshot opened by the privilege check.
- **Locking reads on the ledger sums:** wide share locks and deadlocks, as above.
- **A stock-balance table with a row lock:** there is none (spec 012 R4), and adding one is a redesign.

## R3. Cancelling a confirmed document

**Decision**: after R2's locks, read every ledger entry with `source = <document type>` and `reference = <document id>`, and post one entry per row with the opposite sign through `stock_ledger.post_movement`.
- Before posting, net the reversal per (warehouse, product). Wherever it is outbound, require `available − outbound ≥ 0`, or answer 409 naming the product and warehouse.
- A draft is cancelled with no ledger reads or writes, but under the same document lock and facility rule (FR-011).
- A cancelled document answers 409.

**Rationale**:
- "The opposite of each entry it posted" (FR-008), read from what was actually posted. It is exact for receipts, issues, partly received transfers, closed remainders and legacy one-step transfers, including the 241 whose entries don't net to zero.
- Types 3, 4 and 5 are written only by these three documents (deliveries use 10), so a reversal cannot pick up another document's entries.
- One function serves all three documents.

**Cost**: `lot_serial_tracking` has no index on `(source, reference)`, so the read scans about 1.38M rows. At about 10 cancels a month this is acceptable, and it is a plain read with no locks; an index is a later optimisation.

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
- The transit warehouse is that of the transfer's own `facility` (set from the origin when the draft was made, and fixed once dispatched), found by R2's warehouse lock query. It is never derived from the origin warehouse's *current* facility, which a warehouse edit could change. Spec 013 forbids editing or deleting transit warehouses, so dispatch, receive, close and cancel all see the same one.

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
- either one for close the remainder.

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
- **Locking:** following R2, the request ends its opening transaction, then locks the `purchase_order` row `FOR UPDATE` before any ordinary read. Two simultaneous requests for the same order therefore queue, and the second finds the draft the first created.
- **The source** is any non-cancelled receipt linked to the order, confirmed or draft. If the source is itself the open draft for that order and warehouse, it is the one returned.
- The source receipt must have a purchase order that is completed and not cancelled (otherwise 409, with the reason).
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
