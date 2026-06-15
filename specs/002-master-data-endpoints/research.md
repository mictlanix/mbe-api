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

## Unresolved Items

None. All NEEDS CLARIFICATION markers resolved.
