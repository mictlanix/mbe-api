# Research: Pilot Sales Rules

The spec's "Findings that shape this spec" records the investigation of the current system, mbe-ui and mbe_dev. This file records the design decisions.

## R1. Telling "no discount sent" from "discount 0" (FR-001, FR-002)

**Decision**: `SalesOrderLineCreate.discount_rate` and `SalesQuoteLineCreate.discount_rate` become `Decimal | None = Field(default=None, ge=0, le=1)`. In `add_line`, `None` means "use the customer's override for this product, or 0".

**Rationale**:
- Today the field defaults to 0, so an omitted value and an explicit 0 look the same, and FR-002 requires an explicit 0 to win.
- Clients that send a value see no change, and mbe-ui omits it (`capture_step.dart:207-213`).
- The update schemas already use `None` for "unchanged".

**Alternatives considered**: `model_fields_set` on a field that keeps its default of 0 also works, but it hides the meaning in a side channel. The OpenAPI document would still advertise a default of 0 that is no longer what happens.

## R2. Reading the override

**Decision**: Add `customer_discount_service.rate_for(db, customer_id, product_id) -> Decimal | None`, one `select(CustomerDiscount.discount)` filtered by both ids. Both `sales_order_service.add_line` and `sales_quote_service.add_line` call it only when the request's `discount_rate` is `None`.

**Rationale**: the existing service owns the table. The `(customer, product)` pair is unique (spec 021), so `scalar_one_or_none` is exact. The override is read only when it is needed, so an explicit discount costs no query.

**Not changed**:
- `update_line`, `_reprice_lines` and the customer change in `update_order` / `update_quote` do not touch discounts (FR-003).
- `convert_to_order` and `duplicate_quote` already copy `line.discount_rate` (FR-004). A test pins this.

## R3. Who counts as a credit customer (FR-010)

**Decision**: extract the condition `create_order` already inlines into `sales_order_service.is_credit_customer(customer) -> bool`: `credit_limit > 0` and not `settings.default_customer_id`. The order and quote defaults, the customer-change derivation and the cash rule all call it.

**Rationale**: one definition. The 140 customers with a limit but 0 credit days keep today's behaviour (spec Assumptions).

## R4. The credit default on quotes and on customer changes (FR-011, FR-019)

**Decision**:
- **`create_quote`:** NET_D if `is_credit_customer(customer)`, else IMMEDIATE, when the request names no terms. No credit check runs (FR-019).
- **`update_quote`:** when the customer moves to a credit customer, the request names no terms, and the quote is IMMEDIATE, set NET_D. No check runs.
- **`update_order`:** the same derivation when the customer moves. The existing end-state check (`_assert_credit_allowed` when the order ends NET_D, #219) then runs unchanged. Moving an order onto a credit customer on hold is therefore refused, the same as creating one is (#207).
- **The other direction is unchanged.** Moving a NET_D order onto a non-credit customer without naming terms is still refused by the existing check. mbe-ui sends `IMMEDIATE` in the same write for such a customer (`customer_bar.dart:192-194`).

**Rationale**: FR-011 states only the credit direction. Re-deriving cash for non-credit customers would change #219 behaviour that the spec keeps (FR-014).

**Effect on mbe-ui**: attaching a credit customer on hold to an open order is now refused at the attach. Before, the attach succeeded and the UI's follow-up credit write failed silently. Either way the order cannot be confirmed (#219), so no sale is lost; the refusal arrives earlier.

## R5. The cash rule (FR-012, FR-013, FR-019)

**Decision**: add `sales_order_service._assert_cash_allowed(db, customer)`. For a credit customer whose `_customer_credit_debt` is at or above `credit_limit`, it raises 422 `Customer must settle their credit before buying: debt X of limit Y`. Otherwise it returns. It is called when the terms are IMMEDIATE:
- **`create_order`:** after the terms are known;
- **`update_order`:** at the end of the request, when `customer` or `payment_terms` changed and the order ends IMMEDIATE (the same end-state point as #219);
- **`confirm_order`:** next to the existing NET_D limit check, for all origins (FR-013);
- **`convert_to_order`:** beside the existing NET_D check.

Quotes are never checked on create or update (FR-019).

**Rationale**:
- A cash order adds no credit debt, so the comparison is the debt alone, using the same `_customer_credit_debt` the limit check uses (#220).
- One message wording for every refusal.
- 422 matches the existing credit refusals.

**Measured**: the `_assert_within_credit_limit` docstring records that nobody in mbe_dev was over their limit at the time of #220. SC-005 re-measures "at or over" for the open cash drafts.

## R6. Delivery details at confirmation (FR-006 to FR-009, FR-018)

**Decision**: add a pure helper `missing_delivery_details(order, *, today: date) -> list[str]` in `sales_order_service`. It returns the names that are missing:
- `fulfillment_intent` when it is NULL;
- `ship_to` when the intent is DELIVERY or MIXED and `ship_to` is NULL;
- `promise_date` when `promise_date.date() < today`.

`confirm_order` calls it only when `order.origin == OrderOrigin.BACK_OFFICE`, after the hold check and before the line checks. A non-empty list raises 409 with `{'message': 'Order is missing delivery details', 'missing': [...]}`, the same shape as the zero-price and stock refusals.

**Rationale**: the refusal names each item (FR-007), and the helper is pure, so its branches are unit-tested directly (constitution, Testing). 409 is what the other confirmation refusals use.

**Consequences, all following from the spec:**
- **Converted quotes:** `convert_to_order` creates orders with `origin = BACK_OFFICE` and `fulfillment_intent = NULL`, so such an order needs an intent before it can be confirmed.
- **mbe-ui:** a back-office order with intent delivery is refused until the UI sets the order's `ship_to` (FR-018). This needs an issue in mictlanix/mbe-ui, opened as a Polish task.

## R7. PaymentOnDelivery (FR-015, FR-016)

**Decision**: remove the following, and nothing else:
- the `PaymentOnDelivery` class from `app/models/sales.py`;
- the `payment_on_delivery` section from `docs/data-dictionary.md`;
- the `CREATE TABLE payment_on_delivery` block, plus any index, FK or `DROP` lines for it, from `docs/mbe_schema.sql`.

`tests/unit/test_model_schema.py` and `test_data_dictionary.py` read both documents, so they must agree after the change.

**The spec 019 mention stays, with a note.** `specs/019-document-printing/data-model.md:105` (`PaymentOnDelivery | Contraentrega`) is the current system's resource string for the sale-receipt label. It is not the model. The receipt still prints "Contraentrega" for payments collected without a cash session (`app/rendering/templates/sale_receipt.html:111-114`, `print_contexts.py:234`). That concept lives on in `customer_payment.cash_session IS NULL`, and the current system kept it after dropping the table (mbe#84). Removing the row would leave a label the receipt uses undocumented. Instead, the row gains a note saying it is the label for payments collected on delivery, not the dropped `payment_on_delivery` table. This departs from the literal ask in #248 and is called out in the PR.

**Alternatives considered**: deleting the row as #248 lists it. Rejected because it removes the documentation of a label still in use.

## R8. Documentation

`docs/specs/02-sales.md`:
- **§2 Sales Quotes**, "Create Defaults" and "Business Rules": credit default, line discount default, quotes never refused on credit grounds.
- **§4 Sales Orders**, "Create Defaults", "Line Item Fields", "Confirm Action" and "Business Rules": discount default, credit default on customer change, the cash rule, delivery details for back-office orders.
