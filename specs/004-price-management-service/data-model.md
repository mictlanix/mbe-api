# Data Model: Price Management Service

## Existing Entities (unchanged)

### `ProductPrice` (`app/models/product.py:62-71`)

No schema/migration changes. This table is being re-owned by a new service, not altered.

| Column | Type | Notes |
|--------|------|-------|
| `product_price_id` | `int` (PK) | Existing |
| `product` | `int` (FK → `product.product_id`) | Existing |
| `price_list` | `int` (FK → `price_list.price_list_id`, DB column named `list`) | Existing |
| `price` | `Decimal(18,4)` | Existing |
| `low_profit` | `Decimal(20,6)` | Existing |
| `high_profit` | `Decimal(20,6)` | Existing |

### `PriceList` (`app/models/product.py:18-24`) — referenced, not modified

| Column | Type | Notes |
|--------|------|-------|
| `price_list_id` | `int` (PK) | Existing |
| `name` | `str` | Existing |
| `high_profit_margin` | `Decimal(5,4)` | Existing |
| `low_profit_margin` | `Decimal(5,4)` | Existing |

### `Product` (`app/models/product.py:27-59`) — referenced, not modified

No columns added or removed. `tax_rate`, `tax_included`, `price_type`, `currency` remain on the
model — they are pricing-*adjacent* product attributes, explicitly out of scope (spec Assumptions).

## New Schemas (`app/schemas/product_price.py`)

### `ProductPriceCreate`

| Field | Type | Notes |
|-------|------|-------|
| `product` | `int` | Must reference an existing product |
| `price_list` | `int` | Must reference an existing price list |
| `price` | `Decimal` | ≥ 0 |
| `low_profit` | `Decimal` | ≥ 0 |
| `high_profit` | `Decimal` | ≥ 0 |

### `ProductPriceUpdate`

| Field | Type | Notes |
|-------|------|-------|
| `price` | `Decimal \| None` | ≥ 0 if provided |
| `low_profit` | `Decimal \| None` | ≥ 0 if provided |
| `high_profit` | `Decimal \| None` | ≥ 0 if provided |

`product` and `price_list` are immutable after creation — not present on `Update`, matching the
pattern of other FK-bearing update schemas in this codebase (a price is recreated under a
different product/price-list pair rather than moved).

### `ProductPriceResponse`

| Field | Type | Notes |
|-------|------|-------|
| `product_price_id` | `int` | |
| `product` | `int` | |
| `price_list` | `PriceListResponse` | Resolved object (name, margins) — imported from `app.schemas.product`, unchanged |
| `price` | `Decimal` | |
| `low_profit` | `Decimal` | |
| `high_profit` | `Decimal` | |

This matches the existing `ProductPriceResponse` shape (`app/schemas/product.py:52-59`) except it
now also surfaces the `product` id directly (needed since the entry is no longer only ever seen
nested inside a `ProductResponse`).

## Removed From `app/schemas/product.py`

- `ProductPriceResponse` class — moves to `app/schemas/product_price.py`.
- `ProductResponse.prices: list[ProductPriceResponse] = []` field — removed entirely (spec FR-009).

`PriceListCreate`, `PriceListUpdate`, `PriceListResponse` are **not** touched — `customer.py` keeps
importing `PriceListResponse` from `app.schemas.product` unchanged.

## Validation Rules

| Rule | Enforcement |
|------|-------------|
| `product` must exist | 404 if not found, checked in service before insert |
| `price_list` must exist | 404 if not found, checked in service before insert |
| One entry per `(product, price_list)` pair | 409 Conflict if a row already exists for the pair (application-level check — see research.md) |
| `price` / `low_profit` / `high_profit` ≥ 0 | Pydantic `Field(ge=0)` on Create/Update schemas |
| Caller must be authenticated with `PRICING` privilege | 401 if unauthenticated, 403 if privilege missing |

## Lifecycle Coupling (product ↔ price, without a direct reference)

| Product event | Price-side effect | Owned by |
|----------------|-------------------|----------|
| Product created | No price rows created (removed behavior) | `product_service.create_product` — simply stops calling into pricing |
| Product deleted | All of that product's `ProductPrice` rows removed | `product_price_service.delete_for_product(db, product_id)`, called from `product_service.delete_product` |
| Products merged | Duplicate's `ProductPrice` rows removed; canonical's rows untouched | `product_price_service.delete_for_product(db, duplicate_id)`, called from `product_service.merge_products` |
