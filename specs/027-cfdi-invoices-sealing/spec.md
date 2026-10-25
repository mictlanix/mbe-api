# Feature Specification: CFDI Invoices up to a Sealed Document

**Feature Branch**: `027-cfdi-invoices-sealing`

**Created**: 2026-10-25

**Status**: Draft

**Input**: Implements mictlanix/mbe-roadmap#44 (taxpayer batches) and mictlanix/mbe-roadmap#47 (invoice from sales order and standalone invoice editor), in the CFDI invoicing epic (mictlanix/mbe-roadmap#5). The scope document is `docs/specs/10-fiscal-documents.md`: the batch sub-panel of §1 and, in §2, the create flow, header and line fields, the confirm action up to the PAC call, and standalone documents. The roadmap issues call these §1, §2b and §3.

Already built:
- spec 008 stores and validates the CSD certificates;
- spec 020 reads fiscal documents, downloads their XML and renders the CFDI 4.0 PDF.

This feature stops at a **sealed, unstamped** document. Stamping (mictlanix/mbe-roadmap#46) and cancellation (#51) come later through a standalone PAC client library, so no PAC client, interface or configuration is added here. Invoicing moves from the current system in a single cutover (mictlanix/mbe-roadmap#57). The two systems never issue CFDIs at the same time, so nothing here is designed for both writing these tables.

## Findings that shape this spec

### The current system (`mictlanix/mbe`)

- **Batches.** A batch is an issuer's series (`taxpayer_batch`): the series text, a document type and a print template. The current system only lists them; there is no screen to create, change or delete one. A new document takes the issuer's first batch of the requested type, and the batch decides the document's type.
- **Creating an invoice.** The user picks the issuer, the customer and one of the customer's taxpayer records (the recipient). The recipient's regime and postal code are copied. For the general public RFC (XAXX010101000) the postal code is the issuing store's. Defaults:
  - CFDI usage G01;
  - payment form 99 ("por definir");
  - payment terms "credit" (PPD) when the customer has credit, else "immediate" (PUE);
  - currency MXN at rate 1.

  No serial and no date are set at creation.
- **Lines from sales orders.** A sales order is accepted when it is completed and not cancelled. Payment is not checked, and the order's customer may differ from the invoice's.
  - Each order line of an invoiceable product adds its remaining quantity: the line's quantity minus the larger of what was already invoiced and what was refunded. Only issued, non-cancelled invoices and completed refunds count.
  - Code, name, comment, price, discount, tax rate and the tax-included flag come from the order line. The SAT product key and unit come from the product.
  - Errors: "No se encontró el pedido.", "No se ha finalizado el pedido o está cancelado.", "No se encontraron registros facturables.".
  - The same order can be added twice to one draft, which duplicates its lines.
- **Manual lines** (standalone invoices) take the customer's price-list price and the product's tax rate, at quantity 1.
- **Editing.** Header: recipient, batch, CFDI usage, payment terms and form, issue date, issuing store, reference, comment and relations. Lines: name, code, comment, quantity, price, discount and tax rate (16 %, 0 % or exempt).
  - The issue date can be today or 1–3 days back.
  - Changing the issuing store needs its own privilege.
  - A line from an order cannot exceed its remaining quantity.
- **Confirm** checks, in order:
  1. every quantity is positive and within its order line;
  2. the serial: `MAX(serial) + 1` for the issuer and series, with no lock (the mictlanix/mbe#45 pattern);
  3. the date is not 72 hours old or more; the only error text is "Fecha de Emisión";
  4. exactly one active certificate exists. Its validity dates are never checked.

  Confirm then builds the CFDI 4.0 XML (cfdlib's `FiscalDocumentToCFDv40`), seals it, and in the same call stamps it with the PAC. On success it stores the XML and marks the document completed. An empty invoice is not refused.
- **Sealing** (cfdlib `Comprobante.Sign`):
  - the original string comes from SAT's CFDI 4.0 stylesheet;
  - the seal is SHA-256 with RSA (PKCS#1 v1.5) under the CSD private key, in base64;
  - `NoCertificado` is the 20-digit certificate number, and `Certificado` is the base64 DER certificate.
- **The XML.**
  - `Moneda` is always MXN and `Exportacion` is 01.
  - `MetodoPago` is PUE or PPD, from the terms. `LugarExpedicion` is the issuing store's postal code.
  - Amounts round half away from zero: 2 places for amounts, 6 for unit values.
  - Exempt lines carry an exempt transfer with no rate.
  - A line with a 100 % discount is "not subject to tax" (`ObjetoImp` 01).
  - `CfdiRelacionados` lists the related documents' UUIDs under one relation type.
  - `InformacionGlobal` is never written.

### This API today

- `TaxpayerBatch`, `FiscalDocument`, `FiscalDocumentDetail`, `FiscalDocumentRelation` and `FiscalDocumentXml` are mapped. Spec 020 reads documents: its states are draft, issued (completed) and cancelled.
- Spec 008 decrypts and validates a CSD. Spec 020 parses the stored XML and has real stamped invoices as test fixtures.
- Privileges exist for FiscalDocuments (23), Taxpayers (24) and StandaloneFiscalDocuments (56).

### Measured on mbe_dev (2026-10-25, documents issued since 2024-10-25)

| Document type | Issued | Of which cancelled |
|---|---|---|
| Invoice (0) | 10,698 | 351 |
| Fee receipt (1), rent receipt (2), debit note (3) | 0 | — |
| Credit note (100) | 3 | 1 |
| Advance payments applied (101) | 48 | 4 |
| Payment receipt (200) | 30 | 2 |

So the only invoice type in use is Invoice (0). The others are out of scope, by measurement or by roadmap.

Invoices:
- **Source:**
  - 9,081 have lines from sales orders and 1,617 are standalone.
  - 1,388 draw on more than one order, up to 6 or more.
  - Invoices average 2.7 lines; the largest has 109.
- **Lines:**
  - 2,710 order lines were invoiced at a partial quantity, and 726 order lines appear on more than one invoice.
  - 3,040 lines had their price changed after copying.
  - 2,692 invoices took lines from another customer's orders (not counting the walk-in customer).
- **Header values:**
  - All 10,698 are MXN at rate 1.
  - None carries a federal or local retention.
  - Tax rates: 29,246 lines at 16 %, 28 at 0 %, and 1 exempt. Every line is tax-included.
  - 132 are PPD, all with payment form 99. 29 are PUE with payment form 99.
  - CFDI usages: G03 6,535, G01 2,853, S01 972, I01 322, others 16.
- **General public:** 827 went to XAXX010101000. 814 are named "PÚBLICO EN GENERAL" (accented) or "\"PUBLICO EN GENERAL\"", and none carries `InformacionGlobal`.
- **Relations:** 49 invoices carry `CfdiRelacionados`. 47 are type 07 (applying an advance invoice) and 2 are type 04 (substituting a cancelled invoice).
- **Serials:** 9 series numbers were issued twice, all stamped; the latest pair is from 2025-05-28. This is the mictlanix/mbe#45 race.
- **Dates:** no invoice was stamped more than 48 hours after its issue date.
- **Batches:**
  - 25 batches across 7 issuers, of types 0, 100, 101 and 200;
  - one issuer has three invoice series in use;
  - stores issue under several issuers;
  - all templates name the "T02Blue" print layout.
- **Certificates:** 5 issuers have a certificate valid today.

## Clarifications

### Session 2026-10-25

- Q: Public-general invoices carry no `InformacionGlobal` today. What should the new MBE do? → A: Keep as today. No `InformacionGlobal`, and the recipient name is used as stored.
- Q: Should an invoice be able to carry CFDI relations? → A: Yes. Include both type 04 (substitution) and type 07 (application of an advance).
- Q: What happens to a sealed invoice that is wrong, or that waited too long to be stamped? → A: It can return to draft. The sealed XML is discarded, the serial stays with the invoice, and confirming again reseals it with a valid date.
- Q: How should a refund count against an order line's remaining quantity? → A: By order of events. A refund counts only for the units that had not been invoiced when it was recorded; a refund of already-invoiced units does not free them (that needs a credit note).
- Q: Who can return a sealed invoice to draft? → A: Anyone with the Update privilege on FiscalDocuments (23), the same privilege that edits a draft.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Billing invoices a customer's sales orders (Priority: P1)

A billing clerk creates an invoice for a customer, picks the recipient, adds one or more completed sales orders, adjusts what needs adjusting, and confirms it. The invoice is sealed with the issuer's certificate and waits to be stamped.

**Why this priority**: 85 % of the invoices issued in the last 24 months (9,081 of 10,698) came from sales orders. Without this, invoicing cannot leave the current system.

**Independent Test**: Create an invoice, add two completed orders, change one quantity, and confirm. The document gets the next serial of its series and a sealed XML. The XML validates against SAT's CFDI 4.0 schema, and its seal verifies with the issuer's certificate.

**Acceptance Scenarios**:

1. **Given** an issuer with an invoice batch and an active certificate, **When** the clerk creates an invoice for a customer, **Then** a draft is created with the recipient's regime and postal code, the batch's series, CFDI usage G01, payment form 99, and terms from the customer's credit.
2. **Given** a draft, **When** the clerk adds a completed, non-cancelled sales order, **Then** each line of an invoiceable product is added at its remaining quantity, with the order line's code, name, comment, price, discount and tax, and the product's SAT key and unit.
3. **Given** an order line already invoiced in full, **When** its order is added, **Then** that line is skipped. If no line remains, the request is refused with "No invoiceable lines were found".
4. **Given** a draft that already holds an order's lines, **When** the same order is added again, **Then** no line is duplicated.
5. **Given** a draft, **When** the clerk confirms it, **Then** it gets the next serial of its issuer and series, its issue date, a sealed XML, and the state "sealed". It can no longer be edited.
6. **Given** an order that is not completed or is cancelled, **When** it is added, **Then** the request is refused.

---

### User Story 2 - Billing issues a standalone invoice (Priority: P1)

A clerk with the standalone privilege creates an invoice that is not tied to a sales order, adds products by hand with their price, quantity and tax, and confirms it.

**Why this priority**: 1,617 invoices in 24 months, mostly services and adjustments.

**Independent Test**: Create an invoice, add two products by hand, set the quantity, price and discount, and confirm. The sealed XML validates against the schema and its totals match the lines.

**Acceptance Scenarios**:

1. **Given** a draft, **When** a clerk with the standalone privilege adds a product, **Then** a line is added at quantity 1, with the customer's price-list price, the product's tax and SAT keys.
2. **Given** a clerk without the standalone privilege, **When** they add a product by hand, **Then** the request is refused (403).
3. **Given** a manual line, **When** its quantity, price, discount, tax rate, code, name or comment is edited, **Then** the change is kept within the line rules (FR-011).

---

### User Story 3 - Admin maintains an issuer's batches (Priority: P2)

An administrator lists, creates, changes and removes an issuer's series, each with its document type and print template.

**Why this priority**: invoices need a batch, and the 25 existing batches already cover every issuer in use, so this is maintenance rather than a blocker.

**Independent Test**: Create a series for an issuer, use it on an invoice, then try to rename and delete it.

**Acceptance Scenarios**:

1. **Given** an issuer, **When** an admin creates a batch with a series, type and template, **Then** it is listed under the issuer.
2. **Given** a series already used by any fiscal document of that issuer, **When** an admin changes the series text or the type, or deletes it, **Then** the request is refused (409). Changing the template is allowed.
3. **Given** a series that already exists for the issuer, **When** another batch with the same series is created, **Then** the request is refused (409).

---

### User Story 4 - Billing corrects the header, relates documents, or reopens a sealed invoice (Priority: P2)

A clerk changes the recipient, series, CFDI usage, payment terms and form, issue date, issuing store, reference or comment, and relates the invoice to stamped invoices (substitution 04, or application of an advance 07). A sealed invoice that turns out to be wrong, or that was not stamped in time, can return to draft.

**Why this priority**: these change how most invoices go out, but default values cover the common case.

**Independent Test**: Edit each header field, add a type 07 relation to a stamped invoice, confirm, check the XML, reopen it, change the date and confirm again. The serial is unchanged.

**Acceptance Scenarios**:

1. **Given** a draft, **When** the payment terms are set to credit (PPD), **Then** the payment form becomes 99.
2. **Given** a draft, **When** the issue date is set more than 72 hours back, or in the future, **Then** the request is refused.
3. **Given** a draft and a stamped invoice of the same issuer and recipient (cancelled, for type 04), **When** a relation of type 04 or 07 is added, **Then** the sealed XML lists that invoice's UUID under that relation type.
4. **Given** a sealed invoice, **When** it is reopened, **Then** it returns to draft with the same serial, and its sealed XML is gone.
5. **Given** a stamped or cancelled invoice, **When** any change, reopen or confirm is attempted, **Then** the request is refused (409).

---

### Edge Cases

- **Confirm refuses an invoice** (409 or 422, with a message naming the problem) when:
  - it has no lines;
  - a line has no SAT product key or unit;
  - the recipient lacks a regime or postal code;
  - an order line's quantity exceeds what is still invoiceable;
  - the issuer has no active certificate, or none valid at the issue date.
- **No batch:** an issuer with no Invoice batch cannot create an invoice: 422 "The issuer has no invoice series".
- **Concurrent confirms:** two clerks confirming in the same series at the same moment get two different, consecutive serials. A failed confirm consumes no serial.
- **Stale date:** an invoice whose issue date has become more than 72 hours old by the time it is confirmed is refused until the date is updated.
- **General public:** for recipient XAXX010101000, the recipient postal code is the issuing store's postal code. No `InformacionGlobal` is written.
- **Full discount:** a line with a 100 % discount is written as not subject to tax.
- **Exempt lines** are written with an exempt transfer and no rate or amount.
- **Reserved quantities:** sealed invoices count as invoiced when computing an order line's remaining quantity, so two sealed invoices cannot both take the same quantity. Drafts do not reserve.
- **Drafts left by the current system** can be edited and confirmed like any other draft after the cutover.

## Requirements *(mandatory)*

### Functional Requirements

**Batches (#44)**

- **FR-001**: Admins with the Taxpayers privilege (24) MUST be able to list, create, update and delete an issuer's batches. A batch has:
  - a series of 1–10 characters, unique per issuer;
  - a document type: Invoice, Fee receipt, Rent receipt, Debit note, Credit note, Advance payments applied, or Payment receipt;
  - a print template with a name, logo, header and footer heights, and extra information.
- **FR-002**: Changing a batch's series or type, or deleting it, MUST be refused while any fiscal document of that issuer uses the series.

**Invoices (#47)**

- **FR-003**: The system MUST create Invoice drafts only. The other document types are out of scope (see Out of Scope). A draft has:
  - the issuer, by default the issuing store's taxpayer;
  - an Invoice batch of that issuer, by default the store's default series when it belongs to that issuer, else the issuer's first Invoice batch;
  - the customer, and a recipient taken from the customer's taxpayer records;
  - the issuing store;
  - the defaults listed in the Findings.
- **FR-004**: The recipient's postal code MUST be the recipient record's, except for XAXX010101000, where it is the issuing store's postal code.
- **FR-005**: The system MUST add lines from a sales order only when the order is completed and not cancelled. It adds each line of an invoiceable product at its remaining quantity. The order's customer MAY differ from the invoice's.
- **FR-006**: An order line's remaining quantity MUST be its quantity minus:
  - its quantity on sealed or issued, non-cancelled invoices other than this one; and
  - for each completed refund, only the part that had not been invoiced when the refund was recorded (the line's quantity minus what was invoiced and refunded before it, floored at zero).

  The result is floored at zero. Example: a line of 10, 3 refunded, then 7 invoiced leaves 0; a line of 10, 7 invoiced, then 3 refunded also leaves 0.

  Adding an order whose lines are already on the draft MUST NOT duplicate them.
- **FR-007**: Adding a line by hand MUST require the Create privilege on StandaloneFiscalDocuments (56). Every other invoice action requires the matching privilege on FiscalDocuments (23).
- **FR-008**: The header fields MUST be editable on a draft: recipient, batch (of the same issuer and type Invoice), CFDI usage, payment terms, payment form, issue date, issuing store, reference and comment.
  - PPD MUST force payment form 99.
  - Changing the issuing store MUST keep the current system's separate permission.
  - Once an invoice has a serial (it was sealed and reopened), its batch MUST NOT change (409), because the serial belongs to that series.
- **FR-009**: The issue date MUST be no more than 72 hours before now and not in the future, both when it is set and at confirm.
- **FR-010**: A draft MUST accept relations to stamped documents of the same issuer and recipient, and allow removing them. As in the current system:
  - type 04 (substitution) relates a cancelled invoice;
  - type 07 (application of an advance) relates a non-cancelled invoice or advance payments applied (101).

  All relations of one invoice share one relation type.
- **FR-011**: Line rules:
  - quantity is positive and, for an order line, at most its remaining quantity;
  - price is zero or more;
  - discount is 0–100 %;
  - tax rate is 16 %, 0 % or exempt;
  - a blank name resets to the product's name.
- **FR-012**: A document that is sealed, stamped or cancelled MUST NOT accept header, line or relation changes (409).

**Sealing**

- **FR-013**: Confirm MUST validate the invoice (Edge Cases), then:
  1. assign the serial;
  2. build the CFDI 4.0 XML with the current system's values and rounding;
  3. compute the original string with SAT's CFDI 4.0 stylesheet;
  4. seal it with the issuer's active certificate, which must be valid at the issue date;
  5. store the sealed XML;
  6. leave the document in the state "sealed", ready to stamp.

  No PAC is contacted.
- **FR-014**: The serial MUST be the next number for the issuer and series, continuing from the highest serial already recorded. It MUST be unique even when invoices are confirmed concurrently, and a failed confirm MUST NOT consume a number.
- **FR-015**: A sealed, unstamped invoice MUST be able to return to draft, with the Update privilege on FiscalDocuments (23). Its sealed XML is removed, it keeps its serial, and a later confirm reuses that serial.
- **FR-016**: The read endpoints of spec 020 MUST report the "sealed" state for a sealed, unstamped invoice. They MUST keep reporting draft, issued and cancelled as they do today.

**Docs**

- **FR-017**: `docs/specs/10-fiscal-documents.md` MUST describe what is built: batch maintenance, the invoice editor, sealing, the sealed state and reopening. It MUST also mark stamping as pending.

### Key Entities

- **Taxpayer batch**: an issuer's series, with its document type and print template.
- **Invoice (fiscal document)**: header (issuer, recipient, store, series, serial, date, terms, payment form, usage), lines, relations, and a state: draft, sealed, issued or cancelled.
- **Invoice line**: product, optional source sales order line, SAT keys, quantity, price, discount and tax.
- **Relation**: a related stamped invoice and the relation type (04 or 07).
- **Sealed XML**: the CFDI 4.0 document with its seal and certificate, before stamping.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every sealed XML produced in tests validates against SAT's CFDI 4.0 schema, and its seal verifies with the issuer's certificate over the original string.
- **SC-002**: For a sample of real stamped invoices on mbe_dev, building the XML from their stored data reproduces the stored document's original string, apart from the seal and stamp. The sample covers:
  - order-based and standalone invoices;
  - the general public;
  - 0 % and exempt lines;
  - discounts;
  - PPD;
  - a type 07 relation.
- **SC-003**: 20 confirms run concurrently in one series produce 20 distinct, consecutive serials, with no gaps from failures.
- **SC-004**: On the test deployment, a clerk can create, fill and seal an invoice from real sales orders, and reopen and reseal it, without writing to any table outside the fiscal documents.

## Assumptions

- **Single invoice type.** Only Invoice (0) is built, because the other invoice types (1–3) issued nothing in 24 months. Batches still accept every type so the existing 25 keep working.
- **No retentions, MXN only.** No invoice in 24 months used federal or local retention or another currency. New invoices carry no retentions and are always MXN at rate 1. The XML writes no retention nodes and no `TipoCambio`.
- **Legacy defects fixed:**
  - the same order can no longer be added twice;
  - an empty invoice is refused;
  - certificate validity dates are checked;
  - the 72-hour message says what is wrong.

  The current system allowed PUE with payment form 99 (29 stamped invoices), so it stays allowed.
- **Remaining quantity.** The current system's rule (the larger of invoiced and refunded) is replaced, because it let 141 lines refunded before invoicing in the last year be invoiced again. Sealed invoices also count. Invoices are ordered by their issue time and refunds by their completion time.
- **New dependency.** Computing the original string with SAT's stylesheet, and validating against SAT's schema in tests, needs an XSLT 1.0 and XML Schema processor. `lxml` is added for both (constitution V).
- **Template.** The template is stored as the current system's JSON and used by spec 020's PDF. This feature does not change printing.
- **Order of states.** "Sealed" sits between draft and issued. The current system would show a sealed invoice as a draft, but after the cutover it no longer writes these tables.

## Verification

### SC-002: parity with stamped invoices (2026-10-25)

**Method:**
- 55 stamped CFDI 4.0 invoices were exported read-only from mbe_dev on xolotl, with their rows and stored XML:
  - the spec's cases: 3 order-based, 3 standalone, 2 XAXX, 2 with a 0 % line, the 1 exempt, 3 with discounts, 2 PPD and 2 with a type 07 relation;
  - 40 random 2026 invoices.
- Each one was rebuilt locally with `cfdi_invoice.build`, from its rows only. `Fecha`, `NoCertificado` and `Certificado` came from the stored XML.
- The original strings of the rebuilt and stored XML were compared through the vendored stylesheet.

**Result:** 55 of 55 are identical.

The first run matched 48 of 55. The other 7 differed only in number formatting: `75.0` against `75.000000`, and `1250` against `1250.00`. The builder now keeps C#'s decimal scale (research R5), and `tests/unit/test_cfdi_invoice.py` pins both cases with the invoices that showed them (ACH6216 and GR4776).

## Out of Scope

- Stamping with a PAC (#46), cancellation (#51), the payment complement (#53), credit notes and advance payments applied (#55), email and QR (#50), and the UI.
- `InformacionGlobal` for global invoices, per the clarification.
- Federal and local retentions, foreign currency, and the fee receipt, rent receipt and debit note types.
- PDF rendering of a sealed, unstamped invoice.
