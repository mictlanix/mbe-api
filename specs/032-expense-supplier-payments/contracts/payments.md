# Contract: Expense Vouchers and Supplier Payments

Base path: `/api/v1`. Every route needs a bearer token; administrators bypass the privilege checks. Errors use the usual `{"detail": "..."}` shape. "Facility" in the right column means a non-administrator acting on another facility's voucher gets 403 (FR-008).

## Expense vouchers: `/expense-vouchers`

Object 82 is ExpenseTicket.

| Method | Path | Right | Body / query | Success |
|---|---|---|---|---|
| GET | `` | 82 Read | `status` (`draft`, `completed`, `cancelled`), `facility`, `cash_session`, `creator`, `from`, `to`, `skip`, `limit` | 200 `ListResponse[ExpenseVoucherSummary]`, newest first |
| POST | `` | 82 Create | `{comment?}` | 201 `ExpenseVoucherResponse` |
| GET | `/{id}` | 82 Read, facility | — | 200 |
| PUT | `/{id}` | 82 Update, creator | `{comment?}` | 200 |
| POST | `/{id}/lines` | 82 Update, creator | `{expense, amount, comment?}` | 200 |
| PUT | `/{id}/lines/{line_id}` | 82 Update, creator | `{expense?, amount?, comment?}` | 200 |
| DELETE | `/{id}/lines/{line_id}` | 82 Update, creator | — | 200 |
| POST | `/{id}/confirm` | 82 Update, creator | — | 200 |
| POST | `/{id}/cancel` | 82 Update, creator | — (drafts only) | 200 |
| POST | `/{id}/void` | 82 Delete, facility | `{reason}`, 1–500 characters | 200 |
| GET | `/{id}/ticket` | 82 Read, facility | — | 200 `application/pdf` |

**Rules:**
- **Creator:** a caller other than the voucher's creator gets 403 "Only the voucher's cashier can change it".
- **Session** (on create, confirm and void): with no open session, or a closed one, the answer is 409 "An open cash session is required" or "The cash session is closed".
- **409 refusals:**
  - a voucher that isn't a draft, for edits, confirm and cancel;
  - a total of 0 on confirm;
  - void of anything but a confirmed voucher;
  - ticket of anything but a confirmed voucher.
- **422:** an amount of 0 or less, or with more than 2 decimals; an unknown category.

**`ExpenseVoucherSummary`:**
- `expense_voucher_id`, `status`, `facility`, `cash_session`;
- `creator` and `creator_name`, `date`, `comment`;
- `total`, `line_count`.

**`ExpenseVoucherResponse`:** the summary plus `lines[]`, each `{expense_voucher_detail_id, expense, expense_name, amount, comment}`.

## Supplier payments: `/supplier-payments`

Object 21 is SupplierPayment.

| Method | Path | Right | Body / query | Success |
|---|---|---|---|---|
| GET | `` | 21 Read | `supplier`, `facility`, `method`, `status` (`completed`, `cancelled`), `from`, `to`, `skip`, `limit` | 200 `ListResponse[SupplierPaymentSummary]`, newest first |
| POST | `` | 21 Create | `{supplier, amount, method, date?, reference?, comment?, facility?, applications?: [{purchase_order, amount}]}` | 201 `SupplierPaymentResponse` |
| GET | `/{id}` | 21 Read | — | 200 |
| POST | `/{id}/applications` | 21 Update | `{applications: [{purchase_order, amount}]}`, 1–50 items, POs distinct | 200 |
| POST | `/{id}/applications/{application_id}/reverse` | 21 Delete | `{reason}` | 200 |
| POST | `/{id}/cancel` | 21 Delete | `{reason}` | 200 |
| GET | `/suppliers/{supplier_id}/balance` | 21 Read | — | 200 `SupplierBalance` |

On the PO router:

| Method | Path | Right | Success |
|---|---|---|---|
| GET | `/purchase-orders/{id}/payments` | 21 Read | 200 `PurchaseOrderBalance` |

**Rules:**
- **Cash payments:**
  - need the caller's open session (409 without one);
  - their `date` is set to now, and a body `date` is refused with 422;
  - can be cancelled only while their session is open (409 "The cash session is closed").
- **`facility` in the body:** only an administrator without a facility sends it. Anyone else gets 422 (research R5).
- **Each application is refused with 409 when:**
  - the PO isn't confirmed, or is cancelled;
  - it's another supplier's PO;
  - the PO was confirmed before the cutoff, or the cutoff is not set;
  - the amount exceeds the PO's balance or the payment's credit;
  - the payment is cancelled or legacy.

  The whole call is refused if any application is.
- **Reversing** an application that is already reversed answers 409.
- **Cancelling** a payment that is already cancelled answers 409.
- **422:** an amount of 0 or less, or with more than 2 decimals; a `date` in the future; an unknown method.
- **404:** an unknown supplier, PO, payment or application.

**`SupplierPaymentSummary`:**
- `supplier_payment_id`, `facility`, `serial`, `status`;
- `supplier` and `supplier_name`, `amount`, `method`, `date`, `reference`, `comment`;
- `cash_session`, `creator`;
- `applied`, `credit`.

**`SupplierPaymentResponse`:** the summary plus `applications[]`, each `{purchase_order_payment_id, purchase_order, amount, applier, date, cancelled}`.

**`PurchaseOrderBalance`:**
- `purchase_order_id`, `supplier`, `status`, `counts`;
- `total`, `applied`, and `balance` (null when the PO does not count);
- `applications[]`, the same shape, each with its `supplier_payment` and `serial`.

**`SupplierBalance`:**
- `supplier`, `supplier_name`, `cutoff`;
- `owed`, `credit`, `net`;
- `orders[]`: `{purchase_order_id, confirmed, total, applied, balance}`, oldest first;
- `payments[]`: `{supplier_payment_id, facility, serial, date, amount, credit}`, oldest first.

## Cash sessions (changed)

- **The cut ticket** shows "Pagos a proveedores" when the session has cash supplier payments.
- **The figures** gain `supplier_payments_total`, and `cash_in_drawer` / `expected_cash` subtract it.
- **`POST /cash-sessions/{id}/close`** is otherwise unchanged. It now waits for an in-flight voucher or cash payment on the session, and a second close still answers 409.
