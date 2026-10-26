# Contract: Purchase Requests, Purchase Orders and Cost

Base path: `/api/v1`. Every route needs a bearer token, and administrators bypass the privilege checks. Errors use the usual `{"detail": "..."}` shape. Write responses carry `warnings: [str]`, which never stop the operation.

## Purchase requests: `/purchase-requests`

Object 86 is PurchaseRequest and 96 is PurchaseRequestApproval. "Facility" in the right column means a non-administrator acting on another facility's request gets 403 (FR-011).

| Method | Path | Right | Body / query | Success |
|---|---|---|---|---|
| GET | `` | 86 Read | `all` (false), `status` (`draft`, `submitted`, `approved`, `cancelled`), `warehouse`, `facility`, `creator`, `serial`, `id`, `from`, `to`, `skip`, `limit` | 200 `ListResponse[PurchaseRequestSummary]` |
| POST | `` | 86 Create | `{warehouse?, comment?}`; warehouse defaults to the caller's point of sale | 201 `PurchaseRequestResponse` |
| GET | `/{id}` | 86 Read | — | 200 |
| PUT | `/{id}` | 86 Update, facility | `{warehouse?, comment?}` | 200 |
| POST | `/{id}/lines` | 86 Update, facility | `{product, quantity?, product_name?, warehouse?}` | 200 |
| PUT | `/{id}/lines/{line_id}` | 86 Update, facility | `{quantity?, product_name?, warehouse?}` | 200 |
| DELETE | `/{id}/lines/{line_id}` | 86 Update, facility | — | 200 |
| POST | `/{id}/submit` | 86 Update, facility | — | 200 |
| POST | `/{id}/cancel` | 86 Delete, facility | — | 200 |
| PUT | `/{id}/lines/{line_id}/to-purchase` | 20 Update | `{to_purchase: bool}` | 200 |
| GET | `/approval-queue` | 96 Read | `facility`, `skip`, `limit` | 200 `ListResponse[PurchaseRequestSummary]`, oldest first |
| POST | `/{id}/approve` | 96 Update | — | 200 |
| POST | `/approve` | 96 Update | `{ids: [int]}`, 1–100 items | 200 `{results: [{purchase_request_id, approved: bool, reason?}]}` |
| POST | `/{id}/reject` | 96 Update | `{reason}`, 1–500 characters | 200 |

- **`PurchaseRequestSummary`:**
  - `purchase_request_id`, `serial`, `status`, `warehouse`, `facility`, `date`, `comment`;
  - `creator`, `updater`, `approver`, `approval_time`;
  - `line_count`.
- **`PurchaseRequestResponse`:** the summary, plus `warnings`, plus `lines[]`, each with:
  - `purchase_request_detail_id`, `product`, `product_code`, `product_name`, `quantity`, `warehouse`, `to_purchase`, `min_order_qty`;
  - the live PO line, if any: `purchase_order_detail`, `purchase_order`, `purchase_order_status` (FR-010).
- **Warnings:** below the minimum order quantity (FR-003).

## Purchase orders: `/purchase-orders`

Object 20 is PurchasesOrders.

| Method | Path | Right | Body / query | Success |
|---|---|---|---|---|
| GET | `` | Read | `all` (false), `status` (`draft`, `completed`, `cancelled`), `supplier`, `creator`, `id`, `from`, `to`, `skip`, `limit` | 200 `ListResponse[PurchaseOrderSummary]` |
| POST | `` | Create | `{supplier, estimated_receipt_date?, comment?}` | 201 `PurchaseOrderResponse` |
| GET | `/{id}` | Read | — | 200 |
| PUT | `/{id}` | Update | `{supplier?, estimated_receipt_date?, comment?}` | 200 |
| POST | `/{id}/lines` | Update | `{product, quantity, warehouse?, price?, tax_rate?}` | 200 |
| PUT | `/{id}/lines/{line_id}` | Update | `{quantity?, warehouse?, price?, tax_rate?}` | 200 |
| DELETE | `/{id}/lines/{line_id}` | Update | — | 200 |
| POST | `/{id}/request-lines` | Update | `{purchase_request}` | 200, with `skipped` |
| POST | `/{id}/confirm` | Update | — | 200, with `receipts` |
| POST | `/{id}/cancel` | Delete | — | 200 |
| GET | `/{id}/document` | Read | — | 200 `application/pdf` |
| POST | `/{id}/email` | Update | `{to: [email], message?}` | 200 `{recipients, sent_at}` |
| GET | `/to-purchase` | Read | `facility` | 200 `{groups: [ToPurchaseGroup]}` |
| POST | `/to-purchase` | Create | `{supplier, facility}` | 201 `PurchaseOrderResponse`, with `skipped` |
| GET | `/cost-changes` | Read | `product` (required), `skip`, `limit` | 200 `ListResponse[CostChange]`, newest first |

- **`PurchaseOrderSummary`:** `purchase_order_id`, `status`, `supplier`, `supplier_name`, `estimated_receipt_date`, `comment`, `creator`, `updater`, `creation_time`, `modification_time`, `line_count`, `subtotal`, `tax`, `total`.
- **`PurchaseOrderResponse`:** the summary, plus `lines[]`, `warnings`, `skipped[]` and `receipts[]`:
  - each line: `purchase_order_detail_id`, `product`, `product_code`, `product_name`, `warehouse`, `quantity`, `price`, `tax_rate`, `amount`, `purchase_request_detail`, `purchase_request`, `quantity_received`, `quantity_pending`;
  - `skipped[]`: `{purchase_request_detail, reason}`;
  - `receipts[]`: `{inventory_receipt_id, warehouse, facility, line_count}`, on confirm.
- **`ToPurchaseGroup`:** `supplier` (null = no supplier), `supplier_name`, `facility`, `line_count`, `estimated_total`, and `lines[]`. Each line: `purchase_request_detail_id`, `purchase_request`, `serial`, `product`, `product_code`, `product_name`, `quantity`, `warehouse`, `cost`.
- **`CostChange`:** `product_cost_change_id`, `product`, `receipt`, `purchase_order_detail`, `previous_cost`, `new_cost`, `quantity_on_hand`, `quantity_received`, `unit_cost`, `reverted_change`, `creation_time`, `creator`.

## Changed: inventory receipts (spec 029)

- **`POST /inventory-receipts/{id}/confirm`:** for a receipt with a PO, updates cost (FR-031). `warnings` adds lines priced at 0 (FR-035).
- **`POST /inventory-receipts/{id}/cancel`:** for a confirmed receipt with a PO, restores cost or warns (FR-037).
- **`PUT /inventory-receipts/{id}`:** moving the warehouse of a receipt with a PO answers 409.

## Status codes

| Code | When |
|---|---|
| 401 | no or bad token |
| 403 | missing right; another facility's request (non-administrator) |
| 404 | unknown request, PO, line, product, warehouse or supplier |
| 409 | wrong state (an edit of a non-draft, approving a non-submitted request, a second confirm, cancelling a confirmed PO, cancelling a request with a line on a live PO, unmarking a line on a live PO), no lines, nothing left to add, busy lock, moving a numbered request to another facility, moving a PO receipt's warehouse |
| 422 | validation: quantity, price, product not purchasable, warehouse in transit, blank reason, bad email, `supplier = null` on to-purchase |
| 502 | the mail server refused the message |
| 503 | mail is not configured (as spec 024) |
