# Implementation Plan: Sales Order Origin

**Branch**: `017-sales-order-origin` | **Date**: 2026-09-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/017-sales-order-origin/spec.md`

## Summary

Record on `sales_order` which workflow raised it (issue #209), so a back-office order and a register
sale stop being indistinguishable. One nullable `SMALLINT` column added by migration 020, one
two-member `IntEnum`, the field accepted on creation and nowhere else, exposed on the order and on
the list row alongside `sales_quote`, and two new filter parameters — one to select a workflow, one
to exclude it.

The whole change is small and almost entirely additive. Two parts of it carry the risk:

1. **`convert_to_order` must stamp the value itself.** It builds `SalesOrder(...)` directly and
   takes no request body, so no client can declare anything on that path. Missed, it leaves all
   6,261-and-growing quote-converted orders permanently unrecorded — the population a back-office
   list most needs.
2. **The exclusion filter must spell out the `NULL` arm.** `origin != 1` evaluates to `NULL` for
   the 335,816 rows that record nothing, and a `WHERE` keeps only true — so the obvious spelling
   silently empties the register's list. It must be
   `or_(SalesOrder.origin.is_(None), SalesOrder.origin != value)`.

Everything else follows `fulfillment_intent` (migration 017, issue #170) line for line, which is
also the honest precedent for adoption: it is recorded on **7** of 335,816 rows, because clients
have not been changed to send it. This column will look the same for a while, and the design
assumes it.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 — unchanged. Nothing added.

**Storage**: MariaDB via aiomysql. One numbered migration, `migrations/020_sales_order_origin.sql`,
with its rollback. No Alembic; `app/db/migrate.py` discovers `migrations/NNN_*.sql` in numeric
order, so creating the file is the whole registration step.

**Testing**: pytest + pytest-asyncio. Three existing layers all apply — `tests/unit/` for the schema
and service behaviour, `tests/api/` for the router's parameter forwarding (mocked service), and
`tests/integration/` for the end-to-end facts that matter most here (a real schema, real services,
SQLite).

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — one column, one enum, four schemas, two services, one router.

**Performance Goals**: Unchanged. The list endpoint must stay at its current fixed query count per
page; both new summary fields are mapped columns already loaded, so no helper and no extra query
(research R4). No index is added (data-model).

**Constraints**:
- `NULL` means *not recorded* and is never written, derived, defaulted or inferred (FR-002, FR-004).
- No existing row is written by the migration (FR-014). 335,816 rows, measured 2026-09-13.
- The value is fixed at creation (FR-005): the field is added to `SalesOrderCreate` only, and
  `SalesOrderUpdate` is not touched.
- The exclusion filter must return rows recording nothing (FR-012).
- Constitution VIII: `uv run ruff check app/ migrations/ tests/` clean, 100-column lines.

**Scale/Scope**: 1 column, 1 enum (2 members), 4 schema classes, 2 services, 1 router, 1 migration
pair, 1 dictionary row, 1 changelog entry, and the tests for all of it. ~11 source files.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. No violations; Complexity Tracking is
empty.*

| Principle | Verdict |
|---|---|
| **I. Simplicity First** | Pass. Two enum members, two query parameters, no index, no `CHECK` constraint, no service-layer refusal for an update path that already ignores the field (R3). Each rejected alternative is recorded in research.md with its reason. |
| **II. Think Before Coding** | Pass. The two decisions the issue left open were put to the owner before drafting — backfill (option A) and how quote conversion is recorded (two members, not three) — and the filter's exclusion semantics were raised as the spec's one clarification rather than guessed. |
| **III. Surgical Changes** | Pass. Nothing adjacent is improved: `point_sale`'s equality-only filter stays as it is, `SalesOrderUpdate` keeps its permissive `extra` behaviour, and the conversion endpoint's 422-without-a-register defect is recorded as out of scope rather than fixed in passing. |
| **IV. Goal-Driven Execution** | Pass. Seven success criteria, each with a runnable check in [quickstart.md](./quickstart.md); tests come before implementation in the task order. |
| **V. Reuse Over Rebuild** | Pass. No new model, service or dependency. `sales_quote` is reused for "what preceded this order" rather than a third enum member; the summary reuses `from_attributes` rather than a new attach helper. |
| **VI. Async-First** | Pass. No new route handler and no new query path; the two filter clauses are added to the existing `list_orders` statement builder. |
| **VII. Security by Default** | Pass. No new endpoint, no change to any privilege dependency (`_READ`/`_CREATE`/`_UPDATE` on the sales-order router are untouched), and `origin` carries no user data. |
| **VIII. Ruff Compliance** | Pass — verified as part of the quickstart's prerequisites. |
| **Testing (workflow)** | Pass, and note it is not optional here: the service, the schema and the filter all carry branching logic, so `tests/unit/` and `tests/integration/` coverage is required, written first. |

## Project Structure

### Documentation (this feature)

```text
specs/017-sales-order-origin/
├── spec.md              # what and why (committed 5ce6f21)
├── plan.md              # this file
├── research.md          # Phase 0 — measured baseline and seven decisions
├── data-model.md        # Phase 1 — the column, the vocabulary, the filter truth table
├── contracts/api.md     # Phase 1 — the four endpoints that change shape
├── quickstart.md        # Phase 1 — one runnable check per success criterion
├── checklists/
│   └── requirements.md  # spec quality gate, 16/16
└── tasks.md             # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
app/
├── enums.py                              # + OrderOrigin(IntEnum), after FulfillmentType (:333)
├── models/sales.py                       # + SalesOrder.origin, after fulfillment_intent (:95)
├── schemas/sales_order.py                # + origin on Create (:95-122), Response (:152-191),
│                                         #   Summary (:194-220); Update (:125-149) untouched
│                                         # + sales_quote on Summary
├── services/
│   ├── sales_order_service.py            # create_order (:704) writes it; list_orders (:783)
│   │                                     #   gains origin / exclude_origin clauses
│   └── sales_quote_service.py            # convert_to_order (:519) stamps BACK_OFFICE
└── api/v1/endpoints/sales_orders.py      # + two Query params on the list route (:38-69)

migrations/
├── 020_sales_order_origin.sql            # ADD COLUMN, no backfill
└── 020_sales_order_origin_rollback.sql   # DROP COLUMN, states what is lost

docs/
└── data-dictionary.md                    # + the `origin` row in the sales_order section (:667)

tests/
├── unit/
│   ├── test_sales_order_service.py       # + TestTheOrigin, mirroring TestTheFulfilmentIntent (:866)
│   ├── test_field_descriptions.py        # + DESCRIBED tuples (:28-38)
│   └── test_list_query_counts.py         # existing guard — must stay at its current count
├── api/test_sales_orders.py              # + the two filters forward to the service (:174, :434)
└── integration/
    ├── test_sales_and_delivery_flow.py   # + origin end-to-end, list row, immutability
    └── test_sales_quote_list.py          # + conversion stamps BACK_OFFICE

CHANGELOG.md                              # + an [Unreleased] entry
```

**Structure Decision**: the existing layout, unchanged. This feature adds no module and no
directory; every file above already exists except the migration pair.

## Complexity Tracking

No constitution violations. Nothing to justify.
