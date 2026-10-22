# Tasks: Master Data Access Privileges

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/route-privileges.md](./contracts/route-privileges.md), [quickstart.md](./quickstart.md)

> **Ship US1, US2 and US3 together.** US1 on its own denies the lookups the pilot uses: the salesperson picker, checkout and the product form would break for 11 to 23 users each (spec "Measured in mbe_dev"). The stories are separate so that each one can be tested on its own, not so that they can be deployed one at a time.

**Tests**: required by the constitution, and the template's "tests are optional" line does not apply. In every phase the test tasks come first and must fail before the implementation. Each test task is marked **(commit with T0xx)**: write it, watch it fail, implement T0xx, then commit both together.

**Router edits**: each router is edited by at most one task per story, so the tasks within a phase that are marked `[P]` touch different files. Every router task:
- declares its guards as module constants after `router = APIRouter()`, in the style of `sales_orders.py:23-27` (`_READ = require_privilege(SystemObject.X, AccessRight.READ)`, and so on);
- replaces `Depends(get_current_user)` with `Depends(_READ)` and the like on each route it owns;
- drops `get_current_user` from the import once nothing in the file uses it (constitution III). Keep `CurrentUser`.

## Format: `- [ ] T### [P?] [US#] Description · path`

---

## Phase 1: Setup

- [X] T001 Record the baseline before any change: run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. Write the pass/fail counts under this task. Any failure that already exists is listed here so it isn't attributed to this work · `specs/022-master-data-privileges/tasks.md`
  - **Baseline (2026-10-22)**: ruff clean; pytest 3048 passed, 0 failed.

---

## Phase 2: Foundational (blocks all stories)

- [X] T002 [P] Write unit tests for the new dependency in `tests/unit/test_deps_privileges.py` (commit with T003). Use a fake `AsyncSession` whose `execute` returns a result with `.all()` → a list of `(system_object, privileges)` rows. Cases:
  - **`has_any_privilege`**:
    - no rows → False;
    - a row with mask 0 → False;
    - a row with the wrong bit (e.g. Create only when Read is asked) → False;
    - the right bit on the second grant only → True;
    - grants with different rights per object (CUSTOMERS Update, SALES_ORDERS Update): the right is checked per object, so SALES_ORDERS Read alone → False.
  - **`require_any_privilege(...)`**:
    - the returned `_check(current_user=..., db=...)` returns the user when allowed;
    - when refused, it raises `HTTPException` 403 with detail `Insufficient privileges`;
    - an administrator returns without calling `db.execute` (assert not awaited).
  - **`grants`:** `require_any_privilege(a, b).grants == (a, b)` and `require_privilege(X, R).grants == ((X, R),)`.
- [X] T003 Implement in `app/core/deps.py`, below `require_privilege` (research R1):
  - **`async def has_any_privilege(db, user_id: str, grants: Sequence[tuple[SystemObject, AccessRight]]) -> bool`**: one `select(AccessPrivilege.system_object, AccessPrivilege.privileges)` with `user_id ==` and `system_object.in_(...)`. Read `.all()`, and return True if any row's mask holds the right paired with its object.
  - **`def require_any_privilege(*grants)`**: a dependency factory with the same signature shape as `require_privilege`. It lets administrators through, calls `has_any_privilege` for everyone else, and raises the identical 403. Its docstring cites spec 022 FR-005.
  - **Grants attribute:** set `_check.grants = grants` in the new factory, and add `_check.grants = ((system_object, right),)` to `require_privilege`. That one line is the only change to `require_privilege`.

  Run `uv run pytest tests/unit/test_deps_privileges.py` and get green.

**Checkpoint**: the shared dependency exists. The story phases can start.

---

## Phase 3: User Story 1 — Master data is closed to users without the privilege (P1)

**Goal**: Every non-lookup route on the 11 routers requires its module's privilege. The 4 modules without hosts are fully guarded.

**Independent test**: the route-table test passes for every row of the contract whose "Hosts" column is "—". A non-admin user with no rows gets 403 from `POST /labels` and `GET /vehicles`, and an administrator gets through.

