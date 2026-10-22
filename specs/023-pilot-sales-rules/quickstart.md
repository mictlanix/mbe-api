# Quickstart: validating Pilot Sales Rules

The route-level behaviour is in [contracts/sales-rules.md](contracts/sales-rules.md), and the decisions are in [research.md](research.md).

## Automated gate

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

Expected: zero ruff violations and a green suite, including `test_model_schema.py` and `test_data_dictionary.py` after the PaymentOnDelivery removal.

| Requirement | Covered by |
|---|---|
| FR-001–FR-005 (discount default) | `tests/unit/test_sales_order_service.py`, `tests/unit/test_sales_quote_service.py`: override, no override, explicit 0, explicit value. `tests/integration/test_customer_discount_lines.py`: real rows, customer change leaves lines alone, convert and duplicate copy |
| FR-006–FR-009 (delivery details) | `tests/unit/test_sales_order_service.py`: `missing_delivery_details` branches. `tests/integration/test_sales_rules_flow.py`: back-office refused per missing item, then confirmed; POS and null origin unaffected |
| FR-010–FR-014, FR-019 (credit) | `tests/unit/test_sales_order_service.py`: `is_credit_customer`. `tests/integration/test_sales_rules_flow.py`: order and quote defaults; customer change; cash under the limit accepted; cash at the limit refused on create, update, confirm and convert; quote never refused. `tests/integration/test_credit_hold.py` passes unchanged |
| FR-015 (PaymentOnDelivery) | `grep -rn "PaymentOnDelivery\|payment_on_delivery" app docs` returns nothing; schema and dictionary tests pass |

## Measurement re-run on mbe_dev (SC-005)

Read only. Use the connection from the `mbe-dev` CLI memory.

1. **Cash drafts for credit customers at or over their limit.**
   - Take the open cash orders (37) and open cash quotes (86) for credit customers.
   - For each one, compute the customer's credit debt the way `_customer_credit_debt` does, and compare it with `credit_limit`.
   - Report how many would now be refused at confirm or convert.
2. **Back-office drafts missing an FR-008 item:** `origin = 1`, not completed or cancelled, with any of these: `fulfillment_intent IS NULL`, intent in (1, 2) with `ship_to IS NULL`, or `promise_date < CURDATE()`. It was 1 at spec time.
3. **`customer_discount` rows:** 0 at spec time.

Record the counts in the spec's measurement section.

## Manual checks on the test deployment

Deploy only after the user confirms (`/var/www/mbe-api-test`). No migration is needed.

1. **Discount.**
   - Add `POST /customers/{id}/discounts` `{"product": P, "discount": "0.1"}`.
   - Add P to an order for that customer: the line shows `discount_rate: 0.1`.
   - Remove the discount row afterwards.
2. **Credit default on a quote.** Create a quote for a credit customer with no terms: `payment_terms: 1`. Create one for a customer on hold: it is not refused.
3. **Delivery details.** Create an order with `origin: 1`, intent delivery and no `ship_to`, and add a line. Confirming returns 409 with `missing: ["ship_to"]`. Cancel it afterwards.
