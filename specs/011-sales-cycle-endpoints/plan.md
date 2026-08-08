# Implementation Plan: Sales Cycle Endpoints

**Branch**: `011-sales-cycle-endpoints` | **Date**: 2026-07-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-sales-cycle-endpoints/spec.md`

## Summary

Deliver the transactional sales cycle — quotes, orders, payments, cash sessions, refunds, credit
notes and the two supervisor tools — on top of the SQLAlchemy models that already exist for every
table involved. No new model and no column change: this feature is schemas, services, routers and
tests, plus one migration that adds a unique index on `(facility, serial)`.

Three things drive the technical approach:

1. **Documents have a lifecycle, catalog rows do not.** Every existing service in this repository is
   CRUD over a single table. These services own multi-step state transitions (confirm, cancel,
   reverse) that touch several tables in one transaction. The transition logic lives in the service
   layer, and each transition is one commit.
2. **Three document types share the same mechanics.** Folio assignment, total computation, stock
   ledger posting and audit entries are identical across orders, quotes and refunds. They become
   four small shared helper modules rather than being written three times.
3. **The authenticated user is currently under-described.** Every document needs the caller's
   employee, and orders need their point of sale. Both are already loaded by `get_current_user` and
   simply not surfaced — so this is an additive change to `CurrentUser`, not a token change, and no
   session is invalidated.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI (ASGI), SQLAlchemy 2.0 async (`Mapped`/`mapped_column`), Pydantic
v2, aiomysql. No new dependency.

**Storage**: MariaDB 10.11. All tables already mapped in `app/models/sales.py`, `core.py`,
`inventory.py`, `incidence.py`; no column is added or changed. **One migration** —
`007_document_serial_unique.sql` adds the unique index on `(facility, serial)` that SC-005 needs
and corrects the legacy rows blocking it (research R1).

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`, following the existing
`tests/api/` pattern (FastAPI `dependency_overrides` over mocked services, no live database) and
`tests/unit/` for service-level logic.

> **A third layer was added after two 500s shipped through this one.** #149 and #154 were both
> endpoint bugs whose endpoints had passing tests: mocking the service means the service never runs,
> and mocking `db.execute` means the SQL never reaches a database. `tests/integration/` now drives
> the API with a real session over in-memory SQLite (`aiosqlite`, dev dependency), schema built from
> the model metadata and seeded once into a template file each test copies. **"No live database"
> still holds** — nothing has to be provisioned and the suite runs on a bare checkout. The two
> earlier layers keep their jobs: `tests/api/` pins status codes and authorisation cheaply, and
> `tests/unit/` pins decision rules without I/O. What the new layer adds is the assertion that the
> code path runs at all.

**Target Platform**: Linux server, ASGI (uvicorn)

**Project Type**: Web service — REST API under `/api/v1/`

**Performance Goals**: No numeric target. The spec sets none and the constitution fixes the stack;
the relevant constraint is avoiding N+1 queries on list endpoints, for which
`app/services/fk_expansion.py` already exists.

**Constraints**: Every route `async def`; all DB access through `AsyncSession`; ruff clean at 100
columns; every endpoint gated by `require_privilege`.