- [X] T004 [US1] Write `tests/api/test_master_data_privileges.py` (commit with T005–T015):
  1. **Expected table.** Define `EXPECTED: dict[tuple[str, str], tuple[tuple[SystemObject, AccessRight], ...] | None]`, keyed by `(method, path)` with the full `/api/v1/...` path. It holds all 68 rows of `contracts/route-privileges.md`. The tuple lists the own grant first, then the hosts with `AccessRight.READ`. `PUT /api/v1/customers/{customer_id}` maps to `None`, meaning it is resolved in the handler and covered by US3.
  2. **Route-table test.** Collect every `APIRoute` in `app.routes` whose path starts with one of the 11 prefixes (`/api/v1/customers`, `/employees`, `/exchange-rates`, `/expenses`, `/labels`, `/payment-method-options`, `/price-lists`, `/suppliers`, `/taxpayer-recipients`, `/vehicle-operators`, `/vehicles`; match the prefix plus `/` or end of path, so `/vehicles` does not match `/vehicle-operators`). Assert that the set of `(method, path)` equals `EXPECTED.keys()`.
  3. **Per-route test.** Parametrize over `EXPECTED`. For each route, find the dependency in `route.dependant.dependencies` whose `call` has a `grants` attribute, and assert `call.grants == EXPECTED[key]`. For `None`, assert that no dependency has `grants`. This test fails for the 14 lookup rows until US2. That is expected, and the commit for T005–T015 records it. The PUT customers row passes throughout, because a route left on `get_current_user` carries no `grants` either. US3's behavior tests are what prove that route.
  4. **Behavior helpers and tests.**
     - **Auth:** `_auth(administrator: bool)` overrides `get_current_user`, as in `tests/api/test_print_endpoints.py:46-59`.
     - **Privileges:** `_privileges(**masks)` overrides `get_db` with a fake session. Its `execute(stmt)` reads the SystemObject values bound in the statement (`stmt.compile().params`) and answers only for those objects. An `IN (...)` comes back as one list-valued parameter: flatten list values, keep only the ints that are valid `SystemObject` values, and ignore the user-id string. The fake answers:
       - `.scalar_one_or_none()` → `SimpleNamespace(privileges=mask)`, or None (this path backs `require_privilege`);
       - `.all()` → `[(obj, mask), ...]` (this path backs `has_any_privilege`).
     - **Tests:**
       - a non-admin user with no rows → 403 on `POST /api/v1/labels` and `GET /api/v1/vehicles`;
       - LABELS=2 (Read only) → 403 on `DELETE /api/v1/labels/1`;
       - a mask of 0 → 403;
       - an administrator with no rows → not 403 on `GET /api/v1/vehicles` (patch `vehicle_service.list_vehicles` to return `([], 0)`);
       - no token → 401 (do not override `get_current_user`).
  5. **No-privilege sweep.** Parametrize over `EXPECTED`. As a non-admin user with no privilege rows, call each route and assert 403:
     - path parameters are `1` (or `'XAXX010101000'` for `{rfc}`);
     - send `json={}` for POST and PUT;
     - send no file for the license-photo POST.

     Guards run before the body is validated, so no service patching is needed. `PUT /customers/{customer_id}` is checked in the handler after the body is parsed; `{}` is a valid `CustomerUpdate`, so it also returns 403 there once US3 is done. Until then the sweep fails for PUT customers and, until US2, for the 14 lookup routes.
- [X] T005 [P] [US1] Guard every route in `app/api/v1/endpoints/exchange_rates.py` with EXCHANGE_RATES Read/Create/Update/Delete by method.
- [X] T006 [P] [US1] Guard every route in `app/api/v1/endpoints/expenses.py` with EXPENSES by method.
- [X] T007 [P] [US1] Guard every route in `app/api/v1/endpoints/vehicles.py` with VEHICLE by method.
- [X] T008 [P] [US1] Guard every route in `app/api/v1/endpoints/vehicle_operators.py` with VEHICLE_OPERATORS by method. The license photo is the exception: `POST` and `DELETE /{id}/license-photo` take Update, and `GET` takes Read (research R3).
- [X] T009 [P] [US1] Guard the write routes in `app/api/v1/endpoints/customers.py`:
  - `POST /` → CUSTOMERS Create;
  - `DELETE /{id}` → CUSTOMERS Delete;
  - `GET /{id}/discounts` → CUSTOMERS Read, own only;
  - `POST`, `PUT` and `DELETE` on discounts → CUSTOMERS Update.

  Leave `GET /`, `GET /{id}` (US2) and `PUT /{id}` (US3) on `get_current_user` for now. Name the own-only read `_READ` here; US2 adds `_LOOKUP`.
