# Phase 0 Research: Product Merge Integrity

Every question here was settled by measuring the mapped metadata and the populated `mbe_dev`
database. The first measurement alone reversed the assumption the merge had been built on.

## R1 — How many relations actually reference `product`?

**Decision**: 19, from the mapped metadata. The merge was handling 8.

**Rationale**: `referencing_columns(Product)` returns 19 `(table, column)` pairs. The merge
handled six from a hand-written list (`sales_order_detail`, `purchase_order_detail`,
`inventory_receipt_detail`, `inventory_issue_detail`, `inventory_transfer_detail`,
`lot_serial_tracking`), plus `product_price` through a service call and `product_label` through
an `UPDATE IGNORE`. Eleven were never touched: `sales_quote_detail`, `delivery_order_detail`,
`fiscal_document_detail`, `customer_refund_detail`, `purchase_request_detail`,
`customer_discount`, `lot_serial_rqmt`, `supplier_return_detail`,
`service_order_detail.spare_part`, `commission_product` and `commissions_history`.

`service_order_detail` is worth naming separately: it references a product through a column
called `spare_part`. Any approach that assumes the referencing column is named `product` misses
it, which is one more reason the enumeration reports the column and not only the table.

**Alternatives considered**: Extending the list with the eleven — rejected, because the list
being wrong *is* the defect. A twentieth foreign key would reproduce it.

## R2 — What did the eleven actually cost?

**Decision**: Two distinct failures, both severe, and the second is silent.

**Rationale**: Nine of the eleven have an enforced foreign key in `mbe_dev`, so the final
deletion of the duplicate failed, the transaction rolled back, and the client received the
generic backstop conflict from `app.main` — which by design names nothing. Counted against live
rows, that is **13,248 of 21,542 products**: any product ever quoted, delivered, invoiced,
refunded or requested could not be merged at all. The products that cannot be merged were
precisely the products with a reason to be merged.

`commission_product` and `commissions_history` are worse. Both declare a `ForeignKey` in
`app/models/commission.py`, but neither has a constraint in the database. Nothing stopped those
deletions, so the merge *succeeded* and left commission rows pointing at a product id that no
longer existed — 1,008 and 248 products carry such rows.

**Consequence recorded**: modelled relationships and enforced constraints are not the same set.
The metadata is the wider one, and is therefore the right source for a remap; enforcement is not
what makes a reference real.

## R3 — Where should the remap set come from?

**Decision**: `referencing_columns()`, extracted from `find_blocking_references`.

**Rationale**: Feature 006 already built exactly this scan for the delete guards, and its
FR-007 already forbade hand-maintained per-entity lists for the same reason this feature
rediscovered. Extracting the enumeration lets the guard that counts references, the preview that
reports them and the merge that rewrites them read one source. A new foreign key to `product` is
covered by all three as soon as its model exists — this class of defect cannot recur by
omission.

**Alternatives considered**: A list inside `merge_products` mirroring what the preview counts —
rejected as the same defect with two copies instead of one.

## R4 — Should fiscal history be remapped, or should it block the merge?

**Decision**: Remapped, like everything else.

**Rationale**: Both rows describe the same physical product entered twice, so the reference
follows. A CFDI line keeps its own `product_code` / `product_name` snapshot, so what the stamped
document says was invoiced does not change — only the catalog row it points at, and that row is
about to stop existing. The alternative, refusing to merge any product with fiscal history,
would block 10,434 products, and would leave the reference dangling in exactly the cases where
the document matters most.

**Alternatives considered**: Refusing the merge when `fiscal_document_detail` rows exist. Sound
in isolation, wrong in effect: it protects a snapshot that was never at risk at the cost of the
majority of real merges.

## R5 — What happens to the four relations that cannot hold two rows per product?

**Decision**: The duplicate's rows are deleted outright. This reverses the first answer.

**Rationale**: `product_price(product, list)`, `product_label(product, label)`,
`commission_product(product)` and `customer_discount(customer, product)` each carry a unique key
covering the product column, so the duplicate's rows can never all land on the canonical.

