# Implementation Plan: Delivery & Logistics Endpoints

**Branch**: `012-delivery-logistics-endpoints` | **Date**: 2026-07-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-delivery-logistics-endpoints/spec.md`

## Summary

Build the delivery capability the API has never had — delivery orders, a supervisor approval queue,
a pending-deliveries view, and itineraries that carry stops to customers — on the v2 status state
machine rather than the legacy booleans. Two routers, three services, three new tables, four changed
ones, and one migration that is the largest this repository has run.

Four things drive the technical approach:

1. **The lifecycle is the feature.** Every other delivery module in this codebase would be CRUD;
   this one is a state machine with eleven statuses, and correctness means no transition escapes it.
   All transitions route through a single helper that moves the status and writes the audit row
   together, so "no status change goes unrecorded" (SC-008) is structural rather than remembered.
2. **Quantities are the hard part, not the statuses.** Four quantities per delivery line, and the guard that
   the same open quantity is never committed twice. The guard is a `SELECT ... FOR UPDATE` on the
   delivery-order line, which is why the running totals live denormalised on that row — the lock
   must cover the values it protects.
3. **This feature reaches into sales orders, and that is the main risk.** The clarified inventory
   decision moves stock consumption from sales-order confirmation to delivery. `confirm_order` stops
   posting an outbound entry and writes a reservation instead. If the stock check is not
   simultaneously changed to subtract reservations, the system silently oversells — one physical
   unit satisfying unlimited orders. That single coupling is called out as its own research decision
   (R5) and its own phase.
4. **The migration is destructive and needs an audit first.** 26,763 existing delivery orders settle
   into terminal statuses; seven columns are dropped. Three audit queries did not complete before the
   database went offline during planning, and each gates a migration step (R12).

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI (ASGI), SQLAlchemy 2.0 async (`Mapped`/`mapped_column`), Pydantic
v2, aiomysql, Pillow (already present, used by `image_service`). **No new dependency.**

**Storage**: MariaDB 10.11. Four tables changed and three added by
`migrations/008_delivery_flow_v2.sql` (+ rollback) — see [data-model.md](./data-model.md). Existing
models in `app/models/logistics.py` are extended; `app/models/inventory.py` and `sales.py` are
unchanged as schema.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`, following `tests/api/`
(`dependency_overrides` over mocked services, no live database) and `tests/unit/` for service logic.

**Target Platform**: Linux server, ASGI (uvicorn)

**Project Type**: Web service — REST API under `/api/v1/`

**Performance Goals**: No numeric target; the spec sets none and no prior feature did. The operative
constraint is avoiding N+1 queries on the pending-deliveries and itinerary list endpoints, for which
`app/services/fk_expansion.py` already exists.

**Constraints**: Every route `async def`; all DB access through `AsyncSession`; ruff clean at 100
columns; every endpoint gated by `require_privilege`. Row locks via `with_for_update()`.

