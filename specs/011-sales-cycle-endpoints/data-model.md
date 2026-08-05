# Phase 1 Data Model: Sales Cycle Endpoints

**No new tables and no column changes.** Every entity below is already mapped. One migration
(`007_document_serial_unique.sql`) adds a unique index on `(facility, serial)` to the three
document tables and corrects the legacy rows blocking it — see research R1. This document records
which columns this feature writes, what is derived rather than stored, and the state machines the
lifecycle rules impose.

**Folio invariant**: `serial` is `NULL` on a draft and unique per facility once assigned. A
`serial` of `0` is not a folio — it was the legacy application's placeholder for "not numbered" and
migration 007 clears it to `NULL`.

Column names are the repository's, not the source document's — see the spec's Divergences table.

---

## Entities

### SalesQuote / SalesQuoteDetail — `app/models/sales.py`

A priced, expiring offer.

| Concern | Detail |
|---|---|
| Written on create | `facility`, `date`, `salesperson`, `customer`, `payment_terms`, `due_date`, `currency`, `exchange_rate`, `creator`, `updater`, timestamps; `completed=0`, `cancelled=0`, `serial=NULL` |
| Written on confirm | `serial` (folio), `completed=1` |
| Never written | — |
| Line columns | `product`, `quantity`, `price`, `price_adjustment`, `discount_rate`, `tax_rate`, `tax_included`, `currency`, `exchange_rate`, `comment`, plus the `product_code` / `product_name` snapshot |

`serial` stays `NULL` until confirmation (FR-032), so an abandoned draft leaves no gap.

### SalesOrder / SalesOrderDetail — `app/models/sales.py`

The committed sale; the spine every other entity hangs off.

| Concern | Detail |
|---|---|
| Written on create | Quote's set plus `point_sale`, `promise_date`, `priority`, optional `sales_quote` origin, optional `recipient` / `recipient_name` / `recipient_address`; `completed=0`, `cancelled=0`, `paid=0`, `delivered=0` |
| Written on confirm | `serial`, `completed=1` |
| Written on cancel | `cancelled=1` |
| Written by payment | `paid` (set and cleared — FR-044, FR-045) |
| Not written by this feature | `balance_zeroed_time` (FR-065a — supervisor action, not the refund path), `delivered`, `partial_deliveries` (logistics) |
| Line columns | Quote's set plus `cost`, `warehouse`, `delivery` |

`recipient` is a 13-character RFC string, **not** a foreign key — see Divergences.

### CustomerPayment — `app/models/sales.py`

Money received. Exists independently of any order.

| Concern | Detail |
|---|---|
| Written on create | `customer`, `amount`, `currency`, `method`, `payment_charge`, `reference`, `date`, `facility`, `cash_session`, `payment_type`, `serial`, `creator`, `updater`, timestamps |
| Written on verify | `verifier` (FR-071) |
| Derived, never stored | **Unapplied amount** = `amount` − Σ non-cancelled applications |

### SalesOrderPayment — `app/models/sales.py`

One application of one payment to one order. **Never deleted** (FR-045).

| Concern | Detail |
|---|---|
| Written on apply | `sales_order`, `customer_payment`, `amount`, `amount_change`, `applier`, `date`, `cancelled=0` |
| Written on reverse | `cancelled=1` only |
| Evidence | Lives in `incidence`, because no canceller/timestamp column exists — see below |

`amount_change` carries change given back (FR-042a) and does **not** reduce the payment's unapplied
amount available to other orders.

### CustomerRefund / CustomerRefundDetail — `app/models/sales.py`

Goods returned against a **paid** order.

| Concern | Detail |
|---|---|
| Written on create | `sales_order`, `customer`, `sales_person`, `facility`, `currency`, `exchange_rate`, `creator`, `updater`, timestamps; `completed=0`, `cancelled=0`, `serial=NULL`, `date=NULL` |
| Written on confirm | `serial`, `date`, `completed=1` |
| Line columns | `sales_order_detail` (the line being reversed), `product`, `quantity`, `price`, `discount`, `tax_rate`, `tax_included`, `currency`, `warehouse`, product snapshot |

Note the line-level divergence: the refund line's discount column is `discount`, while order and
quote lines use `discount_rate`.

### CreditNote — `app/models/sales.py`

Value owed back, when a refund is settled as store credit rather than cash.

| Concern | Detail |
|---|---|
| Written on issue | `sales_order`, `customer_refund`, `customer_payment` (the backing payment), `customer`, `refunded` (amount issued), `cash_session`, `date` |
| Derived, never stored | **Remaining balance** = `refunded` − Σ non-cancelled applications of `customer_payment` (FR-070) |

`refunded` is the amount *issued* and is never decremented — redemption is visible only through the
backing payment's applications (FR-070a).

### CashSession / CashCount — `app/models/core.py`

A cashier's shift on a drawer.

| Concern | Detail |
|---|---|
| Written on open | `start`, `cashier`, `cash_drawer` |
| Written on close | `end`, `cash_supervisor`; one `cash_count` row per denomination (`denomination`, `quantity`, `type`) |
| Derived | Open = `end IS NULL`; **stale** = open and `start` is before today (FR-053); closed = `end IS NOT NULL`. The same three serve as the list's `status` facet (FR-051b) |
| Expanded in responses | `cash_drawer`, `cashier` and `cash_supervisor`, batched two queries per page rather than one lookup per row (FR-051a) |

