# Quickstart Validation Guide: Master Data REST Endpoints

**Purpose**: Verify that the feature works end-to-end after implementation.

**Prerequisites**:
- `uv run uvicorn app.main:app --reload` running on `http://localhost:8000`
- A valid JWT token (obtain via `POST /api/v1/auth/login`)
- At least one `PriceList` row in the DB (or create one as the first test)
- MariaDB accessible and migrated

Set `TOKEN` and `BASE` before running:
```bash
BASE="http://localhost:8000/api/v1"
TOKEN="<your-jwt>"
H='-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"'
```

---

## Scenario 1: Unauthenticated requests are rejected

```bash
curl -s -o /dev/null -w "%{http_code}" $BASE/products
# expected: 401
```

---

## Scenario 2: Create and retrieve a price list

```bash
# Create
curl -s -X POST $BASE/price-lists \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Retail","high_profit_margin":0.4,"low_profit_margin":0.1}' | jq .
# expected: 201, price_list_id returned

# List
curl -s $BASE/price-lists -H "Authorization: Bearer $TOKEN" | jq '.total'
# expected: >= 1
```

---

## Scenario 3: Create a product with auto-defaults applied

```bash
curl -s -X POST $BASE/products \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "code":"P001",
    "name":"Test Product",
    "unit_of_measurement":"H87",
    "currency":0,
    "stockable":true,
    "perishable":false,
    "seriable":false,
    "purchasable":true,
    "salable":true,
    "invoiceable":true
  }' | jq '{id:.product_id,min_order_qty,stock_required,tax_rate}'

# expected:
#   min_order_qty: 1
#   stock_required: true
#   tax_rate: non-zero (from config default)
#
# Note: ProductResponse has no `prices` field — per-product pricing is a separate resource,
# see Scenario 15 below and specs/004-price-management-service.
```

---

## Scenario 4: Duplicate product code returns 409

```bash
curl -s -X POST $BASE/products \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code":"P001","name":"Dupe Product","unit_of_measurement":"H87","currency":0,
       "stockable":false,"perishable":false,"seriable":false,
       "purchasable":false,"salable":false,"invoiceable":false}' \
  -o /dev/null -w "%{http_code}"
# expected: 409
```

---

## Scenario 5: Invalid barcode returns 422

```bash
curl -s -X POST $BASE/products \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code":"P002","name":"Barcode Test","bar_code":"123","unit_of_measurement":"H87",
       "currency":0,"stockable":false,"perishable":false,"seriable":false,
       "purchasable":false,"salable":false,"invoiceable":false}' \
  -o /dev/null -w "%{http_code}"
# expected: 422
```

---

## Scenario 6: Full CRUD round-trip for a simple resource (Label)

```bash
# Create
ID=$(curl -s -X POST $BASE/labels \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Electronics","comment":null}' | jq -r .label_id)

# Read
curl -s $BASE/labels/$ID -H "Authorization: Bearer $TOKEN" | jq .name
# expected: "Electronics"

# Update
curl -s -X PUT $BASE/labels/$ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Electronics & Gadgets"}' | jq .name
# expected: "Electronics & Gadgets"

# Delete
curl -s -X DELETE $BASE/labels/$ID -H "Authorization: Bearer $TOKEN" -o /dev/null -w "%{http_code}"
# expected: 204

# Confirm gone
curl -s $BASE/labels/$ID -H "Authorization: Bearer $TOKEN" -o /dev/null -w "%{http_code}"
# expected: 404
```

---

## Scenario 7: Protected customer delete returns 409

```bash
# Assuming default_customer_id = 1
curl -s -X DELETE $BASE/customers/1 \
  -H "Authorization: Bearer $TOKEN" -o /dev/null -w "%{http_code}"
# expected: 409
```

---

## Scenario 8: Product merge

