# Research: Purchase Requests, Purchase Orders and Average Cost

All measurements are from mbe_dev on 2026-10-26 (see spec, Findings). "Locking pattern" means spec 029's (`inventory_documents` R1, R2):
- plain pre-reads;
- a commit that ends the request's opening transaction;
- locking reads only;
- then ordinary reads, whose REPEATABLE READ snapshot opens after the last lock and so sees what earlier lock holders committed.

## R1. States on the existing flags

**Decision**: derive the state from the legacy flags and store nothing new for it. NULL counts as false.

| Document | State | `completed` | `approved` | `cancelled` |
|---|---|---|---|---|
| Request | `draft` | 0 | 0 | 0 |
| Request | `submitted` | 1 | 0 | 0 |
| Request | `approved` | 1 | 1 | 0 |
| Request | `cancelled` | any | any | 1 |
| PO | `draft` | 0 | — | 0 |
| PO | `completed` (confirmed) | 1 | — | 0 |
| PO | `cancelled` | any | — | 1 |

- **PO status values:** `draft`, `completed` and `cancelled`, the same values receipts use (`derive_status`). The spec's "confirmed" is `completed` on the wire.
- **`purchase_order.approved`** is written 0 and never read. Since 2025-04, every PO has `approved = 0`.

**Rationale**: the data already has these shapes:
- requests: 63 drafts, 48 submitted, 2,123 approved, and 38 cancelled;
- POs since 2025-04: 191 drafts, 2,893 confirmed and 114 cancelled.

The current system reads the same flags until cutover.

**Alternatives considered**: a status column. It needs a backfill, and it would duplicate the flags the current system still reads.

## R2. Request serial on submit

**Decision**: submit runs the locking pattern, adapted because `purchase_request` has no facility column.
1. **Pre-read:** the facility → transit-warehouse map (`inv._transit_map`) and the warehouse → facility map (every warehouse; a few dozen rows), then commit. Transit rows are fixed, but a warehouse's facility can be edited (`WarehouseUpdate.facility`), so the map is re-checked after the locks (step 4).
2. **Lock the request row** `FOR UPDATE`, and re-read it (`populate_existing`).
3. **Lock the transit warehouse** of the header warehouse's facility (`inv.lock_warehouses`). The facility comes from the pre-read map, looked up in memory with the locked row's warehouse: an ordinary read here would open the snapshot before the numbering lock, and `MAX(serial)` could miss a serial another submit just committed. Spec 029 avoids this because its documents carry `facility` on the locked row; `purchase_request` does not.
   - This is the same numbering lock receipts, issues, transfers and counts take for that facility, so every facility numbering step queues on one row.
   - A facility without a transit warehouse answers 422, as in spec 029.
4. **Checks and serial:**
   - check the state, that there are lines, and that every quantity is above 0;
   - re-read the header warehouse's facility (an ordinary read is safe now); if it differs from the pre-read map, answer 409 "The warehouse changed facility, retry" before numbering;
   - when `serial` is NULL, read `MAX(serial) + 1` over requests joined to warehouses of that facility, with an ordinary read;
   - set `completed = 1`.

A refusal rolls back, so no number is used. Deadlocks and lock waits answer 409 through `inv.busy_as_conflict`.

**Rationale**:
- The facility lock is what makes `MAX + 1` safe (spec 029 R1). It is also the lock spec 029's numbering already queues on.
- The facility comes from the header warehouse when the request is locked.
- Spec clarification Q5 stops a numbered request from moving to another facility, so the facility can't change between submit and resubmit.

**Alternatives considered**:
- **A unique `(facility, serial)` index:** there is no facility column, and the current system's 6 duplicate pairs would block the index.
- **Locking the header warehouse only:** requests of two warehouses of one facility would both read the same MAX.

## R3. One live PO line per request line (FR-026), under concurrency

**Decision**: every step that puts a request line on a PO, takes it off, or depends on whether it is on one, locks that request line's row `FOR UPDATE` before deciding. Rows are locked in ascending id order.

| Step | Locks, in order |
|---|---|
| Add a request's lines to a PO (FR-023) | PO row → the request's line rows |
| Create a PO from a to-purchase group (FR-025) | the group's line rows (the new PO doesn't exist yet) |
| Remove a PO line linked to a request line | PO row (the line row is freed by the delete itself) |
| Cancel a draft PO (FR-028) | PO row |
| Mark or unmark a request line (FR-008) | request row → that line row |
| Cancel a request (FR-009) | request row → its line rows |