The first implementation (#112) handled that with `UPDATE IGNORE` followed by a blanket
`DELETE`: whichever rows happened not to collide moved, the rest were dropped. Two objections
retired it. The outcome for any given row depended on whether the canonical already had its
counterpart — a rule nobody can state to an operator — and the canonical was left holding a
mixture of both products' configuration. Separately, `UPDATE IGNORE` suppresses errors it was
never meant to suppress; a statement that fails for an unrelated reason should undo the merge,
not be silently skipped.

Deleting them outright makes the rule statable: the canonical is the row being kept, so its own
prices, labels, commission assignment and per-customer discounts survive, for every row,
regardless of what the duplicate had. `product_price` — already discarded, but through
`product_price_service.delete_for_product` and an exempt set — joined the same set and the same
loop.

**Consequence accepted**: a label or per-customer discount that existed only on the duplicate is
no longer carried over. That is the honest reading of "the canonical's configuration wins", and
it is called out in the changelog because it loses data the old path sometimes kept.

**Why the set is declared rather than derived**: it is exactly the set of relations with a unique
key covering the product column, but the models do not carry those keys — only the database
does. Deriving it would mean writing the same four names while implying they were discovered. A
relation missed here fails loudly on its constraint and rolls the merge back rather than
corrupting anything.

## R6 — Should the preview count only what the merge reassigns?

**Decision**: No. It counts every relation referencing the duplicate.

**Rationale**: When the preview shipped (#111) the merge handled 8 of 19 relations, so the scan
reported a superset. That was deliberate: a non-zero count outside the eight was not work that
would be reassigned, it was a merge that was *going to fail on the final deletion*. Reporting
only the eight would have hidden the failure from the operator reviewing the merge. #112 then
closed the gap from the other side, and the two sets are now identical — an invariant a test
asserts rather than a coincidence.

**Correction recorded**: the counts describe what a merge *touches*, not what it reassigns. Four
of the nineteen relations are deleted rather than moved, so a client labelling the total "will
be reassigned" overstates it. The changelog entry for #111 said "the rows the merge moves" and
was corrected when the configuration split landed.

## R7 — What should gate a read-only preview?

**Decision**: `SystemObject.PRODUCTS_MERGE` (73) with `AccessRight.READ`.

**Rationale**: The preview belongs to the merge workflow and discloses how much history a
product carries, so it is not merely a product read. Gating it on the merge's own object at read
level lets a reviewer see the blast radius without holding the `AllowCreate` the merge itself
requires. `PRODUCTS` read was rejected as too broad for what it discloses, and `PRODUCTS_MERGE`
create as too narrow for a read.

## R8 — How is a destructive, irreversible operation verified against real data?

**Decision**: Run it against `mbe_dev` inside a transaction that is never committed.

**Rationale**: Mocks prove the loop covers what the metadata says; they cannot prove the
statements execute against a real schema or that the deletion at the end succeeds. Both fixes
were verified by merging the most-referenced product in the database with the session's commit
neutralised, inspecting the result, and rolling back:

- #112: merging the product with the most fiscal history — 83,488 rows across 13 relations. The
  deletion succeeds and nothing is left pointing at the duplicate; the previous path fails on
  `customer_refund_detail`.
- The configuration split: merging product 18829 (67,920 rows across 15 relations) into 8, where
  both sides carry a label, a commission row and prices. The duplicate is deleted with no
  orphans, each of the four configuration relations leaves the canonical's count untouched, and
  each of the twelve history relations lands on exactly canonical + duplicate.

## R9 — Why did the test suite not catch the configuration defect?

**Decision**: Because the test stubbed the one relation it was asserting about.

**Rationale**: The unit helper that captured the merge's statements patched out
`product_price_service.delete_for_product`, so `product_price` never appeared among the observed
statements. The preview/merge invariant therefore asserted a hardcoded literal for that one
relation, and removing the `delete_for_product` call from `merge_products` left the test green.
The helper now stubs nothing: every relation goes through the one loop, so what the tests observe
is what the merge does. With the stub gone, that test fails against the old code, along with
three others.

**Lesson recorded**: a test that stubs the collaborator it is making an assertion about is
asserting the stub. It is worth checking that a coverage test fails when the behaviour it covers
is deleted.
