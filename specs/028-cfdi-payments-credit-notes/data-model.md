# Data Model: CFDI Payment Receipts, Credit Notes and Email

One migration (research R1). Everything else uses existing tables, mapped in `app/models/fiscal.py`, `app/models/sales.py` and `app/models/core.py`.

## Migration 025 (confirm before running on mbe_dev)

```sql
ALTER TABLE fiscal_document
  ADD COLUMN customer_payment INT NULL,
  ADD KEY fiscal_document_customer_payment_idx (customer_payment),
  ADD CONSTRAINT fiscal_document_customer_payment_fk
    FOREIGN KEY (customer_payment) REFERENCES customer_payment (customer_payment_id);
```

The rollback drops the constraint, the key and the column. In the model, `FiscalDocument` gains `customer_payment: int | None`, plus the existing columns `payment_date: date | None` and `payment_amount: Decimal | None` (`decimal(18,2)`), which are not mapped yet.

## Payment receipt (`fiscal_document`, type 200)

| Field | Value |
|---|---|
| `type` | 200 |
| `batch` | an issuer batch of type 200 |
| `customer`, `recipient*`, `facility`, `issued_at`, `issued_location`, `issuer*` | as for invoices (spec 027, R7) |
| `payment_terms` | IMMEDIATE |
| `usage` | `CP01` |
| `payment_method` | SAT payment form 01–31, never 99 |
| `payment_date` | date, ≤ today, ≥ every related invoice's issue date |
| `payment_amount` | > 0; at confirm, = Σ relation amounts |
| `payment_reference` | ≤ 50, SAT `NumOperacion` |
| `reference` | ordering bank (`NomBancoOrdExt`), ≤ 25 |
| `customer_payment` | the payment it was created from, or null; at most one live receipt per payment |
| `currency`, `exchange_rate` | MXN, 1 |

**Relations** (`fiscal_document_relation`), one per paid invoice:

| Field | Value |
|---|---|
| `relation` | a stamped, non-cancelled, PPD invoice (type 0) of the same issuer and recipient |
| `amount` | > 0, ≤ previous balance |
| `installment`, `previous_balance` | computed (R2); stored at confirm |
| `exchange_rate` | 1 |
| `type` | null (no CFDI relation type) |

An invoice appears at most once per receipt (existing unique index `document, relation`).

## Fiscal credit note (`fiscal_document`, type 100)

| Field | Value |
|---|---|
| `type` | 100 |
| `batch` | an issuer batch of type 100 |
| Header | copied from the one related invoice: issuer, customer, recipient, store, `payment_method` (billing picks when the invoice's is 99), `usage` |
| `payment_terms` | IMMEDIATE (PUE) |

- **Relation:** exactly one, type `'1'` (SAT 01), to a stamped, non-cancelled invoice. Created with the credit note; it cannot be removed or changed.
- **Lines:** spec 027's `fiscal_document_detail`.
  - Copied from the invoice: quantity ≤ the invoice line's.
  - Added by hand: privilege 56.
- **Cap at confirm:** the credit note's total ≤ the invoice's total, less its other live credit notes' totals.

## Derived values

| Value | Source |
|---|---|
| Invoice total | `Total` of the invoice's stored XML |
| Live document | not cancelled, and completed or sealed (spec 027, R1) |
| Invoice balance | total − Σ live receipts' relation amounts − Σ live credit notes' totals |
| Refundable quantity of an order line | quantity − completed refunds − units on live invoices (R5) |

## States

The same as spec 027 for both types: draft → (confirm) sealed → (stamp, #46) issued. Sealed → (reopen) draft. Reopening a receipt frees its amounts for other receipts. Reopening a credit note frees its total.

## Incidence (email record)

`incidence` row with:
- `source = 13` (`SourceType.FISCAL_DOCUMENT`, API-only);
- `instance_id` = the document;
- `reason = 'CFDI emailed'`;
- the context listing the recipients, as spec 024 does.