- [X] T010 [P] [US1] Guard the write routes in `app/api/v1/endpoints/suppliers.py`:
  - `POST` / `PUT` / `DELETE` → SUPPLIERS by method;
  - `GET /{id}/agreements` → SUPPLIERS Read, own only;
  - `POST`, `PUT` and `DELETE` on agreements → SUPPLIERS Update.

  Leave `GET /` and `GET /{id}` for US2.
- [X] T011 [P] [US1] Guard `POST`, `PUT` and `DELETE` in `app/api/v1/endpoints/employees.py` with EMPLOYEES by method. Leave both GETs for US2.
- [X] T012 [P] [US1] Guard `POST`, `PUT` and `DELETE` in `app/api/v1/endpoints/labels.py` with LABELS by method. Leave both GETs for US2.
- [X] T013 [P] [US1] Guard `POST`, `PUT` and `DELETE` in `app/api/v1/endpoints/payment_method_options.py` with PAYMENT_METHOD_OPTIONS by method. Leave both GETs for US2.
- [X] T014 [P] [US1] Guard the routes in `app/api/v1/endpoints/price_lists.py`:
  - `POST` / `PUT` / `DELETE` → PRICE_LISTS by method;
  - `GET /{id}/delete/preview` → PRICE_LISTS Read, own only (research R3).

  Leave `GET /` and `GET /{id}` for US2.
- [X] T015 [P] [US1] Guard `POST`, `PUT` and `DELETE` in `app/api/v1/endpoints/taxpayer_recipients.py` with TAXPAYER_RECIPIENTS by method. Leave both GETs for US2.
- [X] T016 [US1] Run `uv run pytest tests/api/test_master_data_privileges.py tests/api -q`. The only failures are the 14 lookup rows in the route table and the sweep, plus PUT customers in the sweep. The existing suites are green. Commit T004–T015.

**Checkpoint**: writes are closed. Do not deploy until US2 is done.

---

## Phase 4: User Story 2 — Sellers and back-office users keep every lookup they use today (P1)

**Goal**: The 14 lookup routes (list and get-by-id of 7 modules) accept their own Read or Read on the host modules (FR-005).

**Independent test**: as a non-admin user with SALES_ORDERS=7 and POS=2 only:
- `GET /customers`, `GET /employees`, `GET /taxpayer-recipients` and `GET /payment-method-options` are not refused;
- `DELETE /payment-method-options/1` is 403;
- `GET /suppliers` is 403, because POS and SalesOrders don't host suppliers.

- [X] T017 [US2] Extend `tests/api/test_master_data_privileges.py` with behavior tests (commit with T018–T024). Patch each service's list function to return `([], 0)`, and assert the status is 200:
  - SALES_ORDERS=2 only → `GET /customers`, `GET /employees`, `GET /taxpayer-recipients`, `GET /payment-method-options` all 200;
  - POS=2 only → `GET /customers`, `GET /employees`, `GET /payment-method-options` 200, `GET /taxpayer-recipients` 403;
  - CUSTOMER_PAYMENTS=2 only → `GET /payment-method-options` 200;
  - PRODUCTS=2 only → `GET /labels` and `GET /suppliers` 200, `GET /price-lists` 403;
  - PRICING=2 only → `GET /labels`, `GET /suppliers`, `GET /price-lists` 200;
  - CUSTOMERS=2 only → `GET /employees`, `GET /price-lists`, `GET /taxpayer-recipients` 200;
  - VEHICLE_OPERATORS=2 only → `GET /employees` 200, `GET /vehicles` 403;
  - a host never opens a write (FR-007): POS=15 → `POST /payment-method-options` 403; PRODUCTS=15 → `DELETE /labels/1` 403;
  - a host never opens a sub-resource: SALES_ORDERS=15 → `GET /customers/1/discounts` 403; PRICING=15 → `GET /price-lists/1/delete/preview` 403.
