# Contract: Inventory Documents API

Base path: `/api/v1`. All routes require a bearer token. **Right** names the AccessRight needed on the document's SystemObject:
- 15 InventoryReceipts;
- 16 InventoryIssues;
- 17 InventoryTransfers.

Administrators bypass these checks. Errors use the API's usual `{"detail": "..."}` shape.

## Receipts: `/inventory-receipts` (object 15)

| Method | Path | Right | Body | Success |
|---|---|---|---|---|
| GET | `` | Read | query: `status`, `warehouse`, `skip`, `limit` | 200 `ListResponse[InventoryReceiptSummary]` |
| POST | `` | Create | `{warehouse, comment?}` | 201 `InventoryReceiptResponse` |
| GET | `/{id}` | Read | — | 200 `InventoryReceiptResponse` |
| PUT | `/{id}` | Update | `{warehouse?, comment?}` | 200 |
| POST | `/{id}/lines` | Update | `{product, quantity}` | 200 (merges into an existing line) |
| PUT | `/{id}/lines/{line_id}` | Update | `{quantity}` | 200 |
| DELETE | `/{id}/lines/{line_id}` | Update | — | 200 |
| POST | `/{id}/confirm` | Update | — | 200 |
| POST | `/{id}/cancel` | Delete | — | 200 |
| POST | `/{id}/complementary` | Create | — | 200 (the existing draft) or 201 (a new draft) |

**`InventoryReceiptResponse`**:
- `inventory_receipt_id`, `facility`, `warehouse`, `purchase_order`, `serial`;
- `status` (`draft`, `completed` or `cancelled`);
- `comment`, `creator`, `updater`, `creation_time`, `modification_time`;
- `lines[]`, each with:
  - `receipt_detail_id`, `product`, `product_code`, `product_name`, `quantity`;
  - `purchase_order_detail`, `quantity_ordered`;
  - `quantity_received_elsewhere`, which is null for unlinked lines;
- `warnings[]` (R7).

**Summary**: id, facility, warehouse, purchase_order, serial, status, creator and creation_time.

## Issues: `/inventory-issues` (object 16)

These routes are the same as for receipts, without `/complementary`.

**`InventoryIssueResponse`**: the receipt header, with `supplier_return` in place of `purchase_order`, and no warnings. Lines have `issue_detail_id`, `product`, `product_code`, `product_name` and `quantity`.

## Transfers: `/inventory-transfers` (object 17)

| Method | Path | Right | Body | Success |
|---|---|---|---|---|
| GET | `` | Read | query: `status` (`draft`, `in_transit`, `received` or `cancelled`), `warehouse`, `skip`, `limit` | 200 `ListResponse[InventoryTransferSummary]` |
| GET | `/in-transit` | Read | query: `facility` (defaults to the caller's) | 200 `InTransitResponse` |
| POST | `` | Create | `{warehouse, warehouse_to, comment?}` | 201 |
| GET | `/{id}` | Read | — | 200 `InventoryTransferResponse` |
| PUT | `/{id}` | Update | `{warehouse?, warehouse_to?, comment?}` | 200 |
| POST | `/{id}/lines` | Update | `{product, quantity}` | 200 |
| PUT | `/{id}/lines/{line_id}` | Update | `{quantity}` | 200 |
| DELETE | `/{id}/lines/{line_id}` | Update | — | 200 |
| POST | `/{id}/sales-order-items` | Update | `{sales_order}` | 200 |
| POST | `/{id}/dispatch` | Update | — | 200 |
| POST | `/{id}/receive` | Update | `{lines?: [{line_id, quantity > 0}]}`; omitting it receives everything outstanding | 200 |
| POST | `/{id}/close` | Delete | — | 200 |
| POST | `/{id}/cancel` | Delete | — | 200 |

**`InventoryTransferResponse`**:
- `inventory_transfer_id`, `facility`, `warehouse`, `warehouse_to`, `serial`;
- `status`;
- `comment`, `creator`, `updater`, `creation_time`, `modification_time`;
- `lines[]`, each with `transfer_detail_id`, `product`, `product_code`, `product_name`, `quantity`, `quantity_received` and `quantity_outstanding`.

**`InTransitResponse`**:
- `facility`;
- `products[]`: `{product, product_code, product_name, quantity}`;
- `lines[]`: `{inventory_transfer_id, serial, warehouse, warehouse_to, transfer_detail_id, product, product_code, product_name, quantity_outstanding}`.

## Status codes

| Code | When |
|---|---|
| 401 | No token, or an invalid one |
| 403 | The right is missing, or a non-administrator acts in another facility (FR-011) |
| 404 | Unknown document, line, warehouse, product, purchase order or sales order |
| 409 | Wrong state (not a draft, not in transit, already cancelled); no lines; short stock (names the product code and warehouse); receive over the outstanding quantity; complementary receipt not possible (the reason); sales order not confirmed |
| 422 | Validation: quantity ≤ 0, product not stockable, origin equal to destination, in-transit warehouse used, facility without a transit warehouse |
