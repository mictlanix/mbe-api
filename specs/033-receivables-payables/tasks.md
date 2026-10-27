---

description: "Task list for accounts receivable and accounts payable (spec 033)"
---

# Tasks: Accounts Receivable and Accounts Payable

**Input**: Design documents from `specs/033-receivables-payables/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R14), data-model.md, contracts/accounts.md, quickstart.md

**Tests**: requested. The constitution and plan call for tests first on every service branch and route. Within each story, write the tests, see them fail, then implement.

**Where tests go:**
- `tests/unit/`: pure rules and state transitions, on plain objects (`SimpleNamespace`), as the existing unit files do.
- `tests/api/`: the route contract only. These tests replace the service with `AsyncMock`, as `tests/api/test_customer_payments.py` does, and cover 401, 403, 404, 422, 409, 502, 503, and what scope and parameters reach the service.
- `tests/integration/`: anything that needs real rows, on SQLite, through the `db` and `client` fixtures of `tests/integration/conftest.py`. That fixture's caller is an administrator. A scoped caller overrides `current_user` with `administrator=False` and seeds its `access_privilege` rows.

**Commits** (constitution, Testing and Linting Gate):
- A test task is committed together with the implementation task that makes it pass, never alone.
- `uv run ruff check app/ migrations/ tests/` and `uv run ruff format --check .` pass before every commit.

**Organization**: by user story. P1 stories come first (US1, US2), then P2 (US3, US4, US5), then P3 (US6).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task).
- **[Story]**: US1–US6 from spec.md.
- Paths are relative to the repository root. Work in the worktree `mbe-api-033`.

**Notes on plan.md, found while writing these tasks:**
- The stale-flag correction follows `app/jobs/expire_orders.py`: the job is a thin CLI, and the logic lives in a service (`customer_payment_service.settle_stale_flags`), so it can be tested on SQLite. Incidences use `SYSTEM_EMPLOYEE_ID` as the updater, as `order_expiry` does.
- One customer-side function selects open orders, `sales_order_service.open_credit_orders`. The credit check, the AR view and statements all read it (FR-001).
- `supplier_balance` (spec 032) lists POs with a balance above 0. Once it reads `open_purchase_orders`, it uses spec 033's 0.01 threshold. A PO with exactly 0.01 left is the only case that changes.
- CFDI cancellation is mictlanix/mbe-roadmap#51, not mbe-api#51.

---

## Phase 1: Setup

**Purpose**: the one enum value.

- [X] T001 Add `SUPPLIER = 16` to `SourceType` in `app/enums.py`, after `SUPPLIER_PAYMENT = 15`. Comment it like the 10–15 entries: `# API-only, like 10–15: a supplier account statement emailed (spec 033).` (research R12).

---

## Phase 2: Foundational (blocks every story)

**Purpose**: the customer balance function, the corrected unapplied amount, the shared view core and the response schemas. Every story reads them.

### Customer balance (FR-002, FR-003; research R1, R2)

- [X] T002 [P] Write the test first, in `tests/unit/test_order_balances.py`, for a pure helper `credit_note_shares(amount: Decimal, parts: dict[int, Decimal]) -> dict[int, Decimal]`. Cases:
  - the amount is split in proportion to `parts`, each share rounded to cents;
  - the order with the highest id takes the rounding remainder, so the shares sum to `amount` exactly (100 split three ways over equal parts gives 33.33, 33.33, 33.34);
  - a single order takes the whole amount;
  - `parts` that sum to 0 or less give `{}`;
  - an amount of 0 gives every order 0.
- [X] T003 Implement `credit_note_shares` in `app/services/sales_order_service.py`, near `refunded_by_order`, so T002 passes.
- [X] T004 Write the test first, in `tests/integration/test_order_balances.py`, for `order_balances(db, orders)`. Use `seed_sales_order` and `seed_fiscal_document` from `tests/integration/seed.py`. Cases:
  - total minus live applications. A cancelled application doesn't count, and `amount_change` is never subtracted (research R14, "Not changed");
  - completed, non-cancelled refunds are deducted. Draft and cancelled refunds aren't;
  - a live credit note (type 100) related to an invoice of the order is deducted;
  - a note line whose `order_detail` belongs to the order counts in full for that order;
  - one invoice covering two orders: the untraced part of the note is split by each order's share of the invoice's line totals (`credit_note_shares`);
  - a cancelled note doesn't count, and neither does a draft with no XML (`fiscal_document_service.live`);
  - deductions above the total give a balance of 0, never negative;
  - several orders in one call give the same figures as one call per order.
- [X] T005 Implement in `app/services/sales_order_service.py` so T004 passes:
  - A frozen dataclass `OrderBalance` with:
    - `totals`: the `totals.document_totals` result, so callers don't fetch the lines again;
    - `total`, `refunded`, `credited`, `applied` and `balance`.
  - `order_balances(db, orders: Sequence[SalesOrder]) -> dict[int, OrderBalance]`, in batched queries for the whole set:
    - the lines, as `attach_summary_totals` fetches them today;
    - applied: sum of non-cancelled `SalesOrderPayment.amount`;
    - `refunded_by_order`, unchanged;
    - credited, from a new `_credited_by_order(db, order_ids)`:
      - find the live invoices that have a `fiscal_document_detail` whose `order_detail` is a line of these orders;
      - find the live credit notes related to them (`FiscalDocumentRelation.relation`, `type == CREDIT_NOTE`, `fiscal_document_service.live`);
      - take each note's total from its XML (`xml_total(get_xml(...))`, as `invoice_settlements` reads it);
      - a traced note line goes to its order in full (its total by `cfdi_invoice.line_amounts`);
      - the rest of the note goes through `credit_note_shares`. Each order's part is the sum of that invoice's line totals for that order's lines;
    - `balance = totals.remaining(total, [refunded, credited, applied])`.
