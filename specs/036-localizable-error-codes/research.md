# Research: Localizable Error Codes

Decisions for spec 036. The design doc ("mbe-api: Localizable error codes", 21 Oct 2026) is the starting point. Where a decision departs from it, the departure is named. Counts are on main at 6a1fd4b. The infrastructure behaviour (R2, R3, R5) was checked with a throwaway FastAPI 0.136.3 app, not assumed.

## R1. Where the pieces live

**Decision**:

| Piece | Location |
|---|---|
| `ErrorCode` StrEnum | `app/core/error_codes.py` |
| `DomainError`, `not_found()`, `RESOURCE_NAMES`, `problem_body()`, the HTTP-exception handler | `app/core/errors.py` |
| `Problem` and `ProblemPart` (OpenAPI only) | `app/schemas/problem.py` |
| Handler registration, and the `IntegrityError` and 500 bodies | `app/main.py` (rebuilt with `problem_body()`) |

**Rationale**:
- The design names `app/core/error_codes.py` and `app/core/errors.py`. The phase 5 guard allows `HTTPException(` only in `errors.py`, so everything that constructs one has to live there.
- Response models live in `app/schemas`.
- `app/main.py` already holds the `IntegrityError` handler and the 500 middleware.

**Alternatives considered**:
- Per-area exception classes: these are more types with no added meaning, and the code already carries the meaning.
- A separate `resources.py`: the registry is used only by `not_found()` and `references`, so it sits beside them.

## R2. `ErrorCode` carries its status; `DomainError` takes the code, not a status

**Decision**:
- **One line per code:** each member is defined as `'resource.not_found', 404`. A custom `__new__` stores `.status` and keeps the value a plain string. Verified: Pydantic and OpenAPI export it as `{"type": "string", "enum": [...]}`, and `ErrorCode('resource.conflict').status == 409`.
- **Signature:** `DomainError(code, detail, params=None, *, errors=None, headers=None)`, with status `code.status`.
- **Param normalization at construction:**
  - `Decimal` → `format(d, 'f')`;
  - `date`/`datetime` → `isoformat()`;
  - `Enum` → `.value`;
  - lists, tuples and dicts recursively;
  - `None` values dropped.

  Unit tests that catch the exception therefore see the same raw types the client gets.

**Rationale**:
- **Status drift:** the spec requires one status per code (FR-002), and a separate status argument would let a raise pair the two wrongly.
- **Money:** the prototype showed FastAPI's `jsonable_encoder` turning `Decimal('18450.00')` into `18450.0`. That breaks FR-003's decimal strings and loses the scale. Normalizing in the constructor rather than the handler also covers the 200-body uses (R12).

**Alternatives considered**:
- A `dict[ErrorCode, int]` beside a plain enum: it can drift and needs a test to keep it in step.
- A status argument on every raise: that's 376 chances to disagree with the catalog.
- Normalizing in the handler only: the exception's `.params` would then differ from the body.

## R3. One handler, on Starlette's `HTTPException`

