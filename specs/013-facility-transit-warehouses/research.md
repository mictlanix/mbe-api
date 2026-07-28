# Phase 0 — Research: One In-Transit Location per Facility

Eight decisions. Two of them (R1, R6) are the ones the rest hang off: how an in-transit location is
recognised once its configuration setting is gone, and what the changeover actually has to move.

**R4 was revised and R8 added after the clarification session of 2026-07-28.** R4's original choice
of `404` is kept visible above its replacement rather than overwritten.

---

## R1 — Identifying an in-transit location without configuration

**Decision.** A boolean column on `warehouse`:

```sql
`in_transit` TINYINT(1) NOT NULL DEFAULT 0
```

plus a deterministic identifying code, `IN-TRANSIT-{facility_id}`, so the globally unique
`code_UNIQUE` index still holds with fourteen of them.

**Rationale.** Spec 012's R3 rejected exactly this column, on the grounds that *"the config setting
already identifies the row… a column change for one comparison is not warranted."* That rationale
does not survive FR-006, which retires the setting. What is left is four questions the code has to
answer, and only one of them is about a single row:

| Question | Where | With a flag |
|---|---|---|
| Which location receives goods leaving warehouse *W*? | departure, stop closure | `facility = W.facility AND in_transit = 1` |
| Is *this* warehouse an in-transit location? | edit / delete / fetch guards | one boolean read |
| Which warehouses may a user choose from? | listings, pickers, fallback, stock lookup | `in_transit = 0` |
| Does every facility have exactly one? | migration, tests | `GROUP BY facility` |

Three of the four are set predicates. A single configured id could never have answered them, which
is why spec 012 could only ever have one in-transit location.

**Alternatives rejected.**

- *Code-prefix sniffing (`code LIKE 'IN-TRANSIT-%'`) with no new column.* Cheapest on schema and
  genuinely tempting. Rejected because `code` is user-writable: someone can create a warehouse coded
  `IN-TRANSIT-7` today, and it would silently become facility 7's transit location — or become
  uneditable and undeletable. Defending that needs a new guard on `create_warehouse` plus
  `NOT LIKE` on every listing. A boolean the user cannot set is less machinery, not more.
- *`facility.in_transit_warehouse` as a foreign key.* Structurally guarantees one per facility, and
  gives the fastest forward lookup. Rejected on the insert order: the facility must exist before its
  warehouse, and the warehouse before the column can point at it, so the column must be nullable —
  which forfeits the very guarantee that motivated it. It also turns "which warehouses may I choose
  from" into an anti-join on every listing.
- *A `warehouse_kind` enum column instead of a boolean.* Speculative. One kind exists; a second
  would be a migration when it arrives (Principle I).
- *A separate `facility_transit_warehouse` mapping table.* A table to hold one boolean (Principle V).

---

## R2 — Which facility's location receives the goods

**Decision.** The facility of the **delivery line's dispatch warehouse** —
`delivery_order_detail.warehouse` → `warehouse.facility` → that facility's in-transit location.
Resolved once per departure and once per stop closure with a single self-join:

```sql
SELECT w.warehouse_id, t.warehouse_id
FROM warehouse w
JOIN warehouse t ON t.facility = w.facility AND t.in_transit = 1
WHERE w.warehouse_id IN (:dispatch_warehouse_ids)
```

**Rationale.** Attribution follows where the goods physically were. `delivery_order.facility` is the
office that raised the paperwork and can differ from the warehouse that shipped; the warehouse is
the truthful answer (FR-002, spec Edge Cases). The line already snapshots its dispatch warehouse
(FR-025a of spec 012), so nothing new needs storing — the input is already on the row that the
return path reads.

One query per operation, not per line, keeps this off the N+1 list that
`app/services/fk_expansion.py` exists to police. Both `depart()` and `close_stop()` already iterate
`(entry, order_line)` pairs, so the map slots into loops that exist.

**Alternatives rejected.**

- *Snapshot the in-transit warehouse id on the itinerary line at departure.* Would make closure a
  pure read. Rejected: a fourth quantity-adjacent column on `deliveries_itinerary_detail` to cache
  something a two-row join recovers, and it would go stale if a warehouse ever moved facility.
- *Use `delivery_order.facility`.* Simpler lookup, wrong answer whenever the two diverge — and the
  divergence is silent, which is the failure mode this whole feature exists to remove.

---

## R3 — Enforcing exactly one per facility

