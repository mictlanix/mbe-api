# Feature Specification: CFDI Invoice PDF

**Feature Branch**: `020-cfdi-invoice-pdf`
**Created**: 2026-09-29
**Status**: Draft
**Input**: GitHub issue #230, the part of Phase 1 that spec 019 left out: the CFDI invoice PDF. The API stores every stamped invoice but has no fiscal document routes, so an invoice cannot be looked up, downloaded or printed. This feature adds read access to fiscal documents, the stamped XML, and the printed representation of a CFDI 4.0 document. It ports legacy's `Print40T02Blue` onto the rendering core from spec 019.

## Clarifications

### Session 2026-09-29

- Q: Should a cancelled document's PDF carry a visible cancellation mark with the cancellation date? Legacy prints none. → A: Yes. Show a visible cancellation mark with the cancellation date.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Print a stamped invoice (Priority: P1)

A back-office user opens a stamped CFDI 4.0 invoice and gets its printed representation as a letter-size PDF. The customer receives the same content legacy prints today: issuer and recipient with their tax regimes, the CFDI use, payment form and method, every line with its SAT product and unit codes, the totals, the amount in words, and the stamp block with both seals and the original string of the stamp. The SAT verification QR code lets anyone check the invoice on the SAT site.

**Why this priority**: Invoices are 17,306 of the 17,433 CFDI 4.0 documents in the shared database. mbe-ui cannot give a customer their invoice without this.

**Independent Test**: Request the PDF of a stamped 4.0 invoice. Compare its content with legacy's PDF for the same document, and decode its QR code.

**Acceptance Scenarios**:

1. **Given** a stamped CFDI 4.0 invoice, **When** a user with read access to fiscal documents requests its PDF, **Then** they receive a letter-size PDF showing the series and folio, UUID, issue date and place, issuer, recipient, every line, the totals, the amount in words, the stamp date, both certificate numbers, both seals, and the original string of the stamp.
2. **Given** the same invoice, **When** its QR code is decoded, **Then** it holds the SAT verification address with the invoice's UUID, issuer RFC, recipient RFC, total, and the last 8 characters of the issuer's seal, all exactly as stamped.
3. **Given** an invoice whose batch lists bank accounts, **When** it is printed, **Then** each page's footer shows those accounts, the legend "Este documento es una representación impresa de un CFDI." and the page number.
4. **Given** an invoice with more lines than fit on one page, **When** it is printed, **Then** the lines continue on further letter pages, with the footer on every page.
5. **Given** a cancelled invoice, **When** it is printed, **Then** every page shows a visible cancellation mark with the cancellation date, and the rest of the content is unchanged.

---

### User Story 2 - Find a fiscal document and download its XML (Priority: P1)

A back-office user searches the fiscal documents by series and folio, UUID, or recipient RFC or name. They open one to see its header, lines and status. Then they download its stamped XML, the file that is legally the invoice, to send it alongside the PDF.

**Why this priority**: A PDF route is useless to mbe-ui without a way to find the document's id. The XML is the file the customer and their accountant need; the PDF only represents it.

**Independent Test**: List fiscal documents with a search term, open one, and download its XML. The downloaded bytes are identical to the stored XML.

**Acceptance Scenarios**:

1. **Given** stored fiscal documents, **When** a user lists them with a search term, **Then** they get the matching documents, newest first and paginated, each with its series, folio, type, version, issue date, recipient, total, stamp UUID and status.
2. **Given** a list, **When** it is filtered by issuer, type, status or issue-date range, **Then** only matching documents are returned.
3. **Given** a fiscal document, **When** a user opens it, **Then** they see its header fields and every line.
4. **Given** an issued document of any version, **When** a user downloads its XML, **Then** they receive the stored XML unchanged, as an XML file named after the issuer, series and folio.

---

### User Story 3 - Print a credit note or an applied advance payment (Priority: P2)

A user prints a CFDI 4.0 credit note or applied-advance document. It has the invoice's layout, plus the related CFDIs section: the type of relation and the UUID of each related document.

**Why this priority**: These are real documents a customer receives (56 of them in the shared database), and they share the invoice's layout. They follow the invoice because they are far less frequent.

**Independent Test**: Print a stamped 4.0 credit note that relates to an invoice. The related CFDIs section names the relation type and lists the related UUID.

**Acceptance Scenarios**:

1. **Given** a stamped 4.0 credit note related to an invoice, **When** it is printed, **Then** it shows everything an invoice shows plus the relation type and the related document's UUID.
2. **Given** a stamped 4.0 applied-advance document, **When** it is printed, **Then** its related CFDIs section uses the applied-advance relation type.

