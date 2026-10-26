# Data Model: Warehouse Receipts, Issues and Transfers

The tables exist in mbe_dev and in `app/models/inventory.py`. Only migration 026 changes them.

## Migration 026: `026_inventory_transfer_transit.sql`

Same style and header as `migrations/025_fiscal_document_customer_payment.sql`: a comment block, idempotent, MariaDB 10.11.

```sql
ALTER TABLE `inventory_transfer`
    ADD COLUMN IF NOT EXISTS `in_transit` TINYINT(1) NOT NULL DEFAULT 0 AFTER `cancelled`;

ALTER TABLE `inventory_transfer_detail`
    ADD COLUMN IF NOT EXISTS `quantity_received` DECIMAL(18,4) NULL DEFAULT NULL AFTER `quantity`;
```

The rollback (`026_inventory_transfer_transit_rollback.sql`) runs `DROP COLUMN IF EXISTS` on both. Legacy rows need no backfill (research R4). The current system's NHibernate mapping names its columns explicitly, so it neither reads nor writes these two.

## Headers

All three headers share these fields:
- `<doc>_id` (PK);
- `facility` (FK, NOT NULL; the warehouse's facility, or the origin's for a transfer);
- `serial` (INT NULL, set on confirm or dispatch);
- `creation_time` and `modification_time`;
- `creator` and `updater` (FK employee);
- `completed` and `cancelled` (flags);
- `comment` (VARCHAR 500 NULL).

| Table | Own fields |
|---|---|
| `inventory_receipt` | `warehouse` (FK), `purchase_order` (FK NULL) |
| `inventory_issue` | `warehouse` (FK), `supplier_return` (FK NULL, read-only here) |
| `inventory_transfer` | `warehouse` (origin, FK), `warehouse_to` (FK), **`in_transit`** (new) |

## Lines

All three line tables share:
- id (PK) and the parent FK;
- `product` (FK);
- `quantity` DECIMAL(18,4), greater than 0;
- `product_code` VARCHAR 25 and `product_name` VARCHAR 250, snapshots taken when the line is added.

| Table | Own fields |
|---|---|
| `inventory_receipt_detail` | `purchase_order_detail` (FK NULL), `quantity_ordered` DECIMAL NOT NULL (written as the order line's quantity, or 0 for hand-added lines; the over-receipt check reads the order line itself, research R7) |
| `inventory_issue_detail` | — |
| `inventory_transfer_detail` | **`quantity_received`** DECIMAL NULL (new; 0 from dispatch, NULL on legacy rows) |

A product appears at most once per document for lines this API writes (FR-003). Legacy documents may repeat a product, and they are read as they are.

## Derived state

**Receipts and issues** use `DocumentStatus`: `cancelled` if cancelled, `completed` if completed, otherwise `draft`.

**Transfers** use `TransferStatus`:

| Status | Rule |
|---|---|
| `cancelled` | `cancelled` |
| `draft` | not `completed` |
| `in_transit` | `completed` and `in_transit` |
| `received` | `completed` and not `in_transit` |

Line figures for a transfer:
- **received** = `quantity_received`. If that is NULL, it is `quantity` once the transfer is completed, otherwise 0.
- **outstanding** = `quantity − received` while the transfer is in transit, otherwise 0.
- A cancelled transfer keeps `quantity_received` as it was before the cancel. The response shows what had been received, and the ledger holds the reversal (research R3).

## State transitions

```text
Receipt / Issue:  draft ──confirm──▶ completed ──cancel──▶ cancelled
                  draft ──cancel──▶ cancelled
Transfer:         draft ──dispatch──▶ in_transit ──receive (all outstanding)──▶ received
                                      in_transit ──receive (part)──▶ in_transit
                                      in_transit ──close remainder──▶ received
                  draft ──cancel──▶ cancelled;  in_transit | received ──cancel──▶ cancelled
```

## Ledger entries (`lot_serial_tracking`)

Every entry has `source` = TransactionType, `reference` = the document id, `date` = now, and NULL lot, serial and expiry.

| Step | Source | Entries per line |
|---|---|---|
| Receipt confirm | 4 | +qty at `warehouse` |
| Issue confirm | 3 | −qty at `warehouse` |
| Transfer dispatch | 5 | −qty at origin; +qty at the origin facility's transit |
| Transfer receive | 5 | −received at transit; +received at `warehouse_to` |
| Transfer close remainder | 5 | −outstanding at transit; +outstanding at origin |
| Cancel (confirmed) | same | negation of each entry with this source and reference (R3) |

## Validation rules

| Rule | Error |
|---|---|
| The product is unknown or not stockable | 422 |
| A line quantity is ≤ 0 | 422 (schema) |
| The warehouse is unknown, or is an in-transit warehouse | 404 or 422 (warehouse status is not checked, research R9) |
| A transfer's origin equals its destination | 422 |
| Editing a document that isn't a draft | 409 (`documents.assert_editable`) |
| Confirming or dispatching with no lines | 409 |
| Available stock is short (issue, dispatch, cancel) | 409, naming the product code and the warehouse |
| The origin facility has no transit warehouse | 422, naming the facility |
| A receive exceeds the outstanding quantity of a line, or names a line with nothing outstanding | 409 |
| A receive names a line of another transfer | 404 |
| A receive names the same line twice, or sends an empty `lines: []` | 422 |
| Another request holds the locks too long, or InnoDB picks this step as a deadlock victim (MariaDB 1205 / 1213) | 409 "The warehouse is busy, retry" (research R2) |
| The serial unique index fires (it should never, research R1) | 409 |
| Receive or close on a transfer that isn't in transit | 409 |
| Cancelling a cancelled document | 409 |
| A non-administrator in the wrong facility (R6) | 403 |
| The receipt has no purchase order, or the order is incomplete, cancelled or fully received (complementary receipt) | 409 with the reason; an unknown receipt is 404 |
| The sales order for the items is not completed, or is cancelled | 409 |