- [X] T006 Add `open_credit_orders(db, *, customer: int | None = None, facility: int | None = None, due_before: datetime | None = None) -> list[tuple[SalesOrder, OrderBalance]]` to `app/services/sales_order_service.py` (FR-007, research R5):
  - a private `_credit_candidates(db, *, customer=None, facility=None, due_before=None) -> list[tuple[SalesOrder, OrderBalance]]` selects `payment_terms == NET_D`, completed, not cancelled and `paid` false, filtered by the arguments that are given, and runs `order_balances` on them. It's the one customer-side selector: the stale-flag job (T057) reads it too;
  - `open_credit_orders` keeps the candidates with a balance above 0.01.

  Test it in `tests/integration/test_order_balances.py`:
  - an unpaid order with a zero balance is left out;
  - an order flagged paid with a computed balance is left out;
  - an IMMEDIATE order is left out;
  - the facility and customer filters work.

### Unapplied amount counts change (FR-005a; research R14)

- [X] T007 [P] Write the tests first, in `tests/integration/test_payment_flow.py` (the unit file has no database fixture):
  - `unapplied_amount` and `attach_summary_unapplied` both give `amount − Σ(amount + amount_change)` over non-cancelled applications;
  - the payment-300789 shape gives 0: 600, with 530 applied and 70 change;
  - a cancelled application's change doesn't count.
- [X] T008 Change `unapplied_amount` and `attach_summary_unapplied` in `app/services/customer_payment_service.py`:
  - sum `SalesOrderPayment.amount + SalesOrderPayment.amount_change`;
  - update the docstring of `unapplied_amount` to say that change handed back can't be applied again, as the ticket and the current system's `Balance` count it.

  T007 must pass.
- [X] T009 In `app/services/print_contexts.py`, make `payment_ticket_context` print `balance` from `customer_payment_service.unapplied_amount(db, payment.customer_payment_id)`:
  - drop the local `allocated` sum;
  - remove the docstring sentence saying `unapplied` and the ticket differ.

  Run the existing ticket tests in `tests/unit/test_print_contexts.py` and the ticket route tests in `tests/api/test_customer_payments.py`. The printed figure must not change.

### Shared view core and schemas (FR-014, FR-015; research R6, R7)

- [X] T010 [P] Write the tests first, in `tests/unit/test_accounts.py`, for the pure core of `app/services/accounts_service.py`:
  - `days_past_due(due: datetime | None, today: date) -> int`: 0 with no due date or a due date of today or later; otherwise local days;
  - `bucket(days: int) -> str`: `current` for 0, `1-30`, `31-60`, `61-90`, `90+`, with the edges 30/31, 60/61 and 90/91;
  - `summarize(documents, *, credit, credit_limit, owed_company)`:
    - owed and overdue are Σ `balance × exchange_rate`;
    - the buckets sum to owed to the cent;
    - `oldest` is the earliest `date`;
    - `net = owed − credit`;
    - `utilization = owed_company / limit` (4 decimals), and `available_credit = limit − owed_company`, which can be negative;
    - with a limit of 0 or None, both are None;
    - a customer with a 50,000 limit and 40,000 owed company-wide gets 0.8000 and 10,000, whatever `owed` is (US2 scenario 3).
- [X] T011 Implement the pure core in `app/services/accounts_service.py` so T010 passes:
  - a frozen dataclass `Document` in the shared `OpenDocument` shape (data-model.md);
  - `BUCKETS = ('current', '1-30', '31-60', '61-90', '90+')`;
  - `days_past_due`, `bucket` and `summarize`. `summarize` returns a dict with `PartySummary`'s figures.

  Use `local_now()` for today in callers, never inside the pure functions.
- [X] T012 [P] Create `app/schemas/accounts.py` with the shapes in `contracts/accounts.md`:
  - `Buckets`, with the aliases `1-30`, `31-60`, `61-90` and `90+`;
  - `PartySummary`, with `sales_agent` and `sales_agent_name` optional;
  - `LinkedDocument`, `OpenDocument` and `AccountPayment`;
  - `PartyDetail`;
  - `AccountScope`;
  - `AccountsSummaryResponse`: `items`, `total`, `scope`, `totals` and an optional `cutoff`;
  - `StatementEmailRequest`:
    - `contacts: list[int] = []` and `to: list[str] = []`, each at most 10;
    - a model validator requiring at least one of the two;
    - `to` checked and deduplicated by the same rule as `QuoteEmailRequest._valid_and_distinct` in `app/schemas/sales_quote.py`. Import and reuse `_EMAIL`; don't copy the pattern;
  - `StatementEmailResponse`: `sent_at` and `to`.

  Money fields use the existing decimal response convention, as `app/schemas/customer_payment.py` does.

**Checkpoint**: `uv run pytest -q` is green. Nothing reads `order_balances` yet, so no existing figure has moved, except `unapplied`, which now subtracts change.

---

## Phase 3: User Story 1 — One customer balance everywhere (P1) 🎯 MVP

**Goal**: order lists, the order detail, the credit check, the outstanding-orders list and `paid` maintenance all read `order_balances`. The hold counts overdue orders by balance.