- [X] T018 [P] [US2] In `app/api/v1/endpoints/customers.py`, add `_LOOKUP = require_any_privilege((CUSTOMERS, READ), (SALES_ORDERS, READ), (SALES_QUOTES, READ), (POS, READ))` and use it on `GET /` and `GET /{customer_id}`.
- [X] T019 [P] [US2] In `app/api/v1/endpoints/employees.py`, use `_LOOKUP` on both GETs, with hosts SALES_ORDERS, SALES_QUOTES, POS, CUSTOMERS and VEHICLE_OPERATORS.
- [X] T020 [P] [US2] In `app/api/v1/endpoints/taxpayer_recipients.py`, use `_LOOKUP` on `GET /` and `GET /{rfc}`, with hosts SALES_ORDERS and CUSTOMERS.
- [X] T021 [P] [US2] In `app/api/v1/endpoints/price_lists.py`, use `_LOOKUP` on `GET /` and `GET /{price_list_id}`, with hosts CUSTOMERS and PRICING. The delete preview keeps the own-only `_READ`.
- [X] T022 [P] [US2] In `app/api/v1/endpoints/payment_method_options.py`, use `_LOOKUP` on both GETs, with hosts SALES_ORDERS, POS and CUSTOMER_PAYMENTS.
- [X] T023 [P] [US2] In `app/api/v1/endpoints/labels.py`, use `_LOOKUP` on both GETs, with hosts PRODUCTS and PRICING.
- [X] T024 [P] [US2] In `app/api/v1/endpoints/suppliers.py`, use `_LOOKUP` on `GET /` and `GET /{supplier_id}`, with hosts PRODUCTS and PRICING. Agreements keep the own-only `_READ`.
- [X] T025 [US2] Run `uv run pytest tests/api -q`. The route table is fully green; the sweep fails only for PUT customers. Run `uv run ruff check app/ tests/`. Commit T017–T024.

**Checkpoint**: the pilot's lookups are covered.

---

## Phase 5: User Story 3 — Sellers can still attach a new ship-to address or contact (P2)

**Goal**: `PUT /customers/{id}` requires Customers Update. A link-only body also accepts SalesOrders or SalesQuotes Update (FR-006, research R2).

**Independent test**: SALES_ORDERS=7 with no CUSTOMERS row. `PUT /customers/1` with `{"addresses": [3]}` is applied, and `{"addresses": [3], "name": "x"}` is 403.

- [X] T026 [US3] Extend `tests/api/test_master_data_privileges.py` (commit with T027). Patch `customer_service.get_customer` to return a customer stub and `customer_service.update_customer` to return it. Cases:
  - SALES_ORDERS=7, body `{"addresses": [3]}` → 200, and `update_customer` was awaited;
  - SALES_QUOTES=4, body `{"contacts": [5]}` → 200;
  - SALES_ORDERS=7, body `{"addresses": [3], "name": "x"}` → 403, and `update_customer` was not awaited;
  - SALES_ORDERS=7, body `{"addresses": [3], "name": null}` → 200, because `null` is not a change;
  - SALES_ORDERS=2 (Read only), body `{"addresses": [3]}` → 403;
  - CUSTOMERS=4, body `{"name": "x", "price_list": 1}` → 200;
  - no rows → 403;
  - SALES_ORDERS=7, body `{"addresses": []}` → 200, because an empty list counts as link-only;
  - SALES_ORDERS=7, body `{}` → 200. This is a no-op and counts as link-only.
  - administrator → 200;

  Mirror the response stub on the existing `tests/api/test_customers.py` update test so the response model validates.
- [X] T027 [US3] In `app/api/v1/endpoints/customers.py` `update_customer`:
  - **Signature:** take `current: CurrentUser = Depends(get_current_user)` and `db`.
  - **Rule:** before the existing lookup and update, compute `link_only = data.model_dump(exclude_none=True).keys() <= {'addresses', 'contacts'}`. Then grants are `((CUSTOMERS, UPDATE), (SALES_ORDERS, UPDATE), (SALES_QUOTES, UPDATE))` if `link_only`, else `((CUSTOMERS, UPDATE),)`.
  - **Check:** if the user is not an administrator and `not await has_any_privilege(db, current.user_id, grants)`, raise `HTTPException(403, 'Insufficient privileges')`. The check comes before the 404 lookup, the same order the guarded routes use.
  - **Comment:** one line citing spec 022 FR-006 and why: mbe-ui links a new address or contact through this route.

  Run `uv run pytest tests/api -q`: all green. Commit T026–T027.

