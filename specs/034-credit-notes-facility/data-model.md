# Data Model: Credit Note Balances and the Facility Rule

No schema change (research R9). Everything below is read from existing columns.

## Credit note (a view over its backing payment)

| Field | Source | Rule |
|---|---|---|
| `credit_note_id`, `customer`, `sales_order`, `customer_refund`, `customer_payment`, `date` | `credit_note` | Unchanged |
| `amount` | `customer_payment.amount` | The amount issued (FR-001) |
| `refunded` | `credit_note.refunded` | Amount handed back in cash. 0 unless paid back (R8) |
| `cash_session` | `credit_note.cash_session` | Set only on a note paid back in cash. NULL on every note issued from now on (FR-005) |
| `refunded_to_customer` | `cash_session IS NOT NULL` | The current system's `IsRefundedToCustomer` (FR-003) |
| `remaining` | see below | FR-002 |
| `facility` | `customer_payment.facility` | FR-006 |

**Remaining:**

```text
paid back in cash          → 0
otherwise                  → max(0, payment.amount − Σ(amount + amount_change) over live applications)
```

**Open** (FR-004): `remaining > 0.01`, not paid back in cash, and `customer ≠ default_customer_id`.

## Writing a credit note (FR-005)

| Column | Before | After |
|---|---|---|
| `credit_note.refunded` | the refund total | `0` |
| `credit_note.cash_session` | the cashier's session | `NULL`, written explicitly (the column defaults to 0) |
| `customer_payment` (type 4, method NA, the session, the facility, the amount) | unchanged | unchanged |

## Facility rule

| Path | Record's facility | Cross-facility privilege (read) | Another facility's record | Naming another facility |
|---|---|---|---|---|
| Customer payment apply, reverse | `customer_payment.facility` | PaymentsEditor (100) | 404 | — |
| Customer payment verify, reject | — | company-wide for PaymentsVerification (108) update | proceeds | — |
| `GET /customer-payments/unverified` | — | company-wide for PaymentsVerification (108) read | — | narrows |
| `GET /credit-notes`, `/{id}` | `customer_payment.facility` | PaymentsEditor (100) | 404 | 403 |
| `GET /sales-orders` | `sales_order.facility` | SearchCreditsFromAllStores (101) | — | 403 |
| Sales order `PUT`, lines, `confirm`, `cancel` | `sales_order.facility` | SearchCreditsFromAllStores (101) | 404 | — |
| Sales order `GET`, ticket, document, payments | — | open by id | proceeds | — |

Administrators pass every check (FR-014).
