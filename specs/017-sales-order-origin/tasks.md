---
description: "Task list for 017-sales-order-origin"
---

# Tasks: Sales Order Origin

**Input**: Design documents from `/specs/017-sales-order-origin/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/api.md](./contracts/api.md)

**Tests**: **Mandatory, and written first.** The constitution (v1.2.0) removed the optional-tests
carve-out the upstream template still describes: services, schemas and filters carrying branching
logic require `tests/unit/` coverage, endpoints require `tests/api/`, and tests are written before
the implementation they pin.

**Organization**: grouped by the four user stories in [spec.md](./spec.md). All four are P1 — they
are the four halves of one small change, not a priority ladder — so the MVP is US1 + US2 (a fact
that exists and is written on every path), with US3 (reading and filtering it) and US4 (leaving
history alone) completing it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1–US4 from spec.md

---

## Phase 1: Setup

**Purpose**: nothing to scaffold. This feature adds no module, directory or dependency; it is
entirely edits to existing files plus one migration pair.

- [X] T001 Confirm the branch and baseline are green: `git branch --show-current` reports `017-sales-order-origin`, and `uv run pytest -q` plus `uv run ruff check app/ migrations/ tests/` both pass before anything changes

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the vocabulary and the column. Every user story reads or writes these, so nothing else
can start until they exist.

- [X] T002 Add `OrderOrigin(IntEnum)` to `app/enums.py` immediately after `FulfillmentType` (ends line 333), with `POINT_OF_SALE = 0` and `BACK_OFFICE = 1`, and a docstring in the house style: name the column it backs (`sales_order.origin`), say why the register takes 0 (the ordinary capture surface, as `FulfillmentType.PICKUP` does), say that `NULL` is not a member, and cite #209
- [X] T003 Write `migrations/020_sales_order_origin.sql` following `017_sales_order_fulfillment_intent.sql` exactly in structure: title line with the issue, the problem, a `MEASURED against the deployment database 2026-09-13` table (335,816 sales orders; 6,261 from a quote; 0 with a NULL point_sale; 21 distinct registers; `fulfillment_intent` recorded on 7 rows), a `WHY THIS COLUMN SHIPS EMPTY` section stating that backfill was rejected (issue #209 option A), the value scale, and the statement `ALTER TABLE sales_order ADD COLUMN IF NOT EXISTS origin SMALLINT NULL COMMENT '0=point of sale 1=back office; NULL=not recorded (#209)' AFTER fulfillment_intent`
- [X] T004 [P] Write `migrations/020_sales_order_origin_rollback.sql` following `017_..._rollback.sql`: what it drops, a `WHAT IS LOST` section (every recorded origin — there is no other copy), the `SELECT COUNT(*) FROM sales_order WHERE origin IS NOT NULL` to run first, the note that the application code must be reverted with it, and `ALTER TABLE sales_order DROP COLUMN IF EXISTS origin`
- [X] T005 Map the column on `SalesOrder` in `app/models/sales.py` at line 96, directly after `fulfillment_intent`: `origin: Mapped[int | None] = mapped_column(SmallInteger)`, preceded by a `#`-comment in the style of the one above `fulfillment_intent` — which workflow raised the order, `NULL` means not recorded (every row predating migration 020 and every order whose client does not say), and that it is not `point_sale`, which says which register, not which workflow
- [X] T006 Run `uv run pytest tests/unit/test_model_schema.py -q` and confirm it passes — the mapped column is backed by migration 020. It fails if T003 and T005 disagree on the name or nullability

**Checkpoint**: the fact can be stored. Nothing writes or reads it yet.

---

## Phase 3: User Story 1 — an order records the workflow that raised it (P1)

**Goal**: a client creating an order can declare which workflow raised it, and nothing is inferred
when it does not.

**Independent test**: create one order declaring each workflow and one declaring nothing; read all
three back; two orders raised on the same register from different workflows stay distinguishable.

### Tests (write first, watch them fail)

