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
uv run ruff format --check app/ migrations/ tests/
```

Expected: all tests pass, no ruff violations. The ones that speak to this feature:

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

Cross-check one count by hand:

```sql
SELECT COUNT(*) FROM product_price WHERE list = 2;
SELECT COUNT(*) FROM customer WHERE price_list = 2;
```

## 4. A retirement, rolled back

```bash
PYTHONPATH=. uv run python - <<'PY'
import asyncio
from sqlalchemy import func, select
from app.db.session import AsyncSessionLocal
from app.models.customer import Customer
from app.models.product import PriceList, ProductPrice
from app.services import price_list_service

RETIRED, REPLACEMENT = 2, 1  # substitute real ids

async def main() -> None:
    async with AsyncSessionLocal() as db:
        async def counts() -> tuple[int, int]:
            prices = await db.scalar(
                select(func.count()).select_from(ProductPrice).where(ProductPrice.price_list == RETIRED))
            movers = await db.scalar(
                select(func.count()).select_from(Customer).where(Customer.price_list == RETIRED))
            return prices, movers

        before = await counts()
        print(f'before: {before[0]} prices, {before[1]} customers on {RETIRED}')

        pl = await db.get(PriceList, RETIRED)
        # `delete_price_list` commits, so this runs it inside an outer transaction that is
        # rolled back afterwards — the commit lands on the savepoint, not the database.
        async with db.begin_nested():
            await price_list_service.delete_price_list(db, pl, replacement_id=REPLACEMENT)
            print('retired:', await counts(), '(expect (0, 0))')
            print('on replacement:', await db.scalar(
                select(func.count()).select_from(Customer).where(Customer.price_list == REPLACEMENT)))
            raise RuntimeError('rollback')

asyncio.run(main())
PY
```

Expected: the `RuntimeError` propagates, and the counts printed inside show `(0, 0)` for the retired
list while the replacement's customer count has risen by exactly the number that moved. Then
re-run step 3 and confirm every count is back to what it was.

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

## Success criteria

| Criterion | Verified by |
|-----------|-------------|
| SC-001 — one request retires a priced list | Step 5, first `DELETE` |
| SC-002 — one request regardless of customer count | Step 4, `replacement_id` moving every assignment |
| SC-003 — nothing still refers to the retired list | Step 4's `(0, 0)`; step 5's `total: 0` |
| SC-004 — a failed retirement changes nothing | Step 5's three refusals, then step 3 |
| SC-005 — the report matches what is acted on | Step 1, `test_report_counts_exactly_what_a_retirement_touches` |
| SC-006 — a later relation is covered with no edit | Step 2 printing `BLOCK` for anything unclassified |
