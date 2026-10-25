# Fiscal Documents Specs

Covers Mexican CFDI 4.0 e-invoicing: taxpayer (issuer) configuration and fiscal document generation, stamping, and cancellation.

---

## Background: CFDI 4.0

CFDI (Comprobante Fiscal Digital por Internet) is Mexico's mandatory electronic invoicing standard governed by the SAT (Servicio de Administración Tributaria). Every issued invoice must be:

1. Generated from a registered RFC (taxpayer issuer)
2. Digitally signed with a CSD certificate
3. Stamped (timbrado) by an authorized PAC (Proveedor Autorizado de Certificación)
4. UUID-assigned by SAT via the `TimbreFiscalDigital` complement

Key SAT catalog tables: `sat_cfdi_usage`, `sat_tax_regime`, `sat_product_service`, `sat_unit_of_measurement`, `sat_currency`, `sat_reason_cancellation`, `sat_postal_code`.

---

## 1. Taxpayers (Issuers)

**Route**: `GET /taxpayers`  
**Controller**: `TaxpayersController`  
**SystemObject**: `Taxpayers` (24)

### Purpose
Configure the company's RFC(s) used to issue CFDI documents. Each facility is linked to a taxpayer issuer.

### List View
- Columns: RFC, name, tax regime, PAC provider, active certificate status

### Taxpayer Issuer Form (`taxpayer_issuer`)

| Field | Column | Notes |
|-------|--------|-------|
| RFC | `taxpayer_issuer_id` | 12–13 chars, PK |
| Legal Name | `name` | |
| Tax Regime | `regime` | FK → `sat_tax_regime` |
| PAC Provider | `provider` | Enum of configured PAC integrations (e.g. Finkok, Edicom) |
| SAT Postal Code | `postal_code` | FK → `sat_postal_code` |
| Notes | `comment` | |

### Certificate Sub-Panel (`taxpayer_certificate`)

| Field | Column | Notes |
|-------|--------|-------|
| Certificate Number | `taxpayer_certificate_id` | 20-char SAT cert number, PK |
| Certificate File | `certificate_data` | Upload .cer file (DER-encoded X.509) |
| Key File | `key_data` | Upload .key file (private key) |
| Key Password | `key_password` | Encrypted passphrase |
| Valid From | `valid_from` | Certificate validity start |
| Valid To | `valid_to` | Certificate validity end |
| Active | `active` | Only one active cert per RFC |

### Batch/Series Sub-Panel (`taxpayer_batch`)

| Field | Column | Notes |
|-------|--------|-------|
| Series | `batch` | Folio prefix (e.g. "A", "FAC", "E") |
| Document Type | `type` | CFDI type: `I`=Ingreso, `E`=Egreso, `P`=Pago, `T`=Traslado, `CreditNote`, `PaymentReceipt`, `AdvancePaymentsApplied` |
| XML Template | `template` | JSON blob describing print template name, logo, header/footer heights, extra info |

### Business Rules
- Only one certificate should be `active = 1` per RFC at a time.
- If no `TaxpayerBatch` exists for the issuer + document type, creating a fiscal document of that type fails.
- Certificates must be renewed before `valid_to`; alert 30 days prior.

### New MBE: batch maintenance (spec 027, #44)

Legacy only lists batches. The API maintains them under the issuer, gated on Taxpayers (24):

| Method | Route | Right |
|---|---|---|
| GET | `/api/v1/taxpayer-issuers/{rfc}/batches` | Read |
| POST | `/api/v1/taxpayer-issuers/{rfc}/batches` | Create |
| PUT | `/api/v1/taxpayer-issuers/{rfc}/batches/{id}` | Update |
| DELETE | `/api/v1/taxpayer-issuers/{rfc}/batches/{id}` | Delete |

- **Series:** 1–10 characters, unique per issuer (409).
- **Type:** one of the `FiscalDocumentType` codes: 0, 1, 2, 3, 100, 101, 200.
- **In use:** once any fiscal document of the issuer carries the series, its series and type cannot change and it cannot be deleted (409). Its template can still change. Each response says `in_use`.
- **Template:** the API exchanges it as `name`, `logo`, `header_height`, `footer_height` and `extra_info` (bank accounts: `bank`, `account`, `clabe`, `currency`). It stores legacy's keys (`Name`, `Logo`, `HeaderHeight`, `FooterHeight`, `ExtraInfo`) as JSON, which the PDF of spec 020 reads, as it does legacy's single-quoted text.

