# Quickstart: Validating Product Merge Integrity

Steps 1–3 and 5 are read-only. **Step 4 writes and then rolls back** — read it before running
it, and run it against a database you are willing to hold a large transaction open on.

## Prerequisites

- `uv sync`
- `.env` pointing at a populated database (the counts below come from `mbe_dev`)

## 1. Test suite

```bash
uv run pytest -q
```

Expected: all tests pass. The ones that speak to this feature:

- `tests/unit/test_product_service.py` — the merge's statements are observed, not mocked:
  every mapped relation is covered, history and configuration never overlap, the split is
  exhaustive, no `UPDATE IGNORE` is issued, no history table is deleted from, and the preview
  counts exactly what the merge touches
- `tests/api/test_products.py` — the preview's shape and totalling, that `/merge/preview` is not
  swallowed by `GET /{product_id}`, and that it requires authentication

The invariant to look for is `test_preview_counts_exactly_what_a_merge_touches`: it asserts the
preview's categories equal the merge's targets. If it can fail, the preview is a lie.

## 2. The split is what the data model says it is

```bash
PYTHONPATH=. uv run python - <<'EOF'
from app.models.product import Product
from app.services.product_service import _MERGE_DISCARD
from app.services.references import referencing_columns

cols = referencing_columns(Product)
print(f'{len(cols)} relations reference product')
for table, column in cols:
    verb = 'DELETE' if table.name in _MERGE_DISCARD else 'UPDATE'
    print(f'  {verb}  {table.name}.{column.name}')
EOF
```

Expected: 19 relations, 15 `UPDATE`, 4 `DELETE`. Three things to confirm by eye:

- `service_order_detail.spare_part` is remapped through `spare_part`, not a column named
  `product` — the enumeration carries the column (research R1).
- The four `DELETE`s are exactly `product_price`, `product_label`, `commission_product`,
  `customer_discount` — configuration, nothing else (FR-007, FR-010).
- No history relation is deleted from.

## 3. Modelled coverage is wider than enforced coverage

```bash
PYTHONPATH=. uv run python - <<'EOF'
import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal, engine
from app.models.product import Product
from app.services.references import referencing_columns

async def main():
    async with AsyncSessionLocal() as db:
        enforced = {
            (t, c) for t, c in (await db.execute(text("""
                select table_name, column_name from information_schema.key_column_usage
                where referenced_table_name = 'product'
                  and referenced_column_name = 'product_id'
                  and table_schema = database()
            """))).all()
        }
        modelled = {(t.name, c.name) for t, c in referencing_columns(Product)}
        print(f'modelled={len(modelled)} enforced={len(enforced)}')
        print('modelled but not enforced:', sorted(modelled - enforced))
    await engine.dispose()

asyncio.run(main())
EOF
```

Expected:

```
modelled=19 enforced=17
modelled but not enforced: [('commission_product', 'product'), ('commissions_history', 'product')]
```

These two are why the merge reads the *modelled* set. Before this feature they were the silent
failure: nothing stopped the deletion, so their rows were left pointing at a product id that no
longer existed (research R2).

## 4. A real merge, rolled back

Mocks prove the loop covers what the metadata says. Only a real database proves the statements
execute and that the final deletion succeeds. `merge_products` commits, so the commit is replaced
with a flush to keep everything inside one transaction, and the transaction is rolled back.

```bash
PYTHONPATH=. uv run python - <<'EOF'
import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal, engine
from app.models.product import Product
from app.schemas.product import ProductMergeRequest
from app.services.product_service import _MERGE_DISCARD, merge_products
from app.services.references import referencing_columns

CANONICAL, DUPLICATE = 8, 18829  # pick a pair where both sides carry prices and labels

async def counts(db, product_id):
    return {
        f'{t.name}.{c.name}': (await db.execute(
            text(f'select count(*) from {t.name} where {c.name} = :p'), {'p': product_id}
        )).scalar_one()
        for t, c in referencing_columns(Product)
    }

async def main():
    db = AsyncSessionLocal()
    db.commit = db.flush  # everything stays inside the transaction
    try:
        before_c, before_d = await counts(db, CANONICAL), await counts(db, DUPLICATE)
        await merge_products(db, ProductMergeRequest(product_id=CANONICAL, duplicate_id=DUPLICATE))
        after_c, after_d = await counts(db, CANONICAL), await counts(db, DUPLICATE)

        gone = (await db.execute(
            text('select count(*) from product where product_id = :p'), {'p': DUPLICATE}
        )).scalar_one()
        print(f'duplicate rows remaining: {gone}  orphans: {sum(after_d.values())}')
        for key in sorted(before_c):
            table = key.split('.')[0]
            expected = before_c[key] if table in _MERGE_DISCARD else before_c[key] + before_d[key]
            flag = 'ok ' if after_c[key] == expected else 'BAD'
            kind = 'config ' if table in _MERGE_DISCARD else 'history'
            print(f'  {flag} {kind} {key:<35} {before_c[key]:>6} + {before_d[key]:>6} -> {after_c[key]:>6}')
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()

asyncio.run(main())
EOF
```

Expected: `duplicate rows remaining: 0  orphans: 0`, every line `ok`, and specifically —

- each of the four `config` lines leaves the canonical's count **unchanged** (FR-008),
- each `history` line lands on exactly canonical + duplicate (FR-001),
- nothing remains pointing at the duplicate (FR-003).

Against `mbe_dev`, merging 18829 into 8 moves 67,920 rows across 15 relations. Running the same
script against the pre-#112 code fails on `customer_refund_detail` instead.

## 5. Lint and types

```bash
uv run ruff check app/ migrations/ tests/
uv run ruff format --check app tests
uv run mypy app
```

Expected: ruff clean; mypy at its pre-existing baseline with **no** errors in
`product_service.py`, `references.py` or `products.py`.

## Success criteria mapping

| Criterion | Verified by |
|-----------|-------------|
| SC-001 every product can be merged | Step 4 on the most-referenced product; step 2 shows the coverage is total |
| SC-002 no record refers to the deleted duplicate | Step 4 (`orphans: 0`) |
| SC-003 no record survives pointing at a deleted product | Step 3 (the unenforced pair is covered) + step 4 |
| SC-004 the outcome is statable in one sentence | Step 2 (the split is by relation, not by row) |
| SC-005 scale visible before committing | `GET /merge/preview`, contract in [contracts/product-merge.md](contracts/product-merge.md) |
| SC-006 preview and merge cannot drift | Step 1 (`test_preview_counts_exactly_what_a_merge_touches`) |
| SC-007 new relations covered automatically | Step 2 (enumeration is metadata-driven, no list) |
