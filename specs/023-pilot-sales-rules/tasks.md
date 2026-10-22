# Tasks: Pilot Sales Rules

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/sales-rules.md](./contracts/sales-rules.md), [quickstart.md](./quickstart.md)

**Tests**: required by the constitution; the template's "tests are optional" line does not apply. In every story the test tasks come first and must fail before the implementation. Each test task is marked **(commit with T0xx)**.

**Shared files**: US2 and US3 both edit `app/services/sales_order_service.py` (`confirm_order`) and `tests/unit/test_sales_order_service.py`. US1 edits the `add_line` functions in both sales services. Run the stories one after another; `[P]` marks only tasks within a phase that touch different files.

**Integration test pattern**: follow `tests/integration/test_credit_hold.py`:
- use the `client` and `db` fixtures from `tests/integration/conftest.py`;
- seed customers with `db.add(Customer(...))`;
- drive the API through `client`;
- product 1 exists in the template schema.

## Format: `- [ ] T### [P?] [US#] Description · path`

---

## Phase 1: Setup

- [X] T001 Record the baseline before any change: run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. Write the pass/fail counts under this task · `specs/023-pilot-sales-rules/tasks.md`
  - **Baseline (2026-10-22)**: ruff clean; pytest 3240 passed, 0 failed.

---

## Phase 2: User Story 1 — A customer's agreed discount appears on the line by itself (P1)

**Goal**: FR-001 to FR-005. A line added without a discount takes the customer's override for the product.

**Independent test**: with an override of 0.1 for customer C and product 1:
- adding product 1 to C's order without a discount gives 0.1;
- an explicit 0 gives 0;
- another customer gets 0;
- a converted quote keeps its edited discount.

- [X] T002 [US1] Write `tests/integration/test_customer_discount_lines.py` (commit with T003–T006). Seed customer C (no credit line) and create the override with `POST /api/v1/customers/{C}/discounts` `{"product": 1, "discount": "0.1"}`. Cases:
  1. **Order line, override applies:** create an order for C with `payment_terms: 0`, add product 1 without `discount_rate`: the line's `discount_rate` is `0.1`.
  2. **Explicit 0 is kept:** the same with `discount_rate: "0"`: `0`.
  3. **Explicit value is kept:** with `discount_rate: "0.05"`: `0.05`.
  4. **No override:** an order for a second customer D: `0`.
  5. **Not re-applied on edit:** update a line's quantity with `PUT .../lines/{id}`: the discount is unchanged. Edit it to `0.2`, then change the quantity again: it stays `0.2`.
  6. **Not re-applied on customer change:** move an order with a line from D to C with `PUT /sales-orders/{id}` `{"customer": C}`: the existing line keeps `0`. A line added afterwards gets `0.1`.
  7. **Quote line:** the same as case 1 on `POST /api/v1/sales-quotes/{id}/lines`: `0.1`.
  8. **Convert copies:** a quote line edited to `0.07` and converted with `POST /sales-quotes/{id}/convert`: the order line has `0.07`.
  9. **Duplicate copies:** duplicating a quote with `POST /sales-quotes/{id}/duplicate` gives the copy the same line discounts.

  Cases 5, 6, 8 and 9 pin existing behaviour and may pass before T003–T006. Cases 1 and 7 must fail first.
