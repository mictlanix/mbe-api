---

description: "Task list for expense vouchers and supplier payments (spec 032)"
---

# Tasks: Expense Vouchers and Supplier Payments

**Input**: Design documents from `specs/032-expense-supplier-payments/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R13), data-model.md, contracts/payments.md, quickstart.md

**Tests**: requested. The constitution and plan call for tests first on every service branch and route. Within each story, write the tests, see them fail, then implement.

**Organization**: by user story. P1 stories come first (US1, US2, US4), then P2 (US3, US5, US6).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task).
- **[Story]**: US1–US6 from spec.md.
- Paths are relative to the repository root.

**Correction to plan.md**: `SupplierPayment` already exists in `app/models/supplier.py`. Its new columns go there. The two new models go in `app/models/purchases.py`.

---

## Phase 1: Setup

**Purpose**: the schema and the enum values every story uses.

- [ ] T001 Write `migrations/029_supplier_payments.sql`, idempotent, in the style of `migrations/028_purchasing.sql` (research R11):
  - `supplier_payment` gains, guarded by information_schema checks:
    - `facility` INT NULL with an FK to `facility`, and `serial` INT NULL;
    - `UNIQUE (facility, serial)`;
    - `cash_session` INT NULL, with an FK and an index;
    - `cancelled TINYINT(1) NOT NULL DEFAULT 0`;
    - `creation_time` DATETIME NULL, `updater` INT NULL (FK employee) and `modification_time` DATETIME NULL.
  - Backfill the legacy rows: `creation_time = modification_time = date` and `updater = creator`, where `creation_time IS NULL`.
  - `CREATE TABLE IF NOT EXISTS purchase_order_payment`:
    - `purchase_order_payment_id` auto-increment primary key;
    - `supplier_payment` and `purchase_order`, with FKs and an index each;
    - `amount DECIMAL(18,2) NOT NULL`;
    - `applier` (FK employee) and `date DATETIME NOT NULL`;
    - `cancelled TINYINT(1) NOT NULL DEFAULT 0`.
  - `CREATE TABLE IF NOT EXISTS supplier_balance_cutoff (supplier_balance_cutoff_id INT PRIMARY KEY, cutoff DATETIME NULL)`, then `INSERT IGNORE` the row `(1, NULL)`.
- [ ] T002 [P] Write `migrations/029_supplier_payments_rollback.sql`: drop the two tables, the unique key, the FKs, the index and the added `supplier_payment` columns, each guarded so it can run twice.
- [ ] T003 [P] Add `EXPENSE_VOUCHER = 14` and `SUPPLIER_PAYMENT = 15` to `SourceType` in `app/enums.py`, after `FISCAL_DOCUMENT = 13`, commented like the 10–13 entries (research R9).

---

## Phase 2: Foundational (blocks every story)

**Purpose**: the models, and the session-row lock that vouchers and cash payments both rely on.

- [ ] T004 Add to `SupplierPayment` in `app/models/supplier.py`, matching T001:
  - `facility`, `serial` and `cash_session`, all nullable;
  - `cancelled: Mapped[bool]` with a `False` default;
  - `creation_time`, `updater` and `modification_time`, all nullable.
- [ ] T005 [P] Add to `app/models/purchases.py`, matching T001:
  - `PurchaseOrderPayment` (`__tablename__ = 'purchase_order_payment'`);
  - `SupplierBalanceCutoff` (`__tablename__ = 'supplier_balance_cutoff'`).
- [ ] T006 Write the test first, in `tests/unit/test_cash_session_service.py`: `close_session` re-reads its row under a lock and answers 409 "Session is already closed" when the re-read row has `end` set, even if the object passed in still shows it open. Simulate this by setting `end` through a separate statement before the call.
- [ ] T007 Change `close_session` in `app/services/cash_session_service.py` (research R2):
  - before checking `end`, re-select the session row with `.with_for_update()` and `execution_options(populate_existing=True)`;
  - check `end` on the re-read row;
  - change nothing else.
- [ ] T008 Add a shared helper `lock_open_session(db, cash_session_id) -> CashSession` to `app/services/cash_session_service.py`:
  - it selects the row with `FOR UPDATE` and `populate_existing`;
  - it raises 409 "The cash session is closed" when `end` is set, and 404 when the row is missing.

  Unit-test it in `tests/unit/test_cash_session_service.py`. Vouchers (US1, US2) and cash payments (US4, US5) use it.

**Checkpoint**: `uv run pytest -q` is green, and the SQLite test schema builds with the new models.

---

## Phase 3: User Story 1 — Record cash expenses during a session (P1) 🎯 MVP

**Goal**: a cashier creates a draft voucher in their open session, edits its lines, and confirms it. The cut subtracts it.

**Independent Test**: with an open session, create a voucher with two lines and confirm it. The session's cut shows the expense, and the expected cash drops by the voucher's total.

### Tests for User Story 1

- [ ] T009 [P] [US1] Unit tests in `tests/unit/test_expense_vouchers.py`:
  - **State** (research R1): (0,0) is draft, (1,0) completed, (any,1) cancelled, and NULL flags count as false. `voided` is true only for (1,1).
  - **Create:** without an open session the answer is 409 "An open cash session is required". The draft takes the session, the session's drawer's facility, `creator = updater =` the caller, and `date = creation_time = now`. A cashier with two open sessions gets the newest.
  - **Edits and lines:**
    - a non-creator gets 403 "Only the voucher's cashier can change it";
    - a voucher that isn't a draft answers 409;
    - an amount of 0 or less, with more than 2 decimals, or above 99,999,999.99 answers 422;
    - an unknown category answers 422;
    - a line's note defaults to the category's `comment`.
  - **Confirm:**
    - a total of 0 answers 409;
    - with no open session the answer is 409;
    - a non-creator gets 403;
    - on success the voucher moves to the caller's then-open session and that session's drawer's facility, `date` becomes now, `completed` is set, and `updater` and `modification_time` are written.
  - **Cancelling a draft:** the creator only; a voucher that isn't a draft answers 409; `cancelled` is set and `voided` is false.
- [ ] T010 [P] [US1] Integration test in `tests/integration/test_expense_voucher_flow.py`, on SQLite:
  - open a session, create a voucher, add 2 lines, confirm;
  - `cut_figures` `expenses` equals the total, and `expected_cash` drops by it;
  - a draft and a cancelled draft don't count;
  - a voucher created in session A and confirmed after A closes and B opens lands in B.
- [ ] T011 [P] [US1] API tests in `tests/api/test_expense_vouchers.py` for list, create, get, put, the 3 line routes, confirm and cancel:
  - 401 without a token, 403 without the right on 82 (Read, Create or Update as contract/payments.md says), 404 for an unknown voucher or line;
  - 409 and 422 as the contract says;
  - 403 for a non-administrator reading another facility's voucher (FR-008);
  - the list: a non-administrator's is scoped to their facility, a non-administrator naming another facility gets 403, and an administrator's is unscoped unless `facility` is given;
  - list filters: `status`, `facility`, `cash_session`, `creator`, `from`, `to`, and `skip`/`limit`, newest first.

### Implementation for User Story 1

- [ ] T012 [P] [US1] Write the schemas in `app/schemas/expense_vouchers.py`, following `app/schemas/` conventions (see contract/payments.md):
  - `ExpenseVoucherCreate` and `ExpenseVoucherUpdate`, each with `comment` up to 500 characters;
  - `ExpenseVoucherLineCreate` (`expense`, `amount`, `comment`) and `ExpenseVoucherLineUpdate` (each field optional). `amount` is above 0, has at most 2 decimals and is at most 99,999,999.99 (research R12);
  - `ExpenseVoucherLine`, `ExpenseVoucherSummary` (with `voided: bool`) and `ExpenseVoucherResponse`.
- [ ] T013 [US1] Write `app/services/expense_voucher_service.py`:
  - `voucher_state(voucher)` (research R1);
  - `create_voucher`, using `cash_session_service.open_session_for_cashier`; it takes no lock (research R3);
  - `update_voucher`, `add_line`, `update_line`, `remove_line`: each locks the voucher row `FOR UPDATE` and checks for a draft whose creator is the caller;
  - `confirm_voucher` (research R3):
    1. pre-read the caller's open session id, then commit;
    2. `lock_open_session`, and check that the session is the caller's;
    3. lock the voucher row and check for a draft created by the caller;
    4. read the lines and require a total above 0;
    5. set `cash_session`, `facility` (from the session's drawer), `date`, `completed`, `updater` and `modification_time`.
  - `cancel_draft`;
  - `get_voucher` with its lines, total and names (category, creator), and `list_vouchers` with the contract's filters;
  - the facility rule (contract/payments.md): `list_vouchers` scopes a non-administrator to their facility and answers 403 when they name another; an administrator is unscoped unless `facility` is given. Get, void and ticket answer 403 to a non-administrator for another facility's voucher. Follow `customer_payment_service.list_payments`, not `list_refunds`, which shows nothing to an administrator without a facility.

  Wrap the locking paths in `inventory_documents.busy_as_conflict`.
- [ ] T014 [US1] Write the endpoints in `app/api/v1/endpoints/expense_vouchers.py`: GET list, POST, GET `/{id}`, PUT `/{id}`, POST, PUT and DELETE `/{id}/lines[/{line_id}]`, POST `/{id}/confirm` and POST `/{id}/cancel`. Gate each on `require_privilege(SystemObject.EXPENSE_TICKET, ...)`, as the contract says.
- [ ] T015 [US1] Register the router in `app/api/v1/router.py` with `prefix='/expense-vouchers'` and `tags=['expense-vouchers']`.

**Checkpoint**: T009–T011 pass. The cut subtracts confirmed vouchers.

---

## Phase 4: User Story 2 — Void a confirmed voucher while the session is open (P1)

**Goal**: anyone with delete on 82 in the voucher's facility voids a confirmed voucher, with a reason, while its session is open. A void racing the close never changes a closed cut.

**Independent Test**: confirm a voucher, then void it with a reason. The cut no longer counts it, and the reason is recorded. After the session closes, another void is refused.

### Tests for User Story 2

- [ ] T016 [P] [US2] Unit tests in `tests/unit/test_expense_vouchers.py`:
  - voiding a draft or a cancelled voucher answers 409;
  - a caller with no open session of their own can void a voucher in another cashier's open session;
  - voiding when the voucher's session is closed answers 409 "The cash session is closed";
  - a voucher of another facility gets 403 for a non-administrator;
  - a missing reason, or one over 500 characters, answers 422;
  - on success `completed` stays 1, `cancelled` becomes 1, and one incidence is written with source 14, the voucher id, the reason, and "Expense voucher voided"; `voided` is true.
- [ ] T017 [P] [US2] Integration test in `tests/integration/test_expense_voucher_flow.py`:
  - void a confirmed voucher, and the cut's `expenses` drops by its total;
  - close the session, and a later void and a later confirm both answer 409;
  - the closed session's figures equal the figures at close.
- [ ] T018 [P] [US2] API tests in `tests/api/test_expense_vouchers.py` for `POST /{id}/void`: 401; 403 without 82 Delete; 403 for another facility; 404; 409; 422.

### Implementation for User Story 2

- [ ] T019 [US2] Add `VoidRequest` (`reason`, 1–500 characters) to `app/schemas/expense_vouchers.py`. Reuse an existing reason schema if one exists in `app/schemas/`.
- [ ] T020 [US2] Add `void_voucher` to `app/services/expense_voucher_service.py` (research R3):
  1. pre-read the voucher's `cash_session`, then commit;
  2. `lock_open_session` on that session;
  3. lock the voucher row, and re-check that it is still in that session, completed and not cancelled;
  4. set `cancelled`, `updater` and `modification_time`;
  5. write `incidences.record(... SourceType.EXPENSE_VOUCHER ...)` in the same transaction.

  Use `busy_as_conflict`.
- [ ] T021 [US2] Add `POST /{id}/void` to `app/api/v1/endpoints/expense_vouchers.py`, gated on 82 Delete with the facility rule.

**Checkpoint**: US1 and US2 pass on their own. Vouchers are complete except for printing.

---

## Phase 5: User Story 4 — Pay a supplier and apply the payment to purchase orders (P1)

**Goal**:
- record a numbered payment;
- apply it, now or later, to confirmed POs of its supplier that are on or after the cutoff;
- a cash payment comes out of the caller's session and the cut subtracts it.

**Independent Test**: a $10,000 payment to a supplier with confirmed POs of $6,000 and $8,000. Apply $6,000 to the first and $3,000 to the second. The PO balances are 0 and 5,000, and the payment's credit is 1,000.

### Tests for User Story 4

- [ ] T022 [P] [US4] Unit tests in `tests/unit/test_supplier_payments.py`:
  - **Create:**
    - the amount, date and method rules: 422 for an amount of 0 or less, more than 2 decimals, a future date, or an unknown method;
    - a cash payment with a body `date` answers 422;
    - a cash payment without an open session answers 409;
    - a cash payment takes the session and the session's drawer's facility;
    - a non-cash payment takes the caller's facility and no session;
    - `facility` in the body from a non-administrator, or on a cash payment, answers 422; an administrator without a facility uses it for a non-cash payment (research R5);
    - the serial is the facility's MAX + 1, numbered independently per facility;
    - an unknown supplier answers 404.
  - **Application refusals** (409; one refusal refuses the whole call):
    - the PO is a draft, or cancelled;
    - it's another supplier's PO;
    - the cutoff is NULL ("Balances start at the cutoff, which is not set yet");
    - the PO's `modification_time` is before the cutoff;
    - the amount exceeds the PO's balance, or the payment's credit;
    - the payment is cancelled, or legacy (`facility IS NULL`);
    - duplicate POs, or more than 50 items, answer 422; an unknown PO answers 404.
  - **Arithmetic:** credit is the amount minus live applications, and a reversed application doesn't count; the PO balance is `order_totals` total minus live applications.
- [ ] T023 [P] [US4] Unit tests in `tests/unit/test_cash_cut.py`:
  - `cut_figures.supplier_payments_total` sums the session's non-cancelled cash payments;
  - cancelled and non-cash payments are left out;
  - `cash_in_drawer` and `expected_cash` subtract it.
- [ ] T024 [P] [US4] Integration test in `tests/integration/test_supplier_payment_flow.py`:
  - the Independent Test, with the cutoff row set in the test;
  - creating a payment with applications in one call;
  - a cash payment lowering the session's expected cash;
  - serials 1, 2 and 3 in facility A, and 1 in facility B.
- [ ] T025 [P] [US4] API tests in `tests/api/test_supplier_payments.py` for list, create, get and `POST /{id}/applications`:
  - 401, 403 without 21 Read, Create or Update, 404, 409 and 422 as contract/payments.md says;
  - list filters: `supplier`, `facility`, `method`, `status`, `from`, `to`, and `skip`/`limit`, newest first;
  - the response's `applied`, `credit` and `applications[]`.
- [ ] T026 [P] [US4] In `tests/api/test_cash_sessions.py`: the session's cut and `expected_cash` reflect a cash supplier payment and a confirmed voucher.

### Implementation for User Story 4

- [ ] T027 [P] [US4] Write the schemas in `app/schemas/supplier_payments.py`:
  - `ApplicationItem` (`purchase_order`, `amount`);
  - `SupplierPaymentCreate`:
    - `supplier`, `amount`, `method` (a `PaymentMethod`), `date`, `reference` up to 50, `comment` up to 500, `facility`, and up to 50 `applications`;
    - validators for the amount, for 2 decimals and the maximum, for distinct POs, and for a date not in the future;
  - `ApplicationsCreate`, with 1–50 distinct items;
  - `Application`, `SupplierPaymentSummary` and `SupplierPaymentResponse`.
- [ ] T028 [US4] Write `app/services/supplier_payment_service.py`:
  - **Shared helpers:**
    - `get_cutoff(db)`, which reads `supplier_balance_cutoff` row 1;
    - `po_counts(order, cutoff)`: completed, not cancelled, and `modification_time >= cutoff`;
    - `po_applied(db, po_ids)` and `payment_applied(db, payment_ids)`, grouped sums of live applications;
    - `payment_credit(payment, applied)`, which is 0 for cancelled or legacy payments.
  - **`create_payment`**, following research R4's order (session → POs ascending → facility):
    1. pre-read the supplier, the caller's open session for cash, and the POs named; commit;
    2. `lock_open_session` (cash only);
    3. lock the PO rows ascending;
    4. `documents.assign_folio(SupplierPayment, facility=...)`;
    5. ordinary reads: the PO lines through `purchase_order_service.order_totals`, and their live applications;
    6. validate each application (`_check_applications`);
    7. insert the payment and its applications; commit.
  - **`apply_payment`:**
    1. lock the payment row;
    2. lock the PO rows ascending;
    3. ordinary reads;
    4. `_check_applications`, then insert.
  - `get_payment` and `list_payments`, with `applied` and `credit` computed and the supplier's name.

  Wrap the locking paths in `busy_as_conflict`.
- [ ] T029 [US4] Write the endpoints in `app/api/v1/endpoints/supplier_payments.py`: GET list, POST, GET `/{id}` and POST `/{id}/applications`, gated on `SystemObject.SUPPLIER_PAYMENT` (Read, Create, Update). Register the router in `app/api/v1/router.py` with `prefix='/supplier-payments'`.
- [ ] T030 [US4] Extend `app/services/cash_session_service.py` (research R8):
  - `CashCutFigures` gains `supplier_payments_total`;
  - `cut_figures` sums `SupplierPayment.amount` where `cash_session = session`, `method = PaymentMethod.CASH` and `cancelled` is false;
  - `cash_in_drawer` subtracts it.
- [ ] T031 [US4] Add the "Pagos a proveedores" row:
  - pass `supplier_payments_total` through `cash_cut_context` in `app/services/print_contexts.py`;
  - render it in `app/rendering/templates/cash_cut.html` after "Gastos", only when it is above 0.

  Check the cut print test in `tests/unit/test_cash_cut.py`, or wherever `cash_cut_context` is tested.

**Checkpoint**: US4 passes on its own. Payments, applications and cash payments all work.

---

## Phase 6: User Story 3 — Print a voucher (P2)

**Goal**: a confirmed voucher prints as a ticket, on spec 019's layout.

**Independent Test**: print a confirmed voucher. The ticket shows the header, cashier, date, lines, total and signature lines.

### Tests for User Story 3

- [ ] T032 [P] [US3] In `tests/unit/test_expense_vouchers.py`, check that `expense_voucher_context` returns the template name and:
  - `header` from `header_context`, `pad8(voucher id)` and the cashier's name;
  - the date, each line's category, note and amount, and the total.
- [ ] T033 [P] [US3] In `tests/api/test_expense_vouchers.py`, check `GET /{id}/ticket`:
  - 200 with `application/pdf` for a confirmed voucher;
  - 409 for a draft or a cancelled voucher;
  - 401, 403 (82 Read, and the facility rule) and 404.

### Implementation for User Story 3

- [ ] T034 [P] [US3] Write `app/rendering/templates/expense_voucher_ticket.html`, extending `_ticket_layout.html` the way `cash_cut.html` does:
  - the header, the voucher number, the cashier and the date;
  - the lines, then the total;
  - "Entregó" and "Recibió" signature lines.
- [ ] T035 [US3] Add `expense_voucher_context(db, voucher)` to `app/services/print_contexts.py`, following `cash_cut_context`.
- [ ] T036 [US3] Add `GET /{id}/ticket` to `app/api/v1/endpoints/expense_vouchers.py`, rendering as `GET /cash-sessions/{id}/ticket` does in `app/api/v1/endpoints/cash_sessions.py`.

**Checkpoint**: vouchers are complete (#72).

---

## Phase 7: User Story 5 — Reverse an application, cancel a payment (P2)

**Goal**: reverse a single application, or cancel a whole payment, with a reason. Both are kept as records.

**Independent Test**: reverse one application and check both balances. Then cancel the payment and check every PO it touched is back to its earlier balance.

### Tests for User Story 5

- [ ] T037 [P] [US5] Unit tests in `tests/unit/test_supplier_payments.py`:
  - **Reverse:**
    - an application that is already reversed answers 409;
    - an application of another payment answers 404;
    - on success the application's `cancelled` is set, and one source-15 incidence is written: "Application to purchase order {id} reversed: {amount}".
  - **Cancel:**
    - a payment that is already cancelled answers 409;
    - a cash payment whose session is closed answers 409 "The cash session is closed";
    - on success every live application is reversed, the payment's `cancelled` is set, and one source-15 incidence is written: "Supplier payment cancelled".
  - **After cancel:** applying to the payment answers 409, and its credit is 0.
- [ ] T038 [P] [US5] Integration test in `tests/integration/test_supplier_payment_flow.py`:
  - reverse, and the PO balance and the payment credit both rise by the amount;
  - cancel, and every PO it touched is back to its earlier balance;
  - cancelling a cash payment returns its amount to the cut.
- [ ] T039 [P] [US5] API tests in `tests/api/test_supplier_payments.py` for `POST /{id}/applications/{application_id}/reverse` and `POST /{id}/cancel`: 401; 403 without 21 Delete; 404; 409; 422 for a missing or overlong reason.

### Implementation for User Story 5

- [ ] T040 [US5] Add `reverse_application` to `app/services/supplier_payment_service.py`. It locks the payment row, then that application's PO row (research R4), sets `cancelled`, writes the incidence, and uses `busy_as_conflict`.
- [ ] T041 [US5] Add `cancel_payment` to `app/services/supplier_payment_service.py`, using `busy_as_conflict`:
  1. pre-read the payment's `cash_session`; commit;
  2. `lock_open_session` (cash only);
  3. lock the payment row and re-check it (not cancelled, same session);
  4. read its live applications (no application can be added past the payment lock), then lock their PO rows ascending;
  5. reverse every live application, set `cancelled`, `updater` and `modification_time`, and write the incidence.
- [ ] T042 [US5] Add `POST /{id}/applications/{application_id}/reverse` and `POST /{id}/cancel` to `app/api/v1/endpoints/supplier_payments.py`, gated on 21 Delete, with the reason schema of T019 (or a shared one).

**Checkpoint**: payments can be corrected.

---

## Phase 8: User Story 6 — Balances per PO and per supplier (P2)

**Goal**: show a PO's balance with its applications, and a supplier's summary, for accounts payable (#79).

**Independent Test**: for a supplier with 3 POs and 2 payments, check each PO's balance, each payment's credit, and the supplier's totals.

### Tests for User Story 6

- [ ] T043 [P] [US6] Unit tests in `tests/unit/test_supplier_payments.py`:
  - **PO balance:** `counts` is false and `balance` is null before the cutoff and with a NULL cutoff; with `counts` true, `balance = total − applied`.
  - **Supplier summary:**
    - `owed`, `credit` and `net`;
    - `orders[]` lists only counting POs with a balance above 0, oldest first;
    - `payments[]` lists only payments with credit above 0, so legacy and cancelled payments are left out;
    - `cutoff` is echoed.
- [ ] T044 [P] [US6] API tests:
  - in `tests/api/test_supplier_payments.py`, `GET /supplier-payments/suppliers/{id}/balance`: 200, 401, 403 (21 Read) and 404;
  - in `tests/api/test_purchase_orders.py`, `GET /purchase-orders/{id}/payments`: 200, 401, 403 (21 Read) and 404.

### Implementation for User Story 6

- [ ] T045 [P] [US6] Add `PurchaseOrderBalance`, `SupplierBalance`, `SupplierBalanceOrder` and `SupplierBalancePayment` to `app/schemas/supplier_payments.py` (contract/payments.md).
- [ ] T046 [US6] Add to `app/services/supplier_payment_service.py` (research R7):
  - `purchase_order_balance(db, order)`, with each application's `supplier_payment` and `serial`;
  - `supplier_balance(db, supplier_id)`, as two grouped queries plus one lines query over the counting POs. Totals come from `order_totals`.
- [ ] T047 [US6] Add the routes:
  - `GET /suppliers/{supplier_id}/balance` in `app/api/v1/endpoints/supplier_payments.py`;
  - `GET /{id}/payments` in `app/api/v1/endpoints/purchase_orders.py`.

  Both are gated on `SystemObject.SUPPLIER_PAYMENT` Read.

**Checkpoint**: every story passes (#74).

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T048 [P] Update `docs/specs/05-purchases.md` §5–6 to describe what was built:
  - voucher states and void;
  - payments, applications, the cutoff, and balances;
  - cash payments in the cut.
- [ ] T049 [P] Update `docs/data-dictionary.md`: the `supplier_payment` columns, `purchase_order_payment`, `supplier_balance_cutoff`, and incidence sources 14 and 15.
- [ ] T050 [P] Update `CHANGELOG.md` `[Unreleased]`:
  - **Added:** vouchers, supplier payments, applications, balances, migration 029;
  - **Changed:** the cut subtracts cash supplier payments; a session's close waits for in-flight vouchers and payments.
- [ ] T051 Run `uv run ruff check app tests migrations`, `uv run ruff format --check` on the changed files, and `uv run pytest -q`. Everything is green, and no route answers 500 in the API tests.
- [ ] T052 Run quickstart.md's local checks and tick off each item. The deployment steps (migration 029, deploy to dev, SC-001 to SC-004, cleanup) wait for the user's confirmation at each step and are not part of implement.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001–T003):** no dependencies.
- **Foundational (T004–T008):** after Setup. Blocks every story.
- **US1 (Phase 3):** after Foundational.
- **US2 (Phase 4):** after US1, because it voids confirmed vouchers.
- **US4 (Phase 5):** after Foundational. It doesn't depend on US1 or US2, except that T026 checks the cut with a voucher too.
- **US3 (Phase 6):** after US1.
- **US5 (Phase 7):** after US4.
- **US6 (Phase 8):** after US4. It doesn't depend on US5, but the reversal cases in T043 are easier once US5 exists.
- **Polish:** after every story.

### Within a story

- Tests come first and must fail before the implementation.
- Schemas, then service, then endpoints, then router registration.
- Tasks that edit the same file (`expense_voucher_service.py`, `supplier_payment_service.py`, an endpoint module) run in order.

### Parallel opportunities

- T002 and T003 run alongside T001. T005 runs alongside T004.
- In each story, the test tasks marked [P] run together, and the schema task runs alongside them.
- After Foundational, there are two independent tracks:
  - vouchers: US1 → US2 → US3;
  - supplier payments: US4 → US5 / US6.
- T048, T049 and T050 run together.

### Parallel example: User Story 4

```text
Together: T022 (unit), T023 (cut unit), T024 (integration), T025 (API), T026 (cash sessions API), T027 (schemas)
Then:     T028 (service) → T029 (endpoints + router)
Together: T030 (cut figure) → T031 (cut row)
```

---

## Implementation Strategy

### MVP first

1. Setup and Foundational.
2. US1: vouchers confirmed into the session, and subtracted by the cut. **Stop and validate.**
3. US2: void, serialized with close.

### Incremental delivery

1. Vouchers: US1 → US2 → US3 (#72 done).
2. Supplier payments: US4 → US5 → US6 (#74 done).
3. Polish, then the deployment steps in quickstart.md, each confirmed by the user.

### Notes

- **Every locking path follows the pattern:** pre-read, commit, locking reads, then ordinary reads. `assign_folio` is always the last lock.
- **SQLite tests** can't show row-lock races. SC-002, SC-003 and SC-004 are checked on the dev deployment (quickstart.md).
- **Commit** after each phase, with no `Co-Authored-By` lines.
