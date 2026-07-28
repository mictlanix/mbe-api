# Phase 1 — Data Model: One In-Transit Location per Facility

One column added, one row converted, thirteen rows inserted, one enum value added, one setting
removed. No new table, no new model, no new schema module.

---

## Schema change

### `warehouse` — one new column

| Column | Change | Notes |
|---|---|---|
| `in_transit` | **new** `TINYINT(1) NOT NULL DEFAULT 0` | `1` marks a system-owned in-transit location. The default is what makes the column invisible to the legacy application, which keeps writing ordinary warehouses without knowing it exists (research R3) |

Everything else about the table is untouched — `code_UNIQUE` still applies, the
`warehouse_facility_fk` foreign key still says `ON DELETE NO ACTION`, and no index is added. The
table holds 32 rows after the migration; every predicate below is a scan of a table that fits in a
single page.

### `app/models/core.py`

```python
class Warehouse(Base):
    ...
    in_transit: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0')
```

Placed after `status`, matching the column order of the migration. `Boolean` over `TINYINT(1)` is
the mapping this repository already uses for `completed` / `cancelled` in
`app/models/inventory.py`.

---

## The in-transit rows

One per facility, created by the system and never by a user.

| Field | Value | Why |
|---|---|---|
| `facility` | the facility that owns it | The whole point of the feature — this is the org-chart edge that was previously a lie |
| `code` | `IN-TRANSIT-{facility_id}` | Deterministic and globally unique, so `code_UNIQUE` holds across fourteen of them. Keyed on `facility_id` rather than `facility.code` because facility codes are editable and would strand the warehouse code on rename |
| `name` | `In Transit` | Unchanged from what migration 008 seeded. Rows are told apart by `facility`, which every reader of them already has |
| `comment` | `Virtual location holding goods between itinerary departure and delivery (migration 011)` | Matches the sentence 008 wrote, with its own migration number |
| `status` | `EntityStatus.ACTIVE` (`0`) | As 008 seeded it. The location must work even for an `INACTIVE` facility, so that deactivating one cannot strand goods already on a truck |
| `in_transit` | `1` | The flag |

**Not a schema constraint.** "Exactly one per facility" is enforced by the application at creation
and asserted by the migration — see research R3 for why a generated column and unique index were
not worth it.

---

## Lookups

Three predicates replace one configured id. All three are on `warehouse` alone.

### Facility → its in-transit location

Used by departure and stop closure, resolved once per operation for every dispatch warehouse on the
trip (research R2):

```sql
SELECT w.warehouse_id AS dispatch, t.warehouse_id AS transit
FROM warehouse w
JOIN warehouse t ON t.facility = w.facility AND t.in_transit = 1
WHERE w.warehouse_id IN (:dispatch_warehouse_ids)
```

A dispatch warehouse missing from the result means its facility has no in-transit location →
`422 Facility {id} has no in-transit location` (FR-009). The map is never partially applied: the
check runs before the first `post_movement`, so a departure either posts every line or none.

### Is this row an in-transit location?

`warehouse.in_transit` — a single boolean on a row already loaded. Read at the **API boundary**, not
in the service: one `_addressable()` helper in `app/api/v1/endpoints/warehouses.py` answers
`404 Warehouse not found` when the row is missing and
`403 In-transit locations are managed by the system` when it is in-transit, and GET, PUT and DELETE
all resolve through it (research R4, FR-013a).

`warehouse_service.get_warehouse` is **deliberately left unfiltered** — it still returns the row. A
service function that pretends a row does not exist is a trap for every future caller; the filtering
belongs where there is an HTTP answer to give.

### Which warehouses may be chosen?

`in_transit = 0`, applied at every selection surface:

