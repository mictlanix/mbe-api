# Feature Specification: Warehouse Receipts, Issues and Transfers

**Feature Branch**: `029-inventory-documents`

**Created**: 2026-10-25

**Status**: Draft

**Input**: Implements the three inventory documents of the inventory epic (mictlanix/mbe-roadmap#6), within §1–3 of `docs/specs/04-inventory.md`, with the corrections listed below:
- mictlanix/mbe-roadmap#58, warehouse receipts;
- mictlanix/mbe-roadmap#60, warehouse issues;
- mictlanix/mbe-roadmap#62, inventory transfers.

Each document goes from draft to confirmed, and may then be cancelled. Confirming and cancelling post to the stock ledger. The current system's behaviour is in `Web/Controllers/Mvc/InventoryController.cs` on `origin/casa-maestra-2026` of mictlanix/mbe.

Inventory moves from the current system to the new MBE in a single cutover. The two systems never record inventory documents at the same time.

## Findings that shape this spec

### The current system (`mictlanix/mbe`)

- **Flow.** All three documents follow the same steps: create a draft, add lines (each new line starts at quantity 1), edit quantities, confirm, and optionally cancel.
  - Confirm sets a serial per facility, marks the document completed and posts one ledger entry per line.
  - Lines snapshot the product code and name.
- **Ledger types:** issue 3, receipt 4, transfer 5.
  - A receipt posts +quantity in its warehouse.
  - An issue posts −quantity.
  - A transfer posts −quantity at the origin and +quantity at the destination in one step, with no transit.
- **Privileges are checked only in the views.** The controller requires a login and nothing more, so any user can call any action directly.
  - The views show "New" with the create right on InventoryReceipts (15), InventoryIssues (16) or InventoryTransfers (17), and "Cancel" with the delete right.
- **Lists.**
  - Receipts and issues show the documents of the caller's point-of-sale warehouse plus those the caller created.
  - Transfers show those where the caller's warehouse is the origin or the destination, plus those the caller created.
  - On receipts only, a search for `*` by a user with the delete right shows everything.
  - Numeric searches ignore the scope and find any document.
- **Defects to fix, not carry over:**
  - `ConfirmIssue` and `ConfirmTransfer` read the document before checking that it exists, so an unknown id throws an error. `ConfirmTransfer` also redirects a missing or closed transfer to the issues page.
  - **Cancel** has no state guard and no reversal. It sets the cancelled flag on any document, even a confirmed one, and leaves the stock booked. Two receipts in mbe_dev are both completed and cancelled.
  - **Confirm's warehouse check is commented out.** It refused non-administrators confirming a document of another facility.
  - **Closed documents can still be edited.** Header and line edits never check completed or cancelled; only the edit pages redirect.
  - **Stock checks are weak.**
    - They use on-hand stock and ignore reservations.
    - They check each line alone, so two lines of the same product can together exceed stock.
    - The transfer quantity edit checks the old quantity, not the new one.
  - **Serial race.** The serial is `MAX(serial)+1`, read outside the transaction (mictlanix/mbe#45).
    - For transfers, `MAX` over a facility with no numbered transfers returns null, so the serial stays null for good.
    - Facilities 47, 50 and 51 have never numbered a transfer.
  - **No stockable check** when lines are added, or when an issue is confirmed.
  - **`AddTransferItems`** adds every stockable line of any sales order, even a draft or cancelled one, at full quantity. Running it twice duplicates the lines.
- **Purchase-order receipts.** Completing a purchase order creates one draft receipt per destination warehouse. This is `PurchasesController.GenerateInventoryEntries`, and it stays in the Purchasing epic.
  - Receipt lines link to purchase-order lines and store the ordered quantity.
  - Nothing prevents receiving more than ordered.
- **Complementary receipt.** It is offered on a receipt whose purchase order is completed and not cancelled, when some of that order's lines for the receipt's warehouse are still under-received. Only completed, non-cancelled receipts count toward the received quantity.
  - It returns the open draft receipt for that order and warehouse if one exists.
  - Otherwise it creates one with the remaining quantities.
  - It stores the *remaining* quantity as "ordered", unlike the receipts made when the order completes.
- **Supplier returns.** The issue's supplier-return link is never read or written.

### This API today

- `app/models/inventory.py` maps all six tables, the ledger (`lot_serial_tracking`) and reservations (`lot_serial_rqmt`). No service, route or test uses the document tables.
- `app/services/stock_ledger.py` posts append-only entries and computes available stock (on hand minus sales-order reservations), per spec 012 FR-055–062.
- Each facility has one in-transit warehouse (spec 013). Delivery dispatch (spec 012) moves stock from the dispatch warehouse to it, then out of it on delivery.
- `invoice_service._next_serial` (spec 027) assigns serials safely under concurrent confirms. `documents.assign_folio`, used by sales orders and deliveries, locks the facility but reads `MAX` without a locking read, which leaves the same race open.
- No list in the API yet uses "the caller's warehouse plus what the caller created".

### Measured on mbe_dev (2026-10-25, last 24 months)

| | Receipts | Issues | Transfers |
|---|---:|---:|---:|
| Total | 11,127 | 2,354 | 3,409 |
| Confirmed | 10,472 | 2,207 | 3,318 |
| Open drafts | 542 | 68 | 80 |
| Cancelled drafts | 111 | 79 | 11 |
| Confirmed, then cancelled | 2 | 0 | 0 |
| Confirmed without a serial | 72 | 211 | 2,729 |

- **Receipts:**
  - 3,349 are linked to a purchase order. 459 of the open drafts are, and 450 of those are older than 30 days.
  - 28 order-and-warehouse pairs have more than one receipt; these are the complementary receipts.
  - Of the 13,493 linked purchase-order lines on confirmed receipts, 151 were received over the ordered quantity and 312 under it.
  - 1,128 lines on confirmed receipts not linked to an order have quantity 0.
  - 54 lines are of non-stockable products.
  - 469 receipts have the same product on more than one line.
- **Issues:** none is linked to a supplier return.
- **Transfers:**
  - 3,407 go from one facility to another, and the transfer is numbered in the origin facility.
  - 1 goes between two warehouses of one facility, and 1 is numbered in the destination facility.
  - None uses an in-transit warehouse.
  - 241 confirmed transfers post ledger entries that don't net to zero, and 11 posted none.
  - Confirm comes on average 23 minutes after creation.
- **Ledger entries:** none for receipts, issues or transfers carries a lot, serial or expiry.
- **Serials:** no facility repeats a serial of any of the three documents.
- **Document facility:** it matches the warehouse's facility for all but 4 receipts and 1 issue.

## Clarifications

### Session 2026-10-25

- Q: Can the destination receive less than was dispatched? → A: Yes, per line. The shortfall stays in transit and the transfer stays open until the rest is received, or someone closes the remainder, which returns it to the origin (FR-042, FR-043).
- Q: Who closes the remainder of a short transfer, and where do the units go? → A: Either facility, origin or destination, can close it. The units always return to the origin; goods really lost are then written off with an issue at the origin (FR-043).
- Q: What happens when a sales order's items are added to a transfer twice? → A: Only products not yet on the transfer are added; existing lines stay as they are, so repeating changes nothing (FR-046).
- Q: Which right gives the wider list view? → A: The delete right on each document type (InventoryReceipts, InventoryIssues, InventoryTransfers). The current system had it only on receipts; it now applies to all three (FR-012).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The warehouse receives goods (Priority: P1)

A warehouse clerk records goods that arrived in their warehouse: they open a receipt, add the products and quantities, and confirm it. The stock goes up, and the receipt gets the facility's next number.

**Why this priority**: Receipts are two thirds of all inventory documents (11,127 in 24 months), and every other movement depends on stock being in.

**Independent Test**: Create a receipt for a warehouse, add two products, and confirm it. Check that the serial is the facility's next one, that each product's stock in that warehouse rose by its quantity, and that the receipt can no longer be edited.

**Acceptance Scenarios**:

1. **Given** a draft receipt with two lines, **When** the clerk confirms it, **Then** it is numbered with the facility's next serial, marked confirmed, and posts one inbound ledger entry per line in its warehouse.
2. **Given** a confirmed receipt, **When** anyone edits its header or lines, **Then** the change is refused and nothing changes.
3. **Given** a product that is not stockable, **When** the clerk adds it to a receipt, **Then** it is refused.
4. **Given** a confirmed receipt whose stock is still in the warehouse, **When** a user with the cancel right cancels it, **Then** opposite ledger entries are posted and stock returns to what it was before.
5. **Given** a confirmed receipt whose goods were already issued, so that reversing it would leave available stock negative, **When** someone cancels it, **Then** the cancellation is refused, naming the product and warehouse.
6. **Given** a draft receipt, **When** it is cancelled, **Then** it is marked cancelled and nothing is posted.

---

### User Story 2 - The warehouse writes stock off (Priority: P1)

A clerk records stock leaving the warehouse for reasons other than a sale, such as waste, damage or internal use. They open an issue, add the lines and confirm. The stock goes down, but never below what is still promised to confirmed sales orders.

**Why this priority**: Issues are the only way to remove stock that was not sold (2,354 in 24 months). Without them, stock counts drift.

**Independent Test**: Put stock in a warehouse, reserve part of it with a confirmed sales order, and then try to issue more than the unreserved quantity. The issue is refused. Issue the unreserved quantity and it succeeds.

**Acceptance Scenarios**:

1. **Given** 10 units on hand and 4 reserved, **When** a clerk confirms an issue of 7, **Then** it is refused because only 6 are available.
2. **Given** an issue with two lines of the same product that together exceed available stock, **When** it is confirmed, **Then** it is refused.
3. **Given** a confirmed issue, **When** it is cancelled, **Then** opposite (inbound) entries are posted and the stock returns.
4. **Given** an unknown issue id, **When** someone confirms or cancels it, **Then** the answer is "not found" and nothing is written.

---

### User Story 3 - Stock moves between facilities through transit (Priority: P1)

The origin warehouse dispatches a transfer to another warehouse, usually in another facility. The goods leave the origin and sit in transit until the destination receives them. Both ends can see the transfer while it is on its way.

**Why this priority**: 3,409 transfers in 24 months, almost all between facilities. Today they move stock instantly, so goods on a truck already count as at the destination.

**Independent Test**: Create a transfer from facility A's warehouse to facility B's warehouse and dispatch it. Stock leaves A and appears in transit, and the transfer shows as in transit in the lists of both A and B. Receive it at B: stock leaves transit and appears in B's warehouse.

**Acceptance Scenarios**:

1. **Given** a draft transfer whose origin and destination are the same warehouse, **When** it is created or edited, **Then** it is refused.
2. **Given** a draft transfer whose lines exceed the origin's available stock, **When** it is dispatched, **Then** it is refused, naming the product.
3. **Given** a draft transfer, **When** it is dispatched, **Then**:
   - it gets the origin facility's next serial;
   - each line posts an outbound entry at the origin and an inbound entry at the origin facility's transit warehouse;
   - the transfer is in transit.
4. **Given** a transfer in transit, **When** a user at the destination facility receives it in full, **Then** each line posts an outbound entry at the transit warehouse and an inbound entry at the destination, and the transfer is received.
5. **Given** a transfer of 10 units in transit, **When** the destination receives 8, **Then** 8 move from transit to the destination, 2 stay in transit, and the transfer stays in transit. A later receipt of the 2 completes it.
6. **Given** a transfer with 2 units still in transit, **When** a user at either facility closes the remainder, **Then** the 2 return from transit to the origin and the transfer is received, showing 8 of 10 received.
7. **Given** a line with 2 units outstanding, **When** the destination tries to receive 3, **Then** it is refused.
8. **Given** a transfer in transit, **When** users at the origin or the destination list transfers, **Then** both see it, marked in transit.
9. **Given** a product with transfers in transit, **When** a user asks for stock in transit, **Then** they see the quantity in transit per product and per transfer.
10. **Given** a draft transfer and a confirmed sales order, **When** the clerk adds the order's items, **Then** the order's stockable lines are added with their quantities, except products already on the transfer, whose lines stay as they are. Adding the same order again changes nothing.
11. **Given** a transfer in transit, **When** it is cancelled, **Then** everything it posted is reversed: units still in transit go back to the origin, and units already received go from the destination back to the origin.

---

### User Story 4 - The warehouse completes a purchase order's delivery (Priority: P2)

A purchase order arrived short. Its receipt shows, for each line, how much was ordered and how much has been received. When the rest arrives, the clerk asks for the complementary receipt and gets the remaining quantities pre-filled.

**Why this priority**: 3,349 receipts come from purchase orders. Only about 25 complementary receipts are needed in 24 months, but a short delivery must be completable without retyping the order.

**Independent Test**: Take a completed purchase order with a confirmed receipt that received less than ordered on one line, and ask for a complementary receipt. A draft comes back with only that line, holding the remaining quantity. Ask again and the same draft comes back.

**Acceptance Scenarios**:

1. **Given** a receipt linked to a purchase order, **When** it is read, **Then** each linked line shows the ordered quantity, the quantity already received on other confirmed receipts, and this receipt's quantity.
2. **Given** a linked line whose quantity would take the received total above the ordered quantity, **When** it is saved or confirmed, **Then** it is accepted with a warning that names the line and the excess.
3. **Given** a completed purchase order with under-received lines for the receipt's warehouse and no open receipt, **When** the clerk asks for the complementary receipt, **Then** a draft is created holding only those lines, at the remaining quantities.
4. **Given** an open draft receipt already exists for that order and warehouse, **When** the clerk asks for the complementary receipt, **Then** that draft is returned and no new one is created.
5. **Given** a purchase order that is fully received, cancelled or not completed, **When** the clerk asks for a complementary receipt, **Then** it is refused with the reason.

---

### User Story 5 - Each user sees their warehouse's documents (Priority: P2)

Clerks see the receipts, issues and transfers of their own warehouse and the ones they created. Supervisors with the wider right see all of them.

**Why this priority**: It keeps lists short and relevant, as today, and closes the gap where any logged-in user could act on any document.

**Independent Test**: As a clerk with read rights, list receipts and get only your warehouse's and your own. As a user with the wider right, get all of them. As a user without the right, get "forbidden" on every action.

**Acceptance Scenarios**:

1. **Given** a clerk whose point of sale uses warehouse W, **When** they list issues, **Then** they see the issues of W and the ones they created, and no others.
2. **Given** a user with the delete right on the document type, **When** they list, **Then** they can see all documents, filtered by warehouse if they choose.
3. **Given** a user without the right needed for an action (read, create, edit or confirm, cancel), **When** they call it, **Then** it is refused as forbidden.
4. **Given** a non-administrator whose facility is not the document's facility, **When** they confirm it, **Then** it is refused. For a transfer, dispatch belongs to the origin facility and receipt to the destination facility.

### Edge Cases

- **Concurrent confirms.** Two confirms in one facility at the same moment get different, consecutive serials. A confirm that fails, for any reason, consumes no serial.
- **Concurrent stock use.** Two issues or transfers that each fit within available stock, but together don't, cannot both be confirmed.
- **In-transit warehouses.** No document may use one as its warehouse, origin or destination. Only transfers post to it, through dispatch and receipt.
- **Missing transit warehouse.** If the origin facility has none, dispatch is refused naming the facility, and nothing is posted.
- **Lines.** A document with no lines cannot be confirmed. A line's quantity must be greater than zero.
- **Duplicate products.** Adding a product already on the document adds to that line instead of creating a second one.
- **Discontinued products.** A product discontinued after it was added to a draft is still confirmed with the draft.
- **Already cancelled.** Cancelling a cancelled document is refused.
- **Cancelling a received or partly received transfer** posts the opposite of every step. It is refused if the destination's available stock would go negative.
- **Several receipts.** A transfer can be received in several goes; each one posts only the quantities it receives.
- **Legacy transfers.** A transfer confirmed by the current system had a single step. It reads as received, and cancelling it reverses it as a received transfer.
- **Legacy drafts.** Drafts left by the current system (690 in 24 months) can be edited, confirmed or cancelled like new ones.
- **Facility.** A document's facility is that of its warehouse, or for a transfer, of its origin warehouse. Changing the warehouse of a draft moves it to that facility.
- **Sales order items on a transfer.** Adding the items of a draft or cancelled sales order is refused.
- **Supplier return.** An issue keeps its supplier-return link if it has one, but nothing in this feature sets or uses it.
- **Unknown ids.** Every action on an unknown document or line answers "not found".

## Requirements *(mandatory)*

### Functional Requirements

**Common to the three documents**

- **FR-001**: The system MUST let users create drafts of receipts and issues for a warehouse, and transfers from an origin warehouse to a destination warehouse, with an optional comment of up to 500 characters.
  - The document takes its facility from its warehouse (for a transfer, the origin's).
  - It records its creator, last updater and the times of creation and last change.
- **FR-002**: While a document is a draft, users MUST be able to:
  - change its warehouse or warehouses and its comment;
  - add, change and remove lines.
  Every other state MUST refuse these changes.
- **FR-003**: A line MUST:
  - be for a stockable product;
  - have a quantity greater than zero;
  - keep a snapshot of the product's code and name taken when the line is added.
  Adding a product that is already on the document MUST add to that line.
- **FR-004**: Confirming (dispatching, for a transfer) MUST be refused for a document with no lines, or with any line that breaks FR-003.
- **FR-005**: Confirming MUST assign the next serial of the document type within the document's facility.
  - Concurrent confirms MUST NOT repeat a serial.
  - A confirm that fails MUST NOT consume one.
- **FR-006**: Every ledger entry MUST be posted with the document's type: receipt 4, issue 3, transfer 5. It MUST reference the document and carry no lot, serial or expiry.
- **FR-007**: Stock checks on issues and transfers MUST use available stock: on hand minus reservations.
  - Quantities are summed per product across the document's lines.
  - Concurrent confirms MUST NOT together spend the same available stock.
- **FR-008**: Cancelling MUST follow the document's state:
  - A draft is cancelled with no ledger entries.
  - A confirmed document is cancelled by posting the opposite of each entry it posted. This MUST be refused if any affected warehouse's available stock for a product would fall below zero; the refusal names the product and warehouse.
  - A cancelled document cannot be cancelled again.
- **FR-009**: No document MUST use an in-transit warehouse as its warehouse, origin or destination.
- **FR-010**: Each action MUST require the right on the document type's object (InventoryReceipts 15, InventoryIssues 16, InventoryTransfers 17):
  - read to list and view;
  - create to create;
  - update to edit, confirm, dispatch and receive;
  - delete to cancel, and to close a transfer's remainder.
- **FR-011**: A non-administrator MUST belong to the facility where the step happens:
  - the document's facility to confirm or cancel it;
  - the origin facility to dispatch or cancel a transfer;
  - the destination facility to receive one;
  - either the origin or the destination facility to close a transfer's remainder.
- **FR-012**: Lists MUST show, by default, documents of the caller's point-of-sale warehouse plus those the caller created. For transfers, the caller's warehouse may be the origin or the destination.
  - A caller with the delete right on the document type (or an administrator) MAY see all documents of that type.
  - Lists MUST be filterable by state and by warehouse, newest first.
- **FR-013**: Any action on an unknown document or line MUST answer "not found" and write nothing.

**Receipts (#58)**

- **FR-020**: Confirming a receipt MUST post one inbound entry per line in its warehouse.
- **FR-021**: A receipt linked to a purchase order MUST show, for each linked line:
  - the ordered quantity;
  - the quantity received on other confirmed, non-cancelled receipts;
  - its own quantity.
- **FR-022**: When a linked line takes the received total above the ordered quantity, saving or confirming it MUST succeed and return a warning naming the line and the excess. It is not a block.
- **FR-023**: Users MUST be able to ask for the complementary receipt of a receipt linked to a purchase order.
  - It is allowed only when the order is completed and not cancelled, and some of its lines for that receipt's warehouse are under-received. Only confirmed, non-cancelled receipts count as received.
  - If an open draft receipt exists for that order and warehouse, it is returned.
  - Otherwise a draft is created for that order and warehouse, with one line per under-received order line, linked to it, at the remaining quantity, and showing the order line's ordered quantity.
  - Otherwise the request is refused with the reason.
- **FR-024**: Lines added by hand to a receipt linked to a purchase order MUST be accepted without a link to an order line.

**Issues (#60)**

- **FR-030**: Confirming an issue MUST post one outbound entry per line in its warehouse, after the check in FR-007.
- **FR-031**: The supplier-return link MUST be kept as stored and shown when present. It MUST NOT be set or changed by this feature.

**Transfers (#62)**

- **FR-040**: The origin and the destination MUST differ. They may belong to different facilities.
- **FR-041**: Dispatching MUST:
  - check FR-007 at the origin;
  - assign the origin facility's serial;
  - post per line an outbound entry at the origin and an inbound entry at the origin facility's transit warehouse;
  - leave the transfer in transit.
  If the origin facility has no transit warehouse, it MUST be refused and post nothing.
- **FR-042**: Users at the destination MUST be able to receive a transfer in transit, in full or with a quantity per line.
  - Each line's received quantity cannot exceed what is still outstanding on it.
  - Each receipt posts, per line received, an outbound entry at the transit warehouse the transfer went through and an inbound entry at the destination.
  - The transfer stays in transit while any quantity is outstanding, and becomes received when none is.
- **FR-043**: Users MUST be able to close the remainder of a transfer in transit.
  - The outstanding quantities are posted from the transit warehouse back to the origin.
  - The transfer becomes received, keeping the dispatched and received quantities of each line.
  - Closing requires the delete right on InventoryTransfers (FR-010). A non-administrator must belong to the origin or the destination facility.
- **FR-044**: Cancelling a transfer MUST reverse everything it posted (FR-008):
  - outstanding units go from transit back to the origin;
  - received units go from the destination back to the origin. This is refused if the destination's available stock would go negative.
- **FR-044a**: A transfer's state MUST be one of draft, in transit, received or cancelled. Each line MUST show the quantity dispatched and the quantity received. Transfers confirmed by the current system read as received in full.
- **FR-045**: Transfer lists MUST let the caller see transfers in transit to or from their warehouse.
  - Users MUST be able to see, for a facility, the quantity in transit per product and the transfers it belongs to.
- **FR-046**: Users MUST be able to add to a draft transfer the stockable lines of a sales order that is confirmed and not cancelled, at the ordered quantities.
  - Products already on the transfer MUST be skipped, and their lines left unchanged.
  - Adding the same order again therefore changes nothing.
  - A product on more than one line of the order MUST be added once, with those lines' quantities summed.

### Key Entities

- **Warehouse receipt**: stock coming into one warehouse.
  - Header: facility, warehouse, optional purchase order, serial, comment, creator, updater, timestamps, state (draft, confirmed, cancelled).
  - Lines: product, code and name snapshots, quantity, and the optional purchase-order line with its ordered quantity.
- **Warehouse issue**: stock leaving one warehouse other than by sale. Same header as a receipt, with an optional supplier return instead of a purchase order. Lines: product, snapshots, quantity.
- **Inventory transfer**: stock moving from an origin warehouse to a destination warehouse through the origin facility's transit warehouse.
  - It is numbered in the origin facility.
  - State: draft, in transit (including partly received), received or cancelled.
  - Lines: product, snapshots, quantity dispatched and quantity received.
- **Ledger entry**: an append-only stock movement with source type, document reference, warehouse, product and signed quantity.
- **Reservation**: quantity promised to a confirmed sales order. It is subtracted from on-hand stock to give available stock.
- **Transit warehouse**: one per facility (spec 013), holding goods dispatched and not yet received.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every document confirmed by the new MBE, its ledger entries match its lines one for one. A transfer's entries net to zero once it is received or its remainder is closed, unlike the 241 unbalanced transfers of the current system.
- **SC-002**: 20 confirms started together in one facility produce 20 distinct serials with no gaps, and a failed confirm leaves no gap.
- **SC-003**: No issue or transfer can be confirmed beyond available stock, including when two are confirmed at once.
- **SC-004**: Cancelling a confirmed document restores every affected warehouse's stock to its value before that document. No document is left cancelled with its stock still booked.
- **SC-005**: A transfer in transit appears in the lists of both the origin and the destination warehouse, and its quantities appear as in transit, from dispatch until it is received or cancelled.
- **SC-006**: Every action is refused to users without the matching right, and every unknown id answers "not found". None of them errors.
- **SC-007**: On the test deployment against mbe_dev data, a receipt, an issue and an inter-facility transfer go through their full cycle, and are then cancelled with balances restored. The current system's documents remain readable, with their states correct.

## Assumptions

- **Searches stay in scope.** The current system's numeric search, which found any document regardless of scope, is not carried over.
- **Zero quantities.** A line can no longer be set to 0; it is removed instead. The current system allowed 0, and 1,128 confirmed lines have it.
- **The issue's reason** (waste, damage, internal use) goes in the comment, as today. There is no reason field.
- **Transfers within one facility** (1 in 24 months) also go through dispatch and receipt, using that facility's transit warehouse.
- **Transit stock.** The transit warehouse also holds delivery-dispatch stock (spec 012). In-transit quantities for transfers are therefore read from the transfers in transit, not from the transit warehouse's balance.
- **Cutover.** The current system's documents stay as they are, including the 2 receipts that are both confirmed and cancelled while their stock stays booked. Correcting their stock is not part of this feature.
- **Serial safety.** As in spec 027, a parent row is locked before the highest serial is read, so the read sees the previous confirm's number. Neither the current system's unlocked `MAX+1` nor `assign_folio`'s read from a possibly older snapshot is used.
- **Purchase-order links** are set only by the complementary receipt here. Creating receipts when a purchase order completes belongs to the Purchasing epic, which will set the same links.

## Out of Scope

- Supplier returns, and setting the issue's supplier-return link.
- Lot, serial and expiry capture or selection, and FIFO consumption.
- Creating receipts automatically from purchase orders (Purchasing epic).
- Printed formats of the three documents.
- Inventory adjustments and physical counts.
- The UI (mbe-ui).
- Correcting stock booked by documents the current system cancelled.
- Changes to `documents.assign_folio` and the documents that use it.