---

### User Story 4 - Print a payment receipt (Priority: P3)

A user prints a CFDI 4.0 payment receipt (a "complemento de pago"). Instead of product lines, it shows the documents the payment settled. For each one: its UUID, series and folio, installment, currency, previous balance, amount paid and outstanding balance. Below them: the payment date, payment form, reference, and total paid, with the amount in words of the payment.

**Why this priority**: 71 documents in the shared database. The payment section differs from the invoice layout, so it is a separable slice that can follow the others.

**Independent Test**: Print a stamped 4.0 payment receipt. Every settled document and every payment figure matches legacy's printout of the same receipt.

**Acceptance Scenarios**:

1. **Given** a stamped 4.0 payment receipt settling two invoices, **When** it is printed, **Then** it lists both with their installment, previous balance, amount paid and outstanding balance, and shows the payment date, form, reference and total.
2. **Given** a payment in a currency other than MXN, **When** it is printed, **Then** it shows the currency and the exchange rate.

---

### Edge Cases

- **Never issued** (a draft), or a 4.0 document without a stamp: there is no CFDI to represent, so the PDF is refused with a clear error. A draft's XML download is refused too, because a draft has no XML. Legacy prints a generic unstamped page; this feature does not.
- **Version before 4.0** (2.0, 2.2, 3.2, 3.3): the PDF is refused with a clear error saying the version is not supported. It is never printed with the 4.0 layout. The list, detail and XML download still work for every version. Versions 2.0 and 2.2 were never stamped, since CFDs predate stamping, but they are issued and have their XML.
- **Cancelled document**: still printable, since a customer may need a copy. Every page carries a visible cancellation mark with the cancellation date, so the printout cannot pass for a valid CFDI. Legacy prints no mark; this is a deliberate departure.
- **Cancelled with no cancellation date recorded**: the mark still appears, without a date.
- **Stored XML missing or unreadable** for a stamped document: the PDF and the XML download fail with a clear error, never an empty or partial document.
- **Batch template missing or malformed**: the document prints with the default layout: no logo, no bank accounts, default footer height.
- **Batch template logo not found**: the document prints without a logo.
- **Unknown document id**: not found.
- **Retentions**: printed only when the document has them, as legacy does.
- **Currency other than MXN**: the currency and exchange rate are shown, and the amount in words uses that currency.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to list fiscal documents, newest first and paginated, searching by series and folio, UUID, or recipient RFC or name.
- **FR-002**: The list MUST be filterable by issuer, document type, status (draft, issued, cancelled) and issue-date range.
- **FR-003**: Users MUST be able to read one fiscal document with its header fields, status and lines.
- **FR-004**: Users MUST be able to download an issued document's XML exactly as stored, for any version. The file is named `{issuer RFC}-{series}{folio}.xml`, with the folio padded to 6 digits, as legacy names its files.
- **FR-005**: The system MUST produce a letter-size PDF for any stamped CFDI 4.0 document of type invoice, credit note, applied advance or payment receipt. The file is named like the XML, ending in `.pdf`.
- **FR-006**: Every amount, code, seal, certificate number, UUID and date printed on the PDF MUST be the value in the stamped XML. The database may supply only descriptions the XML does not carry, such as catalog names.
- **FR-007**: The PDF MUST carry the SAT verification QR code, whose payload is the SAT verification address with the UUID, issuer RFC, recipient RFC, total as written in the XML, and the last 8 characters of the issuer's seal.
- **FR-008**: The PDF MUST show the stamp block: stamp date, SAT certificate number, PAC RFC, the original string of the stamp, the issuer's seal and the SAT seal. Long seals MUST wrap within the page.
- **FR-009**: The PDF MUST show the content legacy's `Print40T02Blue` shows, in the same sections. The printed values are what is compared with legacy; layout and appearance are not.
- **FR-010**: Invoices, credit notes and applied-advance documents MUST show their lines with quantity, unit code and name, SAT product code, description with the internal code and comment, unit price and amount. They MUST also show the subtotal, discount (when non-zero), taxes, retentions (when present) and total.
- **FR-011**: Credit notes and applied-advance documents MUST show their related CFDIs: the relation type and each related UUID.
- **FR-012**: Payment receipts MUST show each settled document and the payment details as described in User Story 4, with the amount in words of the payment total.
- **FR-013**: The amount in words MUST use the corrected wording from spec 019.
- **FR-014**: The per-batch settings MUST drive the layout: the logo, the footer height, and the bank accounts printed in the footer.
- **FR-015**: The PDF MUST repeat its footer on every page, with the page number and total page count.
- **FR-016**: The PDF MUST be refused, with a distinct error for each case, for a document that was never issued, for a version other than 4.0, and for a 4.0 document that is not stamped.
- **FR-017**: The PDF and the XML download MUST fail with a clear error when an issued document's XML is missing, and the PDF also when it cannot be read.
- **FR-018**: Every route MUST require the fiscal documents read privilege.
- **FR-019**: The PDF MUST be rendered by the spec 019 rendering core: nothing fetched over the network, off the request loop, deterministic for the same data, never stored.
- **FR-020**: The PDF and XML routes MUST publish binary response schemas, so the generated client keeps the bytes.
- **FR-021**: The PDF and the XML MUST stay separate files. The XML is not embedded in the PDF.
- **FR-022**: The PDF of a cancelled document MUST show a visible cancellation mark with the cancellation date on every page. The date comes from the database, since the stamped XML does not record the cancellation.