**Scale/Scope**: 8 user stories, 80 functional requirements, 2 new routers, 3 new services, 1
migration, ~26k rows transformed. Sized for phased delivery — see [Delivery Phases](#delivery-phases).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Simplicity First | ✅ Pass | Two v2 features were deliberately *not* built: notification on rejection (no substrate exists) and print/ticket rendering. The ORM event listener v2 prescribes was replaced with explicit calls — fewer moving parts, not more (R7). **Revised:** this row originally also claimed the itinerary needed no status enum. Post-tasks review overturned that — FR-068's `state` filter forces the derivation into a query predicate anyway, so the itinerary now carries a stored `status` (FR-033a). Storing it removes a reconstruction rather than adding state. |
| II. Think Before Coding | ✅ Pass | 8 clarifications recorded in the spec; 12 research decisions below, each with rejected alternatives. Production data was queried rather than assumed — and the one place it could not be, R12 says so instead of guessing. |
| III. Surgical Changes | ⚠️ Justified | **Twelve** existing app files edited, plus five existing test files. Six were foreseen (`models/logistics.py`, `enums.py`, `core/config.py`, `services/sales_order_service.py`, `services/image_service.py`, `api/v1/router.py`); six were not, and each was forced by something the work uncovered — see Complexity Tracking, which now lists all twelve. |
| IV. Goal-Driven Execution | ✅ Pass | Each user story is an independently testable slice; [quickstart.md](./quickstart.md) gives the verification for each, including the concurrency race and the stock invariant table. |
| V. Reuse Over Rebuild | ⚠️ Justified | Reservations reuse the existing `lot_serial_rqmt` table rather than adding one (R4); the in-transit location is a `warehouse` row so `stock_ledger.on_hand` works unchanged (R3); `documents.assign_folio`, `fk_expansion`, `references`, `ListResponse` and `require_privilege` are reused as-is. Three new tables and three new services justified in Complexity Tracking. |
| VI. Async-First | ✅ Pass | All handlers `async def`; all access via `AsyncSession`. The row locks in R2 and R9 use `with_for_update()`, which is async-safe. Image processing already offloads to a thread via `asyncio.to_thread`. |
| VII. Security by Default | ✅ Pass | Every route carries a system object and access right (FR-066); no public endpoint. **POD images are authenticated and stored outside the `/images` static mount** (FR-044a) — this principle is why the clarification went the way it did. |
| VIII. Ruff Compliance | ✅ Pass | Rule set E, F, I, UP at 100 columns; verified by `uv run ruff check app/ migrations/ tests/`. |

**Testing gate (Constitution v1.2.0)**: tests are **REQUIRED** and non-optional. Every router ships
a `tests/api/` file covering happy path, 404, 401 and resource-specific failures (409/422). Every
service carrying state transitions or arithmetic ships a `tests/unit/` file exercising those
branches directly. Tests are written first and confirmed failing.

**Gate result**: PASS with two justified deviations, both recorded in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/012-delivery-logistics-endpoints/
├── plan.md              # This file
├── research.md          # Phase 0 output — 12 technical decisions
├── data-model.md        # Phase 1 output — tables, enums, state machine, inventory table
├── quickstart.md        # Phase 1 output — 8 end-to-end validation scenarios
├── contracts/
│   └── README.md        # Phase 1 output — endpoint contracts, 4 privilege surfaces
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
migrations/
├── 008_delivery_flow_v2.sql              # NEW (+ rollback): status columns, quantities,
│                                         #   3 tables, 26,763-row settle, folio unique index,
│                                         #   in-transit warehouse seed
└── 009_drop_sales_order_detail_delivery.sql  # NEW (+ rollback): drops the dead per-line
                                          #   delivery flag (F1)

app/
├── main.py                               # EDIT: startup guard refusing to serve when the
│                                         #   in-transit warehouse is unset or missing
├── enums.py                              # EDIT: + DeliveryOrderStatus, FulfillmentType,
│                                         #   ItineraryStatus, StopOutcome, ShortfallReason;
│                                         #   TransactionType gains the 5 unmodelled legacy
│                                         #   values, DELIVERY_ORDER=10, SALES_ORDER_RESERVATION=11
├── core/config.py                        # EDIT: + 5 delivery settings
├── models/
│   ├── logistics.py                      # EDIT: 4 tables reshaped + 3 new models
│   └── sales.py                          # EDIT: drop SalesOrderDetail.delivery (F1)
├── schemas/
│   ├── delivery_order.py                 # NEW
│   ├── delivery_itinerary.py             # NEW
│   └── sales_order.py                    # EDIT: drop the line `delivery` field (F1)
├── services/
│   ├── delivery_order_service.py         # NEW — lifecycle, creation, approval, pickup, coverage
│   ├── delivery_itinerary_service.py     # NEW — stops, commitments, departure, stop closure
│   ├── delivery_events.py                # NEW — the transition + audit chokepoint
│   ├── stock_ledger.py                   # EDIT: + reserve/reserved/available/release
│   ├── image_service.py                  # EDIT: split normalisation from filename choice
│   ├── warehouse_service.py              # EDIT: exclude the in-transit warehouse from pickers
│   ├── sales_order_service.py            # EDIT: the R5 inventory rework; drop the delivery flag
│   └── sales_quote_service.py            # EDIT: stop writing the delivery flag on conversion
└── api/v1/
    ├── router.py                         # EDIT: register 2 routers
    └── endpoints/
        ├── delivery_orders.py            # NEW — incl. /approval, POD upload and serving
        └── delivery_itineraries.py       # NEW — incl. /deliveries pending view

tests/
├── api/
│   ├── test_delivery_orders.py           # NEW
│   └── test_delivery_itineraries.py      # NEW
└── unit/
    ├── test_delivery_order_service.py    # NEW
    ├── test_delivery_itinerary_service.py # NEW
    ├── test_delivery_events.py           # NEW
    ├── test_image_service.py             # NEW
    ├── test_stock_ledger.py              # EDIT: + reservations, availability, startup guard
    ├── test_sales_order_service.py       # EDIT: the R5 rework + the removed delivery flag
    ├── test_migrate.py                   # EDIT: + 008 and 009 discovery
    ├── test_references.py                # EDIT: + the 3 new tables are discoverable
    └── test_list_query_counts.py         # EDIT: + the delivery lists stay flat
```

> `tests/api/test_sales_orders.py` was planned as an edit and turned out to need none: it mocks the
> service layer, so it never asserted the ledger behaviour the inventory rework changed. Verified
> rather than assumed (T023).

**Structure Decision**: The existing single-project layout is kept unchanged —
`app/{enums,core,models,schemas,services,api/v1/endpoints}` with `tests/{api,unit}`. Every new file
follows the naming already used by `sales_order_service.py` / `sales_orders.py`. No new package.

## Delivery Phases

The spec covers four legacy screens and a cross-cutting inventory change. These phases follow the
spec's P1–P3 priorities so each lands as a working, testable slice. `/speckit-tasks` should preserve
this ordering — **Phase 0 and Phase 1 are hard prerequisites for everything after them.**

| Phase | Delivers | Stories | Verify |
|---|---|---|---|
| **0 — Audit & migration** | R12 audit completed and recorded; `008` + rollback; enums; settings; model changes | — | Migration applies and rolls back on a copy; 26,763 orders terminal; pending view empty; folio index holds |
| **1 — Inventory rework** | Reservations in `stock_ledger`; `sales_order_service` confirm/cancel/stock-check rework; in-transit warehouse | US4 (part) | On-hand unchanged at SO confirm; availability *does* drop; second order against one unit refused; existing sales tests updated and green |
| **2 — Order lifecycle** | `delivery_events`, `delivery_order_service`, `/delivery-orders` incl. approval | US1, US2, US8 | An order is raised, confirmed, rejected, corrected, approved; every transition appears in its history |
| **3 — Dispatch** | Pending view, itineraries, stops, commitments, departure | US3, US4 | The concurrency race yields exactly one success; departure fixes sent quantity and moves stock two-step |
| **4 — Outcomes** | POD storage and authenticated serving, stop closure, child-DO split, sales-order write-back | US5 | A partial delivery splits correctly; POD returns 401 unauthenticated; SC-003 invariant holds |
| **5 — Pickup & recovery** | Counter pickup, requeue, cancel with reason | US6, US7 | A pickup settles with proof and no transit entry; a failed delivery re-queues and delivers on a second trip |

Phase 1 is where the cross-feature risk lives and should land — and be reviewed — on its own, before
any delivery endpoint depends on it.

## Complexity Tracking

> Filled because the Constitution Check flagged two justified deviations.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| **3 new tables** (Principle V) | `proof_of_delivery` — one definition serving both handover kinds, so a delivery's proof can cover several orders at one stop while still being "archived on the order". `delivery_order_event` — transitions have structured from/to columns the free-text `incidence` log cannot express or query. `deliveries_itinerary_stop` — the stop is the unit that closes, per the clarified answer. | Embedding POD columns in both `delivery_order` and the stop would duplicate five columns and two validation paths. Reusing `incidence` for transitions was considered and rejected in the spec's Assumptions — it has no from/to status. Omitting the stop table was offered in clarification and explicitly declined. |
| **3 new services** (Principle V) | One per lifecycle, matching the one-service-per-resource convention. `delivery_events` is separate because it is the chokepoint every transition passes through; folding it into the order service would leave the itinerary service free to change status without an audit row. | A single `logistics_service.py` would exceed a thousand lines across two unrelated lifecycles and break the naming convention every existing endpoint follows. |
| **Editing `sales_order_service.py`** (Principle III) | FR-055 – FR-057. The clarified inventory decision cannot be implemented anywhere else — confirmation is where consumption happens today. | Leaving it and posting delivery movements on top would double-decrement every stocked line. Leaving the stock check on raw on-hand would silently oversell (R5). |
| **Editing `image_service.py`** (Principle III) | FR-044b. Content-addressed filenames alias identical captures, so deleting one order's proof would remove another's evidence. | Reusing the function unchanged keeps the aliasing bug. Writing a second, independent image pipeline would duplicate the Pillow normalisation and the 2 MB guard. |
| **Editing `enums.py` and `config.py`** (Principle III) | The legacy `WebConfig` delivery values and five new domain enums, following the precedent set by spec 011's five sales settings and enums. | Bare integers for status and reason codes would be unreadable and untypable, contradicting `EntityStatus`, `PaymentTerms` and every other enum in the file. |

| **Editing `app/main.py`** (Principle III) — *unplanned* | The in-transit warehouse id is created by migration 008 and cannot be defaulted. Left at `0`, every departure posts the inbound half of the move against a warehouse that does not exist — stock misfiled rather than an error. | Validating inside `depart()` would fail at the truck, once goods were already committed. A default is impossible: the migration assigns the id. |
| **Editing `services/warehouse_service.py`** (Principle III) — *unplanned* | The in-transit location is an ordinary `warehouse` row so `stock_ledger.on_hand` reports its balance with no new code (R3). That also makes it selectable in every picker, where choosing it would misfile stock into the virtual location. | A `virtual` column on `warehouse` was rejected in R3 — the config setting already identifies the row, and a column change for one comparison is not warranted. |
| **Editing `models/sales.py`, `schemas/sales_order.py`, `sales_quote_service.py`** (Principle III) — *unplanned* | Removing `sales_order_detail.delivery` (finding F1). Written by this API, read by nothing — and while it existed it looked authoritative enough that this spec filtered on it, disabling delivery-order creation, the `delivered` write-back and coverage simultaneously. | Leaving it inert keeps the trap loaded for the next reader. Keeping the filter is what F1 measured as unusable: 0 of 910,891 rows. |

**Deliberately not done**: no notification channel, no print/ticket/PDF rendering, no itinerary
status enum, no ORM event listener, no new dependency, no standalone delivery-order creation.

**Footprint, measured rather than estimated** (2026-07-28): 17 new files and 17 edited — 12 app
files and 5 test files. Six of the twelve app edits were unplanned; each is justified above. This
plan originally claimed five, which is why the count is now stated as a measurement.

## Post-Design Constitution Re-Check

Re-run after Phase 1. **Result: PASS** — no new violation; the design removed one and exposed one
risk worth stating plainly.

| Principle | Post-design finding |
|---|---|
| I. Simplicity First | Held on the listener (R7), **overturned on the itinerary**. The claim that `cancelled`, `completed` and `departure_time` expressed the four states well enough did not survive review: FR-068 has to reconstruct exactly that derivation as a filter predicate. A stored `status` is now the simpler artefact. Recorded here rather than silently amended, because the original reasoning is what the review corrected. |
| III. Surgical Changes | **Wider than planned, and wider than the design predicted.** This is the first feature to edit a *shipped* service's business logic rather than extend it, and the footprint doubled during implementation: `app/main.py` (the in-transit startup guard, which the design left implicit), `warehouse_service.py` (excluding the virtual warehouse from pickers), and `models/sales.py` + `schemas/sales_order.py` + `sales_quote_service.py` (removing the `delivery` flag, which nothing predicted because the flag looked load-bearing until it was measured). Every one traces to a requirement or a finding; none is incidental tidying. Recorded here rather than quietly absorbed, because a Constitution Check that under-reports its own blast radius is worthless. |
| V. Reuse Over Rebuild | Held and strengthened during design. Reservations found a home in the existing `lot_serial_rqmt` table (R4), and making the in-transit location a warehouse row means `stock_ledger.on_hand` reports the in-transit balance with no new code (R3). Both were candidates for new tables before research. |
| VI. Async-First | Held. Row locks use `with_for_update()`; image work already offloads via `asyncio.to_thread`. |
| VII. Security by Default | Held, and the reason FR-044a exists. `app/main.py:45` mounts `images/` unauthenticated — correct for product photos, wrong for a customer's signature. POD files live in a separate private directory and are streamed only after a privilege check. |

**Two risks stated rather than buried.**

**The R5 coupling is the one that can cause silent loss.** Every other failure mode in this feature
is loud — a wrong status returns 409, a bad quantity returns 422. Removing the outbound entry at
sales-order confirmation without simultaneously subtracting reservations from the stock check fails
*quietly*: confirmations keep succeeding, and the shortfall only surfaces when the truck is loaded.
Phase 1 exists to land and review that change alone, and the quickstart's Scenario 4 asserts both
halves — that on-hand is unchanged *and* that availability dropped.

**The migration is destructive, and the audit behind it is incomplete.** The database went offline
during planning with three checks outstanding (R12): folio placeholders and duplicates,
`lot_serial_rqmt` occupancy, and delivery orders without a ship-to. Migration 007 set the standard
here — it stated real row counts and named the consequence it accepted (reassigned refund folios no
longer matching printed receipts). `008` must do the same before it runs. Separately, `008` cancels
any delivery genuinely in flight at cutover; those must be re-raised from their sales orders, which
works because cancelled delivery orders do not count as coverage.

**Agent context (`CLAUDE.md`) updated**: the `<!-- SPECKIT START -->` / `<!-- SPECKIT END -->` block
now points at this plan.
