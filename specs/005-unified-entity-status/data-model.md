# Data Model: Unified Entity Status

## EntityStatus enum (`app/enums.py`)

```python
class EntityStatus(IntEnum):
    ACTIVE = 0      # in normal use (default for new rows)
    INACTIVE = 1    # temporarily out of use / disabled
    ARCHIVED = 2    # retained for history; never set automatically
```

- JSON representation: integer (0/1/2), consistent with `CurrencyCode`.
- Validation: Pydantic rejects out-of-range values on bodies; FastAPI rejects them on query
  params (422).
- No state-transition rules: any status may be set to any other via update endpoints.
- Behavior: `authenticate_user` requires `status == ACTIVE`. Nothing else branches on status.

## Column pattern (all models)

```python
status: Mapped[EntityStatus] = mapped_column(
    Integer, default=EntityStatus.ACTIVE, server_default='0'
)
```

Non-nullable everywhere — the `bool | None` sprawl and SmallInteger casts are eliminated.

## Affected entities

### API-exposed (model + schemas + service + endpoint filter)

| Entity | Table | Model file | Legacy field(s) | Backfill → status |
|---|---|---|---|---|
| User | `user` | app/models/user.py | `disabled` (bool, NOT NULL, default 0) | `disabled` ≠ 0 → INACTIVE else ACTIVE |
| Customer | `customer` | app/models/customer.py | `disabled` (bool, NULL) | NULL/0 → ACTIVE, else INACTIVE |
| Product | `product` | app/models/product.py | `deactivated` (bool) | NULL/0 → ACTIVE, else INACTIVE |
| Employee | `employee` | app/models/core.py | `active` (bool) **and** `disabled` (bool, NULL) | ACTIVE iff `active=1 AND (disabled IS NULL OR disabled=0)`, else INACTIVE; both columns dropped |
| Facility | `facility` | app/models/core.py | `disabled` (bool, NULL) | NULL/0 → ACTIVE, else INACTIVE |
| Warehouse | `warehouse` | app/models/core.py | `disabled` (smallint, NULL) | NULL/0 → ACTIVE, else INACTIVE |
| PointSale | `point_sale` | app/models/core.py | `disabled` (bool, NULL) | NULL/0 → ACTIVE, else INACTIVE |
| CashDrawer | `cash_drawer` | app/models/core.py | `disabled` (bool, NULL) | NULL/0 → ACTIVE, else INACTIVE |
| PaymentMethodOption | `payment_method_option` | app/models/core.py | `enabled` (bool) | 1 → ACTIVE, 0/NULL → INACTIVE |
| Vehicle | `vehicle` | app/models/core.py | `active` (bool) | 1 → ACTIVE, 0/NULL → INACTIVE |
| VehicleOperator | `vehicle_operator` | app/models/core.py | `active` (bool) | 1 → ACTIVE, 0/NULL → INACTIVE |

### Persistence-only (model + migration; no schemas/endpoints exist)

| Entity | Table | Model file | Legacy field | Backfill → status |
|---|---|---|---|---|
| Address | `address` | app/models/core.py | `disabled` (bool, NULL) | NULL/0 → ACTIVE, else INACTIVE |
| TaxpayerCertificate | `taxpayer_certificate` | app/models/fiscal.py | `active` (bool) | 1 → ACTIVE, 0/NULL → INACTIVE |

## Schema field changes (`app/schemas/`)

Pattern per entity (replacing the legacy field in place):

| Schema kind | New field |
|---|---|
| `*Create` | `status: EntityStatus = EntityStatus.ACTIVE` |
| `*Update` | `status: EntityStatus \| None = None` (partial update: None = unchanged) |
| `*Response` / `*ListItem` / `*Summary` | `status: EntityStatus` |

Applies to: `UserCreate/Update/ListItem/Response` (user.py), `CustomerCreate/ListItem/Response`
(customer.py), `ProductUpdate/ListItem/Response` (product.py — `ProductCreate` continues to not
expose the field; server defaults to ACTIVE), `EmployeeCreate/Update/Response` (single `status`
replaces both `active` and `disabled`), `FacilityCreate/Update/Summary/Response`,
`WarehouseCreate/Update/Summary/Response`, `PointSaleCreate/Update/Response`,
`CashDrawerCreate/Update/Response`, `PaymentMethodOptionCreate/Update/Response`,
`VehicleCreate/Update/Response`, `VehicleOperatorCreate/Update/Response` (all core.py).

## Migration (first Alembic revision, `down_revision = None`)

Upgrade, per table above:
1. `op.add_column(<table>, sa.Column('status', sa.SmallInteger(), nullable=False, server_default='0'))`
2. `op.execute('UPDATE <table> SET status = <CASE per backfill column>')`
3. `op.drop_column(<table>, <legacy column>)` (×2 for `employee`)

Downgrade, per table: re-add legacy column(s) with original type/nullability, backfill
(`status = 0` → "on" state; `status IN (1, 2)` → "off" state; employee: `active = (status = 0)`,
`disabled = (status <> 0)`), drop `status`.
