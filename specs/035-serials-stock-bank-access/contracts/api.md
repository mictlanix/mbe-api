# Contract changes: Supplier Sub-Panels by Privilege, Race-Safe Serials and Stock

Every route is under `/api/v1`. No route is added or removed, and no request or response shape changes.

## `GET /suppliers/{supplier_id}`

The gate is unchanged: Suppliers (3), Products (0) or Pricing (106) read.

**200** `SupplierDetailResponse`. Each panel is filled only for a caller who may read it (data-model, Supplier detail), and is `[]` otherwise. As a user with Products read only:

```json
{
  "supplier_id": 112, "code": "PROV-112", "name": "Aceros del Norte", "zone": null,
  "credit_limit": "0.00", "credit_days": 30, "comment": null,
  "addresses": [], "contacts": [], "bank_accounts": [], "agreements": []
}
```

## `POST /suppliers`, `PUT /suppliers/{supplier_id}`

- Gates are unchanged: Suppliers create and update.
- `addresses`, `contacts` and `bank_accounts` for a panel the caller cannot read are ignored. On update, that panel's links stay as they are. On create, the panel has no links.
- The other fields and the readable lists apply as today.
- The response is filtered like `GET`.

## `GET /customer-payments`

| Parameter | Change |
|---|---|
| `search` | All digits: matches the payment's id or its serial, within the list's facility scope. Anything else: the customer's name, as today. |

**200** `ListResponse[CustomerPaymentResponse]`. `serial` is the payment's folio: a facility sequence number, no longer `0` for the API's payments.

## `POST /customer-payments`

- Unchanged request.
- The created payment carries its facility's next `serial`.
- **409 "The cash session is closed"** when the caller's open session was closed between the pre-read and the lock (research R6). This is new.
- **409 "The facility is busy, retry"** on a deadlock or lock timeout.

## `POST /customer-refunds/{id}/confirm`

- Unchanged request and response.
- The payment it writes (the cash payout or the credit note's backing payment) is numbered.
- **409 "The cash session is closed"** when the cashier's session was closed between the pre-read and the lock (research R3). This is new; today the refund is recorded into the closed session.
- **409 "The warehouse is busy, retry"** on a deadlock or lock timeout.

## `POST /sales-orders/{id}/confirm`

- Unchanged request and response.
- **409 `{"message": "Insufficient stock", "lines": [...]}`** (unchanged shape) when another confirmation or an inventory step took the units first.
- **409 "Document is completed and can no longer be edited"** for the second of two concurrent confirmations of one order.
- **409 "The warehouse is busy, retry"** on a deadlock or lock timeout.
- No longer: the generic 409 "The request conflicts with existing data" from a duplicate serial.

## `PUT /sales-orders/{id}`, `POST/PUT/DELETE /sales-orders/{id}/lines…`

Unchanged request and response. An edit that reaches a confirmed order, including one that waited behind the confirmation, answers the existing 409 "Document is completed and can no longer be edited".

## `POST /sales-quotes/{id}/confirm`, `POST /delivery-orders/{id}/confirm`

Unchanged request and response. **409 "The facility is busy, retry"** on a deadlock or lock timeout. The generic duplicate-serial 409 no longer occurs.

## `POST /delivery-itineraries/{id}/stops/{stop_id}/close`

Unchanged request and response. A split child order's serial is race-safe. **409 "The warehouse is busy, retry"** on a deadlock or lock timeout.
