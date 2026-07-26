---

description: "Task list for Sales Cycle Endpoints"
---

# Tasks: Sales Cycle Endpoints

**Input**: Design documents from `/specs/011-sales-cycle-endpoints/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/README.md)

**Tests**: **NOT OPTIONAL.** Constitution v1.2.0 requires tests for every change — `tests/api/` for
endpoints (happy path, 404, 401, 403, resource-specific 409/422) **and** `tests/unit/` for every
service, helper or utility carrying branching logic, state transitions or arithmetic. Tests are
written first, confirmed failing, then implemented, and are committed with the code they cover.

> The upstream Spec Kit boilerplate in `tasks-template.md` says tests are optional. It is
> contradicted by this project's constitution and is overridden here.

**Organization**: Grouped by user story so each lands as an independently testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: Which user story the task serves (US1–US8)
- Exact file paths are given in every task

## Path Conventions

Existing single-project layout, unchanged: `app/{enums,core,schemas,services,api/v1/endpoints}` and
`tests/{api,unit}` at repository root.

---

## Phase 1: Setup

**Purpose**: Establish a known-good baseline before touching anything.

- [X] T001 Confirm baseline is green on this branch: `uv run ruff check app/ migrations/ tests/` and `uv run pytest tests/ -q`
- [X] T002 Confirm no pending schema work: `uv run python -m app.db.migrate status` reports nothing pending (this feature adds no migration)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Everything every user story depends on. **No user story can start until this phase
completes.**

- [X] T003 [P] Add `PaymentTerms`, `PaymentMethod`, `PaymentType`, `Priority`, `TransactionType` and `SourceType` int enums to `app/enums.py`, values taken verbatim from `docs/constants.md` per research R7
- [X] T004 [P] Add `default_currency`, `default_quotation_due_days`, `max_days_to_deliver_stockables`, `price_validation_in_range_required` and `cost_price_list_id` settings to `app/core/config.py` per research R8
- [X] T005 [P] Write failing unit tests in `tests/unit/test_deps_current_user.py` asserting `CurrentUser` carries the caller's `employee_id`, `point_sale_id` and `cash_drawer_id`, and that each is `None` when the user has no employee or no settings
- [X] T006 Extend the `CurrentUser` dataclass in `app/core/deps.py` with `employee_id`, `point_sale_id` and `cash_drawer_id`, populated from the already-loaded `User` and its eager-loaded `UserSettings`; give each a default so existing call sites and test fixtures stay valid (research R9). Do **not** change the JWT payload
- [X] T007 Verify the `CurrentUser` change broke nothing: `uv run pytest tests/ -q` still green, especially `tests/api/test_auth.py`
- [X] T008 [P] Write failing unit tests for money computation in `tests/unit/test_totals.py`: tax-excluded lines, tax-included lines back-derived, discount application, and quantize-once-at-document-level (research R5)
- [X] T009 Implement `app/services/totals.py` — line subtotal/tax and document subtotal/tax/total/balance — making `tests/unit/test_totals.py` pass
- [X] T010 [P] Write failing unit tests for folio assignment and the editability guard in `tests/unit/test_documents.py`, including that a second confirm for the same facility does not reuse a serial
- [X] T011 Implement `app/services/documents.py` — `assign_folio()` taking a `FOR UPDATE` lock on the owning `facility` row before `MAX(serial)+1` (research R1), and `assert_editable()` refusing completed or cancelled documents — making `tests/unit/test_documents.py` pass
- [X] T012 [P] Write failing unit tests for stock ledger posting and on-hand aggregation in `tests/unit/test_stock_ledger.py`, covering negative-on-sale, positive-on-refund and positive-on-cancel
- [X] T013 Implement `app/services/stock_ledger.py` — `post_movement()` writing `lot_serial_tracking` rows (`source` = `TransactionType`, `reference` = document id) and `on_hand()` summing quantity by product + warehouse (research R4) — making `tests/unit/test_stock_ledger.py` pass
- [X] T014 [P] Write failing unit tests in `tests/unit/test_incidences.py` asserting an audit entry records source, instance id, updater, timestamp and reason, and that a missing reason is rejected
- [X] T015 Implement `app/services/incidences.py` writing `incidence` rows for audit entries (FR-045a, FR-072), making `tests/unit/test_incidences.py` pass
- [X] T016 Run `uv run ruff check app/ migrations/ tests/` and fix violations introduced by Phase 2

**Checkpoint**: Shared helpers exist and are unit-tested. User stories may now begin.

---

## Phase 3: User Story 1 — Build and confirm a sales order (P1)

**Goal**: A salesperson can open an order, manage its lines, confirm it and cancel it, with stock
moving correctly in both directions.

**Independent test**: Open an order for a credit customer, add lines for stocked products, adjust
quantity and discount, confirm, and observe a folio assigned, the order read-only, and one outbound
ledger row per stocked line. Then cancel and observe the stock restored.

- [X] T017 [P] [US1] Create request/response schemas in `app/schemas/sales_order.py`: create, update, line create/update, and response carrying derived `subtotal`/`tax_total`/`total`/`balance` and a single derived lifecycle `status`
- [X] T018 [P] [US1] Write failing endpoint tests in `tests/api/test_sales_orders.py`: happy-path create/read/update/list, 401 unauthenticated, 403 without `SALES_ORDERS` (7), 404 unknown id, 409 editing a completed order, 422 when the caller has no employee and (distinguishably) no point of sale
- [X] T019 [P] [US1] Write failing unit tests in `tests/unit/test_sales_order_service.py` for the state machine: confirm rejects zero-priced lines naming them, cancel refuses when paid, cancel refuses when live applications exist, priority stays editable after completion, credit-terms guard, and price-margin bypass with privilege 102
- [X] T020 [US1] Implement create/read/update/list in `app/services/sales_order_service.py` with defaults per FR-010 (default customer, caller's employee and point of sale, default currency at today's rate, promise date, terms from credit standing) and the credit-terms guard (FR-016)
- [X] T021 [US1] Implement line add/update/remove in `app/services/sales_order_service.py`: snapshot product code/name/tax/tax-inclusion, cost from `cost_price_list_id` (research R3), price from the customer's price list, quantity defaulted to and floored at the product minimum (FR-012, FR-013)
- [X] T022 [US1] Implement price-margin validation in `app/services/sales_order_service.py`, bypassed when the caller holds `EXCLUDE_PRICE_RANGE_VALIDATION` (102) and skipped when `price_validation_in_range_required` is false (FR-014)
- [X] T023 [US1] Implement `confirm()` in `app/services/sales_order_service.py`: refuse completed/cancelled, refuse zero-priced lines naming them, validate stock per product aggregated across lines, assign folio via `documents.assign_folio()`, post outbound movements via `stock_ledger.post_movement()`, set completed — all in one transaction (FR-017, FR-018, research R6)
- [X] T024 [US1] Implement `cancel()` in `app/services/sales_order_service.py`: refuse when paid with a message directing to refund, refuse when any non-cancelled application exists naming them, and post compensating inbound movements for a previously confirmed order (FR-019, FR-019a, FR-019b)
- [X] T025 [US1] Implement currency change bringing the exchange rate and every line into agreement in `app/services/sales_order_service.py` (FR-020)
- [X] T026 [P] [US1] Write failing unit tests in `tests/unit/test_sales_order_lookup.py` for the product lookup: a 13-digit numeric pattern routes to barcode matching, anything else to free-text, and results carry per-customer price and per-warehouse on-hand
- [X] T027 [US1] Implement the sales product lookup in `app/services/sales_order_service.py` (FR-021), making `tests/unit/test_sales_order_lookup.py` pass
- [X] T028 [US1] Create the router in `app/api/v1/endpoints/sales_orders.py` wiring every route in [contracts](./contracts/README.md#sales-orders--systemobject-sales_orders-7), each gated by `require_privilege(SystemObject.SALES_ORDERS, ...)`
- [X] T029 [US1] Register the sales-orders router in `app/api/v1/router.py` under prefix `/sales-orders`
- [X] T030 [US1] Make all US1 test files pass; run `uv run ruff check app/ tests/`

**Checkpoint**: US1 is independently demonstrable — an order can be built, confirmed and cancelled.

---

## Phase 4: User Story 2 — Take a payment and settle an order (P1)

**Goal**: Money can be recorded, applied, and reversed with evidence.

**Independent test**: Confirm an order, record a payment, apply it, watch the balance fall to zero
and the order be marked paid; reverse the application with a reason and watch all of it undo while
the cancelled application stays visible.

- [X] T031 [P] [US2] Create schemas in `app/schemas/customer_payment.py`: payment create/response with derived `unapplied`, application create/response, and a reversal body whose `reason` is required
- [X] T032 [P] [US2] Write failing endpoint tests in `tests/api/test_customer_payments.py`: record, list with explicit filters, apply, reverse, 401, 403 without `CUSTOMER_PAYMENTS` (8), 404, 409 applying to a draft or cancelled order, 422 over-application, 422 cross-currency, 422 reversal without a reason
- [X] T033 [P] [US2] Write failing unit tests in `tests/unit/test_customer_payment_service.py` for paid-flag set and clear, unapplied-amount arithmetic, that `amount_change` does not consume unapplied amount, and that reversal writes an incidence entry
- [X] T034 [US2] Implement payment create/read/list in `app/services/customer_payment_service.py`, attaching the caller's open cash session when one exists, with explicit filters only and no implicit session scoping (FR-040, FR-041, FR-009a)
- [X] T035 [US2] Implement `apply()` in `app/services/customer_payment_service.py`: require a completed, uncancelled order for the same customer, cap at unapplied amount, refuse cross-currency, record `applier`/`date`/`amount_change`, and set `paid` when applications cover the total (FR-042, FR-042a, FR-043, FR-044)
- [X] T036 [US2] Implement `reverse()` in `app/services/customer_payment_service.py`: require a reason, mark the application cancelled without deleting it, restore the balance, clear `paid`, and write an incidence entry via `incidences` (FR-045, FR-045a)
- [X] T037 [P] [US2] Write failing unit tests in `tests/unit/test_outstanding_search.py` for the outstanding-orders search: numeric terms match id or serial, text terms match customer name, customer's salesperson nickname, order salesperson nickname or `customer_name`
- [X] T038 [US2] Implement the outstanding-orders search in `app/services/customer_payment_service.py` (FR-046), making `tests/unit/test_outstanding_search.py` pass
- [X] T039 [US2] Create the router in `app/api/v1/endpoints/customer_payments.py` per [contracts](./contracts/README.md#customer-payments--systemobject-customer_payments-8), including the applications sub-resource
- [X] T040 [US2] Register the customer-payments router in `app/api/v1/router.py` under prefix `/customer-payments`
- [X] T041 [US2] Wire the cancel-blocked-by-live-applications check (T024) to the real application query and extend `tests/unit/test_sales_order_service.py` to cover it end to end
- [X] T042 [US2] Make all US2 test files pass; run `uv run ruff check app/ tests/`

**Checkpoint**: The core revenue path is complete and demonstrable — MVP boundary.

---

## Phase 5: User Story 3 — Open and close a cash session (P2)

**Goal**: A cashier's shift is bounded and auditable.

**Independent test**: Open a session on a drawer, be refused a second on the same drawer, read it
with its payment summary, then close it with denomination counts.

- [ ] T043 [P] [US3] Create schemas in `app/schemas/cash_session.py`: open request with `opening_amount`, close request with denomination counts, and a current-session response distinguishing none / open-today / open-stale
- [ ] T044 [P] [US3] Write failing endpoint tests in `tests/api/test_cash_sessions.py`: open, 409 second session on the same drawer, 409 second session for the same cashier, current in all three states, close, 401, 403 on close without `CASH_SESSION_CLOSE` (111)
- [ ] T045 [P] [US3] Write failing unit tests in `tests/unit/test_cash_session_service.py` for the three-state current-session logic (none / open-today / open-stale), the two open-session refusals, and payment summarisation by method
- [ ] T046 [US3] Implement `open_session()` in `app/services/cash_session_service.py` recording `start`, `cashier` and `cash_drawer`, refusing when the drawer or the cashier already has an open session, and storing the opening amount as a `cash_count` row of the opening type (FR-050)
- [ ] T047 [US3] Implement `current_session()` in `app/services/cash_session_service.py` returning the three-state result and the session's payments summarised by method (FR-051, FR-053)
- [ ] T048 [US3] Implement `close_session()` in `app/services/cash_session_service.py` storing `cash_count` rows and setting `end` (FR-052)
- [ ] T049 [US3] Create the router in `app/api/v1/endpoints/cash_sessions.py` per [contracts](./contracts/README.md#cash-sessions--systemobject-pos-adjacent-close-gated-by-cash_session_close-111), gating close with `CASH_SESSION_CLOSE` (111)
- [ ] T050 [US3] Register the cash-sessions router in `app/api/v1/router.py` under prefix `/cash-sessions`
- [ ] T051 [US3] Make all US3 test files pass; run `uv run ruff check app/ tests/`

**Checkpoint**: Counter operation is possible; refunds are now unblocked.

---

## Phase 6: User Story 4 — Quote a customer and convert to an order (P3)

**Goal**: Presales work is captured and flows into an order without re-keying.

**Independent test**: Create a quote, add lines, confirm it, duplicate it, and convert it into a
sales order carrying its customer and lines.

**Depends on**: US1 (conversion produces a sales order).

- [ ] T052 [P] [US4] Create schemas in `app/schemas/sales_quote.py` mirroring the order schemas, with `price_adjustment` and **no** `price_increment_rate` (see spec Divergences)
- [ ] T053 [P] [US4] Write failing endpoint tests in `tests/api/test_sales_quotes.py`: CRUD, confirm, cancel, duplicate, convert, 401, 403 without `SALES_QUOTES` (30), 404, 409 editing a confirmed quote, 409 converting an expired / unconfirmed / cancelled quote
- [ ] T054 [P] [US4] Write failing unit tests in `tests/unit/test_sales_quote_service.py` for the salesperson fallback when `customer.salesperson` is unset, expiry-date defaulting, duplicate re-fetching prices, folio assigned only at confirm, and the three conversion refusals
- [ ] T055 [US4] Implement quote create/read/update/list in `app/services/sales_quote_service.py` with defaults per FR-030
- [ ] T056 [US4] Implement quote line add/update/remove in `app/services/sales_quote_service.py` (FR-031)
- [ ] T057 [US4] Implement `confirm()` and `cancel()` in `app/services/sales_quote_service.py`, assigning the folio only at confirmation via `documents.assign_folio()` (FR-032)
- [ ] T058 [US4] Implement `duplicate()` in `app/services/sales_quote_service.py` creating an editable copy dated today with prices re-fetched from the customer's current price list (FR-033)
- [ ] T059 [US4] Implement `convert_to_order()` in `app/services/sales_quote_service.py` producing a draft order carrying customer, contact, ship-to, currency, rate and lines with the quote as origin, refusing unconfirmed, cancelled or expired quotes (FR-034)
- [ ] T060 [US4] Create the router in `app/api/v1/endpoints/sales_quotes.py` per [contracts](./contracts/README.md#sales-quotes--systemobject-sales_quotes-30); the convert route additionally requires CREATE on `SALES_ORDERS`
- [ ] T061 [US4] Register the sales-quotes router in `app/api/v1/router.py` under prefix `/sales-quotes`
- [ ] T062 [US4] Make all US4 test files pass; run `uv run ruff check app/ tests/`

---

## Phase 7: User Story 5 — Refund a customer's returned goods (P3)

**Goal**: Paid goods come back, stock is restored, and the customer gets their money as cash or
credit.

**Independent test**: Pay an order in full, open a refund, return part of a line, confirm choosing
each payout form, and observe stock restored, the source order still paid, and a second refund of
the same units capped.

**Depends on**: US1, US2, US3 (confirmation requires an open cash session).

- [ ] T063 [P] [US5] Create schemas in `app/schemas/customer_refund.py`, using `discount` on refund lines (not `discount_rate` — see Divergences) and a confirm body carrying `payout` of `cash` or `credit_note`
- [ ] T064 [P] [US5] Write failing endpoint tests in `tests/api/test_customer_refunds.py`: open against a paid order, **409 with distinguishable reasons** for not-completed vs not-paid, 409 when no refundable lines, 422 quantity above refundable, confirm with each payout form, 409 confirming with no open cash session, 403 on confirm without `CUSTOMER_REFUND_CONFIRM` (110), 401, 404
- [ ] T065 [P] [US5] Write failing unit tests in `tests/unit/test_customer_refund_service.py` for refundable-quantity arithmetic, re-validation dropping and adjusting lines at confirm, both payout branches, and that the source order's `paid` flag and `balance_zeroed_time` are never touched
- [ ] T066 [US5] Implement `open_refund()` in `app/services/customer_refund_service.py`: require a completed **and paid** order, pre-populate lines whose refundable quantity exceeds zero at quantity zero, refuse when none, with distinct reasons for not-completed and not-paid (FR-060, FR-061)
- [ ] T067 [US5] Implement refund line edit in `app/services/customer_refund_service.py` capping return quantity at the line's refundable quantity (FR-062)
- [ ] T068 [US5] Implement `confirm()` in `app/services/customer_refund_service.py`: require an open cash session, take a `FOR UPDATE` lock on the source order (research R2), re-validate and adjust or drop lines, drop zero-quantity lines, post inbound movements, assign the folio, set `date` and completed — one transaction (FR-063)
- [ ] T069 [US5] Implement the payout branch in `app/services/customer_refund_service.py`: cash paid against the open session, or a credit note plus its backing `customer_payment` classified as a credit note, for the **full** refund total; never write `balance_zeroed_time` and never alter the order's `paid` flag (FR-064, FR-065, FR-065a)
- [ ] T070 [US5] Implement `cancel()` in `app/services/customer_refund_service.py`, refused once completed (FR-066)
- [ ] T071 [US5] Create the router in `app/api/v1/endpoints/customer_refunds.py` per [contracts](./contracts/README.md#customer-refunds--systemobject-customer_refunds-22-confirm-gated-by-110)
- [ ] T072 [US5] Register the customer-refunds router in `app/api/v1/router.py` under prefix `/customer-refunds`
- [ ] T073 [US5] Make all US5 test files pass; run `uv run ruff check app/ tests/`

---

## Phase 8: User Story 6 — Spend a customer's credit note (P4)

**Goal**: Refunded value is redeemable rather than stranded.

**Independent test**: Produce a credit note through a store-credit refund, list it with its
remaining balance, redeem it against another order, and reverse the redemption.

**Depends on**: US2 (redemption is a payment application), US5 (credit notes originate there).

- [ ] T074 [P] [US6] Create schemas in `app/schemas/credit_note.py` exposing amount issued, originating refund, source order and derived remaining balance
- [ ] T075 [P] [US6] Write failing endpoint tests in `tests/api/test_credit_notes.py`: list by customer, read, 401, 403 without `CREDIT_PAYMENTS` (83), 404
- [ ] T076 [P] [US6] Write failing unit tests in `tests/unit/test_credit_note_service.py` asserting remaining balance is derived from the backing payment's non-cancelled applications, falls on redemption, is restored exactly on reversal, and that `refunded` is never decremented
- [ ] T077 [US6] Implement list/read in `app/services/credit_note_service.py` (FR-070), making the unit tests pass
- [ ] T078 [US6] Create the router in `app/api/v1/endpoints/credit_notes.py` per [contracts](./contracts/README.md#credit-notes--systemobject-credit_payments-83). **Add no redemption route** — redemption is `POST /customer-payments/{backing_payment_id}/applications` (FR-070a)
- [ ] T079 [US6] Register the credit-notes router in `app/api/v1/router.py` under prefix `/credit-notes`
- [ ] T080 [US6] Make all US6 test files pass; run `uv run ruff check app/ tests/`

---

## Phase 9: User Story 7 — Verify payments received off the counter (P4)

**Goal**: Unverified payments have a queue and a reviewer.

**Independent test**: Record a transfer payment, see it in the unverified queue, verify it, see it
leave.

**Depends on**: US2.

- [ ] T081 [P] [US7] Write failing endpoint tests in `tests/api/test_customer_payments.py` (extending the US2 file) for the unverified queue with its filters, verify, and reject with a required reason; 403 without `PAYMENTS_VERIFICATION` (108)
- [ ] T082 [P] [US7] Write failing unit tests in `tests/unit/test_payment_verification.py` for the `verifier IS NULL` filter combined with facility, date range, method and amount range, that verifying sets `verifier` and removes the payment from the queue, and that rejection writes an incidence entry carrying the reason
- [ ] T083 [US7] Implement the unverified queue in `app/services/customer_payment_service.py` (FR-071)
- [ ] T084 [US7] Implement `verify()` in `app/services/customer_payment_service.py` recording the supervisor's employee as `verifier` (FR-071)
- [ ] T085 [US7] Implement `reject()` in `app/services/customer_payment_service.py` writing an incidence entry carrying the reason (FR-072)
- [ ] T086 [US7] Add the three routes to `app/api/v1/endpoints/customer_payments.py` gated by `PAYMENTS_VERIFICATION` (108); make all US7 test files pass and run ruff

---

## Phase 10: User Story 8 — Correct a misapplied payment (P5)

**Goal**: A cashier's mistake is correctable without deleting history.

**Independent test**: Apply a payment to the wrong order, list the payment's applications including
cancelled ones, reverse the wrong one, and apply the freed amount to the right order.

**Depends on**: US2 (this story is almost entirely its reuse).

- [ ] T087 [P] [US8] Write failing endpoint tests in `tests/api/test_customer_payments.py` for listing **all** applications including cancelled, and cross-facility payment search; 403 without `PAYMENTS_EDITOR` (100)
- [ ] T088 [P] [US8] Write failing unit tests in `tests/unit/test_payments_editor.py` asserting cancelled applications are included in the listing, each naming order/amount/applier, and that cross-facility search is refused without privilege 100
- [ ] T089 [US8] Implement the applications listing in `app/services/customer_payment_service.py` returning cancelled applications too (FR-073)
- [ ] T090 [US8] Implement cross-facility payment search by customer, reference and date in `app/services/customer_payment_service.py`, gated by `PAYMENTS_EDITOR` (100) (FR-073)
- [ ] T091 [US8] Add the routes to `app/api/v1/endpoints/customer_payments.py`; make all US8 test files pass and run ruff

---

## Phase 11: Polish & Cross-Cutting Concerns

- [ ] T092 Verify the derived lifecycle `status` field is consistent across all document responses in `app/schemas/` — one state, not three raw booleans (contracts: Request/response shape notes)
- [ ] T093 [P] Apply `app/services/fk_expansion.py` to every list endpoint added by this feature so no list issues N+1 queries; confirm resolved objects land on `<column>_detail` keys and never overwrite the mapped FK, extending `tests/unit/test_fk_expansion_isolation.py`
- [ ] T094 [P] Confirm no document root exposes `DELETE` in `app/api/v1/endpoints/` (FR-006); line-level deletes exist only while the parent is editable
- [ ] T095 Run the two concurrency checks from [quickstart.md](./quickstart.md#concurrency-checks) against a real database: two simultaneous confirms yield different serials (SC-005), and two simultaneous refunds cannot over-refund a line (SC-006)
- [ ] T096 Walk quickstart Scenarios 1–5 by hand against a real database, especially Scenario 3's lifecycle rule table
- [ ] T097 Update `CHANGELOG.md` `[Unreleased]` under Added, describing the new endpoints, the enums and settings added, and the deliberate absence of POS routes
- [ ] T098 Final gate: `uv run ruff check app/ migrations/ tests/` clean and `uv run pytest tests/ -q` green, with every service in `app/services/` added by this feature having a corresponding `tests/unit/` file (Constitution v1.2.0)

---

## Dependencies

```
Phase 1 (Setup)
   └─▶ Phase 2 (Foundational) ── blocks everything
          ├─▶ US1 (P1) sales orders ──┬─▶ US4 (P3) quotes  [conversion needs orders]
          │                           │
          └─▶ US2 (P1) payments ──────┼─▶ US7 (P4) verification
                 │                    ├─▶ US8 (P5) payments editor
                 │                    │
          US3 (P2) cash sessions ─────┴─▶ US5 (P3) refunds ─▶ US6 (P4) credit notes