```bash
# Create two products
A=$(curl -s -X POST $BASE/products \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"code":"CANON","name":"Canonical Product","unit_of_measurement":"H87","currency":0,
       "stockable":false,"perishable":false,"seriable":false,"purchasable":false,
       "salable":false,"invoiceable":false}' | jq -r .product_id)

B=$(curl -s -X POST $BASE/products \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"code":"DUP","name":"Duplicate Product","unit_of_measurement":"H87","currency":0,
       "stockable":false,"perishable":false,"seriable":false,"purchasable":false,
       "salable":false,"invoiceable":false}' | jq -r .product_id)

# Merge
curl -s -X POST $BASE/products/merge \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"product_id\":$A,\"duplicate_id\":$B}" -o /dev/null -w "%{http_code}"
# expected: 204

# Confirm duplicate is gone
curl -s $BASE/products/$B -H "Authorization: Bearer $TOKEN" -o /dev/null -w "%{http_code}"
# expected: 404
```

---

## Scenario 9: Vehicle operator advisory flag

```bash
# Create an employee first (or use existing)
EMP_ID=1  # replace with real employee_id

# Create an operator with an expiration date in the past
curl -s -X POST $BASE/vehicle-operators \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{
    \"driver\":$EMP_ID,
    \"license_type\":\"B\",
    \"driver_license_number\":\"MX12345\",
    \"issue_date\":\"2020-01-01\",
    \"expiration_date\":\"2023-01-01\",
    \"issuing_location\":\"CDMX\",
    \"active\":true
  }" | jq .days_until_expiry
# expected: negative integer
```

---

## Scenario 10: Ruff compliance

```bash
uv run ruff check app/ tests/
# expected: All checks passed.
```

---

## Scenario 11: FK filter — products by supplier

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d '{"username":"user","password":"pass"}' | jq -r .access_token)

# Filter products by supplier_id=1
curl -s "http://localhost:8000/api/v1/products?supplier=1" \
  -H "Authorization: Bearer $TOKEN" | jq '{total: .total, first_code: .items[0].code}'
# expected: total ≥ 0; all returned products have supplier == 1
```

---

## Scenario 12: FK filter — customers by price list

```bash
curl -s "http://localhost:8000/api/v1/customers?price_list=1" \
  -H "Authorization: Bearer $TOKEN" | jq '.total'
# expected: count of customers assigned to price list 1
```

---

## Scenario 13: SAT catalog list and get-by-id

```bash
# List units of measurement
curl -s "http://localhost:8000/api/v1/sat/units-of-measurement?limit=5" \
  -H "Authorization: Bearer $TOKEN" | jq '{total: .total, first: .items[0].id}'
# expected: total > 0, first id is a 3-char SAT code (e.g. "H87")

# Get known code
curl -s "http://localhost:8000/api/v1/sat/units-of-measurement/H87" \
  -H "Authorization: Bearer $TOKEN" | jq .id
# expected: "H87"

# Unknown code → 404
curl -o /dev/null -s -w "%{http_code}" \
  "http://localhost:8000/api/v1/sat/units-of-measurement/XXX" \
  -H "Authorization: Bearer $TOKEN"
# expected: 404
```

---

## Scenario 14: SAT catalog write attempt → 405

```bash
curl -o /dev/null -s -w "%{http_code}" \
  -X POST "http://localhost:8000/api/v1/sat/units-of-measurement" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":"ZZZ"}'
# expected: 405
```

---

## Scenario 15: Label facets narrow with the current filter set

```bash
# Two labels, applied to overlapping sets of products (assumes labels 2 and 5 already exist,
# see Scenario 6 for creating one)
curl -s "http://localhost:8000/api/v1/products/labels/facets" \
  -H "Authorization: Bearer $TOKEN" | jq .
# expected: [{"label_id":2,"count":N}, {"label_id":5,"count":M}, ...] — every label carried by
# at least one product, no skip/limit params accepted

# Narrow by an existing label filter — the response should only include labels that still
# co-occur with products carrying label 2
curl -s "http://localhost:8000/api/v1/products/labels/facets?label=2" \
  -H "Authorization: Bearer $TOKEN" | jq .
# expected: label_id 2 itself appears (count = size of the label=2 result set); any label that
# never co-occurs with label 2 is absent from the array

# Filters that match zero products return an empty array, not an error
curl -s "http://localhost:8000/api/v1/products/labels/facets?search=zzz-no-such-product" \
  -H "Authorization: Bearer $TOKEN" | jq .
# expected: []
```

---

## References

- [API Contracts](contracts/api.md)
- [Data Model](data-model.md)
- [Feature Spec](spec.md)