- [X] T003 [P] [US1] In `app/schemas/sales_order.py` (`SalesOrderLineCreate`) and `app/schemas/sales_quote.py` (`SalesQuoteLineCreate`), change `discount_rate: Decimal = Field(default=Decimal(0), ge=0, le=1)` to `discount_rate: Decimal | None = Field(default=None, ge=0, le=1)`. Add a one-line comment: `None` means the customer's override for the product, else 0 (spec 023 FR-001). See research R1.
- [X] T004 [P] [US1] Add `async def rate_for(db, customer_id: int, product_id: int) -> Decimal | None` to `app/services/customer_discount_service.py`: `select(CustomerDiscount.discount).where(customer == ..., product == ...)` → `scalar_one_or_none()`. The pair is unique (spec 021).
- [X] T005 [US1] In `app/services/sales_order_service.py` `add_line`, set `discount_rate=data.discount_rate if data.discount_rate is not None else (await customer_discount_service.rate_for(db, customer.customer_id, product.product_id) or Decimal(0))`. `customer` is already loaded there. Import `customer_discount_service`. Do not touch `update_line` or `_reprice_lines` (FR-003).
- [X] T006 [US1] Apply the same change to `app/services/sales_quote_service.py` `add_line`, which also has `customer` loaded. Then run `uv run pytest tests/integration/test_customer_discount_lines.py tests/api/test_sales_orders.py tests/api/test_sales_quotes.py tests/unit -q`: green. If an existing test built a line create and asserted `discount_rate == 0` without sending it, it must still pass: with no override row, the result is 0. Commit T002–T006.

**Checkpoint**: discounts apply.

---

## Phase 3: User Story 2 — Credit customers buy on credit by default, and cannot dodge their debt in cash (P1)

**Goal**: FR-010 to FR-014 and FR-019.

**Independent test**:
- a quote for a credit customer with no terms gets NET_D, even if the customer is on hold;
- a credit customer under the limit can create a cash order;
- at or over the limit, cash is refused on create, update, confirm and convert;
- `tests/integration/test_credit_hold.py` passes unchanged.

- [X] T007 [P] [US2] Add unit tests for `is_credit_customer` to `tests/unit/test_sales_order_service.py` (commit with T009). Build `SimpleNamespace(customer_id=..., credit_limit=...)` stubs:
  - `credit_limit` `None` → False;
  - `0` → False;
  - `5000` → True;
  - the walk-in id (`settings.default_customer_id`) with `5000` → False.
