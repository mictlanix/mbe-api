# Tasks: Price List Retirement

**Input**: Design documents from `/specs/015-price-list-retirement/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/price-list-retirement.md](./contracts/price-list-retirement.md)

**Tests**: Mandatory, and written first. The template's "tests are OPTIONAL" boilerplate is
overridden by the project constitution v1.2.0 — every task with observable behaviour ships with a
test, and the test is confirmed failing before the implementation task that makes it pass.

**Organization**: Grouped by user story. US1 and US2 are both P1 and both land in
`delete_price_list`; US2 depends on US1 having established the function's shape, so they are
sequential rather than parallel. US3 is independent of both and could ship first or last.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1, US2, US3 — maps to the user stories in [spec.md](./spec.md)

## Path Conventions

Existing layout, unchanged: `app/` for source, `tests/api|unit|integration/` for tests. See the
Source Code tree in [plan.md](./plan.md).

---

## Phase 1: Setup

**Purpose**: Establish the baseline the rest of the work is measured against.

- [x] T001 Confirm the baseline is green before touching anything. **Measured 2026-08-29**: `uv run pytest -q` → 2027 passed, 15 skipped. `uv run ruff check app/ migrations/ tests/` → clean. `uv run mypy app/` → 170 errors in 48 files. `uv run ruff format --check` repo-wide → 51 files would be reformatted, which is the *pre-existing* quote-style contradiction of GH #96 and not something this feature fixes; the four files it touches are format-clean and must stay so. Every later gate is measured against these numbers, not against zero.

---

## Phase 2: Foundational

**Purpose**: None. This phase is deliberately empty.

Everything this feature needs already exists: `referencing_columns`, `find_blocking_references` and
`assert_not_referenced` in `app/services/references.py` are used exactly as they stand, and there is
no schema change, no migration, and no new module. Each user story below is a self-contained edit to
`app/services/price_list_service.py` and its callers.

---

## Phase 3: User Story 1 — Retiring a list that was actually priced (P1)

**Goal**: A price list's own prices stop blocking its deletion and are deleted with it.

**Independent test**: Price several products in a list, retire it, confirm the list and its prices
are gone and that the same products' prices in other lists are untouched.

### Tests first

- [x] T002 [P] [US1] Write `tests/unit/test_price_list_service.py` with the statement-level tests for the cascade, following the `_merge_statements` pattern in `tests/unit/test_product_service.py` — capture what `delete_price_list` issues against a mocked `AsyncSession` and assert on the SQL rather than on the mock's call list: (a) exactly one `DELETE FROM product_price`, (b) no `DELETE` against any table outside `_DELETE_CASCADE`, (c) the `WHERE` names the mapped column `list`, not `price_list`, (d) the final statement deletes the list itself. Confirm they fail.
- [x] T003 [P] [US1] In the same file, add `test_exempt_and_cascade_are_the_same_set` — assert that the `exempt` kwarg `delete_price_list` passes to `assert_not_referenced` **is** `_DELETE_CASCADE` (identity, not equality) and that the tables it deletes from are exactly `{t.name for t, _ in referencing_columns(PriceList)} & _DELETE_CASCADE`. This is the invariant of research R3; if it can pass with two hand-kept lists it is not testing anything. Confirm it fails.
- [x] T004 [P] [US1] Write `tests/integration/test_price_list_retirement.py` — against the real schema with foreign keys enforced: create two lists, price the same product in both, retire the first, and assert the list row is gone, its `product_price` rows are gone, and the second list's price for that product is still there. Confirm it fails with today's 409.

### Implementation

- [x] T005 [US1] Add `_DELETE_CASCADE = frozenset({'product_price'})` to `app/services/price_list_service.py` with the comment stating why the prices are the list's own contents and why anything else blocks — the argument is in research R4 and the table in `data-model.md`; do not restate the whole thing, state the rule and the reason.
- [x] T006 [US1] Rewrite `delete_price_list` in `app/services/price_list_service.py`: pass `exempt=_DELETE_CASCADE` to `assert_not_referenced`, then loop `referencing_columns(PriceList)` filtered on `_DELETE_CASCADE` issuing `delete(table).where(column == pl.price_list_id)` — SQLAlchemy Core, not interpolated text, so the identifier `list` is quoted by the dialect (data-model.md, "Statements a retirement issues"). Then `db.delete(pl)` and the single `commit()`. T002–T004 pass.

**Checkpoint**: A priced list with no customers on it retires in one request. This is GH #181's reproduction answered and is shippable on its own.

---

## Phase 4: User Story 2 — Moving the list's customers to another tier (P1)

**Goal**: The retirement accepts a replacement list and moves the retired list's customers onto it,
in the same transaction.

**Independent test**: Assign customers to a list, retire it naming another list, confirm the list is
gone and every one of those customers is on the named list, with no other customer touched.

**Depends on**: US1 (T006) — the same function, whose shape US1 establishes.

### Tests first

- [x] T007 [US2] Extend `tests/unit/test_price_list_service.py`: the customer `UPDATE` is issued **before** the blocker-check `SELECT` (research R2 — assert on statement order, since a later reordering is the way this silently breaks), it is issued only when a replacement is named, and it sets `price_list` to the replacement filtered on the retired list. Confirm they fail.
- [x] T008 [P] [US2] In the same file, add the validation tests: `replacement_id == pl.price_list_id` raises 400 `"Cannot replace a price list with itself"`, a replacement that does not exist raises 404 `"Replacement price list not found"`, and both are raised before any statement is issued — assert nothing was executed, which is what makes the contract's all-or-nothing claim true for these two paths without relying on rollback. Confirm they fail.
- [x] T009 [P] [US2] Extend `tests/integration/test_price_list_retirement.py`: (a) customers assigned + replacement named → 204, every one of them on the replacement, a customer of a third list untouched; (b) customers assigned + no replacement → 409 naming `customer.price_list` with its count, and the list, its prices and the assignments all still there; (c) a replacement named for a list nobody is assigned to → 204, replacement's customer count unchanged (spec edge case, research R8); (d) a refused retirement leaves no customer moved — the rollback claim of FR-006, exercised by pointing the replacement at a list that does not exist *after* customers would have moved. Confirm they fail.

### Implementation

- [x] T010 [US2] Add `replacement_id: int | None = None` to `delete_price_list` in `app/services/price_list_service.py`: validate it (self → 400, missing → 404) before writing anything, then issue the customer `update` ahead of the `assert_not_referenced` call added in T006. T007–T008 pass.
- [x] T011 [US2] Add the `replacement: int | None = Query(None)` parameter to `delete_price_list` in `app/api/v1/endpoints/price_lists.py` and pass it through. Keep `get_current_user` as the only dependency, matching the router (research R9). T009 passes.
- [x] T012 [P] [US2] Add the endpoint-level test to the price-list section of `tests/api/test_products.py`: `replacement` reaches the service as `replacement_id`, and its absence passes `None` rather than being dropped — the mocked layer is the only place that can assert the wiring.

**Checkpoint**: A list in real use — priced and assigned — retires in one request. US1 + US2 together are the whole of GH #181's ask.

---

## Phase 5: User Story 3 — Seeing the scale before committing (P2)

**Goal**: A read-only report of what rides on a list, before the irreversible step.

**Independent test**: Ask for a list that is both priced and assigned, confirm the counts match the
database, confirm nothing changed, then retire it and confirm the kinds acted on are the kinds
reported.

**Depends on**: nothing. Can be built before, after or alongside US1/US2 — its only coupling is the
invariant test in T016, which needs `_DELETE_CASCADE` to exist.

### Tests first

- [x] T013 [P] [US3] Extend `tests/unit/test_price_list_service.py`: `preview_delete` calls `find_blocking_references` with **no** `exempt` (mirroring `test_preview_merge_reports_what_rides_on_the_duplicate` — assert `'exempt' not in kwargs`, since passing the cascade set here is the plausible mistake), returns its result unchanged, and issues no write. Confirm they fail.
- [x] T014 [P] [US3] Add the endpoint tests to the price-list section of `tests/api/test_products.py`: 200 with items and a `total` that is their sum, 200 with empty items and `total: 0`, 404 for a list that does not exist, 401 unauthenticated, and that `/{id}/delete/preview` is not swallowed by `GET /{price_list_id}` — the route-ordering check `test_products.py` already makes for `/merge/preview`. Confirm they fail.

### Implementation

- [x] T015 [P] [US3] Add `PriceListDeletePreviewItem` (`category: str`, `count: int`) and `PriceListDeletePreviewResponse` (`items: list[...]`, `total: int`) to `app/schemas/product.py`, beside the other price-list schemas. The duplication of the merge preview's shape is deliberate and justified in the plan's Complexity Tracking; note it in a comment so the next reader does not "fix" it.
- [x] T016 [US3] Add `preview_delete(db, pl)` to `app/services/price_list_service.py` — `return await find_blocking_references(db, pl)`, with the docstring stating why it passes no `exempt` (FR-008: the report covers the union of what is swept and what blocks, by construction). T013 passes.
- [x] T017 [US3] Add `GET /{price_list_id}/delete/preview` to `app/api/v1/endpoints/price_lists.py`, declared **before** `GET /{price_list_id}` if FastAPI's matching requires it, 404 for a missing list, `get_current_user` only, assembling `PriceListDeletePreviewResponse` from the service's `(category, count)` pairs the way `preview_product_merge` does. T014 passes.
- [x] T018 [US3] Add `test_report_counts_exactly_what_a_retirement_touches` to `tests/unit/test_price_list_service.py`: the report's categories equal the union of the tables the retirement deletes from and the tables it blocks on — i.e. every relation in `referencing_columns(PriceList)`. This is the quickstart's named invariant (SC-005); if it can fail, the report is a lie.

**Checkpoint**: The report answers, matches, and changes nothing.

---

## Phase 6: Polish & Cross-Cutting

- [x] T019 [P] Add the `[Unreleased]` CHANGELOG entry under **Fixed** (the blocked delete) and **Added** (the replacement parameter and the report), in the house style: what changed, what it replaced, and the reasoning that is not recoverable from the diff — the exempt/cascade single-set invariant (R3), why `customer` stays a blocker (R4), and why the report's total counts records *touched* rather than deleted (R5).
- [x] T020 [P] Verify the docs need no change: `docs/specs/01-master-data.md` documents the legacy `PriceListsController`, not this API, and `docs/data-dictionary.md` already describes both `product_price.list` and `customer.price_list`. Confirm by reading rather than by assuming, and say so in the PR either way.
- [x] T021 Ran the quickstart end to end against `mbe_dev`, all five steps. Step 3: three lists, each carrying ~21.5k prices, so every list in the deployment was undeletable before this — the counts are recorded in the quickstart. Step 4: retiring Mayoreo into Mostrador deleted 21,569 prices and moved 150 customers in **0.56s**, then rolled back cleanly with every count back where it started. Step 5: the #181 reproduction returns `204`, and the refusal on a list with customers reads `Still referenced by customer.price_list (150)` — `product_price` no longer appears in it. This is what closes the MariaDB dialect gap `tests/integration/` leaves open on SQLite. Scratch lists created along the way were removed; `mbe_dev` is byte-for-byte as it was found.
- [x] T022 Gate against T001's recorded numbers: `uv run pytest -q` green with the new tests added to the count, `uv run ruff check app/ migrations/ tests/` clean, `uv run ruff format --check` clean **on the touched files** (not repo-wide — see T001), and mypy still at 170 errors in 48 files. Not lower by accident, not higher at all.
- [x] T023 Verify by mutation, the way #174 was: revert T006's cascade loop and confirm T004 fails with the 409 the issue reports; revert T010's ordering and confirm T007 fails. A test that passes against the broken code is testing nothing.

---

## Dependencies

```text
T001 (baseline)
  ├─ US1: T002,T003,T004 [P] → T005 → T006 ────┐
  │                                             ├─ US2: T007,T008,T009 → T010 → T011 → T012
  └─ US3: T013,T014 [P] → T015 [P] → T016 → T017 → T018
                                                     │
                                       T019,T020 [P] ┴─→ T021 → T022 → T023
```

- **US1 → US2**: same function; US2 adds a parameter and a statement to what US1 shapes.
- **US3 ⟂ US1/US2**: independent except T018, which reads `_DELETE_CASCADE` (T005).
- **T018 after T005**: the invariant needs both halves to exist to be worth asserting.

## Parallel Opportunities

- T002, T003, T004 — three test files, no shared edit.
- T013, T014, T015 — schemas and two test files, no shared edit.
- T019, T020 — changelog and a docs read.
- US3 in full can run alongside US1 → US2 if two people are on this; the only file both touch is
  `price_list_service.py`, in different functions.

## Implementation Strategy

**MVP = US1.** Six tasks, and it answers the issue's literal reproduction: a priced list becomes
deletable. Ship it alone if the customer half needs more discussion.

**US1 + US2** is the whole of GH #181 — after it, no price list in real use is unretirable from a
client.

**US3** is the reviewability layer. It changes no data and prevents no failure; it is what lets
mbe-ui put a confirmation in front of an irreversible action instead of a bare "are you sure".
