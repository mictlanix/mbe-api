# Quickstart: Validating Price List Retirement

Steps 1–3 are read-only. **Step 4 writes and then rolls back** — read it before running it. Step 5
retires a list for real and is the only step that keeps its changes; do it on a throwaway list.

## Prerequisites

- `uv sync`
- `.env` pointing at `mbe_dev` for steps 3–5. Steps 1–2 need no database.

## 1. Test suite

```bash
uv run pytest -q
uv run ruff check app/ migrations/ tests/
uv run ruff format --check app/services/price_list_service.py \
  app/api/v1/endpoints/price_lists.py app/schemas/product.py tests/api/test_products.py
```

Expected: all tests pass, no ruff violations. The format check is scoped to the touched files on
purpose — run repo-wide it reports 51 files, which is GH #96's unresolved quote-style contradiction
and predates this feature. The ones that speak to this feature:

- `tests/unit/test_price_list_service.py` — the statements a retirement issues are observed, not
  mocked: the customer move is issued before the blocker check, the cascade deletes exactly the
  cascade set and nothing else, the exempt set and the cascade set are the same object, the
  replacement is validated before anything is written, and the report counts every relation
- `tests/api/test_products.py` — the report's shape and totalling, that
  `/{id}/delete/preview` is not swallowed by `GET /{price_list_id}`, that `replacement` reaches the
  service, and that both endpoints require authentication
- `tests/integration/test_price_list_retirement.py` — against a real schema with foreign keys
  enforced: prices actually gone, other lists' prices intact, customers actually moved, and a
  refused retirement leaving every row where it was

The invariant to look for is `test_report_counts_exactly_what_a_retirement_touches`: it asserts the
report's categories equal the union of what the retirement moves and deletes. If it can fail, the
report is a lie.

## 2. The split is what the data model says it is

```bash
PYTHONPATH=. uv run python - <<'PY'
from app.models.product import PriceList
from app.services.price_list_service import _DELETE_CASCADE
from app.services.references import referencing_columns

cols = referencing_columns(PriceList)
print(f'{len(cols)} relations reference price_list')
for table, column in cols:
    verb = 'DELETE' if table.name in _DELETE_CASCADE else 'BLOCK '
    print(f'  {verb}  {table.name}.{column.name}')
PY
```

Expected: 2 relations — `DELETE product_price.list` and `BLOCK customer.price_list`. Two things to
confirm by eye: the only `DELETE` is the list's own prices, and the column named for
`product_price` is `list`, the database's name, not the mapped attribute `price_list`.

If a third relation ever appears here, it prints `BLOCK` — that is the intended answer for a
relation nobody has classified yet, not a gap.

## 3. What rides on a real list

```bash
# A populated list. Substitute an id from GET /api/v1/price-lists.
curl -s -H "Authorization: Bearer $TOKEN" \
  localhost:8000/api/v1/price-lists/2/delete/preview | jq
```

Expected: `items` with `product_price.list` and, if anyone is assigned, `customer.price_list`,
largest first, and `total` their sum. Re-run `GET /api/v1/price-lists/2` afterwards and confirm the
list is untouched — the report is read-only.

**Measured against `mbe_dev`, 2026-08-29** — three lists, and the shape of the problem in one table:

| id | name | prices | customers |
|----|------|--------|-----------|
| 0 | Costo | 21,571 | 0 |
| 1 | Mostrador | 21,569 | 10,775 |
| 3 | Mayoreo | 21,569 | 150 |

Every list carries ~21.5k prices, so before this change *every* list in the deployment was
undeletable, and clearing the blocker by hand was 21,569 `DELETE` requests. `GET
/price-lists/3/delete/preview` answers `{"items": [{"category": "product_price.list", "count":
21569}, {"category": "customer.price_list", "count": 150}], "total": 21719}`.

Note `Costo` at id **0** — the list `settings.cost_price_list_id` points at. A falsy primary key,
so `replacement=0` has to be a real replacement and not read as "none given"; the service tests
`is not None`, never truthiness.

Cross-check one count by hand:

```sql
SELECT COUNT(*) FROM product_price WHERE list = 2;
SELECT COUNT(*) FROM customer WHERE price_list = 2;
```

## 4. A retirement, rolled back

**Read this before running it.** `delete_price_list` commits, so the transaction has to be arranged
from outside the session. `session.begin_nested()` is *not* enough — in SQLAlchemy 2.0
`Session.commit()` commits the outermost transaction and releases any savepoint under it, so a
nested block would let the retirement land for real. The session has to be bound to a connection
whose transaction it does not own, with `join_transaction_mode='create_savepoint'`, which is what
turns its `commit()` into a savepoint release.