**Decision**:
- `@app.exception_handler(StarletteHTTPException)` answers every HTTP exception with `problem_body()` as `application/problem+json`, keeping `exc.headers`; `WWW-Authenticate: Bearer` survives, as verified.
- A `DomainError` contributes its code, params and errors. Any other HTTP exception can only come from the framework once phase 5 is done, and maps by status:

  | Status | Code |
  |---|---|
  | 400 | `request.malformed` (FastAPI's "There was an error parsing the body") |
  | 401 | `auth.not_authenticated` (`OAuth2PasswordBearer`'s "Not authenticated") |
  | 404 | `request.route_not_found` |
  | 405 | `request.method_not_allowed` |

  Any other framework status keeps that status and gets `request.malformed`; none occurs today.
- `RequestValidationError` keeps FastAPI's default handler.

**Rationale**:
- This is "one handler" in the design's sense: `DomainError` subclasses FastAPI's `HTTPException`, which subclasses Starlette's, so one registration covers our errors and the framework's.
- The spec's assumption that framework errors carry codes costs only the four-entry map.

**Alternatives considered**:
- A handler for `DomainError` only: framework 401/404/405 would stay code-less, and an expired session (401 "Not authenticated") is user-facing.
- Replacing `OAuth2PasswordBearer` to raise `DomainError`: that touches auth for a body shape.

## R4. The `IntegrityError` backstop and the 500

**Decision**:
- **Backstop:** `integrity_error_handler` answers `problem_body(409, resource.conflict, 'The request conflicts with existing data')`.
- **500:** `UnhandledErrorMiddleware` answers `problem_body(500, server.error, 'Internal server error')`.

Both are served as `application/problem+json`, with the same `detail` as today.

**Rationale**: FR-001 and SC-001 cover every error the app produces. The 500 stays generic (VII).

## R5. OpenAPI: `Problem` as the v1 default response

**Decision**:
- **Router default:** `api_router = APIRouter(responses={'default': {'model': Problem, 'description': 'Error (RFC 9457 Problem Details)', 'content': {'application/problem+json': {'schema': {'$ref': '#/components/schemas/Problem'}}}}})`.
- **`Problem`:**
  - `status: int`;
  - `code: ErrorCode`;
  - `detail: str | dict[str, Any]`, because of the four kept dicts (FR-012); phase 3 narrows it to `str`;
  - `params: dict[str, Any] | None`;
  - `errors: list[ProblemPart] | None`.
- **`ProblemPart`:** `code: ErrorCode`, `params: dict | None`, plus extra keys allowed. The inventory count items keep `index`, `product` or `product_code`, and `reason` (R11).

**Finding**: FastAPI always lists the route's own media type, `application/json`, next to the declared one, so the default response shows both, each referencing `Problem`. Removing the `application/json` entry needs an `app.openapi()` override, which isn't worth it: both entries describe the same schema, and errors are in fact served as `application/problem+json`. Generated clients ignore `default` for return types; for mbe-ui's dart-dio client that is a regenerated model and no call-site change.

**Alternatives considered**: per-route `responses={404: …, 409: …}`. That's 300+ edits, and it would drift.

## R6. Validation types: `PydanticCustomError` with a `type`

**Decision**: Each of the 21 `raise ValueError(` sites in `app/schemas` becomes `raise PydanticCustomError('<domain>_<condition>', '<today's sentence>', ctx)`. The types and `ctx` keys are listed in `contracts/error-codes.md` ("Validation types"). `ctx` values are built raw:
- dates use `isoformat()`;
- decimals become strings;
- ints stay ints.

Pydantic doesn't coerce `ctx`, and FastAPI's encoder would otherwise float a Decimal.

**Finding**: the spec said 23 in 11 files. The count is 21 raise sites in 10 files, because two shared helpers apply to two fields each. The spec is corrected.

**Change accepted**: Pydantic prefixes a plain `ValueError`'s message with "Value error, ". A `PydanticCustomError`'s `msg` is the template alone, so `msg` loses that prefix. mbe-ui's `_fieldErrorsFrom` reads `loc`, `msg` and `ctx.failed`. Losing the prefix improves what it shows; no test asserts the prefix (to be confirmed by the suite in phase 4).

**Alternatives considered**: keeping `ValueError` and parsing `msg` on the client. That is exactly what this spec removes.

## R7. Public resource names

**Decision**: `RESOURCE_NAMES: dict[str, str]` maps every table on the metadata (101: the 94 mapped classes' tables plus 7 association tables, which can block a delete too) to its public name, and a test fails when a table is missing. The names:
- **Table name kept:** most tables, such as `customer` and `sales_order`.
- **Renamed:**
  - `*_detail` → `*_line`;
  - `deliveries_itinerary*` → `delivery_itinerary*`;
  - `lot_serial_rqmt` → `stock_reservation`;
  - `lot_serial_tracking` → `stock_movement`;
  - `expenses` → `expense`;
  - `point_sale` → `point_of_sale`;
  - `commissions_history` → `commission_history`;
  - `access_privilege` → `privilege`.
- **Non-table names:** a few 404s name things that aren't tables: `proof_of_delivery_image`, `license_photo`, `receivable`, `product_image`. These are an explicit `EXTRA_RESOURCES` set in the same module, and the test checks that every `not_found` resource string is in one of the two.

The resource names reach the client in three places:
- **`not_found(resource, id=None, detail=None)`** takes a model class or a name. Its detail defaults to "<Name> not found"; where today's text differs, the call passes today's text.
- **`assert_unique`** passes `resource` and `field`.
- **`assert_not_referenced`** maps each `(table, column)` blocker to its resource and sums the counts per resource, so a transfer's source and destination count as one `inventory_transfer` entry. It lists the five largest, `more` counts the rest, and the `detail` sentence uses the same public names.

**Rationale**:
- 93 of 94 class names equal their table names, so deriving names from classes would expose table names by another route (spec clarification).
- An explicit registry makes a new table's public name a decision, enforced by a test (SC-006).

**Alternatives considered**:
- Identity by default with a few overrides: a new table leaks its name silently.
- Listing a table twice by column: the existing code comment explains why that tells the client nothing; summing serves the same purpose with public names.

## R8. Shared refusal helpers return codes

**Decision**: helpers that compute a reason and let the caller pick the status return a code with the reason. The caller raises `DomainError(code, reason, params)`. These are:
- `purchase_request_service.submit_refusal`, `mark_refusal`, `_conflict`;
- `invoice_service._error`, `_unprocessable`;
- `fiscal_documents._conflict`;
- `price_list_service.assert_not_cost_list`;
- `commission_service`'s two builders;
- `account_email`'s builder;
- `inventory_documents.busy_as_conflict` and `assert_facility`, which gains a `resource` argument.

`raise-map.md` lists every call site and its code.

**Rationale**: the design's "one-line migrations" for the shared helpers. The helpers' call sites are where the codes differ, as the workers' expansion showed: 376 constructions turn out to be about 530 call sites.

## R9. Tests: the helper, the guards, and keeping `detail` assertions green

**Decision**:
- **`tests/problem.py`:** `assert_problem(response, status, code) -> dict` asserts status, `content-type: application/problem+json`, `body['status']` and `body['code']`, and returns the body for params assertions. Unit tests that call services directly assert `exc.value.code` and `exc.value.params`.
- **`tests/unit/test_error_codes.py`:**
  1. Every member matches `^[a-z][a-z_]*\.[a-z][a-z_]*$` and has a status in 400–599.
  2. **Guard (FR-016, SC-002):** an AST scan of `app/` finds no `HTTPException(` call outside `app/core/errors.py`.
  3. **Coverage (SC-008):** every `ErrorCode` appears in some test file other than this one, as its value (`'resource.not_found'`) or its member (`ErrorCode.RESOURCE_NOT_FOUND`). The `request.*` and `server.error` codes are covered by `tests/unit/test_errors.py`.
  4. **Registry (SC-006):** every mapped table is a key of `RESOURCE_NAMES`, and no public name equals a table name that was renamed.
- **`tests/unit/test_contract_docs.py`:** parses `specs/036-localizable-error-codes/contracts/error-codes.md` table rows whose first cell is a backticked `domain.condition` and compares `{code: status}` with `ErrorCode` in both directions (FR-017, SC-003). The parser-still-finds test pins the row count.
- **Order:** the guards are written first (phase 0) and marked `xfail(strict=True)` until phase 5, when the marks are removed. A strict xfail that starts passing early fails the suite, which flags a phase finished sooner than recorded.
- **Existing `detail` assertions** (240 references in 76 files) keep passing throughout, because `detail` doesn't change. As each area migrates, its assertions convert to `assert_problem` plus params. The exceptions are `password_change_required` and the four dict details, where `detail` is the contract.

**Rationale**: FR-020 and FR-021. The coverage guard is the only mechanical way to know each of 241 codes is exercised.

## R10. The catalog

**Method**: three workers drafted a code for every raise in their areas from one brief (naming rules, fixed codes, resource names). The drafts were consolidated here: overlapping names were merged, five renames applied and two misreuses corrected. The full per-site mapping is `raise-map.md`; the authoritative list is `contracts/error-codes.md`.

**Result**: **241 codes**: 239 drafted, plus `auth.administrator_required` from the analysis (A1) and `inventory_receipt.warehouse_fixed`, found unmapped during implementation (T081).

| Status | Codes |
|---|---|
| 409 | 112 |
| 422 | 105 |
| 400 | 9 |
| 403 | 6 |
| 404 | 3 |
| 401 | 2 |
| 405 | 1 |
| 500 | 1 |
| 502 | 1 |
| 503 | 1 |

`fiscal_document` alone has 44 codes, because spec 027's CFDI rules are many and distinct.

**Consolidation rulings**:
- **Open session required:** `cash_session.required` (vouchers, supplier payments) and `cash_session.not_open` (refunds) are one situation, so one code: `cash_session.not_open`.
- **In-transit location:** `facility.no_transit_location` and `facility.no_in_transit_location` merge to the latter, with `facility_ids: list[int]` (an itinerary can name several).
- **Status filters:** two spellings of "status filter not accepted" merge to `status_filter.unsupported {allowed}`.
- **"A facility is required":** `inventory_transfers.py:77` and `supplier_payment_service.py:332` are about the request naming no facility, not the user lacking one, so they take `facility.required`, not `user.no_facility`.
- **Inventory stock:** `inventory.insufficient_stock` becomes `inventory_document.insufficient_stock` with `requested`/`available`, matching the design's stock params.
- **Two credit-limit situations stay apart:**
  - `customer.credit_limit_exceeded`: this credit order would pass the limit;
  - `customer.credit_limit_reached`: the customer is already at or over it, so even a cash order is refused.

  Different situations, different copy.
- **Same words, different status:** "line has no quantity" is 409 on purchase orders and 422 on purchase requests. The statuses don't move (FR-005), so these are two codes.
- **Privileges in params are raw grants:** `auth.forbidden.required` and `facility.other_facility.required` are lists of `{system_object: int, right: int}`, any one of which opens the route. These are the raw values the API already uses for privileges (`/user-profiles`) and that mbe-ui's `SystemObject(int)` and `AccessRight` enums read. Enum member names and legacy names were rejected: neither is what the API or mbe-ui uses for privileges, and FR-003 asks for raw values. `deps.py:167` and `customers.py:103` list several grants.
- **Administrator-only is its own code:** `require_admin`'s refusal is `auth.administrator_required`, not `auth.forbidden` with a pseudo-privilege. The situation and the copy differ ("only an administrator" vs "you need permission X"), and a list mixing ints and the word `administrator` breaks FR-003.
- **Composite unique keys:** `resource.already_exists.field` joins the API field names with commas (`date,base,target`).
- **Unchanged statuses that read oddly:**
  - `customer_discount.product_not_found` is a 422, not `resource.not_found`, because the product is a body field.
  - `product_price.duplicate_entry` is a 400, not `resource.already_exists` at 409.
  - `expense_voucher.total_not_positive` is a 409.
  - `delivery_order.unknown_warehouse` is a 422.

  All are kept per FR-005 and flagged in `raise-map.md`.
- **Signature changes the params need:**
  - `sales_order.below_min_quantity` needs `product_id` passed to `assert_quantity_allowed`;
  - `cash_session.user_busy` needs the blocking session's id kept where it's found;
  - `inventory_documents.assert_facility` needs a `resource` argument.
- **Deliberately not a facility code:** `credit_notes.py:78` answers 404 for another facility's credit note, to hide it, so it stays `resource.not_found`.
- **Not user-facing, no code:** `image_service.delete_image`'s "Invalid image filename" `ValueError` (today a 500, from no request path), `stock_ledger.py:38/88` (programming contracts) and `rendering/fetcher.py:35`. These stay as they are.

## R11. The inventory count batch refusal

**Decision**: `BatchRefused` becomes the route's `DomainError(inventory_count.batch_invalid, str(refusal), errors=items)`. Each item keeps `index`, `product` or `product_code`, and `reason`, and gains `code`:

| Reason | Code |
|---|---|
| Ambiguous product | `inventory_count_line.product_reference` |
| Unknown product | `inventory_count_line.product_unknown` |
| Not stockable | `inventory_count_line.not_stockable` |
| Repeated in the batch | `inventory_count_line.repeated` |

`validate_batch` returns the code with each reason. The route's hand-built `JSONResponse` goes away.

**Rationale**: spec FR-012a. The body already had `errors`, so this is additive and uses `errors[]` as the design means it. mbe-ui doesn't call this route yet.

## R12. Bulk approve results

**Decision**: `BulkApproveResult` gains `code: ErrorCode | None` and `params: dict[str, Any] | None`. `approve_many` catches `DomainError`, not `HTTPException`, and copies `.code` and `.params` beside `reason` (`refused.detail`).

**Rationale**: spec FR-014a. `order_expiry` still catches `HTTPException`, a superclass, and records `str(exc.detail)` unchanged. It's a CLI job.

## R13. Verification on dev (SC-004)

**Decision**: after the branch is deployed to xolotl dev :8765 (pre-authorized), quickstart §3 runs requests against the three mbe-ui dependencies and checks body and content type:
1. A user flagged `must_change_password` → 403 `auth.password_change_required`, `detail == "password_change_required"`.
2. A credit order for a customer on credit hold → 422 with a string `detail` and `customer.credit_hold`.
3. A confirm with zero-priced lines → 409 with the dict `detail` and a top-level `code`.

The test records it creates are deleted, as in spec 035's races. A write to mbe_dev needs the user's go-ahead before it runs.

**Rationale**: the spec requires checking mbe-ui's dependencies against a real deployment, not only TestClient. Content type and proxies (nginx) are part of what is checked.
