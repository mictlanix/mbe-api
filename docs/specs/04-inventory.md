# Inventory Specs

Covers all physical stock movements: receipts (in), issues (out), transfers (between warehouses), adjustments, and lot/serial tracking.

All inventory transactions post to `lot_serial_tracking` as entries with a `TransactionType` identifying the source document. The new MBE posts receipts, issues and transfers through `stock_ledger.post_movement`; the current system used `InventoryHelpers.ChangeNotification`.

---

## Common Patterns

Receipts, issues and transfers are served by `/inventory-receipts`, `/inventory-issues` and `/inventory-transfers` (spec 029). Sections 1–3 describe them. Supplier returns are not built. Lots, serials, expiry and FIFO are out of scope for these three documents: every entry they post carries no lot, serial or expiry.

### Warehouse Scope
Lists show, by default, the documents of the user's point-of-sale warehouse plus the documents the user created. For transfers, the user's warehouse may be the origin or the destination. A user with the delete right on the document's object, or an administrator, sees all documents of that type and can filter by `warehouse`. In the default scope, `warehouse` narrows the list and never widens it. Without a point of sale, only the user's own documents are listed.

### Confirm / Complete Sequence
Every document follows: Create (draft) → Edit lines → Confirm (post to ledger) → optionally Cancel. Transfers confirm in two steps, dispatch and receive (section 3).
- **State guards:** header and line edits are allowed on drafts only. Confirming needs a draft with at least one line. A cancelled document cannot be cancelled again. Any other state answers 409.
- **Cancel:** a draft is cancelled with nothing posted. A confirmed document is cancelled by reading every ledger entry it posted (`source` = its type, `reference` = its id) and posting the opposite of each. The cancel is refused (409, naming the product and warehouse) if available stock in any affected warehouse would go negative.
- **Facility rule:** a non-administrator must belong to the facility where the step happens (see each section).
- **Rights:** Read to list and view, Create to create, Update to edit and confirm (dispatch and receive for transfers), Delete to cancel and to close a transfer's remainder.

### Available Stock
Issues, transfers and cancels check **available stock**: on-hand stock minus reservations (`lot_serial_rqmt`), summed per product across the document's lines, so two lines of the same product cannot together exceed it.

### Serial Safety
A step that moves stock locks the document's header row, then the warehouses involved in ascending id order, including the document's facility's in-transit warehouse. Only after those locks does it read stock and, for confirm and dispatch, `MAX(serial)` for the facility. The transit warehouse row is the facility's numbering lock, so concurrent confirms in one facility get consecutive serials, and a refused confirm consumes none. If another request holds the locks too long, or the database picks the step as a deadlock victim, the answer is 409 "The warehouse is busy, retry".

### Ledger Posting
Entries are posted append-only through `stock_ledger.post_movement` with `TransactionType` InventoryIssue (3), InventoryReceipt (4) or InventoryTransfer (5), and the document id as `reference`. Lot, serial and expiry are null.

---

## 1. Inventory Receipts

**Routes**: `/inventory-receipts`  
**SystemObject**: `InventoryReceipts` (15)

| Method | Path | Right |
|--------|------|-------|
| GET | `` | Read |
| POST | `` | Create |
| GET | `/{id}` | Read |
| PUT | `/{id}` | Update |
| POST | `/{id}/lines` | Update |
| PUT, DELETE | `/{id}/lines/{line_id}` | Update |
| POST | `/{id}/confirm` | Update |
| POST | `/{id}/cancel` | Delete |
| POST | `/{id}/complementary` | Create |

### Purpose
Record goods arriving into a warehouse. Typically follows a purchase order but can be standalone.

### States
`draft` → `completed` (confirmed) → `cancelled`. A draft can also be cancelled.

### List View
- Default filter: receipts for the user's POS warehouse OR created by the current user
- Filters: `status` (`draft`, `completed`, `cancelled`), `warehouse`, `skip`, `limit`
- Wider view (administrator, or `AllowDelete` on `InventoryReceipts`): all receipts, optionally filtered by warehouse
- Sort: newest first
- The numeric search that ignored scope is not carried over.

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Facility | `inventory_receipt.facility` | Set from the warehouse's facility; changing a draft's warehouse moves it to that facility |
| Serial/Folio | `inventory_receipt.serial` | Assigned on Confirm (next serial of the facility, see Serial Safety) |
| Warehouse | `inventory_receipt.warehouse` | Destination; cannot be an in-transit warehouse |
| Linked Purchase Order | `inventory_receipt.purchase_order` | Optional FK → `purchase_order`; set by the complementary receipt, and by the Purchasing epic when an order completes |
| Notes | `inventory_receipt.comment` | Up to 500 characters |

