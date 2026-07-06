# Research: Master Data REST Endpoints

**Date**: 2026-06-14

---

## Decision 1: ORM Model Reuse — No Changes Required

**Decision**: All 17 SQLAlchemy models exist in `app/models/`. Zero schema changes needed.

**Model locations**:

| Resource | Model Class | File |
|----------|-------------|------|
| Product | `Product`, `ProductPrice` | `app/models/product.py` |
| PriceList | `PriceList` | `app/models/product.py` |
| Label | `Label` | `app/models/core.py` |
| Customer | `Customer`, `CustomerDiscount` | `app/models/customer.py` |
| TaxpayerRecipient | `TaxpayerRecipient` | `app/models/customer.py` |
| Supplier | `Supplier` | `app/models/supplier.py` |
| Employee | `Employee` | `app/models/core.py` |
| Store | `Store` | `app/models/core.py` |
| Warehouse | `Warehouse` | `app/models/core.py` |
| PointSale | `PointSale` | `app/models/core.py` |
| CashDrawer | `CashDrawer` | `app/models/core.py` |
| ExchangeRate | `ExchangeRate` | `app/models/core.py` |
| Expense | `Expense` | `app/models/core.py` |
| PaymentMethodOption | `PaymentMethodOption` | `app/models/core.py` |
| Vehicle | `Vehicle` | `app/models/core.py` |
| VehicleOperator | `VehicleOperator` | `app/models/core.py` |
| ProductionSite | `ProductionSite` | `app/models/core.py` |

**Rationale**: Constitution Principle V (Reuse Over Rebuild). Models are battle-tested against
the real MariaDB schema.

**Alternatives considered**: Adding SQLAlchemy relationships (`relationship()`) for eager loading.
**Rejected because**: All current query patterns use explicit `select()` statements; adding
relationships would be speculative infrastructure not required by any FR.

---

## Decision 2: `stock_verification` ↔ `stock_required` Field Name Resolution

**Decision**: The ORM field `Product.stock_verification` is exposed in the API as `stock_required`
using a Pydantic field alias. The ORM column is NOT renamed.

**Rationale**: Renaming the ORM column would require a database migration and touches transactional
code outside this feature's scope. A Pydantic alias (`Field(alias="stock_required")` with
`model_config = ConfigDict(populate_by_name=True)`) resolves the mismatch at the API boundary
with zero DB impact.

**Alternatives considered**: Alembic migration to rename the column. **Rejected because**: out
of scope; surgical-changes principle prohibits touching unrelated DB columns.

---

## Decision 3: Config Defaults for Product Creation Auto-Values

**Decision**: Add five new optional fields to `app/core/config.py` `Settings`:

```python
default_vat: Decimal = Decimal("0.160000")          # 16% Mexican VAT
is_tax_included: bool = False
default_price_type: int = 0                           # 0 = Fixed
default_photo_file: str = "no-image.png"
default_customer_id: int = 1
```

**Rationale**: These replace the legacy `WebConfig` static values. Storing them in `Settings`
(pydantic-settings) allows env-var override without code changes.

**Alternatives considered**: Hard-coding the values inline in `product_service.py`. **Rejected
because**: would prevent per-environment override (e.g., different VAT rates).

---

## Decision 4: Authentication Dependency per Endpoint

**Decision**: All 85+ handlers use `get_current_user` (authenticated, any role). The product
merge endpoint (`POST /products/merge`) additionally checks that the caller holds
`AccessRight.CREATE` on `SystemObject.PRODUCTS_MERGE (73)`. A new helper
`require_privilege(system_object, access_right)` is added to `app/core/deps.py`.

**Rationale**: FR-027 says `get_current_user`, not `require_admin`. Requiring admin for all
master data would lock out regular users who legitimately manage catalogs.

**Alternatives considered**:
- `require_admin` for all endpoints → overly restrictive; regular staff need to manage products.
- Inline privilege check in merge handler → workable but not reusable.
- Per-resource privilege dependency → chosen approach; thin wrapper, no new abstraction beyond
  what's needed.

---

## Decision 5: Schema File Grouping

**Decision**: Four schema files mirroring the model files:

- `app/schemas/product.py` — Product, PriceList, ProductPrice (3 classes × ~4 schemas each)
- `app/schemas/customer.py` — Customer, TaxpayerRecipient
- `app/schemas/supplier.py` — Supplier
- `app/schemas/core.py` — Employee, Store, Warehouse, PointSale, CashDrawer, Label,
  ExchangeRate, Expense, PaymentMethodOption, Vehicle, VehicleOperator, ProductionSite

**Rationale**: Follows the model file grouping. `core.py` is large but all those models are
simple CRUD with no cross-references between them in schema space.

**Alternatives considered**: One schema file per resource (17 files). **Rejected because**: the
simpler resources (Label, Expense, Vehicle) have 2–3 fields each; a dedicated file per resource
is overkill for trivial entities.

---

## Decision 6: Product Delete Cascade

**Decision**: On `DELETE /products/{id}`, the service explicitly deletes `ProductPrice` rows
before deleting the `Product`. No ORM `cascade` is added.

**Rationale**: ORM cascade would be a model change (surgical-changes principle prohibits it).
Explicit delete in the service is 2 lines and matches how the reference implementation works.

---

## Decision 7: Product Merge Implementation

