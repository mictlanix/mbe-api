# Phase 1 Data Model: Constraint Violation Handling

**No schema change.** This feature adds no table, column, index or migration. It reads
relationships the data model already declares and reports them to the client.

## Entities

### Blocking reference (derived, not persisted)

A relationship through which some record still points at the row being deleted, paired with
how many records currently use it.

| Field | Type | Meaning |
|-------|------|---------|
| `reference` | `str` | `table.column` — the referencing table and the specific column that points at the target |
| `rows` | `int` | Count of referencing records, always > 0 in a reported blocker |

Derived at request time from `Base.metadata`: every mapped table's foreign keys are scanned
for one whose target is the primary key column of the instance being deleted.

**Why the column and not just the table**: a table may reference the same target more than
once (`inventory_transfer` has both an origin and a destination warehouse; `cash_session`
records both a cashier and a supervisor employee). Reporting the table alone yields two
entries with the same name and different counts, satisfying neither FR-003 nor FR-004.

**Ordering**: descending by `rows`, then by name for stability. Reported blockers are capped
at five, with any remainder summarised as a count (FR-005).

### Owned child (configuration, not data)

A table whose rows the delete legitimately removes along with the parent, and which therefore
must not count as a blocker. Expressed as an `exempt` set passed by the calling service.

| Parent | Exempted tables | Why |
|--------|-----------------|-----|
| `product` | `product_price` | Prices exist only for their product. Independently manageable via `/product-prices`, so a client *may* clear them first, but is not required to. |
| `user` | `user_settings`, `access_privilege` | No lifecycle of their own, and unreachable through any endpoint — without the exemption no user could ever be deleted (see research R6). |

This list is closed. Any addition is a product decision, not an implementation choice.

## Relationships consulted

Not new relationships — the existing ones, read in the opposite direction from usual. The
codebase already resolves foreign keys *outward* (`fk_expansion.batch_fetch`, embedding a
facility into a warehouse response). This feature reads them *inward*: given a row, which
tables point at it.

Coverage: 97 of 107 database tables are mapped, and mapped tables are the ones scanned. The
109 unmapped-table references fall through to the generic conflict (see research R2).

## Uniqueness rules surfaced

Existing database constraints, not new ones. Listed because the feature promotes four of them
from an unhandled failure to a named conflict.

| Table | Constraint | Previously | Now |
|-------|-----------|------------|-----|
| `warehouse` | `code` unique | unhandled → server fault | named conflict |
| `point_sale` | `code` unique | unhandled → server fault | named conflict |
| `cash_drawer` | `code` unique | unhandled → server fault | named conflict |
| `vehicle` | `license_plate` unique | unhandled → server fault | named conflict |
| `product` | `(code, bar_code)` unique | already pre-checked | unchanged |
| `exchange_rate` | `(date, base, target)` unique | already pre-checked | unchanged |

On update, the row being edited is excluded from the duplicate search by primary key, so
re-saving a record without changing its code is never a conflict with itself (FR-009).

## State transitions

None introduced. The lifecycle `status` field (`ACTIVE` / `INACTIVE` / `ARCHIVED`, feature
005) is unchanged and remains client-driven. This feature explicitly does *not* transition a
record to `ARCHIVED` when a delete is refused — that is the operator's decision to make
(FR-015).
