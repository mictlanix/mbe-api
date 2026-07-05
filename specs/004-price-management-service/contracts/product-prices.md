# Contract: Product Prices

All endpoints require a bearer JWT and the `PRICING` privilege (`SystemObject.PRICING`), with the
`AccessRight` noted per endpoint. Returns `401` if unauthenticated, `403` if the privilege/right is
missing.

## `GET /api/v1/product-prices`

List price entries, optionally filtered.

**Privilege**: `PRICING` / `READ`

| Query param | Type | Required | Description |
|-------------|------|----------|--------------|
| `product` | int | no | Filter to entries for this product |
| `price_list` | int | no | Filter to entries under this price list |
| `skip` | int | no | Pagination offset (default 0) |
| `limit` | int | no | Pagination size (default 20, max 100) |

### 200 OK

```json
{
  "items": [
    {
      "product_price_id": 501,
      "product": 42,
      "price_list": { "price_list_id": 1, "name": "Retail", "high_profit_margin": "0.3000", "low_profit_margin": "0.1000" },
      "price": "199.9900",
      "low_profit": "10.000000",
      "high_profit": "30.000000"
    }
  ],
  "total": 1
}
```

## `POST /api/v1/product-prices`

Create a price entry for a product + price list combination.

**Privilege**: `PRICING` / `CREATE`

### Request

```json
{ "product": 42, "price_list": 1, "price": "199.99", "low_profit": "10", "high_profit": "30" }
```

### 201 Created

Returns the created entry (same shape as the list item above).

### 404 Not Found

`product` or `price_list` does not reference an existing record.

```json
{ "detail": "Product not found" }
```
```json
{ "detail": "Price list not found" }
```

### 409 Conflict

A price entry already exists for this `product` + `price_list` pair.

```json
{ "detail": "Price already exists for this product and price list" }
```

### 422 Unprocessable Entity

`price`, `low_profit`, or `high_profit` is negative.

## `GET /api/v1/product-prices/{product_price_id}`

**Privilege**: `PRICING` / `READ`

### 200 OK

Single entry, same shape as the list item.

### 404 Not Found

```json
{ "detail": "Product price not found" }
```

## `PUT /api/v1/product-prices/{product_price_id}`

Update `price`, `low_profit`, and/or `high_profit`. `product` and `price_list` are immutable.

**Privilege**: `PRICING` / `UPDATE`

### Request

```json
{ "price": "210.00" }
```

### 200 OK

Returns the updated entry.

### 404 Not Found / 422 Unprocessable Entity

Same as above.

## `DELETE /api/v1/product-prices/{product_price_id}`

**Privilege**: `PRICING` / `DELETE`

### 204 No Content

### 404 Not Found

```json
{ "detail": "Product price not found" }
```

---

# Contract change: Product endpoints no longer carry pricing

`GET /api/v1/products`, `GET /api/v1/products/{id}`, `POST /api/v1/products`,
`PUT /api/v1/products/{id}` — `ProductResponse` no longer includes a `prices` field. There is no
migration path for existing clients reading `prices` off a product response; they must switch to
`GET /api/v1/product-prices?product={id}`.

`POST /api/v1/products` no longer creates any `ProductPrice` rows as a side effect. A newly created
product has zero prices until explicitly created via `POST /api/v1/product-prices`.
