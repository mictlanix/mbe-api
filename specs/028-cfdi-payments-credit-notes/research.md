# Research: CFDI Payment Receipts, Credit Notes and Email

Measured on mbe_dev (read only) on 2026-10-25. The current system is `mictlanix/mbe`:
- `Web/Controllers/Mvc/FiscalDocumentsController.cs` (FDC);
- `Web/Helpers/CFDHelpers40.cs` (CFD40);
- `Model/FiscalDocument.cs` and `Model/FiscalDocumentRelation.cs`.

## R1. Where the receipt's customer payment lives

- **Decision:** migration `025_fiscal_document_customer_payment.sql` adds a nullable column to `fiscal_document`:

  ```sql
  customer_payment INT NULL, FK → customer_payment, indexed
  ```

  It is set when a receipt is created from a payment (FR-001). Its rollback drops the column. The column is **DDL on mbe_dev, so it runs only after the user confirms**, as every migration does.
- **Rationale:**
  - **Link:** the clarified answer (Q2) keeps the link, and no existing column can hold it. Legacy's NHibernate maps only the columns it names, so an extra nullable column is invisible to it.
  - **Uniqueness:** one live receipt per payment (Edge Cases) is checked in the service. A unique index cannot express "not cancelled".
- **Alternatives:**
  - **A link table:** rejected. It costs more for a 0..1 relation.
  - **Reusing `payment_reference`:** rejected. That column holds the SAT operation number, and overloading it would corrupt the XML.
  - **An incidence:** rejected. It is not queryable as a relation.

## R2. Receipt balances and installments (FR-004)

**Invoice total:** the `Total` of the related invoice's stored XML. That is the stamped or sealed CFDI, the same value spec 020 reports. It works for current-system invoices too.

**Earlier receipts** of invoice *i*: every `fiscal_document_relation` row with `relation = i` whose document:
- is type 200;
- is not cancelled;
- is sealed or issued (spec 027, R1);
- is not this receipt.

**Credit notes** of *i*: documents of type 100, sealed or issued and not cancelled, whose single relation points to *i*. Their XML `Total` is subtracted.

The computed values are:

```
installment       = 1 + count(earlier receipts)
previous_balance  = total − Σ earlier receipts' amount − Σ credit notes' total
remaining         = previous_balance − amount
```

**When the values are computed:**
- on every relation add or amount change, for display;
- again at confirm, where they are stored on the relation row (`installment`, `previous_balance`, `amount`) and written to the XML.

**Concurrency:** confirm locks the related invoices' `fiscal_document` rows with `FOR UPDATE`, in id order, before it recomputes.
- Two receipts for one invoice confirmed at once therefore get installments 1 and 2, never 1 and 1.
- Id order means confirms of overlapping invoice sets cannot deadlock.
- The batch serial lock of spec 027 R2 applies too, taken after the invoice locks.

**Data check:** for the 29 receipt relations of the last 12 months, the stored installment and previous balance equal these formulas wherever the current system's typed values were consistent. SC-002 verifies this.

## R3. The Pagos 2.0 XML (FR-007)

**Comprobante** (as legacy CFD40:126-162):
- `Version` 4.0, `Serie`, `Folio`, `Fecha`, `NoCertificado`, `Certificado`, `Sello`;
- `SubTotal="0"`, `Moneda="XXX"`, `Total="0"`, `TipoDeComprobante="P"`, `Exportacion="01"`, `LugarExpedicion`;
- no `FormaPago`, `MetodoPago` or `Descuento`.

**Other nodes:**
- **Emisor** as for invoices.
- **Receptor:** `UsoCFDI="CP01"`.
- **One Concepto:**

  ```
  ClaveProdServ="84111506" Cantidad="1" ClaveUnidad="ACT" Descripcion="Pago" ValorUnitario="0" Importe="0" ObjetoImp="01"
  ```

- **`cfdi:Complemento/pago20:Pagos Version="2.0"`**: one `Pago` and one `Totales`.

**`Pago`:**
- `FechaPago` is the payment date at `T00:00:00`, as stored rows show.
- `FormaDePagoP` uses 2 digits.
- `MonedaP="MXN"`, `TipoCambioP="1"`, `Monto` = payment amount.
- `NumOperacion` and `NomBancoOrdExt` are written only when set.