### Key Entities

- **Fiscal document**: one CFD or CFDI, a draft or issued. It has an issuer, a recipient, a type, a version, a series (batch) and folio, an issue date and place, payment terms, form and currency, a status, and, once stamped, a UUID, stamp date, seals, certificate numbers and the PAC's RFC.
- **Fiscal document line**: a concept on the document: product, SAT product and unit codes, quantity, price, discount and tax rate.
- **Fiscal document relation**: a link to another fiscal document. For a payment receipt it records the installment, previous balance and amount paid; otherwise it is a related CFDI with its relation type.
- **Stamped XML**: the CFDI as the PAC returned it. It is the legally binding document and the source of every printed value.
- **Batch (series) settings**: per issuer and series: the logo, the footer height and the bank accounts to print. It also stores a layout name and a header height, which this feature doesn't use (see Assumptions).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For one stamped 4.0 document of each supported type, every printed value matches legacy's PDF for the same document, apart from the recorded deviations, including the cancellation mark. The comparison covers content, not layout.
- **SC-002**: For every stamped 4.0 document in a sample of at least 200, the decoded QR payload matches the SAT verification format and the document's stamped values exactly.
- **SC-003**: For every document in that sample, the printed total, UUID, seals and certificate numbers equal the stamped XML's.
- **SC-004**: A document of up to 50 lines is returned in under 1 second, measured on the host that holds the data.
- **SC-005**: Rendering the PDFs of the whole sample, with the network unavailable, produces the same bytes as with it.
- **SC-006**: A stamped document's QR code, scanned with a phone, opens the SAT verification page and reports the invoice as valid.
- **SC-007**: A downloaded XML is byte-identical to the stored one for every document in the sample.

## Assumptions

- **The stamped XML is the source of truth.** Legacy prints from database columns and recomputes the totals. This feature prints what was stamped, so the QR code and printed total match what SAT holds. Every stored 4.0 document has its XML.
- **One layout.** Every batch in the shared database uses the `T02Blue` template, and `Print40T02Blue` is legacy's only 4.0 layout. The batch's template name therefore does not select a layout; the logo, heights and bank accounts still apply.
- **Logos are found by file name** under the API's image directory, since legacy's template points at a file inside the legacy application.
- **Header height is ignored.** No legacy 4.0 layout has a page header.
- **Access follows spec 019.** A privilege check applies, with no per-facility restriction on single-record reads. Legacy checks nothing; that is not reproduced.
- **The legacy footer text is kept**, including its "FacturaElectronicaSimple.mx" line, because the comparison is on content.
- **Stamping, cancelling and issuing documents stay in legacy.** This feature only reads.
- **One new dependency: `segno`**, to draw the QR code. It is pure Python with no dependencies of its own, and emits SVG. Issue #230 names it. The stamped XML is parsed with the standard library.

## Verbatim Constraints

- Router: `/fiscal-documents`
- PDF route: `GET /fiscal-documents/{id}/pdf`
- Response media type: `application/pdf`, schema `{"type": "string", "format": "binary"}`
- Layout ported: `Print40T02Blue`
- Batch settings keys: `Name`, `Logo`, `HeaderHeight`, `FooterHeight`, `ExtraInfo`
- QR payload fields: `id` (UUID), `re` (issuer RFC), `rr` (recipient RFC), `tt` (total), `fe` (last 8 characters of the issuer's seal), on `https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx`
- Printed legend: `Este documento es una representación impresa de un CFDI.`

## Out of Scope

- Stamping, cancelling or creating fiscal documents.
- Email delivery of the PDF and XML.
- Printed layouts for versions 2.0, 2.2, 3.2 and 3.3.
- Embedding the XML in the PDF (PDF/A-3).
- ESC/POS and direct printing (Phase 2 of #230).
