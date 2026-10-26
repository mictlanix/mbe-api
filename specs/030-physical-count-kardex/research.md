# Research: Physical Inventory Count, Stock and Kardex

All measurements are from mbe_dev on 2026-10-26 (see spec, Findings).

## R1. The count document on spec 029's module

**Decision**: add a `COUNT` kind to `inventory_documents`:
- header `InventoryCount`, line `InventoryCountDetail`, `line_fk='count_id'`. The column is `count`, mapped to the attribute `count_id`, because a selected row's `.count` is a tuple method;
- `warehouses=('warehouse',)` and `transit=False`;
- `transaction_type=PHYSICAL_COUNT` (12);
- `system_object=PHYSICAL_COUNT_ADJUSTMENT` (74);
- label "Count".

Reused as they are:
- `create_draft`, `update_header` and `get_document`;
- `list_documents`, with `simple_status_clause`;
- `begin_locked`, `next_serial` and `busy_as_conflict`.

Count-specific, in `inventory_count_service`:
- lines whose quantity is replaced rather than added, and which may be zero;
- batches;
- the blind response;
- confirm.

`inventory_documents.cancel` gains one refusal: a completed document of a kind with `cancellable_when_completed=False` (a new `Kind` field, true for the existing kinds) answers 409 "A confirmed count cannot be cancelled". A draft count cancels as any draft.

**Rationale**: the draft, lock, serial, scope and draft-cancel rules are identical to spec 029's (FR-001, FR-004, FR-008, FR-009). The differences are confined to lines and confirm.

**Alternatives considered**:
- **A standalone service:** it would duplicate the locking that took four analysis rounds in spec 029.
- **Reusing `add_line`:** it adds quantities, but a count replaces them (FR-002).

## R2. Confirm

**Decision**: one transaction inside `busy_as_conflict`:
1. **Lock:** `begin_locked(COUNT, id, stock=True)`. That locks the count row, then the warehouse and the facility's transit warehouse by primary key. The transit warehouse is the numbering lock (spec 029 R1). Any other movement of the warehouse by an inventory document queues behind it.
2. **Checks:** `assert_facility(document.facility)`, then `assert_editable`. The "no lines" check (409) runs after step 4 has added any uncounted lines, so an empty count confirmed with "include uncounted as zero" on a warehouse with nothing non-zero is refused and uses no serial.
3. **Serial:** `next_serial(InventoryCount, facility)`.
4. **On hand:** read with one grouped query, `SELECT product, SUM(quantity) FROM lot_serial_tracking WHERE warehouse = :w [AND product IN (chunk)] GROUP BY product`.
   - With "include uncounted as zero", it reads the whole warehouse, adds a line counted at 0 for each stockable product with a non-zero balance and no line, and snapshots its code and name.
   - Otherwise it reads only the counted products, in chunks of 1,000 ids.
5. **Post:** for each line, `difference = counted − on_hand`. Each non-zero difference posts one type-12 entry with `reference = count id`, as a single bulk `INSERT` executemany in chunks of 1,000. `post_movement`'s sign rule is applied in a pure helper that builds the rows.
6. **Lines:** each line gets `quantity_on_hand` and `difference`, through a bulk `UPDATE` executemany.
7. **Warnings,** read before commit:
   - **negative available:** `stock_ledger.reserved_by_warehouse` for the counted products, and every product where `counted − reserved < 0` (FR-010);
   - **overlap:** products on this count that also appear on another completed count of the same warehouse whose `modification_time` (its confirm time; confirmed counts are read-only) is at or after this count's `creation_time` (FR-010a). Times are stored to the second, so `>=` catches a confirm in the same second, at the cost of a rare extra warning.
8. **Finish:** set the serial last, then `completed`, `updater` and `modification_time`, and commit.

