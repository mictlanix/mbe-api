# Research: Warehouse Receipts, Issues and Transfers

All measurements are from mbe_dev on 2026-10-25, over the last 24 months unless stated (see spec, Findings).

## R1. Serial per facility under concurrent confirms

**Decision**: the confirm (or dispatch) runs as one transaction:
1. lock the document's `facility` row `FOR UPDATE`;
2. read `MAX(serial)` for that model and facility with a **locking read** (`with_for_update(read=True)`);
3. take max + 1.

The serial is written in the same transaction as the ledger entries. Any refusal after step 1 rolls back, so no number is consumed. The helper lives in `inventory_documents.py` as `_next_serial(db, model, facility)`.

**Rationale**:
- This is spec 027's `invoice_service._next_serial`. Under REPEATABLE READ, a plain `SELECT MAX` reads the snapshot this transaction opened before it waited on the lock, so it misses the serial the previous holder committed (mictlanix/mbe#45).
- No facility repeats a serial today, and there is no unique index to fall back on.

**Alternatives considered**:
- **The current system's unlocked `MAX+1`:** this is the race itself.
- **`documents.assign_folio`:** it locks but reads `MAX` without a locking read, so it has the same race. The spec keeps it out of scope. Its fix (one `.with_for_update(read=True)`) is a follow-up for sales orders, deliveries, quotes, refunds and itineraries.
- **A unique index on `(facility, serial)`:** legacy rows with a NULL serial are allowed, but the index would need DDL on three tables, and the lock already prevents duplicates.

## R2. Available-stock checks that concurrent confirms cannot both pass

**Decision**: after the facility lock (R1), lock the affected `warehouse` rows `FOR UPDATE`, in ascending id order. Then compute per product `on_hand − reserved` with locking reads, and compare it with the quantity summed over the document's lines.
- `stock_ledger.on_hand`, `reserved` and `available` gain a keyword-only `lock: bool = False`. It adds `with_for_update(read=True)` to their `SUM`. Existing callers are unchanged.
- The checks are:
  - issue confirm: the issue's warehouse;
  - transfer dispatch: the origin;
  - cancel: every warehouse whose net reversal is outbound (R3);
  - receive and close the remainder: none, because they only move what is already in transit.
- Lock order is always facility, then warehouses by id, so two of these transactions cannot deadlock.

**Rationale**:
- The warehouse lock queues every issue, transfer and cancel that spends the same warehouse's stock (SC-003).
- The locking read is needed for the same REPEATABLE READ reason as R1.
- Warehouses are shared across facilities (14% of 2026 sales lines sell from another facility's warehouse). Locking only the facility would not serialise two facilities spending one warehouse.

**The document row comes first.** Every step that changes a document locks its own header row with `SELECT … FOR UPDATE` and then re-reads its state:
- confirm, dispatch, receive, close and cancel;
- header and line edits.

Without it, two requests on one draft both pass the draft check. The second waits on the facility lock, then posts the ledger entries again under a new serial, and two receives can take the same outstanding units twice. Lock order is document, then facility, then warehouses by id. Each step locks only one document, so this order cannot deadlock.

**Known limit**: sales-order confirmation (spec 012) checks availability with a plain read and takes no warehouse lock. An issue and a sales order confirmed at the same instant can still promise the same unit. Closing that means changing the sales-order confirm, which is outside this spec. It is recorded as a follow-up.

**Alternatives considered**:
- **Facility lock only:** it misses cross-facility warehouses.
- **Locking `lot_serial_tracking` rows directly:** it is the same effect with an uglier lock footprint and more deadlock potential.
- **A stock-balance table with a row lock:** there is none (spec 012 R4), and adding one is a redesign.

## R3. Cancelling a confirmed document

**Decision**: read every ledger entry with `source = <document type>` and `reference = <document id>`, then post one entry per row with the opposite sign, through `stock_ledger.post_movement`.
- Before posting, net the reversal per (warehouse, product). Wherever it is outbound, require `available(lock=True) − outbound ≥ 0`, or answer 409 naming the product and warehouse.
- A draft is cancelled with no ledger reads or writes.
- A cancelled document answers 409.

**Rationale**:
- "The opposite of each entry it posted" (FR-008), read from what was actually posted. It is therefore exact for receipts, issues, partly received transfers, closed remainders, and legacy one-step transfers, including the 241 whose entries don't net to zero.
- One function serves all three documents.

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
- The transit warehouse is the origin facility's, looked up with `warehouse_service.get_transit_warehouse`. Spec 013 forbids editing or deleting it, so dispatch and receive see the same one.

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

**Rationale**: clarification Q3, and the current system's scope (spec Findings). The numeric search that ignored scope is not carried over.

## R6. Facility rule on confirming steps

**Decision**: a non-administrator's `current.facility_id` must equal:
- the document's `facility` for confirm and cancel (origin facility for transfers);
- the destination warehouse's facility for receive;
- either one for close the remainder.

Otherwise the answer is 403 "Document belongs to another facility". Header and line edits are not facility-gated, matching the current system.

**Rationale**: FR-011, restoring the current system's commented-out check, and clarification Q1.

## R7. Purchase-order view and over-receipt warning

**Decision**:
- For each line with `purchase_order_detail`, the response carries:
  - `quantity_ordered` (stored snapshot);
  - `quantity_received_elsewhere`: the sum of linked lines on other receipts that are completed and not cancelled.
- Each line whose `quantity_received_elsewhere + quantity > quantity_ordered` adds a message to the response's `warnings`, naming the product code and the excess.
- Warnings are computed on every read, line save and confirm. They never block.
- Lines added by hand store `quantity_ordered = 0` (the column is NOT NULL) and no link.

**Rationale**: FR-021 and FR-022. Computing warnings on read keeps them true after other receipts change.

## R8. Complementary receipt

**Decision**: `POST /inventory-receipts/{id}/complementary`, with the create right.
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
- `POST /inventory-transfers/{id}/sales-order-items {sales_order}` requires an order that is completed and not cancelled (otherwise 409). It sums the order's stockable lines per product, skips products already on the transfer, and adds the rest.

**Rationale**: FR-003 and FR-046, with clarification Q2. The 469 legacy receipts with repeated products show why lines merge.

## R10. In-transit view

**Decision**: `GET /inventory-transfers/in-transit?facility=` returns every transfer in transit whose origin or destination warehouse belongs to the facility. The facility defaults to the caller's.
- Each outstanding line shows the product, transfer id and serial, origin, destination and outstanding quantity.
- Per product, it gives the total outstanding.
- It needs the read right on InventoryTransfers.

**Rationale**: FR-045. The transit warehouse also holds delivery stock (spec 012), so the view reads the transfers, not the warehouse balance.