**Decision**: The merge service will use individual ORM `update()` statements per table (not raw
SQL) to remap FK references from duplicate → canonical.

The 9 tables that reference `product_id` that we know of from the spec:
`sales_order_detail`, `purchase_order_detail`, `inventory_receipt_detail`,
`inventory_issue_detail`, `inventory_transfer_detail`, `product_price`, `lot_serial_tracking`,
`product_label`, and others. The implementation will use SQLAlchemy `update()` with
`where(column == duplicate_id)` and `values(column=product_id)` per table.

**Rationale**: ORM bulk updates are safer and more readable than raw SQL; they also play nicely
with the async session.

---

## Decision 8: VehicleOperator License Expiry Advisory

**Decision**: The response schema for `VehicleOperator` includes a computed `days_until_expiry`
field (negative = already expired). This is computed in the Pydantic schema `@model_validator`,
not stored in the DB.

**Rationale**: FR-025 requires an advisory flag. A computed field avoids any DB change.

---

## Decision 9: Pagination Envelope

**Decision**: All list endpoints return `{"items": [...], "total": N}` using a generic Pydantic
model `ListResponse[T]` defined once in `app/schemas/__init__.py` (or inline in `app/schemas/core.py`).

**Rationale**: All 17 list endpoints need the same wrapper. A single generic avoids 17 duplicate
classes. This is the one abstraction justified by 17-fold reuse.

---

## CHANGELOG Update

**Decision**: `CHANGELOG.md` `[Unreleased]` section must be updated with an `Added` entry for
all 17 resources before or at the PR.

---

---

## Decision 10: FK Filter Parameters on List Endpoints

**Decision**: Add optional integer query parameters directly to the affected list handlers — no
new abstraction. Five endpoints receive new filter params:

| Endpoint | New param(s) | Model field |
|----------|-------------|-------------|
| `GET /api/v1/products` | `supplier` | `Product.supplier` |
| `GET /api/v1/customers` | `price_list`, `salesperson` | `Customer.price_list`, `Customer.salesperson` |
| `GET /api/v1/points-of-sale` | `store`, `warehouse` | `PointSale.store`, `PointSale.warehouse` |
| `GET /api/v1/cash-drawers` | `store` | `CashDrawer.store` |
| `GET /api/v1/vehicle-operators` | `employee` | `VehicleOperator.driver` |

**Rationale**: Pattern is identical to the existing `label` filter on products and `store` filter
on warehouses — `Query(None)` parameter, `where(Model.field == value)` clause added only when the
param is not `None`. No new service abstraction needed.

**Edge case**: A filter value referencing a non-existent ID returns an empty list (not 404) —
consistent with search returning no results (spec SC-006).

---

## Decision 11: SAT Catalog Endpoints

**Decision**: All 8 SAT catalog read-only endpoints are implemented in a single file
`app/api/v1/endpoints/sat_catalogs.py` with a single service module
`app/services/sat_catalog_service.py`. The router is mounted at `/api/v1/sat`.

**Models used** (all from `app/models/sat_catalog.py`):
`SatCfdiUsage`, `SatCountry`, `SatCurrency`, `SatPostalCode`, `SatProductService`,
`SatReasonCancellation`, `SatTaxRegime`, `SatUnitOfMeasurement`.

**Schema approach**: Each catalog gets a minimal response schema with only its PK (string).
Since the current model only has the PK column, the schema is just `{ id: str }`. If additional
descriptive fields exist in the DB they can be added later — surgical changes principle applies.

**Route pattern**:
- `GET /api/v1/sat/cfdi-usages` → paginated list
- `GET /api/v1/sat/cfdi-usages/{id}` → single record or 404
(Repeated for all 8 catalogs.)

**Write operations**: No `POST`, `PUT`, or `DELETE` routes are registered — 405 is returned by
FastAPI automatically for unregistered methods. No explicit rejection handler needed.

**Rationale**: Consolidating all 8 into one file avoids 8 near-identical tiny files. The service
is parameterized by model class (a dict lookup), keeping the code DRY without overengineering.

---

## Decision 12: Multi-Label Product Search (AND semantics)

**Decision**: `label` on `GET /api/v1/products` becomes a repeatable query parameter
(`list[int] | None`, via FastAPI `Query(None)`, e.g. `?label=2&label=5`). When multiple values are
given, a product must be associated with **every** one (AND/intersection), not just one (OR/union).

**Query approach**: `Product.product_id.in_(select(product_label.c.product).where(product_label.c.label.in_(labels)).group_by(product_label.c.product).having(func.count(func.distinct(product_label.c.label)) == len(labels)))`
— group the junction rows by product and require the distinct-label count to equal the number of
requested labels. This generalizes the existing single-label subquery (a single-element list
degenerates to the same `IN` behavior) without adding a new abstraction.

**Rationale**: AND semantics and the repeated-query-param format were confirmed as explicit product
decisions (not defaults) when refining the spec — see spec.md Assumptions. Repeated params match
the convention FastAPI/Starlette already uses for list-valued query params, and require no custom
parsing (as a comma-separated value would).

**Edge case**: If any requested label doesn't exist, the intersection can never be satisfied, so
the endpoint returns an empty list — consistent with all other filter edge cases (SC-006, SC-009).

---

## Unresolved Items

None. All NEEDS CLARIFICATION markers resolved.
