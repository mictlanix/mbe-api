# Feature Specification: Localizable Error Codes

**Feature Branch**: `036-localizable-error-codes`

**Created**: 2026-10-28

**Status**: Draft

**Input**: Every user-facing error carries a stable `code` and structured `params` next to the English `detail`, so mbe-ui shows its own Spanish message instead of the API's English sentence.
- Roadmap: mictlanix/mbe-roadmap#147 (Cross-cutting).
- Design: "mbe-api: Localizable error codes" (https://claude.ai/artifact/4NoifW4tJKZoUDxDwPssFv, 21 Oct 2026, with Augusto). It holds the contract, the catalog's naming rules and the first-wave codes. This spec takes them as given and settles the design's open questions.
- Scope: the design's phases 0, 1, 2, 4 and 5, all additive.
- Out of scope:
  - Phase 3: moving the dict-shaped details to `errors[]` and the plain-text warnings to `{code, params}`. That change breaks mbe-ui, so it ships in a later spec, after an mbe-ui release reads both shapes.
  - Server-side translation via `Accept-Language`.

## Findings that shape this spec

Counted on main at 6a1fd4b (spec 035 merged), 28 Oct 2026.

### The raises, by area and status

The design counted 236 inline `HTTPException`s on 21 Oct. Specs 027–035 brought that to **376**, across 50 router modules and the services:

| Area | 404 | 409 | 422 | 400 | 401 | 403 | 502 | 503 | variable | Total |
|---|---|---|---|---|---|---|---|---|---|---|
| Deliveries & itineraries | 27 | 21 | 27 | | | | | | | 75 |
| Cash, payments, refunds, credit notes | 25 | 19 | 7 | | | 2 | | | | 53 |
| Sales orders & quotes | 14 | 16 | 15 | | | 1 | 1 | 1 | | 48 |
| Purchasing & expenses | 22 | 5 | 11 | | | 1 | 1 | 1 | 1 | 42 |
| Inventory | 14 | 11 | 14 | | | 2 | | | | 41 |
| Master data & shared helpers | 30 | 6 | 1 | | | 1 | | | | 38 |
| Pricing & products | 20 | 2 | 1 | 4 | | | | | | 27 |
| Fiscal (CFDI, CSD, taxpayers) | 12 | 6 | 3 | | | 1 | 1 | 1 | 1 | 25 |
| Auth & users | 11 | 2 | 1 | 2 | 2 | 4 | 1 | | | 23 |
| AR / AP | 1 | | | | | 1 | | 2 | | 4 |
| **Total** | **176** | **88** | **80** | **6** | **2** | **13** | **4** | **5** | **2** | **376** |

- The "variable" raises are two helpers that pick the status from a computed reason: `invoice_service`'s error builder and `purchase_request_service`'s.
- **New since the design:**
  - The 502s and 503s: "Email is not configured" and "The email could not be sent: …" on six send routes.
  - Nine 403s from spec 034's facility rule, such as "Another facility's payments require PaymentsEditor read" and "Document belongs to another facility".
  - The 033–034 receivables, payables and credit-note errors.
  - The inventory and purchasing errors of specs 029–032.

### Shapes clients already depend on

mbe-ui (origin/main) reads errors in four places, all keyed on the status and the shape of `detail`:

| Where | What it relies on |
|---|---|
| `auth_interceptor.dart` | 403 with `detail == "password_change_required"` opens the change-password flow |
| `sales_order_repository_impl.dart` (create, update, confirm), `sales_quote_repository_impl.dart` | **any 422 whose `detail` is a string** is shown as a credit hold (`AppError.creditHold(detail)`) |
| `_toConfirmError` | a `detail` dict with `message` and `lines`, joined into one message |
| `_detailFrom`, `_fieldErrorsFrom` | `detail` as a string, a dict with `message`, or FastAPI's validation list (`loc`, `ctx.failed`) |

mbe-ui's HTTP client (Dio 5.9) decodes any `+json` content type as JSON, and its interceptor checks only that the type contains `json`. `application/problem+json` therefore reaches it parsed.

On the server, `order_expiry` records `str(exc.detail)` as a skip reason. The tests reference `detail` 240 times in 76 files.

### Dict-shaped details: four, not three

| Where | Status | Shape |
|---|---|---|
| `sales_order_service` confirm, missing delivery details (spec 033) | 409 | `{message, missing}` |
| `sales_order_service` confirm, zero-priced lines | 409 | `{message, lines}` |
| `sales_order_service` confirm, insufficient stock | 409 | `{message, lines}` |
| `delivery_itinerary_service` departure, over-committed lines | 422 | `{message, lines}` |

The user's input named three; the missing-delivery-details detail arrived with spec 033. All four stay as they are and gain a `code` (FR-012).

One more structured refusal isn't raised but built by hand: `PUT /inventory-counts/{id}/lines` answers 422 `{detail, errors: [{index, product | product_code, reason}]}` itself. `reason` is one of four English phrases. A raise count misses it, so it's listed here (FR-012a).

### Other free-text error surfaces

- **`str(e)` routers: six, not five.** Five image uploads re-raise `image_service`'s `ValueError`: product image, facility logo, licence photo, and two proof-of-delivery photos. The CSD upload re-raises `csd_service`'s.
- **Schema validators: 21 plain `ValueError`s in 10 files, not 3.** They are in `user`, `product`, `core`, `purchases`, `supplier_payments`, `fiscal`, `inventory`, `accounts`, `sales_quote` and `supplier`. FastAPI reports each one as `type: "value_error"` with the English sentence as the only distinguishing data. `password_policy` already uses a typed `PydanticCustomError`, and mbe-ui localizes it.
- **Plain-text success warnings: five lists.** `ItineraryResponse.warnings` plus four `list[str]` warnings in `schemas/inventory.py` and `schemas/purchases.py`. All five are phase 3 (out of scope).
- **The `IntegrityError` handler** answers 409 `{"detail": "The request conflicts with existing data"}`.
- **`UnhandledErrorMiddleware`** answers its own JSON 500.
- **Framework errors carry no code:** an unknown route (404), a wrong method (405), and a missing bearer token (401 "Not authenticated").
- **`references.assert_not_referenced` exposes table names** to the user today: "Still referenced by sales_order (12), …".

## Clarifications

### Session 2026-10-28 (settled from the user's input, the design's open questions)

- Q: Do 404s carry `resource` only, or `id` too? → A: Both. The id is already in the URL, and carrying it helps support match a screenshot to a request.
- Q: Are table names safe as `resource.still_referenced` resource values? → A: No. Tables map to public resource names, the same names the 404s use, and a table name is never exposed, in `params` or in `detail`.
- Q: Does `customer.credit_limit_exceeded` move from 422 to 409? → A: No, it stays 422. mbe-ui keys credit hold on 422, so a move would break it for a naming argument.
- Q: Are the schema `ValueError`s phase 4 or a follow-up? → A: Phase 4, this spec. That covers every schema file, not only the three the design named, because the input brings in everything specs 027–034 added.

### Session 2026-10-28 (analysis)

- Q: How do `params.required` name privileges? → A: As raw grants, `[{system_object: int, right: int}]`, the values the API and mbe-ui already use for privileges. An administrator-only refusal is its own code, `auth.administrator_required` (analysis A1).
- Q: Does FR-020 mean a test per raise site or per code? → A: Per code, plus one per site whose params differ; 404s on GET routes share one parametrized test (analysis I1). This narrows the input's "each migrated raise", for 556 raise-map rows.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Any error can be told apart by its code (Priority: P1)

mbe-ui developers receive every error as a Problem Details body with a stable `code` and raw `params`, and the catalog is published in the API's OpenAPI description. They can then map each error to a Spanish message, with numbers and dates formatted for Mexico, instead of showing the English sentence. This story covers the contract and the generic codes: not found, already exists, still referenced, conflicting data, document not editable, invalid state change, and authentication and privilege refusals. Together these cover about half of all raises.

**Why this priority**: Nothing else can be localized until the contract, the code catalog and the generic codes exist. Most errors a user meets are generic, such as a record not found, a duplicate code or a record still in use.

**Independent Test**: Request a missing customer, create a duplicate product code, delete a warehouse still in use, and call a route without privileges. Each answer is `application/problem+json` with `status`, `code` and `detail`, plus `params` where the code defines them. `/openapi.json` lists the Problem schema and every code.

**Acceptance Scenarios**:

1. **Given** no customer 999999, **When** a user requests it, **Then** the answer is 404, `code: "resource.not_found"`, `params: {"resource": "customer", "id": 999999}`, and `detail` still says "Customer not found".
2. **Given** a warehouse referenced by 12 sales orders and 3 transfers, **When** a user deletes it, **Then** the answer is 409 `resource.still_referenced` with `params.references` listing `{resource, count}` by public resource names. No table name appears anywhere in the body.
3. **Given** a user whose password must be changed, **When** they call any protected route, **Then** the answer is 403, `code: "auth.password_change_required"` and `detail: "password_change_required"`, exactly as today.
4. **Given** a write that a database constraint rejects, **When** no service check caught it first, **Then** the answer is 409 `resource.conflict` with the same English `detail` as today.
5. **Given** any of these errors, **When** a client reads only `detail`, **Then** it gets the same string as before this spec.

---

### User Story 2 - Sales and cash errors carry their facts as params (Priority: P1)

Cashiers and sales staff at the POS meet business-rule refusals daily: credit hold, credit limit, no cash drawer, drawer busy, quote expired, payment and refund rules. mbe-ui needs each as its own code, with the numbers the user must see (overdue count, outstanding amount, limit) as raw params. It can then say "4 pedidos a crédito vencidos" or link to the session that holds the drawer.

**Why this priority**: These are the errors users actually read every day, and the design's first wave.

**Independent Test**: Raise an order for a customer with overdue credit orders, open a session on a drawer someone else holds, and confirm a quote that has expired. Each answer carries its catalog code and typed params, and its status and `detail` are unchanged.

**Acceptance Scenarios**:

1. **Given** a customer with 4 overdue credit orders, **When** a user updates a sales order to credit terms, **Then** the answer is 422 `customer.credit_hold` with `params: {"customer_id": <int>, "overdue_count": 4, "action": "raise"}` and today's `detail`.
2. **Given** a credit order that would take the customer to 18,450.00 of a 15,000.00 limit, **When** it is confirmed, **Then** the answer is 422 `customer.credit_limit_exceeded`. `params.outstanding` and `params.limit` are decimal strings ("18450.00", "15000.00"), never formatted money.
3. **Given** a cash drawer with an open session by another cashier, **When** a user opens a session on it, **Then** the answer is 409 `cash_session.drawer_busy` with `params.cash_drawer_id`.
4. **Given** a sales order lacking delivery details, priced at zero or short of stock, **When** it is confirmed, **Then** the answer keeps today's status and dict `detail` and also carries the matching code.
5. **Given** a user without the cross-facility privilege, **When** they open another facility's payment, credit note, sales order or receivables, **Then** the answer is 403 with a facility code and `params.required` naming the privilege.

---

### User Story 3 - Every remaining area speaks in codes (Priority: P2)

Users of deliveries, itineraries, inventory, purchasing, expenses, receivables and payables, fiscal documents, pricing and products, and file uploads get coded errors too. Field validation that today reads only "Value error, …" gets a specific `type`. No screen is left showing English.

**Why this priority**: These are less frequent than the POS errors, but the goal is all user-facing errors. Specs 027–034 added most of these areas since the design was written.

**Independent Test**: For each area, trigger one business refusal and one field-validation failure, and upload a non-image as a product image. Each carries a catalog code or a typed validation error, with the same status as today.

**Acceptance Scenarios**:

1. **Given** a delivery order already departed, **When** a user edits it, **Then** the answer is 409 with a code naming that condition and `params.status` set to the raw enum value.
2. **Given** a file that is not an image, **When** a user uploads it as a product image, facility logo, licence photo or proof of delivery, **Then** the answer is 422 with an image code. The same holds for an unreadable CSD or a wrong key password on the CSD upload. No route builds an error from an exception's text.
3. **Given** a supplier payment dated in the future, **When** it is submitted, **Then** the 422 validation entry carries a specific `type` (not `value_error`) and any values in `ctx`, in FastAPI's validation shape.
4. **Given** email is not configured, **When** a user sends a quote, purchase order, statement or invoice by email, **Then** the answer keeps its 503 and carries a code. A failed send keeps its 502 and carries a code too.

---

### User Story 4 - No new free-text error can slip in (Priority: P3)

Developers adding features after this spec cannot raise an error without a code. The code catalog document stays in step with the codes the API serves.

**Why this priority**: Without the guard, the next spec reintroduces free text. It only matters once the migration is complete.

**Independent Test**: Add an inline `HTTPException(` to any service, or a code to the catalog without documenting it. The test suite fails.

**Acceptance Scenarios**:

1. **Given** the migrated code base, **When** someone raises `HTTPException(` outside the error module, **Then** a test fails naming the file and line.
2. **Given** a code in the catalog that `contracts/error-codes.md` does not list, or a documented code the catalog lacks, **When** the suite runs, **Then** the contract-docs test fails.

### Edge Cases

- **A 404 with no id in the URL**, such as a lookup by code or the current user's own profile: `params.id` is omitted, never null or the raw search text.
- **Composite and non-numeric ids**, such as a SAT catalog key or a postal code: `params.id` carries the raw value as a string.
- **A raise with nothing to parametrize** (`sales_order.no_lines`): `params` is omitted from the body, not sent empty.
- **Money and quantities** go in `params` as decimal strings with the stored scale. Dates are ISO 8601 and statuses are raw enum values. None is ever formatted text or an English word.
- **An email send failure** keeps the exception text in English `detail` only. `params` never carries exception text.
- **A table `references` doesn't know a public name for:** the mapping must cover every table that can block a delete, and a test enumerates them. An unmapped table is a test failure, not a fallback to the table name.
- **Two messages describing the same situation share a code**, distinguished by a param. The two credit-hold messages share `customer.credit_hold` and differ by `params.action`.
- **A retired code** stays in the catalog, marked deprecated, and is never reused.
- **`order_expiry`'s skip reason** still reads `str(exc.detail)` and still gets the English sentence. For the dict-shaped details it gets today's dict text, unchanged.
- **FastAPI validation 422s** keep `application/json` and their list shape. Only errors raised by the app or the framework's HTTP layer become Problem Details.

## Requirements *(mandatory)*

### Functional Requirements

**Contract**

- **FR-001**: Every error response the API produces for a client is an RFC 9457 Problem Details body served as `application/problem+json`. That covers service and router refusals, the conflicting-data backstop, unknown routes, wrong methods, a missing token and the unexpected-failure 500. The body has these fields:
  - `status` (int): mirrors the HTTP status;
  - `code` (string): from the catalog;
  - `detail`: an English string, or one of the four dicts in FR-012;
  - `params` (object, optional): omitted when empty.

  `errors[]` (items with their own `code`) is produced only by the inventory count batch (FR-012a). FastAPI's request-validation 422 is the one exception: it keeps its current shape and content type (FR-015).
- **FR-002**: Every code is `<domain>.<condition>` in lower snake case and names the situation, not the remedy. All codes live in one catalog that `/openapi.json` publishes as an enum. A code is never renamed or reused; a retired code stays in the catalog marked deprecated.
- **FR-003**: `params` carry raw values only: ids, integers, decimal strings, ISO 8601 dates and datetimes, and enum values. They never carry formatted money, formatted dates, English words or exception text.
- **FR-004**: `/openapi.json` declares the Problem schema as the default error response of every v1 route, so generated clients get a typed error.
- **FR-005**: The status code and `detail` of every existing error stay as they are today, with one exception: `resource.still_referenced` replaces table names with public resource names in `detail` (FR-008). The six `str(e)` uploads keep their `detail` too: every message the image and CSD services raise is a fixed sentence of ours, which becomes the coded error's `detail` unchanged.

**Generic codes (phase 1)**

- **FR-006**: Every "not found" answers `resource.not_found` with `params.resource` and, when the request names one, `params.id`:
  - `resource` is the public name, such as `customer` or `sales_order`;
  - `id` is the raw id: an int for numeric keys, a string otherwise.
- **FR-007**: A duplicate value answers 409 `resource.already_exists` with `params.resource` and `params.field`. This covers the shared uniqueness check and the inline duplicate checks.
- **FR-008**: A delete blocked by references answers 409 `resource.still_referenced`:
  - `params.references` is a list of `{resource, count}`, largest first, five at most;
  - `params.more` gives the number of further blockers.

  Resource names are public names from a mapping that covers every referencing table. `detail` uses the same names.
- **FR-009**: A write rejected by a database constraint that no service check caught answers 409 `resource.conflict` with today's `detail`.
- **FR-010**: The document edit guard answers 409 `document.not_editable` with `params.status`. Invalid delivery state changes answer 409 `state.transition_invalid` with `params.resource`, `from`, `to` and, where relevant, `required`.
- **FR-011**: Authentication and privilege refusals carry codes:
  - bad credentials: 401 `auth.invalid_credentials`;
  - a missing or invalid token: 401;
  - a required password change: 403 `auth.password_change_required`, keeping `detail: "password_change_required"` exactly;
  - an administrator-only route: 403 `auth.administrator_required`;
  - a missing privilege: 403 `auth.forbidden`, with `params.required` listing the grants that would open the route, each `{system_object, right}` as the raw ints the API uses for privileges.

**Sales and cash (phase 2)**

- **FR-012**: The sales and cash raises carry the design's first-wave codes and params, with statuses as today:
  - `customer.credit_hold` (`customer_id`, `overdue_count`, `action`);
  - `customer.credit_limit_exceeded` (422; `customer_id`, `outstanding`, `limit`);
  - `customer.no_credit_limit`, `customer.walk_in_no_credit`;
  - `sales_order.*`, `user.no_point_of_sale`, `user.no_facility`, `user.no_cash_drawer`;
  - `cash_session.*`, `quote.*`, `payment.*` and `refund.*`.

  The four dict-shaped details (missing delivery details, zero-priced lines, insufficient stock, over-committed itinerary lines) keep their status and their dict `detail` byte for byte, and gain `status` and a `code` at the top level.
- **FR-012a**: The inventory count batch refusal keeps its 422, its `detail` and every key of its `errors[]` items, and is served as a Problem. It gains a top-level `status` and `code`, and each item gains a `code` naming its reason: ambiguous product, unknown product, not stockable, or repeated in the batch.
- **FR-013**: Spec 034's facility rule answers 403 with one facility code wherever a record or requested facility is another facility's and the caller lacks the cross-facility privilege. `params.required` names that privilege, in the same `{system_object, right}` list shape as `auth.forbidden`. This covers payments, credit notes, sales orders, receivables and inventory documents. The receivables credit hold from spec 033 is `customer.credit_hold`.

**Remaining areas (phase 4)**

- **FR-014**: Every other raise carries a code assigned with the same rules. This covers deliveries, itineraries, vehicles and operators, inventory, purchasing, expenses, supplier payments, receivables and payables, fiscal documents and CSD, taxpayers, pricing, products, commissions, users and email sending. The image and CSD services raise coded errors themselves, and the six routers that turned `str(e)` into a 422 no longer build errors from exception text.
- **FR-014a**: `POST /purchase-requests/approve` (bulk approve) answers 200 with one result per request, and a refused request carries an English `reason`. Each result gains `code` and `params` beside `reason`: the code of the refusal, null when the request was approved. `reason` stays. The internal expiry sweep (`app/jobs/expire_orders.py`) keeps recording the English `detail`; it isn't user-facing.
- **FR-015**: Every custom validation failure in a request schema reports a specific `type` (`<domain>_<condition>`, like `password_policy`) and puts its values in `ctx`. That covers all 21 plain `ValueError`s in the 10 schema files. FastAPI's validation response keeps its list shape, `loc`, `msg` and `application/json`.

**Guard and documentation (phase 5)**

- **FR-016**: A test fails when `HTTPException(` is constructed anywhere in `app/` outside the error module.
- **FR-017**: `specs/036-localizable-error-codes/contracts/error-codes.md` lists every code with its status, its params and their types, and the situation it names. The contract-docs test fails when the document and the served catalog differ in either direction.
- **FR-018**: `CHANGELOG.md` records the change under `[Unreleased]` as additive. It notes that phase 3 (the dict details and the warnings) is still to come and will be breaking.
- **FR-019**: After merge, an issue in mictlanix/mbe-ui hands over the code list (contract doc link, codes, params and statuses) so mbe-ui can add its Spanish catalog. It also names the four dict details and five warning lists that phase 3 will change.

**Tests**

- **FR-020**: Each code has a test for every distinct way it is raised: one test per code, plus one per further raise site whose params differ in keys or in where their values come from. Every `resource.not_found` on a GET route with an id is covered by one parametrized test. Each test asserts the status, the code, the params keys and the raw type of each param. Tests use a shared `assert_problem(response, status, code)` helper.
- **FR-021**: Existing assertions on `detail` text keep passing while each area migrates. Once an area is done, they are converted to code assertions, except where `detail` itself is the contract (`password_change_required`, the four dict details).

### Key Entities

- **Problem**: the error body. Fields: `status`, `code`, `detail`, optional `params`, optional `errors[]` (only the inventory count batch fills it).
- **Error code**: a member of the published catalog. Each has a `<domain>.<condition>` name, one HTTP status, a fixed set of param names with raw types, and a lifecycle (active or deprecated, never removed).
- **Public resource name**: the snake-case name a client sees for an entity (`customer`, `sales_order`, `warehouse`). It is shared by not-found, already-exists and still-referenced, and mapped from each table that can appear in a reference check.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every error raised by the app answers with a catalog code: 376 of 376 of today's raises, plus the conflicting-data backstop, framework 401/404/405 and the unexpected-failure 500. Request validation 422s are the one exception and keep their typed `type`.
- **SC-002**: 0 inline `HTTPException(` outside the error module, enforced by a test.
- **SC-003**: 100% of catalog codes appear in `contracts/error-codes.md` and vice versa, enforced by a test.
- **SC-004**: mbe-ui's three dependencies behave as today against a dev deployment of the branch, checked by request before merge:
  - the change-password redirect;
  - the credit-hold message on a 422 from order create, update and confirm and quote convert;
  - the per-line confirm message.
- **SC-005**: 0 status-code changes, compared before and after by the existing suite plus the new code assertions. 0 changes to `detail` text outside FR-005's one exception. Before merge, every `raise-map.md` row's status equals its code's status in the catalog.
- **SC-006**: 0 table names in any error body, including the still-referenced and conflict answers. A test sweeps every referencing table.
- **SC-007**: 0 plain `value_error` types left from the API's own schema validators.
- **SC-008**: Every catalog code is asserted by at least one test, enforced by a test.

## Assumptions

- **Status codes don't move**, including where the design or a reader might prefer another, such as `customer.credit_limit_exceeded` at 422. mbe-ui keys behaviour on statuses.
- **Framework errors are in scope**: unknown route, wrong method, missing token and the 500 middleware. They get generic codes, because "every user-facing error" includes an expired session. They change no status and no `detail`.
- **Exact names are settled in planning.** The design's catalog fixes the generic and first-wave names. Codes for later areas, and the exact facility, image, CSD and email codes, are assigned during planning with the design's naming rules, and all land in `contracts/error-codes.md`.
- **`errors[]`** is in the Problem schema now. Only the inventory count batch fills it, because its body already had that list. The confirm and itinerary dicts move to it in phase 3.
- **The success warnings are untouched**: `ItineraryResponse.warnings` and the four inventory and purchasing warning lists stay `list[str]` (phase 3).
- **No database change and no new dependency.** `PydanticCustomError` comes from `pydantic_core`, which is already in use.
- **Dev verification is pre-authorized:** deploying the branch to xolotl dev :8765 is allowed. The SC-004 checks read and write only test records they create and delete.
- **mbe-ui adds its Spanish catalog in its own repository.** This spec delivers the codes and the handoff issue.