```bash
PYTHONPATH=. uv run python - <<'ROLLBACK'
import asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal, engine
from app.models.customer import Customer
from app.models.product import PriceList, ProductPrice
from app.services import price_list_service

RETIRED, REPLACEMENT = 3, 1  # substitute real ids


async def counts(db: AsyncSession, list_id: int) -> tuple[int, int]:
    prices = await db.scalar(
        select(func.count()).select_from(ProductPrice).where(ProductPrice.price_list == list_id)
    )
    people = await db.scalar(
        select(func.count()).select_from(Customer).where(Customer.price_list == list_id)
    )
    return int(prices or 0), int(people or 0)


async def main() -> None:
    async with engine.connect() as connection:
        outer = await connection.begin()
        # The session commits onto a savepoint inside `outer`, which is never committed.
        async with AsyncSession(bind=connection, join_transaction_mode='create_savepoint') as db:
            print('before ', await counts(db, RETIRED), await counts(db, REPLACEMENT))
            pl = await db.get(PriceList, RETIRED)
            await price_list_service.delete_price_list(db, pl, replacement_id=REPLACEMENT)
            print('retired', await counts(db, RETIRED), await counts(db, REPLACEMENT))
            print('list row gone:', await db.get(PriceList, RETIRED) is None)
        await outer.rollback()

    # A fresh connection, to prove the rollback reached the database and not just a cache.
    # `AsyncSessionLocal()`, not `AsyncSession(bind=engine)`: the engine sets `pool_pre_ping`,
    # whose ping runs outside the greenlet when a session binds the engine directly.
    async with AsyncSessionLocal() as db:
        print('after rollback, list row back:', await db.get(PriceList, RETIRED) is not None)
    await engine.dispose()


asyncio.run(main())
ROLLBACK
```

Expected: the retired list drops to `(0, 0)`, the replacement's customer count rises by exactly the
number that moved, the list row reads as gone inside the transaction — and after the rollback, on a
fresh connection, it is back. Then re-run step 3 and confirm every count is what it was.

**Measured against `mbe_dev`, 2026-08-29**, retiring Mayoreo (3) into Mostrador (1):

```text
before  retired=(21569, 150) replacement=(21569, 10775)
retired retired=(0, 0)       replacement=(21569, 10925)
list row gone: True
elapsed: 0.56s
after rollback — list back: True
after rollback — counts: (21569, 150) (21569, 10775)
```

21,569 prices deleted and 150 customers moved in 0.56s — four statements, not 21,719 requests. The
replacement's customer count rises by exactly 150 and its own 21,569 prices are untouched. This is
the run that exercises the MariaDB dialect: `tests/integration/` proves the behaviour on SQLite,
and only this proves the cascade's `list` identifier renders and executes on the deployed engine.

Rehearse it on a throwaway list first. If the savepoint arrangement is wrong this deletes a real
price list and every price in it, and there is no undo.

## 5. End to end through the API

On a list you are willing to lose:

```bash
# 1. Create a list and price something in it
NEW=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Scratch","high_profit_margin":"0.3","low_profit_margin":"0.1"}' \
  localhost:8000/api/v1/price-lists | jq -r .price_list_id)

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"product\":1,\"price_list\":$NEW,\"price\":\"10\",\"low_profit\":\"1\",\"high_profit\":\"2\"}" \
  localhost:8000/api/v1/product-prices | jq -c

# 2. It used to be undeletable. Now it is 204.
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE -H "Authorization: Bearer $TOKEN" \
  localhost:8000/api/v1/price-lists/$NEW
```

Expected: `204`, which is the reproduction in GH #181 answered. Then:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/api/v1/product-prices?price_list=$NEW" | jq .total   # 0
```

And the refusals, each of which must change nothing:

```bash
# Customers assigned, no replacement → 409 naming customer.price_list
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/price-lists/2 | jq .detail

# Replacement that does not exist → 404
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/api/v1/price-lists/2?replacement=999999" | jq .detail

# The list as its own replacement → 400
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "localhost:8000/api/v1/price-lists/2?replacement=2" | jq .detail
```

Expected: `409` naming `customer.price_list` with its count, `404 Replacement price list not found`,
`400 Cannot replace a price list with itself` — and after all three, step 3's counts unchanged.

**Measured against `mbe_dev`, 2026-08-29**, through the real app on the real database:

```text
created list 25   -> 201
priced product 1  -> 201
preview           -> 200 {"items": [{"category": "product_price.list", "count": 1}], "total": 1}
no replacement    -> 409 Still referenced by customer.price_list (150) — remove those records first
missing replace   -> 404 Replacement price list not found
self as replace   -> 400 Cannot replace a price list with itself
DELETE priced list-> 204
prices left       -> 0
list afterwards   -> 404
```

The line that matters most is the `409`: it names `customer.price_list` **and not**
`product_price.list`, which on this list would have read `(21569)`. That is the half #181 called
unactionable, gone from the refusal.

## Success criteria

| Criterion | Verified by |
|-----------|-------------|
| SC-001 — one request retires a priced list | Step 5, first `DELETE` |
| SC-002 — one request regardless of customer count | Step 4, `replacement_id` moving every assignment |
| SC-003 — nothing still refers to the retired list | Step 4's `(0, 0)`; step 5's `total: 0` |
| SC-004 — a failed retirement changes nothing | Step 5's three refusals, then step 3 |
| SC-005 — the report matches what is acted on | Step 1, `test_report_counts_exactly_what_a_retirement_touches` |
| SC-006 — a later relation is covered with no edit | Step 2 printing `BLOCK` for anything unclassified |