**Independent Test**: for a customer with a credit note, a refund and applications, the order's `balance`, the credit check's debt and the outstanding-orders balance agree. A customer whose only overdue order has a zero balance and is flagged unpaid can confirm a new order.

### Tests

- [X] T013 [P] [US1] Extend `tests/integration/test_credit_hold.py`:
  - an overdue NET_D order flagged unpaid whose applications cover it doesn't hold the customer (US1 scenario 1);
  - an overdue order whose balance a live credit note covers doesn't hold the customer;
  - an overdue order with 0.02 left does hold the customer;
  - the limit check sums `order_balances`, so a credit note lowers the debt.
- [X] T014 [P] [US1] Extend `tests/integration/test_payment_flow.py` with `paid` maintenance (FR-011, research R3):
  - an application that brings the balance to 0.01 or less sets `paid` and stamps `balance_zeroed_time`;
  - reversing it clears `paid` and keeps `balance_zeroed_time`;
  - the sentinel `0001-01-01` counts as unset and is replaced;
  - a paid order whose balance was already above 0.01 stays paid when an application on it is reversed. That's the legacy case: seed the order flagged paid with a partial application;
  - a partial application leaves `paid` false.
- [X] T015 [P] [US1] Extend `tests/integration/test_credit_note_flow.py`: confirming a credit note whose share covers an unpaid order's balance sets `paid` on that order. An order on the same invoice that still owes stays unpaid.
- [X] T016 [P] [US1] Extend `tests/integration/test_refunded_balance.py`: `confirm_refund` on a refund that brings an unpaid order's balance to 0 sets `paid` (US1 scenario 4). Seed the refund rows directly, since `open_refund` requires a paid order (FR-060).
- [X] T017 [P] [US1] Write `tests/integration/test_outstanding_orders.py` for `GET /customer-payments/outstanding-orders`, through the `client` fixture:
  - an unpaid order with a balance of 0.01 or less isn't listed, whatever its payment terms;
  - an unpaid cash sale with a balance is still listed (FR-010: any terms);
  - `balance` deducts a credit note's share, and equals the order's `order_balances` balance;
  - `total` counts only the listed orders, and paging is stable;
  - the facility scope, the `customer` filter and search are unchanged.
- [X] T018 [P] [US1] Add to `tests/integration/test_order_balances.py`: `attach_derived`, `attach_summary_totals` and `order_balances` report the same `balance` for an order with a refund and a credit note.
- [X] T019 [P] [US1] Write the tests first, in `tests/unit/test_customer_payment_service.py`, for `settle(order, balance)` and `reopen(order, before, after)` on `SimpleNamespace` orders (FR-011, research R3):
  - `settle` at 0.01 and at 0 sets `paid`;
  - `settle` stamps `balance_zeroed_time` when it's None, and replaces the `0001-01-01` sentinel;
  - `settle` keeps an existing zero time;
  - `settle` at 0.02 changes nothing, and never clears `paid`;
  - `reopen` clears `paid` only when `before <= 0.01 < after`. Check the table: (0, 0.02) clears; (0.01, 0.02) clears; (0.02, 50) doesn't, the legacy case; (0, 0.01) doesn't;
  - `reopen` never touches `balance_zeroed_time`.

### Implementation

- [X] T020 [US1] In `app/services/sales_order_service.py`, make `attach_derived` and `attach_summary_totals` take `subtotal`, `tax_total`, `total` and `balance` from `order_balances`:
  - `attach_derived` keeps its own line fetch for the line rows;
  - `attach_summary_totals` drops its own line, applied and refunded queries;
  - update the `balance` comment to name credit notes.

  T018 must pass, along with the existing sales order tests.
- [X] T021 [US1] In `app/services/sales_order_service.py`, rewrite the credit check on `open_credit_orders` (FR-009):
  - `_overdue_credit_orders` returns `len(await open_credit_orders(db, customer=customer_id, due_before=local_now()))`;
  - `_customer_credit_debt` returns Σ `balance × exchange_rate` over `open_credit_orders(db, customer=customer_id)`;
  - rewrite both docstrings: the debt nets refunds and credit notes through the one balance rule (FR-001), and the hold counts overdue orders with a balance above 0.01, not the flag alone. Remove the "It does not net off refunds" paragraph and its 2025 figures. Keep the NET_D and exchange-rate reasoning.
  - Messages and the walk-in exemption don't change.

  T013 must pass.
- [X] T022 [US1] In `app/services/customer_payment_service.py`, replace `_sync_paid_flag` with two plain functions, with no database argument (research R3):
  - `settle(order, balance: Decimal)`: if the order isn't flagged paid and the balance is 0.01 or less, set `paid`, and stamp `balance_zeroed_time` with `local_now()` when it's None or before 1900. It never clears `paid`, and it leaves an order already flagged paid alone, zero time included: a refund on a legacy paid order settles nothing new (spec 011 FR-065a), so it mustn't gain a commission month;
  - `reopen(order, before: Decimal, after: Decimal)`: clear `paid` only when `before <= 0.01 < after`. It never touches `balance_zeroed_time`.

  Callers compute the balances with `order_balances`. Keep the commission-month comment on `settle`. Remove `covers_total` and `_order_total` once nothing uses them, and update or remove their unit tests in `tests/unit/test_customer_payment_service.py`. T019 must pass.