- [X] T007 [P] [US1] Add `class TestTheOrigin` to `tests/unit/test_sales_order_service.py`, mirroring `TestTheFulfilmentIntent` (line 866): the vocabulary has exactly the two members; the register is 0 and the back office is 1; `SalesOrderCreate()` leaves `origin` at `None` rather than defaulting; `SalesOrderCreate(origin=2)` raises `ValidationError`
- [X] T008 [P] [US1] In the same class, add the source-text guard the `fulfillment_intent` suite uses (`test_the_creation_path_does_not_infer_it_from_the_address`, line 906): `inspect.getsource(sales_order_service.create_order)` contains `data.origin` and does **not** mention `point_sale` or `customer` in the `origin` expression — FR-004, no inference
- [X] T009 [P] [US1] Add to `tests/integration/test_sales_and_delivery_flow.py`: two `POST /api/v1/sales-orders` on the same register, one declaring back office and one declaring point of sale, reread by `GET`, asserting the two values survive — the test that would have failed before this feature existed because the two rows were identical

### Implementation

- [X] T010 [US1] Add `origin` to `SalesOrderCreate` in `app/schemas/sales_order.py` (class at lines 95–122), typed `OrderOrigin | None` with `Field(default=None, description=...)` and a `#`-comment above it, matching how `fulfillment_intent` is declared there (lines 111–122)
- [X] T011 [US1] Write it in `create_order` in `app/services/sales_order_service.py` (function at line 704), inside the `SalesOrder(...)` construction next to `fulfillment_intent` (lines 765–770): `origin=(None if data.origin is None else int(data.origin))`, with a comment saying why it is not derived from `point_sale` — a back-office user's register is the same register a walk-in sale carries (#209)
- [X] T012 [US1] Add `origin` to `SalesOrderResponse` (lines 152–191) in `app/schemas/sales_order.py`, declared like `fulfillment_intent` there (lines 175–185)
- [X] T013 [US1] Register the three descriptions in `tests/unit/test_field_descriptions.py` `DESCRIBED` (lines 28–38) as `(component, field, phrase)` tuples, following the `fulfillment_intent` entries at 35–37

**Checkpoint**: US1 passes end to end. An order can record its workflow and report it back.

---

## Phase 4: User Story 2 — a converted quote lands in the back office (P1)

**Goal**: `POST /sales-quotes/{id}/convert` records the back-office workflow itself, and
"converted from a quote" stays a separate fact.

**Independent test**: convert a quote; the resulting order reports back office with nothing supplied
by the caller, and names the quote it came from.

### Tests (write first)

- [X] T014 [P] [US2] Add to `tests/integration/test_sales_quote_list.py` (or alongside the existing conversion flow in `tests/integration/test_credit_hold.py:168-185`, whichever fixture set fits): convert a quote through `POST /api/v1/sales-quotes/{id}/convert` and assert the resulting order reports `origin` = back office **and** a non-null `sales_quote`, with no body sent
- [X] T015 [P] [US2] Add a unit guard to `tests/unit/test_sales_quote_service.py`: `inspect.getsource(sales_quote_service.convert_to_order)` contains `origin=`, so the stamp cannot be dropped silently in a later refactor — the failure mode the issue calls load-bearing

### Implementation