- [X] T008 [P] [US2] Write `tests/integration/test_sales_rules_flow.py`, class `TestCreditTerms` (commit with T009–T013).
  - **Seeded customers:**
    - **G:** credit limit 5000, credit days 30, no debt (in good standing).
    - **H:** limit 5000, with one overdue unpaid NET_D order, as `_overdue_credit_order` in `test_credit_hold.py` (on hold).
    - **L:** limit 1000, with one **completed, unpaid, not yet due** NET_D order (due date one year ahead), holding a `SalesOrderDetail` for product 1 with quantity 1 and price 1500 (at or over the limit, not on hold).
    - **N:** limit 0 (non-credit).
  - **Cases:**
    1. `POST /sales-quotes {customer: G}` → `payment_terms == 1`. For N → `0`.
    2. `POST /sales-quotes {customer: H}` → 201, `payment_terms == 1`: never refused (FR-019). `PUT` that quote with `{payment_terms: 0}` and with `{customer: N}` → 200.
    3. An order created for N, then `PUT {customer: G}` without terms → 200, `payment_terms == 1`.
    4. An order for N, then `PUT {customer: H}` without terms → 422, the credit-hold message (#207 and #219 wording).
    5. `POST /sales-orders {customer: G, payment_terms: 0}` → 201: cash under the limit is allowed.
    6. `POST /sales-orders {customer: L, payment_terms: 0}` → 422, and the detail starts with `Customer must settle their credit`.
    7. **Update to cash at the limit:** an order for N, then `PUT {customer: L, payment_terms: 0}` → 422 with the same message.
    8. **Confirm at the limit:** an open cash draft for L, inserted directly with `db.add(SalesOrder(... payment_terms=0, completed=False ...))` plus a line, then confirm → 422 with the same message.
    9. **Convert at the limit:** a cash quote for L (`POST /sales-quotes {customer: L, payment_terms: 0}` → 201, FR-019), with a line, then `POST /sales-quotes/{id}/convert` → 422 with the same message.
    10. A cash order for the walk-in customer and for N, created and confirmed with a line → no cash-rule refusal.
- [X] T009 [US2] In `app/services/sales_order_service.py`, add `def is_credit_customer(customer) -> bool` (research R3): `bool(customer.credit_limit and customer.credit_limit > 0) and customer.customer_id != settings.default_customer_id`. Replace the inline condition in `create_order` with it.
- [X] T010 [US2] In `app/services/sales_order_service.py`:
  - **Helper:** add `async def _assert_cash_allowed(db, customer) -> None` (research R5). If `is_credit_customer(customer)` and `await _customer_credit_debt(db, customer.customer_id) >= customer.credit_limit`, raise `HTTPException(422, f'Customer must settle their credit before buying: debt {debt:.2f} of limit {limit:.2f}')`. Give it a docstring citing spec 023 FR-012 and why the comparison uses the debt alone.
  - **Call sites:**
    - (a) in `create_order`, `if terms == PaymentTerms.IMMEDIATE: await _assert_cash_allowed(db, customer)`;
    - (b) in `update_order`, beside the #219 end-state check: when `customer` or `payment_terms` changed and `order.payment_terms == PaymentTerms.IMMEDIATE`, call it with the order's customer;
    - (c) in `confirm_order`, in the branch beside the existing `if order.payment_terms == PaymentTerms.NET_D:` limit check: `else: await _assert_cash_allowed(db, await _customer_or_404(db, order.customer))`.
- [X] T011 [US2] In `app/services/sales_order_service.py` `update_order`, inside the customer branch: when the customer moved, `'payment_terms' not in changes`, `order.payment_terms == PaymentTerms.IMMEDIATE` and `is_credit_customer(customer)`, set `order.payment_terms = int(PaymentTerms.NET_D)` and recompute `order.due_date` with `derive_due_date(order.date, PaymentTerms.NET_D, credit_days=customer.credit_days or 0)`. The existing end-state check then applies. Leave the reverse direction alone (research R4).
- [X] T012 [US2] In `app/services/sales_quote_service.py`:
  - **`create_quote`:** default the terms to `PaymentTerms.NET_D if sales_order_service.is_credit_customer(customer) else PaymentTerms.IMMEDIATE` when `data.payment_terms is None`.
  - **`update_quote`:** in the customer branch, the same derivation as T011 (moved, terms not in the request, quote IMMEDIATE, credit customer → NET_D).
  - **No credit check** in either function (FR-019). Add a one-line comment saying so.
- [X] T013 [US2] In `app/services/sales_quote_service.py` `convert_to_order`, beside `if terms == PaymentTerms.NET_D: await sales_order_service._assert_credit_allowed(...)`, add `else: await sales_order_service._assert_cash_allowed(db, customer)`. Then run `uv run pytest tests/integration tests/unit tests/api -q`. All green, including `tests/integration/test_credit_hold.py` unchanged (SC-004). Commit T007–T013.
  - **Done (2026-10-22)**: 3263 passed. Three things beyond the task list:
    - `test_credit_hold.py::test_a_cash_sale_is_weighed_against_no_limit` asserted the opposite of FR-012. It is rewritten as `test_a_cash_sale_over_the_limit_is_refused_for_its_own_reason`, and SC-004 now names the exception.
    - The customer stubs in `TestCustomerChangeGuard` (orders) gained `credit_limit=None`.
    - In `TestTheSalespersonFollowsTheCustomer` (quotes), the quote stub gained `payment_terms` and the customer stub `credit_limit=None`. The new code reads both.

**Checkpoint**: the credit rules are enforced.

---

## Phase 4: User Story 3 — Back-office orders are confirmed with delivery details and a date (P2)

**Goal**: FR-006 to FR-009 and FR-018.

**Independent test**:
- a back-office order is refused at confirm, with the missing items named, for: no intent; delivery with no `ship_to`; a past promise date;
- it confirms once complete, and pickup confirms without an address;
- POS and null-origin orders are unaffected.

- [X] T014 [P] [US3] Add unit tests for `missing_delivery_details(order, *, today)` to `tests/unit/test_sales_order_service.py` (commit with T016). Build `SimpleNamespace(fulfillment_intent=..., ship_to=..., promise_date=...)` stubs with `today = date(2026, 10, 22)`:
  - intent `None` → contains `'fulfillment_intent'`;
  - DELIVERY with `ship_to None` → `['ship_to']`;
  - MIXED with `ship_to None` → `['ship_to']`;
  - PICKUP with `ship_to None` → `[]`;
  - DELIVERY with `ship_to=5`, promise `2026-10-22 08:00` → `[]` (today passes);
  - promise `2026-10-21 23:59` → `['promise_date']`;
  - everything missing → all three, in the order intent, ship_to, promise_date.
  - **As built:** with no intent, `ship_to` is not reported. Whether an address is needed depends on the intent, which is unknown, so the refusal names the intent and, if stale, the date: `['fulfillment_intent', 'promise_date']`.
- [X] T015 [P] [US3] Add class `TestBackOfficeDelivery` to `tests/integration/test_sales_rules_flow.py` (commit with T016).
  - **Setup:** use the walk-in customer, or a non-credit customer, to keep credit out of it. Use the address the integration seed already creates, `address_id=1` (`tests/integration/seed.py:79`), as `ship_to`. No new seeding is needed.
  - **Cases:**
    1. `origin: 1` with no intent, add a line, confirm → 409, `detail.missing == ['fulfillment_intent']`.
    2. `origin: 1`, `fulfillment_intent: 1`, no `ship_to` → 409, `['ship_to']`. Then `PUT {ship_to: A}` → confirm 200.
    3. `origin: 1`, intent 0 (pickup), no `ship_to` → confirm 200.
    4. `origin: 1`, intent 1, `ship_to: A`, `promise_date` yesterday → 409, `['promise_date']`.
    5. `origin: 0`, no intent → confirm 200.
    6. No origin → confirm 200.
    7. **Converted quote:** a converted quote's order (origin 1, intent null) → 409 with `fulfillment_intent`.
    8. **Nothing written on refusal:** after a 409, the order still has `serial is None` and `completed is False`.
- [X] T016 [US3] In `app/services/sales_order_service.py`:
  - **Helper:** add the pure `def missing_delivery_details(order, *, today: date) -> list[str]` (research R6), with a docstring citing spec 023 FR-008 and the mbe-ui dependency (FR-018).
  - **Call site:** in `confirm_order`, right after `_assert_not_on_credit_hold`, add: `if order.origin == OrderOrigin.BACK_OFFICE: missing = missing_delivery_details(order, today=local_now().date())`. If anything is missing, raise `HTTPException(409, detail={'message': 'Order is missing delivery details', 'missing': missing})`.

  Run `uv run pytest tests -q`: green. Commit T014–T016.

**Checkpoint**: delivery details are enforced.

---

## Phase 5: User Story 4 — The dead PaymentOnDelivery model is gone (P3)

**Goal**: FR-015 and FR-016.

**Independent test**: `grep -rn "PaymentOnDelivery\|payment_on_delivery" app docs` returns nothing, and `tests/unit/test_model_schema.py` and `tests/unit/test_data_dictionary.py` pass.

- [X] T017 [US4] Remove the `PaymentOnDelivery` class from `app/models/sales.py` (lines ~228-240). Remove imports only if this change left them unused. Remove the `### payment_on_delivery` section, up to the next heading, from `docs/data-dictionary.md`. Remove the `payment_on_delivery` `CREATE TABLE` block and any statements naming it (`DROP TABLE IF EXISTS`, `ALTER TABLE ... payment_on_delivery`, constraints) from `docs/mbe_schema.sql`. Run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py -q` and the grep: both clean. This is behaviour removal with no new assertion: the existing schema tests are the test, and the exemption is stated here. Commit.
- [X] T018 [P] [US4] In `specs/019-document-printing/data-model.md` line ~105, keep the `PaymentOnDelivery | Contraentrega` row. Below the table, add one sentence: the row is the sale-receipt label for payments collected on delivery (`customer_payment.cash_session IS NULL`, `sale_receipt.html`), not the `payment_on_delivery` table dropped by mictlanix/mbe#84 (research R7). Commit with T017.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T019 [P] Update `docs/specs/02-sales.md` (research R8):
  - **§2 Sales Quotes**, "Create Defaults" and "Business Rules": credit default, line discount default, never refused on credit grounds.
  - **§4 Sales Orders**, "Create Defaults": credit default on customer change.
  - **§4**, "Line Item Fields": discount default.
  - **§4**, "Confirm Action": back-office delivery details, and the cash rule.
  - **§4**, "Business Rules": the cash rule and its scope.

  Cite spec 023.
- [ ] T020 [P] Add to `CHANGELOG.md` `[Unreleased]`, in the existing entry style:
  - **`### Changed`**, the sales rules (mictlanix/mbe-roadmap#149, #35, spec 023):
    - line discount default;
    - quote credit default;
    - credit on customer change;
    - cash refused at or over the limit;
    - back-office delivery details.
    - **Client notes:** `discount_rate` on line create is now nullable with default null, and mbe-ui must set the order's `ship_to` (FR-018).
  - **`### Removed`:** the `PaymentOnDelivery` model (#248).
- [ ] T021 Run the full gate: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. No new failures against the T001 baseline. Record the counts here.
- [ ] T022 Re-run the SC-005 measurement on mbe_dev, read only, following `quickstart.md`: open cash drafts for credit customers at or over their limit, back-office drafts missing an FR-008 item, and `customer_discount` rows. Record the counts in the spec's measurement section · `specs/023-pilot-sales-rules/spec.md`
- [ ] T023 **Confirm with the user before creating it.** Draft an issue for mictlanix/mbe-ui: "Set the sales order's ship_to before confirming a back-office delivery". Explain FR-018: the API refuses to confirm a back-office order with intent delivery or mixed until the order's own `ship_to` is set, and the UI today sets addresses only on delivery orders after confirming. Link spec 023. Create it with `gh issue create` once approved, and record the URL here.
- [ ] T024 **Confirm with the user before deploying.** Deploy the branch to the test instance (`/var/www/mbe-api-test`) and run the manual checks in `quickstart.md`. Clean up the seeded discount and the test order. Record the results in `quickstart.md`.

---

## Dependencies

- T001 → everything.
- **US1** (T002–T006): independent.
- **US2** (T007–T013) and **US3** (T014–T016) both edit `confirm_order` and `tests/unit/test_sales_order_service.py`. Run US3 after US2. US1 touches `add_line` only and can go before or after.
- **US4** (T017–T018): independent of every other story.
- **Polish** comes after all stories. T023 and T024 wait for the user's go-ahead.

## Parallel opportunities

- **US1:** T003 (schemas) and T004 (discount service) are different files.
- **US2:** T007 (unit) and T008 (integration) are different files.
- **US3:** T014 and T015 are different files.
- **US4:** T018 alongside T017.
- **Polish:** T019 and T020.
- **Whole stories:** US4 can run alongside any other story.

## Implementation strategy

1. **US1 (discounts):** closes #149 on its own.
2. **US2 (credit):** the larger behaviour change. Land it with the hold tests green.
3. **US3 (delivery):** small, but it depends on mbe-ui for delivery orders (FR-018), which is why T023 exists.
4. **US4 (model removal):** any time.
5. **Polish:** docs, measurement, the mbe-ui issue, the test deployment.
6. **PR:** under "Roadmap", write `Closes mictlanix/mbe-roadmap#149`, `Closes mictlanix/mbe-roadmap#35` and `Closes #248`.
