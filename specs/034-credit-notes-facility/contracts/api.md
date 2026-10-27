# Contract changes: Credit Note Balances and the Facility Rule

Every route is under `/api/v1`. No route is added or removed.

## `GET /credit-notes`

Requires CreditPayments (83) read, unchanged.

| Parameter | Change |
|---|---|
| `customer` | unchanged |
| `facility` | **new.** Defaults to the caller's facility. Another facility answers 403 ("Another facility's credit notes require PaymentsEditor read") unless the caller is an administrator or holds PaymentsEditor (100) read |
| `open_only` | now filters in the query: remaining > 0.01, not paid back in cash, not the walk-in customer. `total` counts what it keeps |
| `skip`, `limit` | unchanged (limit ≤ 100) |

**200** `ListResponse[CreditNoteResponse]`, newest first:

```json
{
  "items": [{
    "credit_note_id": 990, "customer": 4211, "sales_order": 341120, "customer_refund": 9605,
    "customer_payment": 300743, "facility": 8,
    "amount": "27.00", "remaining": "3.00",
    "refunded": "0.00", "refunded_to_customer": false, "cash_session": null,
    "date": "2026-09-29T12:55:20"
  }],
  "total": 31
}
```

**Changed fields:**
- `refunded` was the amount issued. It is now the amount handed back in cash, the column's meaning: 0 unless `refunded_to_customer`.
- `remaining` was `refunded − Σ applied`. It is now the backing payment's unapplied amount, and 0 when paid back in cash.
- `amount`, `facility` and `refunded_to_customer` are new.

## `GET /credit-notes/{id}`

The same shape. **404** "Credit note not found" when the note doesn't exist, or belongs to another facility and the caller is neither an administrator nor a PaymentsEditor (100) reader.

## Customer payments

| Route | Change |
|---|---|
| `POST /customer-payments/{id}/applications` | **404** "Payment not found" for another facility's payment without PaymentsEditor (100) read or administrator. The order may still be of any facility |
| `POST /customer-payments/{id}/applications/{application_id}/reverse` | the same 404 |
| `POST /customer-payments/{id}/verify`, `/reject` | unchanged: any facility for PaymentsVerification (108) update |
| `GET /customer-payments/unverified` | covers every facility by default; `facility` narrows to one, for any PaymentsVerification (108) reader. Before, it defaulted to the caller's facility |

## Sales orders

| Route | Change |
|---|---|
| `GET /sales-orders?facility=` | **403** "Another facility's sales orders require SearchCreditsFromAllStores read" when it names another facility and the caller is neither an administrator nor a 101 reader. The default stays the caller's facility |
| `PUT /sales-orders/{id}` | **404** "Sales order not found" for another facility's order without 101 read or administrator |
| `POST /sales-orders/{id}/lines`, `PUT …/lines/{line_id}`, `DELETE …/lines/{line_id}` | the same 404 |
| `POST /sales-orders/{id}/confirm`, `/cancel` | the same 404 |
| `GET /sales-orders/{id}`, `/ticket`, `/document`, `/payments` | unchanged: any facility |

## Refund confirmation (`POST /customer-refunds/{id}/confirm`)

No request or response change. With `payout: credit_note`, the note is now written with `refunded = 0` and no cash session; the backing payment is unchanged.