**Checkpoint**: all 68 routes match the contract.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T028 [P] Update `docs/specs/12-users.md` §5 "Authorization". Add that a lookup route also accepts Read on the modules whose screens use it, link to `specs/022-master-data-privileges/contracts/route-privileges.md`, and state the customer link exception. Keep the existing four bullets · `docs/specs/12-users.md`
- [ ] T028a [P] In `specs/002-master-data-endpoints/contracts/api.md`, add a `**Privileges**:` line under each of the 11 module headings (§2 Price Lists, §3 Customers, §4 Labels, §5 Taxpayer Recipients, §6 Suppliers, §7 Employees, §12 Exchange Rates, §13 Expenses, §14 Payment Method Options, §15 Vehicles, §16 Vehicle Operators). Match the wording at :484: "`CUSTOMERS (2)`, access right matching the operation; `403` without it." Where it applies, add:
  - the alternative read privileges for the list and get-by-id routes;
  - the parent object for nested routes;
  - Update for the license photo;
  - the customer link exception.

  Each line links to `specs/022-master-data-privileges/contracts/route-privileges.md` rather than repeating the table · `specs/002-master-data-endpoints/contracts/api.md`
- [ ] T029 [P] Add a `### Changed` entry to `CHANGELOG.md` `[Unreleased]`, in the existing entry style: **The 11 master data routers enforce access privileges** (mictlanix/mbe-roadmap#22, spec 022). Cover:
  - which routers, and the rights by method;
  - nested routes take the parent's object;
  - the lookup hosts, with one sentence of why (the pilot's POS, checkout, product form and pricing grid) and a link to the contract;
  - the customer link exception;
  - an **upgrade note**: non-admin users without a matching row now get 403 where they used to get data. Counts are in the spec.

  File: `CHANGELOG.md`
- [ ] T030 Run the full gate: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. Compare against the T001 baseline: no new failures.
- [ ] T031 Re-run the SC-002 measurement on mbe_dev, read only, per `quickstart.md`. Run one query per row of the spec's "Users who would lose a lookup" table, using the hosts from the contract. Expected: 0 in every row. Record the results under this task · `specs/022-master-data-privileges/tasks.md`
- [ ] T032 **Confirm with the user before deploying.** Then deploy the branch to the test instance (`/var/www/mbe-api-test`, systemd `mbe-api-test`, as `mictlanix@xolotl`) and run the manual checks 1–4 in `quickstart.md`. Remove any address created in check 2. Record the outcome in `specs/022-master-data-privileges/quickstart.md` under a "Checks on the test deployment" heading. Next to the results, record the mbe-ui commit that is deployed at https://test.mbe.mictlanix.com (`git -C /var/www/mbe-ui-test …` or the build's version file).

---

## Dependencies

- T001 → T002/T003 → every story.
- **US1** (T004–T016) and **US2** (T017–T025) edit the same router files (customers, suppliers, employees, labels, payment_method_options, price_lists, taxpayer_recipients). Run US2 after US1.
- **US3** (T026–T027) edits `customers.py` and the test file. Run it after US2's T018.
- Polish runs after all three stories. T032 comes last and needs the user's go-ahead.

## Parallel opportunities

- **Phase 2:** T002 can be written while T003 is designed, but they commit together.
- **US1:** T005–T015 are 11 different router files, all `[P]`. Write T004 first.
- **US2:** T018–T024 are 7 different router files, all `[P]`. Write T017 first.
- **Polish:** T028, T028a and T029 are `[P]`.

```text
# US1, after T004 is written and failing:
T005 exchange_rates.py | T006 expenses.py | T007 vehicles.py | T008 vehicle_operators.py
T009 customers.py | T010 suppliers.py | T011 employees.py | T012 labels.py
T013 payment_method_options.py | T014 price_lists.py | T015 taxpayer_recipients.py
```

## Implementation strategy

1. **Foundation:** the dependency and its unit tests (Phase 2).
2. **US1 then US2, back to back:** writes closed, then lookups reopened through hosts. Together they are the MVP. US1 alone must not be deployed.
3. **US3:** the link exception, which completes the route table.
4. **Polish:** docs, changelog, the measurement re-run, then the test deployment once confirmed.
5. **PR:** under "Roadmap", write `Closes mictlanix/mbe-roadmap#22`.
