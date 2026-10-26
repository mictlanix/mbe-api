# Data Model: Physical Inventory Count, Stock and Kardex

## New tables (migration 027)

### `inventory_count`

| Column | Type | Null | Notes |
|---|---|---|---|
| `inventory_count_id` | INT AUTO_INCREMENT | NO | PK |
| `facility` | INT | NO | FK facility; the warehouse's facility |
| `warehouse` | INT | NO | FK warehouse; never an in-transit one |
| `serial` | INT | YES | set on confirm; UNIQUE `(facility, serial)` |
| `creation_time`, `modification_time` | DATETIME | NO | `modification_time` is the confirm time once confirmed |
| `creator`, `updater` | INT | NO | FK employee |
| `completed`, `cancelled` | TINYINT(1) | NO | default 0 |
| `comment` | VARCHAR(500) | YES | |

### `inventory_count_detail`

| Column | Type | Null | Notes |
|---|---|---|---|
| `count_detail_id` | INT AUTO_INCREMENT | NO | PK |
| `count` | INT | NO | FK inventory_count; ORM attribute `count_id` |
| `product` | INT | NO | FK product; UNIQUE `(count, product)` |
| `product_code`, `product_name` | VARCHAR(25) / VARCHAR(250) | YES | snapshots |
| `quantity` | DECIMAL(18,4) | NO | counted, ≥ 0 |
| `quantity_on_hand` | DECIMAL(18,4) | YES | read at confirm; NULL while a draft |
| `difference` | DECIMAL(18,4) | YES | `quantity − quantity_on_hand`, posted if non-zero; NULL while a draft |

## State

`DocumentStatus`: `draft` (not completed, not cancelled), `completed`, `cancelled`.

```text
draft ──confirm──▶ completed          (read-only, not cancellable)
draft ──cancel───▶ cancelled          (no ledger effect)
```

## Ledger types (`TransactionType`)

| Type | Meaning | Reference |
|---|---|---|
| 8 | The current system's adjustment (legacy only) | count receipt id (0 for the 2025 bulk adjustment) |
| 10 | Delivery itinerary: departure and settlement | `deliveries_itinerary_id` |
| **12** | **Physical count** (new) | `inventory_count_id` |
| **13** | **Delivery pickup** (new; was 10) | `delivery_order_id` |

## Read models

- **StockRow:** `product, product_code, product_name, warehouse, on_hand, reserved, available, incoming`.
  - `reserved`: type-11 reservations only.
  - `available = on_hand − reserved`.
  - `incoming`: outstanding quantities of transfers in transit to the warehouse.
- **ProductStock:** `product`, `warehouses: [StockRow]`, `in_transit: [{facility, warehouse, on_hand}]`.
- **KardexRow:** `entry id, date, source, source_name, document {type, id, facility, serial} | null, reference, quantity_in, quantity_out, balance`.
- **Kardex:** `product, warehouse, from, to, opening, rows, total_in, total_out, closing`.

## Validation

| Rule | Error |
|---|---|
| Counted quantity < 0, or more than 4 decimals | 422 |
| Batch empty or over 2,000 items | 422 |
| Batch entry unknown, not stockable or repeated | 422 listing every bad entry; nothing applied |
| Editing or confirming a count that isn't a draft | 409 |
| Confirming a count with no lines and without `include_uncounted` | 409 |
| Cancelling a confirmed count | 409 "A confirmed count cannot be cancelled" |
| Counting an in-transit warehouse; stock of an in-transit warehouse | 422 |
| Unknown count, line, warehouse or product | 404 |
| Non-administrator of another facility confirming or cancelling | 403 |
| Kardex range over 366 days, or `to` before `from` | 422 |
| A deadlock or lock wait timeout | 409 "The warehouse is busy, retry" |
