# Data Model: Purchase Requests, Purchase Orders and Average Cost

## Changed tables (migration 028)

### `purchase_request` (+2 columns)

| Column | Type | Null | Notes |
|---|---|---|---|
| `approver` | INT | YES | NEW. FK employee; set on approve, cleared on reject |
| `approval_time` | DATETIME | YES | NEW. Set on approve, cleared on reject |

The existing columns are used as they are:
- `warehouse`: the header warehouse, whose facility numbers the request;
- `serial`: set on first submit (research R2);
- `completed`, `approved`, `cancelled`: the state (research R1);
- `date`: the creation date.

### `purchase_order_detail` (+index, +FK)

- `KEY purchase_order_detail_request_detail_idx (purchase_request_detail)`;
- `FOREIGN KEY (purchase_request_detail) REFERENCES purchase_request_detail`.

## New table: `product_cost_change`

| Column | Type | Null | Notes |
|---|---|---|---|
| `product_cost_change_id` | INT AUTO_INCREMENT | NO | PK; the order of changes per product |
| `product` | INT | NO | FK product |
| `receipt` | INT | NO | FK inventory_receipt |
| `purchase_order_detail` | INT | YES | FK purchase_order_detail; NULL on a restore |
| `previous_cost` | DECIMAL(18,4) | NO | C; 0 when the product had no cost row |
| `new_cost` | DECIMAL(18,4) | NO | rounded half up (FR-039) |
| `quantity_on_hand` | DECIMAL(18,4) | YES | Q, floored at 0; NULL on a restore |
| `quantity_received` | DECIMAL(18,4) | YES | R; NULL on a restore |
| `unit_cost` | DECIMAL(18,7) | YES | U = price × (1 − discount); NULL on a restore |
| `reverted_change` | INT | YES | FK self; set on a restore row, pointing to the receipt's first change it undid |
| `creation_time` | DATETIME | NO | |
| `creator` | INT | NO | FK employee |

Indexes:
- `(product, product_cost_change_id)` to find a product's latest change and list its history;
- `(receipt)` for the restore on cancel.

## Existing tables written

- **`purchase_request`, `purchase_request_detail`:** lines take `product`, `product_name`, `quantity` (18,2) and `warehouse`, plus `to_purchase`, written as 1 on new lines (the column default is 0). `customer` is the default customer and `Accepted` is 0 (FR-013).
- **`purchase_order`:**
  - `supplier`, `estimated_receipt_date`, `comment`, the state flags, creator, updater and times;
  - `approved` = 0, `approver` = NULL and `invoice_number` = NULL.
- **`purchase_order_detail`:** price is before tax, and the defaults are fixed: `discount` 0, `currency` MXN, `exchange_rate` 1, `tax_included` 0 (FR-021).
- **`inventory_receipt`, `inventory_receipt_detail`:** the receipts generated on confirm (research R4), with `purchase_order` and `purchase_order_detail` set.
- **`product_price`, list 0:** the cost (`price`), created with default profits when missing (research R5).
- **`incidence`:** source 4 for a request reject (reason = the rejection reason), source 5 for a PO email (reason "Purchase order emailed", context "To: …").

## State transitions

**Request:**

```text
draft ──submit──▶ submitted ──approve──▶ approved
  ▲                  │
  └─────reject───────┘ (incidence; keeps serial)
draft | submitted | approved ──cancel──▶ cancelled   (refused if any line is on a live PO)
```

**PO:**

```text
draft ──confirm──▶ completed   (draft receipts per warehouse; once)
draft ──cancel───▶ cancelled   (lines kept; request lines pending again)
```

## Derived values

- **Pending request line:** request approved and not cancelled, `to_purchase` = 1, and no `purchase_order_detail` of a non-cancelled PO links it.
- **Live PO line of a request line:** the linking `purchase_order_detail` whose PO is not cancelled. There is at most one for new data; legacy has 109 doubles, and the API shows the lowest id.
- **PO line received:** Σ `inventory_receipt_detail.quantity` over completed, non-cancelled receipts linking the line. Pending = max(0, ordered − received).
- **Pending at confirm** (research R4): ordered − Σ quantity on non-cancelled receipts (draft or completed) linking the line.
- **Unit cost U:** `price × (1 − discount)`.
- **New cost:** `round_half_up((max(Q,0)×C + R×U) / (max(Q,0) + R), 4)`.

## Validation

| Field | Rule |
|---|---|
| Request line quantity | > 0, 2 decimals |
| Request line product | exists, purchasable |
| Request / PO warehouse | exists, not in transit |
| Request header warehouse after a serial | same facility as before (FR-002) |
| PO line quantity | > 0, 4 decimals |
| PO line price | ≥ 0, 7 decimals |
| PO line tax rate | 0 ≤ rate < 1, 4 decimals; defaults to the product's |
| PO estimated receipt date | not before today |
| Reject reason | 1–500 characters, not blank |
| Comment | ≤ 500 characters |
| Email | 1–10 addresses, deduplicated; message ≤ 1,000 characters |