**`DoctoRelacionado`, one per relation:**
- `IdDocumento` = the invoice's UUID, as stored, in lower case;
- `Serie`, `Folio`;
- `MonedaDR="MXN"`, `EquivalenciaDR="1"`;
- `NumParcialidad`, `ImpSaldoAnt`, `ImpPagado`, `ImpSaldoInsoluto`;
- `ObjetoImpDR`: `02` when the invoice's XML has transfers, else `01` with no `ImpuestosDR`.

**Taxes per related document.** This is the correction FR-007 asks for. The invoice's own `Impuestos/Traslados` groups come from its stored XML. Each group *g* has a factor, a rate *r_g* and a base *B_g*. Its share of the invoice's taxed total is:

```
w_g = B_g × (1 + r_g) / Σ_k B_k × (1 + r_k)     (exempt: r = 0)
BaseDR_g    = round6(ImpPagado × w_g / (1 + r_g))
ImporteDR_g = round6(BaseDR_g × r_g)             (Tasa only; exempt has none)
```

With a single 16 % group, `w = 1` and this reduces to legacy's `round6(amount / 1.16)` and `round6(base × 0.16)`. Its 21 receipts, which all relate 16 %-only invoices, therefore keep their original string (SC-002).

**`ImpuestosP` and `Totales`:**
- `ImpuestosP/TrasladosP` sums the DR transfers by factor and rate, rounded to 6.
- `Totales` is **always** written:
  - `MontoTotalPagos`;
  - `TotalTrasladosBaseIVA16` and `TotalTrasladosImpuestoIVA16`;
  - `TotalTrasladosBaseIVA0` and `TotalTrasladosImpuestoIVA0`;
  - `TotalTrasladosBaseIVAExento`.

  Each field is present only when that rate occurs, and rounded to 2. Legacy summed every rate into the IVA16 fields and dropped `Totales` whenever a related invoice had no taxes.
- **No retentions** (spec Assumptions).

**Original string:** the vendored `cadena_original_4_0.xslt` already includes `pagos20.xslt` (spec 027, R3).

**Schemas:** for SC-001, `Pagos20.xsd` and `catPagos.xsd` are vendored into `tests/fixtures/sat/`, next to spec 027's three files. Their imports point at the local files. All URLs returned 200 on 2026-10-25.

## R4. Credit notes reuse the invoice builder

- **Decision:**
  - `cfdi_invoice.InvoiceData` gains `voucher_type` (`'I'` by default, `'E'` for credit notes).
  - A credit note sets `relation_type='01'`, `payment_terms=IMMEDIATE` (PUE), and the related invoice's form and usage (spec Assumptions).
  - Lines, rounding, original string and seal are spec 027's unchanged.
- **Rationale:** legacy builds both from one function (CFD40:368). The only differences are the type letter, the forced relation type and the defaults.
- **Line source:** a line taken from the invoice copies these from the invoice's `fiscal_document_detail` row:
  - `product`, `order_detail`;
  - SAT keys, unit, code and name;
  - price, discount, tax rate and tax-included flag.

  The quantity must be ≤ that line's. A line added by hand is spec 027's manual line.
- **Cap:**
  - **Per line:** quantity ≤ the invoice line's.
  - **Per invoice, at confirm:** the credit note's total ≤ the invoice's XML total, less the XML totals of its other sealed or issued, non-cancelled credit notes.

  The related invoice row is locked `FOR UPDATE` as in R2, so concurrent credit notes cannot both fit.
- **Alternatives:** a per-line cap across credit notes. It was rejected because no column ties a credit-note line to an invoice line, and the 123 legacy credit notes were typed by hand anyway. The per-invoice amount cap is what prevents crediting more than was invoiced.

## R5. Refund rule (FR-011)

- **Decision:** `customer_refund_service.line_refundable` becomes:

  ```
  quantity − refunded − invoiced_live
  ```

  `invoiced_live` is the quantity on `fiscal_document_detail` rows for the order line whose document is type 0, not cancelled, and completed or sealed. It is the same predicate as spec 027's `remaining_by_order_line`.
