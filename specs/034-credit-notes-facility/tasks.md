---

description: "Task list for credit note balances and the facility rule (spec 034)"
---

# Tasks: Credit Note Balances and the Facility Rule

**Input**: Design documents from `specs/034-credit-notes-facility/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R10), data-model.md, contracts/api.md, quickstart.md

**Tests**: requested. The constitution requires tests first on every changed service branch and route.

**Where tests go:**
- `tests/unit/`: pure rules, on plain objects.
- `tests/api/`: the route contract, with the service patched, as each existing file there does.
- `tests/integration/`: real rows on SQLite, through the `db` and `client` fixtures.
  - A scoped caller overrides `current_user` with `administrator=False` and seeds its `access_privilege` rows, as `tests/integration/test_accounts_receivable_flow.py` does.
  - A second facility comes from `seed_facility` in `tests/integration/seed.py`.

**Commits:** a test task is committed with the implementation task that makes it pass, never alone. `uv run ruff check app/ migrations/ tests/` passes before every commit, with no `Co-Authored-By` line.

**Organization**: by user story; US1–US3 are P1, US4 is P2.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task).
- **[Story]**: US1–US4 from spec.md.
- Paths are relative to the repository root. Work in the worktree `mbe-api-034`.

---

## Phase 1: Setup

No setup: no dependency, setting, enum or migration is added (research R9).

---

## Phase 2: Foundational (blocks US3 and US4)

**Purpose**: one helper for "this caller sees every facility" (research R4).

- [ ] T001 Write the tests first, in `tests/unit/test_deps_privileges.py`, for `can_see_every_facility(db, current, system_object) -> bool`, using that file's fake `_db(...)` rows:
  - an administrator → True without querying;
  - a holder of read on the object → True;
  - a row with another bit, or no row → False;
  - the object passed is the one queried (100 and 101 cases).
- [ ] T002 Add `can_see_every_facility` to `app/core/deps.py`, beside `has_any_privilege`: `current.administrator or await has_any_privilege(db, current.user_id, [(system_object, AccessRight.READ)])`, with a docstring naming the rule (spec 034 FR-009, FR-014). Then make the two existing private copies call it:
  - `_sees_every_facility` in `app/api/v1/endpoints/customer_payments.py`, with PAYMENTS_EDITOR;
  - `_sees_every_facility` in `app/api/v1/endpoints/accounts_receivable.py`, with SEARCH_CREDITS_FROM_ALL_STORES.

  Keep each one's docstring. T001 and the existing customer-payment and AR tests must pass.

**Checkpoint**: `uv run pytest -q` is green.

---

## Phase 3: User Story 1 - A credit note shows what it can still pay (P1) 🎯 MVP

**Goal**: amount, remaining, refunded and facility read from the backing payment. Notes paid back in cash read 0, and the API's refund writes notes the current system's way.

**Independent Test**: a legacy-shaped note (refunded 0) with applications and change reads its payment's unapplied amount; a note paid back in cash reads 0; a refund through this API writes `refunded = 0` and `cash_session` NULL, and reads its full amount.

### Tests

- [ ] T003 [P] [US1] Rewrite `tests/unit/test_credit_note_service.py` for the new pure rule `remaining_balance(unapplied: Decimal, *, refunded_to_customer: bool) -> Decimal`:
  - unapplied passes through;
  - negative floors at 0;
  - paid back in cash → 0, whatever the payment holds.

  Keep the existing cases that still describe the rule (reversal restores, a zero note has nothing to spend); drop those about `refunded` being the issued amount.
- [ ] T004 [P] [US1] Write `tests/integration/test_credit_notes_flow.py`, through the `client` fixture as an administrator, on notes seeded the current system's way: a type-4 payment with method 0 and a cash session (any id, or NULL in SQLite), and a `credit_note` with `refunded = 0` and `cash_session` NULL.
  - Note 990's shape: payment 27.00 with 24.00 applied reads `amount` 27.00 and `remaining` 3.00 (US1 scenario 1).
  - An application with change: change counts as used (scenario 2).
  - A note paid back in cash (`cash_session` set, `refunded` = what was left): `remaining` 0, `refunded_to_customer` true, `refunded` the amount handed back (scenario 3).
  - A reversed application restores the remaining.
  - Each note reports `facility` = its payment's facility (FR-006).
  - Detail and list agree.
- [ ] T005 [P] [US1] Extend `tests/integration/test_payment_flow.py`, beside `test_a_paid_sale_is_refunded_as_a_credit_note`: confirming a refund with `payout: credit_note` writes the note with `refunded == 0` and `cash_session is None`, the backing payment keeps the session, and `GET /credit-notes/{id}` reads `amount` = `remaining` = the refund total (US1 scenario 5, SC-003).
- [ ] T006 [P] [US1] Update `tests/api/test_credit_notes.py` for the new fields: `_note()` gains `amount`, `facility` and `refunded_to_customer`; `refunded` is the cash handed back. The list and detail routes return them.

### Implementation

- [ ] T007 [US1] Rewrite `app/services/credit_note_service.py` (research R1, R3):
  - `remaining_balance(unapplied, *, refunded_to_customer)` per T003;
  - a private `_notes_query()` returning a select of `CreditNote`, its payment's `amount` and `facility`, and the remaining expression. The used amount is an outer-joined aggregate subquery: `Σ(SalesOrderPayment.amount + amount_change)` per `customer_payment`, non-cancelled only. The expression is a SQLAlchemy `case`: 0 when `cash_session` is not NULL, 0 when `payment.amount − COALESCE(used, 0)` is below 0, otherwise that difference. Use `case` for the floor, not `GREATEST`, which SQLite lacks;
  - `_attach(note, amount, facility, remaining)` setting `__dict__` keys `amount`, `facility`, `remaining`, `refunded_to_customer`;
  - `get_credit_note(db, credit_note_id)` runs `_notes_query()` for one id and attaches;
  - `list_credit_notes` runs it with the filters, unchanged for now apart from using the query (paging is US2);
  - drop `attach_remaining`, `attach_summary_remaining` and `_applied_against`, and update the module docstring: the note is a view over its payment, `refunded` is cash handed back.

  T003 and T004 must pass.
- [ ] T008 [US1] Update `app/schemas/credit_note.py`, `CreditNoteResponse`: add `amount: Decimal`, `facility: int`, `refunded_to_customer: bool`; rewrite the docstring (spec 034 FR-001 to FR-006, `refunded` = cash handed back). In `app/api/v1/endpoints/credit_notes.py`, the detail route stops calling `attach_remaining`. T006 must pass.
- [ ] T009 [US1] In `app/services/customer_refund_service.py`, `_pay_customer_back`, write the note with `refunded=Decimal(0)` and `cash_session=None` (explicitly: the column defaults to 0), with a comment citing the current system's `CustomerRefundsController.cs:353-378` and FR-005. T005 must pass, and so must the existing refund tests; update any that assert `refunded == total`.

**Checkpoint**: US1 works alone. `uv run pytest -q` is green.

---

## Phase 4: User Story 2 - Open notes page correctly (P1)

**Goal**: `open_only` filters in SQL, before counting and paging.

**Independent Test**: with more open notes than one page, `limit` returns full pages, `total` counts only open notes, and no note repeats or goes missing.

### Tests

- [ ] T010 [US2] Add to `tests/integration/test_credit_notes_flow.py`:
  - 5 open notes, 3 spent, 1 paid back in cash and 2 of the walk-in customer, interleaved by id;
  - `open_only=true&limit=2` pages 2, 2, 1 with `total` 5, every open note once and newest first;
  - without `open_only`, `total` counts all 11;
  - a `customer` filter counts only that customer's open notes (US2 scenarios 1 and 2);
  - the same rule called directly, `credit_note_service.list_credit_notes(db, current=…, open_only=True)`, one case per branch of the open rule: remaining 0.01 (not open) and 0.02 (open), paid back in cash, the walk-in customer. The constitution requires branching logic to be tested directly, not only through an endpoint; the rule is SQL, so it is tested against SQLite.

### Implementation

- [ ] T011 [US2] In `app/services/credit_note_service.py`, `list_credit_notes`:
  - when `open_only`, add the open condition (FR-004) to the query: the remaining expression `> totals.SETTLED`, `CreditNote.cash_session.is_(None)`, and `CreditNote.customer != settings.default_customer_id`;
  - count with `select(func.count()).select_from(query.subquery())`, so the count and the page share every clause;
  - order by `credit_note_id` descending, then offset and limit;
  - remove the Python filter.

  T010 must pass.
- [ ] T012 [US2] Record the FR-008 check in the docstring of `list_credit_notes`: it was the only list that filtered after paging (research R3). No other list changes.

**Checkpoint**: US1 and US2 make the MVP that unblocks roadmap#28.

---

## Phase 5: User Story 3 - Payment and order writes respect the caller's facility (P1)

**Goal**: apply and reverse answer 404 for another facility's payment without PaymentsEditor; sales-order writes answer 404 for another facility's order without SearchCreditsFromAllStores; verify, reject and order reads stay open.

**Independent Test**: as a clerk of facility 1 without 100 or 101, each write on a facility-2 payment or order answers 404 and changes nothing; as an administrator, each proceeds; verify works on any facility.

### Tests

- [ ] T013 [P] [US3] Write `tests/integration/test_facility_rule.py`, overriding `current_user` with a non-administrator of facility 1 who holds `access_privilege` rows for CustomerPayments (8), PaymentsVerification (108) and SalesOrders (7) with full rights, and neither 100 nor 101. Use `seed_facility` for facility 2.
  - **Payments:**
    - `POST /customer-payments/{facility-2 payment}/applications` → 404, no application written;
    - `…/reverse` on a facility-2 payment's application → 404, still live;
    - a facility-1 payment applied to a facility-2 order → 201 (US3 scenario 3);
    - `verify` and `reject` on a facility-2 payment → 200 (scenario 4).
  - **Sales orders:** for a facility-2 draft order:
    - `PUT`, `POST …/lines`, `PUT …/lines/{id}`, `DELETE …/lines/{id}`, `confirm` and `cancel` each → 404, unchanged;
    - `GET /{id}` and `GET /{id}/payments` → 200 (scenario 5).
  - **The same caller with 100 and 101 rows added:** the apply and the `PUT` proceed.
  - **An administrator** (the fixture's default): every call proceeds.
- [ ] T014 [P] [US3] Extend `tests/api/test_customer_payments.py`: apply and reverse on a payment whose `facility` differs from the caller's answer 404 when `has_any_privilege` is patched to False, and reach the service when it's True. Verify and reject reach the service for any facility.
- [ ] T015 [P] [US3] Extend `tests/api/test_sales_orders.py`: each of the six write routes answers 404 for an order of another facility without 101, and reaches the service with it or as an administrator; `GET /{id}` reaches the service for any facility.

### Implementation

- [ ] T016 [US3] In `app/api/v1/endpoints/customer_payments.py`, `apply_customer_payment` and `reverse_customer_payment_application` fetch the payment with `_visible_payment_or_404(db, current, customer_payment_id)` instead of `_payment_or_404`. Leave verify and reject on `_payment_or_404`, with a one-line comment that verification is company-wide (spec 034 FR-010, clarified).
- [ ] T017 [US3] In `app/api/v1/endpoints/sales_orders.py`:
  - add `_writable_order_or_404(db, current, sales_order_id)`: fetch through `_order_or_404`, then answer 404 "Sales order not found" when `order.facility != current.facility_id` and `not await can_see_every_facility(db, current, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)`;
  - use it in `update_sales_order`, `confirm_sales_order`, `cancel_sales_order`, `add_sales_order_line`, `update_sales_order_line` and `remove_sales_order_line`;
  - the read routes keep `_order_or_404`.

  T013–T015 must pass.

**Checkpoint**: run the full suite; existing sales-order and payment tests that act as administrators stay green.

---

## Phase 6: User Story 4 - Lists honour the caller's facility (P2)

**Goal**: the sales-order list's `facility` answers 403 without 101; `/unverified` covers every facility; credit notes are scoped by facility, with 403 for a named other facility and 404 for another facility's note, without 100.

**Independent Test**: as the scoped clerk, `GET /sales-orders?facility=2` → 403; `GET /credit-notes?facility=2` → 403; `GET /credit-notes/{facility-2 note}` → 404; the credit-note list shows only facility 1; `/unverified` lists both facilities and narrows with `facility=2`.

### Tests

- [ ] T018 [P] [US4] Add to `tests/integration/test_facility_rule.py`, as the scoped clerk (also holding CreditPayments 83 read):
  - `GET /sales-orders?facility=2` → 403; `GET /sales-orders` → facility 1 only (US4 scenario 1);
  - `GET /credit-notes` → only notes whose payment is of facility 1; `?facility=2` → 403; `GET /credit-notes/{facility-2 note}` → 404 (scenario 2);
  - `GET /customer-payments/unverified` → payments of both facilities; `?facility=2` → only facility 2 (scenario 3, SC-005);
  - with 100 and 101 added, `?facility=2` works on sales orders and credit notes, and the facility-2 note reads.
- [ ] T019 [P] [US4] Extend `tests/api/test_credit_notes.py`: the list passes `facility` (default the caller's) to the service, and answers 403 for another facility without PaymentsEditor (`has_any_privilege` patched); the detail answers 404 for a note of another facility without it.
- [ ] T020 [P] [US4] Extend `tests/api/test_sales_orders.py`: `GET /sales-orders?facility=<other>` → 403 without 101, and reaches the service with it.
- [ ] T021 [P] [US4] Extend `tests/api/test_customer_payments.py`: `/unverified` calls `list_payments` with `cross_facility=True`, passing a named `facility` through.

### Implementation

- [ ] T022 [US4] In `app/api/v1/endpoints/sales_orders.py`, `list_sales_orders`: when `facility` is given, differs from `current.facility_id`, and `not await can_see_every_facility(db, current, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)`, answer 403 "Another facility's sales orders require SearchCreditsFromAllStores read", as `list_customer_payments` does.
- [ ] T023 [US4] In `app/api/v1/endpoints/customer_payments.py`, `list_unverified_payments` passes `cross_facility=True` to `list_payments`, with a comment that the current system's verification screen covers every store (FR-012).
- [ ] T024 [US4] Credit notes and facility:
  - `app/services/credit_note_service.py`, `list_credit_notes` gains `facility: int | None` and filters `CustomerPayment.facility == (facility or current.facility_id)` in the shared query, before counting;
  - `app/api/v1/endpoints/credit_notes.py`:
    - the list takes `facility: int | None = Query(None)` and answers 403 "Another facility's credit notes require PaymentsEditor read" as T022 does, with PAYMENTS_EDITOR;
    - the detail takes `current`, and answers 404 "Credit note not found" when the note's `facility` differs and `can_see_every_facility(..., PAYMENTS_EDITOR)` is false.

  T018–T021 must pass.

**Checkpoint**: full suite green.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T025 [P] Update `docs/specs/02-sales.md`:
  - **Customer Payments:** add an "API (spec 034)" note: apply and reverse follow the payment's facility; verification is company-wide; `/unverified` covers every facility;
  - **Credit notes:** amount and remaining come from the backing payment, paid back in cash reads 0, the open pool, the facility rule, and the refund writer's shape;
  - **Sales Orders:** the facility rule on the list and writes, with 101;
  - **Credit notes, spec 011's FR-070:** a note's remaining is now its backing payment's unapplied amount, not "the amount issued less applications". Spec 011 stays as the historical record.
- [ ] T026 [P] Add to `CHANGELOG.md` under `[Unreleased]`:
  - **Fixed:**
    - credit note balances on legacy data (#277);
    - open-only paging (#278);
    - the API refund's credit note now reads like the current system's, and both systems can spend it;
  - **Changed:**
    - the facility rule on payment apply and reverse, sales-order list and writes, and credit notes (roadmap#156);
    - `/unverified` covers every facility;
    - `CreditNoteResponse` fields: `amount`, `facility`, `refunded_to_customer`; `refunded` now means cash handed back.
- [ ] T027 Run `uv run ruff check app/ migrations/ tests/`, `uv run ruff format --check` on the touched files, and `uv run pytest -q`; all green.
- [ ] T028 Run the quickstart. Steps 1–5 are read-only after deploying to dev, which needs no confirmation. Step 6 writes to mbe_dev and needs confirmation. Record the figures in `.spec-context.json` verifications.

---

## Dependencies & Execution Order

- **Phase 2** (T001–T002) blocks US3 and US4. US1 and US2 don't need it.
- **US1** (T003–T009) first: the MVP.
- **US2** (T010–T012) needs US1's query (T007).
- **US3** (T013–T017) needs Phase 2 only.
- **US4** (T018–T024) needs Phase 2, and US1's query for the credit-note part (T024).
- **Polish** last.

### Same-file chains (not parallel)

- `app/services/credit_note_service.py`: T007 → T011 → T012 → T024.
- `app/api/v1/endpoints/credit_notes.py`: T008 → T024.
- `app/api/v1/endpoints/customer_payments.py`: T002 → T016 → T023.
- `app/api/v1/endpoints/sales_orders.py`: T017 → T022.
- `tests/integration/test_credit_notes_flow.py`: T004 → T010.
- `tests/integration/test_facility_rule.py`: T013 → T018.
- `tests/api/test_credit_notes.py`: T006 → T019.
- `tests/api/test_customer_payments.py`: T014 → T021.
- `tests/api/test_sales_orders.py`: T015 → T020.

## Parallel Examples

- **US1 tests:** T003, T004, T005 and T006 together, in four files.
- **US3 tests:** T013, T014 and T015 together.
- **US4 tests:** T019, T020 and T021 together; T018 follows T013 in its file.
- **Stories:** US3 can run beside US1/US2 once Phase 2 is done.

## Implementation Strategy

1. **MVP:** US1 and US2. Credit notes read right and page right, which unblocks mbe-ui (roadmap#28).
2. **Then US3:** closes the write gaps #271 left.
3. **Then US4:** the lists.
4. **Then Polish:** docs, CHANGELOG, gates, and the quickstart on dev.
