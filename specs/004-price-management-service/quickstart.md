# Quickstart: Price Management Service

## Prerequisites

1. API running locally (`uv run uvicorn app.main:app --reload`)
2. A valid JWT for a user with `PRICING` privilege (all rights) and `PRODUCTS` `CREATE`/`READ`
3. An existing `price_list` row (create one via `POST /api/v1/price-lists` if needed)

## Scenario 1: New product has no prices and none are auto-created

```bash
curl -s -X POST http://localhost:8000/api/v1/products \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"code":"P100","name":"Test Widget","unit_of_measurement":"H87","currency":0}'

# Expected: response has no "prices" key at all.
curl -s http://localhost:8000/api/v1/product-prices?product=<new_product_id> \
  -H "Authorization: Bearer <token>"
# Expected: {"items": [], "total": 0}
```

## Scenario 2: Create a price for a product + price list

```bash
curl -s -X POST http://localhost:8000/api/v1/product-prices \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"product": <product_id>, "price_list": <price_list_id>, "price": "199.99", "low_profit": "10", "high_profit": "30"}'

# Expected: 201, body echoes price with resolved price_list object nested.
```

## Scenario 3: Duplicate product + price_list pair is rejected

```bash
# Repeat the exact same POST from Scenario 2
# Expected: 409 Conflict
```

## Scenario 4: Filter prices by product and by price list

```bash
curl -s "http://localhost:8000/api/v1/product-prices?product=<product_id>" -H "Authorization: Bearer <token>"
curl -s "http://localhost:8000/api/v1/product-prices?price_list=<price_list_id>" -H "Authorization: Bearer <token>"
# Expected: each returns only matching entries
```

## Scenario 5: Update and delete a price entry

```bash
curl -s -X PUT http://localhost:8000/api/v1/product-prices/<product_price_id> \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"price": "210.00"}'
# Expected: 200, price updated, low_profit/high_profit unchanged

curl -s -X DELETE http://localhost:8000/api/v1/product-prices/<product_price_id> \
  -H "Authorization: Bearer <token>" -o /dev/null -w "%{http_code}\n"
# Expected: 204

curl -s http://localhost:8000/api/v1/product-prices/<product_price_id> \
  -H "Authorization: Bearer <token>" -o /dev/null -w "%{http_code}\n"
# Expected: 404
```

## Scenario 6: Deleting a product cleans up its prices

```bash
# Create a product, create a price for it, then delete the product:
curl -s -X DELETE http://localhost:8000/api/v1/products/<product_id> -H "Authorization: Bearer <token>"

curl -s "http://localhost:8000/api/v1/product-prices?product=<product_id>" -H "Authorization: Bearer <token>"
# Expected: {"items": [], "total": 0} — no orphaned rows
```

## Scenario 7: Merging products removes the duplicate's prices only

```bash
# Given canonical product A (with its own prices) and duplicate product B (with its own prices):
curl -s -X POST http://localhost:8000/api/v1/products/merge \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"product_id": <A_id>, "duplicate_id": <B_id>}'

curl -s "http://localhost:8000/api/v1/product-prices?product=<B_id>" -H "Authorization: Bearer <token>"
# Expected: {"items": [], "total": 0}
curl -s "http://localhost:8000/api/v1/product-prices?product=<A_id>" -H "Authorization: Bearer <token>"
# Expected: A's original prices, unaffected
```

## Running the Unit Tests

```bash
uv run pytest tests/api/test_product_prices.py tests/api/test_products.py -v
```

See [contracts/product-prices.md](contracts/product-prices.md) for the full request/response specs.