**Decision.** Enforced in the application at facility creation, backfilled by migration 011, and
asserted by the migration itself. **No database constraint.**

**Rationale.** MariaDB has no partial unique index. The nearest equivalent is a virtual generated
column plus a unique key on it:

```sql
ADD COLUMN `in_transit_facility` INT AS (IF(`in_transit` = 1, `facility`, NULL)) VIRTUAL,
ADD UNIQUE KEY `uq_warehouse_in_transit` (`in_transit_facility`)
```

That works — NULLs do not collide — but it is a second column and a second index to enforce an
invariant with no realistic violator. There are two writers to this schema: this API, which creates
the row in one place and is covered by tests; and the legacy application, which does not know the
column exists and therefore writes `0` into it by default and can never produce a second in-transit
row. A constraint guarding against nobody is complexity without a threat (Principle I).

**Alternatives rejected.**

- *Generated column + unique index* — above.
- *A `BEFORE INSERT` trigger* — logic invisible to anyone reading the codebase, and this repository
  has no trigger precedent.

---

## R4 — Making the row untouchable

> **Revised after clarification (2026-07-28).** This decision originally chose `404 Not found`,
> reasoning that the row is not an addressable warehouse and that one filter in `get_warehouse`
> would cover three requirements at once. The clarified answer is **`403 Forbidden`**, and the
> original reasoning does not survive it: hiding a row that demonstrably exists sends a developer
> hunting the database for a warehouse the API says is absent. The cheaper design was cheap partly
> because it told the caller less. Recorded as a revision rather than silently rewritten, because
> the rejected reasoning is what the clarification corrected.

**Decision.** `warehouse_service.get_warehouse` is **unchanged** — it still returns the row.
`list_warehouses` filters on `in_transit = 0`. The three single-row endpoints resolve through one
shared helper in `app/api/v1/endpoints/warehouses.py`:

```python
async def _addressable(db, warehouse_id) -> Warehouse:
    warehouse = await warehouse_service.get_warehouse(db, warehouse_id)
    if warehouse is None:
        raise HTTPException(404, 'Warehouse not found')
    if warehouse.in_transit:
        raise HTTPException(403, 'In-transit locations are managed by the system')
    return warehouse
```

**Rationale.** `403` keeps "forbidden" and "not found" distinguishable (FR-013a), and carries a
message that explains itself — which `404` structurally could not, because a 404 that explains why
the row is hidden is not a 404. mbe-ui can show an operator something better than a dead end.