**No ordinary read may run between the commit and the last lock** of a step: the first ordinary read opens the REPEATABLE READ snapshot. So:
- the facility rule (`assert_facility`) and the request's facility use the warehouse → facility map pre-read before the commit (R2), looked up in memory. After the last lock, the header warehouse's facility is re-read; a mismatch with the map answers 409 "retry" (a warehouse moved facility in between);
- the ids of the rows to lock (a request's lines, a to-purchase group's lines, the request of a line being marked) are pre-read before the commit; after the locks, the pending check re-reads them, and rows that appeared in between are not added.

The check that follows the locks is an ordinary read, so it sees every committed PO line:
- **Pending:** no `purchase_order_detail` links the line from a PO that is not cancelled.
- A line that is no longer pending is left out and named in the response's `skipped`. When nothing is left, the call answers 409.

The adders read the request header after locking its lines. A concurrent request cancel holds the header and then waits for the lines, so:
- either the adder commits first, and the cancel then sees the new PO line and refuses;
- or the cancel commits first, and the adder sees the request cancelled and skips its lines.

Removing or cancelling never makes a line "doubled", so those two steps need no request-line lock.

Every write to a request or a PO, including hand edits of headers and lines, first commits and locks its document row `FOR UPDATE` (`populate_existing`), then checks the state. Without it a line added from a stale "draft" read could commit after a concurrent confirm and sit on a confirmed PO with no receipt.

**Rationale**: a unique index can't express "on a non-cancelled PO", because `cancelled` is on the header. Row locks on the request lines are local: only steps on the same lines wait.

**Alternatives considered**:
- **A `live` flag on PO lines with a unique key:** that is two writes for every PO cancel, and a migration of 8,369 linked rows.
- **Locking the request header only:** "to purchase" groups span many requests, so it would lock dozens of headers in an order that depends on the group.

## R4. PO confirm generates receipts (FR-027)

**Decision**: one transaction in `inv.busy_as_conflict`.
1. **Lock:** commit, then lock the PO row `FOR UPDATE` and re-read it.
2. **Checks:** draft, supplier set, at least one line, and every quantity above 0. A line priced at 0 gives a warning.
3. **Pending quantity:** for every stockable line, the ordered quantity minus what non-cancelled receipts (draft or confirmed) already hold for that line. One grouped query. On a first confirm this is the ordered quantity.
4. **Receipts:** per destination warehouse, in ascending id order, one draft `InventoryReceipt`:
   - `facility` = the warehouse's facility, `purchase_order` = the PO, and no serial;
   - one `InventoryReceiptDetail` per stockable line with something pending: `purchase_order_detail` = the line, `quantity` = pending, `quantity_ordered` = ordered, and the code and name snapshots.

   Warehouses whose lines are all non-stockable, or have nothing pending, get no receipt.
5. **State:** `completed = 1`, the updater and the time. Commit.

Warehouses aren't locked: drafts take no serial and post no stock. The PO row lock with the state check is what keeps receipts from being duplicated. A second or concurrent confirm finds the PO completed and answers 409.

**Rationale**: this is the current system's `GenerateInventoryEntries` with its three defects fixed:
- it ran outside the confirm transaction;
- it had no guard against running twice (13 duplicated receipt pairs, 6 of them posted twice);
- it included non-stockable lines.

Spec 029's receipt already shows the ordered quantity live from the PO line (R7) and gives the complementary receipt (R8).

**Alternatives considered**: reuse `complementary_receipt`. It needs an existing receipt as its source, and it locks per warehouse rather than per PO.

## R5. Average cost on receipt confirm (FR-031 to FR-036, FR-039)

**Decision**: a new `purchase_cost_service`, called from `inv.confirm_simple` only when `kind is RECEIPT` and the locked receipt has a `purchase_order`. It works in two steps:

1. **Lock costs** right after `begin_locked`, before any ordinary read:
   - Read the receipt's products from lines with a PO line, using a locking read: `SELECT DISTINCT product … FOR UPDATE` on `inventory_receipt_detail`. The receipt row lock already stops line edits, so this just avoids opening the snapshot.
   - Lock those products' list-0 rows: `SELECT … FROM product_price WHERE list = 0 AND product IN (…) ORDER BY product FOR UPDATE`.
   - A second receipt of the same product, in any facility, waits here. When it gets the lock, its snapshot sees the first receipt's ledger entries and new cost together.
