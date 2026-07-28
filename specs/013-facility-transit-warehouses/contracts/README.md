# Phase 1 — Contracts: One In-Transit Location per Facility

**No new endpoint, no new route, no new schema field, no new privilege.** This feature changes what
five existing endpoints do, and removes one deployment setting. The response models
(`WarehouseResponse`, `FacilityResponse`) are unchanged — `in_transit` is a storage flag, not a
field any client needs, and exposing it would advertise rows the client can never address.

---

## Warehouses — `/api/v1/warehouses`

Privileges unchanged (`SystemObject.WAREHOUSES` with the matching `AccessRight`).

| Method | Path | Change |
|---|---|---|
| GET | `` | Excludes in-transit locations, as today. The predicate moves from the configured id to `in_transit = 0`, so it now excludes **all fourteen** rather than the one that happened to be configured. `total` reflects the exclusion |
| GET | `/{id}` | **Changed** — `403` for an in-transit location, which was previously returned in full (FR-013) |
| PUT | `/{id}` | **Changed** — `403` for an in-transit location. No rename, re-code, re-facility, deactivation or comment edit is possible, for any role including administrator (FR-010) |
| DELETE | `/{id}` | **Changed** — `403` for an in-transit location (FR-011) |
| POST | `` | Unchanged. Creates ordinary warehouses with `in_transit = 0`; the flag is not accepted from the request body |

### `403` body

```json
{ "detail": "In-transit locations are managed by the system" }
```

**Why 403 and not 404.** The two conditions stay distinguishable (FR-013a): an id naming no
warehouse still answers `404 Warehouse not found`, and an id naming an in-transit location answers
`403` **with a reason**. A `404` would send a developer hunting the database for a row the API
claims is absent, and a `404` that explains why the row is hidden is not a `404`.

All three single-row endpoints resolve through one shared helper, so this is a single point of
enforcement rather than three guards — and the helper *replaces* the `if warehouse is None: raise
404` block those endpoints currently repeat, so the module gets shorter. `warehouse_service.get_warehouse`
is deliberately left unfiltered: a service function that pretends a row does not exist is a trap for
every future caller. See research R4, which records the superseded `404` decision and why it was
overturned.

---

## Facilities — `/api/v1/facilities`

Privileges unchanged.

| Method | Path | Change |
|---|---|---|
| POST | `` | **Changed** — also creates the facility's in-transit location, in the same transaction. `201` with the same body as today. If the location cannot be created the facility is not created either (FR-007) |
| DELETE | `/{id}` | **Changed** — removes the facility's in-transit location as part of the delete, so that row alone never blocks it (FR-014) |
| GET, PUT, POST `/{id}/logo` | | Unchanged |

### `DELETE /facilities/{id}` — responses in the order they are decided

| Status | Condition | Body |
|---|---|---|
| `404` | No such facility | `Facility not found` — unchanged |
| `409` | The facility's in-transit location carries inventory history | `Still referenced by lot_serial_tracking.warehouse (n) — remove those records first` (FR-015) |
| `409` | The facility is referenced by anything else | `Still referenced by warehouse.facility (n), … — remove those records first` — unchanged |
| `422` | The acting user has no employee record | `A reason is required and cannot be blank` is *not* the answer here — the refusal names the missing employee record. Audit attribution cannot be invented (FR-015a, research R8) |
| `204` | None of the above | Facility and its in-transit location both gone, audit entry written (FR-014, FR-015a) |

The in-transit blocker is reported **first** because it is the surprising one; a caller who sees
`warehouse.facility (3)` knows what to do, and a caller who sees inventory history on a location
they never created needs to be told that specifically.

**Audit side effect.** A successful `204` writes an `incidence` row under the new
`SourceType.FACILITY`, keyed to the facility id, naming the acting user and the in-transit location
removed with it. It is staged in the same transaction as the deletes, so a facility cannot be
deleted without the trace, and a refused delete leaves no orphan entry.

**Signature change.** `facility_service.delete_facility` now needs the acting user, so the endpoint
passes its `CurrentUser` through rather than discarding it. This is the only shipped-service
signature change in the feature.

---

## Delivery itineraries — `/api/v1/delivery-itineraries`

| Method | Path | Change |
|---|---|---|
| POST | `/{id}/depart` | Posts the inbound half of the move to the in-transit location of **each line's own dispatch facility** instead of one shared location (FR-002, FR-005). New failure: `422` when a facility on the trip has no in-transit location (FR-009) |
| POST | `/{id}/stops/{stop_id}/close` | Consumes and returns against the same per-facility location (FR-003, FR-004). Same `422` on a missing location |

Everything else about both endpoints is unchanged: request bodies, success bodies, the `409` on
departing with nothing committed, the `422` naming over-committed lines, sent quantities,
commitments and reservations.

### New `422` — a facility with no in-transit location

```json
{ "detail": "Facility 7 has no in-transit location" }
```

`422` matches the precedent already set by `_fallback_warehouse`'s
`Facility {id} has no warehouse to dispatch from` — a data-integrity condition the caller cannot fix
by changing the request, surfaced at the point where it would otherwise misfile stock.

The check runs over the whole trip **before the first ledger entry is written**, so a departure
either posts every line or none. A trip spanning several facilities is not itself an error
(FR-005); only a facility missing its location is.

---

## Deployment contract

| Item | Change |
|---|---|
| `IN_TRANSIT_WAREHOUSE_ID` | **Removed.** No longer read. A leftover value in a live `.env` is inert — `Settings` is `extra='ignore'` |
| Startup | The API no longer refuses to boot over in-transit configuration; there is none. `lifespan` keeps `ensure_system_employee` only |
| Post-migration step | **Removed.** Migration 011 requires no follow-up id capture, which is the manual step SC-005 counts down to zero |

---

## Not in this feature

- No endpoint exposing in-transit balances, per facility or otherwise. Facility inventory reporting
  is out of scope; this feature makes the attribution correct so that report can be built on it.
- No `in_transit` field on `WarehouseResponse` or `WarehouseCreate`.
- No new privilege or `SystemObject`.
