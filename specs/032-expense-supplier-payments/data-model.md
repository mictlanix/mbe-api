# Data Model: Expense Vouchers and Supplier Payments

Existing tables are used as they are unless marked **new** or **added**. Migration 029 holds every change (research R11).

## Expense voucher (`expense_voucher`, existing)

| Column | Use |
|---|---|
| `expense_voucher_id` | The voucher number, printed as 8 digits. |
| `facility` | The facility of its session's drawer. It is set on create and again on confirm. |
| `cash_session` | The caller's open session. It is set on create, and moved to the caller's then-open session on confirm. |
| `creator` | The caller who created it, who is its cashier. Only they may edit, confirm or cancel the draft. |
| `updater`, `modification_time` | The last writer. |
| `date` | Creation time, then confirmation time (research R3). |
| `creation_time` | Creation time. |
| `comment` | Up to 500 characters, optional. |
| `completed`, `cancelled` | The state (research R1). |

**State transitions:**
- `draft` → `completed`: confirm, by the creator, in their open session, with a total above 0.
- `draft` → `cancelled`: cancel, by the creator.
- `completed` → `cancelled`: void, with delete on 82, a reason, and the voucher's session still open.
- Nothing changes once the session is closed.

## Expense voucher line (`expense_voucher_detail`, existing)

| Column | Use |
|---|---|
| `expense` | A category from `expenses`, which must exist. |
| `amount` | Above 0, 2 decimals, at most 99,999,999.99. |
| `comment` | Up to 500 characters. It defaults to the category's `comment`. |

## Expense category (`expenses`, existing)

Read only here. It keeps its own routes (Expenses, 81).

## Supplier payment (`supplier_payment`, columns **added**)

| Column | Use |
|---|---|
| `supplier_payment_id` | Id. |
| `supplier` | FK supplier, required. |
| `amount` | Above 0, 2 decimals. |
| `method` | A SAT `PaymentMethod`. |
| `date` | Default now, not in the future; always now for cash. |
| `reference` | Up to 50 characters. |
| `comment` | Up to 500 characters. |
| `creator` | The caller. |
| **`facility`** | Added. The session's drawer's facility for cash, otherwise the caller's (research R5). NULL on the 5 legacy rows. |
| **`serial`** | Added. Per facility; `UNIQUE (facility, serial)`. NULL on legacy rows. |
| **`cash_session`** | Added. The caller's open session for a cash payment, NULL otherwise. |
| **`cancelled`** | Added. 0 or 1. |
| **`creation_time`, `updater`, `modification_time`** | Added. On legacy rows they are backfilled from `date` and `creator`. |

**Derived:**
- `status`: `cancelled` or `completed`.
- `applied`: the sum of live applications.
- `credit`: `amount − applied`; 0 when cancelled or legacy (`facility IS NULL`).

**State transitions:** `completed` → `cancelled`. It needs delete on 21 and a reason. A cash payment can be cancelled only while its session is open. Payments are never edited.

## Purchase order payment (`purchase_order_payment`, **new**)

An application of a payment to a PO.

| Column | Use |
|---|---|
| `purchase_order_payment_id` | Id. |
| `supplier_payment` | FK, indexed. |
| `purchase_order` | FK, indexed. The PO must be confirmed, not cancelled, of the payment's supplier, and on or after the cutoff. |
| `amount` | Above 0, 2 decimals, at most both the payment's credit and the PO's balance at that moment. |
| `applier` | FK employee: who applied it. |
| `date` | When it was applied. |
| `cancelled` | Set when it is reversed. A reversal is never deleted, and its incidence names who reversed it and why. |

## Balance cutoff (`supplier_balance_cutoff`, **new**)

| Column | Use |
|---|---|
| `supplier_balance_cutoff_id` | Always 1. |
| `cutoff` | NULL until the go-live migration sets it. A PO counts when its `modification_time >= cutoff` (research R6). |

## Derived views (not stored)

- **PO balance:**
  - `total` (`order_totals`, tax added);
  - `applied` (sum of live applications);
  - `balance = total − applied`;
  - `counts` (on or after the cutoff).

  A PO that doesn't count reports `balance` as null.
- **Supplier summary:**
  - `owed`: the sum of balances of counting POs;
  - `credit`: the sum of payment credit;
  - `net = owed − credit`;
  - `orders[]`: counting POs with a balance above 0, oldest first;
  - `payments[]`: payments with credit above 0, oldest first.
- **Cash cut** (`CashCutFigures`): adds `supplier_payments_total`, the session's non-cancelled cash supplier payments. `cash_in_drawer` subtracts it (research R8).

## Incidences

| Source | Instance | Written on |
|---|---|---|
| 14 `EXPENSE_VOUCHER` | voucher id | a void |
| 15 `SUPPLIER_PAYMENT` | payment id | an application reversal, and a payment cancel |