**Scale/Scope**: 8 user stories (P1–P5), ~75 functional requirements, 6 new routers, 6 new
services, 4 shared helper modules, ~6 new schema modules. Sized for phased delivery — see
[Delivery Phases](#delivery-phases).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Simplicity First | ✅ Pass | No speculative capability. POS endpoints were explicitly dropped (FR-054). The unreachable legacy balance-settlement path is specified as *must not be implemented* (FR-064) rather than built defensively. |
| II. Think Before Coding | ✅ Pass | 8 clarifications recorded in the spec; every open decision resolved before this plan. Research below states each choice with its rejected alternatives. |
| III. Surgical Changes | ⚠️ Justified | Three existing files are edited: `app/core/deps.py` (extend `CurrentUser`), `app/enums.py` (add 5 enums), `app/core/config.py` (add 5 settings), plus router registration. Each traces to a requirement — see Complexity Tracking. |
| IV. Goal-Driven Execution | ✅ Pass | Each user story is an independently testable slice; delivery phases below define the verification for each. |
| V. Reuse Over Rebuild | ⚠️ Justified | **Zero new models** — every table is already mapped. 6 new services and 4 helper modules are justified in Complexity Tracking. Existing `fk_expansion`, `references`, `ListResponse` and the privilege dependency are reused as-is. |
| VI. Async-First | ✅ Pass | All handlers `async def`; all access via `AsyncSession`. Row locking (research R1, R2) uses `with_for_update()`, which is async-safe. |
| VII. Security by Default | ✅ Pass | FR-001 maps every route to a system object and access right. No public endpoint in this feature. |
| VIII. Ruff Compliance | ✅ Pass | Rule set E, F, I, UP at 100 columns; verified by `uv run ruff check app/ migrations/ tests/`. |

**Testing gate (Constitution v1.1.0)**: tests are **REQUIRED** — this feature introduces API
endpoints. Every router ships with a `tests/api/` file covering happy path, 404, 401 and the
resource-specific failures (409/422). State-transition rules get `tests/unit/` service tests.

**Gate result**: PASS with two justified deviations, both recorded in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/011-sales-cycle-endpoints/
├── plan.md              # This file
├── research.md          # Phase 0 output — 10 technical decisions
├── data-model.md        # Phase 1 output — entities, derived values, state machines
├── quickstart.md        # Phase 1 output — end-to-end validation scenarios
├── contracts/           # Phase 1 output — endpoint contracts per resource
│   └── README.md
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
migrations/
└── 007_document_serial_unique.sql    # NEW (+ rollback): unique (facility, serial)

app/
├── enums.py                          # EDIT: + PaymentTerms, PaymentMethod, PaymentType,
│                                     #         Priority, TransactionType, SourceType
├── core/
│   ├── config.py                     # EDIT: + 5 sales defaults (research R8)
│   └── deps.py                       # EDIT: CurrentUser gains employee_id, point_sale_id,
│                                     #         cash_drawer_id (research R9)
├── schemas/
│   ├── sales_quote.py                # NEW
│   ├── sales_order.py                # NEW
│   ├── customer_payment.py           # NEW
│   ├── customer_refund.py            # NEW
│   ├── cash_session.py               # NEW
│   └── credit_note.py                # NEW
├── services/
│   ├── documents.py                  # NEW — shared: folio assignment, editability guard
│   ├── totals.py                     # NEW — shared: line/document money computation
│   ├── stock_ledger.py               # NEW — shared: lot_serial_tracking posting + on-hand
│   ├── incidences.py                 # NEW — shared: audit entry writer
│   ├── sales_quote_service.py        # NEW
│   ├── sales_order_service.py        # NEW
│   ├── customer_payment_service.py   # NEW
│   ├── customer_refund_service.py    # NEW
│   ├── cash_session_service.py       # NEW
│   └── credit_note_service.py        # NEW
└── api/v1/
    ├── router.py                     # EDIT: register 6 routers
    └── endpoints/
        ├── sales_quotes.py           # NEW
        ├── sales_orders.py           # NEW
        ├── customer_payments.py      # NEW
        ├── customer_refunds.py       # NEW
        ├── cash_sessions.py          # NEW
        └── credit_notes.py           # NEW

tests/
├── api/                              # NEW: one file per router (constitution: required)
│   ├── test_sales_quotes.py
│   ├── test_sales_orders.py
│   ├── test_customer_payments.py
│   ├── test_customer_refunds.py
│   ├── test_cash_sessions.py
│   └── test_credit_notes.py
└── unit/                             # NEW: state transitions and money math
    ├── test_totals.py
    ├── test_documents.py
    ├── test_stock_ledger.py
    ├── test_sales_order_service.py
    └── test_customer_refund_service.py
```

**Structure Decision**: The existing single-project layout is kept unchanged —
`app/{enums,core,schemas,services,api/v1/endpoints}` with `tests/{api,unit}`. Every new file follows
the naming already used by `product_price_service.py` / `product_prices.py`. The only structural
addition is four shared helper modules under `app/services/`, which sit alongside the existing
shared modules `fk_expansion.py` and `references.py` rather than introducing a new package.

## Delivery Phases

The spec covers nine source-document sections in one feature. These phases follow the spec's
P1–P5 priorities so each lands as a working, testable slice. `/speckit-tasks` should preserve this
ordering.

| Phase | Delivers | Stories | Verify |
|---|---|---|---|
| **0 — Foundation** | Enums, config settings, `CurrentUser` extension, the 4 shared helpers | — | Unit tests for totals, folio assignment, ledger posting; `/auth/me` still green |
| **1 — Core revenue** | Sales orders (incl. product lookup), customer payments + applications | US1, US2 | A credit order can be opened, confirmed, paid and reversed; stock moves and comes back |
| **2 — Counter** | Cash sessions (open, current, close) | US3 | A session opens, refuses a second on the same drawer, and closes with counts |
| **3 — Returns** | Sales quotes + conversion, customer refunds | US4, US5 | A paid order is refunded to cash or credit note; a quote converts to an order |
| **4 — Supervisory** | Credit notes, payment verification, payments editor | US6, US7, US8 | A credit note is redeemed and reversed; a misapplied payment is corrected |

Phase 0 is a hard prerequisite for everything. Phases 2–4 depend on Phase 1. Phase 3's refunds
depend on Phase 2 (confirmation requires an open cash session).

## Complexity Tracking

> Filled because the Constitution Check flagged two justified deviations.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| **6 new services** (Principle V) | One service per document type, matching the existing one-service-per-resource convention (`product_price_service`, `facility_service`, …). Each owns a distinct lifecycle. | A single `sales_service.py` would exceed a thousand lines covering five unrelated lifecycles, and would break the file-naming convention every existing endpoint follows. |
| **4 shared helper modules** (Principle I — "no abstractions for single-use code") | Each is used by **three or more** callers, so none is single-use: folio assignment by orders/quotes/refunds; totals by orders/quotes/refunds/credit notes; the stock ledger by order confirm, order cancel and refund confirm; incidences by payment reversal and payment rejection. | Inlining them would write folio-under-lock three times and the money math four times. Divergent copies of folio locking would silently break SC-005; divergent rounding would break SC-004. |
| **Editing `app/core/deps.py`** (Principle III) | FR-002 and FR-004a require the caller's employee and point of sale. `get_current_user` already loads the `User` and its eager-loaded `settings`; the values are simply not exposed. | Re-querying the user inside every service would repeat a database read already performed on every request. Putting them in the JWT would invalidate every live session for no benefit. |
| **Editing `app/enums.py` and `config.py`** (Principle III) | Spec Assumptions 5 and 6 — the legacy `WebConfig` values and the constants documented in `docs/constants.md` but absent from code. | Bare integers for payment terms and transaction types would be unreadable and untypable, contradicting the precedent set by `EntityStatus`, `AddressType` and `FiscalCertificationProvider`. |

**Deliberately not done**: no new model, no new dependency, no POS endpoint, no document
rendering. See the spec's Out of Scope. One migration is included, and it adds an index and
corrects data — it changes no column.

## Post-Design Constitution Re-Check

Re-run after Phase 1. **Result: PASS** — no new violation, and one deviation the design removed.

| Principle | Post-design finding |
|---|---|
| I. Simplicity First | Held. Design added no capability beyond the spec. Redemption of a credit note was resolved to **no new route** (it reuses payment application), and the payments editor to **no new routes** — both shrank the surface rather than growing it. |
| III. Surgical Changes | Held, and narrowed: research R9 established that `CurrentUser` can be extended additively from data `get_current_user` already loads, so no JWT change and no test-fixture breakage. |
| V. Reuse Over Rebuild | Held and strengthened. Confirmed **zero new models and zero migrations**: every table is mapped. The four helper modules each have 3+ callers. |
| VI. Async-First | Held. The row locks in R1/R2 use `with_for_update()` inside the existing `AsyncSession`. |
| VII. Security by Default | Held. Every route in contracts carries a system object and access right; the two supervisor tools reuse (100)/(108) rather than inventing objects. |

**The R1 risk is closed.** SC-005 was originally guarded only by the application-level facility
row lock, because no unique index existed on `(facility, serial)`. The index is now part of this
feature (`migrations/007_document_serial_unique.sql`), so a regression in the locking code surfaces
as a constraint violation instead of a silently duplicated folio. The lock stays: it makes
concurrent confirmations queue rather than fail, and the index is the backstop for any future code
path that forgets it.

The production data audit R1 called for was done rather than deferred, and it found real
violations — see R1 for what they were and how the migration corrects them.

**Agent context (`CLAUDE.md`) updated**: the `<!-- SPECKIT START -->` / `<!-- SPECKIT END -->`
markers were reinstated at the end of the file, pointing at this plan. They had been removed in
commit `110b1eb` because the block still referenced `specs/010-product-merge-integrity/plan.md` long
after that feature shipped — the objection was the stale path, not the mechanism. The path is
refreshed on every plan so it cannot go stale again.
