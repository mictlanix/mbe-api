# Data Model: Pilot Sales Rules

No migration. No column is added or changed. The only schema-level change is removing the `PaymentOnDelivery` mapping; the current system's mbe#84 drops the table itself.

## Read, not changed

| Table | Columns read | Rule |
|---|---|---|
| `customer_discount` | `customer`, `product`, `discount` (0–1, unique per pair) | Line discount default (FR-001) |
| `customer` | `customer_id`, `credit_limit` | Credit customer: `credit_limit > 0` and not the walk-in id (FR-010) |
| `sales_order` | `origin`, `fulfillment_intent`, `ship_to`, `promise_date`, `payment_terms` | Delivery details (FR-008), cash rule (FR-012) |
| `sales_order` / `customer_payment` and the related tables | via the existing `_customer_credit_debt` | "Outstanding credit debt" (FR-012, #220) |

## Written differently

| Column | Before | After |
|---|---|---|
| `sales_order_detail.discount_rate`, `sales_quote_detail.discount_rate` on line create | the request's value, default 0 | the request's value if sent, else the override, else 0 |
| `sales_quote.payment_terms` on create | always IMMEDIATE unless sent | NET_D for a credit customer unless sent |
| `sales_order.payment_terms` / `sales_quote.payment_terms` on a customer change | unchanged unless sent | NET_D when moving an IMMEDIATE document onto a credit customer and none is sent |

## Removed mapping

`payment_on_delivery`: the `PaymentOnDelivery` model, its data-dictionary section and its schema-dump block. mbe_dev still holds the table, with 1 row from 2017, until mbe#84's `mbe-26.10.sql` is applied there. The API never reads it.

## Confirmation states (back-office orders)

```
draft ── confirm ──► refused 409 (missing: fulfillment_intent | ship_to | promise_date)
  │                    (origin = 1 only)
  ├── confirm ──► refused 422 (credit hold, #219) / 422 (cash rule, any origin) / 409 (lines, prices, stock)
  └── confirm ──► completed (folio, reservation)
```