- [X] T023 [US1] In `app/services/customer_payment_service.py`:
  - `apply_payment`: after the insert and flush, call `settle` with the order's balance from `order_balances`;
  - `reverse_application`: read `before` from `order_balances` before cancelling, and `after` after the flush, then call `reopen`.

  T014 must pass.
- [X] T024 [US1] In `app/services/customer_refund_service.py`, `confirm_refund`: after `refund.completed = True` and a flush, call `customer_payment_service.settle(order, balance)` on the order it already locks, with the balance from `order_balances`. T016 must pass.
- [X] T025 [US1] In `app/services/invoice_service.py`, `confirm` for a credit note (research R4):
  - after the XML row is added and flushed, lock the orders on the related invoice in ascending id with `with_for_update()`. Find them through the invoice's `fiscal_document_detail.order_detail` → `sales_order_detail.sales_order`;
  - compute `order_balances`, and call `customer_payment_service.settle` on each;
  - the invoice lock taken in `_prepare_credit_note` stays first, so the order is invoices → orders;
  - `reopen` of a sealed credit note (back to draft, its XML deleted) is a raising event: lock the same orders, read their balances before and after the XML goes, and call `customer_payment_service.reopen`. Found during implementation: spec 028 lets a sealed, unstamped note go back to draft.

  T015 must pass, with a reopen test beside it.
- [X] T026 [US1] In `app/services/customer_payment_service.py`, `search_outstanding` (FR-010):
  - select the candidates with today's filters (scope, customer, search);
  - run `order_balances` on all of them, and drop balances of 0.01 or less;
  - set `total` to the remaining count, then page in Python, newest id first;
  - `balance` and `total` come from `order_balances`;
  - drop the per-row `_order_total` and `applied_amount` calls.

  At most about 1,000 candidates per facility on mbe_dev. T017 must pass.

**Checkpoint**: US1 works alone. Run `uv run pytest -q`; sales order, payment, refund, credit note and credit hold tests are green.

---

## Phase 4: User Story 2 — Receivables view with aging and credit utilization (P1)

**Goal**: `GET /accounts-receivable` and `GET /accounts-receivable/{customer_id}`, gated on 18, scoped by facility unless the caller has 101.

**Independent Test**: as a user with 18 only and as one with 18 and 101, check the rows, the scope, `facility=` and the buckets.

### Tests

- [X] T027 [P] [US2] Write the route contract in `tests/api/test_accounts_receivable.py`, with `accounts_service` and `customer_payment_service` patched:
  - `GET /accounts-receivable`:
    - 200 with the patched service's rows, in the contract's `PartySummary` shape, with `scope` and `totals`;
    - 401 without a login, 403 without read on 18;
    - a caller without 101 reaches the service with their own facility, and `scope` is `{"facility": <theirs>, "all_facilities": false}`;
    - `facility=<other>` from that caller is 403;
    - an administrator, or a caller with 101 read, reaches it with `facility=None`, or with the facility passed;
    - `party`, `overdue`, `sales_agent`, `sort`, `skip` and `limit` are passed through, and `limit` above 200 is 422;
  - `GET /accounts-receivable/{customer_id}`:
    - 200 with the patched service's result, in the contract's `PartyDetail` shape, with `payments`;
    - 404 when the service raises it;
    - the payment list gets the caller's facility, unless the caller is an administrator or holds PaymentsEditor (100) read (#271).
- [X] T028 [P] [US2] Write `tests/integration/test_accounts_receivable_flow.py`, through the `client` fixture, on seeded orders in two facilities:
  - **Summary:**
    - a scoped caller (no 101) sees only their facility's orders, and each customer's figures add up those orders only (US2 scenario 1);
    - an administrator sees every facility, and `facility=` narrows the view (scenario 2);
    - `owed_company`, `utilization` and `available_credit` are equal for the scoped caller and the administrator. A 50,000 limit with 40,000 owed gives 0.8000 and 10,000 (scenario 3);
    - a customer with no limit has null utilization and available credit (scenario 4);
    - an order flagged paid isn't counted, and neither is an unpaid order with balance 0;
    - the buckets sum to `owed` in every row and in `totals`;
    - the `party`, `overdue` and `sales_agent` filters and the three sorts work;
    - the walk-in customer is listed with no limit.
  - **Drill-down:**
    - `documents` are sorted by due date, oldest first, and an order without a due date comes last;
    - each document has `linked` live invoices (`batch` + `serial`), `facility_code`, `salesperson`, `deductions` (refunded + credited), `applied` and `balance`;
    - a fully paid order isn't listed (scenario 5);
    - `payments` lists the customer's payments with `unapplied` above 0.01. A payment whose change used it up isn't listed (US4 scenario 6);
    - a customer with nothing open gives `documents: []` and a zero summary.

### Implementation

- [X] T029 [US2] Add the customer side to `app/services/accounts_service.py`:
  - `_customer_documents(db, *, facility, customer=None) -> list[Document]`:
    - from `sales_order_service.open_credit_orders`;
    - add the live invoices per order in one query (`fiscal_document_detail.order_detail` → order lines, `fiscal_document_service.live`, `type` invoice) and facility codes in one query;
    - `deductions = refunded + credited`;
  - `_customer_credit(db, customer_ids) -> dict[int, Decimal]`: one grouped query; per payment, `amount − Σ(amount + amount_change)` over live applications; summed per customer where above 0.01 (research R7). Built on `payments_with_credit` (T031), the same arithmetic as `unapplied_amount`;
  - `_customer_parties(db, ids)`: name, `credit_limit`, `credit_days`, `salesperson` and the agent's name;
  - a frozen dataclass `Side`: privilege, incidence source, labels, and the loader callables. Add a `CUSTOMERS` instance.
