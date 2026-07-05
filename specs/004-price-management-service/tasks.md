# Tasks: Price Management Service

**Input**: Design documents from `specs/004-price-management-service/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — constitution requires tests for all new API endpoints.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Schema and router scaffolding shared by every user story. Must be complete before any
story's endpoints are wired up.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T001 [P] Create `app/schemas/product_price.py` with `ProductPriceCreate` (`product: int`,
  `price_list: int`, `price: Decimal = Field(ge=0)`, `low_profit: Decimal = Field(ge=0)`,
  `high_profit: Decimal = Field(ge=0)`), `ProductPriceUpdate` (same three Decimal fields, all
  optional, no `product`/`price_list`), and `ProductPriceResponse` (`product_price_id: int`,
  `product: int`, `price_list: PriceListResponse` imported from `app.schemas.product`, `price`,
  `low_profit`, `high_profit`)
- [X] T002 Create `app/api/v1/endpoints/product_prices.py` with an empty `APIRouter()` and register
  it in `app/api/v1/router.py` as `api_router.include_router(product_prices.router,
  prefix="/product-prices", tags=["product-prices"])`, alongside the existing `price_lists`
  registration

**Checkpoint**: Schema and router shell exist — user story implementation can now begin.

---

## Phase 2: User Story 1 - Manage a product's price independently of the product record (Priority: P1) 🎯 MVP

**Goal**: Create, retrieve, update, and delete a single `ProductPrice` entry through
`/product-prices`, entirely independent of the product endpoints.

**Independent Test**: Create a product with no prices, `POST /product-prices` a price for it,
`GET` it back, `PUT` to change its values, `DELETE` it — none of these touch the product record.

### Tests for User Story 1

> **Write these tests FIRST — verify they FAIL before implementing T004-T005**

- [X] T003 [P] [US1] Write tests in `tests/api/test_product_prices.py` (mock
  `app.services.product_price_service.*`, following the `_auth()` / `AsyncMock` pattern in
  `tests/api/test_products.py`) covering: create returns 201 with nested `price_list` object;
  create returns 404 when product doesn't exist; create returns 404 when price list doesn't
  exist; create returns 409 when a price already exists for the product+price_list pair; create
  returns 422 for a negative `price`; get returns 200; get returns 404 for unknown id; update
  returns 200 with only changed fields applied; update returns 404 for unknown id; delete returns
  204; delete returns 404 for unknown id; unauthenticated request returns 401

### Implementation for User Story 1

- [X] T004 [US1] Implement `get_product_price`, `create_product_price` (raises 404 if `product` or
  `price_list` id doesn't exist via `db.get`, raises 409 if a row already exists for the same
  `product`+`price_list` pair, else inserts and returns with `price_list` resolved),
  `update_product_price`, and `delete_product_price` in `app/services/product_price_service.py`,
  resolving the nested `price_list` object the same way `_attach_price_relations` does today in
  `app/services/product_service.py` (query `PriceList` by id, attach via `__dict__`)
- [X] T005 [US1] Add `POST ""`, `GET "/{product_price_id}"`, `PUT "/{product_price_id}"`, and
  `DELETE "/{product_price_id}"` routes to `app/api/v1/endpoints/product_prices.py`, each gated
  with `require_privilege(SystemObject.PRICING, AccessRight.<CREATE|READ|UPDATE|DELETE>)`,
  matching the request/response shapes in `specs/004-price-management-service/contracts/product-prices.md`

**Checkpoint**: US1 fully functional — single price entry lifecycle works independently of
products and independently of listing/filtering.

---

## Phase 3: User Story 2 - Find prices by product or price list (Priority: P2)

**Goal**: `GET /product-prices` lists price entries, filterable by `product` and/or `price_list`.

**Independent Test**: Create several price entries across products/price lists, query with each
filter alone and both together, verify only matching entries return.

### Tests for User Story 2

> **Write these tests FIRST — verify they FAIL before implementing T007-T008**

- [X] T006 [P] [US2] Add tests to `tests/api/test_product_prices.py` covering: list with no
  filters returns all; list filtered by `product` only; list filtered by `price_list` only; list
  filtered by both simultaneously; list response total/items shape matches `ListResponse`

### Implementation for User Story 2

- [X] T007 [US2] Implement `list_product_prices(db, *, product: int | None = None, price_list:
  int | None = None, skip: int = 0, limit: int = 20)` in `app/services/product_price_service.py`,
  following the `list_price_lists` pagination/filter pattern in `app/services/price_list_service.py`
- [X] T008 [US2] Add `GET ""` route with `product`, `price_list`, `skip`, `limit` query params to
  `app/api/v1/endpoints/product_prices.py`, gated with `require_privilege(SystemObject.PRICING,
  AccessRight.READ)`, returning `ListResponse[ProductPriceResponse]`

**Checkpoint**: US1 and US2 both work — full CRUD plus filterable listing.

---

## Phase 4: User Story 3 - Product operations stay free of pricing concerns (Priority: P1)

**Goal**: `product_service.py`/`products.py` no longer reference `ProductPrice`/`PriceList`;
`ProductResponse` drops `prices`; product creation stops auto-provisioning price rows; delete/merge
delegate cleanup to `product_price_service`.

**Independent Test**: Create, retrieve, update, delete, and merge products; confirm no response
ever contains pricing data, no price rows are auto-created, and deleting/merging a product leaves
no orphaned `product_price` rows.

### Tests for User Story 3

> **Write these tests FIRST — verify they FAIL before implementing T010-T012**

- [X] T009 [P] [US3] Update `tests/api/test_products.py`: remove `prices=[]` from the `_product()`
  fixture; add/update assertions that `GET/POST/PUT /products` responses contain no `"prices"`
  key; add a test asserting `create_product` is never called in a way that touches
  `product_price_service` (i.e. no price-creation side effect to assert against, since none exists)

### Implementation for User Story 3

- [X] T010 [US3] Add `delete_for_product(db, product_id: int) -> None` to
  `app/services/product_price_service.py` — deletes all `ProductPrice` rows for a given product id
  (used by product delete/merge cleanup)
- [X] T011 [US3] In `app/services/product_service.py`: remove the `PriceList`/`ProductPrice`
  import, `_price_list_id`, and `_attach_price_relations` helpers; remove the price-fetch/attach
  calls and `product.__dict__["prices"] = ...` assignments in `get_product`, `create_product`, and
  `update_product`; remove the price-list auto-provisioning loop in `create_product`; replace the
  `await db.execute(delete(ProductPrice).where(...))` calls in `delete_product` and
  `merge_products` with calls to `product_price_service.delete_for_product(db, product_id)` /
  `product_price_service.delete_for_product(db, req.duplicate_id)` respectively
- [X] T012 [US3] In `app/schemas/product.py`: remove the `ProductPriceResponse` class and the
  `prices: list[ProductPriceResponse] = []` field from `ProductResponse`; leave `PriceListCreate`,
  `PriceListUpdate`, `PriceListResponse` untouched (still imported by `app/schemas/customer.py`)

**Checkpoint**: All three user stories complete — products and pricing are fully decoupled.

---

## Phase 5: Polish

**Purpose**: Linting gate, changelog, full regression run.

- [X] T013 [P] Run `uv run ruff check app/ migrations/ tests/` and fix any violations
- [X] T014 Update `CHANGELOG.md` — add `/product-prices` CRUD endpoints under `[Unreleased] >
  Added`; note under `Changed`/`Removed` that `ProductResponse` no longer includes `prices` and
  that product creation no longer auto-provisions price rows
- [X] T015 Run `uv run pytest tests/ -v` and confirm all tests pass (213 passed)
- [X] T016 Manually walk through `specs/004-price-management-service/quickstart.md` against a
  running instance — performed against the real MariaDB instance in `.env`. All scenarios verified:
  new product has no `prices` field and zero price rows; price creation resolves the nested
  `price_list` object; duplicate `product`+`price_list` returns 409; filtering by `product` and by
  `price_list` both work; update and delete work; deleting a product removes its price rows;
  merging a product removes only the duplicate's prices, leaving the canonical product's prices
  intact; unauthenticated request returns 401. All test data created during the walkthrough was
  deleted afterward — confirmed no `QSTEST%` rows remain.

### Discovered during implementation

- [X] T017 `tests/unit/test_product_service.py` imported the now-removed `_attach_price_relations`
  helper directly (added in the earlier stale-`PriceList` bug fix, commit `18e257d`). Relocated its
  regression test to `tests/unit/test_product_price_service.py`, targeting the equivalent
  `_attach_price_list` in the new `product_price_service.py`, and deleted the old file (confirmed
  with the user before deleting a pre-existing test file).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — start immediately. BLOCKS all user stories.
- **US1 (Phase 2)**: Depends on Phase 1. No dependency on US2/US3.
- **US2 (Phase 3)**: Depends on Phase 1. Independent of US1 (adds a route to the same file, but no
  logic dependency — can be implemented and tested without US1 existing).
- **US3 (Phase 4)**: Depends on Phase 1 (needs `product_price_service` module to exist so
  `delete_for_product` can be added to it). Independent of US1/US2's routes.
- **Polish (Phase 5)**: Depends on all three user stories being complete.

### Within Each User Story

- Tests MUST be written and FAIL before implementation (TDD)
- Service functions before endpoint routes
- Story complete before moving to the next priority

### Parallel Opportunities

- T001 (schema) has no dependents blocking it — can start immediately alongside T002 (router
  scaffold), though both are quick.
- Once Phase 1 completes, US1, US2, and US3 test-writing (T003, T006, T009) can all be written in
  parallel — they touch different files or different, non-overlapping parts of the same file.
- T013 (lint) can run in parallel with writing T014 (changelog).

---

## Parallel Example: Post-Foundational

```bash
# After T001/T002 (Foundational) complete, these can start in parallel:
Task T003: "Write tests/api/test_product_prices.py CRUD tests"
Task T006: "Add list/filter tests to tests/api/test_product_prices.py"
Task T009: "Update tests/api/test_products.py to drop prices field"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Foundational
2. Complete Phase 2: User Story 1
3. **STOP and VALIDATE**: `uv run pytest tests/api/test_product_prices.py -v`

### Incremental Delivery

1. Foundational → schema + router shell ready
2. + US1 → single price CRUD works (MVP)
3. + US2 → filterable listing works
4. + US3 → products fully decoupled from pricing (the other half of this feature's value)
5. Polish → lint, changelog, full suite, quickstart walkthrough

---

## Notes

- `[P]` tasks touch different files (or clearly separated sections of the same file) and have no
  blockers — safe to do in parallel.
- US1 and US2 both add routes to the same `app/api/v1/endpoints/product_prices.py` file — not
  parallel-safe with each other at the route-adding step (T005 vs T008), even though their tests
  (T003 vs T006) and service functions (T004 vs T007) are independent.
- Constitution VIII (Ruff): watch line length on the new service's duplicate-check query and the
  `require_privilege(SystemObject.PRICING, AccessRight.X)` calls.
- US3's T011 is the highest-risk task — it edits `get_product`, `create_product`, `update_product`,
  `delete_product`, and `merge_products` in one file. Run `tests/api/test_products.py` immediately
  after to confirm nothing else broke.