---

## 2. Fiscal Documents

**Route**: `GET /fiscal-documents`  
**Controller**: `FiscalDocumentsController`  
**SystemObject**: `FiscalDocuments` (23)

### Purpose
View, generate, stamp, download, and cancel CFDI electronic invoices. Documents are generated from sales orders or standalone.

### List View
- Search numeric: matches document ID, serial, or linked sales order ID
- Search text: matches issuer RFC, recipient RFC, recipient name, customer name
- Filter: excludes records with `!IsCompleted && IsCancelled` (draft-cancelled)
- Sort: open documents first (non-completed, non-cancelled), then by `Issued` date descending

### Create Flow
1. Select document type (`FiscalDocumentType`: `I`=Ingreso, `E`=Egreso, `P`=Pago, etc.)
2. Select issuer (from facility's taxpayer): system finds matching `TaxpayerBatch` for the type; error if none found
3. Select customer and recipient RFC (`TaxpayerRecipient`): auto-fills `TaxpayerRegime`, `TaxpayerPostalCode`
   - If recipient is the general public RFC (`TaxpayerGeneralReceiptId`): `TaxpayerPostalCode` = facility's location postal code
4. Document is created with `Batch` from the matched `TaxpayerBatch`

### Header Fields (`fiscal_document`)

| Field | Column | Notes |
|-------|--------|-------|
| Facility | `facility` | FK → `facility` |
| Issuer | `issuer` | FK → `taxpayer_issuer` — auto from facility |
| Issuer Regime | `issuer_regime` | Auto from taxpayer |
| Customer | `customer` | FK → `customer` |
| Recipient RFC | `recipient` | FK → `taxpayer_recipient` |
| Recipient Name | `recipient_name` | Auto-filled from recipient |
| Recipient Address | `recipient_address` | FK → `address` |
| Taxpayer Regime | `taxpayer_regime` | Recipient's SAT tax regime (CFDI 4.0 required) |
| Taxpayer Postal Code | `taxpayer_postal_code` | Recipient's postal code (CFDI 4.0 required) |
| Document Type | `type` | `I`=Ingreso, `E`=Egreso, `P`=Pago, `T`=Traslado |
| Folio Series | `batch` | From `taxpayer_batch` |
| Serial | `serial` | Auto-incremented |
| Version | `version` | CFDI version (e.g. `4.0`) |
| Issued Date | `issued` | Stamping timestamp |
| Issued At | `issued_at` | FK → `address` — facility's address |
| Payment Method | `payment_method` | SAT code: `PUE` (single) or `PPD` (installments) |
| Payment Terms | `payment_terms` | Matches sales order |
| Payment Reference | `payment_reference` | |
| CFDI Usage | `usage` | FK → `sat_cfdi_usage` (G01, G03, P01, etc.) |
| Currency | `currency` | |
| Exchange Rate | `exchange_rate` | |
| VAT Retention Rate | `retention_rate` | ISR retention rate |
| Local Tax Name | `local_retention_name` | Municipal/local tax label |
| Local Tax Rate | `local_retention_rate` | |
| Notes | `comment` | |
| Stamp UUID | `stamp_id` | SAT-assigned UUID after timbrado |
| Stamped Date | `stamped` | Timestamp from PAC response |
| Authority Digital Seal | `authority_digital_seal` | PAC certificate seal |
| Authority Certificate | `authority_certificate_number` | PAC certificate number |
| RFC PAC | `rfc_pac` | PAC's RFC |
| Cancellation Reason | `cancellation_reason` | FK → `sat_reason_cancellation` |
| Cancellation Date | `cancellation_date` | |
| Cancellation Substitution | `cancellation_substitution` | UUID of replacement document |

### Line Item Fields (`fiscal_document_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | FK → `product` |
| Source Order Line | `order_detail` | FK → `sales_order_detail` |
| SAT Product Key | `product_service` | FK → `sat_product_service` |
| Internal Code | `product_code` | |
| Description | `product_name` | |
| Unit of Measurement | `unit_of_measurement` | FK → `sat_unit_of_measurement` |
| Quantity | `quantity` | |
| Unit Price (pre-tax) | `price` | |
| Discount | `discount` | |
| Tax Rate | `tax_rate` | |
| Currency | `currency` | |
| Exchange Rate | `exchange_rate` | |
| Tax Included | `tax_included` | |
| Notes | `comment` | |

### Payment Complement Sub-Panel (`fiscal_document_relation`, for PPD invoices)

| Field | Column | Notes |
|-------|--------|-------|
| Related Invoice | `relation` | FK → `fiscal_document` |
| Installment Number | `installment` | Payment sequence number |
| Previous Balance | `previous_balance` | Balance before this payment |
| Amount Applied | `amount` | |
| Exchange Rate | `exchange_rate` | |
| Relation Type | `type` | SAT relation type code |

### Confirm Action (Stamp / Timbrar)
1. Builds CFDI XML using CFDv40 helper
2. Submits to PAC via `CFDHelpers40.StampCFD`
3. Extracts `TimbreFiscalDigital` complement from PAC response:
   - `entity.StampId = tfd.UUID`
   - `entity.Stamped = tfd.FechaTimbrado`
4. Saves XML to `FiscalDocumentXml` table
5. Sets `IsCompleted = true`

### Print Template Selection
Template is derived from batch configuration:
```
view = string.Format("Print{0:00}{1}", model.Version * 10, template.Name)
// e.g. for version 4.0 + template "Standard" → "Print40Standard"
```

### Cancel Action
1. Sets `CancellationDate = now`, `IsCancelled = true`
2. If `CancellationReason != null`: calls `CFDHelpers40.CancelCFD` (PAC API)
3. `CancelNoReason` skips the reason check for certain cancellation scenarios

### Actions
- **New** (from scratch): select type → create blank document
- **Generate from Sales Order**: populate all header and line fields from a `sales_order`; lines linked to `sales_order_detail` via `order_detail` FK
- **Stamp (Timbrar)** (`Confirm`): submit to PAC for UUID + digital seal
- **Download XML** (`Download`): streams `FiscalDocumentXml.Data` as `text/xml`
- **Download PDF** (`Pdf`): generates PDF using configured template
- **QR Code** (`QRCode`): generates QR code with: `issuer.RFC|recipient.RFC|total|UUID|...`
- **Send by Email** (`SendEmail`): sends XML + PDF as MIME attachments; uses `WebConfig.DefaultSender` and optional CC
- **Cancel**: PAC cancellation with reason; or `CancelNoReason`

### New MBE: invoices up to a sealed CFDI (spec 027, #47)

The new MBE builds Invoice (type 0) only. In the 24 months to 2026-10-25, mbe_dev issued 10,698 invoices and no fee receipts, rent receipts or debit notes. Credit notes, payment receipts and the payment complement are separate roadmap items.

**Stamping is pending** (mictlanix/mbe-roadmap#46). Confirm stops at a sealed, unstamped document and contacts no PAC.

**States.** These derive from existing columns, with no schema change:

| State | completed | cancelled | `fiscal_document_xml` |
|---|---|---|---|
| draft | 0 | 0 | none |
| sealed | 0 | 0 | the sealed XML |
| issued | 1 | 0 | the stamped XML |
| cancelled | – | 1 | – |

Legacy never stores XML for an uncompleted document, so a sealed invoice reads as a draft there. Spec 020's list and detail report `sealed`, and `GET /fiscal-documents?status=sealed` filters by it. The XML download and PDF still require an issued document.

**Routes** (`/api/v1/fiscal-documents`):

| Method | Route | Privilege |
|---|---|---|
| POST | `` | Create on 23 |
| PUT | `/{id}` | Update on 23; changing `facility` also needs Update on IssuedLocationId (103) |
| POST | `/{id}/sales-orders` | Update on 23 |
| POST | `/{id}/lines` | Create on StandaloneFiscalDocuments (56) |
| PUT, DELETE | `/{id}/lines/{line_id}` | Update on 23 |
| POST, DELETE | `/{id}/relations`, `/{id}/relations/{relation_id}` | Update on 23 |
| POST | `/{id}/confirm` | Update on 23 |
| POST | `/{id}/reopen` | Update on 23 |

Each returns the document.

**Create.**
- **Store:** the caller's store.
- **Issuer:** the store's taxpayer, unless given.
- **Series:** the store's `default_batch` when it is an Invoice series of the issuer, else the issuer's first Invoice series. 422 "The issuer has no invoice series" when there is none.
- **Recipient:** must be one of the customer's taxpayers. Its name, regime and postal code are copied; for XAXX010101000 the postal code is the store's.
- **Defaults:**
  - CFDI usage G01;
  - payment form 99;
  - PPD when the customer has credit days and a credit limit, else PUE;
  - MXN at rate 1.

**Lines from sales orders.**
- **Accepted orders:** completed and not cancelled. Payment is not checked, and another customer's order is accepted.
- **Which lines:** each line of an invoiceable product, added at its remaining quantity. Lines already on the draft are skipped, so adding an order twice duplicates nothing.
- **Remaining quantity:** the line's quantity minus:
  - what sealed or issued, non-cancelled invoices hold, other than this one. Drafts reserve nothing;
  - each completed refund, counted only for the units not yet invoiced when it was recorded.

  Events are taken in time order: invoices by issue date, refunds by date, and at equal times the refund first. Refunds cannot take units on a live invoice (spec 028). Legacy's `quantity − max(invoiced, refunded)` let goods refunded before invoicing be invoiced anyway.

**Manual lines.**
- Quantity 1, at the customer's price-list price.
- The product's tax rate, tax-included flag, SAT keys and unit.

**Editing a line.**
- Quantity > 0, and no more than the remaining quantity for an order line.
- Price ≥ 0.
- Discount 0–1.
- Tax rate 0.16, 0 or -1 (exempt).
- A blank name resets to the product's.

**Header.**
- Only the fields sent change.
- PPD forces payment form 99.
- The issue date must be within the last 72 hours and not in the future. It is checked when set and again at confirm. When it is empty, confirm uses the current time.
- Once an invoice has a serial, its series cannot change.

**Relations.**
- The related document must be stamped, with the same issuer and recipient:
  - 04 (substitution) relates a cancelled invoice (type < 100);
  - 07 (application of an advance) relates a non-cancelled document that is not a credit note or payment receipt.
- An invoice uses one relation type for all its relations.
- Stored as legacy does, `'4'` or `'7'`; read and written as `04` and `07`.

**Confirm.**
1. **Checks:**
   - the invoice has lines;
   - each line has its SAT keys;
   - quantities are within what remains;
   - the recipient has a regime and postal code;
   - the issue date is within 72 hours;
   - an active certificate is valid at the issue date. The latest to expire wins.
2. **Serial:** the issuer's series row is locked (`SELECT … FOR UPDATE`), then the highest serial is read with a locking read, plus 1. A plain read would not do: MariaDB's REPEATABLE READ snapshot can miss a serial the previous confirm committed, which is legacy's duplicate-serial race (mictlanix/mbe#45). A failed confirm rolls back and consumes no number.
3. **XML:** CFDI 4.0 with legacy's values and rounding (`CFDHelpers40.InvoiceToCFDv40`), half away from zero, per line:
   - `MetodoPago` follows the terms;
   - usage P01 is written as G03;
   - an exempt line carries an exempt transfer;
   - a fully discounted line is `ObjetoImp` 01;
   - no retentions, no `TipoCambio`, no `InformacionGlobal`.
4. **Original string:** SAT's stylesheet, from cfdlib's XSLT 1.0 copy in `app/resources/cfdi40/`.
5. **Seal:** SHA-256 with RSA under the CSD key, with `NoCertificado` and `Certificado` set.
6. The XML is stored and the invoice is left **sealed**.

**Reopen.** A sealed invoice can return to draft. The sealed XML is removed and the serial is kept, and the next confirm reuses that serial.

### New MBE: payment receipts, credit notes and email (spec 028, #53, #55, #50)

**Stamping and cancellation are still pending** (#46, #51). Receipts and credit notes stop at a sealed document, as invoices do. Spec 027's states, serial lock, 72-hour rule, certificate choice and reopen apply to both.

**Payment receipts** (type 200, Pagos 2.0):

| Route | Does |
|---|---|
| `POST /fiscal-documents/payment-receipts` | Creates a draft, by hand or from a customer payment. From a payment it copies the date, amount, form and reference, keeps the link (`fiscal_document.customer_payment`, migration 025) and proposes the PPD invoices holding lines of the orders that payment settled |
| `PUT /fiscal-documents/{id}` | Edits `payment_date`, `payment_amount`, `payment_method` (not 99), `payment_reference` (`NumOperacion`, ≤ 50), `ordering_bank` (`NomBancoOrdExt`, stored in `reference`), `issued` and `comment` |
| `POST`, `PUT`, `DELETE /fiscal-documents/{id}/paid-invoices…` | Adds, edits and removes the invoices the receipt pays |

- **Which invoices can be paid:** stamped, non-cancelled PPD invoices of the receipt's issuer and recipient.
- **Installment:** 1 plus the invoice's earlier live receipts.
- **Previous balance:** the invoice's stored total, less its live receipts' payments and its live credit notes' totals. Receipts from the current system count too.
- **Amount paid:** defaults to the smaller of that balance and the unassigned payment amount, and must not exceed the balance.
- **At confirm:**
  - the paid invoices are locked;
  - installments and balances are recomputed and stored;
  - the amounts paid must equal the payment amount.

  The relation rows keep `installment`, `previous_balance` and `amount`.
- **The XML** is the current system's zero-value receipt, with these corrections:
  - each paid invoice's taxes follow that invoice's own transfers (16 %, 0 %, exempt), scaled to the amount paid; the current system assumed 16 %;
  - an untaxed invoice is `ObjetoImpDR` 01;
  - `Totales` is always written, with one field per rate.

  A 16 %-only invoice gives the current system's exact values.
- A customer payment backs at most one receipt that is not cancelled.

**Credit notes** (type 100, egreso):

| Route | Does |
|---|---|
| `POST /fiscal-documents/credit-notes` | Creates a draft for one stamped invoice, related with type 01 (stored as `'1'`). It copies the invoice's parties, store, payment form and usage; terms are PUE |
| `POST /fiscal-documents/{id}/invoice-lines` | Copies an invoice line at its quantity or less |
| `POST /fiscal-documents/{id}/lines` | Adds a product by hand (privilege 56), for example a discount concept |

- **Confirm refuses:**
  - payment form 99;
  - a total above the invoice's total less its other live credit notes. The invoice row is locked while this is checked.
- **The XML:** spec 027's invoice XML, with `TipoDeComprobante` E, `MetodoPago` PUE and `CfdiRelacionados` 01.
- **Remaining quantity:** a credit note's copied lines keep their `order_detail`, but only invoices count toward an order line's invoiced quantity.

**Refunds of invoiced goods.** A refund cannot return units on a live invoice (sealed or stamped, not cancelled). Opening, editing or confirming such a refund is refused with 409, naming the invoice: "Units of line {code} are on invoice {series}{folio}; cancel the invoice to refund them". Refunds never create credit notes; billing issues them. A sealed, unstamped invoice can be reopened and the line removed to free its units.

**Email.** `POST /fiscal-documents/{id}/email` (Read on 23) sends a stamped CFDI 4.0 of any type to 1–10 addresses.
- **Message:** the current system's subject, `Comprobante Fiscal - {RFC} - {series}{folio:000000}`, and its Spanish body.
- **Attachments:** the PDF and the stored XML, named `{RFC}-{series}{folio:000000}`.
- **Recording:** each send the server accepts is recorded as an incidence (source 13).
- **Errors:** 409 for anything not stamped, 502 when the server refuses, 503 when mail is not configured.
- **QR:** spec 020's PDF already prints the SAT verification QR.

### Document Type Views
Different view templates per document type:
- `FiscalDocumentType.PaymentReceipt` → `ViewPayment`
- `FiscalDocumentType.CreditNote` or `AdvancePaymentsApplied` → `ViewOutcome`
- All others → default `View`

### Standalone Fiscal Documents

**SystemObject**: `StandaloneFiscalDocuments` (56)

Fiscal documents not generated from a sales order (e.g., service invoices, advance payment receipts). Use the same `FiscalDocument` model and workflow; the distinction is access control — this gate allows creating documents without a source SO.

### Taxpayer Recipients

**Route**: `GET /taxpayer-recipients`  
**Controller**: `TaxpayerRecipientsController`  
**SystemObject**: `TaxpayerRecipients` (54)

Catalog of customer RFC records used as CFDI recipients. See Master Data spec for full field details.

### Business Rules
- CFDI 4.0 mandates both `taxpayer_regime` and `taxpayer_postal_code` for the recipient.
- A document cannot be edited once `IsCompleted = true`.
- Cancellation within 24 hours of issuance can be done without a substitution document.
- Cancellation after 24 hours requires customer acceptance (SAT 2-step workflow).
- PPD invoices (payment in installments) require a Payment Complement (`type = P`) for each payment received.
- Only certificates with `active = 1` and `valid_to > now()` can be used for stamping.
- `version = "4.0"` for all new documents.
