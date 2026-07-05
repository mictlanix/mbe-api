# Research: Price Management Service

No `[NEEDS CLARIFICATION]` markers remained from the spec (scope, response shape, and
auto-creation behavior were pre-agreed with the user). This document records the concrete design
decisions needed to turn the spec into an implementable plan.

## Decision: Endpoint shape — top-level `/product-prices`, not nested under `/products`

**Rationale**: The spec's core value (User Story 3) is that products stay completely unaware of
pricing. Nesting the new routes under `/products/{id}/prices` would still couple the URL space (and
likely the router file) to products. A top-level resource — `product_price_id` as the path key,
`product` and `price_list` as optional query filters — mirrors the existing `/price-lists` module
exactly and keeps the two domains structurally independent.

**Alternatives considered**: `/products/{product_id}/prices` — rejected; keeps a product-shaped URL
even though the resource and its lifecycle no longer belong to the product endpoints.

## Decision: Permission — reuse `SystemObject.PRICING`

**Rationale**: `app/enums.py:122` already defines `PRICING = 106` and it is not referenced by any
existing endpoint. This is exactly the "reuse over rebuild" case the constitution calls for —
adding a new `SystemObject` member would duplicate an already-provisioned one.

**Alternatives considered**: `SystemObject.PRICE_LISTS` — rejected; that enum member is already the
identity of the separate, untouched `/price-lists` module, and reusing it would conflate two
independently-permissioned capabilities. A brand-new enum member — rejected; unnecessary given an
unused, semantically-correct one already exists.

## Decision: Duplicate prevention (FR-005) is an application-level check, not a DB constraint

**Rationale**: The plan's constraint list rules out a migration. `product_price_service.create_price`
must query for an existing row with the same `(product, price_list)` pair before inserting, raising
a 409 Conflict if found — the same pattern `product_service._check_code_unique` already uses for
product codes.

**Alternatives considered**: Add a unique DB constraint on `(product, price)` columns — rejected for
this feature; would require a migration, which is out of scope, and the constitution's "no DB
migration" constraint here is intentional (this is a logic-relocation feature, not a schema change).

## Decision: Product-side cleanup delegates to the new service via a plain function call

**Rationale**: `product_service.delete_product` and `product_service.merge_products` still need
`ProductPrice` rows cleaned up (FK integrity) when a product is deleted or merged away, but must not
import the `ProductPrice` model themselves (spec FR-009). `product_price_service` exposes a
`delete_for_product(db, product_id)` function; `product_service` calls it and never touches the
`product_price` table or model directly. This is a same-process function call (not an HTTP call) —
both modules run in the same FastAPI app and share the same `AsyncSession`/transaction.

**Alternatives considered**: DB-level `ON DELETE CASCADE` — rejected, requires a migration (out of
scope). Keeping the raw `DELETE FROM product_price` inline in `product_service` — rejected, directly
contradicts FR-009 ("product endpoints/service MUST NOT reference ProductPrice").

## Decision: `ProductPriceResponse` moves to a new `app/schemas/product_price.py`; `PriceListResponse` stays put

**Rationale**: `app/schemas/customer.py` imports `PriceListResponse` from `app.schemas.product`
today. The spec explicitly keeps `PriceList` management out of scope, so relocating
`PriceListResponse` would be an unrelated, unrequested refactor touching a file (`customer.py`) this
feature has no reason to touch. Only `ProductPriceResponse` (plus new `Create`/`Update` schemas,
which don't exist yet) move to the new module, importing `PriceListResponse` from
`app.schemas.product` the same way `customer.py` already does.

**Alternatives considered**: Move all price-related schemas (`PriceList*` + `ProductPrice*`) into
the new module — rejected; would force an edit to `customer.py`'s import path for no functional
reason, violating Surgical Changes.