```

- **US1 and US2** are the MVP and can be built in parallel after Phase 2, except T041 which joins them.
- **US3** is independent of US1/US2 and can start any time after Phase 2.
- **US5** needs US1, US2 and US3 all present.
- **US6, US7, US8** are thin layers over US2 and US5.

## Parallel Opportunities

Within Phase 2: T003, T004, T005 touch three different files; the four helper modules pair as
test-then-implement (T008/T009, T010/T011, T012/T013, T014/T015) and the four pairs are mutually
independent.

Within each story phase, the schema task and every test-writing task are `[P]` — different files,
no dependency on the implementation that follows. Implementation tasks within one service file are
sequential because they edit the same file.

Across stories: once Phase 2 is done, US1, US2 and US3 can proceed on three tracks. US6, US7 and
US8 all extend `customer_payment_service.py` and `test_customer_payments.py`, so they are **not**
parallel with each other.

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + US1 + US2.** That delivers the whole revenue path: an order can be
built, confirmed, paid, reversed and cancelled, with stock moving correctly and every money movement
attributed. Everything after it is additive.

Recommended increments, each independently shippable:

1. **MVP** — Phases 1–4 (T001–T042)
2. **Counter** — Phase 5 (T043–T051)
3. **Returns** — Phases 6–7 (T052–T073)
4. **Supervisory** — Phases 8–10 (T074–T091)
5. **Polish** — Phase 11 (T092–T098), where the concurrency checks live

Do not defer Phase 11's T095 to the end of the whole feature if only the MVP is shipping — the
folio-uniqueness check applies the moment US1 is in production, and no database constraint backs it
(research R1).