**Rationale**:
- **Locks:** after them, the reads are current (spec 029 R2). Reading on hand at confirm, under the warehouse lock, is "on hand at that moment" (FR-004).
- **Speed:** ORM `add()` of 15,000 entries, plus 15,000 attribute updates, flushes row by row. A core `insert()` and `update()` executemany sends batches, so 15,000 lines stay well inside 60 s (SC-003).
- **Chunks:** 1,000 keeps `IN` lists and packets small.
- **Idle warehouse for the opening count:** while confirm runs, the count holds the warehouse's lock. Every ledger or reservation insert into that warehouse (a sale's reservation, a pickup, a departure, a refund) takes a foreign-key lock on the same row and waits. MariaDB's lock wait timeout (50 s) is shorter than SC-003's 60 s budget, so a large count must be confirmed with the warehouse idle. The migration runbook (mictlanix/mbe-roadmap#145) must say so. Day-to-day counts of about 30 lines hold the lock for well under a second.

**Alternatives considered**:
- **On hand at the physical count's time (the current system's):** the request asks for on hand at confirm.
- **Posting through `post_movement` one by one:** too slow for the opening count.
- **One entry per line, including zero differences:** that adds noise to the kardex, and FR-004 says non-zero only.

## R3. Lines and batches

**Decision**:
- `PUT /inventory-counts/{id}/lines` takes a body `{lines: [{product | product_code, quantity ≥ 0}]}` of 1 to 2,000 items.
  - **Validation first, applied to the whole batch:** every code or id must exist and be stockable, and no product may repeat within the batch. Otherwise the answer is 422, listing every bad entry, and nothing is applied.
  - **Then the batch is applied:** an existing line's quantity is replaced, and a missing one is created with snapshots.
  - The request runs under `begin_locked` (document only). Product lookups use one `IN` query per batch.
- `PUT /inventory-counts/{id}/lines/{line_id} {quantity ≥ 0}` and `DELETE` edit a single line.
- **Line uniqueness:** a unique index `(count, product)` backs FR-002's single-line rule.

**Rationale**:
- **Batch size:** 2,000 per batch gives 8 requests for 15,000 lines (FR-011), and keeps each request small enough for nginx and timeouts.
- **Repeats:** replacing quantities makes a repeated batch idempotent (US2 scenario 2).

**Alternatives considered**:
- **File upload (CSV):** not requested. A list in the body is enough for the migration tooling.
- **Adding quantities, as receipts do:** a batch sent twice would double the count.

## R4. Blind drafts

**Decision**:
- `quantity_on_hand` and `difference` are NULL until confirm, and the draft response omits them. The response schema has them nullable, and the service never computes them for a draft.
- The count endpoints expose no expected quantity.
- The stock query (FR-020) stays available to anyone with right 67.

**Rationale**: clarification Q4 (FR-003a).

## R5. Stock queries

**Decision**:
- **`GET /stock?warehouse=&q=&include_zero=false&skip&limit`:**
  1. on hand per product: `SUM(quantity) GROUP BY product` for the warehouse;
  2. reserved: `lot_serial_rqmt`, type 11 only, grouped by product, for the warehouse;
  3. incoming: the outstanding quantities (`quantity − COALESCE(quantity_received, 0)`) on transfer lines whose transfer is in transit to that warehouse, grouped by product;
  4. the three are merged in Python, keyed by product.
  - **Filters:**
    - `q`: product code or name contains, case-insensitive;
    - `include_zero`: false keeps only products with any figure non-zero; true also adds the warehouse's other stockable products that have a ledger entry there.
  - **Sorting and paging:** sorted by product code, then paged.
- **`GET /stock/products/{product}`:**
  - one row per warehouse with any non-zero figure, with on hand, reserved, available and incoming;
  - plus `in_transit`: one row per facility whose transit warehouse holds a non-zero balance of the product.
  - Built from three queries: `stock_ledger.on_hand_by_warehouse`, `reserved_by_warehouse`, and the product's lines on transfers in transit, grouped by `warehouse_to` (incoming).
- **Rights:** Read on WarehouseStockReport (67) for both, with no facility scope (the current report has none).
- **Not offered:** the in-transit warehouses themselves as a `/stock?warehouse=` target. Spec 013 forbids listing them, so that answers 422.

**Rationale**:
- **No label join**, so there is no multiplication (FR-022).
- **Speed:** grouped sums measured under 1 s for the largest warehouse.

## R6. Kardex

