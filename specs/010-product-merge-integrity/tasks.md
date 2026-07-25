---

description: "Task list for Product Merge Integrity"
---

# Tasks: Product Merge Integrity

**Input**: Design documents from `/specs/010-product-merge-integrity/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. The merge's correctness is entirely a question of which statements it
issues, so it is verified by observing them; the preview is a new endpoint, which the
constitution requires be tested.

**Organization**: Grouped by user story so each is independently implementable and testable.

**Status**: All tasks complete — delivered in PR #114 (commits `990fa83`, `caa4fcc`) and PR #115
(commit `c9e83ad`). Checked boxes record what shipped, not a plan awaiting execution.

**Delivery order note**: User Story 3 (the preview) shipped first, against a merge that still
handled 8 of 19 relations — deliberately, since a count outside those 8 was a merge about to
fail. User Story 1 closed that gap; User Story 2 was discovered because it did.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story the task serves (US1, US2, US3)

## Path Conventions

Single project at repository root: `app/`, `tests/`.

---

## Phase 1: Setup

**Purpose**: Establish the ground truth. Every later decision rests on these two measurements.

- [x] T001 Enumerate the mapped foreign keys pointing at `product.product_id` and compare against what `merge_products` handled — 19 modelled, 8 handled (`specs/010-product-merge-integrity/research.md` R1)
- [x] T002 Measure what the eleven unhandled relations cost against `mbe_demo`: 13,248 of 21,542 products unmergeable, plus 1,008 and 248 products carrying commission rows orphaned by the two unenforced foreign keys (research R2)
- [x] T003 Confirm which of the 19 the database actually enforces — 17, with `commission_product` and `commissions_history` modelled only (research R2, quickstart step 3)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: One enumeration, read by everything that must agree about it.

**⚠️ CRITICAL**: T004 blocks all three user stories.

**Policy this implements** (spec FR-002): no relation may be handled from a hand-written list.
The list *is* the defect this feature exists to remove — a twentieth foreign key must be covered
without anyone remembering to add it. If a future task appears to need "just add the table to the
list", that is this defect returning.

- [x] T004 Extract `referencing_columns(model, *, exempt)` from `find_blocking_references` in `app/services/references.py`, returning the sorted mapped `(table, column)` pairs, so counting and rewriting read one source
- [x] T005 Extract `_load_merge_pair(db, req)` from `merge_products` in `app/services/product_service.py`, raising the 400 self-merge and the two side-named 404s, so the merge and the preview validate a pair identically

---

## Phase 3: User Story 1 — Merging a product that has actually been used (P1) 🎯 MVP

**Goal**: A merge carries every reference the duplicate holds, so the products with history —
the ones anyone would want to merge — can be merged.

**Independent test**: Merge the most-referenced product in a populated database; the duplicate is
deleted and nothing points at it.

### Tests for User Story 1

- [x] T006 [P] [US1] Rewrite `_merge_statements` in `tests/unit/test_product_service.py` to stub nothing, so the observed statements are what the merge issues (research R9)
- [x] T007 [P] [US1] Assert the merge covers every relation `referencing_columns(Product)` reports, naming the eleven left behind before #112, in `tests/unit/test_product_service.py`
- [x] T008 [P] [US1] Assert `service_order_detail` is remapped through `spare_part`, in `tests/unit/test_product_service.py`
- [x] T009 [P] [US1] Assert the duplicate is deleted and the session commits exactly once, in `tests/unit/test_product_service.py`

### Implementation for User Story 1

- [x] T010 [US1] Rewrite `merge_products` in `app/services/product_service.py` as one loop over `referencing_columns(Product)`, replacing the six-table list, the `product_price_service.delete_for_product` call and the `product_label` special case
- [x] T011 [US1] Remap `fiscal_document_detail` like any other reference, documenting in the docstring why the CFDI snapshot makes this safe (research R4)
- [x] T012 [US1] Verify against `mbe_demo` inside a rolled-back transaction: merge the product with the most fiscal history (83,488 rows across 13 relations), confirm the deletion succeeds and no orphan remains

**Checkpoint**: Every mapped reference moves; the merge no longer fails on `customer_refund_detail`.

---

## Phase 4: User Story 2 — The kept record's setup is the one that survives (P1)

**Goal**: The canonical's configuration is what stands after a merge, in full, for every row.

**Independent test**: Merge two products that each carry prices, a label and a commission
assignment; the canonical's counts for those four relations are unchanged.

### Tests for User Story 2

- [x] T013 [P] [US2] Assert the four configuration relations are deleted and never remapped, in `tests/unit/test_product_service.py`
- [x] T014 [P] [US2] Assert history and configuration never overlap and the split is exhaustive, in `tests/unit/test_product_service.py`
- [x] T015 [P] [US2] Assert no `DELETE` is issued against a history relation, in `tests/unit/test_product_service.py`
- [x] T016 [P] [US2] Assert no statement is an `UPDATE IGNORE`, so nothing suppresses a failure that should roll the merge back (FR-005), in `tests/unit/test_product_service.py`
- [x] T017 [P] [US2] Assert `product_price` is discarded by the same loop as the rest, not by a separate call the statement coverage would not see, in `tests/unit/test_product_service.py`

### Implementation for User Story 2

- [x] T018 [US2] Declare `_MERGE_DISCARD` in `app/services/product_service.py` — `product_price`, `product_label`, `commission_product`, `customer_discount` — with the comment recording that each has a unique key covering the product column and why the set is declared rather than derived (research R5)
- [x] T019 [US2] Branch the loop in `merge_products` on `_MERGE_DISCARD` membership: `DELETE` for configuration, `UPDATE` for history — the only thing that varies per relation
- [x] T020 [US2] Remove the `UPDATE IGNORE` + blanket `DELETE` pair introduced by #112, along with the unique-key set it required
- [x] T021 [US2] Verify against `mbe_demo` inside a rolled-back transaction: merge 18829 (67,920 rows across 15 relations) into 8 where both sides carry a label, a commission row and prices; confirm each configuration relation leaves the canonical untouched and each history relation lands on canonical + duplicate

**Checkpoint**: The outcome of a merge is statable in one sentence, independent of which rows collided.

---

## Phase 5: User Story 3 — Seeing the scale before committing (P2)

**Goal**: An operator reviewing an irreversible merge is shown what rides on the duplicate.

**Independent test**: Request the preview for a pair and compare the breakdown against the
database; confirm nothing changed.

### Tests for User Story 3

- [x] T022 [P] [US3] Cover the response shape and that `total` sums the counts, in `tests/api/test_products.py`
- [x] T023 [P] [US3] Cover an untouched duplicate previewing as `{items: [], total: 0}`, in `tests/api/test_products.py`
- [x] T024 [P] [US3] Cover both query parameters reaching the service, in `tests/api/test_products.py`
- [x] T025 [P] [US3] Cover `/merge/preview` returning 422 for missing parameters rather than a 404 from `GET /{product_id}`, in `tests/api/test_products.py`
- [x] T026 [P] [US3] Cover the preview requiring authentication, in `tests/api/test_products.py`
- [x] T027 [P] [US3] Cover the preview reporting what rides on the duplicate, changing nothing, and refusing the pairs a merge refuses, in `tests/unit/test_product_service.py`

### Implementation for User Story 3

- [x] T028 [P] [US3] Add `ProductMergePreviewItem` and `ProductMergePreviewResponse` to `app/schemas/product.py`, documenting that `category` is the `table.column` label the referential guard already uses
- [x] T029 [US3] Add `preview_merge(db, req)` to `app/services/product_service.py`, validating through `_load_merge_pair` and counting through `find_blocking_references` with no exempt set
- [x] T030 [US3] Add `GET /merge/preview` to `app/api/v1/endpoints/products.py`, gated by `require_privilege(SystemObject.PRODUCTS_MERGE, AccessRight.READ)`, declared before `GET /{product_id}`

**Checkpoint**: The review step in the client has real counts to show.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T031 Assert the invariant tying the two features together — the preview's categories equal what the merge remaps plus what it deletes — in `tests/unit/test_product_service.py`
- [x] T032 [P] Record the merge fix, the fiscal-history decision and the configuration split in `CHANGELOG.md`, calling out the data a merge no longer carries over
- [x] T033 [P] Correct the #111 changelog entry: the preview counts what a merge *touches*, not what it moves, since four of the nineteen relations are deleted (FR-016)
- [x] T034 `uv run ruff check app/ migrations/ tests/`, `ruff format --check`, `mypy app` clean
- [x] T035 Close the constitution's endpoint-testing gate for `GET /merge/preview`: cover the 400 self-merge and the side-named 404 at API level in `tests/api/test_products.py`, exercising the real service rather than a mock of it. Found when this specification was written — the two refusals were covered only by unit tests, and §Testing requires a 404 case in `tests/api/`

---

## Dependencies & Execution Order

```
T001–T003 (measure)
   └── T004 (referencing_columns) ──┬── T005 (_load_merge_pair)
                                    │
        US1: T006–T009 → T010 → T011 → T012
                          │
        US2: T013–T017 → T018 → T019 → T020 → T021
                          │
        US3: T022–T027 → T028 → T029 → T030   (T029 needs T005)
                                    │
                                   T031 → T032, T033 → T034
```

- **US1** depends on T004. It is the MVP: without it the merge fails for 61% of the catalog.
- **US2** depends on US1 — the defect only exists once configuration is being moved at all.
- **US3** depends on T004 and T005 only, which is why it shipped first. Its invariant (T031)
  depends on US1.

## Implementation Strategy

The three stories were delivered in two pull requests, not three. US3 shipped alone in #114's
first commit because the review step in the client was blocked on it and it is read-only; US1
followed in the same PR once the preview made the size of the gap visible. US2 shipped in #115
after US1 revealed it. Each is independently testable, and the tests for each fail against the
code that preceded it.