### Line Item Fields (`inventory_receipt_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | FK → `product` (must be `stockable = true`) |
| Linked PO Line | `purchase_order_detail` | Optional FK; lines added by hand carry no link |
| Quantity Ordered | `quantity_ordered` | The order line's true ordered quantity; 0 on lines added by hand |
| Quantity Received | `quantity` | Quantity received; must be greater than 0 |
| Product Code | `product_code` | Snapshot of code when the line is added |
| Product Name | `product_name` | Snapshot |

Adding a product already on the receipt adds to its line. For a linked line, the response also shows `quantity_received_elsewhere`, the quantity on other confirmed, non-cancelled receipts, with `quantity_ordered` read from the order line.

### Complementary Receipt
`POST /{id}/complementary` creates, or returns, the draft that completes a purchase order's delivery.
- The receipt must be linked to a purchase order that is completed and not cancelled, and some of the order's lines for the receipt's warehouse must be under-received (only confirmed, non-cancelled receipts count). Otherwise 409, with the reason.
- If an open draft exists for the same order and warehouse, it is returned (200).
- Otherwise a draft is created (201) with one line per under-received order line, linked to it, at the remaining quantity.
- Each line stores the order line's **true ordered quantity** in `quantity_ordered`. The current system stored the remaining quantity there, which made its own over-receipt comparison wrong.

