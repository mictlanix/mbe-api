# Research: Unified Entity Status

No NEEDS CLARIFICATION markers remained in the Technical Context — all decisions were made
with the user before specification. This document records those decisions and the code-level
findings that ground them.

## Decision 1: Enum shape and storage

**Decision**: `EntityStatus(IntEnum)` with `ACTIVE = 0`, `INACTIVE = 1`, `ARCHIVED = 2`,
declared in `app/enums.py`; stored as an integer column
(`Mapped[EntityStatus] = mapped_column(Integer, default=EntityStatus.ACTIVE, server_default="0")`);
serialized as an integer in JSON.

**Rationale**:
- Matches the repo's only established enum pattern: `CurrencyCode(IntEnum)` typed as
  `Mapped[CurrencyCode] = mapped_column(Integer)` (e.g. `app/models/product.py`,
  `app/models/core.py`). No SQLAlchemy `Enum` or varchar-backed enum exists anywhere.
- `INACTIVE = 1` matches the dominant `disabled` polarity (9 of 14 legacy columns), so most
  backfills are a straight value copy with NULL→0 coalescing, minimizing migration risk.
- `ARCHIVED = 2` was explicitly requested as a third state, reserved for client use; it has no
  server-side behavior beyond "not ACTIVE" (blocks login like INACTIVE).
- Pydantic v2 serializes `IntEnum` members as integers natively; using the enum as the field
  type gives free validation (invalid ints → 422) on bodies and query params.

**Alternatives considered**:
- String enum ("active"/"inactive"/"archived") — self-documenting but breaks the codebase's
  integer-enum convention; rejected by user.
- Two-state enum — rejected by user in favor of including ARCHIVED now.
- `ACTIVE = 1` polarity — would invert 9 columns during backfill for cosmetic benefit; rejected.

## Decision 2: Hard breaking change, no deprecation aliases

**Decision**: Legacy fields (`disabled`, `active`, `deactivated`, `enabled`) are removed from
all Pydantic schemas and query parameters in one release. No dual-field period.

**Rationale**: mbe-ui is the only known client and is updated separately (issues #80/#81 are
filed from that project). Dual fields would require polarity-mapping code — the exact bug
surface this feature exists to eliminate.

**Alternatives considered**: Deprecation aliases via Pydantic `alias`/computed fields —
rejected as speculative complexity (Constitution I).

## Decision 3: Migration strategy (first Alembic revision)

**Decision**: One migration file with `down_revision = None` — `migrations/versions/` is
currently empty, so this is the repo's first revision. Per table: `op.add_column("status",
SMALLINT NOT NULL server_default "0")` → `op.execute(UPDATE ... SET status = CASE ...)` →
`op.drop_column(<legacy>)`. Downgrade reverses: re-add legacy column(s), backfill from status
(`INACTIVE`/`ARCHIVED` → "off" state), drop `status`.

**Rationale**:
- The async `migrations/env.py` already targets `Base.metadata`; nothing blocks a first revision.
- `server_default="0"` makes the column addition safe on populated tables and matches the model
  default (`EntityStatus.ACTIVE`), keeping future autogenerate diffs quiet.
- Raw-SQL `UPDATE ... CASE` backfill avoids loading ORM models inside the migration (standard
  Alembic practice; models will already describe the *new* shape).

**Backfill mapping** (per legacy polarity):
- `disabled`-polarity (user, customer, address, facility, warehouse, point_sale, cash_drawer,
  employee.disabled): `NULL/0 → 0 (ACTIVE)`, `non-zero → 1 (INACTIVE)`.
- `active`/`enabled`-polarity (employee.active, vehicle, vehicle_operator,
  taxpayer_certificate, payment_method_option): `1 → 0 (ACTIVE)`, `0/NULL → 1 (INACTIVE)`.
- `employee` (both flags): `status = 0` iff `active = 1 AND (disabled IS NULL OR disabled = 0)`,
  else `1` — the restrictive flag wins.

**Alternatives considered**: Renaming columns in place with type change — MariaDB `CHANGE
COLUMN` cannot express the polarity inversion or the two-column employee collapse; rejected.

## Decision 4: Filter parameter

**Decision**: Every API-exposed status-bearing list endpoint accepts
`status: EntityStatus | None = Query(None)`; services add `.where(<Model>.status == status)`
when provided, mirroring the existing filter pattern in `customer_service.list_customers`
(applied to both the page query and the count query). Legacy `?deactivated` (products),
`?disabled` (customers), `?active` (employees) parameters are deleted.

**Rationale**: Uniform filter name/semantics is the client-facing payoff of the feature (issue
#81's "Active" filter chip). FastAPI validates enum query params automatically → invalid values
422. Applied per user decision to *all* exposed entities, not just the three that filter today.

## Decision 5: Login gate

**Decision**: `user_service.authenticate_user` rejects any user whose
`status != EntityStatus.ACTIVE` (was: `user.disabled` truthy).

**Rationale**: Preserves today's disabled-user rejection (FR-005) and gives ARCHIVED the same
safe default. It remains the codebase's only behavioral status check.

## Code-level findings (verified against current tree)

- 13 tables carry legacy flags; `employee` carries two. `warehouse.disabled` is SmallInteger
  (not Boolean) with `int()` casts in `warehouse_service.py` — these disappear with the column.
- `address` and `taxpayer_certificate` have no Pydantic schemas or endpoints → model + migration
  changes only.
- `facility` is the current name of the former store entity (commit da6cbaa); schema classes are
  `FacilityCreate/Update/Summary/Response` in `app/schemas/core.py`; tests still live in
  `tests/api/test_stores.py`.
- Only `user.disabled` has `default=False, server_default="0"` today; all other legacy columns
  are default-less and mostly nullable — the new column is uniformly non-nullable with
  `default=EntityStatus.ACTIVE, server_default="0"`.
- `ProductCreate` does not expose the legacy flag (`product_service.create_product` hardcodes
  `deactivated=False`); the new shape keeps `status` out of `ProductCreate` and defaults
  server-side to ACTIVE, preserving the existing contract shape.