The helper is still a single point of enforcement: GET, PUT and DELETE all resolve through it, so
FR-010, FR-011 and FR-013 remain one change rather than three guards. It also **removes** the
triplicated `if warehouse is None: raise 404` block those three endpoints repeat today
([warehouses.py:47](../../app/api/v1/endpoints/warehouses.py#L47),
[:65](../../app/api/v1/endpoints/warehouses.py#L65),
[:79](../../app/api/v1/endpoints/warehouses.py#L79)), so the endpoint module gets shorter, not
longer.

Keeping `get_warehouse` unfiltered is the better half of this trade. A service function that
silently pretends a row does not exist is a trap for every future caller; the filtering belongs at
the boundary that has an HTTP answer to give.

The remaining selection surfaces are separate call sites and each needs its own predicate flipped
from the retired setting to the flag: `sales_order_service` product lookup
([:927](../../app/services/sales_order_service.py#L927)) and `delivery_order_service._fallback_warehouse`
([:115](../../app/services/delivery_order_service.py#L115)). The fallback **does not exclude
in-transit today** — it takes `MIN(warehouse_id)` within the facility with no exclusion at all, so
it is a live latent defect, not merely a port. FR-012 closes it.

**Alternatives rejected.**

- *`404`, by returning `None` from `get_warehouse`.* The original decision — overturned above.
- *`409 Conflict`.* Matches how this codebase refuses blocked mutations
  (`assert_not_referenced`), but `409` on a GET is wrong, so GET would need a second behaviour and
  the single-point-of-enforcement property would be lost.
- *Read-only exposure (GET succeeds, PUT/DELETE forbidden).* Would let a facility manager see the
  in-transit balance — arguably useful. Out of scope: the spec puts facility inventory reporting
  outside this feature, and building a half-surface now pre-empts how that report will want it.

---

## R8 — Auditing the facility cascade

**Decision.** `facility_service.delete_facility` writes an `incidence` entry through the existing
`incidences.record`, under a **new** `SourceType.FACILITY = 10`, keyed to the facility id, naming
the removed in-transit location in the entry's context. Staged in the same transaction as the
deletes, so it commits with them or not at all.

**Rationale.** The cascade destroys a row the operator never created and cannot see (FR-014). Even
though it can only succeed when that row has no inventory history — so nothing of value is lost —
the clarified answer is that the deletion should not be silent. `incidences.record` already exists
for exactly this shape of evidence: who, when, why, keyed by `(source, instance_id)`.

`SourceType` has no facility value; the enum's highest is `PRODUCT = 9`, so `FACILITY = 10` is the
next free slot. Adding one value is the honest option — the alternative considered and rejected in
clarification was filing the entry under an existing type, which makes the log less trustworthy than
no log at all.

**Attribution.** `incidences.record` requires an `updater` employee id. `CurrentUser.employee_id` is
typed `int | None`, and the clarified answer is that every authenticated user has an employee record
— a user without one is a broken state, not a case to design a fallback for. So `delete_facility`
takes the employee id and **refuses rather than inventing an attribution** if it is absent.

**Worth stating plainly**: the database does not enforce this. `user.employee` is nullable
(`docs/mbe_schema.sql`), so the invariant is deployment policy, not a schema guarantee. Enforcing it
globally is a different feature; this one refuses loudly instead of falling back to the system
employee, which would have logged a person-shaped lie.

> **Resolved by #127, after this feature shipped.** T043 checked the invariant against live data and
> found it false — 2 of 34 active users had no employee record, one of them an administrator. The
> "different feature" turned out to be a one-column migration: `user.employee` is `NOT NULL` from
> migration 012, and the unlinked accounts were purged before it was applied. `CurrentUser.employee_id`
> is now `int`, and `delete_facility`'s refusal is deleted along with the seven other services' —
> a branch that cannot execute is worse than no branch, because the annotation above it lies about
> what can arrive. The rejected alternative below stands unchanged: nothing falls back to the system
> employee. The invariant is now guaranteed rather than refused-upon.

**Consequences beyond this feature.** `delete_facility` gains a parameter and the endpoint must pass
`CurrentUser` through — a signature change to a shipped service, which is why it is named in the
plan's Complexity Tracking rather than absorbed quietly.

**Alternatives rejected.**

- *No audit entry.* The design this feature started with, and defensible — the cascade removes only
  a system-created row with no history. Overturned by clarification.
- *Reuse an existing `SourceType`.* Cheapest, and worse than nothing: an audit log filed under the
  wrong entity type is a log nobody can trust.
- *Fall back to `SYSTEM_EMPLOYEE_ID` when the user has no employee record.* Would keep deletion
  working for every user it works for today, but records "the system did it" for something a person
  did. Rejected by the clarified invariant.

---

## R5 — Facility deletion cascade

**Decision.** In `facility_service.delete_facility`: resolve the facility's in-transit location,
assert *its own* references are clear, delete it, flush, then run the existing facility reference
check unchanged.

```python
transit = await warehouse_service.get_transit_warehouse(db, facility.facility_id)
if transit is not None:
    await assert_not_referenced(db, transit)   # FR-015 — ledger history still blocks
    await db.delete(transit)
    await db.flush()
await assert_not_referenced(db, facility)      # unchanged
await db.delete(facility)
await db.commit()
```

**Rationale.** `assert_not_referenced`'s `exempt` parameter is **table-granular**
([references.py:65](../../app/services/references.py#L65)) — exempting `warehouse` would hide the
facility's real warehouses too, turning a correct 409 into a foreign-key 500. Deleting the row and
flushing first makes the existing `COUNT` see it gone inside the same transaction, so FR-014 needs
no change to `references.py` at all.

FR-015 then falls out rather than being written: if the transit location carries ledger history the
first assert raises `409 Still referenced by lot_serial_tracking.warehouse (n)` — the same answer
any other warehouse gives. Ordering the transit assert *first* is deliberate: it is the more
surprising blocker, so it should be the one named.

Rollback on failure is automatic. `get_db` yields inside `async with AsyncSessionLocal()`
([session.py:28](../../app/db/session.py#L28)) and never commits on an exception, so the staged
delete is discarded when the session closes.

**Alternatives rejected.**

- *A row-level `exempt` in `references.py`.* A new parameter on a shared helper for exactly one
  caller (Principle III).
- *An ORM `cascade='all, delete-orphan'` relationship from facility to warehouse.* Would delete
  every warehouse in the facility, destroying real stock records. `user_service` uses that pattern
  legitimately ([user_service.py:102](../../app/services/user_service.py#L102)) because every
  `user_settings` row *is* owned by its user; warehouses are not.

---

## R6 — The changeover, measured

**Decision.** Migration 011 adds the column, converts the existing shared row into facility 1's
in-transit location, and inserts one for each remaining facility. It **refuses to run** if the
shared location holds a nonzero balance, rather than attempting to redistribute it.

**Measurement.** Run read-only against the deployment database on **2026-07-28**, following the
precedent migration 007 set of stating real counts rather than assuming them — and correcting spec
012's R12, which had to plan around an audit it could not complete:

| Query | Result |
|---|---|
| Facilities | **14** — ids 1–5, 6, 8, 47–53. Seven `ACTIVE`, seven `INACTIVE` |
| Facilities with at least one real warehouse | **14 of 14** — none is warehouse-less |
| Warehouse rows | **19** — 18 real, 1 in-transit |
| The in-transit row | id **20**, code `IN-TRANSIT`, facility **1** |
| Status of facility 1 | **`INACTIVE`** |
| `lot_serial_tracking` rows against warehouse 20 | **0** |
| Nonzero in-transit balances | **none** |
| Itineraries in `DEPARTED` | **0** |

Two things fall out of this.

**FR-017 has nothing to redistribute.** The shared location has never been posted to. The
requirement is therefore satisfied by an assertion that the balance is zero, not by a rewrite — and
SC-007's "zero discrepancy" is trivially true because both totals are zero. The guard stays in the
migration because it is what makes the claim checkable at run time rather than at planning time.

**The defect is sharper than the spec stated.** The shared location sits on facility 1, which is
`INACTIVE` — and `available_orders` filters on `Facility.status == 0`
([delivery_itinerary_service.py:96](../../app/services/delivery_itinerary_service.py#L96)), so
facility 1 can never dispatch anything. Every other facility's in-transit stock would have
accumulated on the books of the one facility structurally incapable of shipping it.

After migration: **14** in-transit rows; `warehouse` goes 19 → 32.

**Alternatives rejected.**

- *`UPDATE ... JOIN` re-attributing historic ledger entries* — drafted, then dropped. A ledger row
  is keyed by `(source, reference, product)` where `reference` is the itinerary; one itinerary can
  carry the same product for two facilities, and a single row cannot be split by an UPDATE. With
  zero rows to move it buys nothing, and the guard covers the case honestly if it ever arises.
- *Requiring zero `DEPARTED` itineraries as the precondition.* Measured at 0 anyway, but it is the
  wrong invariant: a settled trip leaves a zero balance and does not need blocking, while a stuck
  itinerary in another state could still hold one. Guard the balance, which is what actually matters.

---

## R7 — Retiring the setting and the startup check

**Decision.** Remove `in_transit_warehouse_id` from `app/core/config.py`, its two entries from
`.env.example`, and `verify_in_transit_warehouse()` from `app/main.py` along with its call in
`lifespan`.

**Rationale.** The startup check exists for one reason, stated in its own docstring: the id *"is
created by migration 008 and cannot be defaulted"*, so an unset setting would post the inbound half
of every departure against warehouse 0. A flag needs no configuration, so there is nothing left to
misconfigure — the check would be guarding a variable that no longer exists.

The failure it prevented does not come back. It moves to the point of use: FR-009 refuses the
dispatch with `422 Facility {id} has no in-transit location`, which is strictly better placed —
loud, specific about which facility, and it cannot be true for one facility while the deployment
boots fine for the others.

Deleting a setting that is currently set in live environments is safe: `Settings` is configured
`extra='ignore'` ([config.py:9](../../app/core/config.py#L9)), so a leftover
`IN_TRANSIT_WAREHOUSE_ID=20` in a deployed `.env` is inert rather than fatal.

`ensure_system_employee` stays in `lifespan`; it is unrelated. Nothing else uses the removed
setting — verified across `app/`, `tests/` and `migrations/`.

**Alternatives rejected.**

- *Keep a startup check that asserts every facility has an in-transit location.* Attractive as an
  SC-001 tripwire, but it refuses to boot the entire API because someone added a facility in the
  legacy application — an availability failure in response to a condition FR-009 already handles at
  the exact moment it matters, for the one facility affected.
- *Keep the setting as an override.* Configurability nobody asked for (Principle I).