- [X] T016 [US2] In `app/services/sales_quote_service.py`, add `origin=int(OrderOrigin.BACK_OFFICE)` to the `SalesOrder(...)` construction in `convert_to_order` (lines 545–583), directly after the `fulfillment_intent=None` block (579–582), with a comment explaining the asymmetry: a quote has no fulfilment intent to carry, but a converted order does belong to a workflow, and this path has no request body for a client to say so (#209)

**Checkpoint**: every converted order records an origin, with no client change anywhere.

---

## Phase 5: User Story 3 — the list separates the two inboxes (P1)

**Goal**: the workflow is on the list row, and the list can be asked for one workflow or for
everything except one.

**Independent test**: list orders from both workflows and confirm each row reports its own; filter
by selection and by exclusion and confirm each returns what the truth table in
[data-model.md](./data-model.md) says.

### Tests (write first)

- [X] T017 [P] [US3] Add to `tests/integration/test_sales_and_delivery_flow.py`: a page of orders from both workflows where every row carries `origin` and `sales_quote`, read from the list response alone — no per-row `GET`
- [X] T018 [P] [US3] Add the selection test: `?origin=1` returns back-office orders only, and omits both register sales and orders that recorded nothing (FR-011)
- [X] T019 [P] [US3] Add the exclusion test, the one that fails on a bare `!=`: seed an order recording nothing, one recording point of sale and one recording back office; `?exclude_origin=1` returns the first two and omits the third (FR-012)
- [X] T020 [P] [US3] Add to `tests/api/test_sales_orders.py`, following `test_list_passes_the_point_sale_filter_through` (line 434) and `test_list_leaves_point_sale_unset_when_not_asked_for` (line 446): both new parameters forward to `list_orders` as kwargs, and are absent when not asked for. Add a 422 case for a value outside the vocabulary
- [X] T021 [P] [US3] Extend `tests/unit/test_list_query_counts.py::TestSalesOrders` coverage or assert the existing count is unchanged — the two new summary fields are mapped columns and must not add a query per row (research R4)

### Implementation

- [X] T022 [US3] Add `origin` and `sales_quote` to `SalesOrderSummary` in `app/schemas/sales_order.py` (lines 194–220) as plain fields — both are mapped columns already loaded, so no helper and no change to `attach_summary_totals` or `attach_customer_names`
- [X] T023 [US3] Add `origin` and `exclude_origin` to the `list_orders` signature in `app/services/sales_order_service.py` (lines 783–798), both `OrderOrigin | None = None`, and add their clauses beside the `point_sale` clause (lines 822–824) using the existing `both()` helper: selection is `SalesOrder.origin == int(origin)`; **exclusion is `or_(SalesOrder.origin.is_(None), SalesOrder.origin != int(exclude_origin))`**, with a comment stating that the `IS NULL` arm is what keeps the 335,816 unrecorded rows on the register's list (`or_` is already imported for the `mine` clause)
- [X] T024 [US3] Add the two `Query(None)` parameters to `list_sales_orders` in `app/api/v1/endpoints/sales_orders.py` (route at lines 38–69) and forward them to the service, giving `exclude_origin` a `description=` — the house rule that a parameter whose name does not carry its own meaning gets one (as `missing_price_list` does in `products.py:43-45`)

**Checkpoint**: both inboxes are expressible from the list endpoint.

---

## Phase 6: User Story 4 — history is untouched (P1)

**Goal**: orders raised before this feature keep behaving exactly as they did, and the value cannot
be changed after creation.

**Independent test**: an order created without an origin reads, lists and updates exactly as before;
a `PUT` carrying `origin` changes nothing.

### Tests (write first)

- [X] T025 [P] [US4] Add to `tests/integration/test_sales_and_delivery_flow.py`: an order created with no origin reports `null`, and still confirms, delivers, updates and lists as it does today
- [X] T026 [P] [US4] Add the immutability test: create an order with `origin` back office, `PUT` it with `origin` point of sale, assert 200 and that the stored value is still back office; and that a `PUT` carrying `origin` on an order that recorded nothing leaves it `null` (FR-005, research R3)

### Implementation

- [X] T027 [US4] No code change: `SalesOrderUpdate` (lines 125–149) is deliberately **not** touched, and no `model_config` is added anywhere. Confirm T026 passes against the untouched schema, and record in the test's docstring that the guarantee comes from Pydantic's default `extra='ignore'` rather than from an explicit refusal — otherwise the next reader adds one

**Checkpoint**: all four stories pass. The feature is complete.

---

## Phase 7: Polish & cross-cutting

- [X] T028 [P] Add the `origin` row to the `sales_order` section of `docs/data-dictionary.md`, directly after `fulfillment_intent` (line 667), in the same sentence shape: the enum and its values, `NULL` means not recorded (every row predating migration 020), and what it is not (`point_sale`, which says which register). Required by `tests/unit/test_data_dictionary.py:184`, not optional
- [X] T029 [P] Add the `[Unreleased]` entry to `CHANGELOG.md` under `### Added`, in the house style: bolded lede, a measured number, the issue number, and a closing bullet naming the tests that pin it
- [X] T030 Run the full gate: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`, both clean
- [X] T031 Walk [quickstart.md](./quickstart.md) end to end and confirm each success criterion's command produces the stated outcome; correct the quickstart if a command or its output drifted during implementation

---

## Dependencies

```
Phase 1 (T001)
  └─> Phase 2 (T002 → T003, T004, T005 → T006)      the enum and the column
        ├─> Phase 3  US1  T007-T009 → T010-T013      declaring it on creation
        ├─> Phase 4  US2  T014-T015 → T016           stamping it on conversion
        ├─> Phase 5  US3  T017-T021 → T022-T024      reading and filtering it
        └─> Phase 6  US4  T025-T026 → T027           leaving history alone
              └─> Phase 7 (T028-T031)
```

- **US1, US2, US3 and US4 are independent of each other** once Phase 2 lands. US3's tests seed rows
  directly, so it does not wait on US1's endpoint work; US2 touches a different service entirely.
- Within each story, every test task precedes its implementation task, per the constitution.
- T005 depends on T003 only in the sense that T006 checks them against each other; they can be
  written in either order.

## Parallel execution

- **Phase 2**: T004 (rollback) is `[P]` against T003 and T005.
- **Phase 3**: T007, T008, T009 are all `[P]` — three different files.
- **Phase 5**: T017–T021 are all `[P]`; the implementation trio T022–T024 is sequential only because
  T024 forwards what T023 accepts.
- **Phase 7**: T028 and T029 are `[P]` — different files, no shared state.
- **Across stories**: after Phase 2, US1's test trio, US2's pair and US3's five can all be written
  in parallel; nothing in one story's tests reads another story's implementation.

## Implementation strategy

1. **Phase 2 is the whole foundation** — enum, migration pair, mapped column. Six tasks, and the
   feature cannot start without them.
2. **MVP = US1 + US2**: the fact exists and is written on every path that creates an order. At that
   point nothing is lost going forward, which is the part that cannot be recovered later — an order
   raised before the write path lands can never be told apart afterwards.
3. **US3 makes it useful** and carries the one subtle defect risk in the feature (the `NULL` arm of
   the exclusion filter). Its tests are the ones worth writing carefully.
4. **US4 is mostly proof**: one deliberate non-change and the tests that prove it.
5. **Phase 7 is not optional garnish** — T028 is required by an existing unit test, and T030 is the
   constitution's linting gate.

## Task count

31 tasks: 1 setup, 5 foundational, 7 US1, 3 US2, 8 US3, 3 US4, 4 polish. 16 of them are test tasks,
which is the ratio the constitution's testing rule produces for a change whose whole value is
behavioural.

---

## Execution notes (2026-09-13)

All 31 tasks executed on branch `017-sales-order-origin`. Two are worth recording because their
outcome was "nothing to write", which a checked box does not convey:

- **T021** needed no new test. `tests/unit/test_list_query_counts.py::TestSalesOrders` already pins
  the per-page query count, and it passes unchanged — which is the finding: `origin` and
  `sales_quote` are mapped columns already loaded by the list query, so the two new summary fields
  cost nothing. An implementation that had reached for an attach helper would have failed it.
- **T027** was a deliberate non-change, as planned. `SalesOrderUpdate` was not touched and no
  `model_config` was added anywhere; the immutability guarantee comes from Pydantic's default
  `extra='ignore'`, and the integration test says so in its docstring so that the next reader does
  not "fix" it by adding an explicit refusal.

One correction made during implementation: **T031 rewrote the quickstart's test selectors**, which
had been written before the tests existed and named patterns (`-k "origin and filter"`,
`-k "origin and immutable"`) that matched nothing. The file now carries the commands that run, with
their recorded output.

Gate at completion: `uv run ruff check app/ migrations/ tests/` clean, `uv run pytest -q` at
**2499 passed** — 24 more than the 2,475 baseline.
