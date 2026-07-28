# Phase 1 — Quickstart: One In-Transit Location per Facility

Seven scenarios. Each maps to a user story and the success criterion it proves. Scenario 1 is the
one that would have been impossible before this feature; Scenario 7 is the one most likely to be
skipped and most expensive to get wrong.

## Prerequisites

```bash
uv sync
uv run ruff check app/ migrations/ tests/          # must be clean before and after
uv run pytest -q                                    # green baseline
```

Migration 011 applied to a **copy** of the database, never the live one first:

```bash
uv run python -m app.jobs.migrate --check           # 011 discovered
# apply 011 on the copy, then:
uv run pytest -q                                    # still green
```

---

## Scenario 1 — Two facilities dispatch, and neither shows up on the other's books

**Proves**: US1, FR-002, SC-002, SC-003. *This is the defect.*

1. Pick two active facilities with real warehouses — from the audit, e.g. **51** (`CMZUAL01`) and
   **53** (`CMCOAL04`).
2. Raise and approve a delivery order per facility, each dispatching from its own warehouse.
3. Commit both to itineraries and depart.

**Expect**

- Facility 51's transit location holds exactly what left `CMZUAL01`; facility 53's holds exactly
  what left `CMCOAL04`.
- Neither balance appears on the other. **Zero cross-attribution.**
- Facility 1's transit location — which under spec 012 would have received both — holds nothing.

**Negative**: with spec 012's code, both quantities land on warehouse 20. That contrast is the test.

---

## Scenario 2 — One trip, two facilities

**Proves**: US1, FR-005, spec Edge Cases.

Load a single itinerary with lines dispatched from facility 51 **and** facility 53. Depart.

**Expect**: departure succeeds — spanning facilities is not an error. Each line's quantity is posted
to its own facility's transit location. Close the stop with a full delivery and both balances return
to zero.

---

## Scenario 3 — Refusal returns goods to the warehouse, not to a facility default

**Proves**: US1, FR-004, and that spec 012's return path is genuinely unchanged.

Depart a line from facility 51, then close the stop as refused.

**Expect**: the quantity leaves facility 51's transit location and lands back in `CMZUAL01` — the
line's own dispatch warehouse. The reservation is reclaimed. Nothing about this differs from spec
012 except which transit location the goods left.

---

## Scenario 4 — The order's facility and the warehouse's facility disagree

**Proves**: FR-002, spec Edge Cases, and the assumption the spec records explicitly.

Raise a delivery order whose `facility` is **51** but whose line dispatches from a warehouse owned by
**53**. Depart.

**Expect**: the goods are in transit for **53** — the facility they physically left — not 51.

---

## Scenario 5 — A new facility can dispatch immediately

**Proves**: US2, FR-007, SC-001, SC-005.

```bash
# POST /api/v1/facilities  → 201
```

**Expect**

- Its in-transit location exists the moment the `201` returns, coded `IN-TRANSIT-{new_id}`.
- A dispatch from one of its warehouses succeeds with **no setup step and no environment change** in
  between. This is the manual step SC-005 counts to zero.
- `SELECT facility, COUNT(*) FROM warehouse WHERE in_transit = 1 GROUP BY facility` returns exactly
  one row per facility, count 1, with no exceptions.

**Negative**: force the warehouse insert to fail (duplicate code) → the facility is **not** created
either. No facility ever exists without its location.

**Negative**: delete a facility's in-transit row directly in SQL, then dispatch from one of its
warehouses → `422 Facility {id} has no in-transit location`, and **no ledger entry is written for
any line on the trip** — the check runs before the first `post_movement`, so departure is all or
nothing (FR-009).

---

## Scenario 6 — The location cannot be touched, by anyone

**Proves**: US3, FR-010 – FR-013, SC-004. Run every request as an **administrator**.

| Request | Expect |
|---|---|
| `GET /warehouses` | No in-transit location in `items`; `total` excludes all fourteen |
| `GET /warehouses/20` | `403 In-transit locations are managed by the system` |
| `PUT /warehouses/20` (rename) | `403` |
| `PUT /warehouses/20` (`facility: 53`) | `403` — no re-parenting another facility's stock |
| `PUT /warehouses/20` (`status: INACTIVE`) | `403` |
| `DELETE /warehouses/20` | `403` |
| `GET /warehouses/999999` | **`404 Warehouse not found`** — the two conditions stay distinguishable (FR-013a) |
| `GET /warehouses?facility=1` | Facility 1's real warehouse only |
| Product stock lookup on a sales order | No in-transit location offered |
| Delivery line with no warehouse chosen | Automatic fallback never picks an in-transit location |

**Assert the `404` row, not just the `403` rows.** A guard that answers `403` for *everything*
— including ids that name nothing — would pass every other line in this table while destroying the
distinction FR-013a exists for.

**The fallback row is a live bug fix, not a port** — today `_fallback_warehouse` takes
`MIN(warehouse_id)` inside the facility with no exclusion at all. Assert it explicitly with a
facility whose in-transit row has the lowest id.

---

## Scenario 7 — Facility deletion, all three answers

**Proves**: US4, FR-014, FR-015, SC-006.

| Case | Action | Expect |
|---|---|---|
| Never traded | Create a facility, then delete it | `204`. Facility **and** its in-transit location gone, **plus an `incidence` row** under `SourceType.FACILITY` naming the acting user and the removed location (FR-015a) |
| Has dispatched before | Delete a facility whose transit location carries ledger history | `409 Still referenced by lot_serial_tracking.warehouse (n)` — named first, before any other blocker. **No audit entry** is left behind |
| Blocked for other reasons | Delete a facility with real warehouses | `409 Still referenced by warehouse.facility (n), …` — unchanged from today |
| Acting user has no employee record | Delete any deletable facility | `422` naming the missing employee record. Attribution is never invented (research R8) |

**The one to actually verify**: after a `409`, confirm the in-transit location is **still there**
and no `incidence` row was written. The delete and the audit entry are both staged before the
assert, so a missing rollback would silently destroy the row and log a deletion that did not happen,
while appearing to refuse. Re-fetch both after the failed request.

---

## Migration checks

Run on a copy. Counts are from the audit of **2026-07-28** (research R6).

| Check | Before | After |
|---|---|---|
| `warehouse` rows | 19 | **32** |
| `in_transit = 1` rows | — | **14**, one per facility |
| Facilities without one | 14 | **0** |
| Row 20 | `IN-TRANSIT`, facility 1, `in_transit` absent | `IN-TRANSIT-1`, facility 1, `in_transit = 1` |
| `lot_serial_tracking` rows against row 20 | **0** | **0** — nothing to redistribute (SC-007 holds trivially) |
| Itineraries in `DEPARTED` | **0** | 0 |

**Guard**: re-run the migration on a database where the shared location holds a nonzero balance →
it **refuses** rather than guessing an attribution (spec Assumptions).

**Idempotence**: apply 011 twice → the backfill inserts nothing the second time.

**Rollback**: apply `011_..._rollback.sql` → 13 rows deleted, row 20 back to `IN-TRANSIT`, column
dropped. The rollback restores code that reads `IN_TRANSIT_WAREHOUSE_ID`, so **the setting must go
back into the environment as `20`** — the rollback script says so at the top.

---

## Final gates

```bash
uv run ruff check app/ migrations/ tests/    # zero violations
uv run pytest -q                             # all green
```

Plus, by inspection: `grep -rn "in_transit_warehouse_id\|IN_TRANSIT_WAREHOUSE_ID" app/ tests/ .env.example`
returns **nothing**. The setting, its startup check and its documentation are all gone (FR-006,
research R7).