| Call site | Today | After |
|---|---|---|
| `warehouse_service.list_warehouses` ([:42](../../app/services/warehouse_service.py#L42)) | `warehouse_id != settings.in_transit_warehouse_id` | `Warehouse.in_transit.is_(False)` |
| `warehouse_service.get_warehouse` ([:66](../../app/services/warehouse_service.py#L66)) | *no exclusion* | **unchanged** — the guard lives in the endpoint helper above |
| `warehouses.py` GET/PUT/DELETE by id | *no exclusion* — inconsistent with the listing | `403` via `_addressable()`; `404` reserved for ids naming nothing |
| `sales_order_service` product lookup ([:927](../../app/services/sales_order_service.py#L927)) | `warehouse_id != settings.in_transit_warehouse_id` | `Warehouse.in_transit.is_(False)` |
| `delivery_order_service._fallback_warehouse` ([:115](../../app/services/delivery_order_service.py#L115)) | **no exclusion at all** — `MIN(warehouse_id)` in the facility | `+ Warehouse.in_transit.is_(False)` |

The fallback row is a live defect, not a port: it can already hand a delivery line the in-transit
warehouse as its dispatch warehouse. It is fixed here because FR-012 names it.

---

## Movement, unchanged in shape

Departure and closure still post the same two-step move through `stock_ledger.post_movement`. Only
the second argument changes, from one configured id to the one belonging to the line's facility.

| Event | Outbound from | Inbound to |
|---|---|---|
| Departure ([delivery_itinerary_service.py:598](../../app/services/delivery_itinerary_service.py#L598)) | `order_line.warehouse` | **transit of `order_line.warehouse`'s facility** *(was: the single configured id)* |
| Acceptance ([:746](../../app/services/delivery_itinerary_service.py#L746)) | **transit of `order_line.warehouse`'s facility** | — |
| Refusal / failure | **transit of `order_line.warehouse`'s facility** | `order_line.warehouse` |

Quantities, commitments, reservations, the delivery state machine and proof of delivery are all
untouched. `stock_ledger` itself needs no change: it takes a warehouse id and does not care which
one.

**Ledger entries are unchanged in shape too** — `source = DELIVERY_ORDER`, `reference` = the
itinerary id. Nothing about a ledger row records which facility it belongs to, and nothing needs to:
the row's warehouse now answers that through `warehouse.facility`, which is what the whole feature
is for.

---

## Facility lifecycle

| Operation | Behaviour |
|---|---|
| **Create** | Facility and its in-transit location are inserted in one transaction and committed together. A failure on either leaves neither (FR-007) |
| **Update** | Unchanged. Renaming or re-coding a facility does not touch its in-transit location — the code is keyed on `facility_id`, which cannot change |
| **Deactivate** | Unchanged. The in-transit location stays `ACTIVE`, so trips already in flight settle normally |
| **Delete** | The in-transit location is asserted-clear, deleted and flushed first; then the facility's own reference check runs unchanged (research R5). An audit entry is staged in the same transaction (FR-015a) |

Deletion answers, in order:

1. In-transit location carries ledger history → `409 Still referenced by lot_serial_tracking.warehouse (n)`
2. Facility referenced by anything else → `409 Still referenced by warehouse.facility (n), …` — unchanged from today
3. Otherwise → `204`, both rows gone **and one audit entry written**

A fourth answer, decided first, was a `422` when the acting user had no employee record. Removed by
#127: `user.employee` is `NOT NULL` from migration 012, so the acting user always has one.

### The audit entry (FR-015a)

Reuses the existing `incidence` table through `incidences.record` — no new table, no new column.

| Field | Value |
|---|---|
| `source` | **new** `SourceType.FACILITY = 10` — the next free value after `PRODUCT = 9` |
| `instance_id` | the facility id |
| `updater` | the acting user's employee id. `CurrentUser.employee_id` is typed `int | None`; absence is a broken state, so the delete refuses rather than falling back to the system employee (research R8) |
| `comment` | the reason — required non-blank by `incidences.record` |
| `content` | names the in-transit location removed with the facility |

Staged, not committed, by `incidences.record` — it commits with the deletes or not at all, so a
refused delete leaves no orphan entry and a successful one cannot be untraced.

**Signature change**: `facility_service.delete_facility` gains the acting user, and
`app/api/v1/endpoints/facilities.py` passes its `CurrentUser` through instead of discarding it. This
is the only shipped-service signature change in the feature.

---

## Configuration removed

| Setting | Was | Now |
|---|---|---|
| `in_transit_warehouse_id` ([config.py:53](../../app/core/config.py#L53)) | `int = 0`, set per deployment after migration 008 | **removed** |
| `IN_TRANSIT_WAREHOUSE_ID` in `.env.example` | documented with a recovery query | **removed** |
| `verify_in_transit_warehouse()` ([main.py:42](../../app/main.py#L42)) | refused to boot when unset or dangling | **removed**; `lifespan` keeps only `ensure_system_employee` |

`Settings` is `extra='ignore'`, so a leftover `IN_TRANSIT_WAREHOUSE_ID` in a live `.env` is inert
(research R7).

---

## Migration `011_facility_transit_warehouses.sql`

Four steps, plus a rollback. Counts are measured, not estimated (research R6).

1. **Guard.** Fail if the existing `IN-TRANSIT` row holds a nonzero balance in
   `lot_serial_tracking`. Measured: **0 rows against it**, so this passes trivially today and exists
   to keep the claim checkable at run time.
2. **Add the column.** `ALTER TABLE warehouse ADD COLUMN in_transit TINYINT(1) NOT NULL DEFAULT 0`.
3. **Convert the existing row.** `id 20` → `in_transit = 1`, `code = 'IN-TRANSIT-1'`. It already
   belongs to facility 1; it becomes that facility's location rather than being discarded (FR-016).
4. **Backfill the other thirteen.** Insert one per facility that has none, keyed on
   `NOT EXISTS (… WHERE w.facility = f.facility_id AND w.in_transit = 1)` so the step is idempotent.
5. **Assert.** Every facility has exactly one; `warehouse` holds 32 rows, 14 of them in-transit.

**Rollback** — `011_facility_transit_warehouses_rollback.sql`: delete the 13 inserted rows (safe,
they can have no ledger history that soon), restore row 20 to code `IN-TRANSIT`, drop the column.
The rollback must state that `IN_TRANSIT_WAREHOUSE_ID=20` has to go back into the environment,
because the code it rolls back to reads that setting.

---

## What is deliberately not changed

- **`stock_ledger`** — takes a warehouse id, always has.
- **`deliveries_itinerary_detail`** — no snapshot of the transit warehouse; the join recovers it
  (research R2).
- **`references.py`** — the cascade is achieved by delete-then-flush, not by a new parameter
  (research R5).
- **`incidence` table and `incidences.record`** — the audit entry reuses both unchanged; only the
  `SourceType` enum gains a value (research R8).
- **`warehouse_service.get_warehouse`** — left unfiltered on purpose; the guard lives at the API
  boundary (research R4).
- **`lot_serial_tracking` history** — settled entries against the old shared row stay where they
  are. There are none.
- **Delivery flow semantics** — commitments, sent quantity, reservations, statuses, POD: untouched.
