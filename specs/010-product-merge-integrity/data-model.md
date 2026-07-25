# Phase 1 Data Model: Product Merge Integrity

**No schema change.** This feature adds no table, column, index or migration. It reads
relationships the data model already declares and decides, for each, whether it describes
something that *happened* to a product or how a product is *set up*.

## The split

Every mapped foreign key pointing at `product.product_id`. The enumeration is
`referencing_columns(Product)`; the split is membership in `_MERGE_DISCARD`.

### History — carried over to the canonical (15)

`UPDATE <table> SET <column> = :canonical WHERE <column> = :duplicate`

| Relation | What it records |
|----------|-----------------|
| `sales_order_detail.product` | Sold |
| `sales_quote_detail.product` | Quoted |
| `delivery_order_detail.product` | Delivered |
| `fiscal_document_detail.product` | Invoiced (see below) |
| `customer_refund_detail.product` | Refunded |
| `purchase_order_detail.product` | Purchased |
| `purchase_request_detail.product` | Requested |
| `supplier_return_detail.product` | Returned to supplier |
| `service_order_detail.spare_part` | Consumed as a spare part |
| `inventory_receipt_detail.product` | Received into stock |
| `inventory_issue_detail.product` | Issued from stock |
| `inventory_transfer_detail.product` | Transferred between warehouses |
| `lot_serial_tracking.product` | Tracked by lot or serial |
| `lot_serial_rqmt.product` | Required lot or serial tracking |
| `commissions_history.product` | Commission earned |

**`service_order_detail` references a product through `spare_part`, not `product`.** The
enumeration carries the column, so the statement targets the right one; anything assuming the
column is named after the table would silently skip it.

**`fiscal_document_detail` follows like the rest.** A stamped CFDI line keeps its own
`product_code` / `product_name` snapshot, so what the document states was invoiced does not
change. Only the catalog row it points at moves — and that row is about to stop existing
(research R4).

### Configuration — discarded with the duplicate (4)

`DELETE FROM <table> WHERE <column> = :duplicate`

| Relation | Unique key covering the product column | What survives |
|----------|----------------------------------------|---------------|
| `product_price.product` | `(product, list)` | The canonical's prices |
| `product_label.product` | `(product, label)` | The canonical's labels |
| `commission_product.product` | `(product)` | The canonical's commission assignment |
| `customer_discount.product` | `(customer, product)` | The canonical's per-customer discounts |

These are the four relations that describe how a catalog row is *set up*, and — not by
coincidence — the four that cannot hold two rows for one product. The canonical is the row being
kept, so its own configuration is the one that stands, for every row, regardless of what the
duplicate had (research R5).

The unique keys above live in the database only; the models do not declare them. They are listed
here as the reason the split falls where it does, not as something the code reads.

**Data loss to be aware of**: a label or per-customer discount that existed *only* on the
duplicate is not carried over. Set it on the canonical before merging if it should survive.

## Enforcement is not coverage

| | Modelled | Enforced by the database |
|---|---|---|
| Foreign keys to `product` | 19 | 17 |
| The two that differ | — | `commission_product`, `commissions_history` declare a `ForeignKey` in `app/models/commission.py` and have no constraint in `mbe_demo` |

The merge reads the modelled set, which is the wider one. Before this feature the unenforced
pair produced the quieter failure: nothing stopped the deletion, so commission rows were left
pointing at a product id that no longer existed (research R2).

## Entities

### Blast-radius item (derived, not persisted)

| Field | Type | Meaning |
|-------|------|---------|
| `category` | `str` | `table.column` — the same label the referential guard uses in a delete conflict, so one vocabulary covers both |
| `count` | `int` | Records currently referring to the duplicate through that relation, always > 0 |

Ordering: descending by count, then by name for stability — inherited unchanged from
`find_blocking_references`. Relations with no rows are omitted rather than reported as zero. The
response adds `total`, the sum of the counts.

**What the total means**: the records a merge *touches*. Fifteen of the nineteen relations are
moved and four are deleted, so the total is not a count of records that will be reassigned
(FR-016).

## Relationship to feature 006

The same scan, read for a third purpose. Feature 006 reads it inward to refuse a delete
(`assert_not_referenced`) and to name the blockers (`find_blocking_references`). This feature
reuses the enumeration underneath both (`referencing_columns`) to rewrite the references instead
of refusing.

`delete_product` still exempts `product_price` from its guard, because a delete cascades those
rows away. A merge has no exempt set: it deletes the duplicate's `product_price` rows too, but
they are part of the blast radius an operator should see, not a cascade to hide.

## State transitions

None introduced. A merge is a deletion of the duplicate, not a status change; the `status`
lifecycle from feature 005 is untouched, and a merge is never a substitute for archiving.