**Decision**: `GET /kardex?product=&warehouse=&from=&to=`.
- **Dates:**
  - `from` and `to` are dates, and `to` is inclusive of its whole day (`date < to + 1 day`);
  - more than 366 days, or `to` before `from`, answers 422.
- **Opening balance:** `SUM(quantity)` for the product and warehouse with `date < from`.
- **Rows:**
  - every entry with `from ≤ date < to + 1 day`, ordered by `(date, lot_serial_tracking_id)`;
  - each row shows date and time, `source`, a type name, quantity in or out, and the running balance.
- **Closing balance:** the opening balance plus the movements.
- **Document resolution:** batched, with one `IN` query per type over the references in the range:

  | Type | Table | Shown |
  |---|---|---|
  | 1 | `sales_order` | facility, serial |
  | 2 | `customer_refund` | facility, serial. Legacy rows written by payment cancellation reference a sales order, not a refund. So a row resolves only when a refund with that id exists **and** the entry's product is on that refund's lines (`customer_refund_detail`). On mbe_dev that links 9,234 of 9,267 type-2 entries; the other 33, including the 31 payment-cancellation rows whose reference collides with a refund id, show raw (spec Assumptions). |
  | 3 | `inventory_issue` | facility, serial |
  | 4 | `inventory_receipt` | facility, serial |
  | 5 | `inventory_transfer` | facility, serial |
  | 6 | `purchase_order` | id only, legacy |
  | 8 | `inventory_receipt` | reference 0 is unresolved |
  | 10 | `deliveries_itinerary` | id only, no serial |
  | 12 | `inventory_count` | facility, serial |
  | 13 | `delivery_order` | facility, serial |
  | 7, 9, other | — | raw |

  Each resolved row carries `{type, id, facility, serial}`, which the client turns into a link.
- **Rights:** Read on Kardex (32).

**Rationale**:
- **No netting:** one row per entry (FR-030), unlike the current system's daily netting.
- **Speed:** measured 0.4 s for the busiest product's opening sum.
- **Range:** the busiest product and warehouse has 9,525 entries in all, so a 366-day range stays a single response with no paging.

**Alternatives considered**:
- **Paging the kardex:** a running balance across pages adds complexity, and the volumes don't need it.
- **Netting by day, as the current system does:** it hides documents.

## R7. Delivery pickup as type 13

**Decision**:
- `TransactionType.DELIVERY_PICKUP = 13`, and `PHYSICAL_COUNT = 12`.
- `delivery_order_service` posts the counter pickup with `source=DELIVERY_PICKUP`.
- Nothing reads pickup entries back by type; the only users of `DELIVERY_ORDER` are the posting sites (delivery_order_service:886, delivery_itinerary_service:627/636/779/789).
- Tests that assert the pickup's source change to 13.
- The enum docstrings say 10 is the itinerary (departure and settlement) and 13 the counter pickup.

**Rationale**: clarification Q2. mbe_dev has no type-10 entries, so no data needs converting.

## R8. Migration 027

**Decision**: create two tables in the style of migration 023 (`CREATE TABLE IF NOT EXISTS`, utf8mb3_unicode_ci, InnoDB, named keys):
- `inventory_count`:
  - `inventory_count_id` PK AUTO_INCREMENT;
  - `facility`, `warehouse`, `creator`, `updater`, each an FK;
  - `serial` INT NULL, with UNIQUE `(facility, serial)`;
  - `creation_time` and `modification_time`;
  - `completed` and `cancelled` flags;
  - `comment` VARCHAR(500) NULL.
- `inventory_count_detail`:
  - `count_detail_id` PK;
  - `count` FK (ORM attribute `count_id`), `product` FK;
  - `product_code` VARCHAR(25) NULL and `product_name` VARCHAR(250) NULL;
  - `quantity` DECIMAL(18,4) NOT NULL (counted);
  - `quantity_on_hand` DECIMAL(18,4) NULL and `difference` DECIMAL(18,4) NULL;
  - UNIQUE `(count, product)`.

The rollback drops the detail table first. Both tables are documented in `docs/data-dictionary.md` §7 as API-only, migration 027.

**Rationale**: the header mirrors spec 029's documents, so `inventory_documents` works on it unchanged (R1).
