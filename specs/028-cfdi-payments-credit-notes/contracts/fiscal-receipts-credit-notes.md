# Contract: Payment Receipts, Credit Notes, Refund Rule and Email

All routes are under `/api/v1` and require a bearer token: 401 without one, 403 without the privilege named. Every mutation returns spec 020's `FiscalDocumentResponse`, which gains the receipt fields below. A document that is not a draft gets 409 "Fiscal document is not a draft".

## Payment receipts (#53)

| Method | Route | Privilege | Notes |
|---|---|---|---|
| POST | `/fiscal-documents/payment-receipts` | Create (23) | 201. Body `PaymentReceiptCreate`. With `customer_payment`, it copies the payment and proposes relations (R7) |
| PUT | `/fiscal-documents/{id}` | Update (23) | spec 027's header route. On a receipt it accepts `payment_date`, `payment_amount`, `payment_method`, `payment_reference`, `ordering_bank` (stored in `reference`, so `reference` itself is refused), `issued`, `comment` |
| POST | `/fiscal-documents/{id}/paid-invoices` | Update (23) | Body `{invoice: int, amount?: Decimal}`. The amount defaults to `min(balance, unassigned)` |
| PUT | `/fiscal-documents/{id}/paid-invoices/{relation_id}` | Update (23) | Body `{amount: Decimal}` |
| DELETE | `/fiscal-documents/{id}/paid-invoices/{relation_id}` | Update (23) | |
| POST | `/fiscal-documents/{id}/confirm` | Update (23) | spec 027's route. Seals the receipt (Pagos 2.0) |
| POST | `/fiscal-documents/{id}/reopen` | Update (23) | spec 027's route |

### Bodies

```text
PaymentReceiptCreate
  customer: int
  recipient: str (12..13)
  issuer: str | null
  batch: str | null
  customer_payment: int | null
```

### Additions to `FiscalDocumentResponse`

```text
payment_date: date | null
payment_amount: Decimal | null
payment_reference: str | null
customer_payment: int | null
paid_invoices: [
  {fiscal_document_relation_id, invoice, batch, serial, stamp_uuid,
   installment, previous_balance, amount, remaining}
]                                  # receipts only; computed live on drafts
```

## Credit notes (#55)

| Method | Route | Privilege | Notes |
|---|---|---|---|
| POST | `/fiscal-documents/credit-notes` | Create (23) | 201. Body `{invoice: int}`, a stamped, non-cancelled invoice. Relates it with type 01 |
| POST | `/fiscal-documents/{id}/invoice-lines` | Update (23) | Body `{line: int, quantity?: Decimal}`. Copies a line of the related invoice; the quantity defaults to the invoice line's |
| POST | `/fiscal-documents/{id}/lines` | Create (56) | spec 027's manual product line |
| PUT, DELETE | `/fiscal-documents/{id}/lines/{line_id}` | Update (23) | spec 027. For a copied line, the quantity must be ≤ the invoice line's |
| PUT | `/fiscal-documents/{id}` | Update (23) | `payment_method`, `usage`, `issued`, `reference`, `comment` |
| POST | `/{id}/confirm`, `/{id}/reopen` | Update (23) | Seals an egreso (E) with `CfdiRelacionados` 01 |

## Refund rule (#55)

There is no new route. Spec 011's refund routes now refuse a quantity on a live invoice:

| Route | Change |
|---|---|
| `POST /customer-refunds` (open from an order) | Lines whose units are all on live invoices are not offered. Their `refundable_quantity` excludes invoiced units |
| `PUT /customer-refunds/{id}/lines/{line_id}` | A quantity above the refundable quantity, because of a live invoice, gives 409 `Units of line {code} are on invoice {series}{folio}; cancel the invoice to refund them` |
| `POST /customer-refunds/{id}/confirm` | Same 409, re-checked at confirm; never trimmed silently |

## Email (#50)

| Method | Route | Privilege | Notes |
|---|---|---|---|
| POST | `/fiscal-documents/{id}/email` | Read (23) | Body `{to: [1..10 email addresses]}`. Returns `{recipients, sent_at}` |

| Case | Status | Detail |
|---|---|---|
| Not stamped, cancelled, or not 4.0 | 409 | `Only a stamped CFDI 4.0 can be emailed` |
| Mail not configured | 503 | `Email is not configured` |
| Server refused | 502 | `The email could not be sent: …` |

## Errors (422 unless noted)

| Case | Detail |
|---|---|
| Not a PPD invoice, or not stamped/live, or another issuer or recipient | `The invoice cannot be paid by this receipt` |
| Amount above the balance | `Amount exceeds the invoice balance of {balance}` |
| Amounts above the payment amount | `The amounts paid exceed the payment amount` |
| Confirm with amounts ≠ payment amount | `The amounts paid must equal the payment amount` |
| Payment date in the future or before an invoice | `The payment date must be between the invoices' dates and today` |
| Payment form 99 on a receipt | `A payment receipt needs the actual payment form` |
| The customer payment already has a live receipt | 409 `This payment already has receipt {series}{folio}` |
| No series of the type | `The issuer has no payment receipt series` / `The issuer has no credit note series` |
| Credit note above the invoice's remaining total | `The credit note exceeds what remains of invoice {series}{folio}: {amount}` |
| Copied line quantity above the invoice line's | `Quantity exceeds the invoice line's {quantity}` |
| Wrong route for the type | 409 `Not available for this document type` |
