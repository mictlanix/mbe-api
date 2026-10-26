# Feature Specification: Physical Inventory Count, Stock and Kardex

**Feature Branch**: `030-physical-count-kardex`

**Created**: 2026-10-26

**Status**: Draft

**Input**: Implements two items of the inventory epic (mictlanix/mbe-roadmap#6), within §4–6 of `docs/specs/04-inventory.md`, with the corrections below:
- mictlanix/mbe-roadmap#64, the physical inventory count;
- mictlanix/mbe-roadmap#66, stock queries and the movement history (kardex).

It builds on spec 029: the receipt, issue and transfer documents, their ledger postings, and the shared locking.

Lots, serials and expiry are not used, so this covers stock and history only. Inventory moves from the current system to the new MBE in a single cutover; the two systems never record inventory at the same time.

## Findings that shape this spec

### The current system (`mictlanix/mbe`)

- **There is no count document.** A count is an ordinary warehouse receipt holding the counted quantities. A user with the update right on PhysicalCountAdjustment (74) then presses "Adjustment" on that receipt. That posts, for every product on the receipt, the negation of its balance before the receipt (type 8, reference = the receipt). The two together leave the counted quantity.
  - **No guards:** the controller checks no privilege, does not check that the receipt is confirmed or not cancelled, and keeps no record that the adjustment ran. 58 receipts were adjusted more than once (1,797 repeated groups).
  - **Bug: duplicate lines over-adjust.** The adjustment joins the receipt's lines, so a product on two lines has its balance negated twice. In 2026, 157 of 176 products with 2+ lines kept a wrong balance, against 9 of 6,045 with one line.
  - **ZeroOut** (the same over every product of the warehouse, gated on InventoryAdjustments 93) is commented out of the screen. The separate "inventory adjustments" form does not exist in practice.
- **Stock reports** are gated only by menu visibility; no action checks a privilege.
  - The warehouse stock report (WarehouseStockReport 67) shows on hand per product, filtered by warehouse, label, brand and model. Its join to labels multiplies the stock of products with several labels.
  - No screen shows reserved, available or in-transit stock.
  - LotSerialNumbers (42) is not a stock query: it lists pending lot and serial assignments.
- **The kardex** (Kardex 32) takes a warehouse, a product and a date range.
  - It shows an opening balance, then one row per day and document (several movements of one document on the same day are netted into one), the document's raw id rather than its number, in and out quantities, a running balance and a closing balance.
  - Some references are ambiguous: cancelling a payment writes type 2 (customer refund) with a sales order id as reference.

### This API today

- **Types written:**
  - the ledger (`lot_serial_tracking`) gets receipts 4, issues 3, transfers 5, customer refunds 2 and deliveries 10;
  - reservations of confirmed sales orders go to `lot_serial_rqmt` under type 11.
  - Type 8 is not written by the API.
- **Type 10 has two meanings:** its reference is a delivery order for a counter pickup, and an itinerary for a departure or a settlement. Nothing on the entry tells them apart. mbe_dev has no type-10 entries yet.
- **Existing stock functions:** `stock_ledger` computes on hand, reserved and available per product and warehouse, including in batches. The only stock endpoint is the sales-order product lookup, which excludes transit warehouses.
- **Spec 029's shared module** (draft, lines, locks, serials, list scope) assumes a header with facility, warehouse, serial, completed and cancelled flags, and a line table. Its confirm posts the line quantity, so a count needs its own confirm.

### Measured on mbe_dev (2026-10-26, last 24 months unless stated)

- **Adjustments (type 8):** 26,393 entries under 748 references.
  - 747 references are receipt ids; the highest is 31,919.
  - One reference, 0, is a single adjustment of 7,030 products across 5 warehouses on 2025-02-06.
  - Counts average 30 products; the largest receipt-based count has 534.
  - 10,821 adjustment entries carry the text "Physical Adjustment From Query" in the lot column. It is a marker, not a lot.
- **No real lots:** no entry of types 1–5 has a lot, serial or expiry.
- **Warehouse size:** the largest warehouse (11) has 14,569 products in the ledger, 10,709 with a non-zero balance and 26 negative. Warehouse 7 has 11,997 (6,907 non-zero). A whole-warehouse opening count can therefore reach about 15,000 lines.
- **Reservations:** `lot_serial_rqmt` holds 3,252 rows. Only 35 are API reservations (type 11); the rest are the current system's stale rows.
- **Ledger:** 639,954 entries in all. A sum for one product in one warehouse takes well under a second.

## Clarifications

### Session 2026-10-26

- Q: How should the kardex tell the current system's adjustments (type 8, reference = receipt) from new counts? → A: New counts post a new type, **12 (physical count)**, referencing the count. Type 8 stays the current system's only, and resolves to its receipt (FR-004, FR-032).
- Q: How should the kardex tell a counter pickup from an itinerary departure or settlement under type 10? → A: Counter pickups post a new type, **13 (delivery pickup)**, referencing the delivery order. Type 10 is then always an itinerary. This changes spec 012's pickup posting, which is safe because mbe_dev has no type-10 entries yet (FR-034).
- Q: Can a warehouse have several draft counts at once? → A: Yes. The confirm response warns about each product that another count of the same warehouse confirmed after this count was created (FR-010a).
- Q: Should a draft count show the system's expected quantity? → A: No, the count is blind. Drafts show only counted quantities; the on-hand quantity and the difference appear only once the count is confirmed (FR-003a).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The warehouse counts its stock and corrects it (Priority: P1)

A supervisor opens a count for a warehouse, enters the counted quantity of each product they counted (by hand, or as a list), and confirms it. Each counted product's stock then equals what was counted. Products not counted stay as they were, unless the supervisor asks for them to be set to zero.

**Why this priority**: Stock drifts, and a count is the only way to correct it. Today it takes a receipt plus a separate adjustment that can run twice and over-adjust.

**Independent Test**: Give a product 10 on hand, count 7 and confirm. The count is numbered, the product's stock is 7, the ledger holds one count entry (type 12) of −3 referencing the count, and confirming again is refused.

**Acceptance Scenarios**:

1. **Given** a draft count with products A (counted 7, on hand 10) and B (counted 5, on hand 5), **When** it is confirmed, **Then**:
   - it gets the facility's next count serial;
   - A gets a count entry (type 12) of −3 referencing the count, and B gets none;
   - the count is read-only and shows, per line, the counted quantity, the on-hand quantity at confirm and the difference.
2. **Given** a confirmed count, **When** anyone confirms it again or edits it, **Then** it is refused and nothing is posted.
3. **Given** product C on hand 4 in that warehouse and not on the count, **When** the count is confirmed without "include uncounted as zero", **Then** C is untouched. **With** that option, **Then** C gets an adjustment of −4 and appears on the count as counted 0.
4. **Given** a draft count, **When** the supervisor enters a list of product codes and quantities in one request, **Then** each becomes a line. A product already on the count has its counted quantity replaced, not added to. Unknown or non-stockable codes are reported with nothing applied.
5. **Given** stock that moves between the physical count and the confirm, **When** the count is confirmed, **Then** the difference is computed against on-hand stock at the moment of confirm.
6. **Given** a user without the right on PhysicalCountAdjustment (74), **When** they call any count action, **Then** it is refused.

---

### User Story 2 - The opening count loads a whole warehouse (Priority: P1)

For the move to the new MBE, each warehouse's opening stock is loaded as one count over the whole warehouse, several thousand products confirmed at once.

**Why this priority**: The stock migration (mictlanix/mbe-roadmap#145, still open) depends on it, and the largest warehouse holds about 15,000 products.

**Independent Test**: Load a count of 15,000 lines for one warehouse and confirm it. It completes within the time limit in SC-003, posts one adjustment per product whose count differs from on hand, and leaves every counted product at its counted quantity.

**Acceptance Scenarios**:

1. **Given** a count with 15,000 lines, **When** it is confirmed, **Then** it completes in one confirm, all or nothing.
2. **Given** a list of 15,000 products entered in batches, **When** the batches are applied, **Then** the count holds them all, and a repeated batch does not double any quantity.

---

### User Story 3 - Anyone authorised sees stock per warehouse (Priority: P1)

A user looks up stock by warehouse and product: on hand, reserved for confirmed sales orders, available, and on its way to that warehouse.

**Why this priority**: Every sale, transfer and count decision starts from stock. Today the only figure shown is on hand, and reservations and goods in transit are invisible.

**Independent Test**: For a product with 10 on hand, 3 reserved and 2 on a transfer in transit to the warehouse, the query shows 10, 3, 7 and 2.

**Acceptance Scenarios**:

1. **Given** a warehouse, **When** the user asks for its stock, **Then** each product with any non-zero figure is listed with on hand, reserved, available and incoming. It can be filtered by product (code or name) and by whether zero stock is shown.
2. **Given** a product, **When** the user asks for its stock, **Then** every warehouse with any non-zero figure is listed with the same figures, plus each facility's in-transit warehouse balance.
3. **Given** a product on several labels, **When** stock is listed, **Then** it appears once with its true quantity.

---

### User Story 4 - Anyone authorised traces a product's movements (Priority: P2)

A user opens the kardex of a product in a warehouse for a date range and sees the opening balance, every movement in order with its document, the running balance and the closing balance.

**Why this priority**: When stock looks wrong, the movement history is how it is explained. Today it shows raw ids and nets same-day movements together.

**Independent Test**: For a product with movements before and inside a range, the opening balance equals the sum before the range, each movement shows its date and time, type, document number and link, in and out quantities and running balance, and the closing balance equals the opening balance plus the movements.

**Acceptance Scenarios**:

1. **Given** a range, **When** the kardex is read, **Then** the opening balance is the sum of all entries before the range start.
2. **Given** movements of a receipt, an issue, a transfer (dispatch, receive, close, cancel), a count, a customer refund and a delivery, **When** the kardex is read, **Then** each movement is its own row, oldest first, with its type and its document's facility serial and id.
3. **Given** an entry whose document cannot be resolved (legacy or ambiguous), **When** the kardex is read, **Then** the row shows the type and the raw reference, without a document link.
4. **Given** a range with no movements, **When** the kardex is read, **Then** the opening and closing balances are equal and there are no rows.

### Edge Cases

- **Re-confirming.** A count can be confirmed once. A second confirm, including two at the same moment, is refused and posts nothing.
- **No difference.** A product whose count equals on hand posts nothing.
- **Negative stock.** A product with negative on hand counted at 0 gets a positive adjustment.
- **Reserved stock.** A count below what is reserved still confirms: reality wins. The count reports, per product, where available stock goes negative, so sales can act.
- **Counted twice.** The same product cannot appear on two lines of a count; entering it again replaces the quantity.
- **Overlapping counts.** Two drafts of one warehouse may cover the same product. The later confirm corrects against stock at its own confirm, and its response warns that the other count corrected that product after this one was created.
- **Products.** Only stockable products are accepted. A product discontinued after it went on a draft still confirms.
- **In-transit warehouses** cannot be counted.
- **Facility rule.** A non-administrator confirms or cancels only counts of their own facility.
- **Drafts.** A draft count can be cancelled, with no ledger effect. A confirmed count cannot be cancelled; a correction is a new count.
- **Kardex range.** The end date is inclusive of its whole day. A range longer than the limit in FR-031 is refused.
- **Legacy adjustments.** The current system's type-8 entries reference the count receipt, which the kardex links. Reference 0 (the 2025 bulk adjustment) has no document and shows raw. New counts never post type 8.
- **Deliveries.** Type 10 always references an itinerary, and type 13 a delivery order picked up at the counter.

## Requirements *(mandatory)*

### Functional Requirements

**Physical count (#64)**

- **FR-001**: Users MUST be able to create a draft count for one warehouse, with an optional comment of up to 500 characters. The count takes its facility from the warehouse and records its creator, updater and times.
- **FR-002**: While a count is a draft, users MUST be able to:
  - set a product's counted quantity (zero or more), which replaces any earlier quantity for that product;
  - remove a product;
  - set many products at once from a list of product codes or ids with quantities. The whole list is refused when any entry is unknown, not stockable or repeated, and the refusal names those entries.
- **FR-003**: Each line MUST keep a snapshot of the product's code and name, and the counted quantity with up to 4 decimals.
- **FR-003a**: A draft count MUST NOT show on-hand or expected quantities, on its lines or anywhere in its responses. The on-hand quantity read and the difference posted appear only once it is confirmed.
- **FR-004**: Confirming a count MUST:
  - be refused for a count that is not a draft, or that has no lines (unless "include uncounted as zero" adds some);
  - assign the next count serial within the facility, safely under concurrent confirms, with no number consumed by a failed confirm;
  - read on-hand stock for each counted product at that moment, after locking the warehouse against other movements;
  - post, for each product whose counted quantity differs from on hand, one count entry (new type 12, physical count) of the difference, referencing the count, with no lot, serial or expiry;
  - record on each line the on-hand quantity read and the difference posted;
  - be all or nothing.
- **FR-005**: When confirming with "include uncounted as zero", every stockable product with a non-zero on-hand quantity in that warehouse and no line MUST be added as counted 0 and adjusted like the rest.
- **FR-006**: A confirmed count MUST be read-only and MUST NOT be cancellable.
- **FR-007**: A draft count MUST be cancellable with no ledger effect.
- **FR-008**: Each count action MUST require the right on PhysicalCountAdjustment (74): read to list and view, create to create, update to edit and confirm, delete to cancel a draft. A non-administrator MUST belong to the count's facility to confirm or cancel it.
- **FR-009**: Count lists MUST follow spec 029's scope: the caller's point-of-sale warehouse plus the counts they created, or all counts with the delete right. They MUST be filterable by state and warehouse, newest first.
- **FR-010**: The confirm response MUST report the products whose available stock (on hand − reserved) is negative after the count.
- **FR-010a**: A warehouse MAY have several draft counts at once. The confirm response MUST list each of this count's products that another count of the same warehouse confirmed after this count was created, with that count's serial. It is a warning; the confirm still applies.
- **FR-011**: A count of 15,000 lines MUST be enterable in batches and confirmable in one confirm.

**Stock (#66)**

- **FR-020**: Users MUST be able to list a warehouse's stock. Per product it shows:
  - code and name;
  - on hand;
  - reserved (confirmed sales orders not yet departed);
  - available (on hand − reserved);
  - incoming (outstanding quantities on transfers in transit to that warehouse).

  It is filterable by product code or name, shows only products with a non-zero figure unless zero stock is asked for, and is paged.
- **FR-021**: Users MUST be able to list a product's stock across warehouses, with the same figures per warehouse, plus the balance of each facility's in-transit warehouse.
- **FR-022**: Stock queries MUST NOT multiply a product by its labels or by any other join.
- **FR-023**: Stock queries MUST require the read right on WarehouseStockReport (67), the object the current system uses for its stock report.

**Kardex (#66)**

- **FR-030**: Users MUST be able to read the kardex of one product in one warehouse for a date range. It shows:
  - the opening balance (all entries before the start);
  - one row per ledger entry in the range, oldest first, with date and time, type, the document's id, facility and serial when it can be resolved, quantity in or out, and running balance;
  - the closing balance.
- **FR-031**: The range end MUST be inclusive of its whole day. The range MUST NOT exceed 366 days.
- **FR-032**: Each type MUST resolve to its document:
  - 1 sales order;
  - 2 customer refund;
  - 3 issue;
  - 4 receipt;
  - 5 transfer;
  - 6 purchase order (legacy);
  - 8 the current system's count receipt (reference 0 is unresolved);
  - 10 itinerary;
  - 12 count;
  - 13 delivery order.

  An entry that cannot be resolved MUST show its type and raw reference, without a link.
- **FR-033**: The kardex MUST require the read right on Kardex (32), the object the current system uses for it.
- **FR-034**: A counter pickup MUST post its outbound entry as type 13 (delivery pickup) referencing the delivery order, instead of type 10. Itinerary departures and settlements keep type 10. Anything that reads pickup entries back MUST use type 13.

### Key Entities

- **Physical count**: one warehouse's counted stock.
  - Header: facility, warehouse, serial, comment, creator, updater, times, state (draft, confirmed, cancelled).
  - Lines: product, code and name snapshots, counted quantity, and on confirm the on-hand quantity read and the difference posted.
  - It needs a new table (SQL migration).
- **Count entry**: a ledger entry of type 12 for one product, referencing the count. Type 8 remains the current system's adjustments only.
- **Stock row**: per warehouse and product: on hand, reserved, available, incoming.
- **Kardex row**: a ledger entry with its resolved document and running balance.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a count is confirmed, every counted product's on-hand stock in that warehouse equals its counted quantity, and the count's adjustment entries sum, per product, to exactly the difference recorded on its line.
- **SC-002**: A count can be confirmed only once. Two confirms at the same moment post one set of adjustments, and serials are never repeated.
- **SC-003**: A count of 15,000 lines confirms in under 60 seconds on the test deployment.
- **SC-004**: For any product and warehouse, the kardex closing balance equals on-hand stock at the range end, and the opening balance plus the movements equals the closing balance.
- **SC-005**: Stock figures match the ledger and reservations exactly for a sample of products across 3 warehouses on mbe_dev, including products with several labels.
- **SC-006**: Every count, stock and kardex action is refused to users without the matching right, and unknown ids answer "not found". None of them errors.

## Assumptions

- **No separate adjustment form.** InventoryAdjustments (93) and ZeroOut are dropped; "include uncounted as zero" replaces ZeroOut.
- **Rights for stock and kardex.** The request named LotSerialNumbers (42) "or the privilege the current system uses for stock queries". In the current system, 42 gates a list of pending lot and serial assignments, not stock, so stock uses WarehouseStockReport (67) and the kardex uses Kardex (32), as the current system does.
- **No count cancel after confirm.** A correction is a new count. The current system had no undo either.
- **Counts as of confirm.** The difference uses on-hand stock at confirm time, not at the physical count time. Movements between the physical count and the confirm are the supervisor's to account for, as the user asked.
- **Serial safety** follows spec 029's pattern: the facility's transit warehouse is the per-facility numbering lock, and the highest serial is read after it. It applies spec 027's principle of locking a parent row first.
- **Bulk entry** is a list in the request body. There is no file upload; batches keep request sizes reasonable.
- **Incoming** counts only transfers in transit. Delivery stock in transit warehouses appears in the product view as each facility's in-transit balance.
- **Stale reservations.** Only type-11 reservations count as reserved; the current system's stale `lot_serial_rqmt` rows stay as they are and are ignored.
- **Legacy references.** The kardex shows legacy entries with their raw reference when their document cannot be resolved with confidence, for example type 2 entries written by payment cancellation.

## Out of Scope

- Lots, serials, expiry, FIFO and expiry alerts.
- Cleaning `lot_serial_rqmt`.
- Costs in the stock report or the kardex.
- Label, brand and model filters on stock.
- Cancelling a confirmed count.
- The stock migration itself (mictlanix/mbe-roadmap#145).
- The UI (mbe-ui).