- [X] T030 [US2] Add the side-agnostic reads to `app/services/accounts_service.py`:
  - `summary(db, side, *, facility, party=None, overdue=False, sales_agent=None, sort='owed', skip=0, limit=50)`:
    - load the documents in scope;
    - group them by party;
    - load the company-wide owed for those parties: a second load with `facility=None`, only when the scope is one facility;
    - load credit and party data, then `summarize`;
    - filter, sort and page;
    - return the rows, the count and the column totals;
  - `detail(db, side, party_id, *, facility)`: the party's summary and documents, 404 when the party doesn't exist.
- [X] T031 [US2] Add `payments_with_credit(db, *, customers, facility: int | None = None)` to `app/services/customer_payment_service.py`:
  - the customers' payments, filtered by facility when it's given;
  - unapplied computed in the query, with `unapplied_amount`'s arithmetic, so the walk-in customer's fully used cash payments stay in the database;
  - only those with `unapplied` above 0.01, oldest first. `_customer_credit` sums the same rows.
- [X] T032 [US2] Create `app/api/v1/endpoints/accounts_receivable.py`, with `require_privilege(SystemObject.ACCOUNTS_RECEIVABLE, AccessRight.READ)` on every route:
  - `_scope(db, current, facility)`:
    - all facilities when the caller is an administrator or `has_any_privilege(db, current.user_id, [(SystemObject.SEARCH_CREDITS_FROM_ALL_STORES, AccessRight.READ)])`, narrowed by `facility` if given;
    - otherwise `current.facility_id`, and 403 when `facility` is given and differs;
  - `GET ''` → `accounts_service.summary(..., side=CUSTOMERS)`;
  - `GET '/{customer_id}'` → `accounts_service.detail`, plus `payments` from `payments_with_credit`. The facility filter applies unless the caller is an administrator or holds PaymentsEditor (100) read, the same check as `_sees_every_facility` in `app/api/v1/endpoints/customer_payments.py`.

  Register the router in `app/api/v1/router.py` under `/accounts-receivable`. T027 and T028 must pass.

**Checkpoint**: US1 and US2 make the MVP: one balance, and the AR view.

---

## Phase 5: User Story 3 — Payables view with the same shape (P2)

**Goal**: `GET /accounts-payable` and `GET /accounts-payable/{supplier_id}`, gated on 19 and company-wide. They're built on spec 032's balances, with due dates from receipts.

**Independent Test**: with a test cutoff, every supplier's `owed`, `credit` and `net` equal `GET /supplier-payments/suppliers/{id}/balance`.

### Tests

- [X] T033 [P] [US3] Extend `tests/unit/test_accounts.py` with the PO due date: a pure `po_due_date(first_receipt: datetime | None, credit_days: int | None) -> datetime | None`. A receipt on 1 October with 30 days is due 31 October; with no receipt it's None; with null days it's the receipt date (US3 scenario 1, edge cases).
- [X] T034 [P] [US3] Extend `tests/integration/test_supplier_payment_flow.py` for `open_purchase_orders`:
  - with the cutoff NULL there are none;
  - under a cutoff, a PO with a balance is open;
  - its due date is its first completed, non-cancelled receipt's `modification_time` plus credit days;
  - if that receipt is cancelled, the next one dates it;
  - with no receipt the due date is None, and the PO is still open;
  - `supplier_balance` gives the same owed.
- [X] T035 [P] [US3] Write the route contract in `tests/api/test_accounts_payable.py`, with `accounts_service` patched:
  - `GET /accounts-payable`: 200 with the patched rows in the `PartySummary` shape, with no sales agent, and `cutoff`;
  - `GET /accounts-payable/{supplier_id}`: 200 in the AP `PartyDetail` shape (`serial` null, no `facility` or `salesperson`);
  - 401, and 403 without read on 19;
  - a caller of any facility with 19 reaches the service with no facility filter, and a `facility` parameter isn't accepted (FR-019, US3 scenario 4);
  - 404 for an unknown supplier when the service raises it.
- [X] T036 [P] [US3] Write `tests/integration/test_accounts_payable_flow.py`, through the `client` fixture:
  - with the cutoff NULL, an empty list and `cutoff: null` (US3 scenario 3);
  - under a test cutoff, each row's `owed`, `credit` and `net` equal `GET /supplier-payments/suppliers/{id}/balance` (SC-003);
  - the buckets sum to owed;
  - a PO without a receipt counts in owed with no due date, in `current` (scenario 2);
  - the drill-down has `serial` null, no `facility` or `salesperson`, `linked` receipts as facility code + serial, `deductions` 0, and spec 032's payments with credit.

### Implementation

