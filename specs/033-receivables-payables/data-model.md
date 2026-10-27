# Data Model: Accounts Receivable and Accounts Payable

There's no schema change (research R12). Everything below is computed from existing tables, or is an enum value.

## Enum

- `SourceType.SUPPLIER = 16`: incidences about a supplier. The first use is a statement sent by email. Customers already have `SourceType.CUSTOMER = 7`.

## Computed: document balance (one per side)

### Customer: `OrderBalance` (from `sales_order_service.order_balances`)

| Field | Rule |
|---|---|
| `total` | `totals.document_totals(lines).total`, in the order's currency |
| `refunded` | Completed, non-cancelled refunds (`refunded_by_order`, unchanged) |
| `credited` | The order's share of live fiscal credit notes against its invoices (R2) |
| `applied` | Sum of non-cancelled `sales_order_payment.amount` |
| `balance` | `totals.remaining(total, [refunded, credited, applied])`: floored at 0, rounded to cents |

### Supplier: spec 032's PO balance (unchanged)

| Field | Rule |
|---|---|
| `total` | `order_totals(...)[2]`, total with tax |
| `returned` | Always 0: supplier returns aren't modelled |
| `applied` | `po_applied`: sum of non-cancelled `purchase_order_payment.amount` |
| `balance` | `total − returned − applied`, floored at 0 |

## Computed: `OpenDocument` (shared shape, both sides)

| Field | Customer | Supplier |
|---|---|---|
| `id` | `sales_order_id` | `purchase_order_id` |
| `serial` | `sales_order.serial` | none (POs have no serial; the id is shown) |
| `date` | `sales_order.date` | PO confirmation (`modification_time`) |
| `due_date` | `sales_order.due_date` | First confirmed receipt + `credit_days`, or none (R6) |
| `days_past_due` | `max(0, today − due_date)`, or 0 | Same |
| `bucket` | `current`, `1-30`, `31-60`, `61-90` or `90+` | Same |
| `facility` | `sales_order.facility` | none |
| `linked` | Live invoices: batch + serial | Confirmed receipts: facility + serial |
| `salesperson` | `sales_order.salesperson` | none |
| `currency`, `exchange_rate` | The order's | MXN, 1 |
| `total`, `deductions`, `applied`, `balance` | `total`, `refunded + credited`, `applied`, `balance` | `total`, `returned`, `applied`, `balance` |

**Open** means:
- **Customer:** NET_D, completed, not cancelled, not flagged paid, balance > 0.01.
- **Supplier:** counting under the cutoff, balance > 0.01.

## Computed: `PartySummary` (shared shape)

| Field | Rule |
|---|---|
| `party`, `name` | `customer_id` / `supplier_id`, and the name |
| `owed` | Σ open `balance × exchange_rate`, in scope |
| `overdue` | Σ of the same for documents with `days_past_due > 0` |
| `buckets` | `current`, `1-30`, `31-60`, `61-90`, `90+`; they sum to `owed` |
| `oldest` | Earliest open document `date` in scope |
| `credit` | Customer: Σ positive unapplied amounts, `amount − Σ(applied + change)` over live applications (R14). Supplier: spec 032 credit |
| `net` | `owed − credit` |
| `credit_limit`, `credit_days` | From the party |
| `owed_company` | `owed` across every facility (the customer side can differ from `owed` when the caller is scoped) |
| `utilization` | `owed_company / credit_limit` when the limit is > 0, else null |
| `available_credit` | `credit_limit − owed_company` when the limit is > 0, else null; below 0 means over the limit |
| `sales_agent` | Customer only: `customer.salesperson` |

## State: `sales_order.paid` and `balance_zeroed_time`

```text
                 settle: balance ≤ 0.01 after a lowering event
   unpaid  ─────────────────────────────────────────────▶  paid (zero time stamped once)
     ▲                                                        │
     └──────── reopen: before ≤ 0.01 < after (raising event) ─┘
```

- **Lowering events:** an application; the completion of a refund; the confirmation of a credit note.
- **Raising events:** the reversal of an application; the cancellation of a credit note (#51, later).
- A paid order whose balance was already above 0.01 never reopens. These are the legacy paid orders.
- **The one-time job** (R11) moves stale unpaid orders, those with a balance of 0.01 or less, to paid.
  - Orders dated 2026 or later get a zero time.
  - Earlier orders don't.
  - Each change gets an incidence (source 3).

## Validation (applications, FR-021)

| Refusal | Status | Where |
|---|---|---|
| Payment belongs to another party | 422 | existing `assert_same_customer`; spec 032 |
| Different currency | 422 | existing `assert_same_currency` |
| `amount + amount_change` above the payment's unapplied amount (which now subtracts change) | 422 | `assert_within_unapplied`, **changed** to include change; spec 032 |
| Above the order's balance | 422 | **new** on the customer side; spec 032 on the supplier side |
| Order flagged paid or balance ≤ 0.01 | 409 | **new** on the customer side |
| Lock contention or deadlock | 409 | `busy_as_conflict` |