- **Where it applies:** opening a refund, editing its lines, and confirming it.
- **What the client sees:**
  - when the shortfall comes from a live invoice, the error is **409**, naming the series and folio: "Units of line {code} are on invoice {series}{folio}; cancel the invoice to refund them";
  - spec 011's 422 for over-refunding stays for everything else;
  - at confirm, `reconcile_lines` no longer silently trims a line short because of invoiced units: it raises the 409.
- **Rationale:** this is a single source of truth. Refunds and invoices then agree with spec 027's remaining-quantity rule, where a refund can never take invoiced units.
- **Volume:** in 12 months, 75 of 1,540 refunds would have been refused.

## R6. Email (FR-013)

- **Route:** `POST /fiscal-documents/{id}/email` with `{to: [1..10 addresses]}`, validated like spec 024's quote email. It requires Read on FiscalDocuments (23).
- **Refused with 409 unless** the document is completed, not cancelled, version 4.0 and has a `stamp_uuid`. Email not configured gives 503; a server refusal gives 502 and nothing is recorded.
- **Message:**
  - **Subject:** `Comprobante Fiscal - {issuer RFC} - {series}{folio:06d}`.
  - **Body:** legacy's `FiscalDocumentEmailBodyFormatString` in Spanish:
    - folio fiscal (UUID), issuer RFC, recipient RFC and total;
    - the SAT verification link, built with spec 020's `sat_qr_payload`;
    - the closing lines.
  - **Attachments:** `{RFC}-{series}{folio:06d}.pdf`, from spec 020's renderer, and the same name `.xml`, the stored XML as UTF-8.
- **Record:** an incidence with a new API-only `SourceType.FISCAL_DOCUMENT = 13`, written after the server accepts the message. That is spec 024's order.
- **QR:** spec 020 already renders it. A test asserts the PDF context carries `sat_qr_payload` for invoice, credit note and payment fixtures.

## R7. Receipt creation and its defaults

**Route:** `POST /fiscal-documents/payment-receipts`, body `{customer, recipient, issuer?, batch?, customer_payment?}`.

**Defaults:**
- type 200;
- the issuer's first type-200 batch, or the store's default when it is one;
- terms IMMEDIATE;
- usage `CP01`, which exists in `sat_cfdi_usage`;
- MXN at rate 1;
- the rest as for invoices (spec 027, R7).

**With `customer_payment`:**
- The payment must belong to the customer and have no live receipt.
- `payment_date`, `payment_amount`, `payment_method` and `payment_reference` (the payment's `reference`, ≤ 50 characters) are copied.
- Relations are proposed:
  - **which invoices:** for each of the payment's non-cancelled applications (`sales_order_payment`), the stamped PPD invoices of the same issuer and recipient that hold lines of that order, each once;
  - **how much:** each gets `min(balance, unassigned amount)` in order of invoice date.

**Header:** `PUT /fiscal-documents/{id}` gains `payment_date`, `payment_amount`, `payment_reference` and `ordering_bank`, the last stored in `reference` as legacy does. They are accepted only on receipts, and invoice-only fields only on invoices and credit notes (422 otherwise).

## R8. Credit-note creation

**Route:** `POST /fiscal-documents/credit-notes`, body `{invoice}`. The invoice must be stamped, not cancelled and of type 0.

**Copied from the invoice:**
- issuer, customer, recipient (name, regime, postal code) and store;
- payment form and usage.

**Defaults:**
- terms IMMEDIATE;
- the issuer's first type-100 batch, or the store's default when it is one;
- the relation, type `'1'` (cfdlib's number for 01, research R5 of spec 027).

**Lines:** `POST /{id}/invoice-lines` with `{line, quantity}` copies a line of the related invoice. `POST /{id}/lines` (spec 027, privilege 56) adds a product.

## R9. Shared machinery and type dispatch

- **Shared:** spec 027's `confirm`, `reopen`, `_require_draft`, serial and certificate code is reused.
- **Confirm per type:**
  - type 0 builds an invoice;
  - type 100 runs the credit-note checks (R4), then the invoice builder with `voucher_type='E'`;
  - type 200 runs the receipt checks (R2), then `cfdi_payment.build`.
- **Seal:** `cfdi_invoice.seal` is generalised to seal any built root (`seal_root(root, certificate…)`), so the receipt builder reuses the original string and signing.
- **Spec 027's invoice-only routes** (`/sales-orders`, `/relations` with 04/07) refuse other types with 409 "Not available for this document type".
