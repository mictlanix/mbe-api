# Implementation Plan: Localizable Error Codes

**Branch**: `036-localizable-error-codes` | **Date**: 2026-10-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/036-localizable-error-codes/spec.md`

## Summary

Every error the API raises becomes a Problem Details body (`application/problem+json`): `status`, a stable `code` from one published catalog of 241 codes, today's English `detail`, and raw `params`. Statuses and `detail` text don't change, so mbe-ui keeps working until it reads the codes. FastAPI's request-validation 422 keeps its shape, and its 21 custom validators get specific `type`s.

The approach (research R1–R13):
- **Types:** one `ErrorCode` StrEnum whose members carry their status; one `DomainError(HTTPException)` whose status comes from its code and which normalizes params (Decimal stays a string); one `not_found()` helper.
- **Handler:** one handler on Starlette's `HTTPException`, covering our errors and the framework's 400/401/404/405. The `IntegrityError` backstop and the 500 middleware build the same body.
- **OpenAPI:** `Problem` is the v1 router's default response.
- **Public names:** a registry maps all 101 tables (94 mapped classes plus 7 association tables) to public resource names.
- **Migration:** about 530 raise call sites move to the catalog area by area, following `raise-map.md`. Each code is asserted by a test.
- **Guards:** tests reject any inline `HTTPException(`, any untested code, any code missing from `contracts/error-codes.md`, and any unnamed table.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI 0.136.3, Starlette 1.3.1, Pydantic 2.13.4 (`PydanticCustomError` from `pydantic_core`, already used by `app/core/password_policy.py`). No new dependency.

**Storage**: MariaDB 10.11 (mbe_dev). No schema change, no migration.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport` (SQLite). New: `tests/problem.py::assert_problem`, `tests/unit/test_error_codes.py`, `tests/unit/test_errors.py`.

**Target Platform**: Linux server (xolotl); dev :8765 for SC-004.

**Project Type**: web service (REST API under `/api/v1`).

**Performance Goals**: none new. Building the error body is one dict per error.

**Constraints**:
- Statuses and `detail` text are unchanged (FR-005).
- mbe-ui (Dio 5.9) parses `+json`.
- Request validation keeps `application/json`.

**Scale/Scope**:
- 376 `HTTPException` constructions in 85 modules, expanding to about 530 call sites once the helpers are counted.
- 6 `str(e)` routers over 2 services.
- 21 schema validators.
- 1 hand-built 422.
- 1 bulk result.
- 240 `detail` references in 76 test files.
- 241 codes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | One error type, one enum, one handler, one 404 helper, one name registry. No per-area exception classes or translation layer. Param normalization lives in `DomainError` because Decimal would otherwise serialize as a float (R2). | Pass |
| II. Think Before Coding | The design's numbers were recounted: 376 raises, 4 dicts, 6 routers, 21 validators. Two hidden error paths are covered: the inventory count batch and bulk approve. The departures from the design are named: framework codes (R3), the double media type (R5) and the dropped "Value error, " prefix (R6). | Pass |
| III. Surgical Changes | Every raise site changes, which is the task. Messages and statuses stay byte for byte. Helpers change only to return a code with their reason (R8). Three signatures gain one argument because params need it (R10). | Pass |
| IV. Goal-Driven Execution | Guard tests (SC-002/003/006/008), per-code tests (FR-020) and the dev checks of mbe-ui's dependencies (SC-004, quickstart §3). | Pass |
| V. Reuse Over Rebuild | Subclassing `HTTPException` keeps every `except HTTPException` and `exc.detail` working. `find_blocking_references` stays the source of blockers. Validators reuse the `password_policy` pattern. The new modules are justified below. | Pass, see Complexity Tracking |
| VI. Async-First | No new DB access; the handlers are `async`. | Pass |
| VII. Security by Default | Table names leave error bodies (SC-006). Email failures keep exception text out of `params`. The 500 stays generic. No auth change. | Pass |
| VIII. Ruff Compliance | `uv run ruff check app/ migrations/ tests/` gates each commit. | Pass |
| Testing | Tests first per area. The guards are written in phase 0 as strict xfail and enforced in phase 5 (R9). | Pass |
| Changelog | `[Unreleased]` gains Added (Problem, codes, `BulkApproveResult.code/params`, batch `code`) and Changed (content type, validator types, `msg` prefix, still-referenced names). It notes phase 3 as breaking and still to come. | Pass |

**Post-design re-check**: unchanged. Phase 1 added three app modules, one test helper and two test files. No other abstraction.

## Project Structure

### Documentation (this feature)

```text
specs/036-localizable-error-codes/
├── plan.md               # This file
├── research.md           # R1–R13
├── data-model.md         # Problem, ProblemPart, ErrorCode, DomainError, resource names, validation types
├── quickstart.md         # local checks, dev checks (SC-004), the mbe-ui handoff
├── raise-map.md          # every raise site → code (drafted per area, consolidated)
├── contracts/
│   ├── api.md            # body shapes, unchanged mbe-ui shapes, the two changed models
│   └── error-codes.md    # 241 codes + 22 validation types (FR-017; checked by test_contract_docs)
└── tasks.md              # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── core/
│   ├── error_codes.py        # NEW  ErrorCode(StrEnum): value + .status
│   ├── errors.py             # NEW  DomainError, not_found(), RESOURCE_NAMES, EXTRA_RESOURCES, problem_body(), http_exception_handler
│   └── deps.py               # auth codes
├── schemas/
│   ├── problem.py            # NEW  Problem, ProblemPart
│   ├── purchases.py          # BulkApproveResult.code/params; validator types
│   └── user.py, product.py, core.py, supplier_payments.py, fiscal.py, inventory.py,
│       accounts.py, sales_quote.py, supplier.py   # validator types
├── services/
│   ├── references.py         # still_referenced / already_exists with public names
│   ├── documents.py          # document.not_editable
│   ├── delivery_events.py    # state.transition_invalid
│   ├── image_service.py, csd_service.py           # raise DomainError (image.*, csd.*)
│   ├── inventory_count_service.py                 # batch item codes
│   └── every service in raise-map.md
├── api/v1/
│   ├── router.py             # default Problem response
│   └── endpoints/*.py        # 404s → not_found(); six try/except str(e) removed; inventory_counts batch
└── main.py                   # handler registration; IntegrityError and 500 bodies

tests/
├── problem.py                # NEW  assert_problem
├── unit/test_error_codes.py  # NEW  enum shape, HTTPException guard, every code tested, table registry
├── unit/test_errors.py       # NEW  normalization, handler bodies, framework codes, IntegrityError, 500
├── unit/test_contract_docs.py   # + error-codes.md ⇄ ErrorCode
└── api/, unit/, integration/    # detail assertions kept, then converted per area
```

**Structure Decision**: Single web-service project. Error infrastructure goes in `app/core`, next to `deps.py` and `password_policy.py`. The OpenAPI model goes in `app/schemas`. The handlers are registered in `app/main.py`, where the `IntegrityError` handler already lives.

## Delivery

One branch and one PR, with commits in the design's phase order, each green:

1. **Phase 0, infrastructure:**
   - Pieces: enum (all 241 members, from `contracts/error-codes.md`), `DomainError`, `not_found`, registry, `problem_body`, handler, `Problem`, the router default, `assert_problem`.
   - Tests: `test_errors.py`, the guard tests as strict xfail, and the contract-docs check.
2. **Phase 1, generic codes:** `references`, `documents`, `delivery_events`, `deps`/auth, the `IntegrityError` and 500 bodies, framework codes, and every 404 through `not_found`.
3. **Phase 2, sales and cash:**
   - the first wave;
   - `payment.*`, `refund.*` and `cash_session.*`;
   - the facility rule's 403s;
   - the four dict details, which gain only a code.
4. **Phase 4, areas in turn:**
   - deliveries and itineraries; inventory (plus the batch); purchasing and expenses (plus bulk approve); fiscal and CSD; pricing and products; commissions; AR/AP and email; images; users;
   - then the 21 validators.
5. **Phase 5, lock-in:**
   - the guards drop xfail;
   - the CHANGELOG is written;
   - `docs/` notes the error contract where the error format is already described.
6. **Dev:** deploy the branch and run quickstart §3. The mbe_dev writes need the user's go-ahead.
7. **After merge:**
   - open the mbe-ui issue (FR-019);
   - delete the branch, local and remote;
   - remove the worktree;
   - mark the spec complete and commit its `.spec-context.json` to main.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `app/core/error_codes.py` | The catalog must be one published enum (FR-002) whose members carry their status, so no raise pairs a code with the wrong status | A separate `dict[code, status]` drifts, and needs a test to keep the two in step |
| `app/core/errors.py` | The only module allowed to construct `HTTPException` (FR-016). Its body builder is shared by three producers: the handler, `IntegrityError` and the 500 | Three inline bodies drift; per-area exception classes add types without meaning |
| `RESOURCE_NAMES` (101) + `EXTRA_RESOURCES` (4) | Public names must be deliberate (spec Clarifications); 93 of 94 class names equal their table | Identity by default leaks a new table's name silently |
| `app/schemas/problem.py` | OpenAPI needs a model for the default response (FR-004) | Hand-written OpenAPI dicts bypass Pydantic and the contract-docs test |
| `tests/problem.py` | `assert_problem` is required by the input and used by every converted assertion | — |