- [X] T037 [US3] Add `po_due_date` to `app/services/accounts_service.py`, so T033 passes.
- [X] T038 [US3] Add `open_purchase_orders(db, *, supplier: int | None = None)` to `app/services/supplier_payment_service.py`:
  - the counting POs (`po_counts`'s filters in SQL, as `supplier_balance` writes them);
  - batched `_po_totals` and `po_applied`;
  - the confirmed receipts per PO in one query (`InventoryReceipt.purchase_order`, completed, not cancelled), with facility code, serial and `modification_time`;
  - keep the POs with `total − applied` above 0.01;
  - return each with its receipts and its first receipt time.

  Make `supplier_balance` build its `orders` list from it. Then T034 passes.
- [X] T039 [US3] Add `credit_by_supplier(db, supplier_ids) -> dict[int, Decimal]` to `app/services/supplier_payment_service.py`:
  - batched over a shared `payments_with_credit(db, *, suppliers)`, which `supplier_balance` and the AP drill-down read too;
  - `supplier_balance`'s credit total must equal it for one supplier.
- [X] T040 [US3] Add the supplier side to `app/services/accounts_service.py`:
  - `_supplier_documents`: from `open_purchase_orders`. `date` is the confirmation time, `due_date` comes from `po_due_date`, the currency is MXN at 1, and `deductions` is 0;
  - `_supplier_credit`: `credit_by_supplier`;
  - `_supplier_parties`;
  - a `SUPPLIERS` `Side`.
- [X] T041 [US3] Create `app/api/v1/endpoints/accounts_payable.py`, with `require_privilege(SystemObject.ACCOUNTS_PAYABLE, AccessRight.READ)`:
  - `GET ''` is company-wide, with no `facility` or `sales_agent` parameter, and adds `cutoff` from `get_cutoff`;
  - `GET '/{supplier_id}'` adds `payments` from spec 032's payment credit.

  Register the router in `app/api/v1/router.py` under `/accounts-payable`. T035 and T036 must pass.

**Checkpoint**: AP mirrors AR. With the cutoff NULL it's empty.

---

## Phase 6: User Story 4 — Apply a payment from the view (P2)

**Goal**: the existing customer apply route refuses what FR-021 lists, under spec 032's locking. The supplier route already does.

**Independent Test**: apply 600 and 400 from a 1,000 payment to orders owing 600 and 700. Then try another customer's payment, 350 against 300, and a paid order.

### Tests

- [X] T042 [P] [US4] In `tests/unit/test_customer_payment_service.py`, test the new pure guards:
  - `assert_within_balance(amount, balance)`: 422 "Amount X exceeds the order balance of Y";
  - `assert_not_settled(order, balance)`: 409 "Sales order is already settled" when `paid` is set or the balance is 0.01 or less.
- [X] T043 [P] [US4] Extend `tests/integration/test_payment_flow.py` for `POST /customer-payments/{id}/applications`, through the `client` fixture:
  - US4 scenario 1 end to end: the first order is paid, the second owes 300, and the payment's `unapplied` is 0;
  - another customer's payment is 422;
  - 350 against an order owing 300 is 422;
  - an order flagged paid, or with 0 left, is 409;
  - `amount + amount_change` above the unapplied amount is 422;
  - an order of another facility is still accepted (FR-021);
  - a cash sale gets the same refusals;
  - `GET /customer-payments/{id}` on the 600 / 530 / 70-change payment shows `unapplied` 0 (US4 scenario 6).

  In `tests/api/test_customer_payments.py`, check only that a 409 the patched service raises reaches the caller unchanged.
- [X] T044 [P] [US4] Check that `tests/api/test_supplier_payments.py` covers another supplier's payment, an amount above the PO balance, and an amount above the payment's credit (US4 scenario 5). Add only the missing cases. Spec 032 already covers all three (409), so nothing was added.

### Implementation

- [X] T045 [US4] Add `assert_within_balance` and `assert_not_settled` to the pure rules in `app/services/customer_payment_service.py`, so T042 passes.
- [X] T046 [US4] Rewrite `apply_payment` in `app/services/customer_payment_service.py` (research R4), inside `async with inv.busy_as_conflict(db):`:
  1. Lock the payment row with `select(CustomerPayment)…with_for_update()` and `execution_options(populate_existing=True)`.
  2. Lock the order row the same way. 404 if it's missing.
  3. Then read `unapplied_amount` and `order_balances`.
  4. Check `assert_order_payable`, `assert_same_customer`, `assert_same_currency`, `assert_not_settled`, `assert_within_unapplied(data.amount + data.amount_change, unapplied)` and `assert_within_balance(data.amount, balance)`.
  5. Insert, flush, `settle` (T023), commit.

  `inv` is `app.services.inventory_documents`, as in `expense_voucher_service`. `busy_as_conflict` gains an optional message, 'The order is busy, retry', since its default names the warehouse. T043 must pass, and the old test pinning that nothing capped an application at the order's balance becomes a refusal test.
- [X] T047 [US4] In `reverse_application`, lock the payment, then the order, in the same order as T046, inside `busy_as_conflict`, before reading `before`. Run the reversal tests again.

**Checkpoint**: the concurrency check runs on dev, after confirmation (quickstart step 5). SQLite doesn't serialize row locks.

---

## Phase 7: User Story 5 — Statement as PDF and by email (P2)

**Goal**: `GET /{side}/{id}/statement` and `POST /{side}/{id}/statement/email` on both sides. One template, covering all facilities.

**Independent Test**: render a customer's and a supplier's statement, compare the totals with the unscoped view, and email one with a fake mailer. One incidence is recorded.

### Tests

- [X] T048 [P] [US5] Write `tests/integration/test_account_statement.py` for `account_statement_context(db, side, party_id)` on seeded rows, as `tests/integration/test_delivery_documents_render.py` renders documents:
  - the totals equal `accounts_service.detail(..., facility=None)`, with orders in two facilities;
  - payments are those within the 90 days before the statement date, with applied and unapplied (customers), or applied and credit (suppliers);
  - the wording is "Cliente" / "Facturas" with a sales agent, or "Proveedor" / "Recepciones" without one;
  - a party with only credit renders, with no documents;
  - the PDF renders.
- [X] T049 [P] [US5] Write the email tests in two files:
  - `tests/unit/test_account_email.py`, for a pure `merge_recipients(contact_addresses, typed) -> list[str]`: deduplicated case-insensitively, keeping the first spelling; more than 10 after deduplication is 422; none is 422;
  - `tests/integration/test_account_email.py`, for `email_statement` with a fake mailer, as `tests/integration/test_fiscal_email.py` does:
    - a contact of another party, or one without an address, is 422;
    - `MailError` is 502 and records nothing;
    - success records one incidence: source 7 or 16, `instance_id` = the party, reason "Account statement emailed", context "To: …".
- [X] T050 [P] [US5] Extend the route contracts in `tests/api/test_accounts_receivable.py` and `tests/api/test_accounts_payable.py` for the statement routes, with the services patched:
  - the PDF is `application/pdf` with filename `estado-de-cuenta-{id}.pdf`;
  - the AR statement reaches the service with `facility=None` for a caller scoped to one facility (FR-023), and 101 isn't required (FR-025);
  - 403 without 18 or 19, 404 for an unknown party;
  - email: 503 with no mailer, with the service not called, and 200 with `sent_at` and `to`.

### Implementation

- [X] T051 [US5] Add `account_statement_context(db, side, party_id, *, facility) -> tuple[str, dict]` to `app/services/print_contexts.py` (`facility` chooses the letterhead only):
  - built from `accounts_service.detail(db, side, party_id, facility=None)`;
  - the 90-day payments from each side's `recent_payments`: customer payments with their applied amounts (change handed back is neither applied nor left) and `attach_summary_unapplied`, or supplier payments with `payment_applied`/`payment_credit`;
  - `header_context`, and `formatting.money`/`formatting.date_time` for display;
  - side labels from `Side`.
- [X] T052 [US5] Create `app/rendering/templates/account_statement.html`, extending `_print_layout.html`:
  - the party block: name, limit, credit days, available credit, and the sales agent when present;
  - the aging buckets row;
  - the open documents table: date, serial or id, linked, due date, days past due, balance;
  - the 90-day payments table;
  - owed, credit and net.

  Follow an existing statement-like template's markup, for example the purchase order or quote template, for header and table classes.
- [X] T053 [US5] Create `app/services/account_email.py`:
  - `merge_recipients(contact_addresses, typed) -> list[str]`, pure: deduplicated case-insensitively, keeping the first spelling, 1 to 10 or 422;
  - `recipients(db, side, party_id, contact_ids, typed) -> list[str]`, reading `customer_contact` or `supplier_contact` joined to `Contact.email`, then `merge_recipients`;
  - `email_statement(db, side, party_id, *, contact_ids, to, mailer, current) -> tuple[datetime, list[str]]`, in `sales_quote_service.email_quote`'s order: render, send (502 on `MailError`), record with `incidences.record(source=side.source, ...)`, commit;
  - the subject is "{company} Estado de cuenta {party}". The body gives owed and overdue, and says the PDF is attached;
  - the incidence context uses the same "To: …" format as `sales_quote_service._recipients_context`.
- [X] T054 [US5] Add the two routes to `app/api/v1/endpoints/accounts_receivable.py` and `app/api/v1/endpoints/accounts_payable.py`:
  - `GET '/{id}/statement'` returns the PDF from `rendering.render_pdf`, as the quote print route does;
  - `POST '/{id}/statement/email'` takes `StatementEmailRequest` and `mailer: mail.Mailer | None = Depends(mail.get_mailer)`. It returns 503 when the mailer is None, as `email_sales_quote` in `app/api/v1/endpoints/sales_quotes.py` does, and responds with `StatementEmailResponse`.

  T048–T050 must pass.

---

## Phase 8: User Story 6 — Correct the stale paid flags once (P3)

**Goal**: the orders that are settled but still flagged unpaid (257 on mbe_dev) are corrected by a rerunnable job.

**Independent Test**: on SQLite, the dry run changes nothing, `--apply` corrects and records, and a second run selects 0.

### Tests

- [X] T055 [P] [US6] Write `tests/unit/test_settle_paid_flags.py` for the pure rule `stale_zero_time(order_date, events: Sequence[datetime]) -> datetime | None`:
  - for an order dated before 2026, None;
  - for one dated 2026 or later, the latest event;
  - with no events, None.
- [X] T056 [P] [US6] Write `tests/integration/test_settle_paid_flags.py` for `customer_payment_service.settle_stale_flags(db, *, apply: bool)`:
  - it selects NET_D, completed, not cancelled, unpaid orders with a balance of 0.01 or less, and ignores IMMEDIATE and paid orders;
  - the dry run writes nothing;
  - `apply=True` sets `paid`;
  - a 2025 order keeps `balance_zeroed_time` None;
  - a 2026 order gets the latest of its live applications' `date`, completed refunds' `date` and credit notes' `issued`;
  - one SALES_ORDER (3) incidence per order, by `SYSTEM_EMPLOYEE_ID`, with reason "Paid flag corrected: balance is settled (spec 033)";
  - a second run selects 0.

### Implementation

- [X] T057 [US6] Add `stale_zero_time` and `settle_stale_flags` to `app/services/customer_payment_service.py`:
  - take the candidates from `sales_order_service._credit_candidates` (T006), and keep those with a balance of 0.01 or less;
  - gather each order's event dates in batched queries;
  - return a report: per order, the id, date, balance and new zero time.

  T055 and T056 must pass.
- [X] T058 [US6] Create `app/jobs/settle_paid_flags.py`, shaped like `app/jobs/expire_orders.py` (`_run` disposes the engine in the same loop):
  - `python -m app.jobs.settle_paid_flags` is a dry run;
  - `--apply` writes;
  - it prints one line per order (id, date, balance, new zero time or "unset") and a count.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T059 [P] Rewrite §1–2 of `docs/specs/07-administration.md` for the API:
  - the shared model table;
  - the routes;
  - the scope rules (18 with 101; 19 company-wide);
  - the balance rule, and `paid` maintenance;
  - the statement;
  - the job.
- [X] T060 [P] Add to `CHANGELOG.md` under `[Unreleased]`:
  - **Added:** the AR and AP views, statements as PDF and email, `settle_paid_flags`;
  - **Fixed:** a customer payment's `unapplied` subtracts change handed back;
  - **Changed:**
    - the credit hold and limit read the shared balance;
    - balances deduct credit notes;
    - customer applications are locked and capped at the order balance;
    - `paid` is cleared only on a crossing;
    - outstanding orders lists only orders that owe;
    - a supplier's balance (spec 032) lists POs with more than 0.01 left, not more than 0.
- [X] T061 Run `uv run ruff check app/ migrations/ tests/`, `uv run ruff format --check` on the files this spec touched, and `uv run pytest -q`, all green. (`ruff check .` also lints `.specify/` scripts, and 7 touched test files were unformatted before this branch; both are left as they were.)
- [X] T062 Run the quickstart on mbe_dev and the dev instance. Each step that writes, deploys or sends email needs the user's confirmation first.
  - Steps 1–4 and 6: SC-001, SC-002, SC-004, SC-008;
  - step 5: SC-005, including the 20-way concurrency run;
  - step 7: SC-007;
  - step 8: SC-003, then reset the cutoff to NULL;
  - step 9: SC-006, to an internal address only;
  - step 10: cleanup.

  Record the figures in `.spec-context.json` verifications. Done on 2026-10-27, except step 9: the user chose not to send email. Step 5's first run found the locks didn't serialize (fixed in a426fff, rerun clean).
- [X] T063 After confirmation, comment on mictlanix/mbe-roadmap#51 (CFDI cancellation): cancelling a credit note must lock the related orders, read their balance before and after, and call `customer_payment_service.reopen` (spec 033 FR-011, research R3).

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after Setup. Blocks every story.
- **US1 (Phase 3)**: after Phase 2. The MVP core.
- **US2 (Phase 4)**: after Phase 2. It can run beside US1, but its figures are only right once US1's T021 has made the credit check agree. Finish US1 first when working alone.
- **US3 (Phase 5)**: after Phase 2, and after US2's T029–T030, which give it the `Side` shape and the `summary`/`detail` functions. It's independent of US1.
- **US4 (Phase 6)**: after US1's T022–T023 (`settle`, and the same functions it rewrites).
- **US5 (Phase 7)**: after US2 and US3, since the statement reads `detail` on both sides.
- **US6 (Phase 8)**: after Phase 2 (`order_balances`). It's independent of the views.
- **Polish (Phase 9)**: after the stories it documents or checks.

### Within each story

Tests first, and they must fail. Then pure rules, then services, then routes. Tasks on the same file run in ID order.

### Same-file chains (not parallel)

- `app/services/sales_order_service.py`: T003 → T005 → T006 → T020 → T021.
- `app/services/customer_payment_service.py`: T008 → T022 → T023 → T026 → T031 → T045 → T046 → T047 → T057.
- `app/services/accounts_service.py`: T011 → T029 → T030 → T037 → T040.
- `app/services/supplier_payment_service.py`: T038 → T039.
- `app/api/v1/router.py`: T032 → T041.
- `tests/integration/test_payment_flow.py`: T007 → T014 → T043.
- `tests/api/test_accounts_receivable.py`: T027 → T050; `tests/api/test_accounts_payable.py`: T035 → T050.

---

## Parallel Examples

**Phase 2**: T002, T007, T010 and T012 together (four files). Then T003, T008 and T011, each after its test.

**US1 tests**: T013, T014, T015, T016, T017, T018 and T019 together, all in different files.

**US1 implementation**: T024 (refund service) and T025 (invoice service) beside T022–T023 once `settle` exists.

**US2 and US3 tests**: T027, T028, T033, T034, T035 and T036 together.

**US5 tests**: T048, T049 and T050 together.

**US6**: T055 and T056 together. The whole story can run beside US3–US5.

---

## Implementation Strategy

### MVP first (US1, then US2)

1. Phase 1 and Phase 2.
2. US1: one balance. Validate with the unit and integration tests, then quickstart steps 1, 3 and 6 on dev after confirmation.
3. US2: the AR view. Validate scope and buckets.
4. Stop here if needed. The credit hold is fixed, and collection has its view.

### Incremental delivery

- US4 next: the apply guards protect every customer payment, not only the view.
- US3, then US5: AP and statements.
- US6 last. It's a one-time data fix that runs after confirmation, and again at cutover.

### Notes

- Commit each test task together with the implementation task that makes it pass, never alone, and run the ruff gate before every commit. No `Co-Authored-By` line.
- Nothing in this spec writes to mbe_dev except T062's confirmed steps and the job's `--apply`.