2. **Apply** after the checks and before the ledger posts:
   - **Q:** read company-wide on hand for all of the receipt's products with one grouped `SUM(quantity) … WHERE product IN (…) GROUP BY product` over `lot_serial_tracking`. All warehouses count, transit included. Measured: under 1 s for the 212 busiest products.
   - **Each line, in line id order:**
     - Q is the on-hand total so far, floored at 0. C is the cost row's price, or 0 when there is none.
     - U = PO line `price × (1 − discount)`. The `tax_included` flag is ignored (FR-034).
     - When U = 0 the line is skipped with a warning (FR-035).
     - Otherwise the new cost is `(Q×C + R×U) / (Q + R)`, rounded half up to 4 decimals. A change row is staged, and the running Q and C for that product advance by R and to the new cost (FR-033).
   - **Write:** the cost row, at the product's final cost, is created with the default profits when missing, or updated.

**Missing cost rows:** 50 products have none. Two concurrent first receipts of one of them can both find no row; the unique `(product, list)` key or a gap-lock deadlock then rolls one back, which answers 409 "busy, retry". That is rare and safe.

**Lock order:**
- confirm: receipt → warehouses (ascending) → cost rows (ascending);
- receipt cancel (R6): the same order;
- nothing else writes list 0 (spec 004 FR-005a), and the current system stops writing at cutover.

So no two steps take these locks crosswise.

**Precision (spec FR-039 amended at plan time):**
- `product_price.price` is `DECIMAL(18,4)`, and so is the cost snapshot on sales lines (`sales_order_detail.cost`). Widening the column would change every price list for one rounding digit.
- Cost is rounded to 4 decimals. The history keeps U at the PO line's 7 decimals.
- SC-004 compares to 4 decimals.

**Rationale**: the averaging is in the same transaction as the ledger posts (FR-031), it is serialized per product across facilities (FR-033), and it touches only products on linked lines (FR-032).

**Alternatives considered**:
- **Locking cost rows after the lines are read:** the snapshot would already be open, so a concurrent receipt's ledger entries would be missing from Q while its new cost was read through the lock. Cost would drift.
- **Recomputing cost from the whole history:** there is no history before cutover.

## R6. Receipt cancel restores cost (FR-037)

**Decision**: `inv.cancel` for a completed `RECEIPT` with a `purchase_order` calls `purchase_cost_service` after `begin_locked`:

1. **Lock:**
   - read the products the receipt changed, with a locking read: `SELECT DISTINCT product FROM product_cost_change WHERE receipt = :id AND reverted_change IS NULL FOR UPDATE`;
   - lock their cost rows, ascending.
2. **For each product** (after the stock checks, when the reversal is posted):
   - if the product's latest change (highest id) belongs to this receipt and is not a restore, set cost back to the `previous_cost` of the receipt's first change for that product, and stage a change row with `reverted_change` pointing to that first change and Q, R and U NULL;
   - otherwise keep cost and add the warning "Cost of {code} not restored: changed since by receipt {serial or id}".

The warnings are returned on the receipt response's `warnings`.

**Rationale**: sales and other movements don't change average cost. So when no later receipt touched the product, the cost from before is exactly what it would have been without this receipt (spec Clarifications).

**Alternatives considered**: re-averaging backwards, `(C_now×(Q+R) − R×U) / Q`. It is inexact once stock has moved, and it divides by a Q that may now be 0.

## R7. Changes to spec 029's receipt code

**Decision**:
- **`inventory_documents.confirm_simple`:**
  - When `kind is RECEIPT` and `document.purchase_order` is set, call `purchase_cost_service.lock(db, document_id)` right after `begin_locked`, and `apply(...)` before posting.
  - The warnings are kept on `document.__dict__['cost_warnings']`.
- **`inventory_documents.cancel`:** the same, with `lock_restore` and `restore`.
- **`inventory_receipt_service.attach_derived`:** the over-receipt warnings and any `cost_warnings` together make up `warnings`.
- **`inventory_receipt_service.complementary_receipt`:** non-stockable order lines are left out of the remaining lines, as on confirm (R4); otherwise its draft is refused 422 on confirm.
- **`inventory_receipt_service.update`:** moving the warehouse of a receipt linked to a PO answers 409 "A purchase order's receipt keeps its warehouse".
  - Its lines are linked to that warehouse's PO lines, and the complementary receipt (R8 of spec 029) finds receipts by PO and warehouse.
  - Hand-added receipts have no PO and are unaffected.

**Rationale**: the hooks sit where the locks and the transaction are. Two `if` statements are smaller than a hook registry for one consumer (Principle I).

**Alternatives considered**:
- **A post-commit hook:** cost would leave the receipt's transaction, against FR-031.
- **A `Kind` callback field:** generic machinery for one use.

## R8. To purchase by supplier (FR-024, FR-025, FR-023)

