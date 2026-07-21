# Phase 0 Research: Constraint Violation Handling

All unknowns were resolved by measuring the live `mbe_demo` database rather than by reasoning
about the schema, because the first two measurements both contradicted the assumption they
were meant to confirm.

## R1 — Is the exposure real, or theoretical?

**Decision**: Real, and larger than expected. Guards are warranted on 17 delete endpoints.

**Rationale**: `mbe_demo` is InnoDB with 209 foreign key constraints — enforced, not
decorative. 14 of 20 delete endpoints target a table with inbound references and had no
guard. Counted against live rows: 14 facilities referenced by a warehouse, 15 warehouses by a
point of sale, 12 employees by a vehicle operator, 210 suppliers by a product, 14 addresses
by a facility. Every one of those deletes returned a 500 before this change.

**Alternatives considered**: Treating it as theoretical and fixing only reported cases —
rejected once the counts showed the common path was affected, not an edge.

**False start worth recording**: the first audit query reported *zero* inbound foreign keys
for every table, which would have meant the schema had no referential enforcement at all and
that deletes silently orphaned rows — a different and worse problem. The query was wrong:
SQLAlchemy 2.x deprecates `Row.t`, which silently returned the whole row instead of the
column named `t`, so every dictionary lookup missed. The contradiction was caught only
because "no foreign keys anywhere" was inconsistent with the delete guard written for #100
having been necessary. Measurements that confirm an absence deserve a second look.

## R2 — Where should referencing tables come from?

**Decision**: Derive from `Base.metadata` at call time.

**Rationale**: 97 of the database's 107 tables are mapped, so metadata covers the great
majority, and coverage improves automatically as models are added. FR-007 requires that no
per-entity list be hand-maintained; metadata is the only source already guaranteed to match
the models the rest of the app uses.

**Alternatives considered**:
- *A hand-written list per entity* — rejected by FR-007, and empirically unreliable: the
  pre-existing `price_list` guard listed one of its two referencing tables.
- *Querying `information_schema` at runtime* — accurate for unmapped tables too, but binds
  the guard to MariaDB and adds a metadata round trip per delete. The `IntegrityError`
  backstop already covers unmapped tables, so the extra accuracy buys only a better message
  for a shrinking minority.

**Consequence accepted**: unmapped legacy tables block a delete via the generic 409 rather
than a named one. Recorded in the spec Assumptions as a deliberate trade.

## R3 — One query or one per referencing table?

**Decision**: A single `UNION ALL` of per-reference counts.

**Rationale**: `employee` has 26 mapped inbound references. One query per reference would be
26 round trips on a single delete; a union is one. Deletes are rare and administrator-driven,
so a single extra query is affordable where 26 would be conspicuous.

## R4 — How should a blocker be labelled?

**Decision**: `table.column`, not `table`.

**Rationale**: Discovered by running the guard against live data. `inventory_transfer`
references `warehouse` through two columns, and `cash_session` references `employee` through
two, so table-only labelling produced `inventory_transfer (2769), inventory_transfer (746)` —
the same name twice with different counts, from which a client can determine nothing. With
column labels it reads `inventory_transfer.warehouse_to (2769)` and
`inventory_transfer.warehouse (746)`.

**Alternatives considered**: Aggregating per table by summing — rejected as actively
misleading, since a row referenced through both columns would be counted twice, and the total
would name no relation the client could act on.

## R5 — Does labelling by `table.column` violate the non-disclosure rule?

**Decision**: No, and the distinction is deliberate.

**Rationale**: FR-011 forbids leaking internal detail in *generic* conflicts, where the
driver message names indexes, constraint names and SQL fragments that serve no client. The
guard's labels are different in kind: they are the actionable payload FR-003 requires, chosen
deliberately and stable across releases. The driver's own message is logged, never returned.

## R6 — Is the `user` delete still possible once its cascade is exempted?

**Decision**: Yes, and always — worth an explicit test.

**Rationale**: `user_settings` and `access_privilege` are the *only* tables referencing
`user`. Exempting both leaves no reference to check, so the guard short-circuits before
issuing any query and a user is unconditionally deletable. Surfaced by a test that failed
while asserting a query had run. This is the fact that reversed the earlier decision to
remove the cascades: without the exemption, `DELETE /users/{id}` would return 409 forever,
since neither table is reachable through any endpoint.

## R7 — Which unique constraints are unguarded?

**Decision**: Pre-check four; leave the rest to the backstop.

**Rationale**: Six unique constraints exist. `product.code` and
`exchange_rate(date, base, target)` already had service-level checks returning a clean 409.
The remaining four — `warehouse.code`, `point_sale.code`, `cash_drawer.code`,
`vehicle.license_plate` — had none, so a duplicate code returned a 500 on the single most
likely mistake a create form can produce. Adding pre-checks matches the pattern already
established by `product._check_code_unique`.
