---

description: "Task list for spec 036, localizable error codes"
---

# Tasks: Localizable Error Codes

**Input**: Design documents from `specs/036-localizable-error-codes/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R13), data-model.md, contracts/api.md, contracts/error-codes.md, raise-map.md, quickstart.md

**Tests**: Required (constitution). FR-020 asks for a test per code, plus one per further site whose params differ (status, code, params keys, raw param types) through `assert_problem`, and SC-008 for every code to be asserted. Tests come first within each area.

**Organization**: One phase per user story, in the spec's priority order. US1 and US2 are both P1; US1 goes first because its generic codes and `not_found()` sweep touch every router that US2 and US3 then finish.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1–US4 from spec.md
- Paths are relative to the worktree, `/Users/eddy/Projects/mictlanix/mbe-api-036`

## Rules for every migration task

These apply to every task below that says "migrate". They aren't repeated in each task.

1. **Source of truth:** `raise-map.md` gives each raise site's code and params. `contracts/error-codes.md` wins where the two disagree. Line numbers are as of 6a1fd4b, so find the site by its `detail` text if lines have drifted.
2. **The raise:** `raise DomainError(ErrorCode.X, <today's detail, byte for byte>, {params})`, or `raise not_found(<model or name>, id)` for `resource.not_found`. Statuses come from the code; never pass one. Drop `HTTPException` and `status` imports the change leaves unused.
3. **Params:** raw values only (FR-003). Pass `Decimal`, `date`, `datetime` and enums as they are; `DomainError` normalizes them. Omit params the code doesn't define.
4. **Tests first:** for each code, and for each further raise site whose params differ in keys or source (FR-020), add or convert a test that calls `assert_problem(response, status, code)` (or asserts `exc.value.code` and `exc.value.params` in a service unit test) and checks each params key and its raw type (`int`, `str` for decimals and dates, `list`). Watch it fail, then migrate.
5. **Existing `detail` assertions:** they keep passing, since `detail` doesn't change. Once an area's raises are migrated, convert that area's `detail` assertions to `assert_problem` plus params (FR-021). Keep the `detail` assertion where `detail` is the contract: `password_change_required` and the four dict details.
6. **Gate:** `uv run ruff check app/ migrations/ tests/` and the area's tests pass before the task is checked. Commit per area, in the plan's phase order. Commit messages carry no `Co-Authored-By:` line.

---

## Phase 1: Setup

**Purpose**: Confirm the starting point.

- [X] T001 In the worktree, confirm the branch `036-localizable-error-codes` sits on main at 6a1fd4b or later, that `.env` is linked, and that `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q` pass. Baseline at d2ce73b: ruff clean, 5977 passed, 2 skipped.

---

## Phase 2: Foundational (design phase 0)

**Purpose**: The enum, the exception, the handler, the OpenAPI default and the test helper. Every story depends on these.

**⚠️ CRITICAL**: No story work starts until this phase is done.

- [X] T002 [P] Create `app/core/error_codes.py` with `ErrorCode(StrEnum)` per research R2: a custom `__new__(cls, value, status)` storing `.status` and keeping the value a plain string. Generate all 240 members from the tables in `specs/036-localizable-error-codes/contracts/error-codes.md` (member name = value upper-cased with `.` → `_`, e.g. `RESOURCE_NOT_FOUND = 'resource.not_found', 404`; no two codes collide this way), grouped by domain in the document's order with one comment line per domain. Keep the generator script in the scratchpad, not the repo.
- [X] T003 [P] Create `app/schemas/problem.py` with `Problem` (`status: int`, `code: ErrorCode`, `detail: str | dict[str, Any]`, `params: dict[str, Any] | None`, `errors: list[ProblemPart] | None`) and `ProblemPart` (`code: ErrorCode`, `params: dict[str, Any] | None`, extra keys allowed), per research R5 and data-model.md.
- [X] T004 Create `app/core/errors.py` (depends on T002) per research R1–R3 and R7:
  - `DomainError(HTTPException)` with signature `(code, detail, params=None, *, errors=None, headers=None)`; `status_code = code.status`; params normalized at construction (Decimal → `format(d, 'f')`, date/datetime → `isoformat()`, Enum → `.value`, lists, tuples and dicts recursively, `None` values dropped);
  - `RESOURCE_NAMES` mapping all 94 mapped tables to public names, with R7's renames, and `EXTRA_RESOURCES = {'proof_of_delivery_image', 'license_photo', 'receivable', 'product_image'}`;
  - `not_found(resource, id=None, detail=None) -> DomainError`, where `resource` is a model class or a public name and `detail` defaults to `"<Name> not found"`;
  - `problem_body(status, code, detail, params=None, errors=None) -> dict`, omitting empty `params` and `errors`;
  - `http_exception_handler` for Starlette's `HTTPException`: a `DomainError` contributes code, params and errors; any other maps by status (400 `request.malformed`, 401 `auth.not_authenticated`, 404 `request.route_not_found`, 405 `request.method_not_allowed`, anything else keeps its status with `request.malformed`). It answers `application/problem+json` and keeps `exc.headers`.
- [X] T005 Register `http_exception_handler` on `starlette.exceptions.HTTPException` in `app/main.py` (depends on T004). Leave `RequestValidationError` on FastAPI's default handler.
- [X] T006 In `app/api/v1/router.py`, give `api_router` the default response per research R5: `responses={'default': {'model': Problem, 'description': 'Error (RFC 9457 Problem Details)', 'content': {'application/problem+json': {'schema': {'$ref': '#/components/schemas/Problem'}}}}}` (depends on T003).
- [X] T007 [P] Create `tests/problem.py` with `assert_problem(response, status, code) -> dict`: asserts the HTTP status, `content-type` starting `application/problem+json`, `body['status'] == status` and `body['code'] == code`, and returns the body.
- [X] T008 Create `tests/unit/test_errors.py` (depends on T004, T005):
  - `ErrorCode('resource.conflict').status == 409`, and a Pydantic model field typed `ErrorCode` exports `{"type": "string", "enum": [...]}`;
  - `DomainError` takes its status from the code, and `except HTTPException` still catches it with `exc.detail` intact;
  - normalization: `Decimal('18450.00')` → `"18450.00"`, date, datetime, Enum, a nested list of dicts, `None` dropped;
  - `problem_body` omits empty `params` and `errors`, and passes a dict `detail` through untouched;
  - the handler keeps response headers (`WWW-Authenticate`) and serves `application/problem+json`, using a throwaway route on a test app.
- [X] T009 Create `tests/unit/test_error_codes.py` (depends on T002, T004), per research R9:
  - every member matches `^[a-z][a-z_]*\.[a-z][a-z_]*$` with a status in 400–599 (passes now);
  - every mapped table (`Base.metadata.tables`) is a key of `RESOURCE_NAMES`, and no public name equals a table name that R7 renames (passes now);
  - **guard:** an AST scan of `app/` finds no `HTTPException(` call outside `app/core/errors.py`, failing with file and line (`xfail(strict=True)` until T082);
  - **coverage:** every `ErrorCode` appears in some file under `tests/` other than this one, as its value (`'resource.not_found'`) or its member (`ErrorCode.RESOURCE_NOT_FOUND`) (`xfail(strict=True)` until T083).
- [X] T010 Extend `tests/unit/test_contract_docs.py` per research R9: parse the rows of `specs/036-localizable-error-codes/contracts/error-codes.md` whose first cell is a backticked `domain.condition`, compare `{code: status}` with `ErrorCode` in both directions, and pin the row count at 240 in the parser-still-finds test (FR-017, SC-003).
- [X] T011 Run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`: the suite is as green as T001's baseline, with two strict xfails. Commit "phase 0".

**Checkpoint**: The contract exists; no raise uses it yet.

---

## Phase 3: User Story 1 - Any error can be told apart by its code (Priority: P1) 🎯 MVP

**Goal**: The generic codes (design phase 1): not found, already exists, still referenced, conflict, document not editable, invalid state change, auth, framework errors and the 500.

**Independent Test**: Request customer 999999, create a duplicate product code, delete a warehouse in use, call a route with no token and with too few privileges, and hit an unknown route. Each answer is `application/problem+json` with the code and params in contracts/api.md and an unchanged `detail`. `/openapi.json` lists `Problem` and the 240-value `ErrorCode`.

### Tests for User Story 1 ⚠️

- [X] T012 [P] [US1] In `tests/unit/test_errors.py`, through the real app: unknown route → 404 `request.route_not_found`; wrong method → 405 `request.method_not_allowed`; unparseable JSON body → 400 `request.malformed`; no token on a protected route → 401 `auth.not_authenticated` with `WWW-Authenticate: Bearer`; a body failing validation → 422 that keeps FastAPI's list shape and `application/json`.
- [X] T013 [P] [US1] Create `tests/api/test_openapi_problem.py`: `/openapi.json` has `components.schemas.Problem` and `ErrorCode` with 240 enum values, and every `/api/v1` operation has a `default` response referencing `Problem` under `application/problem+json`.
- [X] T014 [P] [US1] Convert `tests/api/test_constraint_errors.py` to 409 `resource.conflict` with today's `detail`, and `tests/api/test_unhandled_errors.py` to 500 `server.error` with `detail: "Internal server error"`, both as `application/problem+json`.
- [X] T015 [P] [US1] In `tests/unit/test_references.py`:
  - `assert_unique` raises `resource.already_exists` with `params.resource` and `params.field`, and a composite key gives `field` joined with commas;
  - `assert_not_referenced` raises `resource.still_referenced` with `references` as `{resource, count}` by public name, summed per resource (a transfer's source and destination count once), largest first, five at most, and `more` counting the rest; `detail` uses the same names;
  - **SC-006 sweep:** for every table that holds a foreign key, the still-referenced body contains no table name that R7 renames.
- [X] T016 [P] [US1] In `tests/unit/test_documents.py`, the edit guard raises `document.not_editable` with `params.status` a raw enum value. In `tests/unit/test_delivery_events.py`, invalid transitions raise `state.transition_invalid` with `resource`, `from`, `to` and, where given, `required`, all raw values.
- [X] T017 [P] [US1] In `tests/unit/test_deps_current_user.py`, `tests/unit/test_deps_privileges.py` and `tests/api/test_auth.py`:
  - bad login → 401 `auth.invalid_credentials`;
  - a user flagged to change password → 403 `auth.password_change_required` with `detail == "password_change_required"` exactly;
  - a non-administrator on an administrator-only route → 403 `auth.administrator_required`, no params;
  - a missing privilege → 403 `auth.forbidden` with `params.required` a list of `{system_object: int, right: int}`: one entry for `require_privilege`, every grant for `require_any_privilege`;
  - wrong current password → 400 `auth.incorrect_current_password`; a bad recovery token → 400 `auth.recovery_token_invalid`.
- [X] T018 [P] [US1] Create `tests/api/test_not_found.py`: parametrized over every `/api/v1` GET route whose path has exactly one integer id parameter, call it as an administrator with id 999999 and assert 404 `resource.not_found`, `params.id == 999999` (int) and `params.resource` in `RESOURCE_NAMES.values() | EXTRA_RESOURCES`. Skip only routes that answer 404 for another reason, listing each with its reason. Add two edge cases (spec Edge Cases): a 404 that names no id (a lookup by code, or the caller's own profile) omits `params.id`; a non-numeric key (a SAT catalog key or postal code in `sat_catalogs`) gives `params.id` as a string.

### Implementation for User Story 1

- [X] T019 [US1] In `app/main.py`, rebuild `integrity_error_handler` and `UnhandledErrorMiddleware`'s response with `problem_body()` (409 `resource.conflict`, 500 `server.error`), served as `application/problem+json`, keeping today's `detail` text and logging (research R4).
- [X] T020 [US1] Migrate `app/services/references.py`: `assert_unique` → `resource.already_exists`; `assert_not_referenced` → `resource.still_referenced` with public names via `RESOURCE_NAMES`, summed per resource, five largest, `more` (research R7, FR-008).
- [X] T021 [P] [US1] Migrate `app/services/documents.py` (3 rows) to `document.not_editable`.
- [X] T022 [P] [US1] Migrate `app/services/delivery_events.py` (4 rows) to `state.transition_invalid`.
- [X] T023 [US1] Migrate `app/core/deps.py` (5 rows) and `app/api/v1/endpoints/auth.py` (6 rows) to the `auth.*` codes, keeping `detail="password_change_required"` and the `WWW-Authenticate` headers. `require_admin` raises `auth.administrator_required`; `require_privilege` and `require_any_privilege` pass `required=[{'system_object': obj, 'right': right} for obj, right in grants]` (enums normalize to ints). The other inline grant checks (`customers.py:103`, `fiscal_documents.py:252`) follow the same shape in T040 and T065.
- [X] T024 [P] [US1] Migrate every `resource.not_found` and `resource.already_exists` row in the master-data routers and services: `app/api/v1/endpoints/{addresses,bank_accounts,contacts,employees,exchange_rates,labels,payment_method_options,points_of_sale,suppliers,taxpayer_issuers,taxpayer_recipients,users,user_profiles,warehouses,facilities,customers,sat_catalogs}.py`, `app/services/{exchange_rate_service,user_profile_service,warehouse_service,supplier_agreement_service,accounts_service}.py`. Use `not_found()`. Where today's text isn't "<Name> not found", pass it as `detail`.
- [X] T025 [P] [US1] Same for sales and cash: `app/api/v1/endpoints/{sales_orders,sales_quotes,cash_sessions,cash_drawers,customer_payments,customer_refunds,credit_notes,accounts_receivable,accounts_payable}.py` and `app/services/{sales_order_service,sales_quote_service,cash_session_service,customer_payment_service,customer_refund_service,customer_service,customer_discount_service,point_sale_service}.py`. `credit_notes.py:78` (another facility's note) is `resource.not_found` on purpose.
- [X] T026 [P] [US1] Same for deliveries and inventory: `app/api/v1/endpoints/{delivery_orders,delivery_itineraries,vehicles,vehicle_operators,inventory_counts,inventory_issues,inventory_receipts,inventory_transfers}.py` and `app/services/{delivery_order_service,delivery_itinerary_service,inventory_documents,inventory_transfer_service,inventory_receipt_service,inventory_count_service,kardex_service,stock_query_service,incidences}.py`. Proof-of-delivery images and licence photos use the `EXTRA_RESOURCES` names.
- [X] T027 [P] [US1] Same for purchasing, fiscal, pricing and commissions: `app/api/v1/endpoints/{purchase_orders,purchase_requests,expenses,expense_vouchers,supplier_payments,fiscal_documents,taxpayer_certificates,products,price_lists,product_prices}.py` and `app/services/{purchase_order_service,purchase_request_service,purchase_cost_service,expense_voucher_service,supplier_payment_service,invoice_service,taxpayer_certificate_service,taxpayer_batch_service,price_list_service,product_price_service,product_service,commission_service}.py`. `commission_service._get_or_404` returns `not_found()`. Product images use `product_image`.
- [X] T028 [US1] Convert the `detail` assertions on 404s, duplicates, still-referenced deletes and auth in `tests/api/` and `tests/unit/` to `assert_problem` plus params (FR-021), keeping `password_change_required`'s `detail` check. Run the full suite.

**Checkpoint**: US1 stands on its own. The generic codes cover about half of all raises, and mbe-ui can map not found, duplicates, blockers and auth. Commit "phase 1".

---

## Phase 4: User Story 2 - Sales and cash errors carry their facts as params (Priority: P1)

**Goal**: The design's first wave (phase 2): credit hold and limit, sales order and quote rules, cash sessions, payments, refunds, the facility rule's 403s, and the four dict details with a code.

**Independent Test**: Update an order to credit terms for a customer with 4 overdue credit orders (422 `customer.credit_hold`, `overdue_count: 4`, `action: "raise"`), open a session on a drawer someone else holds (409 `cash_session.drawer_busy`), confirm an expired quote, confirm an order with a zero-priced line (409, dict `detail` unchanged, top-level `code`), and open another facility's payment without the privilege (403 `facility.other_facility`, `params.required`).

### Tests for User Story 2 ⚠️

- [X] T029 [P] [US2] In `tests/unit/test_sales_order_service.py` and `tests/api/test_sales_orders.py`, one test per `customer.*` and `sales_order.*` code in the raise map:
  - `customer.credit_hold` for both `action` values, with `overdue_count` an int;
  - `customer.credit_limit_exceeded` with `outstanding` and `limit` as decimal strings ("18450.00", "15000.00"), status 422;
  - `customer.credit_limit_reached`, `customer.no_credit_limit`, `customer.walk_in_no_credit`;
  - `sales_order.below_min_quantity` with `product_id` an int;
  - the three dict details: `detail` equal to today's dict byte for byte, plus the top-level code.
- [X] T030 [P] [US2] In `tests/unit/test_sales_quote_service.py` and `tests/api/test_sales_quotes.py`, one test per `quote.*` code, plus the quote-convert 422 keeping a string `detail`.
- [X] T031 [P] [US2] In `tests/unit/test_cash_session_service.py` and `tests/api/test_cash_sessions.py`, one test per `cash_session.*` code, `cash_session.user_busy` with `cash_session_id` an int, and `user.no_point_of_sale`, `user.no_facility`, `user.no_cash_drawer`.
- [X] T032 [P] [US2] In `tests/unit/test_customer_payment_service.py`, `tests/unit/test_customer_refund_service.py`, `tests/api/test_customer_payments.py`, `tests/api/test_customer_refunds.py` and `tests/api/test_credit_notes.py`, one test per `payment.*` and `refund.*` code, with money params as decimal strings.
- [X] T033 [P] [US2] Facility rule (FR-013): 403 `facility.other_facility` with `params.resource` and `params.required` (`[{system_object: 100 or 101, right: 2}]`, raw ints) for another facility's payment (`tests/api/test_customer_payments.py`), credit note list (`tests/api/test_credit_notes.py`), sales order (`tests/api/test_sales_orders.py`), receivables (`tests/api/test_accounts_receivable.py`) and inventory document (`tests/unit/test_inventory_documents.py`, where `required` is omitted: administrators only).
- [X] T034 [P] [US2] In `tests/unit/test_delivery_itinerary_service.py` (or `tests/api/test_delivery_itineraries.py`), departing an itinerary with over-committed lines answers 422 `itinerary.lines_overcommitted` with today's dict `detail` byte for byte. In `tests/unit/test_point_sale_service.py`, `point_of_sale.warehouse_facility_mismatch`. In `tests/unit/test_customer_discount_service.py`, the `customer_discount.*` codes.

### Implementation for User Story 2

- [X] T035 [US2] Migrate `app/services/sales_order_service.py` (20 rows). `assert_quantity_allowed` gains a `product_id` argument for `sales_order.below_min_quantity`; update its callers. The three dict details keep their dict and gain only the code.
- [X] T036 [P] [US2] Migrate `app/services/sales_quote_service.py` (10 rows, except `:557` `email.send_failed`, which is T074).
- [X] T037 [P] [US2] Migrate `app/services/cash_session_service.py` (7 rows). At `:425`, keep the blocking session found by `open_session_for_cashier(...)` and pass its id as `cash_session_id`.
- [X] T038 [P] [US2] Migrate `app/services/customer_payment_service.py` (11 rows) and `app/services/customer_refund_service.py` (11 rows).
- [X] T039 [P] [US2] Migrate `app/services/customer_service.py` (3 rows, including the `assert_not_cost_list` call sites at `:214` and `:254`), `app/services/point_sale_service.py` (2) and `app/services/customer_discount_service.py` (2).
- [X] T040 [US2] Migrate the remaining rows of `app/api/v1/endpoints/{sales_orders,sales_quotes,cash_sessions,cash_drawers,customer_payments,customer_refunds,credit_notes,accounts_receivable,customers}.py` not done in T025, including the facility-rule 403s at `accounts_receivable.py:49`, `credit_notes.py:50`, `customer_payments.py:80` and `sales_orders.py:83`. The two `email.not_configured` rows are T074.
- [X] T041 [US2] In `app/services/inventory_documents.py`, give `assert_facility` a `resource` argument and raise `facility.other_facility` at `:353`; pass the resource from each caller.
- [X] T042 [US2] Migrate `app/services/delivery_itinerary_service.py:611` to `itinerary.lines_overcommitted`, keeping its dict `detail`.
- [X] T043 [US2] Convert the sales, quote, cash, payment, refund and credit-note `detail` assertions to `assert_problem` plus params (FR-021), except the four dict details. Run the full suite.

**Checkpoint**: US1 and US2 work together. Every POS refusal a cashier sees is coded. Commit "phase 2".

---

## Phase 5: User Story 3 - Every remaining area speaks in codes (Priority: P2)

**Goal**: Design phase 4. Every remaining raise in the raise map, the six `str(e)` routers, the inventory count batch, bulk approve, email, and the 21 schema validators.

**Independent Test**: In each area, trigger one business refusal and one field-validation failure. Upload a non-image as a product image (422 `image.*`) and an unreadable CSD (422 `csd.*`). Submit a supplier payment dated tomorrow: its validation entry has `type: supplier_payment_date_in_future`, `ctx.date`, and a `msg` without "Value error, ". Each answer keeps today's status.

### Deliveries, itineraries and fleet

- [X] T044 [P] [US3] Tests: one per `delivery_order.*`, `delivery_order_line.*`, `itinerary.*`, `vehicle.*`, `incidence.*` and `facility.no_in_transit_location` code in `tests/unit/test_delivery_order_service.py`, `tests/unit/test_delivery_itinerary_service.py`, `tests/unit/test_incidences.py`, `tests/api/test_delivery_orders.py`, `tests/api/test_delivery_itineraries.py` and `tests/api/test_fleet.py`. `facility.no_in_transit_location` has `facility_ids` as a list of ints.
- [X] T045 [US3] Migrate `app/services/delivery_order_service.py` (22 rows) and the remaining rows of `app/services/delivery_itinerary_service.py`.
- [X] T046 [P] [US3] Migrate the remaining rows of `app/api/v1/endpoints/{delivery_orders,delivery_itineraries,vehicles,vehicle_operators}.py` and `app/services/incidences.py`, except the `str(e)` handlers (T049).

### Images and the `str(e)` routers

- [X] T047 [P] [US3] Tests: in `tests/unit/test_image_service.py`, one per `image.*` code raised by `image_service`. Through the routes, a non-image upload answers 422 with the image code for product image (`tests/api/test_product_image.py`), facility logo (`tests/api/test_facilities.py`), licence photo (`tests/api/test_vehicle_operator_license_photo.py`) and both proof-of-delivery photos (`tests/api/test_delivery_orders.py`, `tests/api/test_delivery_itineraries.py`).
- [X] T048 [US3] In `app/services/image_service.py` (2 rows: `:33`, `:39`), raise `DomainError` with `image.too_large` (`max_bytes`) and `image.unsupported_format` instead of `ValueError`. `detail` is today's message unchanged ("Image exceeds maximum allowed size of 2 MB", "Unsupported or unrecognizable image format"), so the routes' `detail` doesn't change (FR-005). `delete_image`'s "Invalid image filename" `ValueError` stays (research R10: not user-facing).
- [X] T049 [US3] Remove the `try/except ValueError → HTTPException(422, str(e))` blocks at `app/api/v1/endpoints/products.py:198`, `facilities.py:55`, `vehicle_operators.py:116`, `delivery_orders.py:368` and `delivery_itineraries.py:381`, so `DomainError` propagates (FR-014). Drop imports left unused.

### Inventory

- [X] T050 [P] [US3] Tests: one per `inventory_document.*`, `inventory_receipt.*`, `inventory_transfer.*`, `kardex.*`, `lock.*`, `warehouse.*`, `status_filter.unsupported` and `facility.required` code in `tests/unit/test_inventory_documents.py`, `tests/unit/test_inventory_transfer_state.py`, `tests/unit/test_inventory_receipt_service.py`, `tests/unit/test_kardex.py`, `tests/unit/test_stock_queries.py`, `tests/unit/test_inventory_locks.py`, `tests/api/test_inventory_issues.py`, `tests/api/test_inventory_receipts.py`, `tests/api/test_inventory_transfers.py` and `tests/api/test_kardex.py`. `inventory_document.insufficient_stock` has `requested` and `available` as decimal strings.
- [X] T051 [P] [US3] Tests for the batch (FR-012a, research R11) in `tests/unit/test_inventory_count.py` and `tests/api/test_inventory_counts.py`: a refused `PUT /inventory-counts/{id}/lines` answers 422 `inventory_count.batch_invalid` as `application/problem+json`, `detail` unchanged, and each `errors[]` item keeps `index`, `product` or `product_code`, and `reason`, and gains one of the four `inventory_count_line.*` codes. Add one test per remaining `inventory_count.*` code.
- [X] T052 [US3] Migrate `app/services/inventory_documents.py` (remaining rows, including `busy_as_conflict`), `app/services/inventory_transfer_service.py` (10), `app/services/inventory_receipt_service.py` (1), `app/services/kardex_service.py` (4), `app/services/stock_query_service.py` (1) and `app/services/warehouse_service.py` (remaining rows).
- [X] T053 [US3] Inventory count batch: `validate_batch` in `app/services/inventory_count_service.py` returns the code with each reason; `app/api/v1/endpoints/inventory_counts.py` raises `DomainError(ErrorCode.INVENTORY_COUNT_BATCH_INVALID, str(refusal), errors=items)` in place of the hand-built `JSONResponse`. Migrate the service's remaining row.
- [X] T054 [P] [US3] Migrate the remaining rows of `app/api/v1/endpoints/{inventory_issues,inventory_receipts,inventory_transfers}.py`. `inventory_transfers.py:77` is `facility.required`.

### Purchasing, expenses and supplier payments

- [X] T055 [P] [US3] Tests: one per `purchase_request.*`, `purchase_order.*`, `expense_voucher.*`, `supplier_payment.*`, `supplier_agreement.*` and `cash_session.not_open` (vouchers, supplier payments) code in `tests/unit/test_purchase_requests.py`, `tests/unit/test_purchase_orders.py`, `tests/unit/test_purchase_states.py`, `tests/unit/test_purchase_cost.py`, `tests/unit/test_expense_vouchers.py`, `tests/unit/test_supplier_agreement_service.py`, `tests/api/test_purchase_requests.py`, `tests/api/test_purchase_orders.py`, `tests/api/test_expense_vouchers.py` and `tests/api/test_supplier_payments.py`. Note the two "line has no quantity" codes: 409 on orders, 422 on requests.
- [X] T056 [P] [US3] Tests for bulk approve (FR-014a, research R12) in `tests/api/test_purchase_requests.py`: a refused request's result has `reason` unchanged, `code` set and `params` raw; an approved one has `code` and `params` null. `tests/unit/test_order_expiry.py` still records the English `detail`.
- [X] T057 [US3] Migrate `app/services/purchase_request_service.py` (22 rows). `submit_refusal`, `mark_refusal` and `_conflict` return the code with the reason, and callers raise `DomainError` (research R8).
- [X] T058 [US3] Bulk approve: add `code: ErrorCode | None = None` and `params: dict[str, Any] | None = None` to `BulkApproveResult` in `app/schemas/purchases.py`; `approve_many` catches `DomainError` and copies `.code` and `.params` beside `reason`. Leave `app/jobs/expire_orders.py` catching `HTTPException`.
- [X] T059 [P] [US3] Migrate `app/services/purchase_order_service.py` (21 rows) and `app/services/purchase_cost_service.py` (1).
- [X] T060 [P] [US3] Migrate `app/services/expense_voucher_service.py` (14 rows; `expense_voucher.total_not_positive` stays 409).
- [X] T061 [P] [US3] Migrate `app/services/supplier_payment_service.py` (19 rows; `:332` is `facility.required`) and `app/services/supplier_agreement_service.py` (remaining rows).
- [X] T062 [US3] Migrate the remaining rows of `app/api/v1/endpoints/{purchase_orders,purchase_requests,expenses,expense_vouchers,supplier_payments}.py`.

### Fiscal documents, CSD and taxpayers

- [X] T063 [P] [US3] Tests: one per `fiscal_document.*` (44), `csd.*`, `taxpayer_certificate.*`, `taxpayer_batch.*` and `taxpayer_issuer.*` code in `tests/unit/test_invoice_service.py`, `tests/unit/test_fiscal_document_service.py`, `tests/unit/test_credit_note_service.py`, `tests/unit/test_csd_service.py`, `tests/api/test_fiscal_documents.py`, `tests/api/test_invoices.py`, `tests/api/test_fiscal_credit_notes.py`, `tests/api/test_taxpayer_certificates.py`, `tests/api/test_taxpayer_batches.py` and `tests/api/test_taxpayer_issuers.py`. Group the `fiscal_document.*` cases in a parametrized test where they share a setup. An unreadable CSD and a wrong key password each answer 422 with a `csd.*` code and no exception text in `params`.
- [X] T064 [US3] Migrate `app/services/invoice_service.py` (66 rows). `_error` and `_unprocessable` return a code with the reason, and their callers raise `DomainError` (research R8). The status comes from the code, never from the helper.
- [X] T065 [US3] Migrate `app/api/v1/endpoints/fiscal_documents.py` (10 rows; `_conflict` returns a code).
- [X] T066 [P] [US3] In `app/services/csd_service.py` (6 rows), raise `DomainError` with the `csd.*` codes instead of `ValueError`, each with today's message as `detail` unchanged (raise-map.md § csd_service lists the six). Remove the `str(exc)` handler at `app/api/v1/endpoints/taxpayer_certificates.py:50` and migrate that file's other row.
- [X] T067 [P] [US3] Migrate `app/services/taxpayer_certificate_service.py` (3 rows) and `app/services/taxpayer_batch_service.py` (4 rows), and the remaining rows of `app/api/v1/endpoints/taxpayer_issuers.py`.

### Pricing and products

- [X] T068 [P] [US3] Tests: one per `price_list.*`, `product_price.*` and `product.*` code in `tests/unit/test_price_list_service.py`, `tests/unit/test_product_price_service.py`, `tests/unit/test_product_service.py`, `tests/api/test_product_prices.py` and `tests/api/test_products.py`. `product_price.duplicate_entry` stays 400.
- [X] T069 [US3] Migrate `app/services/price_list_service.py` (4 rows; `assert_not_cost_list` raises `DomainError`), `app/services/product_price_service.py` (10 rows, including its `assert_not_cost_list` call sites and `_assert_none_missing`) and `app/services/product_service.py` (4 rows).
- [X] T070 [P] [US3] Migrate the remaining rows of `app/api/v1/endpoints/{products,price_lists,product_prices}.py`.

### Commissions

- [X] T071 [P] [US3] Tests: one per `commission_agent.*` and `commission_period.*` code in `tests/unit/test_commission_rules.py`, `tests/api/test_commission_periods.py` and `tests/api/test_commission_settings.py`.
- [X] T072 [US3] Migrate `app/services/commission_service.py` (27 rows). Its two builders (`_conflict` and `_not_found`/`_get_or_404`) take a code.

### Receivables, payables and email

- [X] T073 [P] [US3] Tests: one per `email.*` code in `tests/unit/test_account_email.py`, `tests/unit/test_fiscal_email.py`, `tests/unit/test_quote_email.py`, `tests/api/test_fiscal_email.py`, `tests/api/test_quote_document_email.py` and `tests/api/test_accounts_payable.py`. `email.not_configured` stays 503 and `email.send_failed` 502, and `params` never carries exception text.
- [X] T074 [US3] Migrate `app/services/account_email.py` (6 rows; `_unprocessable` takes a code), `app/services/fiscal_email.py` (3), `app/services/sales_quote_service.py:557`, `app/services/accounts_service.py` (remaining rows) and the `email.not_configured` rows at `app/api/v1/endpoints/accounts_payable.py:118`, `accounts_receivable.py:146` and `sales_quotes.py:109`.

### Users and remaining master data

- [X] T075 [P] [US3] Tests: one per `user.*`, `user_profile.*`, `point_of_sale.*`, `facility.*` and `warehouse.*` code not yet covered, in `tests/api/test_user_profiles.py`, `tests/api/test_facilities.py`, `tests/api/test_master_data_privileges.py`, `tests/api/test_suppliers_employees.py` and `tests/unit/test_facility_service.py`.
- [X] T076 [US3] Migrate every remaining row of `app/api/v1/endpoints/{users,user_profiles,facilities,points_of_sale,warehouses,exchange_rates,suppliers,employees,taxpayer_recipients}.py` and `app/services/{user_profile_service,exchange_rate_service}.py`.

### Schema validators (FR-015, research R6)

- [X] T077 [P] [US3] Create `tests/unit/test_validation_types.py`: one parametrized case per validation type in `contracts/error-codes.md` § Validation types (21 plus `password_policy`), posting a body that fails it and asserting the entry's `type`, `loc`, `msg` without "Value error, ", and each `ctx` key with its raw type. Search `tests/` for "Value error, " and update any assertion that relied on the prefix.
- [X] T078 [US3] Replace the 21 `raise ValueError(` sites in `app/schemas/{user,product,core,supplier_payments,fiscal,inventory,accounts,sales_quote,supplier,purchases}.py` with `PydanticCustomError('<type>', '<today's sentence>', ctx)` using the types and `ctx` keys in the contract. Build `ctx` raw: dates with `isoformat()`, decimals as strings, ints as ints.

### Area close-out

- [X] T079 [US3] Convert the remaining `detail` assertions in the US3 areas' tests to `assert_problem` plus params (FR-021), except the dict details. Run the full suite. Commit per area as each block above finishes ("phase 4: <area>").

**Checkpoint**: Every raise in `raise-map.md` is migrated. `grep -rn "HTTPException(" app/` lists only `app/core/errors.py`.

---

## Phase 6: User Story 4 - No new free-text error can slip in (Priority: P3)

**Goal**: Design phase 5. The guards become enforced.

**Independent Test**: Add `raise HTTPException(status_code=400, detail='x')` to any service, or a code to `ErrorCode` without documenting it: the suite fails, naming the file and line or the code. Revert.

- [X] T080 [US4] Add an SC-007 guard to `tests/unit/test_error_codes.py`: an AST scan of `app/schemas/` finds no `raise ValueError(` (passes after T078).
- [X] T081 [US4] Run `grep -rn "HTTPException(" app/` and migrate any straggler not in `raise-map.md`, adding a row to `raise-map.md` and, if it needs a new code, to `contracts/error-codes.md` and `ErrorCode` (never renaming an existing one).
- [X] T082 [US4] Remove `xfail` from the `HTTPException(` guard in `tests/unit/test_error_codes.py` and confirm it passes. Prove it bites: add an inline `HTTPException(` to a scratch copy of a service, see the test fail naming file and line, and revert.
- [X] T083 [US4] Remove `xfail` from the coverage guard in `tests/unit/test_error_codes.py`. For each code it reports untested, add the missing test in its area's test file (FR-020, SC-008).
- [X] T084 [US4] Prove the contract-docs test bites: add a member to `ErrorCode` without a row in `contracts/error-codes.md`, see `tests/unit/test_contract_docs.py` fail, and revert.

**Checkpoint**: All four stories hold; the guards keep them holding.

---

## Phase 7: Polish & cross-cutting

- [ ] T085 [P] Add the change to `CHANGELOG.md` under `[Unreleased]` (FR-018): **Added** the Problem body, the `ErrorCode` catalog in OpenAPI, `BulkApproveResult.code`/`params` and the inventory count batch's `code`; **Changed** errors served as `application/problem+json`, validator `type`s and the `msg` prefix, and public names in still-referenced errors; a note that phase 3 (dict details and warnings) is still to come and will be breaking.
- [ ] T086 [P] Search `docs/` for where the error format is described (`detail`, "error response") and note the Problem contract there, linking `specs/036-localizable-error-codes/contracts/error-codes.md`. If no such place exists, record that in this task's line and add nothing.
- [ ] T087 Reconcile `raise-map.md` with the code: fix line numbers that drifted, and mark any row whose code changed during implementation (contract and enum changed with it). Then rerun the raise-map ⇄ contract check (every row's status equals its code's status, every row's code is in the catalog) and expect 0 mismatches (SC-005).
- [ ] T088 Final gate: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q` pass with no xfail left from this spec; run quickstart §1's local spot checks against `uv run uvicorn app.main:app --port 8000`.
- [ ] T089 Open the PR from `036-localizable-error-codes`, with `Closes mictlanix/mbe-roadmap#147` under "Roadmap" (or `Part of` if the user prefers, since phase 3 remains), and the summary, behaviour changes and test counts.
- [ ] T090 Deploy the branch to xolotl dev :8765 as `mictlanix@` (pre-authorized). **Ask the user before** creating quickstart §3's test rows in mbe_dev, then run the three checks through nginx, record the responses as spec verifications, delete the test rows, and confirm 0 rows left and no 500s in the dev log (SC-004).

### After merge

- [ ] T091 Open the mbe-ui issue (FR-019, quickstart §4): link `contracts/error-codes.md` on main, list the codes by area with params and statuses, and name what phase 3 changes (the four dict details, `ItineraryResponse.warnings`, the four inventory and purchasing warning lists).
- [ ] T092 Delete the branch locally and remotely, remove the worktree, record the verifications, run `--mark-complete` for `specs/036-localizable-error-codes`, and commit its `.spec-context.json` to main.

---

## Dependencies & execution order

### Phase dependencies

- **Setup (T001)** → **Foundational (T002–T011)** → stories.
- **US1 (Phase 3)** needs Foundational. It goes first: its `not_found()` sweep (T024–T027) touches the routers and services every later story finishes, so doing it once avoids conflicting edits.
- **US2 (Phase 4)** needs US1's T025 (sales and cash 404s) and T020 (references). It doesn't need T024, T026 or T027.
- **US3 (Phase 5)** needs US1. Each of its area blocks is independent of the others, except that T049 needs T048 and T066 changes a file T027 touched.
- **US4 (Phase 6)** needs every migration (US1–US3) finished, since its guards check the whole of `app/`.
- **Polish (Phase 7)** needs US4. T090 needs T089's branch pushed; T091–T092 need the merge.

### Within each area

- Tests before the migration they cover; watch them fail first.
- Services before their routers where a router re-raises or wraps a service error (T048 before T049, T066's service before its router, T053's service before its route).
- Helpers that return codes (R8) before their call sites in the same file.

### Note on the xfail guards

T009's two guards are `xfail(strict=True)`. If one starts passing before T082 or T083, the suite fails: that means a phase finished earlier than recorded, and the right move is to drop the mark then.

## Parallel opportunities

- **Foundational:** T002, T003 and T007 together; then T004; then T005, T006, T008, T009 and T010.
- **US1:** the tests T012–T018 together; T021, T022 together; the four sweeps T024–T027 together (disjoint files).
- **US2:** the tests T029–T034 together; T036–T039 together.
- **US3:** the area blocks can run side by side, one per worker, since their files are disjoint. Each block's test task is [P] with the other blocks' tests.

### Example: US3 fan-out

```text
Worker 1: T044 → T045 → T046            (deliveries)
Worker 2: T047 → T048 → T049            (images)
Worker 3: T050, T051 → T052 → T053 → T054  (inventory)
Worker 4: T055, T056 → T057 → T058 → T059–T062  (purchasing)
Worker 5: T063 → T064 → T065 → T066 → T067  (fiscal)
Worker 6: T068 → T069 → T070; T071 → T072  (pricing, commissions)
Worker 7: T073 → T074; T075 → T076; T077 → T078  (email, users, validators)
```

All workers share the worktree, so they edit only their block's files; `raise-map.md` and `contracts/error-codes.md` changes go through the orchestrator.

## Implementation strategy

### MVP first (US1)

1. Setup and Foundational.
2. US1: the generic codes.
3. **Stop and check:** the independent test for US1 passes. The handler, the contract and half of all raises are coded, and mbe-ui could start its Spanish catalog with not found, duplicates, blockers and auth.

### Incremental delivery

All four stories ship in one PR (plan, Delivery), with one commit per design phase and per US3 area, each green:

1. Phase 0 (Foundational) → 2. Phase 1 (US1) → 3. Phase 2 (US2) → 4. Phase 4 by area (US3) → 5. Phase 5 (US4) → 6. Polish, dev checks, PR.

The strict xfail guards make each step's completeness visible: the suite passes at every commit, and the guards turn green only when the whole migration is done.