**Decision**:
- **Pending request lines:** the request is approved and not cancelled, the line is marked, and no non-cancelled PO line links it.
- **Groups:** `GET /purchase-orders/to-purchase` groups the pending lines by `(product.supplier, warehouse.facility)` of the line's warehouse. When the line's warehouse is NULL (legacy), the header's is used.
  - Each group shows: supplier (NULL for "no supplier"), facility, line count, estimated total (Σ quantity × cost, cost 0 when missing), and the lines.
  - Optional filter: `facility`.
- **`POST /purchase-orders/to-purchase` `{supplier, facility}`:**
  - pre-reads the group's line ids before the commit, then locks those rows, ascending (R3), re-reads what is still pending, and creates one draft PO for the supplier with one line per pending line;
  - each line has the request line's product, quantity and warehouse, price = cost (0 with a warning when missing), the product's tax rate, and the fixed defaults (FR-021);
  - lines taken in the meantime are listed in `skipped`. 409 when none is left, and 422 for `supplier = null`.
- **`POST /purchase-orders/{id}/request-lines` `{purchase_request}`:**
  - adds that approved request's pending lines to a draft PO;
  - warns, per line, when the product's supplier differs from the PO's (FR-023);
  - a request that is not approved answers 409.

**Rationale**: the grouping follows clarification Q1. 19 of 1,797 request-fed POs combine requests and 1 mixes facilities, so a group seldom spans more than one PO.

## R9. PDF and email (FR-040 to FR-043)

**Decision**:
- **Template:** a new `purchase_order.html`, letter size, on spec 019's `rendering.render_pdf`.
- **Context:** `print_contexts.purchase_order_context(db, order)`.
  - Its header block comes from `header_context` for the facility of the warehouse of the PO's lowest-id line. A PO has no facility of its own; only 8 POs span facilities.
  - It shows the supplier's code, name and main address, if any, and the issuing employee (the creator).
  - Lines: code, name, warehouse code, quantity, unit price before tax, and amount.
  - Totals: subtotal, tax at each line's rate (always added, since `tax_included` is off on new lines and ignored on old ones; spec Clarifications), and total.
  - Also the estimated receipt date and the comment.
- **Email:** `purchase_order_service.email_order` follows `sales_quote_service.email_quote`:
  - it requires a confirmed PO;
  - the PDF is attached as `orden-compra-{id:08d}.pdf`;
  - it sends through `mail.get_mailer`, and a `MailError` answers 502;
  - an incidence is recorded only after the send, with source 5, reason "Purchase order emailed", and context "To: …".
  - The body is `PurchaseOrderEmailRequest` = `QuoteEmailRequest` (1–10 addresses, validated and deduplicated) plus an optional `message` of up to 1,000 characters, put above the signature.
  - The UI fills `to` from the supplier's contacts (spec 021's supplier detail).

**Rationale**: the same path as quotes, so the mail configuration, error mapping and audit stay identical.

## R10. Rights and the facility rule

**Decision**:
- **Requests:** `require_privilege(PURCHASE_REQUEST, …)` per route, and `require_privilege(PURCHASE_REQUEST_APPROVAL, …)` for the queue, approve and reject.
- **Marking:** `PURCHASES_ORDERS` update.
- **POs:** `PURCHASES_ORDERS`, as FR-029 says.
- **Facility:** edit, submit and cancel of a request call `inv.assert_facility(current, facility_of(header warehouse))` once the row is locked. Approve, reject, mark and every PO route don't.

## R11. Lists

**Decision**:
- **Requests:**
  - by default, the caller's own (created or updated by their employee), excluding cancelled;
  - `all=true` lists everyone's; any reader may use it (FR-012);
  - filters: `status`, `warehouse`, `facility` (through the warehouse), `creator`, `serial`, `id`, `from`, `to` (date);
  - newest first.
- **Approval queue:** submitted requests, oldest first, optional `facility`, with line count.
- **POs:** the same default scope and `all`, with filters `status`, `supplier`, `creator`, `id`, `from`, `to`.
  - The detail adds per line `quantity_received` (confirmed, non-cancelled receipts) and `quantity_pending` = ordered − received, floored at 0 (FR-030).
- Each request line shows `purchase_order_detail`, `purchase_order` and `purchase_order_status` of its live PO line (FR-010).

## R12. Migration 028

**Decision**: `028_purchasing.sql`, idempotent, with a rollback:
1. **`purchase_request`:** add `approver INT NULL` (FK employee) and `approval_time DATETIME NULL`.
2. **`purchase_order_detail`:** add an index and FK on `purchase_request_detail`. There are 8,369 linked rows and no orphans (measured), so the FK is safe.
3. **New table `product_cost_change`**, described in the data model.

No backfill: past approvals have no approver, and cost has no history before cutover.