### Confirm Action
1. Validates: the receipt is a draft with at least one line, and every line's product is `stockable = true`
2. Checks the facility rule (the receipt's facility, for non-administrators)
3. Assigns `Serial` = next serial for the facility
4. Sets `completed = true`
5. Posts one entry per line, `TransactionType.InventoryReceipt`, `+quantity` in the receipt's warehouse

### Cancel Action
- A draft is cancelled with nothing posted.
- A confirmed receipt is cancelled by posting the opposite of its entries. It is refused if the goods have already left (available stock in the warehouse would go negative).
- Requires the delete right and the facility rule.

### Business Rules
- Completed receipts are locked; only cancellation is allowed.
- If linked to a purchase order, receiving more than ordered is accepted with a warning (`warnings[]`, naming the product code and the excess), computed on every read, line save and confirm. It never blocks.

---

## 2. Inventory Issues

**Routes**: `/inventory-issues`  
**SystemObject**: `InventoryIssues` (16)

The routes and rights are those of receipts, without `/complementary` and without warnings.

### Purpose
Record goods leaving a warehouse other than by sale (write-offs, waste, damage, internal consumption). The reason goes in the comment; there is no reason field.

### States
`draft` → `completed` (confirmed) → `cancelled`. A draft can also be cancelled.

### List View
Same scope, filters and wider view as receipts, on `InventoryIssues`.

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Facility | `inventory_issue.facility` | Set from the warehouse's facility |
| Serial/Folio | `inventory_issue.serial` | Assigned on Confirm |
| Warehouse | `inventory_issue.warehouse` | Source warehouse; cannot be an in-transit warehouse |
| Linked Supplier Return | `inventory_issue.supplier_return` | Shown if stored; never set or changed here. Supplier returns are not built |
| Notes | `inventory_issue.comment` | Up to 500 characters |

### Line Item Fields (`inventory_issue_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | FK → `product` (must be `stockable = true`) |
| Quantity | `quantity` | Quantity to remove; greater than 0 |
| Product Code | `product_code` | Snapshot |
| Product Name | `product_name` | Snapshot |

### Lots and Serials
Not captured. Entries are posted without lot, serial or expiry, and consumption is not FIFO. Lot/serial selection and FIFO are out of scope for this feature.

### Confirm Action
1. Validates: the issue is a draft with at least one line, and every product is stockable
2. Checks the facility rule
3. Checks available stock in the warehouse, summed per product across the lines (409 naming the product code and warehouse if short)
4. Assigns the facility's next serial and sets `completed = true`
5. Posts one entry per line, `TransactionType.InventoryIssue`, `−quantity`

### Cancel Action
A draft is cancelled with nothing posted. A confirmed issue is cancelled by posting the opposite (inbound) entries, so the stock returns.

### Business Rules
- Cannot issue more than available stock (on hand minus reservations).
- Completed issues are locked; only cancellation is possible.

---

## 3. Inventory Transfers

**Routes**: `/inventory-transfers`  
**SystemObject**: `InventoryTransfers` (17)

| Method | Path | Right |
|--------|------|-------|
| GET | `` | Read |
| GET | `/in-transit` | Read |
| POST | `` | Create |
| GET | `/{id}` | Read |
| PUT | `/{id}` | Update |
| POST | `/{id}/lines` | Update |
| PUT, DELETE | `/{id}/lines/{line_id}` | Update |
| POST | `/{id}/sales-order-items` | Update |
| POST | `/{id}/dispatch` | Update |
| POST | `/{id}/receive` | Update |
| POST | `/{id}/close` | Delete |
| POST | `/{id}/cancel` | Delete |

### Purpose
Move stock between two warehouses, in the same facility or in different ones, through the origin facility's in-transit warehouse. Goods that have left the origin are not counted at the destination until it receives them.

### States
Derived from `completed`, `cancelled` and the new `in_transit` flag (migration 026):

| State | Rule |
|-------|------|
| `draft` | not completed |
| `in_transit` | completed and `in_transit` (including a partly received transfer) |
| `received` | completed and not `in_transit` |
| `cancelled` | cancelled |

Transfers confirmed by the current system had a single step. They have `in_transit = 0` and NULL received quantities, so they read as `received` in full.

### List View
- Default filter: transfers where the user's POS warehouse is the origin or the destination, OR created by the current user. A transfer in transit therefore shows at both ends.
- Filters: `status` (`draft`, `in_transit`, `received`, `cancelled`), `warehouse` (matches the origin or the destination), `skip`, `limit`
- Wider view (administrator, or `AllowDelete` on `InventoryTransfers`): all transfers.
- Sort: newest first.

### In-Transit View
`GET /in-transit?facility=` (default: the caller's facility) lists the transfers in transit whose origin or destination warehouse belongs to the facility. It gives each outstanding line (product, transfer id and serial, origin, destination, outstanding quantity) and the total outstanding per product. It reads the transfers, not the transit warehouse's balance, because that warehouse also holds stock dispatched for deliveries.

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Facility | `inventory_transfer.facility` | The origin warehouse's facility; the transfer is numbered there |
| Serial/Folio | `inventory_transfer.serial` | Assigned on Dispatch |
| From Warehouse | `inventory_transfer.warehouse` | Source |
| To Warehouse | `inventory_transfer.warehouse_to` | Destination; may belong to another facility |
| In Transit | `inventory_transfer.in_transit` | New in migration 026 |
| Notes | `inventory_transfer.comment` | Up to 500 characters |

### Line Item Fields (`inventory_transfer_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | FK → `product` (must be `stockable = true`) |
| Quantity | `quantity` | Quantity dispatched; greater than 0 |
| Quantity Received | `quantity_received` | New in migration 026. 0 from dispatch; NULL on legacy rows, which read as `quantity` once the transfer is completed |
| Product Code | `product_code` | Snapshot |
| Product Name | `product_name` | Snapshot |

The response also gives each line's `quantity_outstanding`: `quantity − received` while in transit, otherwise 0.

### Sales-Order Items
`POST /{id}/sales-order-items` adds the stockable lines of a sales order to a draft transfer, at the ordered quantities, summing a product that appears on several lines. The order must be completed and not cancelled (else 409). Products already on the transfer are skipped and their lines left unchanged, so repeating it changes nothing. The current system accepted draft and cancelled orders and duplicated the lines on a repeat.

### Dispatch Action (`/dispatch`)
1. Validates: the transfer is a draft with at least one line, and the origin and destination differ
2. Checks the facility rule: non-administrators dispatch from the origin facility
3. Checks available stock at the origin, per product across the lines
4. Assigns the origin facility's next serial, sets `completed = true`, `in_transit = true` and every `quantity_received = 0`
5. Posts, per line, `TransactionType.InventoryTransfer` entries: `−quantity` at the origin and `+quantity` at the origin facility's in-transit warehouse

If the origin facility has no in-transit warehouse, dispatch answers 422 naming the facility and posts nothing.

### Receive Action (`/receive`)
- Body `{lines: [{line_id, quantity}]}`; omitting it receives everything outstanding. An empty list or a repeated line is 422, a line of another transfer is 404, and more than a line's outstanding quantity is 409.
- Non-administrators receive from the destination warehouse's facility.
- Per line received, posts `−received` at the in-transit warehouse and `+received` at the destination, and adds to `quantity_received`.
- The transfer stays `in_transit` while any quantity is outstanding, and becomes `received` when none is. Any number of receipts is allowed.
- Receiving needs no stock check, because it moves only what is already in transit.

### Close the Remainder (`/close`)
Requires the delete right. A non-administrator must belong to the origin's or the destination's facility. The outstanding quantities are posted from the in-transit warehouse back to the origin, and the transfer becomes `received`, each line keeping what it received (for example 8 of 10). Goods really lost are then written off with an issue at the origin.

### Cancel Action (`/cancel`)
Requires the delete right and, for a non-administrator, the origin facility. A draft is cancelled with nothing posted. Any other transfer has everything it posted reversed from its ledger entries: units still in transit go back to the origin, and units already received go from the destination back to the origin. The cancel is refused if the destination's available stock would go negative. It clears `in_transit`, and the lines keep their `quantity_received`.

### Business Rules
- Source and destination warehouses must be different, and neither can be an in-transit warehouse.
- Cannot dispatch more than available stock at the origin.
- Cross-facility transfers are supported through the transit cycle above; a transfer within one facility uses that facility's in-transit warehouse in the same way.
- Receiving more than the outstanding quantity is refused.
- Completed transfers are locked: only receive, close and cancel act on them.

---

## 4. Inventory Adjustments

**Route**: `GET /inventory/adjustments`  
**SystemObject**: `InventoryAdjustments` (93)

### Purpose
Manual stock corrections after a physical count. Adds or removes stock without a linked purchase or sales document.

### Features
- Enter actual counted quantities per product per warehouse
- System computes delta vs current ledger balance
- Positive delta → issues a positive `lot_serial_tracking` entry
- Negative delta → issues a negative entry

### Key Tables
- `lot_serial_tracking` (direct insert/update)

---

## 5. Physical Count Adjustment

**SystemObject**: `PhysicalCountAdjustment` (74)

### Purpose
Supervisor-level tool for bulk physical inventory count entry. Requires elevated privilege separate from normal adjustments.

### Distinction from Inventory Adjustments
- `PhysicalCountAdjustment (74)` is the privilege gate for bulk count operations.
- `InventoryAdjustments (93)` gates the standard adjustment form.
- In practice, both post to `lot_serial_tracking`.

---

## 6. Lot / Serial Numbers

**Route**: `GET /inventory/lot-serial-numbers`  
**SystemObject**: `LotSerialNumbers` (42)

### Purpose
View and query the lot/serial tracking ledger. Shows current inventory by lot or serial number with full movement history.

### Features

#### Current Stock View
- Filter by: warehouse, product, lot number, serial number, expiration date range
- Columns: warehouse, product, lot/serial, expiration, qty on hand

#### Movement History
- Shows all `lot_serial_tracking` entries for selected filters
- Columns: date, `TransactionType` (source document type), document ID, warehouse, quantity (±)

#### Expiry Alerts
- Highlight lots expiring within a configurable threshold
- Export expiring-soon list

### Key Tables
- `lot_serial_tracking` — full movement ledger (one row per document line)
- `lot_serial_rqmt` — pending reservations (reserved but not yet fulfilled)

### Quantity Calculation
Stock on hand = `SUM(lst.quantity)` grouped by `(warehouse, product, lot_number, serial_number)` where sum > 0

### Business Rules
- A serial number should appear in exactly one location (quantity = 0 or 1 per location).
- FIFO picking order: oldest lot (earliest `expiration_date`, then earliest tracking `date`) is consumed first.
- Products with `perishable = false` and `seriable = false` still get tracking entries — the lot/serial fields are null.