The opening cash amount is recorded as a `cash_count` row rather than a column — `cash_session` has
no amount column. `CashCountType` in `docs/constants.md` distinguishes opening from closing counts.

`cash_session` carries no facility either; the drawer is what holds one, so the list's `facility`
filter resolves through `cash_drawer.facility` (FR-051c).

### Incidence — `app/models/incidence.py`

The audit record that makes a reversal evidenced rather than silent (FR-045a).

| Column | Use |
|---|---|
| `source` | `SourceType.CustomerPayment` (2) |
| `instance_id` | the `customer_payment_id` |
| `updater` | the employee responsible |
| `modification_time` | when |
| `content` / `comment` | which application, and the **required** reason |

There is no `SourceType` value for an application, so the entry keys to the payment and names the
application in its content — a known limitation accepted in clarification rather than adding a
schema value.

### LotSerialTracking — `app/models/inventory.py`

Append-only stock ledger. Rows are **never** edited or deleted; a reversal is its own row.

| Column | Use |
|---|---|
| `source` | `TransactionType` — `SalesOrder` (1) or `CustomerRefund` (2) |
| `reference` | the document id |
| `quantity` | negative outbound (order confirm), positive inbound (refund confirm, order cancel) |
| `warehouse`, `product`, `date` | as written |
| `lot_number`, `serial_number`, `expiration_date` | left `NULL` — out of scope (spec Out of Scope) |

---

## Derived values

Nothing below is stored. One helper computes each so the three document types cannot diverge (R5).

| Value | Definition |
|---|---|
| Line subtotal | `quantity × price × (1 − discount)`; when `tax_included`, back-derived as `÷ (1 + tax_rate)` |
| Line tax | `subtotal × tax_rate` |
| Document subtotal / tax / total | Sum of line figures, quantized to 2 decimals **once** at document level |
| Order balance | `total` − Σ non-cancelled `sales_order_payment.amount` |
| Payment unapplied | `amount` − Σ non-cancelled applications |
| Credit note remaining | `refunded` − Σ non-cancelled applications of the backing payment |
| Line refundable quantity | `sales_order_detail.quantity` − Σ quantity on completed, uncancelled refund lines for that line |
| Product on-hand | `SUM(lot_serial_tracking.quantity)` by product + warehouse |

---

## State machines

### Sales order

```
                    ┌──────────── cancel ───────────┐
                    │        (no live payments)     │
                    ▼                               │
  [draft] ──confirm──▶ [completed] ──────────────▶ [cancelled]
  editable            folio assigned                terminal
  serial NULL         stock decremented             stock restored
                          │
                          │ apply payments (fully covered)
                          ▼
                     [completed + paid] ──refund──▶ goods back,
                     NOT cancellable                money returned
                                                    (stays paid)
```

Rules encoded:

- **Pay** requires `completed=1 AND cancelled=0` (FR-042) ⟹ a paid order is necessarily completed
  and uncancelled, so refund needs no separate cancellation check.
- **Cancel** requires `cancelled=0 AND paid=0 AND` no non-cancelled application (FR-019, FR-019b).
  A partly-paid order is cancellable only after its applications are reversed.
- **Refund** requires `completed=1 AND paid=1` (FR-060). Never alters `paid` (FR-064).
- Cancel and refund are **mutually exclusive** routes — SC-010.

### Sales quote

```
  [draft] ──confirm──▶ [completed] ──convert──▶ new sales order (draft)
     │                  folio assigned            blocked if expired
     └──cancel──▶ [cancelled]
```

Conversion requires `completed=1 AND cancelled=0 AND due_date >= today` (FR-034). Conversion is
permitted more than once (spec Assumption 11).

### Customer refund

```
  [draft] ──confirm──▶ [completed]   requires: source order paid,
     │                                         open cash session,
     │                                         quantities re-validated
     └──cancel──▶ [cancelled]        (only while not completed — FR-066)
```

### Payment application

```
  [applied] ──reverse (reason required)──▶ [cancelled, still visible]
```

Never deleted. Reversal clears the order's `paid` flag when remaining applications no longer cover
the total, returns the amount to the payment's unapplied total, and writes an incidence entry.

### Cash session

```
  [open] ──close (denomination counts)──▶ [closed]
     │
     └─ stale when start < today (reported, not auto-closed)
```

One open session per drawer, and one per cashier (FR-050).

---

## Validation rules

| Rule | Requirement | Enforced |
|---|---|---|
| Quantity ≥ product minimum | FR-013 | Line add/update |
| Price within `[low_profit, high_profit]` | FR-014 | Line add/update, unless privilege 102 |
| No line priced zero | FR-017 | Order confirm |
| Warehouse set and stocked | FR-018 | Order confirm, aggregated per product |
| Credit terms allowed | FR-016 | Order create/update |
| Application ≤ unapplied | FR-042 | Payment apply |
| Application currency = order currency | FR-043 | Payment apply |
| Return quantity ≤ refundable | FR-062, FR-063 | Refund line edit **and** re-checked at confirm under lock |
| Reversal reason present | FR-045a | Payment reverse |
| Confirmed document immutable | FR-011 | All updates, except order `priority` |

---

## Referential integrity

Documents are never deleted (FR-006), so the delete guards in `app/services/references.py` are not
extended by this feature. Line removal is permitted only while the parent is editable and is a
genuine row delete, since a draft line has no history worth keeping.

The `IntegrityError` handler in `app/main.py` remains the backstop for anything that slips through,
returning 409 rather than 500.
