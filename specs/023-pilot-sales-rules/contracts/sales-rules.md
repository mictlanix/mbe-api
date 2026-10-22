# Contract: Pilot Sales Rules

Changes to existing routes only; no route is added or removed.

## Line create: `POST /sales-orders/{id}/lines`, `POST /sales-quotes/{id}/lines`

`SalesOrderLineCreate.discount_rate` and `SalesQuoteLineCreate.discount_rate`: `Decimal | null`, range 0–1, default **null** (was 0).

| Request | Line `discount_rate` |
|---|---|
| field omitted or `null` | customer's override for the product, else `0` |
| `0` | `0` |
| `0.05` | `0.05` |

Later line updates and customer changes never re-apply the override.

## Order create / update: `POST /sales-orders`, `PUT /sales-orders/{id}`

- **Default on create, unchanged:** NET_D for a credit customer, IMMEDIATE otherwise.
- **Update:** when `customer` moves to a credit customer, `payment_terms` is not in the request, and the order is IMMEDIATE, the order becomes NET_D. The existing credit checks then apply (422 on a hold or over the limit).
- **Cash for a credit customer at or over their limit:** when the order ends IMMEDIATE, the customer is a credit customer, and their credit debt ≥ their credit limit:

  ```
  422 {"detail": "Customer must settle their credit before buying: debt 12000.00 of limit 10000.00"}
  ```

## Order confirm: `POST /sales-orders/{id}/confirm`

New refusals, checked after the existing credit-hold check and before the line checks:

1. **Back-office delivery details** (`origin = 1` only):

   ```
   409 {"detail": {"message": "Order is missing delivery details",
                   "missing": ["fulfillment_intent" | "ship_to" | "promise_date", ...]}}
   ```

   - `fulfillment_intent` is missing when it is null.
   - `ship_to` is missing when the intent is delivery (1) or mixed (2) and `ship_to` is null.
   - `promise_date` is missing when it falls before today (business date).
2. **Cash rule:** the same 422 as above, when the order is IMMEDIATE and the customer is a credit customer at or over their limit. This applies to every origin.

Orders with `origin` 0 or null skip (1).

## Quote create / update: `POST /sales-quotes`, `PUT /sales-quotes/{id}`

- **Default on create:** NET_D for a credit customer (was always IMMEDIATE). The request's own terms still win.
- **Update:** when `customer` moves to a credit customer, `payment_terms` is not in the request, and the quote is IMMEDIATE, it becomes NET_D.
- **No refusals:** never refused on credit grounds (FR-019).

## Quote convert: `POST /sales-quotes/{id}/convert`

Existing NET_D checks are unchanged. New: the cash rule (422 above) when the quote is IMMEDIATE.

Line discounts are copied unchanged, as today. The resulting order has `origin = 1` and `fulfillment_intent = null`, so it needs an intent, and an address if it is a delivery, before it can be confirmed.
