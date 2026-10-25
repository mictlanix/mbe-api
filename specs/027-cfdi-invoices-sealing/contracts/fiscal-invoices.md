# Contract: Batches and Invoices

All routes are under `/api/v1` and require a bearer token. `401` without one. `403` without the privilege named. Administrators bypass privilege checks. Error bodies are `{"detail": "..."}`.

## Batches (#44)

Privilege: Taxpayers (24), with the right listed.

| Method | Route | Right | Success | Errors |
|---|---|---|---|---|
| GET | `/taxpayer-issuers/{rfc}/batches` | Read | 200 `TaxpayerBatchResponse[]`, by series | 404 issuer |
| POST | `/taxpayer-issuers/{rfc}/batches` | Create | 201 `TaxpayerBatchResponse` | 404 issuer; 409 series exists; 422 validation |
| PUT | `/taxpayer-issuers/{rfc}/batches/{taxpayer_batch_id}` | Update | 200 `TaxpayerBatchResponse` | 404; 409 series or type changed while in use, or series exists; 422 |
| DELETE | `/taxpayer-issuers/{rfc}/batches/{taxpayer_batch_id}` | Delete | 204 | 404; 409 in use |

```text
TaxpayerBatchCreate / TaxpayerBatchUpdate
  batch: str (1..10, trimmed)
  type: FiscalDocumentType (0, 1, 2, 3, 100, 101, 200)
  template: BatchTemplate

BatchTemplate
  name: str            # e.g. "T02Blue"
  logo: str | null
  header_height: int = 0
  footer_height: int = 0
  extra_info: list[str] = []

TaxpayerBatchResponse = TaxpayerBatchCreate + taxpayer_batch_id, taxpayer, in_use: bool
```

## Invoices (#47)

The privilege is FiscalDocuments (23) unless stated otherwise. Every mutation returns the updated `FiscalDocumentResponse` (spec 020's schema, extended below). Every mutation on a document that is not a draft returns 409 "Fiscal document is not a draft".

| Method | Route | Right | Notes |
|---|---|---|---|
| POST | `/fiscal-documents` | Create (23) | 201. Body `InvoiceCreate`. The facility is the caller's (422 when none) |
| PUT | `/fiscal-documents/{id}` | Update (23); changing `facility` also needs Update on 103 | Body `InvoiceUpdate`, a partial header |
| POST | `/fiscal-documents/{id}/sales-orders` | Update (23) | Body `{sales_order: int}`. Adds the order's invoiceable lines (FR-005, FR-006) |
| POST | `/fiscal-documents/{id}/lines` | Create (56) | Body `{product: int}`. A manual line (FR-007) |
| PUT | `/fiscal-documents/{id}/lines/{line_id}` | Update (23) | Body `InvoiceLineUpdate`, partial (FR-011) |
| DELETE | `/fiscal-documents/{id}/lines/{line_id}` | Update (23) | |
| POST | `/fiscal-documents/{id}/relations` | Update (23) | Body `{relation: int, type: "04" \| "07"}` (FR-010) |
| DELETE | `/fiscal-documents/{id}/relations/{relation_id}` | Update (23) | |
| POST | `/fiscal-documents/{id}/confirm` | Update (23) | Assigns the serial, seals, and returns status `sealed` (FR-013, FR-014) |
| POST | `/fiscal-documents/{id}/reopen` | Update (23) | Sealed to draft; the serial is kept (FR-015) |

### Bodies

```text
InvoiceCreate
  customer: int                     # required
  recipient: str (12..13)           # required; must be linked to the customer
  issuer: str | null                # default: the facility's taxpayer
  batch: str | null                 # default: research R7

InvoiceUpdate (every field optional; only sent fields change)
  recipient: str
  batch: str                        # an Invoice batch of the same issuer; 409 once a serial exists
  usage: str (3)                    # sat_cfdi_usage id
  payment_terms: PaymentTerms       # NET_D forces payment_method 99
  payment_method: int               # SAT forma de pago
  issued: LocalDateTime             # now − 72 h < issued ≤ now
  facility: int                     # needs Update on 103
  reference: str (≤ 25) | null
  comment: str (≤ 1000) | null

InvoiceLineUpdate (every field optional)
  quantity: Decimal > 0             # ≤ remaining for an order line
  price: Decimal ≥ 0
  discount: Decimal 0..1
  tax_rate: Decimal in {0.16, 0, -1}
  product_code: str (≤ 35)
  product_name: str (≤ 1000)        # blank resets to the product's name
  comment: str (≤ 1000) | null
```

### Response changes (spec 020's schema)

- **`status`:** gains `sealed` in `FiscalDocumentSummary` and `FiscalDocumentResponse`. `GET /fiscal-documents?status=sealed` filters on it, and `status=draft` no longer includes sealed documents.
- **New fields on `FiscalDocumentResponse`:** `customer` and `facility`. The other header fields an editor needs are already there.
- **`relations`:** a new list of:

  ```text
  {fiscal_document_relation_id, relation, type: "04" | "07" | null, stamp_uuid, batch, serial}
  ```

  Legacy `'0'` types map to `null`.
- **Lines:** `FiscalDocumentLine` gains `product` and `order_detail`.

### Error texts (English, matching the API's other messages)

| Case | Status | Detail |
|---|---|---|
| Order not found | 404 | `Sales order not found` |
| Order not completed, or cancelled | 422 | `The sales order is not completed or is cancelled` |
| Nothing to add | 422 | `No invoiceable lines were found` |
| No invoice batch | 422 | `The issuer has no invoice series` |
| No price for a manual line | 422 | `The product has no price in the customer's price list` |
| Date out of range | 422 | `The issue date must be within the last 72 hours and not in the future` |
| Confirm on empty | 422 | `The invoice has no lines` |
| Quantity over remaining | 422 | `Line {n}: quantity exceeds the {remaining} still invoiceable` |
| Missing SAT keys | 422 | `Line {n}: missing SAT product key or unit` |
| Recipient incomplete | 422 | `The recipient has no tax regime or postal code` |
| No certificate | 422 | `The issuer has no active certificate valid at the issue date` |
| Bad relation | 422 | `The related document does not qualify for relation type {type}` |
| Mixed relation types | 422 | `All relations must share one type` |
| Not a draft | 409 | `Fiscal document is not a draft` |
| Reopen when not sealed | 409 | `Fiscal document is not sealed` |
